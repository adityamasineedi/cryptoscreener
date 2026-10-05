"""Safety-gate unit tests for COMBO_02 v1 (HL intact + fill revalidation + HTF codes)."""

from __future__ import annotations

from app.research.bos_strategy_comparison.htf import (
    HTF_ALIGNED,
    HTF_CONFLICT,
    HTF_NEUTRAL_UNAVAILABLE,
    classify_htf_alignment,
)
from app.research.combination_engine import (
    hl_intact_for_long,
    revalidate_combo02_long_fill,
)
from app.signals.schemas import SwingRecord


def test_htf_mixed_is_neutral_not_conflict():
    assert (
        classify_htf_alignment(
            bos_direction="BULLISH_BOS",
            trend_4h="BEARISH",
            trend_1h="BULLISH",
        )
        == HTF_NEUTRAL_UNAVAILABLE
    )


def test_htf_both_bearish_is_conflict():
    assert (
        classify_htf_alignment(
            bos_direction="BULLISH_BOS",
            trend_4h="BEARISH",
            trend_1h="BEARISH",
        )
        == HTF_CONFLICT
    )


def test_htf_both_bullish_aligned():
    assert (
        classify_htf_alignment(
            bos_direction="BULLISH_BOS",
            trend_4h="BULLISH",
            trend_1h="BULLISH",
        )
        == HTF_ALIGNED
    )


def _candle(i: int, *, c: float, h: float | None = None, l: float | None = None):
    return {
        "open_time": f"2026-01-01T{i:02d}:00:00+00:00",
        "open": c,
        "high": h if h is not None else c + 1,
        "low": l if l is not None else c - 1,
        "close": c,
        "volume": 1.0,
    }


def test_hl_intact_passes_when_never_closed_below():
    candles = [_candle(i, c=100 + i) for i in range(10)]
    swings = [
        SwingRecord(
            symbol="T",
            timeframe="1h",
            swing_type="LOW",
            price=100.0,
            timestamp=None,
            bar_index=2,
            strength=1.0,
            confirmed_at=None,
            label="HL",
        )
    ]
    ok, reason = hl_intact_for_long(candles, swings, 9)
    assert ok and reason == "HL_INTACT"


def test_hl_broken_when_close_dips_below_hl():
    candles = [_candle(i, c=110.0) for i in range(10)]
    candles[5] = _candle(5, c=99.0)  # close below HL=100
    swings = [
        SwingRecord(
            symbol="T",
            timeframe="1h",
            swing_type="LOW",
            price=100.0,
            timestamp=None,
            bar_index=2,
            strength=1.0,
            confirmed_at=None,
            label="HL",
        )
    ]
    ok, reason = hl_intact_for_long(candles, swings, 9)
    assert not ok and reason == "HL_BROKEN"


def test_revalidate_fill_requires_htf_aligned():
    ok, reason = revalidate_combo02_long_fill(
        {
            "status": "LONG_ENTRY_CANDIDATE",
            "direction": "LONG",
            "entry_price": 100,
            "stop_price": 90,
            "htf": {"htf_alignment": "HTF_CONFLICT"},
            "gates": {"hl_intact": True},
        }
    )
    assert not ok
    assert "HTF_" in reason


def test_revalidate_fill_passes_aligned():
    ok, reason = revalidate_combo02_long_fill(
        {
            "status": "LONG_ENTRY_CANDIDATE",
            "direction": "LONG",
            "entry_price": 100,
            "stop_price": 90,
            "htf": {"htf_alignment": HTF_ALIGNED},
            "gates": {"hl_intact": True},
        }
    )
    assert ok and reason == "OK"
