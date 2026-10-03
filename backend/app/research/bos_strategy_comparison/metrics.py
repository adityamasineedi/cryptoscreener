"""Performance metrics for BOS strategy comparison research."""

from __future__ import annotations

import random
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any, Sequence

from app.research.bos_strategy_comparison.config import MIN_SYMBOL_SAMPLE
from app.research.bos_strategy_comparison.schemas import DirectionMetrics, StrategyTrade
from app.research.metrics import expectancy_r, max_drawdown_r, profit_factor
from app.research.trade_fees import enrich_trade_execution


def _rate(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return num / den


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def bootstrap_expectancy_ci(
    rs: Sequence[float],
    *,
    n_samples: int = 500,
    ci: float = 0.95,
    seed: int = 42,
) -> tuple[float | None, float | None]:
    if not rs:
        return None, None
    if len(rs) == 1:
        v = float(rs[0])
        return v, v
    rng = random.Random(seed)
    means: list[float] = []
    n = len(rs)
    for _ in range(max(1, n_samples)):
        sample = [rs[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    alpha = (1.0 - ci) / 2.0
    lo_i = int(alpha * len(means))
    hi_i = min(len(means) - 1, int((1.0 - alpha) * len(means)))
    return means[lo_i], means[hi_i]


def compute_direction_metrics(
    trades: Sequence[StrategyTrade],
    *,
    direction: str = "ALL",
    min_sample: int = 0,
    use_net: bool = False,
    bootstrap_samples: int = 500,
) -> DirectionMetrics:
    closed = [
        t
        for t in trades
        if t.exit_reason is not None and t.exit_reason != "OPEN"
        and (direction == "ALL" or t.direction == direction)
    ]
    key = "net_R" if use_net else "gross_R"
    rs = [
        float(getattr(t, key))
        for t in closed
        if getattr(t, key) is not None
    ]
    sample = len(closed)
    wins = [r for r in rs if r > 1e-12]
    losses = [r for r in rs if r < -1e-12]
    be = [r for r in rs if abs(r) <= 1e-12]

    if sample <= 0 or not rs:
        equity: list[float] = []
        max_dd = None
    else:
        equity = [0.0]
        for r in rs:
            equity.append(equity[-1] + r)
        max_dd, _ = max_drawdown_r(equity)

    tp1 = sum(1 for t in closed if t.exit_reason == "TP1")
    tp2 = sum(1 for t in closed if t.exit_reason == "TP2")
    tp3 = sum(1 for t in closed if t.exit_reason == "TP3")
    sl = sum(1 for t in closed if t.exit_reason == "SL")
    mfe = [t.MFE_R for t in closed if t.MFE_R is not None]
    mae = [t.MAE_R for t in closed if t.MAE_R is not None]
    holds = [t.holding_period for t in closed if t.holding_period is not None]
    ci_lo, ci_hi = bootstrap_expectancy_ci(rs, n_samples=bootstrap_samples)

    status = "OK"
    if min_sample > 0 and sample < min_sample:
        status = "INSUFFICIENT_SAMPLE"

    return DirectionMetrics(
        direction=direction,
        sample_size=sample,
        win_rate=_rate(len(wins), sample),
        loss_rate=_rate(len(losses), sample),
        breakeven_rate=_rate(len(be), sample),
        average_R=(sum(rs) / len(rs)) if rs else None,
        median_R=statistics.median(rs) if rs else None,
        expectancy_R=expectancy_r(rs),
        gross_expectancy=expectancy_r(
            [float(t.gross_R) for t in closed if t.gross_R is not None]
        ),
        net_expectancy=expectancy_r(
            [float(t.net_R) for t in closed if t.net_R is not None]
        ),
        profit_factor=profit_factor(rs),
        total_R=sum(rs) if rs else None,
        max_drawdown_R=max_dd,
        average_win=(sum(wins) / len(wins)) if wins else None,
        average_loss=(sum(losses) / len(losses)) if losses else None,
        largest_win=max(wins) if wins else None,
        largest_loss=min(losses) if losses else None,
        average_MFE_R=(sum(mfe) / len(mfe)) if mfe else None,
        average_MAE_R=(sum(mae) / len(mae)) if mae else None,
        average_holding_period=(sum(holds) / len(holds)) if holds else None,
        tp1_hit_rate=_rate(tp1, sample),
        tp2_hit_rate=_rate(tp2, sample),
        tp3_hit_rate=_rate(tp3, sample),
        sl_rate=_rate(sl, sample),
        expectancy_ci_low=ci_lo,
        expectancy_ci_high=ci_hi,
        sample_status=status,
    )


def metrics_summary_dict(m: DirectionMetrics) -> dict[str, Any]:
    d = m.to_dict()
    # Compact API shape
    return {
        "direction": d["direction"],
        "sample_size": d["sample_size"],
        "win_rate": d["win_rate"],
        "loss_rate": d["loss_rate"],
        "breakeven_rate": d["breakeven_rate"],
        "expectancy_R": d["expectancy_R"],
        "gross_expectancy": d["gross_expectancy"],
        "net_expectancy": d["net_expectancy"],
        "profit_factor": d["profit_factor"],
        "max_drawdown_R": d["max_drawdown_R"],
        "total_R": d["total_R"],
        "average_R": d["average_R"],
        "median_R": d["median_R"],
        "average_win": d["average_win"],
        "average_loss": d["average_loss"],
        "largest_win": d["largest_win"],
        "largest_loss": d["largest_loss"],
        "average_MFE_R": d["average_MFE_R"],
        "average_MAE_R": d["average_MAE_R"],
        "average_holding_period": d["average_holding_period"],
        "tp1_hit_rate": d["tp1_hit_rate"],
        "tp2_hit_rate": d["tp2_hit_rate"],
        "tp3_hit_rate": d["tp3_hit_rate"],
        "sl_rate": d["sl_rate"],
        "expectancy_ci_low": d["expectancy_ci_low"],
        "expectancy_ci_high": d["expectancy_ci_high"],
        "sample_status": d["sample_status"],
        "label": "Historical Result",
    }


def by_year_breakdown(trades: Sequence[StrategyTrade]) -> dict[str, Any]:
    groups: dict[str, list[StrategyTrade]] = defaultdict(list)
    for t in trades:
        ts = _parse_ts(t.entry_time)
        if ts is None:
            continue
        groups[str(ts.year)].append(t)
    out: dict[str, Any] = {}
    for year in sorted(groups):
        m = compute_direction_metrics(groups[year], direction="ALL")
        out[year] = {
            "n": m.sample_size,
            "win_rate": m.win_rate,
            "expectancy": m.expectancy_R,
            "profit_factor": m.profit_factor,
            "max_DD": m.max_drawdown_R,
            "label": "Historical Result",
        }
    return out


def by_symbol_breakdown(
    trades: Sequence[StrategyTrade],
    *,
    min_sample: int = MIN_SYMBOL_SAMPLE,
) -> dict[str, Any]:
    groups: dict[str, list[StrategyTrade]] = defaultdict(list)
    for t in trades:
        groups[t.symbol].append(t)
    out: dict[str, Any] = {}
    for sym, subset in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        m = compute_direction_metrics(subset, direction="ALL", min_sample=min_sample)
        payload = metrics_summary_dict(m)
        if m.sample_size < min_sample:
            payload["sample_status"] = "INSUFFICIENT_SAMPLE"
            payload["ranking"] = "NOT_RANKED_INSUFFICIENT_SAMPLE"
        out[sym] = payload
    return out


def by_htf_breakdown(trades: Sequence[StrategyTrade]) -> dict[str, Any]:
    groups: dict[str, list[StrategyTrade]] = defaultdict(list)
    for t in trades:
        key = t.htf_alignment or "HTF_NEUTRAL_UNAVAILABLE"
        groups[key].append(t)
    out: dict[str, Any] = {}
    for key in ("HTF_ALIGNED", "HTF_CONFLICT", "HTF_NEUTRAL_UNAVAILABLE"):
        subset = groups.get(key, [])
        m = compute_direction_metrics(subset, direction="ALL")
        out[key] = {
            "n": m.sample_size,
            "win_rate": m.win_rate,
            "expectancy": m.expectancy_R,
            "profit_factor": m.profit_factor,
            "max_DD": m.max_drawdown_R,
            "label": "Historical Result",
        }
    return out


def chronological_splits(
    trades: Sequence[StrategyTrade],
    *,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
    oos_fraction: float = 0.20,
) -> dict[str, list[StrategyTrade]]:
    """Split closed trades by entry_time order — never shuffle."""
    closed = [
        t for t in trades if t.exit_reason is not None and t.exit_reason != "OPEN"
    ]
    closed = sorted(
        closed,
        key=lambda t: (_parse_ts(t.entry_time) or datetime.min, t.entry_index),
    )
    n = len(closed)
    if n <= 0:
        return {"train": [], "validation": [], "oos": []}
    total = train_fraction + validation_fraction + oos_fraction
    tf, vf = train_fraction / total, validation_fraction / total
    t_end = int(n * tf)
    v_end = t_end + int(n * vf)
    if v_end >= n:
        v_end = max(t_end, n - 1)
    return {
        "train": closed[:t_end],
        "validation": closed[t_end:v_end],
        "oos": closed[v_end:],
    }


def apply_net_r(
    trades: Sequence[StrategyTrade],
    *,
    taker_fee: float,
    maker_fee: float,
    slippage_rate: float = 0.0,
    cost_multiplier: float = 1.0,
    risk_usd: float = 100.0,
) -> list[StrategyTrade]:
    """Attach net_R using existing fee helper; optional cost multiplier / slippage."""
    fee_t = taker_fee * cost_multiplier
    fee_m = maker_fee * cost_multiplier
    slip = slippage_rate * cost_multiplier
    out: list[StrategyTrade] = []
    for t in trades:
        # Gross R already on trade; adjust exit conceptually via fee blotter
        enriched = enrich_trade_execution(
            {
                "entry_price": t.entry_price,
                "stop_price": t.sl,
                "exit_price": t.exit_price,
                "direction": t.direction,
                "r_multiple": t.gross_R,
                "entry_type": t.entry_type,
            },
            risk_usd=risk_usd,
            taker_fee=fee_t,
            maker_fee=fee_m,
        )
        net = enriched.get("r_net")
        if net is not None and slip > 0 and t.risk_per_unit and t.risk_per_unit > 0:
            # Approximate extra slippage cost in R units (round-trip)
            slip_r = (2.0 * slip * t.entry_price) / t.risk_per_unit
            net = float(net) - slip_r
        t.net_R = float(net) if net is not None else t.gross_R
        out.append(t)
    return out


def cost_sensitivity(
    trades: Sequence[StrategyTrade],
    *,
    taker_fee: float,
    maker_fee: float,
    slippage_rate: float,
    multipliers: Sequence[float] = (1.0, 1.5, 2.0),
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for mult in multipliers:
        rebuilt = [StrategyTrade(**t.to_dict()) for t in trades]
        apply_net_r(
            rebuilt,
            taker_fee=taker_fee,
            maker_fee=maker_fee,
            slippage_rate=slippage_rate,
            cost_multiplier=float(mult),
        )
        m = compute_direction_metrics(rebuilt, direction="ALL", use_net=True)
        sign = "POSITIVE" if (m.expectancy_R or 0) > 0 else "NEGATIVE_OR_FLAT"
        out[f"{mult}x"] = {
            "cost_multiplier": mult,
            "n": m.sample_size,
            "net_expectancy": m.expectancy_R,
            "profit_factor": m.profit_factor,
            "max_drawdown_R": m.max_drawdown_R,
            "sign": sign,
            "label": "Historical Result",
        }
    return out
