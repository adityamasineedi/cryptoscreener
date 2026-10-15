"""Candle-by-candle structure labels. Research only.

Bullish and bearish require a confirmed pair and an intact designated swing.
A broken pair becomes transition until the opposite pair is confirmed.
Nearly equal boundaries can be a range. Anything else is transition.
Too few confirmed swings is insufficient_data. None of these states is a trade.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from app.research.bos_strategy_comparison.htf import timeframe_seconds
from app.signals._candle_utils import candle_time, ohlc

# Three days of this timeframe's bars. Not a calendar span across a gap.
MAX_AGE_BARS = {"15m": 288, "1h": 72, "4h": 18}
EQUAL_ATR_FRACTION = 0.25
BASELINE_RULE_VERSION = "structure-baseline-v1"
CANDIDATE_RULE_VERSION = "structure-candidate-distinct-pivots-v1"
# The new reversal high and the new reversal low may print in either order.
# Both must be after the break. Their comparison swings may be older.
# The candidate refuses a reversal when those two pivots share one timestamp.
# It does not infer which extreme printed first inside that candle.
CANDIDATE_PIVOT_ORDER = (
    "Either new pivot may confirm first. "
    "Same-candle high and low stay unresolved transition."
)


def audit_candles(
    candles: Sequence[Mapping[str, Any]],
    timeframe: str,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Duplicates, gaps, order, and whether the last bar has closed."""
    step = timeframe_seconds(timeframe)
    times = [candle_time(candle) for candle in candles]
    duplicates = 0
    non_monotonic = 0
    missing_time = sum(1 for stamp in times if stamp is None)
    gaps: list[dict[str, Any]] = []
    for index in range(1, len(times)):
        previous, current = times[index - 1], times[index]
        if previous is None or current is None:
            continue
        delta = (current - previous).total_seconds()
        if delta == 0:
            duplicates += 1
        elif delta < 0:
            non_monotonic += 1
        elif delta != step:
            gaps.append(
                {
                    "after": previous.isoformat(),
                    "next": current.isoformat(),
                    "missing_bars": int(delta // step) - 1,
                }
            )
    unfinished = False
    if times and times[-1] is not None and as_of is not None:
        unfinished = times[-1] + timedelta(seconds=step) > as_of
    present = [stamp for stamp in times if stamp is not None]
    return {
        "timeframe": timeframe,
        "bars": len(candles),
        "start": present[0].isoformat() if present else None,
        "end": present[-1].isoformat() if present else None,
        "duplicates": duplicates,
        "non_monotonic": non_monotonic,
        "missing_time": missing_time,
        "gap_count": len(gaps),
        "missing_bars": sum(int(gap["missing_bars"]) for gap in gaps),
        "unfinished_last_bar": unfinished,
        "gaps": gaps[:8],
    }


def _true_range(candles: Sequence[Mapping[str, Any]], index: int) -> float:
    _, high, low, _ = ohlc(candles, index)
    previous_close = ohlc(candles, index - 1)[3]
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def _atr(candles: Sequence[Mapping[str, Any]], index: int, period: int) -> float | None:
    if index < period:
        return None
    total = 0.0
    for cursor in range(index - period + 1, index + 1):
        total += _true_range(candles, cursor)
    return total / float(period)


def _atr_series(candles: Sequence[Mapping[str, Any]], period: int) -> list[float | None]:
    """Same values as calling ``_atr`` on every bar, computed once."""
    atrs: list[float | None] = [None] * len(candles)
    if period < 1:
        return atrs
    window: list[float] = []
    total = 0.0
    for index in range(1, len(candles)):
        true_range = _true_range(candles, index)
        window.append(true_range)
        total += true_range
        if len(window) > period:
            total -= window.pop(0)
        if index >= period and len(window) == period:
            atrs[index] = total / float(period)
    return atrs


def _swing_clears_floor(
    candles: Sequence[Mapping[str, Any]],
    swing: Any,
    index: int,
    atr: float,
    min_swing_atr: float,
) -> bool:
    pivot = int(swing.bar_index)
    if pivot <= 0 or pivot >= len(candles):
        return False
    _, high, low, _ = ohlc(candles, pivot)
    neighbor_high = max(ohlc(candles, pivot - 1)[1], ohlc(candles, min(pivot + 1, index))[1])
    neighbor_low = min(ohlc(candles, pivot - 1)[2], ohlc(candles, min(pivot + 1, index))[2])
    excursion = (high - neighbor_high) if swing.swing_type == "HIGH" else (neighbor_low - low)
    return excursion >= atr * min_swing_atr


def _ref(swing: Any, right_bars: int, step: int | None = None) -> dict[str, Any]:
    confirmed_index = int(swing.bar_index) + right_bars
    pivot = getattr(swing, "timestamp", None)
    confirmed = getattr(swing, "confirmed_at", None)
    timeframe = str(getattr(swing, "timeframe", "") or "")
    return {
        "swing_id": f"{timeframe}:{swing.swing_type}:{int(swing.bar_index)}",
        "pivot_time": pivot.isoformat() if hasattr(pivot, "isoformat") else None,
        "confirmation_open_time": confirmed.isoformat() if hasattr(confirmed, "isoformat") else None,
        "confirmed_at": confirmed.isoformat() if hasattr(confirmed, "isoformat") else None,
        "known_at": _known_at(confirmed, step) if step is not None else None,
        "price": float(swing.price),
        "type": str(swing.swing_type),
        "timeframe": timeframe,
        "pivot_index": int(swing.bar_index),
        "confirmed_index": confirmed_index,
    }


def _contiguous_swing(
    candles: Sequence[Mapping[str, Any]],
    pivot: int,
    right_bars: int,
    step: int,
) -> bool:
    """A pivot is confirmed only when the right-hand bars are consecutive."""
    end = pivot + right_bars
    if pivot < 0 or end >= len(candles):
        return False
    opened = candle_time(candles[pivot])
    confirmed = candle_time(candles[end])
    if opened is None or confirmed is None:
        return False
    return (confirmed - opened).total_seconds() == right_bars * step


def _known_at(opened: datetime | None, step: int) -> str | None:
    """Close time of a candle. The stored open is not when the close is known."""
    if opened is None:
        return None
    return (opened + timedelta(seconds=step)).isoformat()


def _leg_status(swing: Any, break_index: int) -> str:
    if break_index < 0:
        return "absent"
    if swing is None or int(swing.bar_index) <= break_index:
        return "pending"
    return "confirmed"


def _quality_at(
    candles: Sequence[Mapping[str, Any]],
    index: int,
    step: int,
) -> str:
    if candle_time(candles[index]) is None:
        return "missing_timestamp"
    if index == 0:
        return "ok"
    previous = candle_time(candles[index - 1])
    current = candle_time(candles[index])
    if previous is None or current is None:
        return "missing_timestamp"
    delta = (current - previous).total_seconds()
    if delta == 0:
        return "duplicate_timestamp"
    if delta < 0:
        return "out_of_order"
    if delta != step:
        return "gap_before"
    return "ok"


def label_structure_bars(
    candles: Sequence[Mapping[str, Any]],
    swings: Sequence[Any],
    *,
    right_bars: int,
    timeframe: str,
    equal_atr_fraction: float = EQUAL_ATR_FRACTION,
    max_age_bars: int | None = None,
    atr_period: int = 14,
    min_swing_atr: float = 0.0,
    distinct_reversal_pivots: bool = False,
) -> list[dict[str, Any]]:
    """Label each bar using only swings confirmed by that bar.

    ``min_swing_atr`` above 0 drops a fractal whose excursion is smaller than
    that multiple of ATR. The live detector default is 0. This argument is a
    research comparison, not a change to live swings.
    """
    step = timeframe_seconds(timeframe)
    age_limit = MAX_AGE_BARS.get(timeframe, 288) if max_age_bars is None else max_age_bars
    ordered = sorted(swings, key=lambda swing: int(swing.bar_index))
    state = "insufficient_data"
    state_since = 0
    pending = ""
    expiry_index = -1
    gap_index = -1
    support: Any = None
    resist: Any = None
    last_event: str | None = None
    break_index = -1
    break_origin = ""
    break_direction = ""
    expired_swing: Any = None
    labels: list[dict[str, Any]] = []
    rule_version = CANDIDATE_RULE_VERSION if distinct_reversal_pivots else BASELINE_RULE_VERSION
    # Swings are ordered by pivot. A pivot's confirmation index never moves,
    # so each swing joins the high or low list once. A gap drops pivots from
    # before the hole. An ATR floor still depends on the current bar.
    swing_cursor = 0
    seen_highs: list[Any] = []
    seen_lows: list[Any] = []
    atrs = _atr_series(candles, atr_period)
    ref_cache: dict[int, dict[str, Any]] = {}

    def swing_ref(swing: Any) -> dict[str, Any]:
        key = id(swing)
        cached = ref_cache.get(key)
        if cached is None:
            cached = _ref(swing, right_bars, step)
            ref_cache[key] = cached
        return cached

    for index in range(len(candles)):
        atr = atrs[index]
        quality = _quality_at(candles, index, step)
        if quality == "gap_before":
            # The missing candle ends the previous sequence. Swings that
            # touch the hole are not confirmed, and the state starts over.
            gap_index = index
            state = "insufficient_data"
            state_since = index
            pending = ""
            expiry_index = index
            support = None
            resist = None
            last_event = None
            break_index = -1
            break_origin = ""
            break_direction = ""
            expired_swing = None
            seen_highs = [swing for swing in seen_highs if int(swing.bar_index) >= gap_index]
            seen_lows = [swing for swing in seen_lows if int(swing.bar_index) >= gap_index]
        while swing_cursor < len(ordered):
            swing = ordered[swing_cursor]
            pivot = int(swing.bar_index)
            if pivot + right_bars > index:
                break
            swing_cursor += 1
            if pivot < gap_index:
                continue
            if not _contiguous_swing(candles, pivot, right_bars, step):
                continue
            if swing.swing_type == "HIGH":
                seen_highs.append(swing)
            else:
                seen_lows.append(swing)
        if min_swing_atr > 0 and atr:
            highs = [swing for swing in seen_highs if _swing_clears_floor(candles, swing, index, atr, min_swing_atr)]
            lows = [swing for swing in seen_lows if _swing_clears_floor(candles, swing, index, atr, min_swing_atr)]
        else:
            highs = seen_highs
            lows = seen_lows
        close = ohlc(candles, index)[3]
        event = last_event
        reason = "too_few_confirmed_swings"
        next_state = "insufficient_data"

        if len(highs) >= 2 and len(lows) >= 2:
            bull_pair = highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price
            bear_pair = highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price
            tolerance = (atr * equal_atr_fraction) if atr is not None else None
            equal_bounds = False
            if tolerance is not None:
                equal_bounds = (
                    abs(highs[-1].price - highs[-2].price) <= tolerance
                    and abs(lows[-1].price - lows[-2].price) <= tolerance
                )
            low_bound = min(lows[-1].price, lows[-2].price)
            high_bound = max(highs[-1].price, highs[-2].price)
            inside = low_bound <= close <= high_bound
            support_age = None if support is None else index - (int(support.bar_index) + right_bars)
            resist_age = None if resist is None else index - (int(resist.bar_index) + right_bars)
            broke_support = state == "bullish" and support is not None and close < float(support.price)
            broke_resist = state == "bearish" and resist is not None and close > float(resist.price)
            support_stale = (
                state == "bullish"
                and support is not None
                and support_age is not None
                and support_age > age_limit
            )
            resist_stale = (
                state == "bearish"
                and resist is not None
                and resist_age is not None
                and resist_age > age_limit
            )
            fresh_low = lows[-1].bar_index + right_bars > expiry_index
            fresh_high = highs[-1].bar_index + right_bars > expiry_index
            # Both new pivots must print after the break. Confirmation can
            # lag, but a pivot that already existed before the break is not a
            # new pair. The swing each one is compared with may be older.
            reversal_bull = (
                pending == "bearish_break"
                and bull_pair
                and int(highs[-1].bar_index) > expiry_index
                and int(lows[-1].bar_index) > expiry_index
            )
            reversal_bear = (
                pending == "bullish_break"
                and bear_pair
                and int(highs[-1].bar_index) > expiry_index
                and int(lows[-1].bar_index) > expiry_index
            )
            same_candle_pair = int(highs[-1].bar_index) == int(lows[-1].bar_index)
            if distinct_reversal_pivots and same_candle_pair:
                reversal_bull = False
                reversal_bear = False

            if broke_support or broke_resist:
                event = "choch"
                pending = "bullish_break" if broke_support else "bearish_break"
                expiry_index = index
                break_index = index
                break_origin = state
                break_direction = "downward" if broke_support else "upward"
                next_state = "transition"
                reason = "support_broken" if broke_support else "resistance_broken"
            elif support_stale or resist_stale:
                expired_swing = support if support_stale else resist
                pending = "expired"
                expiry_index = index
                next_state = "transition"
                reason = "support_expired" if support_stale else "resistance_expired"
            elif reversal_bull or reversal_bear:
                event = "reversal_confirmed"
                pending = ""
                if reversal_bull:
                    support = lows[-1]
                    next_state = "bullish"
                    reason = "higher_high_and_higher_low_support_intact"
                else:
                    resist = highs[-1]
                    next_state = "bearish"
                    reason = "lower_high_and_lower_low_resistance_intact"
            elif pending in {"bullish_break", "bearish_break"}:
                next_state = "transition"
                reason = "support_broken" if pending == "bullish_break" else "resistance_broken"
            elif bull_pair and fresh_low:
                previous_close = ohlc(candles, index - 1)[3] if index else close
                if state == "bullish" and previous_close <= float(highs[-1].price) < close:
                    event = "bos"
                support = lows[-1]
                next_state = "bullish"
                pending = ""
                reason = "higher_high_and_higher_low_support_intact"
            elif bear_pair and fresh_high:
                previous_close = ohlc(candles, index - 1)[3] if index else close
                if state == "bearish" and previous_close >= float(lows[-1].price) > close:
                    event = "bos"
                resist = highs[-1]
                next_state = "bearish"
                pending = ""
                reason = "lower_high_and_lower_low_resistance_intact"
            elif equal_bounds and inside and not pending:
                next_state = "range"
                reason = "repeated_boundaries"
            else:
                next_state = "transition"
                reason = "mixed_structure" if not pending else (
                    "support_broken" if pending == "bullish_break"
                    else "resistance_broken" if pending == "bearish_break"
                    else "support_expired" if state != "bearish" else "resistance_expired"
                )
        if quality == "gap_before" and next_state == "insufficient_data":
            reason = "gap_in_history"
        if next_state != state:
            state = next_state
            state_since = index
        last_event = event
        pair = None
        if len(highs) >= 2 and len(lows) >= 2:
            pair = {
                "previous_high": swing_ref(highs[-2]),
                "latest_high": swing_ref(highs[-1]),
                "previous_low": swing_ref(lows[-2]),
                "latest_low": swing_ref(lows[-1]),
            }
        opened = candle_time(candles[index])
        same_candle = bool(
            pair
            and pair["latest_high"]["pivot_index"] == pair["latest_low"]["pivot_index"]
        )
        flags = ["same_candle_reversal_pivots"] if same_candle else []
        if distinct_reversal_pivots and same_candle and pending in {"bullish_break", "bearish_break"}:
            flags.append("candidate_rejected_same_candle_reversal")
        if pending == "bearish_break":
            high_leg = _leg_status(highs[-1] if len(highs) >= 1 else None, break_index)
            low_leg = _leg_status(lows[-1] if len(lows) >= 1 else None, break_index)
            reversal_evidence = {
                "pattern": "higher_high_and_higher_low",
                "higher_high": high_leg,
                "higher_low": low_leg,
            }
        elif pending == "bullish_break":
            reversal_evidence = {
                "pattern": "lower_high_and_lower_low",
                "lower_high": _leg_status(highs[-1] if len(highs) >= 1 else None, break_index),
                "lower_low": _leg_status(lows[-1] if len(lows) >= 1 else None, break_index),
            }
        else:
            reversal_evidence = None
        designated = (
            support
            if state == "bullish" or pending == "bullish_break"
            else resist
            if state == "bearish" or pending == "bearish_break"
            else expired_swing
            if pending == "expired"
            else None
        )
        designated_age = None if designated is None else index - (int(designated.bar_index) + right_bars)
        if pending == "expired":
            expiry_status = "expired"
        elif designated is None:
            expiry_status = "none"
        elif designated_age is not None and designated_age > age_limit:
            expiry_status = "expired"
        else:
            expiry_status = "within_limit"
        bars_since_break = None if break_index < 0 else index - break_index
        sentence = None
        if state == "transition" and pending == "bearish_break" and reversal_evidence:
            sentence = (
                f"Transition from bearish · upward CHoCH · higher low {reversal_evidence['higher_low']}"
                f" · higher high {reversal_evidence['higher_high']} · {bars_since_break} bars since break."
            )
        elif state == "transition" and pending == "bullish_break" and reversal_evidence:
            sentence = (
                f"Transition from bullish · downward CHoCH · lower high {reversal_evidence['lower_high']}"
                f" · lower low {reversal_evidence['lower_low']} · {bars_since_break} bars since break."
            )
        labels.append(
            {
                "rule_version": rule_version,
                "state": state,
                "reason": reason,
                "explanation": sentence,
                "known_at": _known_at(opened, step),
                "candle_open_time": opened.isoformat() if opened else None,
                "state_start_open_time": candle_time(candles[state_since]).isoformat() if candle_time(candles[state_since]) else None,
                "state_age": index - state_since,
                "transition_origin": break_origin or None,
                "break_direction": break_direction or None,
                "break_open_time": candle_time(candles[break_index]).isoformat() if break_index >= 0 else None,
                "break_known_at": _known_at(candle_time(candles[break_index]), step) if break_index >= 0 else None,
                "bars_since_break": bars_since_break,
                "reversal_evidence": reversal_evidence,
                "supporting_swing": None if support is None else swing_ref(support),
                "resisting_swing": None if resist is None else swing_ref(resist),
                "pair": pair,
                "designated_level": None if designated is None else swing_ref(designated),
                "designated_age_bars": designated_age,
                "expiry_status": expiry_status,
                "expiry_limit_bars": age_limit,
                "ambiguity_flags": flags,
                "last_event": last_event,
                "data_quality": quality,
                "initialization": "gap_reset" if quality == "gap_before" else "warming_up" if state == "insufficient_data" else "ready",
            }
        )
    return labels
