"""Configurable swing high/low detection with no look-ahead.

A swing at index i is confirmed only after `swing_right_bars` candles have closed
beyond i. Historical evaluation never uses candles that were not yet available.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr as calc_atr
from app.signals._candle_utils import candle_time, ohlc, series_ohlcv
from app.signals.config import SignalConfig, TimeframeSwingConfig
from app.signals.schemas import SwingRecord


def detect_swings(
    candles: Sequence[Mapping[str, Any]],
    *,
    left: int,
    right: int,
    symbol: str = "",
    timeframe: str = "",
    atr_period: int = 14,
    minimum_swing_distance_atr: float = 0.0,
    as_of_index: int | None = None,
) -> list[SwingRecord]:
    """Detect confirmed swings using only candles[:as_of_index+1] (inclusive).

    No look-ahead: candidate at i requires candles through i+right to exist
    within the as-of window.
    """
    if left < 1 or right < 1 or not candles:
        return []
    end = len(candles) - 1 if as_of_index is None else min(as_of_index, len(candles) - 1)
    if end < left + right:
        return []

    _, highs, lows, closes, _ = series_ohlcv(list(candles[: end + 1]))
    atr_val = calc_atr(highs, lows, closes, atr_period) or 0.0
    min_dist = atr_val * float(minimum_swing_distance_atr)

    # Last index that can host a confirmed swing: needs right bars after it
    last_confirmable = end - right
    out: list[SwingRecord] = []
    for i in range(left, last_confirmable + 1):
        h = highs[i]
        l = lows[i]
        left_h = highs[i - left : i]
        right_h = highs[i + 1 : i + right + 1]
        left_l = lows[i - left : i]
        right_l = lows[i + 1 : i + right + 1]
        if not left_h or not right_h:
            continue
        confirmed_at = candle_time(candles[i + right])
        ts = candle_time(candles[i])
        if h > max(left_h) and h > max(right_h):
            if min_dist > 0:
                neighbors = max(max(left_h), max(right_h))
                if (h - neighbors) < min_dist:
                    continue
            strength = 1.0
            if atr_val > 0:
                strength = min(3.0, (h - max(max(left_h), max(right_h))) / atr_val)
            out.append(
                SwingRecord(
                    symbol=symbol,
                    timeframe=timeframe,
                    swing_type="HIGH",
                    price=h,
                    timestamp=ts,
                    bar_index=i,
                    strength=float(strength),
                    confirmed_at=confirmed_at,
                )
            )
        if l < min(left_l) and l < min(right_l):
            if min_dist > 0:
                neighbors = min(min(left_l), min(right_l))
                if (neighbors - l) < min_dist:
                    continue
            strength = 1.0
            if atr_val > 0:
                strength = min(3.0, (min(min(left_l), min(right_l)) - l) / atr_val)
            out.append(
                SwingRecord(
                    symbol=symbol,
                    timeframe=timeframe,
                    swing_type="LOW",
                    price=l,
                    timestamp=ts,
                    bar_index=i,
                    strength=float(strength),
                    confirmed_at=confirmed_at,
                )
            )
    out.sort(key=lambda s: s.bar_index)
    return _label_swings(out)


def _label_swings(swings: list[SwingRecord]) -> list[SwingRecord]:
    last_high: float | None = None
    last_low: float | None = None
    for sp in swings:
        if sp.swing_type == "HIGH":
            if last_high is None or sp.price > last_high:
                sp.label = "HH"
            else:
                sp.label = "LH"
            last_high = sp.price
        else:
            if last_low is None or sp.price > last_low:
                sp.label = "HL"
            else:
                sp.label = "LL"
            last_low = sp.price
    return swings


def swings_for_timeframe(
    candles: Sequence[Mapping[str, Any]],
    config: SignalConfig,
    timeframe: str,
    *,
    symbol: str = "",
    as_of_index: int | None = None,
) -> list[SwingRecord]:
    sc: TimeframeSwingConfig = config.swing_for(timeframe)
    return detect_swings(
        candles,
        left=sc.swing_left_bars,
        right=sc.swing_right_bars,
        symbol=symbol,
        timeframe=timeframe,
        atr_period=config.atr_period,
        minimum_swing_distance_atr=sc.minimum_swing_distance_atr,
        as_of_index=as_of_index,
    )
