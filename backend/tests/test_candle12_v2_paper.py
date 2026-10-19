"""Virtual paper book for frozen CANDLE12_V2 BTCUSDT 1h LONG."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.candle12_v2_paper import format_signal, paper_step, sync_candles


def _trade(idx: int, outcome: str, when: str) -> dict:
    return {
        "direction": "LONG",
        "entry_index": idx,
        "signal_time": when,
        "entry_price": 100.0,
        "stop_price": 90.0,
        "tp1": 120.0,
        "exit_price": None if outcome == "OPEN" else 90.0,
        "exit_time": None if outcome == "OPEN" else when,
        "exit_index": None if outcome == "OPEN" else idx + 3,
        "outcome": outcome,
        "holding_bars": None if outcome == "OPEN" else 3,
    }


def test_first_read_does_not_book_old_closed_trades_and_adopts_an_open_one():
    state = paper_step(
        None,
        [
            _trade(1, "SL", "2026-10-01T00:00:00+00:00"),
            {**_trade(2, "OPEN", "2026-10-17T00:00:00+00:00"), "direction": "SHORT"},
            _trade(3, "OPEN", "2026-10-17T12:00:00+00:00"),
        ],
        last_bar_time="2026-10-18T00:00:00+00:00",
    )
    assert state["closed"] == []
    assert state["live_orders"] is False
    assert state["open"]["entry_index"] == 3
    assert state["open"]["direction"] == "LONG"
    assert state["watermark"] == "2026-10-18T00:00:00+00:00"


def test_a_new_long_after_the_watermark_is_booked_and_then_closed():
    state = paper_step(None, [], last_bar_time="2026-10-18T00:00:00+00:00")
    state = paper_step(
        state,
        [_trade(9, "OPEN", "2026-10-18T01:00:00+00:00")],
        last_bar_time="2026-10-18T01:00:00+00:00",
    )
    assert state["open"]["entry_index"] == 9
    state = paper_step(
        state,
        [_trade(9, "SL", "2026-10-18T01:00:00+00:00")],
        last_bar_time="2026-10-18T04:00:00+00:00",
    )
    assert state["open"] is None
    assert state["closed"][0]["outcome"] == "SL"
    assert state["closed"][0]["net_pnl_usd"] is not None
    assert state["closed"][0]["live_orders"] is False


def test_new_long_and_its_exit_are_the_only_telegram_signals():
    state = paper_step(
        None,
        [_trade(1, "SL", "2026-10-01T00:00:00+00:00")],
        last_bar_time="2026-10-18T00:00:00+00:00",
    )
    assert state["_alerts"] == []
    state = paper_step(
        state,
        [_trade(9, "OPEN", "2026-10-18T01:00:00+00:00")],
        last_bar_time="2026-10-18T01:00:00+00:00",
    )
    assert [a["kind"] for a in state["_alerts"]] == ["entry"]
    text = format_signal(state["_alerts"][0])
    assert "CANDLE12_V2 PAPER LONG BTCUSDT 1h" in text
    assert "No exchange order" in text
    state = paper_step(
        state,
        [_trade(9, "TP1", "2026-10-18T01:00:00+00:00")],
        last_bar_time="2026-10-18T05:00:00+00:00",
    )
    assert [a["kind"] for a in state["_alerts"]] == ["exit"]
    assert "PAPER EXIT" in format_signal(state["_alerts"][0])


def test_sync_refuses_any_other_symbol_or_timeframe():
    with pytest.raises(ValueError):
        sync_candles([{"time": "t", "open": 1, "high": 1, "low": 1, "close": 1}] * 60, symbol="ETHUSDT")
    with pytest.raises(ValueError):
        sync_candles([{"time": "t", "open": 1, "high": 1, "low": 1, "close": 1}] * 60, timeframe="5m")


def test_state_file_stays_virtual(tmp_path: Path):
    path = tmp_path / "book.json"
    candles = []
    for i in range(40):
        candles.append(
            {"time": f"2024-01-01T{i:02d}:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        )
    out = sync_candles(candles, path=path)
    assert out["status"] == "WAITING_FOR_BARS"
    assert out["live_orders"] is False
    assert not path.exists()


# --- idempotency: one booking per (strategy, symbol, side, signal-bar close) -----------------

from app.services.candle12_v2_paper import load_state, migrate_state, save_state, trade_key  # noqa: E402

WHEN = "2026-10-20T05:00:00+00:00"


def _started() -> dict:
    return paper_step(None, [], last_bar_time="2026-10-20T00:00:00+00:00")


def test_same_closed_bar_processed_twice_books_one_trade():
    # The second read is the same closed bar seen through a window that has
    # moved by one (entry_index 400 -> 399), as the rolling store does hourly.
    state = paper_step(_started(), [_trade(400, "TP1", WHEN)], last_bar_time="2026-10-20T09:00:00+00:00")
    state = paper_step(state, [_trade(399, "TP1", WHEN)], last_bar_time="2026-10-20T09:00:00+00:00")
    assert len(state["closed"]) == 1
    assert state["_alerts"] == []
    assert state["closed"][0]["key"] == f"CANDLE12_V2|BTCUSDT|LONG|{WHEN}"


def test_rolling_window_over_many_hours_books_one_trade():
    state = _started()
    exits = 0
    for shift in range(300):
        state = paper_step(state, [_trade(450 - shift, "TP1", WHEN)], last_bar_time=WHEN)
        exits += sum(1 for a in state["_alerts"] if a["kind"] == "exit")
    assert len(state["closed"]) == 1 and exits == 1


def test_open_then_closed_under_a_shifted_index_clears_the_open():
    state = paper_step(_started(), [_trade(400, "OPEN", WHEN)], last_bar_time=WHEN)
    state = paper_step(state, [_trade(399, "OPEN", WHEN)], last_bar_time="2026-10-20T06:00:00+00:00")
    assert [a["kind"] for a in state["_alerts"]] == []
    state = paper_step(state, [_trade(398, "SL", WHEN)], last_bar_time="2026-10-20T07:00:00+00:00")
    assert state["open"] is None
    assert len(state["closed"]) == 1
    assert [a["kind"] for a in state["_alerts"]] == ["exit"]


def test_new_trade_on_a_previously_used_index_is_still_booked():
    state = paper_step(_started(), [_trade(400, "SL", WHEN)], last_bar_time=WHEN)
    later = "2026-10-20T08:00:00+00:00"
    state = paper_step(state, [_trade(400, "SL", later)], last_bar_time=later)
    assert [r["entry_time"] for r in state["closed"]] == [WHEN, later]


def test_restart_reload_with_a_different_window_base_books_nothing_new(tmp_path: Path):
    path = tmp_path / "book.json"
    state = paper_step(_started(), [_trade(400, "TP1", WHEN)], last_bar_time=WHEN)
    state.pop("_alerts")
    save_state(state, path)
    state = paper_step(load_state(path), [_trade(393, "TP1", WHEN)], last_bar_time=WHEN)
    assert len(state["closed"]) == 1 and state["_alerts"] == []


def test_v1_state_is_marked_invalid_and_restarts_at_the_load_bar():
    old = {
        "watermark": "2026-10-17T18:00:00+00:00",
        "seen_entry_index": [143, 144],
        "open": {"entry_index": 7, "entry_time": "2026-10-18T01:00:00+00:00"},
        "closed": [{"entry_index": 5, "net_pnl_usd": 40.0}, {"entry_index": 6, "net_pnl_usd": 40.0}],
    }
    state = migrate_state(old, last_bar_time="2026-10-20T00:00:00+00:00", now="2026-10-20T00:30:00+00:00")
    assert state["version"] == 2 and state["closed"] == [] and state["open"] is None
    assert [r["status"] for r in state["invalid"]] == ["INVALID"] * 3
    assert state["watermark"] == "2026-10-20T00:00:00+00:00"
    assert "seen_entry_index" not in state
    # A trade from before the restart is not booked again.
    state = paper_step(state, [_trade(5, "SL", "2026-10-18T01:00:00+00:00")], last_bar_time=WHEN)
    assert state["closed"] == []


def test_trade_key_is_utc_time_not_string_form():
    a = trade_key({"signal_time": "2026-10-20T05:00:00Z"})
    b = trade_key({"signal_time": "2026-10-20T10:30:00+05:30"})
    assert a == b == f"CANDLE12_V2|BTCUSDT|LONG|{WHEN}"
