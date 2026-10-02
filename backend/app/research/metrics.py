"""Metric formulas for BOS combination research.

All rates include every eligible setup in the denominator unless EXCLUDE
ambiguous handling removed them. Winning-trade-only metrics are never used
as the primary expectancy / profit-factor definition.

Formulas
--------
expectancy_R:
    mean(realized_R) over all included setups.

profit_factor:
    sum(positive_R) / abs(sum(negative_R)); None if no losses.

max_drawdown_R:
    maximum peak-to-trough decline on the cumulative-R equity curve.

average_R / median_R:
    arithmetic mean / median of realized_R (all included setups).

TP*_hit_rate / SL_rate:
    count(outcome == X) / sample_size.

MAE_R / MFE_R:
    adverse / favorable excursion divided by initial risk |entry - stop|.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any, Sequence

from app.research.schemas import CombinationResult, ResearchTrade


def _rate(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return num / den


def max_drawdown_r(equity_curve: Sequence[float]) -> tuple[float | None, list[float]]:
    if not equity_curve:
        return None, []
    peak = equity_curve[0]
    max_dd = 0.0
    dd_curve: list[float] = []
    for e in equity_curve:
        peak = max(peak, e)
        dd = e - peak
        dd_curve.append(dd)
        max_dd = min(max_dd, dd)
    return abs(max_dd), dd_curve


def profit_factor(rs: Sequence[float]) -> float | None:
    gains = sum(r for r in rs if r > 0)
    losses = sum(abs(r) for r in rs if r < 0)
    if losses <= 0:
        return None if gains <= 0 else float("inf") if gains > 0 else None
    return gains / losses


def expectancy_r(rs: Sequence[float]) -> float | None:
    if not rs:
        return None
    return sum(rs) / len(rs)


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def setup_frequency_series(trades: Sequence[ResearchTrade]) -> list[dict[str, Any]]:
    buckets: dict[str, int] = defaultdict(int)
    for t in trades:
        ts = _parse_ts(t.signal_time)
        if ts is None:
            continue
        key = ts.date().isoformat()
        buckets[key] += 1
    return [{"date": k, "count": buckets[k], "sample_size": buckets[k]} for k in sorted(buckets)]


def signals_per_period(
    trades: Sequence[ResearchTrade],
) -> tuple[float | None, float | None]:
    times = [_parse_ts(t.signal_time) for t in trades]
    times = [t for t in times if t is not None]
    if len(times) < 2:
        n = len(trades)
        return (float(n) if n else None), (float(n) if n else None)
    span_sec = (max(times) - min(times)).total_seconds()
    if span_sec <= 0:
        return None, None
    days = span_sec / 86400.0
    per_day = len(trades) / days
    return per_day, per_day * 7.0


def compute_metrics(
    trades: Sequence[ResearchTrade],
    *,
    combination_id: str,
    description: str,
    symbol: str | None = None,
    timeframe: str | None = None,
    direction: str | None = "ALL",
    period_start: str | None = None,
    period_end: str | None = None,
    period_label: str = "FULL",
    data_coverage: float | None = None,
    data_quality: str = "OK",
    condition_definition: dict[str, Any] | None = None,
    include_open: bool = False,
) -> CombinationResult:
    """Aggregate trade list into CombinationResult. sample_size always set."""
    closed = [
        t
        for t in trades
        if t.outcome is not None and (include_open or t.outcome != "OPEN")
    ]
    # EXCLUDE handling: ambiguous already removed upstream when configured
    rs = [float(t.r_multiple) for t in closed if t.r_multiple is not None]
    sample = len(closed)

    tp1 = sum(1 for t in closed if t.outcome == "TP1")
    tp2 = sum(1 for t in closed if t.outcome == "TP2")
    tp3 = sum(1 for t in closed if t.outcome == "TP3")
    sl = sum(1 for t in closed if t.outcome == "SL")
    amb = sum(1 for t in closed if t.outcome == "AMBIGUOUS_INTRABAR" or t.ambiguous)

    # Empty sample must not invent a zero drawdown (equity=[0.0] → max_dd=0).
    if sample <= 0 or not rs:
        equity = []
        max_dd, dd_curve = None, []
    else:
        equity = [0.0]
        for r in rs:
            equity.append(equity[-1] + r)
        max_dd, dd_curve = max_drawdown_r(equity)

    mae_rs = [t.mae_r for t in closed if t.mae_r is not None]
    mfe_rs = [t.mfe_r for t in closed if t.mfe_r is not None]
    holds = [t.holding_bars for t in closed if t.holding_bars is not None]

    gains = sum(r for r in rs if r > 0)
    losses = sum(r for r in rs if r < 0)
    per_day, per_week = signals_per_period(closed)

    by_dir: dict[str, Any] = {}
    for d in ("LONG", "SHORT"):
        subset = [t for t in closed if t.direction == d]
        sub_rs = [float(t.r_multiple) for t in subset if t.r_multiple is not None]
        by_dir[d] = {
            "sample_size": len(subset),
            "average_R": (sum(sub_rs) / len(sub_rs)) if sub_rs else None,
            "expectancy_R": expectancy_r(sub_rs),
            "tp1_hits": sum(1 for t in subset if t.outcome == "TP1"),
            "sl_hits": sum(1 for t in subset if t.outcome == "SL"),
        }

    by_group: dict[str, Any] = {}
    for t in closed:
        g = t.asset_group or "UNKNOWN"
        by_group.setdefault(g, {"sample_size": 0, "r_sum": 0.0})
        by_group[g]["sample_size"] += 1
        if t.r_multiple is not None:
            by_group[g]["r_sum"] += float(t.r_multiple)
    for g, payload in by_group.items():
        n = payload["sample_size"]
        payload["average_R"] = (payload["r_sum"] / n) if n else None
        del payload["r_sum"]

    result = CombinationResult(
        combination_id=combination_id,
        description=description,
        period_start=period_start,
        period_end=period_end,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction or "ALL",
        sample_size=sample,
        long_setups=sum(1 for t in closed if t.direction == "LONG"),
        short_setups=sum(1 for t in closed if t.direction == "SHORT"),
        tp1_hits=tp1,
        tp2_hits=tp2,
        tp3_hits=tp3,
        sl_hits=sl,
        ambiguous_trades=amb,
        ambiguous_count=amb,
        tp1_hit_rate=_rate(tp1, sample),
        tp2_hit_rate=_rate(tp2, sample),
        tp3_hit_rate=_rate(tp3, sample),
        sl_rate=_rate(sl, sample),
        average_R=(sum(rs) / len(rs)) if rs else None,
        median_R=statistics.median(rs) if rs else None,
        expectancy_R=expectancy_r(rs),
        profit_factor=profit_factor(rs),
        gross_profit_R=gains,
        gross_loss_R=losses,
        max_drawdown_R=max_dd,
        average_MAE_R=(sum(mae_rs) / len(mae_rs)) if mae_rs else None,
        average_MFE_R=(sum(mfe_rs) / len(mfe_rs)) if mfe_rs else None,
        average_holding_time=(sum(holds) / len(holds)) if holds else None,
        median_holding_time=statistics.median(holds) if holds else None,
        signals_per_day=per_day,
        signals_per_week=per_week,
        data_coverage=data_coverage,
        data_quality=data_quality,
        regime="REGIME_NOT_AVAILABLE",
        period_label=period_label,
        r_values=list(rs),
        equity_curve_r=list(equity),
        drawdown_curve_r=dd_curve,
        setup_frequency=setup_frequency_series(closed),
        by_direction=by_dir,
        by_asset_group=by_group,
        by_regime={"REGIME_NOT_AVAILABLE": {"sample_size": sample}},
        condition_definition=condition_definition or {},
    )
    return result


def summarize_breakdown(
    trades: Sequence[ResearchTrade],
    *,
    key_fn,
    combination_id: str,
    description: str,
) -> dict[str, Any]:
    groups: dict[str, list[ResearchTrade]] = defaultdict(list)
    for t in trades:
        groups[str(key_fn(t))].append(t)
    out: dict[str, Any] = {}
    for key, subset in groups.items():
        m = compute_metrics(
            subset,
            combination_id=combination_id,
            description=description,
            direction="ALL",
        )
        out[key] = {
            "sample_size": m.sample_size,
            "tp1_hit_rate": m.tp1_hit_rate,
            "tp2_hit_rate": m.tp2_hit_rate,
            "sl_rate": m.sl_rate,
            "average_R": m.average_R,
            "expectancy_R": m.expectancy_R,
            "profit_factor": m.profit_factor,
            "max_drawdown_R": m.max_drawdown_R,
            "average_MAE_R": m.average_MAE_R,
            "average_MFE_R": m.average_MFE_R,
        }
    return out
