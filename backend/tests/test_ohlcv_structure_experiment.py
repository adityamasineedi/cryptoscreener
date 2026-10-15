"""Deterministic checks for the isolated OHLCV structure experiment."""

from datetime import datetime, timedelta, timezone

from app.research.ohlcv_structure_experiment.engine import (
    BreakEvent,
    qualify,
    run_variant,
    scan_breaks,
    simulate_fill,
)
from app.research.ohlcv_structure_experiment.metrics import assign_window, summarize
from app.signals.swing_detector import detect_swings


def _bar(index: int, open_, high, low, close) -> dict:
    stamp = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return {
        "time": stamp.isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1.0,
    }


def _event(direction: str = "LONG", break_index: int = 5, level: float = 100, stop: float = 90) -> BreakEvent:
    return BreakEvent(
        event_id=f"BTCUSDT:{direction}:1:{level}",
        symbol="BTCUSDT",
        direction=direction,
        call_type="bos_with_trend",
        pivot_index=1,
        pivot_at="bar:1",
        confirmed_index=4,
        confirmed_at="bar:4",
        break_index=break_index,
        break_at=f"bar:{break_index}",
        level=level,
        stop=stop,
        atr=2.0,
        confirmation_delay_bars=3,
        bars_from_confirmation_to_break=break_index - 4,
    )


def _flat(count: int, price: float = 100) -> list[dict]:
    return [_bar(i, price, price + 0.2, price - 0.2, price) for i in range(count)]


def test_swing_is_unavailable_until_right_bars_close():
    candles = _flat(30, 100)
    candles[10] = _bar(10, 100, 130, 99, 101)
    early = detect_swings(candles, left=3, right=3, as_of_index=12)
    late = detect_swings(candles, left=3, right=3, as_of_index=13)
    later = detect_swings(candles, left=3, right=3, as_of_index=25)
    assert all(swing.bar_index != 10 for swing in early)
    assert any(swing.bar_index == 10 and swing.swing_type == "HIGH" for swing in late)
    early_ids = {(swing.swing_type, swing.bar_index) for swing in late}
    future_ids = {
        (swing.swing_type, swing.bar_index)
        for swing in later
        if swing.bar_index + 3 <= 13
    }
    assert early_ids == future_ids


def test_next_bar_entry_uses_open_and_frozen_stop():
    candles = _flat(40, 110)
    candles[5] = _bar(5, 100, 112, 99, 111)
    candles[6] = _bar(6, 108, 109, 107, 108)
    record = qualify(_event(), candles, "A_corrected_baseline")
    assert record.entry_index == 6
    assert record.qualified_at == "bar:5"
    filled = simulate_fill(record, candles)
    assert filled.status == "filled"
    assert filled.entry_price == 108 * 1.0002
    assert filled.stop_price == 90
    assert filled.entry_at != "bar:1"


def test_acceptance_expires_or_waits_for_the_next_close():
    fail = _flat(10, 100)
    fail[6] = _bar(6, 100, 101, 98, 99)
    expired = qualify(_event(), fail, "B_acceptance")
    assert expired.status == "expired"
    hold = _flat(10, 100)
    hold[6] = _bar(6, 101, 106, 100.5, 105)
    qualified = qualify(_event(), hold, "B_acceptance")
    assert qualified.status == "entry_qualified"
    assert qualified.entry_index == 7


def test_retest_qualifies_invalidates_and_expires():
    qualify_bars = _flat(12, 105)
    qualify_bars[6] = _bar(6, 104, 106, 99, 104)
    qualified = qualify(_event(), qualify_bars, "C_retest")
    assert qualified.status == "entry_qualified"
    assert qualified.entry_index == 7

    cancel = _flat(12, 105)
    cancel[6] = _bar(6, 104, 106, 98, 99)
    invalidated = qualify(_event(), cancel, "C_retest")
    assert invalidated.status == "invalidated"

    miss = _flat(12, 105)
    expired = qualify(_event(), miss, "C_retest")
    assert expired.status == "expired"
    assert expired.lifecycle_end == 8


def test_retest_equality_does_not_qualify_or_cancel():
    bars = _flat(12, 105)
    bars[6] = _bar(6, 101, 102, 99, 100)
    bars[7] = _bar(7, 101, 102, 100, 101)
    record = qualify(_event(), bars, "C_retest")
    assert record.status == "entry_qualified"
    assert record.qualified_index == 7


def test_short_retest_mirrors_long_rules():
    bars = _flat(12, 95)
    bars[6] = _bar(6, 96, 101, 94, 96)
    record = qualify(_event("SHORT", level=100, stop=110), bars, "C_retest")
    assert record.status == "entry_qualified"
    cancel = _flat(12, 95)
    cancel[6] = _bar(6, 99, 102, 98, 101)
    assert qualify(_event("SHORT", level=100, stop=110), cancel, "C_retest").status == "invalidated"


def test_same_bar_stop_and_target_is_a_stop_and_ambiguous():
    candles = _flat(30, 100)
    candles[6] = _bar(6, 100, 130, 80, 100)
    record = qualify(_event(stop=95), candles, "A_corrected_baseline")
    filled = simulate_fill(record, candles)
    assert filled.outcome == "stop"
    assert filled.ambiguous is True
    assert filled.exit_price == 95


def test_gap_through_stop_fills_at_the_open():
    candles = _flat(30, 100)
    candles[6] = _bar(6, 100, 101, 99, 100)
    candles[7] = _bar(7, 80, 81, 79, 80)
    record = qualify(_event(stop=90), candles, "A_corrected_baseline")
    filled = simulate_fill(record, candles)
    assert filled.outcome == "stop"
    assert filled.exit_price == 80
    assert filled.ambiguous is False
    assert filled.net_r is not None and filled.net_r < -1


def test_entry_gap_through_stop_is_skipped():
    candles = _flat(30, 80)
    candles[6] = _bar(6, 80, 81, 79, 80)
    record = qualify(_event(stop=90), candles, "A_corrected_baseline")
    filled = simulate_fill(record, candles)
    assert filled.status == "skipped_invalid_entry"
    assert filled.net_r is None


def test_timeout_includes_costs_and_is_not_zero():
    candles = _flat(30, 100)
    candles[6] = _bar(6, 100, 100.2, 99.8, 100)
    record = qualify(_event(stop=90), candles, "A_corrected_baseline")
    filled = simulate_fill(record, candles)
    assert filled.outcome == "timeout"
    assert filled.holding_bars == 20
    assert filled.net_r is not None and filled.net_r != 0
    assert filled.fee_r and filled.fee_r > 0


def test_duplicate_pivot_is_not_scanned_twice_and_overlap_skips():
    candles = _flat(80, 100)
    for index in range(10, 70):
        candles[index] = _bar(index, 100, 101, 99, 100 + (index - 10) * 0.05)
    candles[20] = _bar(20, 100, 110, 99, 109)
    candles[21] = _bar(21, 108, 112, 107, 111)
    events = scan_breaks(candles, symbol="BTCUSDT", right=3)
    ids = [event.event_id for event in events]
    assert len(ids) == len(set(ids))
    records = run_variant(candles, events, "A_corrected_baseline")
    fills = [row for row in records if row.status == "filled"]
    skipped = [row for row in records if row.status == "skipped_overlap"]
    if len(fills) > 1:
        for earlier, later in zip(fills, fills[1:]):
            assert later.entry_index is not None and earlier.exit_index is not None
            assert later.entry_index > earlier.exit_index
    assert all(row.net_r is None for row in skipped)


def test_boundary_events_are_excluded_from_both_windows():
    event = _event(break_index=10)
    record = qualify(event, _flat(40), "A_corrected_baseline")
    record.lifecycle_end = 30
    assert assign_window(record, 20, 40) == "boundary_excluded"
    record.lifecycle_end = 15
    assert assign_window(record, 20, 40) == "development"
    event_late = _event(break_index=25)
    late = qualify(event_late, _flat(40), "A_corrected_baseline")
    late.event.break_index = 25
    late.lifecycle_end = 35
    assert assign_window(late, 20, 40) == "later_slice"


def test_incomplete_window_is_not_a_zero_return_fill():
    candles = _flat(8, 100)
    record = qualify(_event(), candles, "A_corrected_baseline")
    filled = simulate_fill(record, candles)
    assert filled.status == "incomplete_window"
    summary = summarize([filled], bar_count=8)
    assert summary["fills"] == 0
    assert summary["net_expectancy_r"] is None
    assert summary["incomplete_windows"] == 1
