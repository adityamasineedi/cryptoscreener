"""Point-in-time supply and demand zones.

A zone exists only after its departure candle has closed. ATR for that
departure uses candles through the departure bar, not the end of the sample.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr_series as calc_atr_series
from app.engines.mtf.indicators import candle_body_ratio
from app.signals._candle_utils import ohlc


@dataclass(frozen=True)
class CausalZone:
    zone_type: str  # demand | supply
    low: float
    high: float
    impulse_index: int
    departure_index: int
    touch_count: int
    broken: bool
    age_bars: int

    @property
    def fresh(self) -> bool:
        return self.touch_count <= 1 and not self.broken

    def band(self) -> tuple[float, float]:
        return (self.low, self.high)


def _overlaps(high: float, low: float, zone_low: float, zone_high: float) -> bool:
    return low <= zone_high and high >= zone_low


def causal_zones(
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    *,
    atr_period: int = 14,
    impulse_body_ratio: float = 0.6,
    base_max_candles: int = 8,
    min_departure_atr_mult: float = 1.2,
    scan_bars: int = 240,
) -> list[CausalZone]:
    """Zones whose departure is at or before ``as_of_index``."""
    end = min(as_of_index, len(candles) - 1)
    if end < atr_period + base_max_candles + 2:
        return []
    start = max(atr_period, end - scan_bars)
    highs = [float(c.get("high") or c.get("h") or 0) for c in candles[: end + 1]]
    lows = [float(c.get("low") or c.get("l") or 0) for c in candles[: end + 1]]
    closes = [float(c.get("close") or c.get("c") or 0) for c in candles[: end + 1]]
    # Same Wilder value as atr(prefix through the departure bar), computed once.
    atr_at = calc_atr_series(highs, lows, closes, atr_period)

    found: list[CausalZone] = []
    i = start
    while i < end - 1:
        zone = _pattern_at(
            candles,
            i,
            end,
            atr_at,
            impulse_body_ratio=impulse_body_ratio,
            base_max_candles=base_max_candles,
            min_departure_atr_mult=min_departure_atr_mult,
        )
        if zone is None:
            i += 1
            continue
        found.append(zone)
        i = zone.departure_index + 1
    return found


def _pattern_at(
    candles: Sequence[Mapping[str, Any]],
    impulse_idx: int,
    as_of: int,
    atr_at: Sequence[float | None],
    *,
    impulse_body_ratio: float,
    base_max_candles: int,
    min_departure_atr_mult: float,
) -> CausalZone | None:
    o, h, l, c = ohlc(candles, impulse_idx)
    if candle_body_ratio(o, h, l, c) < impulse_body_ratio:
        return None
    bullish = c > o
    bearish = c < o
    if not bullish and not bearish:
        return None
    base_start = impulse_idx + 1
    departure_index = base_start + base_max_candles
    if departure_index > as_of or departure_index >= len(candles):
        return None
    base_end = departure_index
    base_high = max(ohlc(candles, j)[1] for j in range(base_start, base_end))
    base_low = min(ohlc(candles, j)[2] for j in range(base_start, base_end))
    _, _, _, dep_close = ohlc(candles, base_end)
    atr_val = atr_at[base_end] if base_end < len(atr_at) else None
    if atr_val is None or atr_val <= 0:
        return None
    departure = abs(dep_close - (base_high + base_low) / 2.0)
    if departure < min_departure_atr_mult * atr_val:
        return None
    if bullish and dep_close > base_high:
        zone_type = "demand"
    elif bearish and dep_close < base_low:
        zone_type = "supply"
    else:
        return None

    touches = 0
    broken = False
    for j in range(base_end + 1, as_of + 1):
        _, hj, lj, cj = ohlc(candles, j)
        if _overlaps(hj, lj, base_low, base_high):
            touches += 1
        if zone_type == "demand" and cj < base_low:
            broken = True
            break
        if zone_type == "supply" and cj > base_high:
            broken = True
            break
    return CausalZone(
        zone_type=zone_type,
        low=base_low,
        high=base_high,
        impulse_index=impulse_idx,
        departure_index=base_end,
        touch_count=touches,
        broken=broken,
        age_bars=as_of - base_end,
    )


def nearest_ahead(
    zones: Sequence[CausalZone],
    *,
    direction: str,
    entry: float,
) -> CausalZone | None:
    """Nearest unbroken opposing zone that still sits in front of the entry."""
    best: CausalZone | None = None
    best_distance = float("inf")
    for zone in zones:
        if zone.broken:
            continue
        if direction == "LONG" and zone.zone_type == "supply" and zone.low > entry:
            distance = zone.low - entry
        elif direction == "SHORT" and zone.zone_type == "demand" and zone.high < entry:
            distance = entry - zone.high
        else:
            continue
        if distance < best_distance:
            best = zone
            best_distance = distance
    return best


def most_recent_touched(
    zones: Sequence[CausalZone],
    candles: Sequence[Mapping[str, Any]],
    *,
    zone_type: str,
    touch_end: int,
    touch_start: int,
) -> CausalZone | None:
    """Latest non-broken zone of ``zone_type`` touched inside the window."""
    best: CausalZone | None = None
    best_touch = -1
    for zone in zones:
        if zone.zone_type != zone_type or zone.broken:
            continue
        if zone.departure_index >= touch_end:
            continue
        last_touch = -1
        start = max(zone.departure_index + 1, touch_start)
        for j in range(start, touch_end + 1):
            _, h, l, _ = ohlc(candles, j)
            if _overlaps(h, l, zone.low, zone.high):
                last_touch = j
        if last_touch > best_touch:
            best = zone
            best_touch = last_touch
    return best
