"""Reporting slices for supply/demand research rows.

These do not add or remove trades from the backtest. They describe the
closed sample after fees.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

MIN_CLOSED_SAMPLE = 100
# Same rate as SignalConfig.slippage_rate. Applied only on SD result rows.
SD_SLIPPAGE_RATE = 0.0002
DAILY_LOSS_R = 2.0


def _when(trade: Mapping[str, Any]) -> str:
    raw = trade.get("exit_time") or trade.get("signal_time") or trade.get("entry_time") or ""
    return str(raw)


def _r_net(trade: Mapping[str, Any]) -> float | None:
    value = trade.get("r_net")
    if value is None:
        return None
    return float(value)


def _avg(trades: Sequence[Mapping[str, Any]]) -> float | None:
    vals = [r for t in trades if (r := _r_net(t)) is not None]
    if not vals:
        return None
    return sum(vals) / float(len(vals))


def _day(trade: Mapping[str, Any]) -> str:
    raw = _when(trade)
    if len(raw) >= 10:
        return raw[:10]
    return ""


def chronological_split(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """First 70% of closed trades by time, then the remaining holdout."""
    ordered = sorted(trades, key=_when)
    cut = int(len(ordered) * 0.7)
    if cut < 1 or cut >= len(ordered):
        return {
            "development_n": len(ordered),
            "development_avg_r_net": _avg(ordered),
            "holdout_n": 0,
            "holdout_avg_r_net": None,
        }
    develop = ordered[:cut]
    holdout = ordered[cut:]
    return {
        "development_n": len(develop),
        "development_avg_r_net": _avg(develop),
        "holdout_n": len(holdout),
        "holdout_avg_r_net": _avg(holdout),
    }


def daily_loss_slice(
    trades: Sequence[Mapping[str, Any]],
    *,
    max_loss_r: float = DAILY_LOSS_R,
) -> dict[str, Any]:
    """Trades that would remain if a UTC day stopped after losing ``max_loss_r``."""
    ordered = sorted(trades, key=_when)
    day_r: dict[str, float] = {}
    kept: list[Mapping[str, Any]] = []
    for trade in ordered:
        day = _day(trade)
        if day and day_r.get(day, 0.0) <= -abs(max_loss_r):
            continue
        kept.append(trade)
        rnet = _r_net(trade)
        if day and rnet is not None:
            day_r[day] = day_r.get(day, 0.0) + rnet
    return {
        "max_loss_r": abs(max_loss_r),
        "kept_n": len(kept),
        "skipped_n": len(ordered) - len(kept),
        "avg_r_net": _avg(kept),
    }


def regime_counts(trades: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"trend": 0, "range": 0, "unknown": 0}
    for trade in trades:
        diag = trade.get("sd_structure") or {}
        regime = str(diag.get("market_regime") or "unknown")
        if regime not in counts:
            regime = "unknown"
        counts[regime] += 1
    return counts


def review_closed_trades(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    return {
        "min_closed_sample": MIN_CLOSED_SAMPLE,
        "sample_warning": n < MIN_CLOSED_SAMPLE,
        "slippage_rate": SD_SLIPPAGE_RATE,
        "market_type": "perpetual",
        "session": "24h",
        "contract_roll": "none",
        "funding_applied": False,
        "event_days": "not_removed",
        "random_entry_baseline": "not_run",
        "chronological_split": chronological_split(trades),
        "daily_loss_slice": daily_loss_slice(trades),
        "regime_counts": regime_counts(trades),
    }
