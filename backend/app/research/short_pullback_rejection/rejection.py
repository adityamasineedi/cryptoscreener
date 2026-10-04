"""Deterministic bearish rejection pattern detectors (no-lookahead)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.short_pullback_rejection.constants import (
    DEFAULT_THRESHOLDS,
    REJECTION_BEARISH_ENGULFING,
    REJECTION_BEARISH_MICRO_BOS,
    REJECTION_LOWER_HIGH_FAILURE,
    REJECTION_UPPER_WICK,
)
from app.research.short_pullback_rejection.regime import candle_ohlc, candle_time


def _rejection_swing_high(
    candles: Sequence[Mapping[str, Any]],
    *,
    retest_index: int,
    rejection_index: int,
) -> float:
    highs = [
        candle_ohlc(candles[i])[1]
        for i in range(retest_index, min(rejection_index, len(candles) - 1) + 1)
    ]
    return max(highs) if highs else candle_ohlc(candles[rejection_index])[1]


def detect_bearish_engulfing(
    candles: Sequence[Mapping[str, Any]],
    index: int,
) -> dict[str, Any] | None:
    if index < 1 or index >= len(candles):
        return None
    o0, h0, l0, c0 = candle_ohlc(candles[index - 1])
    o1, h1, l1, c1 = candle_ohlc(candles[index])
    prev_bull = c0 > o0
    curr_bear = c1 < o1
    engulfs = (o1 >= c0) and (c1 <= o0) and (abs(c1 - o1) > abs(c0 - o0))
    if prev_bull and curr_bear and engulfs:
        return {
            "rejection_type": REJECTION_BEARISH_ENGULFING,
            "rejection_index": index,
            "passed": True,
        }
    return None


def detect_upper_wick_rejection(
    candles: Sequence[Mapping[str, Any]],
    index: int,
    *,
    thresholds: Mapping[str, float | int] | None = None,
) -> dict[str, Any] | None:
    th = {**DEFAULT_THRESHOLDS, **dict(thresholds or {})}
    if index < 0 or index >= len(candles):
        return None
    o, h, l, c = candle_ohlc(candles[index])
    rng = h - l
    if rng <= 0:
        return None
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    if body <= 0:
        body = rng * 1e-9
    if (
        upper >= float(th["upper_wick_body_ratio"]) * body
        and upper / rng >= float(th["upper_wick_range_frac"])
        and c < o  # bearish or doji-bear close preference
        and upper > lower
    ):
        return {
            "rejection_type": REJECTION_UPPER_WICK,
            "rejection_index": index,
            "passed": True,
        }
    return None


def detect_lower_high_failure(
    candles: Sequence[Mapping[str, Any]],
    *,
    retest_index: int,
    index: int,
) -> dict[str, Any] | None:
    """Retest makes a local high; later bar fails to exceed it and closes bearish."""
    if index <= retest_index or index >= len(candles):
        return None
    retest_high = candle_ohlc(candles[retest_index])[1]
    # Find a subsequent lower high attempt then bearish close.
    o, h, l, c = candle_ohlc(candles[index])
    if h >= retest_high:
        return None
    if c >= o:
        return None
    # Prior bar should have been the failed probe (high < retest_high, preferably up).
    if index - 1 > retest_index:
        po, ph, pl, pc = candle_ohlc(candles[index - 1])
        if ph >= retest_high:
            return None
    return {
        "rejection_type": REJECTION_LOWER_HIGH_FAILURE,
        "rejection_index": index,
        "passed": True,
        "failed_high": h,
        "reference_high": retest_high,
    }


def detect_bearish_micro_bos(
    candles: Sequence[Mapping[str, Any]],
    *,
    retest_index: int,
    index: int,
    thresholds: Mapping[str, float | int] | None = None,
) -> dict[str, Any] | None:
    """Close breaks the most recent micro swing low formed after the retest."""
    th = {**DEFAULT_THRESHOLDS, **dict(thresholds or {})}
    lookback = int(th["micro_bos_lookback"])
    if index <= retest_index or index >= len(candles):
        return None
    start = max(retest_index + 1, index - lookback)
    if start >= index:
        return None
    micro_low = None
    micro_low_i = None
    for i in range(start, index):
        low = candle_ohlc(candles[i])[2]
        if micro_low is None or low < micro_low:
            micro_low = low
            micro_low_i = i
    if micro_low is None or micro_low_i is None:
        return None
    _, _, _, c = candle_ohlc(candles[index])
    if c < micro_low:
        return {
            "rejection_type": REJECTION_BEARISH_MICRO_BOS,
            "rejection_index": index,
            "passed": True,
            "micro_swing_low": micro_low,
            "micro_swing_low_index": micro_low_i,
        }
    return None


def detect_rejection(
    candles: Sequence[Mapping[str, Any]],
    *,
    retest_index: int,
    zone_high: float,
    zone_low: float,
    from_index: int | None = None,
    to_index: int | None = None,
    thresholds: Mapping[str, float | int] | None = None,
) -> dict[str, Any] | None:
    """Scan for the first explicit rejection after retest. Touch alone is not enough."""
    th = {**DEFAULT_THRESHOLDS, **dict(thresholds or {})}
    start = (from_index if from_index is not None else retest_index)
    end = to_index if to_index is not None else min(
        len(candles) - 1, retest_index + int(th["rejection_expiry_bars"])
    )
    for i in range(max(start, retest_index), min(end, len(candles) - 1) + 1):
        # Require the bar to interact with the zone or follow a zone touch.
        _, h, l, c = candle_ohlc(candles[i])
        near_zone = h >= zone_low and l <= zone_high * 1.01
        after_retest = i > retest_index
        if not (near_zone or after_retest):
            continue

        detectors = [
            detect_bearish_engulfing(candles, i),
            detect_upper_wick_rejection(candles, i, thresholds=th),
            detect_lower_high_failure(candles, retest_index=retest_index, index=i)
            if after_retest
            else None,
            detect_bearish_micro_bos(
                candles, retest_index=retest_index, index=i, thresholds=th
            )
            if after_retest
            else None,
        ]
        for hit in detectors:
            if not hit:
                continue
            # Engulfing / wick may fire on the retest bar itself.
            if hit["rejection_type"] in (
                REJECTION_LOWER_HIGH_FAILURE,
                REJECTION_BEARISH_MICRO_BOS,
            ) and i <= retest_index:
                continue
            ts = candle_time(candles[i])
            swing_high = _rejection_swing_high(
                candles, retest_index=retest_index, rejection_index=i
            )
            return {
                **hit,
                "rejection_time": ts.isoformat() if ts else None,
                "rejection_swing_high": swing_high,
                "rejection_close": c,
                "rejection_open": candle_ohlc(candles[i])[0],
                "rejection_high": h,
                "rejection_low": l,
            }
    return None
