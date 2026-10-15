"""Explicit structure labels stay causal and do not call every non-trend sideways."""

from datetime import datetime, timedelta, timezone

from app.research.sd_structure.explicit_structure import audit_candles, label_structure_bars
from app.signals.schemas import SwingRecord
from app.signals.swing_detector import detect_swings


def _bar(index: int, high: float, low: float, close: float):
    return {
        "time": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * index),
        "open": close,
        "high": high,
        "low": low,
        "close": close,
    }


def _swing(kind: str, price: float, bar: int) -> SwingRecord:
    pivot = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * bar)
    return SwingRecord(
        symbol="TEST",
        timeframe="15m",
        swing_type=kind,
        price=price,
        timestamp=pivot,
        bar_index=bar,
        strength=1.0,
        confirmed_at=pivot,
    )


def _book() -> list[dict]:
    candles = [_bar(i, 20 + (i % 3), 10, 15) for i in range(16)]
    candles[8] = _bar(8, 20, 10, 16)
    candles[9] = _bar(9, 20, 8, 8.5)
    return candles


def test_too_few_swings_are_insufficient_not_sideways():
    labels = label_structure_bars(
        _book(),
        [_swing("HIGH", 12, 2), _swing("LOW", 9, 4)],
        right_bars=0,
        timeframe="15m",
    )
    assert labels[8]["state"] == "insufficient_data"
    assert labels[8]["reason"] == "too_few_confirmed_swings"


def test_intact_higher_low_is_bullish_and_a_break_is_only_a_choch():
    swings = [
        _swing("HIGH", 10, 2),
        _swing("LOW", 8, 4),
        _swing("HIGH", 12, 6),
        _swing("LOW", 9, 8),
    ]
    labels = label_structure_bars(_book(), swings, right_bars=0, timeframe="15m", max_age_bars=50)
    assert labels[8]["state"] == "bullish"
    assert labels[8]["supporting_swing"]["price"] == 9
    assert labels[8]["supporting_swing"]["confirmed_at"] is not None
    assert labels[9]["state"] == "transition"
    assert labels[9]["last_event"] == "choch"
    assert labels[9]["reason"] == "support_broken"


def test_opposite_pair_after_a_choch_is_the_reversal_not_the_choch_itself():
    candles = _book()
    candles[11] = _bar(11, 14, 7, 11)
    swings = [
        _swing("HIGH", 10, 2),
        _swing("LOW", 8, 4),
        _swing("HIGH", 12, 6),
        _swing("LOW", 9, 8),
        _swing("HIGH", 11, 10),
        _swing("LOW", 7, 11),
    ]
    labels = label_structure_bars(candles, swings, right_bars=0, timeframe="15m", max_age_bars=50)
    assert labels[10]["state"] == "transition"
    assert labels[11]["state"] == "bearish"
    assert labels[11]["last_event"] == "reversal_confirmed"


def test_reversal_rejects_a_pair_that_was_already_confirmed_before_the_break():
    candles = _book()
    candles[10] = _bar(10, 14, 7, 11)
    swings = [
        _swing("HIGH", 10, 2),
        _swing("LOW", 8, 4),
        _swing("HIGH", 12, 6),
        _swing("LOW", 9, 8),
        _swing("HIGH", 11, 10),
    ]
    labels = label_structure_bars(candles, swings, right_bars=0, timeframe="15m", max_age_bars=50)
    assert labels[9]["state"] == "transition"
    assert labels[9]["last_event"] == "choch"
    assert labels[10]["state"] == "transition"
    assert labels[10]["last_event"] == "choch"


def test_a_pivot_printed_before_the_break_cannot_complete_the_reversal():
    candles = [_bar(i, 20, 10, 16) for i in range(16)]
    candles[10] = _bar(10, 20, 8, 8.5)
    swings = [
        _swing("HIGH", 10, 2),
        _swing("LOW", 8, 4),
        _swing("HIGH", 12, 6),
        _swing("LOW", 9, 6),
        _swing("LOW", 7, 9),
        _swing("HIGH", 11, 12),
    ]
    labels = label_structure_bars(candles, swings, right_bars=2, timeframe="15m", max_age_bars=50)
    assert labels[10]["state"] == "transition"
    assert labels[10]["last_event"] == "choch"
    assert labels[14]["state"] == "transition"
    assert labels[14]["last_event"] == "choch"


def test_a_gap_ends_the_prior_structure():
    candles = [_bar(i, 20, 10, 16) for i in range(12)]
    candles[10]["time"] = candles[9]["time"] + timedelta(minutes=45)
    candles[11]["time"] = candles[10]["time"] + timedelta(minutes=15)
    swings = [
        _swing("HIGH", 10, 2),
        _swing("LOW", 8, 4),
        _swing("HIGH", 12, 6),
        _swing("LOW", 9, 8),
    ]
    labels = label_structure_bars(candles, swings, right_bars=0, timeframe="15m", max_age_bars=50)
    assert labels[8]["state"] == "bullish"
    assert labels[10]["state"] == "insufficient_data"
    assert labels[10]["reason"] == "gap_in_history"
    assert labels[11]["state"] == "insufficient_data"


def test_mixed_pair_is_transition_not_sideways():
    swings = [
        _swing("HIGH", 10, 2),
        _swing("LOW", 9, 4),
        _swing("HIGH", 12, 6),
        _swing("LOW", 7, 8),
    ]
    labels = label_structure_bars(_book(), swings, right_bars=0, timeframe="15m", max_age_bars=50)
    assert labels[8]["state"] == "transition"
    assert labels[8]["reason"] == "mixed_structure"


def test_stale_support_expires_and_does_not_restore_until_a_new_low():
    candles = [_bar(i, 20, 10, 16) for i in range(14)]
    swings = [
        _swing("HIGH", 10, 2),
        _swing("LOW", 8, 4),
        _swing("HIGH", 12, 6),
        _swing("LOW", 9, 8),
    ]
    labels = label_structure_bars(candles, swings, right_bars=0, timeframe="15m", max_age_bars=2)
    assert labels[8]["state"] == "bullish"
    assert labels[11]["state"] == "transition"
    assert labels[11]["reason"] == "support_expired"
    assert labels[12]["state"] == "transition"


def test_prefix_labels_match_the_batch_and_later_bars_do_not_rewrite_them():
    candles = [_bar(i, 30 + i, 5, 15) for i in range(24)]
    for index, high in ((3, 40), (8, 48), (14, 44)):
        candles[index] = _bar(index, high, 12, high - 2)
    for index, low in ((5, 8), (11, 14), (16, 10)):
        candles[index] = _bar(index, 20, low, low + 2)
    full = detect_swings(candles, left=1, right=1, symbol="TEST", timeframe="15m", as_of_index=len(candles) - 1)
    batch = label_structure_bars(candles, full, right_bars=1, timeframe="15m", max_age_bars=50)
    for end in range(6, len(candles)):
        prefix_swings = detect_swings(
            candles[: end + 1], left=1, right=1, symbol="TEST", timeframe="15m", as_of_index=end
        )
        prefix = label_structure_bars(
            candles[: end + 1], prefix_swings, right_bars=1, timeframe="15m", max_age_bars=50
        )
        assert prefix[-1]["state"] == batch[end]["state"]
        assert prefix[-1]["reason"] == batch[end]["reason"]
        assert [row["state"] for row in prefix] == [row["state"] for row in batch[: end + 1]]


def test_same_candle_reversal_is_flagged_on_baseline_and_rejected_by_the_candidate():
    candles = [_bar(i, 20, 10, 12) for i in range(8)]
    candles[5] = _bar(5, 20, 10, 11)
    swings = [
        _swing("HIGH", 12, 1),
        _swing("LOW", 9, 2),
        _swing("HIGH", 10, 3),
        _swing("LOW", 7, 4),
        _swing("HIGH", 13, 6),
        _swing("LOW", 8, 6),
    ]
    baseline = label_structure_bars(candles, swings, right_bars=0, timeframe="15m", max_age_bars=50)
    candidate = label_structure_bars(
        candles, swings, right_bars=0, timeframe="15m", max_age_bars=50, distinct_reversal_pivots=True
    )
    assert baseline[6]["state"] == "bullish"
    assert "same_candle_reversal_pivots" in baseline[6]["ambiguity_flags"]
    assert candidate[6]["state"] == "transition"
    assert "candidate_rejected_same_candle_reversal" in candidate[6]["ambiguity_flags"]
    assert baseline[4]["state"] == candidate[4]["state"] == "bearish"


def test_explanation_names_a_pending_leg_without_inventing_its_price():
    candles = [_bar(i, 20, 10, 12) for i in range(12)]
    candles[5] = _bar(5, 20, 10, 11)
    swings = [
        _swing("HIGH", 12, 1),
        _swing("LOW", 9, 2),
        _swing("HIGH", 10, 3),
        _swing("LOW", 7, 4),
    ]
    labels = label_structure_bars(candles, swings, right_bars=0, timeframe="15m", max_age_bars=50)
    text = labels[11]["explanation"]
    assert text == (
        "Transition from bearish · upward CHoCH · higher low pending · higher high pending · 6 bars since break."
    )
    assert labels[11]["reversal_evidence"]["higher_high"] == "pending"
    assert "price" not in labels[11]["reversal_evidence"]


def test_known_at_is_the_candle_close_and_a_later_pivot_is_not_shown_early():
    candles = [_bar(i, 20, 10, 15) for i in range(8)]
    swings = [
        _swing("HIGH", 12, 1),
        _swing("LOW", 9, 2),
        _swing("HIGH", 14, 6),
        _swing("LOW", 8, 6),
    ]
    labels = label_structure_bars(candles, swings, right_bars=2, timeframe="15m", max_age_bars=50)
    opened = candles[3]["time"]
    assert labels[3]["known_at"] == (opened + timedelta(minutes=15)).isoformat()
    assert labels[3]["known_at"] != labels[3]["candle_open_time"]
    assert labels[4]["pair"] is None or labels[4]["pair"]["latest_high"]["pivot_index"] != 6


def test_every_bar_has_one_state_and_expiry_is_marked_on_a_synthetic_book():
    candles = [_bar(i, 20, 10, 16) for i in range(14)]
    swings = [
        _swing("HIGH", 10, 2),
        _swing("LOW", 8, 4),
        _swing("HIGH", 12, 6),
        _swing("LOW", 9, 8),
    ]
    labels = label_structure_bars(candles, swings, right_bars=0, timeframe="15m", max_age_bars=2)
    assert {row["state"] for row in labels} <= {
        "bullish",
        "bearish",
        "transition",
        "range",
        "insufficient_data",
    }
    assert all(isinstance(row["state"], str) and row["state"] for row in labels)
    assert labels[11]["expiry_status"] == "expired"


def test_structure_labels_are_not_wired_into_trade_evaluation():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "research"
    trade_files = [
        root / "combination_engine.py",
        root / "combination_backtest.py",
        root / "sd_structure" / "evaluate.py",
    ]
    for path in trade_files:
        text = path.read_text(encoding="utf-8")
        assert "label_structure_bars" not in text
        assert "distinct_reversal_pivots" not in text


def test_atr_series_matches_the_per_bar_value():
    from app.research.sd_structure.explicit_structure import _atr, _atr_series

    candles = [_bar(i, 20 + (i % 5), 10, 14) for i in range(40)]
    series = _atr_series(candles, 14)
    for index in range(len(candles)):
        assert series[index] == _atr(candles, index, 14)


def test_scored_holdout_stops_before_each_symbols_first_scored_bar():
    from app.research.sd_structure.labels_api import assert_scored_sets_disjoint

    def row(opened: str, initialization: str = "ready") -> dict:
        return {"candle_open_time": opened, "initialization": initialization}

    recent = [row("2026-08-10T20:45:00+00:00", "warming_up"), row("2026-08-10T21:00:00+00:00", "warming_up")]
    recent.append(row("2026-08-11T04:00:00+00:00"))
    holdout = [row("2026-06-12T00:00:00+00:00", "warming_up"), row("2026-08-10T23:45:00+00:00")]
    holdout.append(row("2026-08-11T04:00:00+00:00"))
    separation = assert_scored_sets_disjoint(recent, holdout)
    assert separation["scored_intersection"] == 0
    assert separation["recent_first_scored_open"] == "2026-08-11T04:00:00+00:00"
    assert separation["holdout_scored_bars"] == 1


def test_display_suffix_keeps_history_that_a_short_window_drops():
    from app.research.sd_structure.labels_api import label_loaded_series

    candles = [_bar(i, 10, 9, 9.5) for i in range(40)]
    candles[5] = _bar(5, 20, 9, 15)
    candles[9] = _bar(9, 11, 5, 8)
    candles[14] = _bar(14, 22, 9, 16)
    candles[18] = _bar(18, 12, 7, 10)
    full = label_loaded_series("TESTUSDT", "15m", candles)
    short = label_loaded_series("TESTUSDT", "15m", candles[-8:])
    displayed = label_loaded_series("TESTUSDT", "15m", candles, display_bars=8)
    assert short["latest"]["state"] == "insufficient_data"
    assert displayed["latest"]["state"] == full["latest"]["state"]
    assert displayed["latest"]["state"] != short["latest"]["state"]
    assert displayed["coverage"]["calculation_bars"] == 40
    assert displayed["denominator"] == 8
    assert displayed["coverage"]["start_open"] != displayed["coverage"]["calculation_start_open"]


def test_candle_audit_counts_a_gap_and_a_duplicate():
    candles = [_bar(i, 2, 1, 1.5) for i in range(4)]
    candles.append(_bar(6, 2, 1, 1.5))
    candles.append(dict(candles[-1]))
    report = audit_candles(candles, "15m")
    assert report["bars"] == 6
    assert report["gap_count"] == 1
    assert report["missing_bars"] == 2
    assert report["duplicates"] == 1
