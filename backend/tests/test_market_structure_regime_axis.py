"""Two-axis regime analytics: prior-only percentiles, warmup, MTF key."""

from datetime import datetime, timedelta, timezone

from app.research.bos_strategy_comparison.htf import (
    build_htf_as_of_index_map_fully_closed,
)
from app.research.market_structure.mtf import combined_structure_key
from app.research.market_structure.regime import (
    classify_market_regime,
    classify_two_axis,
    prior_percentile_threshold,
    prior_percentile_threshold_series,
    unknown_share_after_warmup,
)


def test_percentile_ignores_current_and_future():
    history = [1.0, 2.0, 3.0, 4.0, 5.0, 1000.0]
    at_t = prior_percentile_threshold(history, 5, 10, 100)
    assert at_t == 5.0
    mutated = history[:]
    mutated[5] = 99999.0
    mutated.append(1e9)
    assert prior_percentile_threshold(mutated, 5, 10, 100) == at_t


def test_rolling_percentile_series_matches_scalar_reference():
    history = [None, 3.0, 1.0, None, 2.0, 9.0, 4.0, 4.0, 8.0, 5.0]
    lookback = 6
    percentiles = (80.0, 20.0, 0.0, 100.0)
    actual = prior_percentile_threshold_series(history, lookback, percentiles)

    for output, percentile in zip(actual, percentiles):
        expected = [
            prior_percentile_threshold(history, i, lookback, percentile)
            for i in range(len(history))
        ]
        assert output == expected


def test_two_axis_never_emits_high_volatility_trend():
    snap = {
        "data_quality_state": "OK",
        "trend_state": "BULLISH",
        "structure_state": "UPTREND_HH_HL",
        "ema_alignment": "BULLISH",
        "choppiness_index": 20.0,
        "atr": 5.0,
    }
    axis = classify_two_axis(snap, atr_high=4.0, atr_low=1.0)
    assert axis.trend_state == "TREND_UP"
    assert axis.vol_state == "HIGH"
    assert axis.combined == "TREND_UP|HIGH"
    assert "HIGH_VOLATILITY_TREND" not in (axis.combined or "")
    assert axis.warmup is False


def test_warmup_is_not_transition():
    warming = classify_two_axis(
        {"data_quality_state": "UNKNOWN_INSUFFICIENT_HISTORY", "trend_state": "TRANSITION"},
        atr_high=None,
        atr_low=None,
    )
    assert warming.warmup is True
    assert warming.trend_state is None
    assert warming.unclassified_reason == "WARMUP"
    disagree = classify_two_axis(
        {
            "data_quality_state": "OK",
            "trend_state": "BULLISH",
            "structure_state": "UPTREND_HH_HL",
            "ema_alignment": "BEARISH",
            "choppiness_index": 40.0,
            "atr": 2.0,
        },
        atr_high=4.0,
        atr_low=1.0,
    )
    assert disagree.warmup is False
    assert disagree.trend_state == "TRANSITION"


def test_old_classifier_still_returns_regime_string():
    result = classify_market_regime(
        {
            "data_quality_state": "OK",
            "trend_state": "BULLISH",
            "structure_state": "UPTREND_HH_HL",
            "ema_alignment": "BULLISH",
            "adx": 30,
            "di_plus": 25,
            "di_minus": 10,
            "efficiency_ratio": 0.7,
            "choppiness_index": 30,
            "atr": 2,
            "atr_percentile": 50,
            "bos_state": "NONE",
            "choch_state": "NONE",
            "direction_changes": 1,
        }
    )
    assert result.market_regime


def test_structure_key_and_unknown_share():
    key = combined_structure_key("TREND_UP", "TREND_UP", "BULLISH_PULLBACK", None)
    assert key == "4h_UP/1h_TREND_UP/15m_PULLBACK"
    assert combined_structure_key("TREND_UP", None, None, None) is None
    rows = [
        {"warmup": True, "trend_axis": None, "unclassified_reason": "WARMUP"},
        *[{"warmup": False, "trend_axis": "TREND_UP", "unclassified_reason": None}] * 200,
    ]
    share = unknown_share_after_warmup(rows)
    assert share["unknown_share"] < 0.01
    assert share["bars_after_warmup"] == 200


def test_htf_join_uses_only_closed_candles():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    setup = [{"open_time": start + timedelta(hours=h)} for h in range(8)]
    htf = [
        {"open_time": start, "close": 1.0},
        {"open_time": start + timedelta(hours=4), "close": 2.0},
    ]
    mapping = build_htf_as_of_index_map_fully_closed(
        setup, htf, setup_timeframe="1h", htf_timeframe="4h"
    )
    # 1h bar opening at +3h closes at +4h, so the 4h candle opened at 0 is closed.
    # The 4h candle opened at +4h is not closed yet.
    assert mapping[3] == 0
    assert mapping[4] == 0
    htf[1]["close"] = 999.0
    again = build_htf_as_of_index_map_fully_closed(
        setup, htf, setup_timeframe="1h", htf_timeframe="4h"
    )
    assert again[3] == mapping[3]
