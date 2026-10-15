"""Causal supply/demand structure variants. Research backtest only."""

from datetime import datetime, timedelta, timezone

from app.research.bos_combinations import get_combination
from app.research.config import ResearchConfig
from app.research.sd_structure.sequence import (
    _first_close_through,
    bos_after_choch,
    classify_day_structure,
    director_allows,
    external_swings,
    is_inside_bar,
    preceding_rvol,
    retest_holds,
    structure_swings,
    volume_divergence,
)
from app.research.sd_structure.variants import SD_STRUCTURE_FAMILY
from app.research.sd_structure.zones import causal_zones, nearest_ahead
from app.signals.schemas import SwingRecord


def _bar(index: int, open_, high, low, close, volume=100.0):
    return {
        "time": datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * index),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _swing(kind: str, price: float, bar_index: int, label: str) -> SwingRecord:
    return SwingRecord(
        symbol="BTCUSDT",
        timeframe="15m",
        swing_type=kind,
        price=price,
        timestamp=None,
        bar_index=bar_index,
        strength=1.0,
        confirmed_at=None,
        label=label,
    )


def test_zone_does_not_exist_before_departure_closes():
    candles = [_bar(i, 100, 100.2, 99.8, 100, 100) for i in range(16)]
    candles.append(_bar(16, 100, 110, 100, 109, 500))
    candles.append(_bar(17, 108.6, 109.2, 108.4, 108.8, 80))
    candles.append(_bar(18, 108.7, 109.1, 108.5, 108.9, 80))
    candles.append(_bar(19, 109, 114, 108.8, 113, 400))
    hidden = causal_zones(
        candles,
        18,
        atr_period=3,
        base_max_candles=2,
        min_departure_atr_mult=1.2,
    )
    visible = causal_zones(
        candles,
        19,
        atr_period=3,
        base_max_candles=2,
        min_departure_atr_mult=1.2,
    )
    assert hidden == []
    assert len(visible) == 1
    assert visible[0].zone_type == "demand"
    assert visible[0].departure_index == 19


def test_slot_rvol_ignores_a_different_clock_hour():
    from app.research.sd_structure.sequence import slot_rvol

    candles = []
    for day in range(10):
        quiet = datetime(2024, 1, 1 + day, 3, 0, tzinfo=timezone.utc)
        loud = datetime(2024, 1, 1 + day, 14, 0, tzinfo=timezone.utc)
        candles.append({**_bar(len(candles), 1, 1, 1, 1, 10), "time": quiet})
        candles.append({**_bar(len(candles), 1, 1, 1, 1, 500), "time": loud})
    latest = len(candles)
    candles.append(
        {
            **_bar(latest, 1, 1, 1, 1, 20),
            "time": datetime(2024, 1, 11, 3, 0, tzinfo=timezone.utc),
        }
    )
    assert slot_rvol(candles, latest, samples=9, min_samples=8) == 2.0


def test_review_split_and_daily_stop_do_not_drop_the_original_sample():
    from app.research.sd_structure.review import review_closed_trades

    trades = []
    for index, rnet in enumerate([-1.0, -1.0, -1.0, 2.0]):
        trades.append(
            {
                "signal_time": f"2024-01-01T0{index}:00:00+00:00",
                "r_net": rnet,
                "sd_structure": {"market_regime": "trend" if index < 3 else "range"},
            }
        )
    review = review_closed_trades(trades)
    assert review["sample_warning"] is True
    assert review["daily_loss_slice"]["skipped_n"] == 2
    assert review["daily_loss_slice"]["kept_n"] == 2
    assert review["regime_counts"]["trend"] == 3
    assert review["regime_counts"]["range"] == 1
    assert len(trades) == 4


def test_rvol_uses_the_previous_20_completed_bars():
    candles = [_bar(i, 1, 1, 1, 1, 10) for i in range(21)]
    candles.append(_bar(21, 1, 1, 1, 1, 15))
    assert preceding_rvol(candles, 21, window=20) == 1.5


def test_bos_requires_a_higher_low_and_uses_that_low_as_invalidation():
    candles = [_bar(i, 100, 101, 99, 100, 10) for i in range(16)]
    candles[13] = _bar(13, 109, 110, 108, 109, 10)
    candles[14] = _bar(14, 109, 112, 108, 111, 40)
    swings = [
        _swing("LOW", 90, 4, "LL"),
        _swing("HIGH", 100, 6, "LH"),
        _swing("HIGH", 110, 11, "HH"),
        _swing("LOW", 95, 12, "HL"),
    ]
    missing = bos_after_choch(
        candles,
        swings[:3],
        choch_index=10,
        choch_level=100,
        direction="LONG",
        right_bars=1,
        bos_index=14,
    )
    confirmed = bos_after_choch(
        candles,
        swings,
        choch_index=10,
        choch_level=100,
        direction="LONG",
        right_bars=1,
        bos_index=14,
    )
    assert missing is None
    assert confirmed is not None
    assert confirmed["invalidation"] == 95
    assert confirmed["level"] == 110


def test_volume_divergence_is_lower_volume_at_the_later_extreme():
    candles = [_bar(i, 1, 1, 1, 1, 50) for i in range(8)]
    candles[2] = _bar(2, 1, 1, 1, 1, 80)
    candles[6] = _bar(6, 1, 1, 1, 1, 40)
    swings = [_swing("LOW", 10, 2, "LL"), _swing("LOW", 9, 6, "LL")]
    assert volume_divergence(swings, candles, direction="LONG", as_of_index=7)
    candles[6] = _bar(6, 1, 1, 1, 1, 90)
    assert not volume_divergence(swings, candles, direction="LONG", as_of_index=7)


def test_retest_must_close_back_through_the_broken_level():
    held = _bar(0, 111, 112, 109, 111)
    failed = _bar(0, 111, 112, 109, 109)
    assert retest_holds([held], direction="LONG", level=110, index=0)
    assert not retest_holds([failed], direction="LONG", level=110, index=0)


def test_near_opposing_zone_fails_the_room_check():
    from app.research.sd_structure.evaluate import _room_to_opposing_zone
    from app.research.sd_structure.zones import CausalZone

    supply = CausalZone("supply", 101, 102, 1, 3, 0, False, 4)
    assert not _room_to_opposing_zone(
        direction="LONG", entry=100, stop=99, opposing=supply, min_rr=2
    )
    far = CausalZone("supply", 104, 105, 1, 3, 0, False, 4)
    assert _room_to_opposing_zone(
        direction="LONG", entry=100, stop=99, opposing=far, min_rr=2
    )


def test_nearest_ahead_ignores_zones_already_traded_through():
    from app.research.sd_structure.zones import CausalZone

    broken_path = CausalZone("supply", 90, 91, 1, 2, 1, False, 3)
    ahead = CausalZone("supply", 110, 112, 4, 6, 0, False, 2)
    chosen = nearest_ahead([broken_path, ahead], direction="LONG", entry=100)
    assert chosen is ahead


def test_variants_are_research_combinations_and_leave_combo_01_alone():
    assert "COMBO_01" not in SD_STRUCTURE_FAMILY
    recommended = get_combination("COMBO_SD_C")
    assert recommended is not None
    assert recommended.require_bos is False
    assert "RVOL >= 1.5" in recommended.conditions
    for combination_id in (
        "COMBO_SD_A",
        "COMBO_SD_B",
        "COMBO_SD_C12",
        "COMBO_SD_C",
        "COMBO_SD_C20",
        "COMBO_SD_D",
        "COMBO_SD_E",
        "COMBO_SD_F",
        "COMBO_SD_C_FRESH",
        "COMBO_SD_LIQ",
    ):
        assert get_combination(combination_id) is not None
        assert combination_id in SD_STRUCTURE_FAMILY


def test_inside_bar_is_not_a_swing_and_internal_swings_are_ignored():
    candles = [_bar(0, 100, 110, 90, 100), _bar(1, 100, 105, 95, 101)]
    assert is_inside_bar(candles, 1)
    assert not is_inside_bar(candles, 0)
    swings = [
        _swing("LOW", 90, 0, "LL"),
        _swing("HIGH", 110, 2, "LH"),
        _swing("LOW", 92, 3, "HL"),
        _swing("HIGH", 112, 4, "HH"),
        _swing("HIGH", 111, 5, "INTERNAL"),
        _swing("LOW", 95, 1, "INSIDE"),
    ]
    kept = structure_swings(swings, candles)
    assert all(swing.bar_index != 1 for swing in kept)
    assert all(swing.price != 111 for swing in kept)
    assert any(swing.price == 112 for swing in kept)


def test_external_range_keeps_a_swing_that_breaks_the_boundary():
    swings = [
        _swing("LOW", 90, 1, "LL"),
        _swing("HIGH", 100, 2, "LH"),
        _swing("LOW", 95, 3, "HL"),
        _swing("HIGH", 110, 4, "HH"),
        _swing("HIGH", 111, 5, "BREAK"),
    ]
    kept = external_swings(swings)
    assert any(swing.price == 111 for swing in kept)


def test_a_wick_through_the_level_is_not_a_close_break():
    candles = [
        _bar(0, 100, 101, 99, 100),
        _bar(1, 100, 112, 99, 101),
    ]
    assert not _first_close_through(candles, 1, 110, bullish=True)


def test_director_blocks_only_a_clear_opposite_trend():
    assert director_allows("LONG", "BULLISH")
    assert not director_allows("LONG", "BEARISH")
    assert director_allows("SHORT", "BEARISH")
    assert not director_allows("SHORT", "BULLISH")
    assert director_allows("LONG", "NEUTRAL")
    assert not director_allows("LONG", None)


def test_one_hour_setup_without_a_lower_timeframe_does_not_trade():
    from app.research.sd_structure.evaluate import evaluate_sd_structure_at_bar
    from app.signals.config import SignalConfig

    candles = [_bar(i, 100, 101, 99, 100, 10) for i in range(40)]
    result = evaluate_sd_structure_at_bar(
        symbol="BTCUSDT",
        timeframe="1h",
        candles=candles,
        as_of_index=39,
        combination=get_combination("COMBO_SD_C"),
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
    )
    assert result["status"] == "NO_SETUP"
    assert result["reason"] == "LTF_TRIGGER_UNAVAILABLE"


def test_flat_series_is_not_a_setup():
    from app.research.sd_structure.evaluate import evaluate_sd_structure_at_bar
    from app.signals.config import SignalConfig

    candles = [_bar(i, 100, 101, 99, 100, 10) for i in range(40)]
    result = evaluate_sd_structure_at_bar(
        symbol="BTCUSDT",
        timeframe="15m",
        candles=candles,
        as_of_index=39,
        combination=get_combination("COMBO_SD_C"),
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
    )
    assert result["status"] == "NO_SETUP"
    assert result["combination_id"] == "COMBO_SD_C"


def test_equal_low_sweep_must_close_back_above_the_cluster():
    from app.research.sd_structure.liquidity import completed_equal_sweep

    candles = [_bar(i, 101, 102, 100.4, 101) for i in range(20)]
    candles[12] = _bar(12, 101, 101.2, 99.5, 100.6)
    swings = [
        _swing("LOW", 100.0, 2, "LL"),
        _swing("LOW", 100.1, 8, "EQL"),
    ]
    missing = completed_equal_sweep(
        candles, swings, direction="LONG", before_index=16, right_bars=2
    )
    candles[12] = _bar(12, 101, 101.2, 100.2, 101)
    still_inside = completed_equal_sweep(
        candles, swings, direction="LONG", before_index=16, right_bars=2
    )
    candles[12] = _bar(12, 101, 101.2, 99.4, 99.8)
    closed_through = completed_equal_sweep(
        candles, swings, direction="LONG", before_index=16, right_bars=2
    )
    assert missing is not None
    assert missing["event"] == "EQL_SWEEP"
    assert missing["sweep_index"] == 12
    assert missing["level"] == 100.0
    assert still_inside is None
    assert closed_through is None


def test_sweep_on_the_confirmation_bar_does_not_count():
    from app.research.sd_structure.liquidity import completed_equal_sweep

    candles = [_bar(i, 104, 105, 103, 104) for i in range(16)]
    candles[14] = _bar(14, 104, 112, 103.5, 106)
    swings = [
        _swing("HIGH", 110.0, 3, "LH"),
        _swing("HIGH", 110.1, 9, "EQH"),
    ]
    before_choch = completed_equal_sweep(
        candles, swings, direction="SHORT", before_index=14, right_bars=2
    )
    after_confirmation = completed_equal_sweep(
        candles, swings, direction="SHORT", before_index=15, right_bars=2
    )
    assert before_choch is None
    assert after_confirmation is not None
    assert after_confirmation["event"] == "EQH_SWEEP"


def test_day_structure_covers_bull_bear_sideways_and_reversal():
    candles = [_bar(i, 100, 100 + i, 100 - i, 100) for i in range(8)]
    bull = [
        _swing("HIGH", 100, 1, "LH"),
        _swing("LOW", 90, 2, "LL"),
        _swing("HIGH", 110, 3, "HH"),
        _swing("LOW", 95, 4, "HL"),
    ]
    assert classify_day_structure(bull, candles, 5, right_bars=0) == "upper_trend"
    candles[6] = _bar(6, 100, 101, 80, 90)
    assert classify_day_structure(bull, candles, 6, right_bars=0) == "reversal"
    bear = [
        _swing("HIGH", 110, 1, "HH"),
        _swing("LOW", 100, 2, "HL"),
        _swing("HIGH", 105, 3, "LH"),
        _swing("LOW", 90, 4, "LL"),
    ]
    candles[5] = _bar(5, 100, 101, 99, 100)
    assert classify_day_structure(bear, candles, 5, right_bars=0) == "short_trend"
    candles[5] = _bar(5, 100, 112, 99, 111)
    assert classify_day_structure(bear, candles, 5, right_bars=0) == "reversal"
    assert classify_day_structure([_swing("HIGH", 10, 1, "HH")], candles, 5, right_bars=0) == "sideways"


def test_liquidity_variant_is_bos_sequence_plus_a_sweep_gate():
    from app.research.sd_structure.variants import variant_for

    base = variant_for("COMBO_SD_B")
    liquidity = variant_for("COMBO_SD_LIQ")
    combo = get_combination("COMBO_SD_LIQ")
    assert base.require_liquidity_sweep is False
    assert liquidity.require_choch is True
    assert liquidity.require_bos is True
    assert liquidity.require_liquidity_sweep is True
    assert liquidity.volume_mode == base.volume_mode
    assert combo is not None
    assert "Equal highs or equal lows within 0.2%" in combo.conditions
