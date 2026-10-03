"""Incremental swing updates must match full detect_swings."""

from __future__ import annotations

from app.signals._candle_utils import series_ohlcv
from app.signals.swing_detector import detect_swings, extend_swings


def _synth(n: int = 80) -> list[dict]:
    out = []
    price = 100.0
    for i in range(n):
        # Gentle zig-zag so swings confirm
        wave = 3.0 if (i // 5) % 2 == 0 else -3.0
        o = price
        c = price + wave * 0.4
        h = max(o, c) + 0.5
        l = min(o, c) - 0.5
        out.append(
            {
                "time": i,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 1.0,
            }
        )
        price = c
    return out


def test_extend_swings_matches_full_detect() -> None:
    candles = _synth(120)
    _, highs, lows, closes, _ = series_ohlcv(candles)
    kwargs = dict(
        left=3,
        right=3,
        symbol="TEST",
        timeframe="15m",
        atr_period=14,
        minimum_swing_distance_atr=0.0,
        highs=highs,
        lows=lows,
        closes=closes,
    )
    swings: list = []
    for i in range(len(candles)):
        swings = extend_swings(swings, candles, as_of_index=i, **kwargs)
        full = detect_swings(candles, as_of_index=i, **kwargs)
        assert [(s.bar_index, s.swing_type, round(s.price, 8)) for s in swings] == [
            (s.bar_index, s.swing_type, round(s.price, 8)) for s in full
        ]
