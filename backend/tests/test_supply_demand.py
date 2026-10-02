from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.engines.supply_demand.engine import SupplyDemandEngine, ZoneStatus, ZoneType


def _flat_base(i: int, mid: float = 100.0) -> dict:
    return {
        "open": mid,
        "high": mid + 0.2,
        "low": mid - 0.2,
        "close": mid,
        "volume": 500.0,
        "time": datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
    }


def _impulse_bull(i: int) -> dict:
    return {
        "open": 98.0,
        "high": 102.0,
        "low": 97.5,
        "close": 101.5,
        "volume": 2000.0,
        "time": datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
    }


def _departure_bull(i: int) -> dict:
    return {
        "open": 100.2,
        "high": 106.0,
        "low": 100.0,
        "close": 105.5,
        "volume": 2500.0,
        "time": datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
    }


def _build_series(length: int = 40) -> list[dict]:
    candles = []
    for i in range(length):
        candles.append(
            {
                "open": 100.0 + (i % 3) * 0.1,
                "high": 101.0 + (i % 3) * 0.1,
                "low": 99.0 + (i % 3) * 0.1,
                "close": 100.0 + (i % 3) * 0.1,
                "volume": 800.0 + i * 5,
                "time": datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
            }
        )
    return candles


def test_zone_status_broken_demand():
    engine = SupplyDemandEngine({"supply_demand": {"max_touches_before_weak": 2}})
    from app.engines.supply_demand.engine import SupplyDemandZone

    zone = SupplyDemandZone(
        symbol="BTCUSDT",
        timeframe="15m",
        zone_type=ZoneType.DEMAND,
        high=100.5,
        low=99.5,
        created_at=datetime.now(timezone.utc),
        strength=0.8,
        freshness=1.0,
    )
    touch = [{"open": 100, "high": 100.4, "low": 99.8, "close": 100.1, "volume": 100}]
    engine.update_zone_status(zone, touch)
    assert zone.status == ZoneStatus.TESTED
    break_c = [{"open": 99, "high": 99.2, "low": 98.5, "close": 98.7, "volume": 100}]
    engine.update_zone_status(zone, break_c)
    assert zone.status == ZoneStatus.BROKEN


def test_detect_zones_returns_list():
    engine = SupplyDemandEngine(
        {
            "mtf": {"atr_period": 14},
            "supply_demand": {
                "impulse_body_ratio": 0.5,
                "base_max_candles": 3,
                "min_departure_atr_mult": 0.5,
            },
        }
    )
    candles = _build_series(50)
    idx = 20
    candles[idx] = _impulse_bull(idx)
    for j in range(1, 4):
        candles[idx + j] = _flat_base(idx + j)
    candles[idx + 4] = _departure_bull(idx + 4)
    zones = engine.detect_zones("BTCUSDT", "15m", candles)
    assert isinstance(zones, list)
    if zones:
        assert zones[0].zone_type in (ZoneType.DEMAND, ZoneType.SUPPLY)
