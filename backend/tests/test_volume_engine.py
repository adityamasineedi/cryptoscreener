from __future__ import annotations

import pytest

from app.engines.volume.engine import VolumeEngine, taker_imbalance, volume_regime, volume_zscore


def _c(v: float, tb: float | None = None) -> dict:
    d = {"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": v}
    if tb is not None:
        d["taker_buy_volume"] = tb
    return d


def test_volume_zscore():
    vols = [100.0] * 19 + [200.0]
    z = volume_zscore(vols, 20)
    assert z is not None
    assert z > 0


def test_taker_imbalance():
    assert taker_imbalance(70.0, 100.0) == pytest.approx(0.4)


def test_volume_regime():
    assert volume_regime(2.0, 1.5) == "expansion"
    assert volume_regime(0.3, 1.5) == "contraction"
    assert volume_regime(1.0, 1.5) == "normal"


def test_volume_engine_compute():
    candles = [_c(1000 + i * 10, tb=500 + i * 5) for i in range(25)]
    engine = VolumeEngine({"volume": {"sma_period": 20, "zscore_period": 20, "rvol_threshold": 1.5}})
    result = engine.compute("BTCUSDT", "5m", candles)
    assert "raw" in result and "normalized" in result and "fresh" in result
    assert result["raw"]["volume"] == candles[-1]["volume"]
    assert result["fresh"]["volume"].value is not None
