"""Supply / demand zone detection from impulse–base–departure patterns."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from app.engines.mtf.indicators import atr as calc_atr
from app.engines.mtf.indicators import candle_body_ratio


class ZoneType(str, Enum):
    SUPPLY = "supply"
    DEMAND = "demand"


class ZoneStatus(str, Enum):
    FRESH = "FRESH"
    TESTED = "TESTED"
    WEAKENED = "WEAKENED"
    BROKEN = "BROKEN"
    EXPIRED = "EXPIRED"


@dataclass
class SupplyDemandZone:
    symbol: str
    timeframe: str
    zone_type: ZoneType
    high: float
    low: float
    created_at: datetime | None
    strength: float
    freshness: float
    touch_count: int = 0
    reaction_strength: float = 0.0
    status: ZoneStatus = ZoneStatus.FRESH
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "zone_type": self.zone_type.value,
            "high": self.high,
            "low": self.low,
            "created_at": self.created_at,
            "strength": self.strength,
            "freshness": self.freshness,
            "touch_count": self.touch_count,
            "reaction_strength": self.reaction_strength,
            "status": self.status.value,
        }


def _candle_time(candle: Mapping[str, Any]) -> datetime | None:
    raw = candle.get("time") or candle.get("timestamp")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


def _ohlc(c: Mapping[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(c.get("open") or c.get("o") or 0),
        float(c.get("high") or c.get("h") or 0),
        float(c.get("low") or c.get("l") or 0),
        float(c.get("close") or c.get("c") or 0),
    )


class SupplyDemandEngine:
    """Builds and maintains S/D zones from closed candles."""

    def __init__(self, indicators_config: Mapping[str, Any] | None = None) -> None:
        cfg = dict(indicators_config or {})
        sd = dict(cfg.get("supply_demand") or cfg)
        self.impulse_body_ratio: float = float(sd.get("impulse_body_ratio", 0.6))
        self.base_max_candles: int = int(sd.get("base_max_candles", 8))
        self.min_departure_atr_mult: float = float(sd.get("min_departure_atr_mult", 1.2))
        self.freshness_decay_hours: float = float(sd.get("freshness_decay_hours", 72))
        self.max_touches_before_weak: int = int(sd.get("max_touches_before_weak", 3))
        self._atr_period: int = int(
            (cfg.get("mtf") or {}).get("atr_period", 14)
        )

    def detect_zones(
        self,
        symbol: str,
        timeframe: str,
        candles: list[Mapping[str, Any]],
        now: datetime | None = None,
    ) -> list[SupplyDemandZone]:
        if len(candles) < self._atr_period + self.base_max_candles + 3:
            return []
        highs = [float(c.get("high") or c.get("h") or 0) for c in candles]
        lows = [float(c.get("low") or c.get("l") or 0) for c in candles]
        closes = [float(c.get("close") or c.get("c") or 0) for c in candles]
        atr_val = calc_atr(highs, lows, closes, self._atr_period)
        if atr_val is None or atr_val <= 0:
            return []

        zones: list[SupplyDemandZone] = []
        n = len(candles)
        i = self._atr_period
        while i < n - 2:
            zone = self._try_pattern_at(
                symbol, timeframe, candles, i, atr_val, now
            )
            if zone:
                zones.append(zone)
                i += self.base_max_candles + 2
            else:
                i += 1
        return zones

    def update_zone_status(
        self,
        zone: SupplyDemandZone,
        candles: list[Mapping[str, Any]],
        now: datetime | None = None,
    ) -> SupplyDemandZone:
        """Refresh touch count, status, freshness from subsequent price action."""
        now = now or datetime.now(timezone.utc)
        if zone.created_at and self.freshness_decay_hours > 0:
            age_h = (now - zone.created_at).total_seconds() / 3600.0
            zone.freshness = max(0.0, 1.0 - age_h / self.freshness_decay_hours)
            if zone.freshness <= 0 and zone.status != ZoneStatus.BROKEN:
                zone.status = ZoneStatus.EXPIRED

        for c in candles:
            _, h, l, cl = _ohlc(c)
            if self._touches_zone(zone, h, l):
                zone.touch_count += 1
                if zone.status == ZoneStatus.FRESH:
                    zone.status = ZoneStatus.TESTED
                if zone.touch_count >= self.max_touches_before_weak:
                    zone.status = ZoneStatus.WEAKENED
                zone.reaction_strength = max(
                    zone.reaction_strength, abs(cl - (zone.high + zone.low) / 2)
                )
            if self._is_broken(zone, cl):
                zone.status = ZoneStatus.BROKEN
                break
        return zone

    def _touches_zone(self, zone: SupplyDemandZone, high: float, low: float) -> bool:
        return low <= zone.high and high >= zone.low

    def _is_broken(self, zone: SupplyDemandZone, close: float) -> bool:
        if zone.zone_type == ZoneType.DEMAND:
            return close < zone.low
        return close > zone.high

    def _try_pattern_at(
        self,
        symbol: str,
        timeframe: str,
        candles: list[Mapping[str, Any]],
        impulse_idx: int,
        atr_val: float,
        now: datetime | None,
    ) -> SupplyDemandZone | None:
        o, h, l, c = _ohlc(candles[impulse_idx])
        body_r = candle_body_ratio(o, h, l, c)
        if body_r < self.impulse_body_ratio:
            return None
        bullish_impulse = c > o
        bearish_impulse = c < o
        if not bullish_impulse and not bearish_impulse:
            return None

        base_start = impulse_idx + 1
        base_end = min(base_start + self.base_max_candles, len(candles) - 1)
        if base_end <= base_start:
            return None

        base_high = max(_ohlc(candles[j])[1] for j in range(base_start, base_end))
        base_low = min(_ohlc(candles[j])[2] for j in range(base_start, base_end))
        dep_idx = base_end
        if dep_idx >= len(candles):
            return None
        _, dh, dl, dc = _ohlc(candles[dep_idx])
        dep_range = abs(dc - (base_high + base_low) / 2)
        if dep_range < self.min_departure_atr_mult * atr_val:
            return None

        if bullish_impulse and dc > base_high:
            zt = ZoneType.DEMAND
        elif bearish_impulse and dc < base_low:
            zt = ZoneType.SUPPLY
        else:
            return None

        created = _candle_time(candles[base_start]) or now
        strength = min(1.0, body_r * (dep_range / (atr_val * self.min_departure_atr_mult)))
        return SupplyDemandZone(
            symbol=symbol,
            timeframe=timeframe,
            zone_type=zt,
            high=base_high,
            low=base_low,
            created_at=created,
            strength=strength,
            freshness=1.0,
            status=ZoneStatus.FRESH,
            meta={"impulse_index": impulse_idx, "departure_index": dep_idx},
        )
