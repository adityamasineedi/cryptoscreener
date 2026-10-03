"""Data-health gate for dynamic candidates (1h + 4h required).

A candidate may not enter backtest until validated closed OHLCV exists for
both 1h and 4h. Never silently downgrades to 1h-only or uses 15m.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import text

from app.research.combo02_candidate_thresholds import DEFAULT_SELECTOR
from app.research.strategy_candidate_registry import strategy_candidate_registry
from app.services.database import db_manager

logger = structlog.get_logger(__name__)

# Warmup bars for frozen swing/trend/BOS (conservative floor).
_MIN_WARMUP_1H = 200
_MIN_WARMUP_4H = 80
_TF_SECONDS = {"1h": 3600, "4h": 14400}


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _tf_coverage(symbol: str, timeframe: str) -> dict[str, Any]:
    """Coverage + gap/duplicate diagnostics from canonical ohlcv table."""
    empty = {
        "exists": False,
        "bars": 0,
        "start": None,
        "end": None,
        "history_days": None,
        "completeness": None,
        "duplicate_count": 0,
        "gap_count": 0,
        "boundary_ok": True,
    }
    if not db_manager.enabled or db_manager.engine is None:
        # In-memory / no-DB mode: treat as missing so tests can inject fields.
        return empty

    sql = text(
        """
        SELECT time
        FROM ohlcv
        WHERE symbol = :sym AND timeframe = :tf
        ORDER BY time ASC
        """
    )
    async with db_manager.engine.connect() as conn:
        rows = (await conn.execute(sql, {"sym": symbol, "tf": timeframe})).fetchall()
    if not rows:
        return empty

    times: list[datetime] = []
    for (t,) in rows:
        dt = _parse_ts(t)
        if dt is not None:
            times.append(dt)
    if not times:
        return empty

    step = _TF_SECONDS[timeframe]
    seen: set[float] = set()
    duplicates = 0
    gaps = 0
    boundary_ok = True
    prev: datetime | None = None
    for dt in times:
        key = dt.timestamp()
        if key in seen:
            duplicates += 1
        seen.add(key)
        # UTC candle boundary: open time must align to timeframe step.
        if int(key) % step != 0:
            boundary_ok = False
        if prev is not None:
            delta = (dt - prev).total_seconds()
            if delta > step * 1.5:
                gaps += 1
        prev = dt

    first, last = times[0], times[-1]
    days = (last - first).total_seconds() / 86400.0
    expected = max(days * (86400.0 / step), 1.0)
    completeness = len(times) / expected if expected else None
    return {
        "exists": True,
        "bars": len(times),
        "start": first,
        "end": last,
        "history_days": round(days, 2),
        "completeness": round(completeness, 4) if completeness is not None else None,
        "duplicate_count": duplicates,
        "gap_count": gaps,
        "boundary_ok": boundary_ok,
    }


def evaluate_health(
    cov_1h: dict[str, Any],
    cov_4h: dict[str, Any],
    *,
    min_history_days: float = 540.0,
    min_completeness: float = 0.99,
) -> dict[str, Any]:
    """Pure gate: returns ready flag + exact blocking reasons."""
    blocks: list[str] = []
    if not cov_1h.get("exists"):
        blocks.append("BLOCKED: missing 1h OHLCV")
    if not cov_4h.get("exists"):
        blocks.append("BLOCKED: missing 4h OHLCV")

    if cov_1h.get("exists"):
        days_1h = cov_1h.get("history_days")
        if days_1h is None or float(days_1h) < min_history_days:
            blocks.append(
                f"BLOCKED: 1h history {days_1h if days_1h is not None else 0}d "
                f"< required {min_history_days:g}d"
            )
        comp = cov_1h.get("completeness")
        if comp is None or float(comp) < min_completeness:
            pct = (float(comp) * 100.0) if comp is not None else 0.0
            blocks.append(
                f"BLOCKED: 1h completeness {pct:.1f}% < {min_completeness * 100:.1f}%"
            )
        if int(cov_1h.get("bars") or 0) < _MIN_WARMUP_1H:
            blocks.append(
                f"BLOCKED: 1h bars {cov_1h.get('bars')} < warmup {_MIN_WARMUP_1H}"
            )
        if int(cov_1h.get("duplicate_count") or 0) > 0:
            blocks.append(
                f"BLOCKED: 1h duplicate timestamps={cov_1h.get('duplicate_count')}"
            )
        if int(cov_1h.get("gap_count") or 0) > 0 and (
            comp is None or float(comp) < min_completeness
        ):
            blocks.append(f"BLOCKED: 1h material gaps={cov_1h.get('gap_count')}")
        if not cov_1h.get("boundary_ok", True):
            blocks.append("BLOCKED: 1h inconsistent UTC candle boundaries")

    if cov_4h.get("exists"):
        days_4h = cov_4h.get("history_days")
        # Prefer full history on 4h as well (same calendar span).
        if days_4h is None or float(days_4h) < min_history_days * 0.95:
            blocks.append(
                f"BLOCKED: 4h history {days_4h if days_4h is not None else 0}d "
                f"< required {min_history_days:g}d"
            )
        comp4 = cov_4h.get("completeness")
        if comp4 is None or float(comp4) < min_completeness:
            pct = (float(comp4) * 100.0) if comp4 is not None else 0.0
            blocks.append(
                f"BLOCKED: 4h completeness {pct:.1f}% < {min_completeness * 100:.1f}%"
            )
        if int(cov_4h.get("bars") or 0) < _MIN_WARMUP_4H:
            blocks.append(
                f"BLOCKED: 4h bars {cov_4h.get('bars')} < warmup {_MIN_WARMUP_4H}"
            )
        if int(cov_4h.get("duplicate_count") or 0) > 0:
            blocks.append(
                f"BLOCKED: 4h duplicate timestamps={cov_4h.get('duplicate_count')}"
            )
        if not cov_4h.get("boundary_ok", True):
            blocks.append("BLOCKED: 4h inconsistent UTC candle boundaries")

    ready = len(blocks) == 0
    history_days = None
    if cov_1h.get("history_days") is not None:
        history_days = float(cov_1h["history_days"])
    return {
        "ready": ready,
        "blocks": blocks,
        "block_reason": blocks[0] if blocks else None,
        "history_days": history_days,
        "cov_1h": cov_1h,
        "cov_4h": cov_4h,
    }


async def check_candidate_data_health(
    symbol: str,
    *,
    min_history_days: float | None = None,
    min_completeness: float | None = None,
    queue_backfill: bool = True,
) -> dict[str, Any]:
    """Run health checks and transition DISCOVERED/DATA_PENDING ↔ DATA_READY."""
    sym = str(symbol).upper().strip()
    row = await strategy_candidate_registry.get_by_symbol(sym)
    if row is None:
        return {"status": "NOT_FOUND", "symbol": sym}

    # Allow injected coverage in tests via row fields when DB disabled.
    if strategy_candidate_registry.force_memory or not (
        db_manager.enabled and db_manager.engine is not None
    ):
        cov_1h = {
            "exists": row.get("ohlcv_1h_completeness") is not None
            or row.get("ohlcv_1h_start_utc") is not None,
            "bars": int(row.get("_test_bars_1h") or 0),
            "start": row.get("ohlcv_1h_start_utc"),
            "end": row.get("ohlcv_1h_end_utc"),
            "history_days": row.get("history_days"),
            "completeness": row.get("ohlcv_1h_completeness"),
            "duplicate_count": int(row.get("_test_dup_1h") or 0),
            "gap_count": int(row.get("_test_gap_1h") or 0),
            "boundary_ok": bool(row.get("_test_boundary_1h", True)),
        }
        cov_4h = {
            "exists": row.get("ohlcv_4h_completeness") is not None
            or row.get("ohlcv_4h_start_utc") is not None,
            "bars": int(row.get("_test_bars_4h") or 0),
            "start": row.get("ohlcv_4h_start_utc"),
            "end": row.get("ohlcv_4h_end_utc"),
            "history_days": row.get("_test_history_4h", row.get("history_days")),
            "completeness": row.get("ohlcv_4h_completeness"),
            "duplicate_count": int(row.get("_test_dup_4h") or 0),
            "gap_count": int(row.get("_test_gap_4h") or 0),
            "boundary_ok": bool(row.get("_test_boundary_4h", True)),
        }
        # If fields say missing 4h explicitly
        if row.get("ohlcv_4h_completeness") is None and row.get("ohlcv_4h_start_utc") is None:
            cov_4h["exists"] = False
    else:
        cov_1h = await _tf_coverage(sym, "1h")
        cov_4h = await _tf_coverage(sym, "4h")

    min_days = float(min_history_days if min_history_days is not None else DEFAULT_SELECTOR.min_history_days)
    min_comp = float(
        min_completeness
        if min_completeness is not None
        else DEFAULT_SELECTOR.min_ohlcv_completeness
    )
    verdict = evaluate_health(
        cov_1h, cov_4h, min_history_days=min_days, min_completeness=min_comp
    )
    now = datetime.now(timezone.utc)

    fields: dict[str, Any] = {
        "ohlcv_1h_start_utc": cov_1h.get("start"),
        "ohlcv_1h_end_utc": cov_1h.get("end"),
        "ohlcv_1h_completeness": cov_1h.get("completeness"),
        "ohlcv_4h_start_utc": cov_4h.get("start"),
        "ohlcv_4h_end_utc": cov_4h.get("end"),
        "ohlcv_4h_completeness": cov_4h.get("completeness"),
        "history_days": verdict.get("history_days"),
        "data_health_checked_at_utc": now,
        "data_health_block_reason": verdict.get("block_reason"),
        "risk_percent": 0.0,
    }
    await strategy_candidate_registry.update_fields(
        sym, fields, actor="candidate_data_health", note=verdict.get("block_reason")
    )

    state = str(row.get("state") or "")
    backfill_queued = False

    if verdict["ready"]:
        if state == "DISCOVERED":
            await strategy_candidate_registry.transition(
                sym,
                "DATA_PENDING",
                reason="data_checks_starting",
                actor="candidate_data_health",
            )
            state = "DATA_PENDING"
        if state in ("DATA_PENDING", "DISCOVERED"):
            if state == "DISCOVERED":
                await strategy_candidate_registry.transition(
                    sym,
                    "DATA_PENDING",
                    reason="awaiting_validation",
                    actor="candidate_data_health",
                )
            await strategy_candidate_registry.transition(
                sym,
                "DATA_READY",
                reason="1h_and_4h_health_passed",
                actor="candidate_data_health",
            )
            state = "DATA_READY"
    else:
        if state == "DISCOVERED":
            await strategy_candidate_registry.transition(
                sym,
                "DATA_PENDING",
                reason=verdict.get("block_reason"),
                actor="candidate_data_health",
            )
            state = "DATA_PENDING"
        elif state == "DATA_READY":
            # Data became invalid → back to pending
            await strategy_candidate_registry.transition(
                sym,
                "DATA_PENDING",
                reason=verdict.get("block_reason"),
                actor="candidate_data_health",
            )
            state = "DATA_PENDING"
        elif state == "BACKTEST_RUNNING":
            await strategy_candidate_registry.transition(
                sym,
                "DATA_PENDING",
                reason=verdict.get("block_reason"),
                actor="candidate_data_health",
            )
            state = "DATA_PENDING"
        else:
            # Stay DATA_PENDING with updated reason
            await strategy_candidate_registry.update_fields(
                sym,
                {"state_reason": verdict.get("block_reason")},
                actor="candidate_data_health",
            )

        if queue_backfill and (not cov_1h.get("exists") or not cov_4h.get("exists")):
            backfill_queued = await _queue_backfill(sym, cov_1h, cov_4h)

    result = {
        "status": "READY" if verdict["ready"] else "PENDING",
        "symbol": sym,
        "state": state,
        "ready": verdict["ready"],
        "blocks": verdict["blocks"],
        "block_reason": verdict.get("block_reason"),
        "backfill_queued": backfill_queued,
        "telegram_eligible": False,
        "risk_percent": 0.0,
    }
    logger.info("candidate_data_health", **{k: result[k] for k in ("symbol", "state", "ready", "block_reason")})
    return result


async def _queue_backfill(
    symbol: str, cov_1h: dict[str, Any], cov_4h: dict[str, Any]
) -> bool:
    """Best-effort queue of 1h+4h expansion — never 15m, never 1h-only silently."""
    try:
        from app.research.ohlcv_expand import ohlcv_expand_service

        queued = False
        # Prefer both timeframes always when either is missing.
        for tf, cov in (("1h", cov_1h), ("4h", cov_4h)):
            if cov.get("exists") and cov.get("completeness") is not None:
                if float(cov["completeness"]) >= DEFAULT_SELECTOR.min_ohlcv_completeness:
                    continue
            # Fire-and-forget expand if service exposes a simple API; otherwise log.
            enqueue = getattr(ohlcv_expand_service, "enqueue", None) or getattr(
                ohlcv_expand_service, "request_expand", None
            )
            if callable(enqueue):
                try:
                    enqueue(symbol=symbol, timeframe=tf)
                    queued = True
                except TypeError:
                    try:
                        enqueue(symbol, tf)
                        queued = True
                    except Exception:  # noqa: BLE001
                        pass
            else:
                logger.info(
                    "candidate_backfill_needed",
                    symbol=symbol,
                    timeframe=tf,
                    note="ohlcv_expand enqueue not available — operator must backfill",
                )
                queued = True  # counted as requested
        return queued
    except Exception as exc:  # noqa: BLE001
        logger.warning("candidate_backfill_queue_failed", symbol=symbol, error=str(exc))
        return False


async def run_data_health_batch(
    *,
    states: list[str] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Process DISCOVERED / DATA_PENDING candidates."""
    want = states or ["DISCOVERED", "DATA_PENDING"]
    rows = await strategy_candidate_registry.list_candidates(states=want, limit=limit)
    results = []
    for r in rows:
        results.append(await check_candidate_data_health(str(r["symbol"])))
    ready = sum(1 for x in results if x.get("ready"))
    return {
        "status": "OK",
        "checked": len(results),
        "ready": ready,
        "pending": len(results) - ready,
        "results": results,
    }
