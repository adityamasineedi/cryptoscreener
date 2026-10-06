"""Causal range bounds from confirmed 1H swing high/low (no future candles)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.signals.swing_detector import detect_swings


def confirmed_range_bounds(
    candles_1h: Sequence[Mapping[str, Any]],
    as_of_index: int,
    *,
    left: int = 2,
    right: int = 2,
    symbol: str = "",
    min_width_frac: float = 1e-6,
) -> tuple[float, float] | None:
    """Return (range_low, range_high) from last confirmed swings at as_of_index.

    Uses only candles[:as_of_index+1]. Returns None if swings are not yet
    confirmed (WAIT).
    """
    if as_of_index < left + right:
        return None
    swings = detect_swings(
        candles_1h,
        left=left,
        right=right,
        symbol=symbol,
        timeframe="1h",
        as_of_index=as_of_index,
    )
    highs = [s.price for s in swings if s.swing_type == "HIGH"]
    lows = [s.price for s in swings if s.swing_type == "LOW"]
    if not highs or not lows:
        return None
    range_high = float(highs[-1])
    range_low = float(lows[-1])
    if range_high <= range_low:
        return None
    width = range_high - range_low
    mid = 0.5 * (range_high + range_low)
    if mid <= 0 or width / mid < min_width_frac:
        return None
    return range_low, range_high


def bounds_from_snapshot_swings(
    swing_low: float | None,
    swing_high: float | None,
    *,
    min_width_frac: float = 1e-6,
) -> tuple[float, float] | None:
    """Use PIT snapshot swing_high/swing_low when already computed."""
    if swing_low is None or swing_high is None:
        return None
    rl, rh = float(swing_low), float(swing_high)
    if rh <= rl:
        return None
    mid = 0.5 * (rh + rl)
    if mid <= 0 or (rh - rl) / mid < min_width_frac:
        return None
    return rl, rh
