"""The BTC daily trend picks which passed sides the paper watcher runs.

BULLISH runs the LONG rows, BEARISH the SHORT rows, NEUTRAL none. A symbol
with an open paper position does not get another one.

The regime book is open: a passed-side signal opens a paper position.
Tests marked ``passed_book_closed`` close it and expect reason
regime_book_closed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

import app.services.passed_paper_watcher as watcher_mod
from app.research.passed_cap_book import PASSED_SIDES, cap_symbols, passed_risk_percent
from app.research.passed_regime_router import allowed_passed_sides, route_passed_book
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine
from app.services.passed_paper_watcher import (
    MAX_LATEST_SIGNALS,
    REGIME_BOOK_CLOSED_NOTE,
    DailySnapshot,
    PassedPaperWatcher,
)

START = datetime(2026, 10, 1, tzinfo=timezone.utc)
SHORT_PLAN = {"status": "SHORT_ENTRY_CANDIDATE", "entry_price": 100.0, "stop_price": 102.0, "tp1": 96.0}
LONG_PLAN = {"status": "LONG_ENTRY_CANDIDATE", "entry_price": 50.0, "stop_price": 48.0, "tp1": 54.0}
SIGNAL_KEYS = {
    "strategy_id",
    "direction",
    "timeframe",
    "symbol",
    "setup_bar_time",
    "trend",
    "as_of_daily_bar",
    "outcome",
    "reason",
    "recorded_at",
}


def _h1(n: int = 250, *, shift_hours: int = 0) -> list[dict[str, Any]]:
    return [
        {"time": START + timedelta(hours=hour + shift_hours), "open": 1, "high": 2, "low": 0.5, "close": 1}
        for hour in range(n)
    ]


def _h4(tip_1h: dict[str, Any], n: int = 80) -> list[dict[str, Any]]:
    """``n`` contiguous 4h bars, the last one fully closed when ``tip_1h`` closes."""
    decision = tip_1h["time"] + timedelta(hours=1)
    last = decision.replace(hour=decision.hour - decision.hour % 4) - timedelta(hours=4)
    return [
        {"time": last - timedelta(hours=4 * (n - 1 - i)), "open": 1, "high": 2, "low": 0.5, "close": 1}
        for i in range(n)
    ]


class _Clock:
    """Stands in for RegimeClock: one trend, the daily bar due at each moment."""

    def __init__(self, trend: str, bars: int) -> None:
        self.trend = trend
        self.bars = bars

    def at(self, moment: datetime) -> dict[str, Any]:
        day = moment.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        return {"trend": self.trend, "as_of_bar": day.isoformat(), "bars": self.bars}


def _set_daily(monkeypatch, watcher, trend: str, bars: int = 60) -> dict[str, int]:
    """Serve ``trend`` from the daily read. Returns a read counter."""
    reads = {"n": 0}

    def _read() -> DailySnapshot:
        reads["n"] += 1
        return DailySnapshot(_Clock(trend, bars), read_at="2026-10-14T00:00:00+00:00")

    monkeypatch.setattr(watcher, "read_daily_snapshot", _read)
    return reads


def _due_daily_bar(regime_bar: str) -> str:
    close = datetime.fromisoformat(regime_bar) + timedelta(hours=1)
    return (close.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)).isoformat()


class _Rig:
    def __init__(self, monkeypatch, plans) -> None:
        self.eng = PaperTradeEngine(
            starting_equity=1000.0, enabled=True, risk_policy=PaperRiskPolicy(enabled=False)
        )
        self.watcher = PassedPaperWatcher(enabled=True, paper_engine=self.eng)
        self.evaluated: list[str] = []
        self.opens: list[dict[str, Any]] = []
        self.plans = plans

        self.last_plan: dict[str, Any] | None = None

        def _fake_eval(**kwargs):
            cid = kwargs["combination"].combination_id
            self.evaluated.append(cid)
            plan = plans(cid) if callable(plans) else plans.get(cid)
            self.last_plan = dict(plan) if plan else None
            return dict(plan) if plan else {"status": "NO_SETUP"}

        monkeypatch.setattr(watcher_mod, "evaluate_combination_at_bar", _fake_eval)
        real_fill = self.watcher._next_open_fill

        def _fill_at_plan_open(sym, tip, direction, next_bar):
            # The bar after the tip opens at the plan entry; the real fill
            # step adds the causal entry slippage.
            if next_bar is None and self.last_plan and self.last_plan.get("entry_price"):
                next_bar = {
                    "time": tip["time"] + timedelta(hours=1),
                    "open": self.last_plan["entry_price"],
                }
            return real_fill(sym, tip, direction, next_bar)

        monkeypatch.setattr(self.watcher, "_next_open_fill", _fill_at_plan_open)
        real_open = self.eng.open_passed_position

        def _spy_open(**kwargs):
            self.opens.append(kwargs)
            return real_open(**kwargs)

        monkeypatch.setattr(self.eng, "open_passed_position", _spy_open)

    def run(self, symbol: str, trend: str | None, *, shift_hours: int = 0) -> list[Any]:
        candles = _h1(shift_hours=shift_hours)
        return self.watcher.on_closed_1h(
            symbol,
            candles_1h=candles,
            candles_4h=_h4(candles[-1]),
            candles_15m=candles,
            market_direction=trend,
            force=True,
        )

    def signals(self) -> list[dict[str, Any]]:
        return self.watcher.regime_status()["latest_signals"]


@pytest.fixture
def rig(monkeypatch):
    def _make(plans) -> _Rig:
        made = _Rig(monkeypatch, plans)
        # The daily read is a real NEUTRAL (60 bars) unless a test says otherwise.
        _set_daily(monkeypatch, made.watcher, "NEUTRAL")
        return made

    return _make


# --- which sides run ----------------------------------------------------------


def test_bullish_evaluates_only_the_long_side_and_opens_no_short(rig):
    r = rig(lambda _cid: SHORT_PLAN)
    assert r.run("BTCUSDT", "BULLISH") == []
    print("BULLISH evaluated:", r.evaluated)
    assert r.evaluated == ["COMBO_03_TRANSITION_B"]
    assert r.opens == []
    assert r.eng.status()["open_count"] == 0
    assert r.signals() == []
    assert r.watcher.last_skip == "BTCUSDT:no_passed_entry"


@pytest.mark.usefixtures("passed_book_open")
def test_bullish_long_candidate_opens_long(rig):
    r = rig({"COMBO_03_TRANSITION_B": LONG_PLAN})
    opened = r.run("BTCUSDT", "BULLISH")
    assert [(p.signal_snippet["strategy_id"], p.side) for p in opened] == [
        ("COMBO_03_TRANSITION_B", "LONG")
    ]


def test_bearish_evaluates_the_nine_short_rows_and_opens_no_long(rig):
    r = rig({"COMBO_03_TRANSITION_B": LONG_PLAN})
    assert r.run("BTCUSDT", "BEARISH") == []
    print("BEARISH evaluated:", r.evaluated)
    shorts = [cid for cid, _tf, direction in PASSED_SIDES if direction == "SHORT"]
    assert len(shorts) == 9
    assert r.evaluated == shorts
    # COMBO_03_TRANSITION_B ran once, for its SHORT row. Its LONG row did not run.
    assert r.evaluated.count("COMBO_03_TRANSITION_B") == 1
    assert r.opens == []
    assert r.eng.status()["open_count"] == 0
    assert r.signals() == []


@pytest.mark.parametrize("trend", ["NEUTRAL", "SIDEWAYS", "  "])
def test_neutral_evaluates_nothing_and_leaves_open_positions_alone(rig, trend):
    r = rig(lambda _cid: SHORT_PLAN)
    held = r.eng.open_passed_position(
        symbol="BTCUSDT",
        combination_id="COMBO_01",
        timeframe="1h",
        direction="SHORT",
        eval_result=dict(SHORT_PLAN),
        risk_percent=0.02,
        setup_bar_time_utc="2026-09-30T00:00:00+00:00",
        market_direction="BEARISH",
    )
    assert held is not None
    before = held.to_dict()
    r.opens.clear()

    # A blank override falls back to the regime read, which is NEUTRAL here.
    assert r.run("BTCUSDT", trend if trend.strip() else None) == []
    print("NEUTRAL evaluated:", r.evaluated)
    assert r.evaluated == []
    assert r.opens == []
    assert r.watcher.last_skip == "BTCUSDT:neutral_trend"
    tip_iso = _h1()[-1]["time"].isoformat()
    assert f"BTCUSDT|{tip_iso}" in r.watcher._processed
    assert r.watcher._inflight == set()
    assert r.eng.status()["open_count"] == 1
    still = r.eng.get_open("BTCUSDT", "PASSED|COMBO_01|SHORT")
    assert still is held
    assert still.to_dict() == before
    assert (still.entry_price, still.stop_price, still.tp1_price) == (100.0, 102.0, 96.0)
    assert r.signals() == []


def test_neutral_bar_is_not_evaluated_again(rig):
    r = rig(lambda _cid: SHORT_PLAN)
    r.watcher._seeded.add("BTCUSDT")
    candles = _h1()
    kwargs = dict(candles_1h=candles, candles_4h=_h4(candles[-1]), candles_15m=candles)
    assert r.watcher.on_closed_1h("BTCUSDT", market_direction="NEUTRAL", **kwargs) == []
    assert r.watcher.last_skip == "BTCUSDT:neutral_trend"
    assert r.watcher.on_closed_1h("BTCUSDT", market_direction="BEARISH", **kwargs) == []
    assert r.watcher.last_skip == "BTCUSDT:already_processed"
    assert r.evaluated == []


@pytest.mark.usefixtures("passed_book_open")
def test_trend_comes_from_the_regime_read_when_no_override(rig, monkeypatch):
    r = rig({"COMBO_03_TRANSITION_B": LONG_PLAN})
    reads = _set_daily(monkeypatch, r.watcher, "BULLISH")
    opened = r.run("BTCUSDT", None)
    assert reads["n"] == 1
    assert len(opened) == 1 and opened[0].side == "LONG"
    assert r.evaluated == ["COMBO_03_TRANSITION_B"]
    signal = r.signals()[0]
    assert signal["trend"] == "BULLISH"
    assert signal["as_of_daily_bar"] == "2026-10-10T00:00:00+00:00"
    assert opened[0].signal_snippet["market_direction"] == "BULLISH"


@pytest.mark.usefixtures("passed_book_open")
def test_watcher_takes_the_book_from_the_router(rig, monkeypatch):
    r = rig(lambda _cid: SHORT_PLAN)
    asked: list[Any] = []

    def _nothing(trend):
        asked.append(trend)
        return ()

    monkeypatch.setattr(watcher_mod, "allowed_passed_sides", _nothing)
    assert r.run("BTCUSDT", "BEARISH") == []
    assert asked == ["BEARISH"]
    assert r.evaluated == []
    assert r.opens == []

    # And whatever the router returns is what runs.
    monkeypatch.setattr(
        watcher_mod, "allowed_passed_sides", lambda _trend: (("COMBO_02_LOCAL", "1h", "SHORT"),)
    )
    opened = r.run("ETHUSDT", "BEARISH")
    assert r.evaluated == ["COMBO_02_LOCAL"]
    assert [p.signal_snippet["strategy_id"] for p in opened] == ["COMBO_02_LOCAL"]


# --- closed regime book -------------------------------------------------------


@pytest.mark.usefixtures("passed_book_closed")
def test_bearish_tick_evaluates_the_short_sides_records_them_and_opens_nothing(rig, monkeypatch):
    r = rig(lambda _cid: SHORT_PLAN)
    _set_daily(monkeypatch, r.watcher, "BEARISH")
    assert r.run("BTCUSDT", None) == []

    shorts = [cid for cid, _tf, direction in PASSED_SIDES if direction == "SHORT"]
    assert r.evaluated == shorts
    assert r.opens == []
    assert r.eng.status()["open_count"] == 0
    assert r.watcher.opened_count == 0
    # Every signal the allowed book emitted is recorded, newest first.
    signals = r.signals()
    assert [s["strategy_id"] for s in reversed(signals)] == shorts
    for signal in signals:
        assert SIGNAL_KEYS <= set(signal)
        assert (signal["direction"], signal["trend"]) == ("SHORT", "BEARISH")
        assert (signal["outcome"], signal["reason"]) == ("not_opened", "regime_book_closed")
        assert "position_id" not in signal
    assert r.watcher.last_skip == "BTCUSDT:regime_book_closed"
    tip_iso = _h1()[-1]["time"].isoformat()
    assert f"BTCUSDT|{tip_iso}" in r.watcher._processed
    assert r.watcher.last_evaluated_bar["sides_evaluated"] == len(shorts)


@pytest.mark.usefixtures("passed_book_closed")
def test_closed_book_keeps_the_one_position_check(rig):
    r = rig({"COMBO_01": SHORT_PLAN})
    held = r.eng.open_passed_position(
        symbol="SOLUSDT",
        combination_id="COMBO_02",
        timeframe="1h",
        direction="SHORT",
        eval_result=dict(SHORT_PLAN),
        risk_percent=0.02,
        setup_bar_time_utc="2026-09-30T00:00:00+00:00",
        market_direction="BEARISH",
    )
    assert held is not None
    r.opens.clear()
    assert r.run("SOLUSDT", "BEARISH") == []
    assert r.opens == []
    assert r.signals()[0]["reason"] == "symbol_has_open_position"
    assert r.watcher.last_skip == "SOLUSDT:symbol_has_open_position"
    # Another symbol records the signal as a closed-book signal.
    assert r.run("ETHUSDT", "BEARISH") == []
    assert r.opens == []
    assert r.signals()[0]["reason"] == "regime_book_closed"


# --- one position per symbol --------------------------------------------------


@pytest.mark.usefixtures("passed_book_open")
def test_two_signals_on_one_bar_open_exactly_one_position(rig):
    r = rig({"COMBO_02": SHORT_PLAN, "COMBO_01": SHORT_PLAN})
    opened = r.run("SOLUSDT", "BEARISH")
    # COMBO_02 is ahead of COMBO_01 in PASSED_SIDES.
    assert [p.signal_snippet["strategy_id"] for p in opened] == ["COMBO_02"]
    assert r.eng.status()["open_count"] == 1
    assert [o["combination_id"] for o in r.opens] == ["COMBO_02"]
    assert r.watcher.opened_count == 1
    signals = r.signals()
    assert [(s["strategy_id"], s["outcome"], s["reason"]) for s in signals] == [
        ("COMBO_01", "not_opened", "symbol_has_open_position"),
        ("COMBO_02", "opened", ""),
    ]
    assert signals[1]["position_id"] == opened[0].id
    tip_iso = _h1()[-1]["time"].isoformat()
    for signal in signals:
        assert SIGNAL_KEYS <= set(signal)
        assert (signal["symbol"], signal["direction"], signal["timeframe"]) == ("SOLUSDT", "SHORT", "1h")
        assert signal["setup_bar_time"] == tip_iso
        assert signal["trend"] == "BEARISH"


@pytest.mark.usefixtures("passed_book_open")
def test_open_position_blocks_a_new_open_but_the_signal_is_recorded(rig):
    r = rig({"COMBO_01": SHORT_PLAN})
    first = r.run("SOLUSDT", "BEARISH")
    assert len(first) == 1
    held = first[0].to_dict()

    # Next bar, same symbol, another side signals.
    r.plans.clear()
    r.plans["COMBO_02_V2"] = SHORT_PLAN
    r.opens.clear()
    assert r.run("SOLUSDT", "BEARISH", shift_hours=1) == []
    assert r.opens == []
    assert r.eng.status()["open_count"] == 1
    assert r.watcher.last_skip == "SOLUSDT:symbol_has_open_position"
    newest = r.signals()[0]
    assert (newest["strategy_id"], newest["outcome"], newest["reason"]) == (
        "COMBO_02_V2",
        "not_opened",
        "symbol_has_open_position",
    )
    assert r.eng.get_open("SOLUSDT", "PASSED|COMBO_01|SHORT").to_dict() == held

    # A different symbol is unaffected.
    other = r.run("ETHUSDT", "BEARISH", shift_hours=1)
    assert [(p.symbol, p.signal_snippet["strategy_id"]) for p in other] == [("ETHUSDT", "COMBO_02_V2")]
    assert r.eng.status()["open_count"] == 2


def test_a_position_from_another_stream_blocks_too(rig):
    """The rule is per symbol: any open paper position, not only cap-book ones."""
    from app.services.paper_trade import PaperPosition

    r = rig({"COMBO_01": SHORT_PLAN})
    foreign = PaperPosition(
        id="v1-pos",
        symbol="SOLUSDT",
        side="LONG",
        status="OPEN",
        entry_price=10.0,
        stop_price=9.0,
        tp1_price=12.0,
        quantity=1.0,
        risk_usd=1.0,
        opened_at="2026-10-01T00:00:00+00:00",
        signal_snippet={"source": "V1_PAPER_WATCHER", "strategy_id": "COMBO_02_V1"},
    )
    r.eng._open[r.eng.book_key("SOLUSDT", r.eng.stream_of(foreign))] = foreign
    assert r.run("SOLUSDT", "BEARISH") == []
    assert r.opens == []
    assert r.signals()[0]["reason"] == "symbol_has_open_position"
    assert r.eng.status()["open_count"] == 1


def test_open_position_check_failure_opens_nothing_and_retries(rig, monkeypatch):
    r = rig({"COMBO_01": SHORT_PLAN})

    def _boom(stream=None):
        raise RuntimeError("book unavailable")

    monkeypatch.setattr(r.eng, "open_symbols", _boom)
    r.watcher._seeded.add("SOLUSDT")
    candles = _h1()
    kwargs = dict(candles_1h=candles, candles_4h=_h4(candles[-1]), candles_15m=candles)
    monkeypatch.setattr(
        "app.services.v1_paper_watcher.tip_open_lag_seconds", lambda *_a, **_k: 600.0
    )
    assert r.watcher.on_closed_1h("SOLUSDT", market_direction="BEARISH", **kwargs) == []
    assert r.opens == []
    assert r.signals()[0]["reason"] == "open_position_check_failed"
    assert f"SOLUSDT|{candles[-1]['time'].isoformat()}" not in r.watcher._processed


@pytest.mark.usefixtures("passed_book_open")
def test_engine_refusal_is_recorded_with_its_reason(rig):
    bad = {"status": "SHORT_ENTRY_CANDIDATE", "entry_price": 100.0, "stop_price": 99.0, "tp1": 96.0}
    r = rig({"COMBO_01": bad})
    assert r.run("SOLUSDT", "BEARISH") == []
    assert len(r.opens) == 1
    newest = r.signals()[0]
    assert (newest["outcome"], newest["reason"]) == ("not_opened", "engine_refused:passed_bad_stop")
    assert r.watcher.last_skip == "SOLUSDT:no_passed_entry"


# --- nothing about the trade itself changes -----------------------------------


@pytest.mark.usefixtures("passed_book_open")
@pytest.mark.parametrize(
    "symbol,trend,cid,direction,plan",
    [
        ("BTCUSDT", "BEARISH", "COMBO_01", "SHORT", SHORT_PLAN),
        ("NEARUSDT", "BEARISH", "COMBO_02_V2", "SHORT", SHORT_PLAN),
        ("BTCUSDT", "BULLISH", "COMBO_03_TRANSITION_B", "LONG", LONG_PLAN),
        ("NEARUSDT", "BULLISH", "COMBO_03_TRANSITION_B", "LONG", LONG_PLAN),
    ],
)
def test_risk_and_plan_reach_the_engine_unchanged(rig, symbol, trend, cid, direction, plan):
    extra = {**plan, "targets": [{"target_price": plan["tp1"]}], "note": "kept"}
    r = rig({cid: extra})
    opened = r.run(symbol, trend)
    assert len(opened) == 1
    call = r.opens[0]
    assert call["risk_percent"] == passed_risk_percent(symbol, direction, trend)
    # The plan reaches the engine unchanged except the entry, which is the
    # causal fill: the next bar's open (here the plan entry) slipped 0.05%.
    slip = 0.0005
    fill = plan["entry_price"] * (1 - slip if direction == "SHORT" else 1 + slip)
    tip = _h1()[-1]["time"]
    assert call["eval_result"] == {
        **extra,
        "entry_price": pytest.approx(fill),
        "signal_entry_price": plan["entry_price"],
        "next_open": plan["entry_price"],
        "fill_bar_time": (tip + timedelta(hours=1)).isoformat(),
        "entry_type": "NEXT_OPEN",
        "entry_slippage_rate": slip,
        "exit_slippage_rate": slip,
    }
    assert call["market_direction"] == trend
    assert (call["symbol"], call["combination_id"], call["timeframe"], call["direction"]) == (
        symbol,
        cid,
        "1h",
        direction,
    )
    assert call["setup_bar_time_utc"] == _h1()[-1]["time"].isoformat()
    pos = opened[0]
    assert (pos.entry_price, pos.stop_price, pos.tp1_price) == (
        pytest.approx(fill),
        plan["stop_price"],
        plan["tp1"],
    )
    assert pos.signal_snippet["risk_percent"] == passed_risk_percent(symbol, direction, trend)


# --- admin status -------------------------------------------------------------

STATUS_KEYS = {
    "enabled",
    "trend",
    "as_of_daily_bar",
    "daily_bars",
    "allowed",
    "skipped",
    "allowed_strategy_ids",
    "skipped_strategy_ids",
    "latest_signals",
    "last_evaluated_bar",
    "last_skip",
    "opened_count",
    "daily_read_ok",
    "daily_read_error",
    "regime_bar",
    "regime_read_at",
    "min_daily_bars",
    "short_history",
    "book_open",
    "book_note",
    "symbols",
    "symbol_risk",
}


@pytest.mark.parametrize("trend,n_allowed", [("BULLISH", 1), ("BEARISH", 9), ("NEUTRAL", 0)])
def test_regime_status_shape(rig, monkeypatch, trend, n_allowed):
    r = rig(lambda _cid: SHORT_PLAN)
    _set_daily(monkeypatch, r.watcher, trend, bars=77)
    status = r.watcher.regime_status()
    assert set(status) == STATUS_KEYS
    json.dumps(status)
    book = route_passed_book(trend)
    assert status["trend"] == trend
    assert status["daily_read_ok"] is True
    assert status["daily_read_error"] == ""
    assert status["as_of_daily_bar"] == _due_daily_bar(status["regime_bar"])
    assert status["daily_bars"] == 77
    assert status["min_daily_bars"] == 40
    assert status["short_history"] is False
    assert status["allowed"] == book["allowed"]
    assert status["skipped"] == book["skipped"]
    assert len(status["allowed"]) == n_allowed
    assert len(status["allowed"]) + len(status["skipped"]) == len(PASSED_SIDES)
    assert status["allowed_strategy_ids"] == [cid for cid, _tf, _d in allowed_passed_sides(trend)]
    assert status["latest_signals"] == []
    assert status["last_evaluated_bar"] is None
    assert status["last_skip"] is None
    assert status["opened_count"] == 0
    assert status["enabled"] is True
    assert status["book_open"] is True
    assert status["book_note"] == ""
    assert status["symbols"] == list(cap_symbols())
    assert status["symbol_risk"]["large_risk_percent"] == 0.02
    assert status["symbol_risk"]["mid_risk_percent"] == 0.01
    # Reading the status runs nothing.
    assert r.evaluated == []
    assert r.opens == []
    assert r.eng.status()["open_count"] == 0


@pytest.mark.usefixtures("passed_book_closed")
def test_regime_status_after_a_bar(rig, monkeypatch):
    r = rig({"COMBO_01": SHORT_PLAN})
    _set_daily(monkeypatch, r.watcher, "BEARISH", bars=77)
    r.run("BTCUSDT", None)
    status = r.watcher.regime_status()
    print("regime_status sample:", json.dumps(status))
    # The book is closed: the signal is shown, nothing opened.
    assert status["opened_count"] == 0
    assert r.opens == []
    assert status["book_open"] is False
    assert status["book_note"] == REGIME_BOOK_CLOSED_NOTE
    assert status["trend"] == "BEARISH"
    assert status["allowed_strategy_ids"] == [cid for cid, _tf, _d in allowed_passed_sides("BEARISH")]
    assert [(s["outcome"], s["reason"]) for s in status["latest_signals"]] == [
        ("not_opened", "regime_book_closed")
    ]
    assert status["last_evaluated_bar"] == {
        "symbol": "BTCUSDT",
        "bar": _h1()[-1]["time"].isoformat(),
        "trend": "BEARISH",
        "as_of_daily_bar": "2026-10-10T00:00:00+00:00",
        "sides_evaluated": 9,
    }
    # The status shows the regime that bar used, not a fresh read.
    assert status["regime_bar"] == _h1()[-1]["time"].isoformat()
    assert status["as_of_daily_bar"] == "2026-10-10T00:00:00+00:00"
    assert status["daily_bars"] == 77
    assert [s["strategy_id"] for s in status["latest_signals"]] == ["COMBO_01"]
    # A copy: editing it does not touch the watcher.
    status["latest_signals"].clear()
    assert len(r.watcher.regime_status()["latest_signals"]) == 1


def test_regime_status_shows_a_failed_read_as_a_failure(rig, monkeypatch):
    r = rig({})
    # Use the real daily read against a failing store.
    monkeypatch.delattr(r.watcher, "read_daily_snapshot")
    from app.services.ohlcv_store import ohlcv_store

    def _boom(*_a, **_kw):
        raise RuntimeError("store down")

    monkeypatch.setattr(ohlcv_store, "get_candles_for_engine", _boom)
    status = r.watcher.regime_status()
    assert status["daily_read_ok"] is False
    assert status["daily_read_error"] == "daily_read_failed"
    assert status["allowed"] == []
    assert len(status["skipped"]) == len(PASSED_SIDES)
    assert (status["as_of_daily_bar"], status["daily_bars"]) == ("", 0)


def test_latest_signals_is_bounded_and_newest_first(rig):
    r = rig({"COMBO_01": SHORT_PLAN})
    r.run("SOLUSDT", "BEARISH")
    for shift in range(1, MAX_LATEST_SIGNALS + 6):
        r.run("SOLUSDT", "BEARISH", shift_hours=shift)
    signals = r.signals()
    assert len(signals) == MAX_LATEST_SIGNALS
    bars = [s["setup_bar_time"] for s in signals]
    assert bars == sorted(bars, reverse=True)
    assert bars[0] == _h1(shift_hours=MAX_LATEST_SIGNALS + 5)[-1]["time"].isoformat()
