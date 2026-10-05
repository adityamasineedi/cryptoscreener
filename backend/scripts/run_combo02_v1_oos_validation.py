"""Formal OOS validation for COMBO_02 v1 — calendar window, no tuning.

Window: 2025-07-01 → 2026-09-30 UTC (predeclared).
Symbols: BTCUSDT, ETHUSDT, SOLUSDT only.
Does not create paper or live trades.
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.research.bos_combinations import get_combination
from app.research.bos_strategy_comparison.htf import (
    HTF_ALIGNED,
    as_of_index_at_or_before,
    classify_htf_alignment,
    htf_trends_for_setup_bar,
)
from app.research.combo02_candidate_eligibility import max_losing_streak
from app.research.combination_engine import (
    evaluate_combination_at_bar,
    hl_intact_for_long,
)
from app.research.config import ResearchConfig
from app.research.query_utils import resolve_date_bounds
from app.research.service import BosResearchService, _load_research_candles, _signal_config
from app.research.trade_fees import DEFAULT_MAKER_FEE, DEFAULT_TAKER_FEE
from app.research.v1_production import (
    COMBO_ID,
    COMBO_VERSION,
    DEFAULT_PRINCIPAL_USD,
    FROZEN_V1_SYMBOLS,
    recommended_risk_usd,
)
from app.services.paper_sizing import resolve_min_notional, resolve_symbol_filters, size_paper_long
from app.signals._candle_utils import candle_time, ohlc
from app.signals.signal_engine import SignalEngine

OUT_DIR = Path(__file__).resolve().parents[1] / "reports" / "combo02_v1_oos"
OOS_START = "2025-07-01"
OOS_END = "2026-09-30"
SYMBOLS = sorted(FROZEN_V1_SYMBOLS)
TF = "1h"
MIN_COMPLETENESS = 0.99
MIN_SAMPLE_COMBINED = 20  # below this → INSUFFICIENT_SAMPLE unless other hard fails
MIN_SAMPLE_PER_SYMBOL_REVIEW = 5
MAX_DD_R = 6.0
MAX_LOSE_STREAK = 8
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


def _bucket_reason(reason: str | None) -> str:
    r = str(reason or "UNKNOWN")
    u = r.upper()
    if "HTF_CONFLICT" in u or u == "HTF_CONFLICT":
        return "HTF_CONFLICT"
    if "HTF_NEUTRAL" in u or "HTF_UNAVAILABLE" in u or "MISSING" in u and "HTF" in u:
        return "HTF_NEUTRAL_UNAVAILABLE"
    if "STRUCTURE_INVALID" in u or "HL_BROKEN" in u or "CHOCH" in u:
        return "STRUCTURE_INVALID"
    if "STOP_INVALID" in u or "STOP" in u and "COULD NOT" in u:
        return "STOP_INVALID"
    if "NO_TREND" in u or "TREND" in u and "NOT" in u:
        return "NO_TREND"
    if "TP1_R" in u or "R:R" in u or "MINIMUM" in u and "TP" in u:
        return "TP1_R_BELOW_MIN"
    if "GATES NOT SATISFIED" in u:
        return "GATES_NOT_SATISFIED"
    if "NOTIONAL_BELOW_MIN" in u:
        return "NOTIONAL_BELOW_MIN"
    if "QTY" in u or "ZERO_QUANTITY" in u:
        return "QTY_BELOW_MIN"
    return r[:80]


async def _window_coverage(
    symbol: str, timeframe: str, start: str, end: str
) -> dict[str, Any]:
    """Completeness + gaps inside the OOS calendar window (UTC)."""
    from app.services.database import db_manager

    bounds = resolve_date_bounds(start, end)
    start_ts = bounds["start"]
    end_excl = bounds["end_exclusive"]
    empty = {
        "exists": False,
        "bars": 0,
        "first": None,
        "last": None,
        "expected_bars": None,
        "completeness": None,
        "gap_count": 0,
        "gaps": [],
        "duplicate_count": 0,
    }
    if not db_manager.enabled or db_manager.engine is None or start_ts is None:
        return empty

    sql = text(
        """
        SELECT time FROM ohlcv
        WHERE symbol = :sym AND timeframe = :tf
          AND time >= :start AND time < :end_excl
        ORDER BY time ASC
        """
    )
    async with db_manager.engine.connect() as conn:
        rows = (
            await conn.execute(
                sql, {"sym": symbol, "tf": timeframe, "start": start_ts, "end_excl": end_excl}
            )
        ).fetchall()
    times: list[datetime] = []
    for (t,) in rows:
        dt = _parse_ts(t)
        if dt is not None:
            times.append(dt)
    if not times:
        return empty

    step = _TF_SECONDS[timeframe]
    expected = int((end_excl - start_ts).total_seconds() // step)
    # Last expected closed bar is end_excl - step
    gaps: list[dict[str, Any]] = []
    gap_count = 0
    duplicates = 0
    seen: set[float] = set()
    for i, ts in enumerate(times):
        key = ts.timestamp()
        if key in seen:
            duplicates += 1
        seen.add(key)
        if i == 0:
            continue
        delta = (ts - times[i - 1]).total_seconds()
        if delta > step * 1.5:
            missing = int(round(delta / step)) - 1
            gap_count += max(missing, 1)
            if len(gaps) < 50:
                gaps.append(
                    {
                        "after": times[i - 1].isoformat(),
                        "before": ts.isoformat(),
                        "missing_bars_est": max(missing, 1),
                    }
                )
    completeness = len(times) / expected if expected else None
    return {
        "exists": True,
        "bars": len(times),
        "first": times[0].isoformat(),
        "last": times[-1].isoformat(),
        "expected_bars": expected,
        "completeness": round(completeness, 6) if completeness is not None else None,
        "gap_count": gap_count,
        "gaps": gaps,
        "duplicate_count": duplicates,
        "pass": bool(completeness is not None and completeness >= MIN_COMPLETENESS),
    }


def _max_winning_streak(rs: list[float]) -> int:
    best = cur = 0
    for r in rs:
        if r > 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _metrics_from_trades(trades: list[dict[str, Any]], *, risk_usd: float) -> dict[str, Any]:
    closed = [t for t in trades if t.get("outcome") not in (None, "OPEN")]
    rs_g = [float(t["r_multiple"]) for t in closed if t.get("r_multiple") is not None]
    rs_n = [float(t["r_net"]) for t in closed if t.get("r_net") is not None]
    n = len(closed)
    wins = sum(1 for r in rs_g if r > 0)
    losses = sum(1 for r in rs_g if r <= 0)
    gross = sum(rs_g) if rs_g else 0.0
    net = sum(rs_n) if rs_n else 0.0
    # equity curve in R (net preferred)
    curve = [0.0]
    for r in rs_n or rs_g:
        curve.append(curve[-1] + r)
    peak = curve[0]
    max_dd = 0.0
    for x in curve:
        peak = max(peak, x)
        max_dd = max(max_dd, peak - x)
    hold = [int(t["holding_bars"]) for t in closed if t.get("holding_bars") is not None]
    exits = Counter(str(t.get("outcome") or "OTHER") for t in closed)
    fee_total = sum(float(t.get("fee_total_usd") or 0) for t in closed)
    pnl_net = sum(float(t["net_pnl_usd"]) for t in closed if t.get("net_pnl_usd") is not None)
    pnl_gross = sum(float(t["gross_pnl_usd"]) for t in closed if t.get("gross_pnl_usd") is not None)
    pos = sum(r for r in rs_g if r > 0)
    neg = abs(sum(r for r in rs_g if r < 0))
    pf = (pos / neg) if neg > 0 else (None if pos == 0 else float("inf"))
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / n) if n else None,
        "gross_R": gross,
        "net_R": net,
        "average_gross_R": (gross / n) if n else None,
        "average_net_R": (net / n) if n else None,
        "pnl_usd_gross": pnl_gross if closed else 0.0,
        "pnl_usd_net": pnl_net if closed else 0.0,
        "fees_usd": fee_total,
        "risk_usd": risk_usd,
        "max_drawdown_R": max_dd,
        "max_losing_streak": max_losing_streak(rs_g) if rs_g else 0,
        "max_winning_streak": _max_winning_streak(rs_g),
        "profit_factor": pf,
        "avg_holding_bars": (sum(hold) / len(hold)) if hold else None,
        "exit_breakdown": dict(exits),
    }


def _assert_trade_gates(
    trade: dict[str, Any],
    *,
    candles_1h: list[dict[str, Any]],
    candles_4h: list[dict[str, Any]],
    scfg: Any,
    risk_usd: float,
) -> dict[str, Any]:
    """Return {ok, violations[]} for one closed OOS trade."""
    violations: list[str] = []
    snap = trade.get("condition_snapshot") or {}
    t4 = str(snap.get("trend_4h") or "").upper()
    t1 = str(snap.get("trend_1h") or "").upper()
    align = str(snap.get("htf_alignment") or "").upper()
    if t4 != "BULLISH":
        violations.append(f"trend_4h={t4}")
    if t1 != "BULLISH":
        violations.append(f"trend_1h={t1}")
    if align != HTF_ALIGNED:
        violations.append(f"htf_alignment={align}")

    entry = float(trade.get("entry_price") or 0)
    stop = float(trade.get("stop_price") or 0)
    tp1 = trade.get("tp1")
    if not (stop < entry):
        violations.append("STOP_INVALID:stop_ge_entry")
    if tp1 is not None and entry > stop:
        tp1_r = (float(tp1) - entry) / (entry - stop)
        if tp1_r + 1e-9 < 2.0:
            violations.append(f"TP1_R={tp1_r:.4f}<2")

    # Recompute HL intact + HTF as_of at signal bar
    idx = trade.get("entry_index")
    if idx is not None and 0 <= int(idx) < len(candles_1h):
        i = int(idx)
        engine = SignalEngine(scfg)
        tf_a = engine.analyze_timeframe(
            str(trade.get("symbol")),
            TF,
            candles_1h,
            as_of_index=i,
            serialize_swings=False,
        )
        swings = tf_a.get("_swings_objs") or []
        hl_ok, hl_reason = hl_intact_for_long(candles_1h, swings, i)
        if not hl_ok:
            violations.append(f"HL_INTACT_FAIL:{hl_reason}")
        trend = tf_a.get("trend") or {}
        if str(trend.get("trend") or "").upper() != "BULLISH":
            violations.append(f"trend={trend.get('trend')}")
        bos = tf_a.get("bos") or {}
        if str(bos.get("state") or "").upper() != "CONFIRMED":
            violations.append(f"bos_state={bos.get('state')}")
        if str(bos.get("direction") or "").upper() != "BULLISH_BOS":
            violations.append(f"bos_dir={bos.get('direction')}")

        setup_ts = candle_time(candles_1h[i])
        if setup_ts is not None:
            # HTF must use bars <= setup time
            i4 = as_of_index_at_or_before(candles_4h, setup_ts)
            if i4 is not None and i4 >= 0:
                t4c = candle_time(candles_4h[i4])
                if t4c is not None and t4c > setup_ts:
                    violations.append("LOOKAHEAD:4h_bar_after_setup")
            trends = htf_trends_for_setup_bar(
                symbol=str(trade.get("symbol")),
                setup_candles=candles_1h,
                as_of_index=i,
                candles_4h=candles_4h,
                candles_1h=candles_1h,
                config=scfg,
            )
            cls = classify_htf_alignment(
                bos_direction="BULLISH_BOS",
                trend_4h=trends.get("trend_4h"),
                trend_1h=trends.get("trend_1h"),
            )
            if cls != HTF_ALIGNED:
                violations.append(f"recomputed_htf={cls}")

    # Sizing: no upsize past risk; min notional fail-closed semantics
    sized = size_paper_long(
        symbol=str(trade.get("symbol")),
        account_equity=DEFAULT_PRINCIPAL_USD,
        risk_percent=risk_usd / DEFAULT_PRINCIPAL_USD,
        entry=entry,
        stop=stop,
    )
    if sized.get("reason") in ("notional_below_min", "zero_quantity", "invalid_geometry_after_rounding"):
        # Trade existed in research blotter without exchange sizing — flag review
        violations.append(f"SIZING_NOTE:{sized.get('reason')}")

    return {"ok": len(violations) == 0, "violations": violations}


def _lookahead_check(
    *,
    symbol: str,
    candles_1h: list[dict[str, Any]],
    candles_4h: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    scfg: Any,
    rcfg: ResearchConfig,
) -> dict[str, Any]:
    """Mutate a future 4h bar and confirm earlier eval status/alignment unchanged."""
    combo = get_combination(COMBO_ID)
    assert combo is not None
    results: list[dict[str, Any]] = []
    for t in trades:
        idx = t.get("entry_index")
        if idx is None:
            continue
        i = int(idx)
        if i < 0 or i >= len(candles_1h):
            continue
        base = evaluate_combination_at_bar(
            symbol=symbol,
            timeframe=TF,
            candles=candles_1h,
            as_of_index=i,
            combination=combo,
            signal_config=scfg,
            research_config=rcfg,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
        )
        # Find a 4h bar strictly after setup time and mutate it
        setup_ts = candle_time(candles_1h[i])
        mutated = deepcopy(list(candles_4h))
        mutated_idx = None
        if setup_ts is not None:
            for j, c in enumerate(mutated):
                ct = candle_time(c)
                if ct is not None and ct > setup_ts:
                    mutated_idx = j
                    c2 = dict(c)
                    c2["high"] = float(c.get("high") or 0) * 1.5 + 1000.0
                    c2["close"] = float(c.get("close") or 0) * 1.5 + 1000.0
                    mutated[j] = c2
                    break
        after = evaluate_combination_at_bar(
            symbol=symbol,
            timeframe=TF,
            candles=candles_1h,
            as_of_index=i,
            combination=combo,
            signal_config=scfg,
            research_config=rcfg,
            candles_1h=candles_1h,
            candles_4h=mutated,
        )
        same = (
            base.get("status") == after.get("status")
            and (base.get("htf") or {}).get("htf_alignment")
            == (after.get("htf") or {}).get("htf_alignment")
            and base.get("entry_price") == after.get("entry_price")
        )
        results.append(
            {
                "signal_time": t.get("signal_time"),
                "entry_index": i,
                "mutated_4h_index": mutated_idx,
                "pass": same,
            }
        )
    fails = [r for r in results if not r["pass"]]
    return {
        "checked": len(results),
        "failed": len(fails),
        "pass": len(fails) == 0,
        "failures": fails[:20],
    }


def _htf_rejection_audit(
    *,
    symbol: str,
    candles_1h: list[dict[str, Any]],
    candles_4h: list[dict[str, Any]],
    combo02_trades: list[dict[str, Any]],
    local_trades: list[dict[str, Any]],
    scfg: Any,
    rcfg: ResearchConfig,
) -> dict[str, Any]:
    """Explain every COMBO_02_LOCAL trade that COMBO_02 rejected (HTF/structure)."""
    combo = get_combination(COMBO_ID)
    assert combo is not None
    c02_times = {
        str(t.get("signal_time"))
        for t in combo02_trades
        if t.get("outcome") not in (None, "OPEN")
    }
    counts: Counter[str] = Counter()
    details: list[dict[str, Any]] = []
    for t in local_trades:
        if t.get("outcome") in (None, "OPEN"):
            continue
        sig = str(t.get("signal_time"))
        if sig in c02_times:
            counts["PASSED_COMBO_02"] += 1
            continue
        idx = t.get("entry_index")
        if idx is None:
            counts["MISSING_INDEX"] += 1
            continue
        out = evaluate_combination_at_bar(
            symbol=symbol,
            timeframe=TF,
            candles=candles_1h,
            as_of_index=int(idx),
            combination=combo,
            signal_config=scfg,
            research_config=rcfg,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
        )
        bucket = _bucket_reason(out.get("reason"))
        counts[bucket] += 1
        if len(details) < 100:
            details.append(
                {
                    "signal_time": sig,
                    "status": out.get("status"),
                    "reason": out.get("reason"),
                    "bucket": bucket,
                    "htf": out.get("htf"),
                    "hl_intact_reason": out.get("hl_intact_reason"),
                }
            )
    return {
        "local_trades": len(
            [t for t in local_trades if t.get("outcome") not in (None, "OPEN")]
        ),
        "combo02_trades": len(
            [t for t in combo02_trades if t.get("outcome") not in (None, "OPEN")]
        ),
        "reject_counts": dict(counts.most_common()),
        "rejected_details": details,
        "note": (
            "Rejection audit = COMBO_02_LOCAL entries that COMBO_02 did not take, "
            "re-evaluated for explicit fail-closed reason."
        ),
    }


async def run_oos() -> dict[str, Any]:
    from app.config import get_settings
    from app.services.database import db_manager

    await db_manager.connect(get_settings())
    if not db_manager.enabled or db_manager.engine is None:
        raise SystemExit(f"DB unavailable: {db_manager.status}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scfg = _signal_config()
    rcfg = ResearchConfig()
    svc = BosResearchService()

    data_health: dict[str, Any] = {}
    for sym in SYMBOLS:
        data_health[sym] = {
            "1h": await _window_coverage(sym, "1h", OOS_START, OOS_END),
            "4h": await _window_coverage(sym, "4h", OOS_START, OOS_END),
        }

    data_fail = []
    for sym, cov in data_health.items():
        for tf in ("1h", "4h"):
            c = cov[tf]
            if not c.get("pass"):
                data_fail.append(
                    f"{sym} {tf} completeness={c.get('completeness')} "
                    f"gaps={c.get('gap_count')}"
                )

    # Per-symbol matrix with frozen risk (all 2% → $20)
    per_symbol: dict[str, Any] = {}
    all_trades: list[dict[str, Any]] = []

    for sym in SYMBOLS:
        risk = float(recommended_risk_usd(sym, TF, principal_usd=DEFAULT_PRINCIPAL_USD) or 20.0)
        matrix = await svc.strategy_matrix(
            combination_id=COMBO_ID,
            symbols=[sym],
            timeframes=[TF],
            direction="LONG",
            limit=50_000,  # ignored when dates set; kept for API
            risk_usd=risk,
            start_date=OOS_START,
            end_date=OOS_END,
            taker_fee=DEFAULT_TAKER_FEE,
            maker_fee=DEFAULT_MAKER_FEE,
            include_trades=True,
        )
        row = (matrix.get("rows") or [{}])[0]
        trades = list(row.get("trades") or [])
        candles_1h, eval_start, meta_1h = await _load_research_candles(
            sym, TF, start_date=OOS_START, end_date=OOS_END, warmup_bars=max(rcfg.min_bars, 100)
        )
        candles_4h, _, meta_4h = await _load_research_candles(
            sym, "4h", start_date=OOS_START, end_date=OOS_END, warmup_bars=max(rcfg.min_bars // 4, 50)
        )

        closed_trades = [t for t in trades if t.get("outcome") not in (None, "OPEN")]
        gate_checks = [
            _assert_trade_gates(
                t, candles_1h=candles_1h, candles_4h=candles_4h, scfg=scfg, risk_usd=risk
            )
            for t in closed_trades
        ]
        violation_details = [
            {"signal_time": closed_trades[i].get("signal_time"), **g}
            for i, g in enumerate(gate_checks)
            if not g["ok"]
        ]
        lookahead = _lookahead_check(
            symbol=sym,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
            trades=closed_trades,
            scfg=scfg,
            rcfg=rcfg,
        )
        # LOCAL A/B for HTF rejection audit (research baseline; not v1)
        local_matrix = await svc.strategy_matrix(
            combination_id="COMBO_02_LOCAL",
            symbols=[sym],
            timeframes=[TF],
            direction="LONG",
            limit=50_000,
            risk_usd=risk,
            start_date=OOS_START,
            end_date=OOS_END,
            taker_fee=DEFAULT_TAKER_FEE,
            maker_fee=DEFAULT_MAKER_FEE,
            include_trades=True,
        )
        local_row = (local_matrix.get("rows") or [{}])[0]
        local_trades = list(local_row.get("trades") or [])

        print(f"HTF rejection audit {sym}…")
        funnel = _htf_rejection_audit(
            symbol=sym,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
            combo02_trades=trades,
            local_trades=local_trades,
            scfg=scfg,
            rcfg=rcfg,
        )
        metrics = _metrics_from_trades(trades, risk_usd=risk)
        per_symbol[sym] = {
            "risk_usd": risk,
            "risk_percent": risk / DEFAULT_PRINCIPAL_USD,
            "load_meta_1h": meta_1h,
            "load_meta_4h": meta_4h,
            "eval_start": eval_start,
            "bars_1h_loaded": len(candles_1h),
            "bars_4h_loaded": len(candles_4h),
            "metrics": metrics,
            "gate_integrity": {
                "trades_checked": len(gate_checks),
                "violations": len(violation_details),
                "pass": len(violation_details) == 0,
                "details": violation_details[:50],
            },
            "lookahead": lookahead,
            "rejection_audit": funnel,
            "local_metrics": _metrics_from_trades(local_trades, risk_usd=risk),
            "trades": trades,
        }
        for t in trades:
            if t.get("outcome") not in (None, "OPEN"):
                all_trades.append(t)

    combined = _metrics_from_trades(all_trades, risk_usd=20.0)
    # Classification
    hard_fails: list[str] = []
    reviews: list[str] = []
    if data_fail:
        hard_fails.append(f"DATA_HEALTH: {'; '.join(data_fail)}")
    for sym, block in per_symbol.items():
        if not block["lookahead"]["pass"]:
            hard_fails.append(f"LOOKAHEAD_FAIL:{sym}")
        if not block["gate_integrity"]["pass"]:
            hard_fails.append(
                f"GATE_INTEGRITY_FAIL:{sym} n={block['gate_integrity']['violations']}"
            )
    if combined["average_net_R"] is None or combined["average_net_R"] <= 0:
        hard_fails.append(
            f"NET_EXPECTANCY: average_net_R={combined.get('average_net_R')}"
        )
    if float(combined.get("max_drawdown_R") or 0) > MAX_DD_R + 1e-9:
        hard_fails.append(f"MAX_DD={combined.get('max_drawdown_R')} > {MAX_DD_R}")
    if int(combined.get("max_losing_streak") or 0) > MAX_LOSE_STREAK:
        hard_fails.append(
            f"LOSE_STREAK={combined.get('max_losing_streak')} > {MAX_LOSE_STREAK}"
        )

    n_combined = int(combined.get("trades") or 0)
    if n_combined < MIN_SAMPLE_COMBINED and not hard_fails:
        verdict = "INSUFFICIENT_SAMPLE"
    elif hard_fails:
        verdict = "OOS_FAIL"
    else:
        for sym, block in per_symbol.items():
            n = int(block["metrics"].get("trades") or 0)
            if n < MIN_SAMPLE_PER_SYMBOL_REVIEW:
                reviews.append(f"{sym}:n={n}<{MIN_SAMPLE_PER_SYMBOL_REVIEW}")
            avg = block["metrics"].get("average_net_R")
            if avg is not None and avg <= 0:
                reviews.append(f"{sym}:avg_net_R={avg}")
        verdict = "OOS_PASS_WITH_REVIEW" if reviews else "OOS_PASS"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "label": "COMBO_02_V1_OOS_VALIDATION",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "combo_id": COMBO_ID,
        "combo_version": COMBO_VERSION,
        "strategy_id": "COMBO_02_V1",
        "direction": "LONG",
        "symbols": SYMBOLS,
        "setup_timeframe": TF,
        "oos_window": {
            "start": f"{OOS_START}T00:00:00Z",
            "end_inclusive": f"{OOS_END}T23:59:59Z",
            "mode": "calendar_dates_utc",
            "role": "OOS_validation",
            "partitions": {
                "base_training": "2022-10-05 → 2024-12-31",
                "oos_development": "2025-01-01 → 2025-06-30",
                "oos_validation": "2025-07-01 → 2026-09-30",
            },
        },
        "risk_profile": {
            "principal_usd": DEFAULT_PRINCIPAL_USD,
            "note": "Frozen v1 books: flat 2% → $20/R for BTC/ETH/SOL 1h",
            "per_symbol_risk_usd": {
                s: float(recommended_risk_usd(s, TF) or 20.0) for s in SYMBOLS
            },
            "taker_fee": DEFAULT_TAKER_FEE,
            "maker_fee": DEFAULT_MAKER_FEE,
        },
        "pass_thresholds": {
            "min_completeness": MIN_COMPLETENESS,
            "average_net_R": ">0 combined",
            "max_drawdown_R": MAX_DD_R,
            "max_losing_streak": MAX_LOSE_STREAK,
            "min_combined_trades_for_pass": MIN_SAMPLE_COMBINED,
        },
        "data_health": data_health,
        "data_health_pass": len(data_fail) == 0,
        "per_symbol": {
            s: {k: v for k, v in block.items() if k != "trades"}
            for s, block in per_symbol.items()
        },
        "combined_metrics": combined,
        "hard_fails": hard_fails,
        "reviews": reviews,
        "verdict": verdict,
        "behavior_changed": False,
        "parameters_changed": False,
        "paper_or_live_trades_created": False,
        "disclaimer": (
            "Historical OOS validation only. Not a profitability claim. "
            "No paper or live trades were created by this run."
        ),
    }

    # Trade-level JSON
    trades_path = OUT_DIR / f"oos_trades_{stamp}.json"
    trades_path.write_text(
        json.dumps(
            {
                "window": report["oos_window"],
                "trades": all_trades,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    # Also CSV-ish lines for convenience
    csv_path = OUT_DIR / f"oos_trades_{stamp}.csv"
    headers = [
        "symbol",
        "signal_time",
        "entry_price",
        "stop_price",
        "tp1",
        "outcome",
        "r_multiple",
        "r_net",
        "net_pnl_usd",
        "holding_bars",
        "htf_alignment",
        "trend_4h",
        "trend_1h",
    ]
    lines = [",".join(headers)]
    for t in all_trades:
        snap = t.get("condition_snapshot") or {}
        lines.append(
            ",".join(
                str(x)
                for x in [
                    t.get("symbol"),
                    t.get("signal_time"),
                    t.get("entry_price"),
                    t.get("stop_price"),
                    t.get("tp1"),
                    t.get("outcome"),
                    t.get("r_multiple"),
                    t.get("r_net"),
                    t.get("net_pnl_usd"),
                    t.get("holding_bars"),
                    snap.get("htf_alignment"),
                    snap.get("trend_4h"),
                    snap.get("trend_1h"),
                ]
            )
        )
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report_path = OUT_DIR / f"oos_report_{stamp}.json"
    report["deliverables"] = {
        "report": str(report_path),
        "trades_json": str(trades_path),
        "trades_csv": str(csv_path),
    }
    # Attach trade counts only in full dump file with trades nested lightly
    full = dict(report)
    full["trades"] = all_trades
    report_path.write_text(json.dumps(full, indent=2, default=str), encoding="utf-8")
    latest = OUT_DIR / "oos_report_latest.json"
    latest.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")

    # Markdown summary
    md_path = OUT_DIR / f"oos_report_{stamp}.md"
    md = [
        f"# COMBO_02 v1 OOS Validation — {verdict}",
        "",
        f"**Window:** {OOS_START} → {OOS_END} UTC  ",
        f"**Symbols:** {', '.join(SYMBOLS)}  ",
        f"**Risk:** frozen v1 2% ($20/R)  ",
        f"**Paper/live created:** no  ",
        f"**Parameters changed:** no  ",
        "",
        "## Combined metrics",
        "",
        f"- Trades: **{combined['trades']}**",
        f"- WR: {combined['win_rate']}",
        f"- Avg net R: **{combined['average_net_R']}**",
        f"- Net PnL: {combined['pnl_usd_net']}",
        f"- Max DD: {combined['max_drawdown_R']}R",
        f"- Max lose streak: {combined['max_losing_streak']}",
        f"- PF: {combined['profit_factor']}",
        f"- Exits: {combined['exit_breakdown']}",
        "",
        "## Per symbol",
        "",
    ]
    for sym in SYMBOLS:
        m = per_symbol[sym]["metrics"]
        md.append(
            f"### {sym}\n"
            f"- n={m['trades']} WR={m['win_rate']} avgNetR={m['average_net_R']} "
            f"DD={m['max_drawdown_R']} streak={m['max_losing_streak']} "
            f"lookahead={'PASS' if per_symbol[sym]['lookahead']['pass'] else 'FAIL'} "
            f"gates={'PASS' if per_symbol[sym]['gate_integrity']['pass'] else 'FAIL'}"
        )
    md.extend(
        [
            "",
            "## Data health",
            "",
        ]
    )
    for sym, cov in data_health.items():
        md.append(
            f"- {sym} 1h comp={cov['1h'].get('completeness')} "
            f"gaps={cov['1h'].get('gap_count')} | "
            f"4h comp={cov['4h'].get('completeness')} gaps={cov['4h'].get('gap_count')}"
        )
    md.extend(["", "## Hard fails", ""])
    md.extend([f"- {x}" for x in hard_fails] or ["- (none)"])
    md.extend(["", "## Reviews", ""])
    md.extend([f"- {x}" for x in reviews] or ["- (none)"])
    md.extend(
        [
            "",
            f"## Verdict: `{verdict}`",
            "",
            "No paper or live trade was created.",
        ]
    )
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"\nVERDICT={verdict}")
    print(f"Wrote {report_path}")
    print(f"Wrote {md_path}")
    print(f"Wrote {trades_path}")
    print(f"Wrote {csv_path}")
    print(f"combined n={combined['trades']} avgNetR={combined['average_net_R']} "
          f"DD={combined['max_drawdown_R']} streak={combined['max_losing_streak']}")
    for sym in SYMBOLS:
        m = per_symbol[sym]["metrics"]
        print(
            f"  {sym}: n={m['trades']} avgNetR={m['average_net_R']} "
            f"WR={m['win_rate']} DD={m['max_drawdown_R']}"
        )
    return report


def main() -> None:
    asyncio.run(run_oos())


if __name__ == "__main__":
    main()
