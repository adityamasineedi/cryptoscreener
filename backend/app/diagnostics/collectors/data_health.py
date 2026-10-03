"""Data health drilldown — measurable coverage only; null ≠ 0."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.diagnostics.constants import HealthStatus


TF_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dataset_row(
    *,
    dataset: str,
    status: str,
    provider: str | None,
    universe_size: int | None,
    available_count: int | None,
    fresh_count: int | None,
    stale_count: int | None,
    missing_count: int | None,
    unavailable_count: int | None,
    last_update: str | None,
    note: str | None = None,
) -> dict[str, Any]:
    coverage = None
    if universe_size is not None and available_count is not None and universe_size > 0:
        coverage = round(100.0 * available_count / universe_size, 2)
    return {
        "dataset": dataset,
        "status": status,
        "provider": provider,
        "universe_size": universe_size,
        "available_count": available_count,
        "fresh_count": fresh_count,
        "stale_count": stale_count,
        "missing_count": missing_count,
        "unavailable_count": unavailable_count,
        "coverage_percent": coverage,
        "last_update": last_update,
        "note": note,
    }


def _status_from_counts(
    *,
    available: int | None,
    stale: int | None,
    unavailable: int | None,
    waiting: int | None,
    universe: int | None,
) -> str:
    if universe is None:
        return HealthStatus.UNKNOWN.value
    if universe == 0:
        return HealthStatus.WAITING.value
    if unavailable and available == 0:
        return HealthStatus.UNAVAILABLE.value
    if stale and (available or 0) == 0:
        return HealthStatus.STALE.value
    if (available or 0) > 0 and (stale or 0) > 0:
        return HealthStatus.DEGRADED.value
    if (available or 0) > 0:
        return HealthStatus.HEALTHY.value
    if waiting:
        return HealthStatus.WAITING.value
    return HealthStatus.UNKNOWN.value


async def collect_data_health(settings: Any) -> dict[str, Any]:
    from app.services.coverage import build_data_coverage

    checked = _utcnow_iso()
    out: dict[str, Any] = {
        "phase": 2,
        "last_checked": checked,
        "collector": "diagnostics.collectors.data_health.collect_data_health",
        "file": "backend/app/diagnostics/collectors/data_health.py",
        "function": "collect_data_health",
        "datasets": [],
        "status": HealthStatus.UNKNOWN.value,
        "reason": None,
        "errors": [],
    }

    try:
        cov = await build_data_coverage(settings)
    except Exception as exc:  # noqa: BLE001
        out["status"] = HealthStatus.UNAVAILABLE.value
        out["reason"] = f"coverage_build_failed:{exc.__class__.__name__}"
        out["errors"].append({"code": "COVERAGE", "error": str(exc)})
        return out

    universe = int(cov.get("symbols") or 0) or None
    datasets: list[dict[str, Any]] = []

    # Ticker
    ticker = cov.get("ticker") or {}
    datasets.append(
        _dataset_row(
            dataset="ticker",
            status=_status_from_counts(
                available=ticker.get("available") or ticker.get("live"),
                stale=ticker.get("stale"),
                unavailable=ticker.get("unavailable"),
                waiting=ticker.get("waiting"),
                universe=universe,
            ),
            provider="binance",
            universe_size=universe,
            available_count=ticker.get("available") or ticker.get("live"),
            fresh_count=ticker.get("live"),
            stale_count=ticker.get("stale"),
            missing_count=ticker.get("waiting"),
            unavailable_count=ticker.get("unavailable"),
            last_update=cov.get("timestamp"),
        )
    )

    # OHLCV per TF from coverage
    ohlcv = cov.get("ohlcv") or {}
    for tf, row in ohlcv.items():
        if not isinstance(row, dict):
            continue
        available = row.get("covered") or row.get("available") or row.get("live")
        waiting = row.get("waiting")
        datasets.append(
            _dataset_row(
                dataset=f"ohlcv_{tf}",
                status=_status_from_counts(
                    available=available,
                    stale=row.get("stale"),
                    unavailable=row.get("unavailable"),
                    waiting=waiting,
                    universe=row.get("total") or universe,
                ),
                provider="binance",
                universe_size=row.get("total") or universe,
                available_count=available,
                fresh_count=row.get("live") or available,
                stale_count=row.get("stale"),
                missing_count=waiting,
                unavailable_count=row.get("unavailable"),
                last_update=cov.get("timestamp"),
            )
        )

    # OI
    oi = cov.get("open_interest") or {}
    oi_avail = None
    if any(oi.get(k) is not None for k in ("live", "cached", "stale", "available")):
        oi_avail = int(oi.get("live") or 0) + int(oi.get("cached") or 0) + int(
            oi.get("stale") or 0
        )
        if oi.get("available") is not None:
            oi_avail = int(oi.get("available"))
    datasets.append(
        _dataset_row(
            dataset="oi",
            status=_status_from_counts(
                available=oi_avail,
                stale=oi.get("stale"),
                unavailable=oi.get("unavailable"),
                waiting=oi.get("waiting"),
                universe=universe,
            ),
            provider="binance",
            universe_size=universe,
            available_count=oi_avail,
            fresh_count=oi.get("live"),
            stale_count=oi.get("stale"),
            missing_count=oi.get("waiting"),
            unavailable_count=oi.get("unavailable"),
            last_update=cov.get("timestamp"),
        )
    )

    # Liquidations — status from coverage/providers, never invent event count=0 as missing
    liq = cov.get("liquidations") or {}
    datasets.append(
        _dataset_row(
            dataset="liquidations",
            status=str(liq.get("status") or HealthStatus.WAITING.value).upper()
            if liq
            else HealthStatus.WAITING.value,
            provider="binance",
            universe_size=universe,
            available_count=liq.get("symbols_with_events"),
            fresh_count=None,
            stale_count=None,
            missing_count=None,
            unavailable_count=None,
            last_update=cov.get("timestamp"),
            note="Event stream — zero events is WAITING/STALE, not fabricated missing market data",
        )
    )

    # Fundamentals
    funds = cov.get("fundamentals") or {}
    for key, label in (("market_cap", "market_cap"), ("tvl", "tvl")):
        row = funds.get(key) or {}
        avail = None
        if any(row.get(k) is not None for k in ("live", "cached", "stale")):
            avail = int(row.get("live") or 0) + int(row.get("cached") or 0) + int(
                row.get("stale") or 0
            )
        datasets.append(
            _dataset_row(
                dataset=label,
                status=_status_from_counts(
                    available=avail,
                    stale=row.get("stale"),
                    unavailable=row.get("unavailable"),
                    waiting=row.get("waiting"),
                    universe=universe,
                ),
                provider="coingecko" if label == "market_cap" else "defillama",
                universe_size=universe,
                available_count=avail,
                fresh_count=row.get("live"),
                stale_count=row.get("stale"),
                missing_count=row.get("waiting"),
                unavailable_count=row.get("unavailable"),
                last_update=cov.get("timestamp"),
            )
        )

    # Sentiment
    sent = cov.get("sentiment") or {}
    if sent:
        avail = None
        if any(sent.get(k) is not None for k in ("live", "cached", "stale")):
            avail = int(sent.get("live") or 0) + int(sent.get("cached") or 0) + int(
                sent.get("stale") or 0
            )
        datasets.append(
            _dataset_row(
                dataset="sentiment",
                status=_status_from_counts(
                    available=avail,
                    stale=sent.get("stale"),
                    unavailable=sent.get("unavailable"),
                    waiting=sent.get("waiting"),
                    universe=universe,
                ),
                provider="free_social",
                universe_size=universe,
                available_count=avail,
                fresh_count=sent.get("live"),
                stale_count=sent.get("stale"),
                missing_count=sent.get("waiting"),
                unavailable_count=sent.get("unavailable"),
                last_update=cov.get("timestamp"),
            )
        )

    # Signals / structure — presence from engine_store if measurable
    try:
        from app.services.engine_store import engine_store

        sig_n = len(getattr(engine_store, "setup_signals", {}) or {})
        datasets.append(
            _dataset_row(
                dataset="signals",
                status=HealthStatus.HEALTHY.value
                if sig_n > 0
                else HealthStatus.WAITING.value,
                provider="internal",
                universe_size=universe,
                available_count=sig_n if sig_n else None,
                fresh_count=sig_n if sig_n else None,
                stale_count=None,
                missing_count=None,
                unavailable_count=None,
                last_update=None,
                note="In-memory setup signal cache size",
            )
        )
    except Exception as exc:  # noqa: BLE001
        out["errors"].append({"code": "SIGNALS", "error": str(exc)})
        datasets.append(
            _dataset_row(
                dataset="signals",
                status=HealthStatus.UNKNOWN.value,
                provider="internal",
                universe_size=universe,
                available_count=None,
                fresh_count=None,
                stale_count=None,
                missing_count=None,
                unavailable_count=None,
                last_update=None,
                note="Signal store unavailable for diagnostics",
            )
        )

    # Structure / S/D / Trade Plan — mark UNKNOWN unless measured
    for name in ("structure", "supply_demand", "trade_plan"):
        datasets.append(
            _dataset_row(
                dataset=name,
                status=HealthStatus.UNKNOWN.value,
                provider="internal",
                universe_size=universe,
                available_count=None,
                fresh_count=None,
                stale_count=None,
                missing_count=None,
                unavailable_count=None,
                last_update=None,
                note="No aggregate coverage probe in Phase 2 — UNKNOWN not zero",
            )
        )

    # Research data manifest summary if DB available
    try:
        from app.services.database import db_manager

        if db_manager.enabled and db_manager.engine is not None:
            async with db_manager.engine.begin() as conn:
                exists = (
                    await conn.execute(
                        text(
                            """
                            SELECT EXISTS (
                              SELECT 1 FROM information_schema.tables
                              WHERE table_schema='public' AND table_name='research_data_manifest'
                            )
                            """
                        )
                    )
                ).scalar()
                if exists:
                    row = (
                        await conn.execute(
                            text(
                                """
                                SELECT count(*),
                                       count(*) FILTER (WHERE status = 'COMPLETE'),
                                       count(*) FILTER (WHERE status = 'FAILED'),
                                       max(updated_at)
                                FROM research_data_manifest
                                """
                            )
                        )
                    ).one()
                    total = int(row[0] or 0)
                    complete = int(row[1] or 0)
                    failed = int(row[2] or 0)
                    datasets.append(
                        _dataset_row(
                            dataset="research_data",
                            status=(
                                HealthStatus.ERROR.value
                                if failed and not complete
                                else HealthStatus.HEALTHY.value
                                if complete
                                else HealthStatus.WAITING.value
                            ),
                            provider="binance",
                            universe_size=total if total else None,
                            available_count=complete if total else None,
                            fresh_count=None,
                            stale_count=None,
                            missing_count=None,
                            unavailable_count=failed if total else None,
                            last_update=row[3].isoformat() if row[3] else None,
                        )
                    )
                else:
                    datasets.append(
                        _dataset_row(
                            dataset="research_data",
                            status=HealthStatus.UNKNOWN.value,
                            provider="binance",
                            universe_size=None,
                            available_count=None,
                            fresh_count=None,
                            stale_count=None,
                            missing_count=None,
                            unavailable_count=None,
                            last_update=None,
                            note="research_data_manifest table not present",
                        )
                    )
    except Exception as exc:  # noqa: BLE001
        out["errors"].append({"code": "RESEARCH_DATA", "error": str(exc)})

    out["datasets"] = datasets
    measured = [
        d["status"]
        for d in datasets
        if d["status"] not in (HealthStatus.UNKNOWN.value,)
    ]
    if measured:
        from app.diagnostics.constants import worst_status

        out["status"] = worst_status(*measured)
        out["reason"] = f"{len(datasets)} datasets reported from measured coverage"
    else:
        out["status"] = HealthStatus.UNKNOWN.value
        out["reason"] = "No measured dataset statuses"
    return out


async def drilldown_ohlcv(
    *,
    symbol: str,
    timeframe: str,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Expensive gap scan — only on explicit drilldown."""
    from app.services.database import db_manager

    sym = symbol.upper().strip()
    tf = timeframe.lower().strip()
    interval_ms = TF_MS.get(tf)
    checked = _utcnow_iso()
    base: dict[str, Any] = {
        "dataset": "ohlcv",
        "symbol": sym,
        "timeframe": tf,
        "source": "binance",
        "storage_table": "ohlcv",
        "expected_interval_ms": interval_ms,
        "earliest_timestamp": None,
        "latest_timestamp": None,
        "candle_count": None,
        "gap_count": None,
        "duplicate_count": None,
        "invalid_count": None,
        "freshness_seconds": None,
        "last_successful_update": None,
        "provider_state": None,
        "status": HealthStatus.UNKNOWN.value,
        "reason": None,
        "last_checked": checked,
        "collector": "diagnostics.collectors.data_health.drilldown_ohlcv",
        "file": "backend/app/diagnostics/collectors/data_health.py",
        "function": "drilldown_ohlcv",
    }

    if interval_ms is None:
        base["status"] = HealthStatus.UNKNOWN.value
        base["reason"] = f"Unsupported timeframe for gap scan: {tf}"
        return base

    if not db_manager.enabled or db_manager.engine is None:
        # Fall back to in-memory ohlcv_store
        try:
            from app.services.ohlcv_store import ohlcv_store

            candles = ohlcv_store.get_closed(sym, tf) or []
            if not candles:
                base["status"] = HealthStatus.WAITING.value
                base["reason"] = "No closed candles in memory; DB disabled"
                base["candle_count"] = 0
                return base
            times = [c.timestamp for c in candles if getattr(c, "timestamp", None)]
            # timestamps may be datetime
            base["candle_count"] = len(candles)
            if times:
                first, last = times[0], times[-1]
                base["earliest_timestamp"] = (
                    first.isoformat() if hasattr(first, "isoformat") else str(first)
                )
                base["latest_timestamp"] = (
                    last.isoformat() if hasattr(last, "isoformat") else str(last)
                )
            base["status"] = HealthStatus.HEALTHY.value
            base["reason"] = "In-memory candles only (DB disabled)"
            base["gap_count"] = None  # not scanned without ordered ms
            return base
        except Exception as exc:  # noqa: BLE001
            base["status"] = HealthStatus.UNAVAILABLE.value
            base["reason"] = f"DB disabled and memory read failed: {exc.__class__.__name__}"
            return base

    try:
        async with db_manager.engine.begin() as conn:
            stats = (
                await conn.execute(
                    text(
                        """
                        SELECT min(time), max(time), count(*),
                               count(*) - count(DISTINCT time) AS dupes,
                               count(*) FILTER (
                                 WHERE high < low OR open IS NULL OR close IS NULL
                               ) AS invalid
                        FROM ohlcv
                        WHERE symbol = :s AND timeframe = :tf
                        """
                    ),
                    {"s": sym, "tf": tf},
                )
            ).one()
            earliest, latest, count, dupes, invalid = stats
            base["earliest_timestamp"] = earliest.isoformat() if earliest else None
            base["latest_timestamp"] = latest.isoformat() if latest else None
            base["candle_count"] = int(count or 0)
            base["duplicate_count"] = int(dupes or 0)
            base["invalid_count"] = int(invalid or 0)
            if latest:
                base["freshness_seconds"] = max(
                    0.0, (datetime.now(timezone.utc) - latest).total_seconds()
                )
                base["last_successful_update"] = latest.isoformat()

            # Gap count via generate_series on bounded range only if count reasonable
            if earliest and latest and count and int(count) <= 50_000:
                gap_row = (
                    await conn.execute(
                        text(
                            """
                            WITH expected AS (
                              SELECT generate_series(
                                CAST(:start AS timestamptz),
                                CAST(:end AS timestamptz),
                                (CAST(:iv_ms AS bigint) * INTERVAL '1 millisecond')
                              ) AS t
                            ),
                            present AS (
                              SELECT DISTINCT time AS t
                              FROM ohlcv
                              WHERE symbol = :s AND timeframe = :tf
                            )
                            SELECT count(*) FROM expected e
                            LEFT JOIN present p ON p.t = e.t
                            WHERE p.t IS NULL
                            """
                        ),
                        {
                            "start": earliest,
                            "end": latest,
                            "iv_ms": int(interval_ms),
                            "s": sym,
                            "tf": tf,
                        },
                    )
                ).scalar()
                base["gap_count"] = int(gap_row or 0)
            elif count and int(count) > 50_000:
                base["gap_count"] = None
                base["reason"] = "Gap scan skipped — candle_count > 50000 (explicit heavy scan)"
            else:
                base["gap_count"] = None

        if base["candle_count"] == 0:
            base["status"] = HealthStatus.MISSING.value
            base["reason"] = "No OHLCV rows for symbol/timeframe"
        elif (base.get("invalid_count") or 0) > 0:
            base["status"] = HealthStatus.WARNING.value
            base["reason"] = "Invalid OHLC rows present"
        elif (base.get("gap_count") or 0) > 0:
            base["status"] = HealthStatus.DEGRADED.value
            base["reason"] = f"{base['gap_count']} gaps detected"
        else:
            base["status"] = HealthStatus.HEALTHY.value
            base["reason"] = base.get("reason") or "Series present"
    except Exception as exc:  # noqa: BLE001
        base["status"] = HealthStatus.UNAVAILABLE.value
        base["reason"] = f"drilldown_failed:{exc.__class__.__name__}"
    return base
