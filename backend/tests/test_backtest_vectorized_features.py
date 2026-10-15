"""Parity coverage for the NumPy-backed research backtest fast path."""

from __future__ import annotations

import pytest

from app.engines.mtf.indicators import atr
from app.research.combination_backtest import (
    _bos_break_levels,
    _bos_break_possible,
    _close_breaks_level,
    _rolling_mean_inclusive,
)
from app.signals import bos_engine
from app.signals._candle_utils import series_ohlcv
from app.signals.schemas import SwingRecord
from app.signals.swing_detector import detect_swings, extend_swings


def _candles(n: int = 40) -> list[dict[str, float]]:
    return [
        {
            "open": 10.0,
            "high": 13.0,
            "low": 7.0,
            "close": 12.0,
            "volume": float(100 + i),
        }
        for i in range(n)
    ]


def _swings() -> list[SwingRecord]:
    return [
        SwingRecord("T", "1h", "LOW", 7.0, None, 5, 1.0, None, "HL"),
        SwingRecord("T", "1h", "HIGH", 15.0, None, 10, 1.0, None, "HH"),
    ]


def test_vectorized_volume_mean_matches_legacy_window() -> None:
    values = [float(i * 7 + 3) for i in range(57)]
    actual = _rolling_mean_inclusive(values, 20)

    expected = [
        sum(values[max(0, i - 19) : i + 1])
        / len(values[max(0, i - 19) : i + 1])
        for i in range(len(values))
    ]

    assert actual.tolist() == pytest.approx(expected, rel=0.0, abs=1e-12)


@pytest.mark.parametrize("confirmed", [False, True])
def test_precomputed_bos_path_matches_legacy_output(confirmed: bool) -> None:
    candles = _candles()
    if confirmed:
        candles[-1].update(high=17.0, close=16.0)
    swings = _swings()
    trend = {"trend": "BULLISH", "reason": "HH+HL"}

    legacy = bos_engine.detect_bos(
        candles,
        swings,
        trend,
        symbol="T",
        timeframe="1h",
    )
    _, highs, lows, closes, volumes = series_ohlcv(candles)
    atr_value = atr(highs, lows, closes, 14)
    averages = _rolling_mean_inclusive(volumes, 20)
    optimized = bos_engine.detect_bos(
        candles,
        swings,
        trend,
        symbol="T",
        timeframe="1h",
        atr_value=atr_value,
        volumes=volumes,
        avg_volume_ref=float(averages[-1]),
    )

    assert optimized == legacy


def test_precomputed_bos_path_does_not_rebuild_ohlcv_prefix(monkeypatch) -> None:
    candles = _candles()
    _, highs, lows, closes, volumes = series_ohlcv(candles)
    atr_value = atr(highs, lows, closes, 14)

    def _unexpected_prefix_conversion(*_args, **_kwargs):
        raise AssertionError("precomputed backtest path rebuilt the OHLCV prefix")

    monkeypatch.setattr(bos_engine, "series_ohlcv", _unexpected_prefix_conversion)
    result = bos_engine.detect_bos(
        candles,
        _swings(),
        {"trend": "BULLISH"},
        symbol="T",
        timeframe="1h",
        atr_value=atr_value,
        volumes=volumes,
        avg_volume_ref=float(_rolling_mean_inclusive(volumes, 20)[-1]),
    )

    assert result is not None
    assert result["state"] == "NONE"


def _break_candles(*, impulse: bool) -> list[dict[str, float]]:
    candles = []
    for i in range(30):
        candles.append(
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 100.0,
            }
        )
    if impulse:
        candles[-1] = {
            "open": 100.0,
            "high": 120.0,
            "low": 99.5,
            "close": 119.0,
            "volume": 500.0,
        }
    else:
        candles[-1] = {
            "open": 110.0,
            "high": 111.0,
            "low": 100.0,
            "close": 110.2,
            "volume": 10.0,
        }
    return candles


def test_bos_impulse_candidate_requires_range_body_and_volume() -> None:
    swings = _swings()
    swings[1] = SwingRecord("T", "1h", "HIGH", 105.0, None, 10, 1.0, None, "HH")
    trend = {"trend": "BULLISH", "reason": "HH+HL"}

    weak = bos_engine.detect_bos(
        _break_candles(impulse=False),
        swings,
        trend,
        symbol="T",
        timeframe="1h",
    )
    strong = bos_engine.detect_bos(
        _break_candles(impulse=True),
        swings,
        trend,
        symbol="T",
        timeframe="1h",
    )

    assert weak is not None and weak["direction"] == "BULLISH_BOS"
    assert weak["impulse_candidate"] is False
    assert strong is not None and strong["direction"] == "BULLISH_BOS"
    assert strong["impulse_candidate"] is True


def test_cached_bos_break_matches_per_bar_prefilter() -> None:
    candles = []
    price = 100.0
    for i in range(180):
        wave = 4.0 if (i // 6) % 2 == 0 else -4.0
        open_ = price
        close = price + wave * 0.35
        candles.append(
            {
                "time": i,
                "open": open_,
                "high": max(open_, close) + 0.4,
                "low": min(open_, close) - 0.4,
                "close": close,
                "volume": 100.0,
            }
        )
        price = close
    _, highs, lows, closes, _ = series_ohlcv(candles)
    kwargs = dict(
        left=3,
        right=3,
        symbol="T",
        timeframe="1h",
        atr_period=14,
        minimum_swing_distance_atr=0.0,
        highs=highs,
        lows=lows,
        closes=closes,
    )
    swings = detect_swings(candles, as_of_index=40, **kwargs)
    cached_n = -1
    levels = None
    for i in range(40, len(candles)):
        swings = extend_swings(swings, candles, as_of_index=i, **kwargs)
        if len(swings) != cached_n:
            cached_n = len(swings)
            levels = _bos_break_levels(swings, i)
        if levels is None:
            cached = False
        else:
            trend_state, high_px, low_px = levels
            cached = _close_breaks_level(
                trend_state, float(closes[i]), high_px, low_px, "LONG"
            )
        assert cached == _bos_break_possible(
            candles, i, swings, direction_filter="LONG"
        )
