"""COMBO_02 V2.1-A paper validation / live↔backtest observability tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.research.combo02_v21a_paper_constants import LIVE_EXECUTION_ALLOWED, MODE, STRATEGY_ID
from app.services.paper_trade import PaperTradeEngine, STREAM_V21A
from app.services.v21a_paper_validation import (
    PARITY_MATCH,
    assert_paper_no_real_orders,
    build_evaluation_audit,
    build_signal_snapshot,
    decision_from_result,
    get_v21a_validation_monitor,
    is_candle_fully_closed,
    paper_performance_summary,
    replay_snapshot_through_evaluator,
    reset_v21a_validation_monitor_for_tests,
)
from app.services.v21a_paper_watcher import (
    V21APaperWatcher,
    is_choppy_wait,
    reset_v21a_paper_watcher_for_tests,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_v21a_paper_watcher_for_tests()
    reset_v21a_validation_monitor_for_tests()
    yield
    reset_v21a_paper_watcher_for_tests()
    reset_v21a_validation_monitor_for_tests()


def _candles(
    n: int,
    *,
    hours: float = 1.0,
    base: float = 100.0,
    drift: float = 0.1,
    closed: bool | None = True,
    start: datetime | None = None,
) -> list[dict]:
    t0 = start or datetime(2024, 6, 1, tzinfo=timezone.utc)
    out = []
    px = base
    step = timedelta(hours=hours)
    for i in range(n):
        o = px
        c = px + drift
        row: dict[str, Any] = {
            "time": t0 + step * i,
            "open": o,
            "high": max(o, c) + 0.5,
            "low": min(o, c) - 0.5,
            "close": c,
            "volume": 1000.0 + i,
        }
        if closed is not None:
            row["is_closed"] = closed
        out.append(row)
        px = c
    return out


def _closed_4h(*, closed: bool | None = True) -> list[dict]:
    """4h bars fully closed at the close of the ``_candles(80)`` 1h tip."""
    return _candles(20, hours=4, closed=closed)


def _entry_result() -> dict[str, Any]:
    return {
        "status": "LONG_ENTRY_CANDIDATE",
        "combination_id": STRATEGY_ID,
        "entry_price": 100.0,
        "stop_price": 98.0,
        "tp1": 104.0,
        "targets": [{"target_price": 104.0}],
        "regime": "BULL_TREND",
        "playbook": "TREND_CONTINUATION",
        "signal_time": "2024-06-01T10:00:00+00:00",
    }


def test_assert_paper_no_real_orders():
    assert MODE == "PAPER"
    assert LIVE_EXECUTION_ALLOWED is False
    assert_paper_no_real_orders()


def test_forming_candle_rejected_closed_candle_eligible():
    paper = PaperTradeEngine(starting_equity=1000.0, enabled=True)
    watcher = V21APaperWatcher(
        enabled=True,
        symbols=["BTCUSDT"],
        paper_engine=paper,
        replay_mode=True,
        emit_alerts=False,
        risk_percent=0.02,
    )
    closed = _candles(80, closed=True)
    with patch.object(watcher, "evaluate_at_bar", return_value=_entry_result()):
        pos = watcher.on_closed_1h(
            "BTCUSDT",
            candles_1h=closed,
            candles_4h=_closed_4h(),
            candles_15m=_candles(200, hours=0.25, closed=True),
            force=True,
        )
    assert pos is not None

    # Explicit forming tip → no signal
    forming = _candles(80, closed=True)
    forming[-1]["is_closed"] = False
    paper2 = PaperTradeEngine(starting_equity=1000.0, enabled=True)
    watcher2 = V21APaperWatcher(
        enabled=True,
        symbols=["ETHUSDT"],
        paper_engine=paper2,
        replay_mode=True,
        emit_alerts=False,
        risk_percent=0.02,
    )
    with patch.object(watcher2, "evaluate_at_bar", return_value=_entry_result()):
        pos2 = watcher2.on_closed_1h(
            "ETHUSDT",
            candles_1h=forming,
            candles_4h=_closed_4h(),
            candles_15m=_candles(200, hours=0.25, closed=True),
            force=True,
        )
    assert pos2 is None
    assert "forming_candle" in (watcher2._last_skip or "")
    assert get_v21a_validation_monitor().status()["forming_candle_blocks"] >= 1


def test_choppy_wait_no_trade_no_telegram():
    paper = PaperTradeEngine(starting_equity=1000.0, enabled=True)
    watcher = V21APaperWatcher(
        enabled=True,
        symbols=["BTCUSDT"],
        paper_engine=paper,
        replay_mode=True,
        emit_alerts=True,
        risk_percent=0.02,
    )
    chop = {
        "status": "NO_SETUP",
        "reason": "WAIT_CHOPPY_RANGE_DISABLED_V21A",
        "regime": "CHOPPY",
        "playbook": "WAIT",
        "combination_id": STRATEGY_ID,
    }
    assert is_choppy_wait(chop)
    assert decision_from_result(chop) == "WAIT"
    with patch.object(watcher, "evaluate_at_bar", return_value=chop):
        pos = watcher.on_closed_1h(
            "BTCUSDT",
            candles_1h=_candles(80),
            candles_4h=_closed_4h(),
            candles_15m=_candles(200, hours=0.25),
            force=True,
        )
    assert pos is None
    assert watcher._choppy_waits >= 1
    mon = get_v21a_validation_monitor()
    assert mon.status()["last_evaluation"] is not None
    assert mon.status()["last_evaluation"]["decision"] == "WAIT"


def test_duplicate_same_candle_one_signal():
    paper = PaperTradeEngine(starting_equity=1000.0, enabled=True)
    watcher = V21APaperWatcher(
        enabled=True,
        symbols=["BTCUSDT"],
        paper_engine=paper,
        replay_mode=True,
        emit_alerts=False,
        risk_percent=0.02,
    )
    c1h = _candles(80)
    with patch.object(watcher, "evaluate_at_bar", return_value=_entry_result()):
        a = watcher.on_closed_1h(
            "BTCUSDT",
            candles_1h=c1h,
            candles_4h=_closed_4h(),
            candles_15m=_candles(200, hours=0.25),
            force=True,
        )
        b = watcher.on_closed_1h(
            "BTCUSDT",
            candles_1h=c1h,
            candles_4h=_closed_4h(),
            candles_15m=_candles(200, hours=0.25),
            force=False,
        )
    assert a is not None
    assert b is None
    assert paper.status()["open_by_stream"][STREAM_V21A] == 1


def test_websocket_duplicate_event_one_trade():
    """Same closed tip delivered twice (reconnect/poll) → one paper trade."""
    paper = PaperTradeEngine(starting_equity=1000.0, enabled=True)
    watcher = V21APaperWatcher(
        enabled=True,
        symbols=["SOLUSDT"],
        paper_engine=paper,
        replay_mode=True,
        emit_alerts=False,
        risk_percent=0.02,
    )
    c1h = _candles(80)
    with patch.object(watcher, "evaluate_at_bar", return_value=_entry_result()):
        first = watcher.on_closed_1h(
            "SOLUSDT",
            candles_1h=c1h,
            candles_4h=_closed_4h(),
            candles_15m=_candles(200, hours=0.25),
            force=True,
        )
        # Simulate duplicate WS event with force=True but same signal_id / open book
        second = watcher.on_closed_1h(
            "SOLUSDT",
            candles_1h=c1h,
            candles_4h=_closed_4h(),
            candles_15m=_candles(200, hours=0.25),
            force=True,
        )
    assert first is not None
    assert second is None
    opens = paper.positions(closed_limit=10).get("open") or []
    assert len([p for p in opens if p["symbol"] == "SOLUSDT"]) == 1


def test_restart_hydrate_and_close():
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
    recovered = paper2.get_open("BTCUSDT", STREAM_V21A)
    assert recovered is not None
    assert recovered.id == pos.id

    closed = paper2.tick({"BTCUSDT": float(recovered.stop_price) - 0.01})
    assert closed
    assert closed[0].exit_reason == "STOP"
    assert not paper2.has_open("BTCUSDT", STREAM_V21A)


def test_telegram_failure_paper_continues():
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
            candles_1h=_candles(80, closed=True),
            candles_4h=_closed_4h(),
            candles_15m=_candles(200, hours=0.25, closed=True),
            force=True,
        )
    assert pos is not None
    assert paper.has_open("BTCUSDT", STREAM_V21A)


def test_paper_exit_stop_tp():
    paper = PaperTradeEngine(starting_equity=1000.0, enabled=True)
    watcher = V21APaperWatcher(
        enabled=True,
        symbols=["LINKUSDT"],
        paper_engine=paper,
        replay_mode=True,
        emit_alerts=False,
        risk_percent=0.02,
    )
    with patch.object(watcher, "evaluate_at_bar", return_value=_entry_result()):
        pos = watcher.on_closed_1h(
            "LINKUSDT",
            candles_1h=_candles(80),
            candles_4h=_closed_4h(),
            candles_15m=_candles(200, hours=0.25),
            force=True,
        )
    assert pos is not None
    # TP path
    closed = paper.tick({pos.symbol: float(pos.tp1_price) + 0.01})
    assert closed
    assert closed[0].exit_reason == "TP1"


def test_real_execution_safety():
    assert LIVE_EXECUTION_ALLOWED is False
    paper = PaperTradeEngine(starting_equity=1000.0, enabled=True)
    real_exec = MagicMock(name="ExchangeOrders")
    paper._real_exchange_client = real_exec  # type: ignore[attr-defined]
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
    real_exec.create_order.assert_not_called()
    real_exec.futures_create_order.assert_not_called()


def test_backtest_parity_same_decision():
    c1h = _candles(80, closed=True)
    c4h = _candles(80, hours=4, closed=True)
    c15 = _candles(200, hours=0.25, closed=True)
    live = {
        "status": "NO_SETUP",
        "reason": "WAIT_CHOPPY_RANGE_DISABLED_V21A",
        "regime": "CHOPPY",
        "playbook": "WAIT",
    }
    # Replay uses real evaluator — for CHOPPY inputs may or may not match synthetic
    # live; instead verify helper match path when decisions equal.
    with patch(
        "app.research.combination_engine.evaluate_combination_at_bar",
        return_value=dict(live),
    ):
        parity = replay_snapshot_through_evaluator(
            symbol="BTCUSDT",
            candles_1h=c1h,
            candles_4h=c4h,
            candles_15m=c15,
            live_decision="WAIT",
            live_result=live,
            signal_id="test",
        )
    assert parity["event"] == PARITY_MATCH
    assert parity["live_decision"] == parity["backtest_decision"] == "WAIT"


def test_evaluation_audit_and_signal_snapshot():
    tip = _candles(3, closed=True)[-1]
    result = _entry_result()
    audit = build_evaluation_audit(
        symbol="BTCUSDT",
        tip_1h=tip,
        tip_4h=tip,
        tip_15m=tip,
        result=result,
        signal_id="sig1",
    )
    assert audit["event"] == "V21A_EVALUATION"
    assert audit["decision"] == "SIGNAL"
    assert audit["1h_candle"] is not None
    snap = build_signal_snapshot(
        symbol="BTCUSDT",
        tip_1h=tip,
        tip_4h=tip,
        tip_15m=tip,
        result=result,
        signal_id="sig1",
        direction="LONG",
    )
    assert snap["event"] == "V21A_SIGNAL_SNAPSHOT"
    assert snap["entry"] == 100.0
    assert snap["mode"] == "PAPER"


def test_is_candle_fully_closed_helpers():
    past = {
        "time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "is_closed": True,
    }
    assert is_candle_fully_closed(past, "1h")
    forming = {
        "time": datetime.now(timezone.utc),
        "is_closed": False,
    }
    assert not is_candle_fully_closed(forming, "1h")


def test_paper_performance_observation_label():
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
    paper.tick({pos.symbol: float(pos.tp1_price) + 0.01})
    perf = paper_performance_summary(paper)
    assert perf["label"] == "OBSERVATION"
    assert perf["paper_trades"] >= 1
    assert "total_r" in perf


def test_watcher_health_status_fields():
    watcher = V21APaperWatcher(enabled=True, symbols=["BTCUSDT"], emit_alerts=False)
    st = watcher.status()
    assert st["strategy"] == STRATEGY_ID
    assert st["mode"] == "PAPER"
    assert st["real_execution"] == "DISABLED"
    assert st["status"] in ("RUNNING", "STOPPED", "HALTED")
    assert "validation" in st
