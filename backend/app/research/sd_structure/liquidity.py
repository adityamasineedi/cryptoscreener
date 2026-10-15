"""Equal-high and equal-low pools, then a wick sweep that closes back inside.

Research filter only. A pool is two confirmed swings of the same type whose
prices sit within ``EQUAL_PRICE_TOLERANCE``. Stops are assumed to rest just
beyond that cluster. The sweep is a later wick through the cluster that
closes back inside, and it must finish before the structure confirmation.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.signals._candle_utils import ohlc
from app.signals.schemas import SwingRecord

# Relative distance between the two swing prices.
EQUAL_PRICE_TOLERANCE = 0.002
MIN_PIVOT_GAP_BARS = 3


def _pair_level(kind: str, earlier: float, later: float) -> float:
    if kind == "HIGH":
        return max(earlier, later)
    return min(earlier, later)


def _within_tolerance(earlier: float, later: float) -> bool:
    mid = (earlier + later) / 2.0
    if mid <= 0:
        return False
    return abs(earlier - later) / mid <= EQUAL_PRICE_TOLERANCE


def _close_already_through(
    candles: Sequence[Mapping[str, Any]],
    *,
    kind: str,
    level: float,
    start: int,
    end: int,
) -> bool:
    last = min(end, len(candles) - 1)
    for index in range(max(0, start), last + 1):
        close = ohlc(candles, index)[3]
        if close is None:
            continue
        if kind == "HIGH" and float(close) > level:
            return True
        if kind == "LOW" and float(close) < level:
            return True
    return False


def completed_equal_sweep(
    candles: Sequence[Mapping[str, Any]],
    swings: Sequence[SwingRecord],
    *,
    direction: str,
    before_index: int,
    right_bars: int,
) -> dict[str, Any] | None:
    """Most recent equal-high or equal-low sweep strictly before ``before_index``.

    LONG uses equal lows (stops under the cluster). SHORT uses equal highs.
    Both pivots must already be confirmed, and no close may have run the
    cluster before the sweep bar.
    """
    kind = "LOW" if direction == "LONG" else "HIGH"
    event_name = "EQL_SWEEP" if kind == "LOW" else "EQH_SWEEP"
    points = [
        swing
        for swing in swings
        if swing.swing_type == kind and swing.bar_index + right_bars < before_index
    ]
    if len(points) < 2 or before_index < 2:
        return None

    for later_pos in range(len(points) - 1, 0, -1):
        later = points[later_pos]
        for earlier_pos in range(later_pos - 1, -1, -1):
            earlier = points[earlier_pos]
            if later.bar_index - earlier.bar_index < MIN_PIVOT_GAP_BARS:
                continue
            if not _within_tolerance(float(earlier.price), float(later.price)):
                continue
            level = _pair_level(kind, float(earlier.price), float(later.price))
            sweep_from = later.bar_index + right_bars
            if _close_already_through(
                candles,
                kind=kind,
                level=level,
                start=earlier.bar_index + 1,
                end=sweep_from,
            ):
                continue
            for index in range(before_index - 1, sweep_from, -1):
                if index >= len(candles):
                    continue
                _, high, low, close = ohlc(candles, index)
                if None in (high, low, close):
                    continue
                if _close_already_through(
                    candles,
                    kind=kind,
                    level=level,
                    start=sweep_from + 1,
                    end=index - 1,
                ):
                    continue
                swept = (
                    float(low) < level and float(close) > level
                    if kind == "LOW"
                    else float(high) > level and float(close) < level
                )
                if not swept:
                    continue
                return {
                    "event": event_name,
                    "direction": direction,
                    "level": level,
                    "first_index": earlier.bar_index,
                    "second_index": later.bar_index,
                    "sweep_index": index,
                    "wick": float(low) if kind == "LOW" else float(high),
                    "close": float(close),
                }
    return None
