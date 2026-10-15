"""Aggregate experiment records. Unfilled and incomplete trades are not zero-return fills."""

from __future__ import annotations

from typing import Sequence

from app.research.ohlcv_structure_experiment.engine import SetupRecord


def assign_window(record: SetupRecord, split: int, length: int) -> str:
    if record.status == "incomplete_window" or record.lifecycle_end >= length:
        return "incomplete"
    if record.event.break_index < split and record.lifecycle_end < split:
        return "development"
    if record.event.break_index >= split and record.lifecycle_end < length:
        return "later_slice"
    return "boundary_excluded"


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _drawdown(values: list[float]) -> float | None:
    if not values:
        return None
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def summarize(records: Sequence[SetupRecord], *, bar_count: int) -> dict:
    fills = [row for row in records if row.status == "filled" and row.net_r is not None]
    nets = [float(row.net_r) for row in fills if row.net_r is not None]
    gains = sum(value for value in nets if value > 0)
    losses = sum(value for value in nets if value < 0)
    close_known = [row for row in records if row.close_back_10 is not None]
    post_known = [row for row in fills if row.post_entry_close_back_10 is not None]
    holding = [row.holding_bars for row in fills if row.holding_bars is not None]
    stop_atr = [row.stop_distance_atr for row in fills if row.stop_distance_atr is not None]
    target_atr = [row.target_distance_atr for row in fills if row.target_distance_atr is not None]
    outcomes = [row.outcome for row in fills]

    def share(name: str) -> float | None:
        if not fills:
            return None
        return sum(1 for outcome in outcomes if outcome == name) / len(fills)

    return {
        "candidates": len(records),
        "qualified": sum(1 for row in records if row.qualified_at is not None),
        "fills": len(fills),
        "skips": sum(1 for row in records if row.status.startswith("skipped")),
        "expirations": sum(1 for row in records if row.status == "expired"),
        "invalidations": sum(1 for row in records if row.status == "invalidated"),
        "incomplete_windows": sum(1 for row in records if row.status == "incomplete_window"),
        "net_expectancy_r": _mean(nets),
        "net_pnl_r": sum(nets) if nets else None,
        "max_drawdown_r": _drawdown(nets),
        "profit_factor": (gains / abs(losses)) if losses < 0 else None,
        "win_rate": (sum(1 for value in nets if value > 0) / len(nets)) if nets else None,
        "target_pct": share("target"),
        "stop_pct": share("stop"),
        "timeout_pct": share("timeout"),
        "ambiguous_stop_and_target": sum(1 for row in fills if row.ambiguous),
        "close_back_10_pct": (
            sum(1 for row in close_known if row.close_back_10) / len(close_known)
            if close_known
            else None
        ),
        "close_back_10_denominator": len(close_known),
        "post_entry_close_back_10_pct": (
            sum(1 for row in post_known if row.post_entry_close_back_10) / len(post_known)
            if post_known
            else None
        ),
        "post_entry_close_back_10_denominator": len(post_known),
        "exposure_bars": sum(holding) if holding else 0,
        "exposure_fraction": (sum(holding) / bar_count) if holding and bar_count else None,
        "average_holding_bars": _mean([float(value) for value in holding]),
        "average_stop_distance_atr": _mean([float(value) for value in stop_atr]),
        "average_target_distance_atr": _mean([float(value) for value in target_atr]),
        "average_mae_r": _mean([float(row.mae_r) for row in fills if row.mae_r is not None]),
        "average_mfe_r": _mean([float(row.mfe_r) for row in fills if row.mfe_r is not None]),
        "fees_r": sum(float(row.fee_r or 0) for row in fills),
        "funding_r": sum(float(row.funding_r or 0) for row in fills),
        "funding_note": (
            "Funding is included when timestamps overlap a funding print. "
            "Zero means no overlapping funding print was available for these fills."
        ),
    }
