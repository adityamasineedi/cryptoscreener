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
