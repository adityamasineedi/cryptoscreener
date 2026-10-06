"""LH-intact mirror of HL-intact for SHORT research reverse of long."""

from __future__ import annotations

from app.research.combination_engine import lh_intact_for_short
from app.signals.schemas import SwingRecord


def _candle(o: float, h: float, l: float, c: float) -> dict:
    return {"open": o, "high": h, "low": l, "close": c}


def _lh(price: float, bar_index: int, label: str = "LH") -> SwingRecord:
    return SwingRecord(
        symbol="T",
        timeframe="1h",
        swing_type="HIGH",
        price=price,
        timestamp=None,
        bar_index=bar_index,
        strength=1.0,
        confirmed_at=None,
        label=label,
    )


def test_lh_intact_passes_when_last_high_is_lh_and_never_closed_above():
    candles = [
        _candle(100, 105, 99, 104),
        _candle(104, 110, 103, 108),  # LH pivot bar
        _candle(108, 109, 100, 101),
        _candle(101, 102, 98, 99),
        _candle(99, 100, 95, 96),
    ]
    ok, reason = lh_intact_for_short(candles, [_lh(110.0, 1)], 4)
    assert ok is True
    assert reason == "LH_INTACT"


def test_lh_intact_fails_when_close_breaks_above_lh():
    candles = [
        _candle(100, 105, 99, 104),
        _candle(104, 110, 103, 108),  # LH pivot
        _candle(108, 112, 107, 111),  # close above LH
        _candle(111, 112, 100, 101),
    ]
    ok, reason = lh_intact_for_short(candles, [_lh(110.0, 1)], 3)
    assert ok is False
    assert reason == "LH_BROKEN"


def test_lh_intact_fails_without_lh_label():
    candles = [_candle(100, 105, 99, 104), _candle(104, 110, 103, 108)]
    ok, reason = lh_intact_for_short(candles, [_lh(110.0, 0, label="HH")], 1)
    assert ok is False
    assert reason == "NO_LH"
