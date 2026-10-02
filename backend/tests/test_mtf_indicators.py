from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.engines.mtf import indicators as ind
from app.engines.mtf.engine import MTFEngine
from app.models.schemas import DataStatus


def test_sma_and_ema():
    prices = list(range(1, 21))
    assert ind.sma(prices, 20) == pytest.approx(10.5)
    ema_val = ind.ema(prices, 20)
    assert ema_val is not None
    assert ema_val == pytest.approx(10.5)
    assert ind.sma(prices, 25) is None


def test_rsi_bounds():
    closes = [100.0] * 20 + [101.0] * 5
    rsi = ind.rsi(closes, 14)
    assert rsi is not None
    assert 50 <= rsi <= 100


def test_atr_and_volatility():
    n = 30
    highs = [float(i + 2) for i in range(n)]
    lows = [float(i) for i in range(n)]
    closes = [float(i + 1) for i in range(n)]
    atr = ind.atr(highs, lows, closes, 14)
    assert atr is not None
    assert atr > 0
    vol = ind.volatility(closes, 20)
    assert vol is not None
    assert vol >= 0


def test_vwap_and_relative_volume():
    h = [10.0, 11.0, 12.0]
    l = [8.0, 9.0, 10.0]
    c = [9.0, 10.0, 11.0]
    v = [100.0, 200.0, 300.0]
    vw = ind.vwap(h, l, c, v)
    assert vw is not None
    vsma = ind.volume_sma(v, 2)
    assert vsma == pytest.approx(250.0)
    rvol = ind.relative_volume(300.0, vsma)
    assert rvol == pytest.approx(1.2)


def _candle(i: int, o: float, h: float, l: float, c: float, v: float, closed: bool = True):
    return {
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "time": datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() + i * 60,
        "closed": closed,
    }


def test_mtf_engine_waiting_on_open_candle():
    cfg = {
        "mtf": {
            "ema_periods": [20],
            "sma_periods": [20],
            "rsi_period": 14,
            "atr_period": 14,
            "volume_sma_period": 20,
            "vwap_enabled": True,
            "volatility_period": 20,
        }
    }
    engine = MTFEngine(cfg)
    candles = [_candle(i, 100 + i * 0.1, 101, 99, 100 + i * 0.1, 1000) for i in range(25)]
    candles[-1]["closed"] = False
    out = engine.on_candle_close("BTCUSDT", "5m", candles)
    assert out["ema_20"].status == DataStatus.WAITING


def test_mtf_engine_live_on_close():
    cfg = {
        "mtf": {
            "ema_periods": [5],
            "sma_periods": [5],
            "rsi_period": 14,
            "atr_period": 14,
            "volume_sma_period": 5,
            "vwap_enabled": True,
            "volatility_period": 5,
        }
    }
    engine = MTFEngine(cfg)
    candles = [_candle(i, 100 + i, 102 + i, 98 + i, 100 + i, 1000 + i * 10) for i in range(30)]
    out = engine.on_candle_close("BTCUSDT", "5m", candles)
    assert out["ema_5"].status == DataStatus.LIVE
    assert out["ema_5"].value is not None
    assert out["rsi"].status == DataStatus.LIVE
