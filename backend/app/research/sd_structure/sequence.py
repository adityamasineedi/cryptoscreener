"""Ordered structure checks. Each event uses only bars through its own index."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.signals._candle_utils import candle_time, ohlc, volume_at
from app.signals.choch_engine import detect_choch
from app.signals.schemas import SwingRecord
from app.signals.trend_engine import infer_trend


def slot_rvol(
    candles: Sequence[Mapping[str, Any]],
    index: int,
    *,
    samples: int = 20,
    min_samples: int = 8,
) -> float | None:
    """Volume versus the same UTC clock slot, not the previous 20 bars.

    A 15m bar is compared with earlier 15m bars that open at the same hour
    and minute. Quiet sessions are not scored against the cash-open spike.
    """
    if index < 1 or index >= len(candles):
        return None
    stamp = candle_time(candles[index])
    if stamp is None:
        return None
    slot = (stamp.hour, stamp.minute)
    baseline: list[float] = []
    for earlier_index in range(index - 1, -1, -1):
        earlier = candle_time(candles[earlier_index])
        if earlier is None:
            return None
        if (earlier.hour, earlier.minute) != slot:
            continue
        baseline.append(volume_at(candles, earlier_index))
        if len(baseline) >= samples:
            break
    if len(baseline) < min_samples:
        return None
    avg = sum(baseline) / float(len(baseline))
    if avg <= 0:
        return None
    return volume_at(candles, index) / avg


def preceding_rvol(
    candles: Sequence[Mapping[str, Any]],
    index: int,
    *,
    window: int = 20,
) -> float | None:
    """Current volume divided by the mean of the previous ``window`` bars."""
    if index < window:
        return None
    base = [volume_at(candles, j) for j in range(index - window, index)]
    avg = sum(base) / float(window)
    if avg <= 0:
        return None
    return volume_at(candles, index) / avg


def range_expansion(
    candles: Sequence[Mapping[str, Any]],
    index: int,
    *,
    window: int = 20,
) -> bool:
    if index < window:
        return False
    ranges = []
    for j in range(index - window, index):
        _, h, l, _ = ohlc(candles, j)
        ranges.append(h - l)
    avg = sum(ranges) / float(window)
    _, h, l, _ = ohlc(candles, index)
    return avg > 0 and (h - l) > avg


def volume_divergence(
    swings: Sequence[SwingRecord],
    candles: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    as_of_index: int,
) -> bool:
    """Later swing extreme at or beyond the prior one, on lower volume.

    Bullish: second swing low is not higher, and its volume is lower.
    Bearish: second swing high is not lower, and its volume is lower.
    Standard OHLCV volume is participation, not buy/sell delta.
    """
    kind = "LOW" if direction == "LONG" else "HIGH"
    points = [
        swing
        for swing in swings
        if swing.swing_type == kind and swing.bar_index < as_of_index
    ]
    if len(points) < 2:
        return False
    earlier, later = points[-2], points[-1]
    if later.bar_index >= len(candles) or earlier.bar_index >= len(candles):
        return False
    if direction == "LONG" and later.price > earlier.price:
        return False
    if direction == "SHORT" and later.price < earlier.price:
        return False
    return volume_at(candles, later.bar_index) < volume_at(candles, earlier.bar_index)


def _first_close_through(candles: Sequence[Mapping[str, Any]], index: int, level: float, *, bullish: bool) -> bool:
    if index < 1:
        return False
    prev = ohlc(candles, index - 1)[3]
    close = ohlc(candles, index)[3]
    if bullish:
        return prev <= level < close
    return prev >= level > close


def choch_at(
    candles: Sequence[Mapping[str, Any]],
    swings: Sequence[SwingRecord],
    index: int,
    *,
    symbol: str,
    timeframe: str,
    right_bars: int,
) -> dict[str, Any] | None:
    visible = _swings_confirmed_by(swings, index, right_bars)
    trend = infer_trend(visible)
    event = detect_choch(
        candles,
        visible,
        trend,
        symbol=symbol,
        timeframe=timeframe,
        as_of_index=index,
    )
    if not event or event.get("state") != "CONFIRMED" or event.get("level") is None:
        return None
    direction = str(event.get("direction") or "")
    bullish = direction == "CHOCH_BULLISH"
    bearish = direction == "CHOCH_BEARISH"
    if not bullish and not bearish:
        return None
    if not _first_close_through(candles, index, float(event["level"]), bullish=bullish):
        return None
    return event


def _swings_confirmed_by(
    swings: Sequence[SwingRecord],
    index: int,
    right_bars: int,
) -> list[SwingRecord]:
    return [swing for swing in swings if swing.bar_index + right_bars <= index]


def is_inside_bar(candles: Sequence[Mapping[str, Any]], index: int) -> bool:
    """Bar whose high and low sit inside the previous bar, including equals."""
    if index < 1 or index >= len(candles):
        return False
    _, prev_high, prev_low, _ = ohlc(candles, index - 1)
    _, high, low, _ = ohlc(candles, index)
    return high <= prev_high and low >= prev_low


def drop_inside_bar_swings(
    swings: Sequence[SwingRecord],
    candles: Sequence[Mapping[str, Any]],
) -> list[SwingRecord]:
    """A pivot printed on an inside bar is not a distinct V swing."""
    kept: list[SwingRecord] = []
    for swing in swings:
        if 0 <= swing.bar_index < len(candles) and is_inside_bar(candles, swing.bar_index):
            continue
        kept.append(swing)
    return kept


def _dealing_range(
    swings: Sequence[SwingRecord],
) -> tuple[float, float] | None:
    highs = [swing for swing in swings if swing.swing_type == "HIGH"]
    lows = [swing for swing in swings if swing.swing_type == "LOW"]
    if len(highs) < 2 or len(lows) < 2:
        return None
    previous_high, latest_high = highs[-2], highs[-1]
    previous_low, latest_low = lows[-2], lows[-1]
    bullish = latest_high.price > previous_high.price and latest_low.price > previous_low.price
    bearish = latest_high.price < previous_high.price and latest_low.price < previous_low.price
    if not bullish and not bearish:
        return None
    return latest_low.price, latest_high.price


def external_swings(swings: Sequence[SwingRecord]) -> list[SwingRecord]:
    """Ignore swings strictly inside an established HH-HL or LH-LL range.

    A later swing at or beyond either boundary is kept, and the range is
    rebuilt from the swings that remain.
    """
    kept: list[SwingRecord] = []
    active: tuple[float, float] | None = None
    for swing in sorted(swings, key=lambda item: item.bar_index):
        if active is not None and active[0] < swing.price < active[1]:
            continue
        kept.append(swing)
        if active is not None:
            active = None
        active = _dealing_range(kept)
    return kept


def structure_swings(
    swings: Sequence[SwingRecord],
    candles: Sequence[Mapping[str, Any]],
) -> list[SwingRecord]:
    return external_swings(drop_inside_bar_swings(swings, candles))


def classify_day_structure(
    swings: Sequence[SwingRecord],
    candles: Sequence[Mapping[str, Any]],
    index: int,
    *,
    right_bars: int,
) -> str:
    """Label one closed bar. Uses only swings confirmed by that bar.

    ``upper_trend`` is the bull structure (higher high and higher low).
    ``short_trend`` is the bear structure (lower high and lower low).
    ``sideways`` is neither pair. ``reversal`` is a close through the
    protective swing while the prior pair is still the confirmed trend.
    """
    confirmed = [
        swing
        for swing in structure_swings(swings, candles)
        if swing.bar_index + right_bars <= index
    ]
    trend = str(infer_trend(confirmed).get("trend") or "")
    close = ohlc(candles, index)[3]
    if trend == "BULLISH":
        lows = [swing for swing in confirmed if swing.swing_type == "LOW"]
        if lows and close < lows[-1].price:
            return "reversal"
        return "upper_trend"
    if trend == "BEARISH":
        highs = [swing for swing in confirmed if swing.swing_type == "HIGH"]
        if highs and close > highs[-1].price:
            return "reversal"
        return "short_trend"
    return "sideways"


def director_allows(direction: str, trend: str | None) -> bool:
    """Block a trade only when the higher timeframe has a clear opposite trend.

    Neutral is not a clear trend. Missing or unfinished labels fail closed.
    """
    if trend not in {"BULLISH", "BEARISH", "NEUTRAL"}:
        return False
    if trend == "NEUTRAL":
        return True
    if direction == "LONG":
        return trend != "BEARISH"
    if direction == "SHORT":
        return trend != "BULLISH"
    return False


def bos_after_choch(
    candles: Sequence[Mapping[str, Any]],
    swings: Sequence[SwingRecord],
    *,
    choch_index: int,
    choch_level: float,
    direction: str,
    right_bars: int,
    bos_index: int,
) -> dict[str, float | int] | None:
    """BOS is the first close through the next swing after a higher low / lower high.

    The swing being broken must already be confirmed, and the protective swing
    (higher low or lower high) must be confirmed before the break.
    """
    visible = _swings_confirmed_by(swings, bos_index, right_bars)
    if direction == "LONG":
        prior_lows = [
            swing
            for swing in visible
            if swing.swing_type == "LOW" and swing.bar_index < choch_index
        ]
        if not prior_lows:
            return None
        origin = prior_lows[-1]
        higher_lows = [
            swing
            for swing in visible
            if swing.swing_type == "LOW"
            and swing.bar_index > origin.bar_index
            and swing.price > origin.price
            and swing.bar_index < bos_index
        ]
        if not higher_lows:
            return None
        highs = [
            swing
            for swing in visible
            if swing.swing_type == "HIGH"
            and swing.bar_index > choch_index
            and swing.price > choch_level
            and swing.bar_index < bos_index
        ]
        if not highs:
            return None
        level = highs[-1].price
        if not _first_close_through(candles, bos_index, level, bullish=True):
            return None
        return {
            "level": level,
            "invalidation": higher_lows[-1].price,
            "bos_index": bos_index,
        }

    prior_highs = [
        swing
        for swing in visible
        if swing.swing_type == "HIGH" and swing.bar_index < choch_index
    ]
    if not prior_highs:
        return None
    origin = prior_highs[-1]
    lower_highs = [
        swing
        for swing in visible
        if swing.swing_type == "HIGH"
        and swing.bar_index > origin.bar_index
        and swing.price < origin.price
        and swing.bar_index < bos_index
    ]
    if not lower_highs:
        return None
    lows = [
        swing
        for swing in visible
        if swing.swing_type == "LOW"
        and swing.bar_index > choch_index
        and swing.price < choch_level
        and swing.bar_index < bos_index
    ]
    if not lows:
        return None
    level = lows[-1].price
    if not _first_close_through(candles, bos_index, level, bullish=False):
        return None
    return {
        "level": level,
        "invalidation": lower_highs[-1].price,
        "bos_index": bos_index,
    }


def retest_holds(
    candles: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    level: float,
    index: int,
) -> bool:
    """Retest bar trades the broken level and closes back with the break."""
    _, high, low, close = ohlc(candles, index)
    if direction == "LONG":
        return low <= level <= high and close > level
    return low <= level <= high and close < level


def plain_bos(
    candles: Sequence[Mapping[str, Any]],
    swings: Sequence[SwingRecord],
    *,
    direction: str,
    right_bars: int,
    bos_index: int,
    beyond: float,
) -> dict[str, float | int] | None:
    """Close through the latest confirmed swing that is beyond the zone.

    Used when the variant does not require a prior CHoCH. ``beyond`` is the
    zone edge the swing must clear (zone high for demand, zone low for supply).
    """
    visible = _swings_confirmed_by(swings, bos_index, right_bars)
    if direction == "LONG":
        highs = [
            swing
            for swing in visible
            if swing.swing_type == "HIGH" and swing.bar_index < bos_index and swing.price > beyond
        ]
        lows = [swing for swing in visible if swing.swing_type == "LOW" and swing.bar_index < bos_index]
        if not highs or not lows:
            return None
        level = highs[-1].price
        if not _first_close_through(candles, bos_index, level, bullish=True):
            return None
        return {"level": level, "invalidation": lows[-1].price, "bos_index": bos_index}
    lows = [
        swing
        for swing in visible
        if swing.swing_type == "LOW" and swing.bar_index < bos_index and swing.price < beyond
    ]
    highs = [swing for swing in visible if swing.swing_type == "HIGH" and swing.bar_index < bos_index]
    if not lows or not highs:
        return None
    level = lows[-1].price
    if not _first_close_through(candles, bos_index, level, bullish=False):
        return None
    return {"level": level, "invalidation": highs[-1].price, "bos_index": bos_index}


def structural_invalidation_without_bos(
    swings: Sequence[SwingRecord],
    *,
    direction: str,
    choch_index: int,
) -> float | None:
    """Stop anchor for a CHoCH-only entry: the extreme CHoCH just reversed."""
    if direction == "LONG":
        lows = [
            swing.price
            for swing in swings
            if swing.swing_type == "LOW" and swing.bar_index < choch_index
        ]
        return lows[-1] if lows else None
    highs = [
        swing.price
        for swing in swings
        if swing.swing_type == "HIGH" and swing.bar_index < choch_index
    ]
    return highs[-1] if highs else None
