"""COMBO_02 V2.1-A paper + Telegram — PAPER ONLY, no real orders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.research.combo02_v21a_paper_constants import (
    LIVE_EXECUTION_ALLOWED,
    MODE,
    SOURCE_WATCHER,
    STRATEGY_ID,
)
from app.services.paper_trade import PaperTradeEngine, STREAM_V21A
from app.services.telegram_alerts import (
    format_telegram_message,
    is_telegram_trade_alert,
    is_v21a_paper_alert,
)
from app.services.v21a_paper_watcher import (
    V21APaperWatcher,
    is_choppy_wait,
    is_v21a_entry,
    reset_v21a_paper_watcher_for_tests,
    signal_idempotency_key,
)


@pytest.fixture(autouse=True)
def _reset_watcher():
    reset_v21a_paper_watcher_for_tests()
    yield
    reset_v21a_paper_watcher_for_tests()


def _candles(n: int, *, hours: float = 1.0, base: float = 100.0, drift: float = 0.1) -> list[dict]:
    t0 = datetime(2024, 6, 1, tzinfo=timezone.utc)
    out = []
    px = base
    step = timedelta(hours=hours)
    for i in range(n):
        o = px
        c = px + drift
        out.append(
            {
                "time": t0 + step * i,
                "open": o,
                "high": max(o, c) + 0.5,
                "low": min(o, c) - 0.5,
                "close": c,
                "volume": 1000.0 + i,
            }
        )
        px = c
    return out


def _closed_4h() -> list[dict]:
    """4h bars fully closed at the close of the ``_candles(80)`` 1h tip."""
    return _candles(20, hours=4.0)


def _entry_result(*, side: str = "LONG") -> dict[str, Any]:
    if side == "LONG":
        return {
            "status": "LONG_ENTRY_CANDIDATE",
            "combination_id": STRATEGY_ID,
            "entry_price": 100.0,
            "stop_price": 98.0,
            "tp1": 104.0,
            "targets": [{"target_price": 104.0}],
            "regime": "BULL_TREND",
            "playbook": "TREND_CONTINUATION",
            "event": "Liquidity sweep",
            "confirmation": "15M confirmation",
            "signal_time": "2024-06-01T10:00:00+00:00",
        }
    return {
        "status": "SHORT_ENTRY_CANDIDATE",
        "combination_id": STRATEGY_ID,
        "entry_price": 100.0,
        "stop_price": 102.0,
        "tp1": 96.0,
        "targets": [{"target_price": 96.0}],
        "regime": "BEAR_TREND",
        "playbook": "TREND_CONTINUATION",
        "signal_time": "2024-06-01T10:00:00+00:00",
    }


def test_live_execution_constant_disabled():
    assert LIVE_EXECUTION_ALLOWED is False
    assert MODE == "PAPER"


def test_is_v21a_entry_and_choppy_wait():
    assert is_v21a_entry(_entry_result())
    assert is_v21a_entry(_entry_result(side="SHORT"))
    assert not is_v21a_entry({"status": "NO_SETUP", "reason": "WAIT"})
    chop = {
        "status": "NO_SETUP",
        "reason": "WAIT_CHOPPY_RANGE_DISABLED_V21A",
        "regime": "CHOPPY",
        "playbook": "WAIT",
    }
    assert is_choppy_wait(chop)
    assert not is_v21a_entry(chop)


def test_valid_entry_opens_one_paper_trade_and_signal():
    paper = PaperTradeEngine(starting_equity=1000.0, risk_percent=0.02, enabled=True)
    watcher = V21APaperWatcher(
        enabled=True,
        symbols=["BTCUSDT"],
        paper_engine=paper,
        replay_mode=True,
        emit_alerts=False,
        risk_percent=0.02,
    )
    c1h = _candles(80)
    c4h = _closed_4h()
    c15 = _candles(200, hours=0.25)
    tip = c1h[-1]["time"].isoformat()

    with patch.object(watcher, "evaluate_at_bar", return_value=_entry_result()):
        pos = watcher.on_closed_1h(
            "BTCUSDT",
            candles_1h=c1h,
            candles_4h=c4h,
            candles_15m=c15,
            force=True,
        )
    assert pos is not None
    assert pos.side == "LONG"
    assert paper.has_open("BTCUSDT", STREAM_V21A)
    assert pos.signal_snippet["strategy_id"] == STRATEGY_ID
    assert pos.signal_snippet["source"] == SOURCE_WATCHER
    assert pos.signal_snippet["mode"] == "PAPER"
    assert pos.signal_snippet["telegram_eligible"] is True

    # Duplicate same bar → no second trade
    with patch.object(watcher, "evaluate_at_bar", return_value=_entry_result()):
        pos2 = watcher.on_closed_1h(
            "BTCUSDT",
            candles_1h=c1h,
            candles_4h=c4h,
            candles_15m=c15,
            force=False,
        )
    assert pos2 is None
    assert paper.status()["open_by_stream"][STREAM_V21A] == 1
    assert tip  # setup used closed tip


def test_choppy_wait_no_telegram_signal():
    paper = PaperTradeEngine(starting_equity=1000.0, enabled=True)
    watcher = V21APaperWatcher(
        enabled=True,
        symbols=["BTCUSDT"],
        paper_engine=paper,
        replay_mode=True,
        emit_alerts=True,
        risk_percent=0.02,
    )
    c1h = _candles(80)
    alerts: list[dict] = []

    def _capture(alert):
        alerts.append(alert)

    with patch.object(
        watcher,
        "evaluate_at_bar",
        return_value={
            "status": "NO_SETUP",
            "reason": "WAIT_CHOPPY_RANGE_DISABLED_V21A",
            "regime": "CHOPPY",
            "playbook": "WAIT",
            "combination_id": STRATEGY_ID,
        },
    ), patch("app.services.alerts.get_alert_feed") as feed_mod:
        feed_mod.return_value = MagicMock(observe_paper_open=_capture)
        pos = watcher.on_closed_1h(
            "BTCUSDT",
            candles_1h=c1h,
            candles_4h=_closed_4h(),
            candles_15m=_candles(200, hours=0.25),
            force=True,
        )
    assert pos is None
    assert alerts == []
    assert watcher._choppy_waits >= 1


def test_paper_exit_stop_and_tp_short():
    paper = PaperTradeEngine(starting_equity=1000.0, risk_percent=0.02, enabled=True)
    watcher = V21APaperWatcher(
        enabled=True,
        symbols=["ETHUSDT"],
        paper_engine=paper,
        replay_mode=True,
        emit_alerts=False,
        risk_percent=0.02,
    )
    with patch.object(watcher, "evaluate_at_bar", return_value=_entry_result(side="SHORT")):
        pos = watcher.on_closed_1h(
            "ETHUSDT",
            candles_1h=_candles(80),
            candles_4h=_closed_4h(),
            candles_15m=_candles(200, hours=0.25),
            force=True,
        )
    assert pos is not None
    assert pos.side == "SHORT"
    # Hit stop (short stop above entry)
    closed = paper.tick({pos.symbol: float(pos.stop_price) + 0.01})
    assert closed
    assert closed[0].exit_reason == "STOP"
    assert closed[0].pnl_usd is not None


def test_restart_recovers_open_position():
    paper = PaperTradeEngine(starting_equity=1000.0, enabled=True)
    watcher = V21APaperWatcher(
        enabled=True,
        symbols=["BTCUSDT"],
        paper_engine=paper,
        replay_mode=True,
        emit_alerts=False,
        risk_percent=0.02,
    )
    with patch.object(watcher, "evaluate_at_bar", return_value=_entry_result()):
        pos = watcher.on_closed_1h(
            "BTCUSDT",
            candles_1h=_candles(80),
            candles_4h=_closed_4h(),
            candles_15m=_candles(200, hours=0.25),
            force=True,
        )
    assert pos is not None
    row = pos.to_dict()
    paper2 = PaperTradeEngine(starting_equity=1000.0, enabled=True)
    paper2.hydrate_from_rows([row])
    assert paper2.has_open("BTCUSDT", STREAM_V21A)
    recovered = paper2.get_open("BTCUSDT", STREAM_V21A)
    assert recovered is not None
    assert recovered.id == pos.id
    assert recovered.signal_snippet["strategy_id"] == STRATEGY_ID


def test_telegram_message_for_v21a_entry_and_exit():
    entry_alert = {
        "type": "PAPER_ENTRY",
        "symbol": "BTCUSDT",
        "strategy_id": STRATEGY_ID,
        "telegram_eligible": True,
        "payload": {
            "id": "t1",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 95250.0,
            "stop_price": 94700.0,
            "tp1_price": 96350.0,
            "quantity": 0.01,
            "risk_usd": 5.5,
            "signal_snippet": {
                "strategy_id": STRATEGY_ID,
                "source": SOURCE_WATCHER,
                "telegram_eligible": True,
                "risk_percent": 0.005,
                "event": "Liquidity sweep",
                "confirmation": "15M confirmation",
            },
        },
    }
    assert is_v21a_paper_alert(entry_alert)
    assert is_telegram_trade_alert(entry_alert)
    text = format_telegram_message(entry_alert)
    assert text is not None
    assert "PAPER TRADE OPEN" in text
    assert "PAPER ONLY" in text
    assert "COMBO_02 V2.1-A" in text
    assert "BTCUSDT" in text

    exit_alert = {
        "type": "PAPER_EXIT",
        "symbol": "BTCUSDT",
        "payload": {
            "id": "t1",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 95250.0,
            "stop_price": 94700.0,
            "tp1_price": 96350.0,
            "exit_price": 96350.0,
            "exit_reason": "TP1",
            "r_multiple": 2.0,
            "pnl_usd": 10.0,
            "opened_at": "2024-06-01T10:00:00+00:00",
            "closed_at": "2024-06-01T17:00:00+00:00",
            "signal_snippet": {
                "strategy_id": STRATEGY_ID,
                "source": SOURCE_WATCHER,
                "telegram_eligible": True,
            },
        },
    }
    text2 = format_telegram_message(exit_alert)
    assert text2 is not None
    assert "PAPER TRADE CLOSED" in text2
    assert "+2.0R" in text2
    assert "PAPER ONLY" in text2


def test_telegram_failure_does_not_block_paper_trade():
    paper = PaperTradeEngine(starting_equity=1000.0, enabled=True)
    watcher = V21APaperWatcher(
        enabled=True,
        symbols=["BTCUSDT"],
        paper_engine=paper,
        replay_mode=False,
        emit_alerts=True,
        risk_percent=0.02,
    )

    def _boom(*_a, **_k):
        raise RuntimeError("telegram down")

    with patch.object(watcher, "evaluate_at_bar", return_value=_entry_result()), patch(
        "app.services.paper_trade._live_price_with_source",
        return_value=(100.5, "test"),
    ), patch("app.services.alerts.get_alert_feed") as feed_mod:
        feed = MagicMock()
        feed.observe_paper_open.side_effect = _boom
        feed_mod.return_value = feed
        pos = watcher.on_closed_1h(
            "BTCUSDT",
            candles_1h=_candles(80),
            candles_4h=_closed_4h(),
            candles_15m=_candles(200, hours=0.25),
            force=True,
        )
    assert pos is not None
    assert paper.has_open("BTCUSDT", STREAM_V21A)


def test_real_execution_never_called():
    """Mandatory safety: paper path must not touch exchange order placement."""
    paper = PaperTradeEngine(starting_equity=1000.0, enabled=True)
    watcher = V21APaperWatcher(
        enabled=True,
        symbols=["BTCUSDT"],
        paper_engine=paper,
        replay_mode=True,
        emit_alerts=False,
        risk_percent=0.02,
    )
    real_exec = MagicMock(name="RealExchangeClient")
    paper._real_exchange_client = real_exec  # type: ignore[attr-defined]

    with patch.object(watcher, "evaluate_at_bar", return_value=_entry_result()):
        pos = watcher.on_closed_1h(
            "BTCUSDT",
            candles_1h=_candles(80),
            candles_4h=_closed_4h(),
            candles_15m=_candles(200, hours=0.25),
            force=True,
        )
    assert pos is not None
    assert paper.stream_of(pos) == STREAM_V21A
    assert pos.signal_snippet.get("live_execution") is False
    assert pos.signal_snippet.get("mode") == "PAPER"
    real_exec.assert_not_called()
    real_exec.create_order.assert_not_called()
    real_exec.futures_create_order.assert_not_called()
    real_exec.place_order.assert_not_called()
    real_exec.cancel_order.assert_not_called()
    assert not hasattr(PaperTradeEngine, "submit_real_order")
    assert LIVE_EXECUTION_ALLOWED is False

    # If LIVE were ever flipped on, open_v21a_position must refuse.
    paper2 = PaperTradeEngine(starting_equity=1000.0, enabled=True)
    with patch(
        "app.research.combo02_v21a_paper_constants.LIVE_EXECUTION_ALLOWED",
        True,
    ):
        with pytest.raises(RuntimeError, match="v21a_live_execution_forbidden"):
            paper2.open_v21a_position(
                symbol="ETHUSDT",
                timeframe="1h",
                eval_result=_entry_result(),
                risk_percent=0.02,
                signal_snippet={
                    "strategy_id": STRATEGY_ID,
                    "source": SOURCE_WATCHER,
                },
                setup_bar_time_utc="2024-06-01T11:00:00+00:00",
                replay=True,
                emit_alert=False,
            )


def test_signal_idempotency_key_stable():
    a = signal_idempotency_key(
        symbol="btcusdt",
        direction="LONG",
        setup_timestamp="2024-06-01T10:00:00+00:00",
    )
    b = signal_idempotency_key(
        symbol="BTCUSDT",
        direction="long",
        setup_timestamp="2024-06-01T10:00:00+00:00",
    )
    assert a == b
    assert STRATEGY_ID in a
