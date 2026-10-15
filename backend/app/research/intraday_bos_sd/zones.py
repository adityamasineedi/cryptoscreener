"""15m supply and demand zones created by buffered BOS candles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.research.intraday_bos_sd.candles import Series
from app.research.intraday_bos_sd.spec import ZONE_BODY_ATR, ZONE_EXPIRY_HOURS, ZONE_LOOKBACK_CANDLES
from app.research.intraday_bos_sd.structure import StructureEvent, StructureState


@dataclass
class Zone:
    side: str  # DEMAND or SUPPLY
    low: float
    high: float
    origin_index: int
    bos_index: int
    active_from: object
    origin_open: float
    origin_close: float
    invalid_from: object | None = None
    touched: bool = False

    def expired(self, when) -> bool:
        return when >= self.active_from + timedelta(hours=ZONE_EXPIRY_HOURS)

    def invalidated(self, when) -> bool:
        return self.invalid_from is not None and when >= self.invalid_from

    def eligible_for_retest(self, when) -> bool:
        return (
            (not self.touched)
            and (not self.invalidated(when))
            and (not self.expired(when))
            and when > self.active_from
        )


def create_zones(series: Series, structure: StructureState) -> list[Zone]:
    zones: list[Zone] = []
    for event in structure.events:
        if event.kind != "BOS":
            continue
        i = event.index
        atr = series.preceding_atr(i)
        if atr is None:
            continue
        body = abs(series.closes[i] - series.opens[i])
        if body < ZONE_BODY_ATR * atr:
            continue
        start = max(0, i - ZONE_LOOKBACK_CANDLES)
        origin = None
        if event.direction == "BULLISH":
            for j in range(i - 1, start - 1, -1):
                if series.closes[j] < series.opens[j]:
                    origin = j
                    break
            side = "DEMAND"
        else:
            for j in range(i - 1, start - 1, -1):
                if series.closes[j] > series.opens[j]:
                    origin = j
                    break
            side = "SUPPLY"
        if origin is None:
            continue
        zones.append(
            Zone(
                side=side,
                low=series.lows[origin],
                high=series.highs[origin],
                origin_index=origin,
                bos_index=i,
                active_from=series.close_time(i),
                origin_open=series.opens[origin],
                origin_close=series.closes[origin],
            )
        )
    _apply_invalidations(series, zones)
    return zones


def _apply_invalidations(series: Series, zones: list[Zone]) -> None:
    for i in range(len(series)):
        when = series.close_time(i)
        close = series.closes[i]
        for zone in zones:
            if zone.invalid_from is not None or when <= zone.active_from:
                continue
            if zone.side == "DEMAND" and close < zone.low:
                zone.invalid_from = when
            elif zone.side == "SUPPLY" and close > zone.high:
                zone.invalid_from = when


def select_zone(zones: list[Zone], *, side: str, when, price_low: float, price_high: float) -> Zone | None:
    """Newest active overlapping zone. Does not mark it touched."""
    matches = [
        z
        for z in zones
        if z.side == side and z.eligible_for_retest(when) and price_low <= z.high and price_high >= z.low
    ]
    if not matches:
        return None
    matches.sort(key=lambda z: (z.active_from, z.bos_index))
    return matches[-1]


def obstructing_zones(zones: list[Zone], *, side: str, when, entry: float, target: float) -> list[Zone]:
    """Active opposing zones whose range intersects the path to the target."""
    found = []
    for zone in zones:
        if zone.side != side or zone.invalidated(when) or zone.expired(when):
            continue
        if when <= zone.active_from:
            continue
        # Long path is (entry, target]. Short path is [target, entry).
        if target > entry:
            if zone.high > entry and zone.low <= target:
                found.append(zone)
        elif zone.low < entry and zone.high >= target:
            found.append(zone)
    return found
