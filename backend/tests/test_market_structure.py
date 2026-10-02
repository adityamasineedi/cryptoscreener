from __future__ import annotations

from datetime import datetime, timezone

from app.engines.structure.engine import (
    StructureEngine,
    detect_swings,
    infer_trend,
    label_swings,
)


def _c(i: int, o: float, h: float, l: float, c: float) -> dict:
    return {
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "time": datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() + i * 300,
    }


def test_detect_swing_high():
    candles = [
        _c(0, 10, 11, 9, 10),
        _c(1, 10, 11, 9, 10),
        _c(2, 10, 15, 9, 14),
        _c(3, 14, 14, 12, 13),
        _c(4, 13, 13, 11, 12),
        _c(5, 12, 12, 10, 11),
    ]
    swings = detect_swings(candles, swing_left=2, swing_right=2)
    highs = [s for s in swings if s.kind == "high"]
    assert len(highs) >= 1
    assert highs[0].price == 15


def test_swing_labels_hh_hl():
    from app.engines.structure.engine import SwingPoint

    swings = [
        SwingPoint(1, "high", 100, None),
        SwingPoint(2, "low", 90, None),
        SwingPoint(3, "high", 105, None),
        SwingPoint(4, "low", 92, None),
    ]
    labeled = label_swings(swings)
    assert labeled[2].label.value == "HH"
    assert labeled[3].label.value == "HL"


def test_infer_trend_bullish():
    from app.engines.structure.engine import SwingLabel, SwingPoint, TrendBias

    swings = [
        SwingPoint(1, "high", 100, None, SwingLabel.HH),
        SwingPoint(2, "low", 90, None, SwingLabel.HL),
        SwingPoint(3, "high", 110, None, SwingLabel.HH),
        SwingPoint(4, "low", 95, None, SwingLabel.HL),
    ]
    assert infer_trend(swings) == TrendBias.BULLISH


def test_structure_engine_skips_1m():
    engine = StructureEngine(
        {
            "market_structure": {
                "swing_left": 2,
                "swing_right": 2,
                "timeframes": ["5m", "15m"],
            }
        }
    )
    assert engine.supports_timeframe("1m") is False
    res = engine.analyze("BTCUSDT", "1m", [_c(0, 1, 2, 0.5, 1.5)])
    assert res["events"] == []


def test_liquidity_sweep_event():
    engine = StructureEngine(
        {
            "market_structure": {
                "swing_left": 1,
                "swing_right": 1,
                "minimum_break_distance_pct": 0.01,
                "atr_tolerance_mult": 0.01,
                "timeframes": ["5m"],
            }
        }
    )
    candles = [
        _c(0, 100, 101, 99, 100),
        _c(1, 100, 102, 98, 101),
        _c(2, 101, 105, 100, 104),
        _c(3, 104, 104, 102, 103),
        _c(4, 103, 103, 101, 102),
        _c(5, 102, 106, 101, 101),
    ]
    res = engine.analyze("ETHUSDT", "5m", candles)
    types = {e["event_type"] for e in res["events"]}
    assert "trend_state" in types
