"""The passed watcher evaluates every closed 1h bar once, oldest first.

A per-symbol watermark in a state file lets a restart or a stalled loop resume
from the last processed bar instead of jumping to the latest one. A signal on
a bar evaluated after the late-open bound is journaled as missed and counted;
it never opens. Clocks, the OHLCV store and the combination eval are fakes;
the paper engine is real.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.ohlcv_store as ohlcv_store_mod
import app.services.passed_paper_watcher as watcher_mod
import app.services.v1_paper_watcher as v1_mod
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine
from app.services.passed_paper_watcher import (
    MAX_CATCHUP_BARS,
    DailySnapshot,
    PassedPaperWatcher,
)

pytestmark = pytest.mark.usefixtures("passed_book_open")

SYM = "ARBUSDT"
SIDE = "COMBO_02_V2"
SHORT_PLAN = {"status": "SHORT_ENTRY_CANDIDATE", "entry_price": 100.0, "stop_price": 102.0, "tp1": 96.0}
DAY0 = datetime(2026, 10, 17, tzinfo=timezone.utc)


def _h(hour: int, minute: int = 0) -> datetime:
    return DAY0 + timedelta(hours=hour, minutes=minute)


def _floor(ts: datetime, step: timedelta) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return epoch + ((ts - epoch) // step) * step


def _bars(n: int, step: timedelta, last: datetime) -> list[dict[str, Any]]:
    return [
        {"time": last - step * (n - 1 - i), "open": 100.0, "high": 101.0, "low": 99.0,
         "close": 100.0, "volume": 1000.0, "is_closed": True}
        for i in range(n)
    ]


class _Clock:
    """Stands in for RegimeClock: one trend, the daily bar due at each moment."""

    def __init__(self, trend: str) -> None:
        self.trend = trend

    def at(self, moment: datetime) -> dict[str, Any]:
        day = moment.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        return {"trend": self.trend, "as_of_bar": day.isoformat(), "bars": 60}


class _Store:
    """Closed bars as the store holds them at ``now``, plus the forming 1h bar."""

    def __init__(self) -> None:
        self.now = _h(0)

    def get_candles_for_engine(self, symbol: str, timeframe: str, *, include_open: bool = False):
        step = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "15m": timedelta(minutes=15)}[timeframe]
        n = {"1h": 300, "4h": 120, "15m": 500}[timeframe]
        return _bars(n, step, _floor(self.now, step) - step)

    def get_open(self, symbol: str, timeframe: str):
        return SimpleNamespace(open_time=_floor(self.now, timedelta(hours=1)), open=100.0)


class _Rig:
    def __init__(self, monkeypatch, tmp_path, signal_bars: set[datetime]) -> None:
        self.monkeypatch = monkeypatch
        self.state_path = tmp_path / "passed_paper_watermark.json"
        self.store = _Store()
        self.signal_bars = signal_bars
        self.seen: list[dict[str, Any]] = []
        self.eng = PaperTradeEngine(
            starting_equity=1000.0, enabled=True, risk_policy=PaperRiskPolicy(enabled=False)
        )
        self.opens: list[dict[str, Any]] = []
        real_open = self.eng.open_passed_position

        def _spy_open(**kwargs):
            self.opens.append(kwargs)
            return real_open(**kwargs)

        monkeypatch.setattr(self.eng, "open_passed_position", _spy_open)
        monkeypatch.setattr(ohlcv_store_mod, "ohlcv_store", self.store)
        monkeypatch.setattr(v1_mod, "_utc_now", lambda: self.store.now)
        monkeypatch.setattr(watcher_mod, "evaluate_combination_at_bar", self._fake_eval)
        self.daily = DailySnapshot(_Clock("BEARISH"), read_at="t")

    def _fake_eval(self, **kwargs):
        candles = kwargs["candles"]
        bar = candles[kwargs["as_of_index"]]["time"]
        cid = kwargs["combination"].combination_id
        if cid == SIDE:
            self.seen.append(
                {
                    "bar": bar,
                    "last_1h": candles[-1]["time"],
                    "last_4h": kwargs["candles_4h"][-1]["time"],
                    "last_15m": kwargs["candles_15m"][-1]["time"],
                }
            )
        if cid == SIDE and bar in self.signal_bars:
            return dict(SHORT_PLAN)
        return {"status": "NO_SETUP"}

    def write_state(self, mark: datetime, **extra: Any) -> None:
        payload = {"version": 1, "watermarks": {SYM: mark.isoformat()}, **extra}
        self.state_path.write_text(json.dumps(payload), encoding="utf-8")

    def watcher(self) -> PassedPaperWatcher:
        """A fresh process: nothing in memory, the state file on disk."""
        return PassedPaperWatcher(enabled=True, paper_engine=self.eng, state_path=self.state_path)

    def tick(self, watcher: PassedPaperWatcher, now: datetime) -> list[Any]:
        self.store.now = now
        return watcher.catch_up(SYM, daily=self.daily)

    def evaluated(self) -> list[datetime]:
        return [row["bar"] for row in self.seen]


@pytest.fixture
def rig(monkeypatch, tmp_path):
    return lambda signal_bars=(): _Rig(monkeypatch, tmp_path, set(signal_bars))


def _signals(watcher: PassedPaperWatcher, outcome: str | None = None) -> list[dict[str, Any]]:
    rows = list(reversed(watcher.regime_status()["latest_signals"]))
    return [row for row in rows if outcome is None or row["outcome"] == outcome]


# --- restart catch-up ---------------------------------------------------------


def test_restart_evaluates_every_missed_bar_oldest_first(rig):
    r = rig({_h(8), _h(10), _h(11)})
    r.write_state(_h(7))  # the old process finished the 07:00 bar, then went down
    w = r.watcher()

    opened = r.tick(w, _h(12, 5))

    # 08, 09 and 10 were missed while down; 11 is the closed tip, 5 min after close.
    assert r.evaluated() == [_h(8), _h(9), _h(10), _h(11)]
    missed = _signals(w, "missed")
    assert [(row["setup_bar_time"], row["note"]) for row in missed] == [
        (_h(8).isoformat(), "missed: evaluated late (185m)"),
        (_h(10).isoformat(), "missed: evaluated late (65m)"),
    ]
    assert all(row["reason"] == "late_tip" for row in missed)
    # Only the tip, still inside the late-open bound, opens; at the next open.
    assert len(opened) == 1 and len(r.opens) == 1
    assert r.opens[0]["setup_bar_time_utc"] == _h(11).isoformat()
    assert r.opens[0]["eval_result"]["fill_bar_time"] == _h(12).isoformat()

    status = w.regime_status()
    assert status["missed_signals"] == 2
    assert status["heartbeat"]["watermarks"] == {SYM: _h(11).isoformat()}
    assert status["heartbeat"]["pending_bars"] == 0
    assert status["heartbeat"]["last_eval_bar"] == _h(11).isoformat()
    assert status["heartbeat"]["last_eval_lag_seconds"] == 300.0

    # The next process resumes from the saved watermark and counter.
    saved = json.loads(r.state_path.read_text(encoding="utf-8"))
    assert saved["watermarks"] == {SYM: _h(11).isoformat()}
    assert saved["missed_signals"] == 2
    again = r.watcher()
    assert again.regime_status()["missed_signals"] == 2
    assert again.heartbeat()["watermarks"] == {SYM: _h(11).isoformat()}


def test_older_bars_are_evaluated_on_the_windows_closed_at_their_close(rig):
    r = rig()
    r.write_state(_h(7))
    r.tick(r.watcher(), _h(12, 5))
    by_bar = {row["bar"]: row for row in r.seen}
    # 08:00 closes 09:00: the 4h bar 04:00-08:00 and the 15m bar 08:45 are the last closed.
    assert by_bar[_h(8)]["last_1h"] == _h(8)
    assert by_bar[_h(8)]["last_4h"] == _h(4)
    assert by_bar[_h(8)]["last_15m"] == _h(8, 45)
    # The tip sees the store as it is, as before.
    assert by_bar[_h(11)]["last_1h"] == _h(11)
    assert by_bar[_h(11)]["last_15m"] == _h(11, 45)


# --- late bars ----------------------------------------------------------------


def test_bar_first_evaluated_after_the_bound_is_recorded_as_missed(rig):
    # 2026-10-17 ARBUSDT: the 09:00 signal was first evaluated at 10:56.
    r = rig({_h(9)})
    r.write_state(_h(8))
    w = r.watcher()

    assert r.tick(w, _h(10, 56)) == []

    assert r.opens == []
    [row] = _signals(w)
    assert row["outcome"] == "missed"
    assert row["reason"] == "late_tip"
    assert row["note"] == "missed: evaluated late (56m)"
    assert row["lag_seconds"] == 3360.0
    assert (row["symbol"], row["strategy_id"], row["direction"]) == (SYM, SIDE, "SHORT")
    status = w.regime_status()
    assert status["missed_signals"] == 1
    assert status["recent_missed"][0]["note"] == "missed: evaluated late (56m)"
    assert status["last_skip"] == f"{SYM}:late_tip"
    assert status["heartbeat"]["last_eval_lag_seconds"] == 3360.0
    # Kept across a restart.
    assert r.watcher().regime_status()["recent_missed"][0]["setup_bar_time"] == _h(9).isoformat()


def test_stalled_loop_still_evaluates_the_bar_it_slept_through(rig):
    # 2026-10-17: the loop froze over the 12:01 run and woke after the next close.
    r = rig({_h(11), _h(12)})
    r.write_state(_h(9))
    w = r.watcher()
    r.tick(w, _h(11, 1))  # 10:00 tip, no signal
    assert r.evaluated() == [_h(10)]

    r.tick(w, _h(13, 20))

    assert r.evaluated() == [_h(10), _h(11), _h(12)]
    assert [(row["setup_bar_time"], row["note"]) for row in _signals(w, "missed")] == [
        (_h(11).isoformat(), "missed: evaluated late (80m)"),
        (_h(12).isoformat(), "missed: evaluated late (20m)"),
    ]
    assert r.opens == []
    assert w.regime_status()["missed_signals"] == 2


def test_not_allowed_side_on_a_late_bar_is_not_counted_as_missed(rig, monkeypatch):
    from app.research import passed_symbol_allowlist as allowlist

    entry = allowlist.SymbolAllowlist(
        entries={SYM: allowlist.SymbolEntry(symbol=SYM, allowed=(("COMBO_01", "1h", "SHORT"),))},
        source="tests",
    )
    r = rig({_h(9)})
    r.write_state(_h(8))
    w = PassedPaperWatcher(
        enabled=True, paper_engine=r.eng, state_path=r.state_path, symbol_allowlist=entry
    )
    r.tick(w, _h(10, 56))
    [row] = _signals(w)
    assert row["outcome"] == "not_opened"
    assert w.regime_status()["missed_signals"] == 0


# --- duplicate opens ------------------------------------------------------------


def test_catch_up_never_opens_a_bar_twice(rig):
    r = rig({_h(11)})
    r.write_state(_h(10))
    w = r.watcher()

    assert len(r.tick(w, _h(12, 5))) == 1
    # Same process, next ticks.
    assert r.tick(w, _h(12, 6)) == []
    assert r.tick(w, _h(12, 7)) == []
    # Restart: the watermark already covers the bar.
    restarted = r.watcher()
    assert r.tick(restarted, _h(12, 8)) == []
    assert r.evaluated() == [_h(11)]
    assert len(r.opens) == 1


def test_restart_before_the_watermark_was_saved_does_not_reopen(rig):
    r = rig({_h(11)})
    r.write_state(_h(10))
    assert len(r.tick(r.watcher(), _h(12, 5))) == 1
    # Crash before the save: the file still says 10:00. The ledger holds the position.
    r.write_state(_h(10))
    w = r.watcher()
    assert r.tick(w, _h(12, 8)) == []
    assert len(r.eng.open_symbols()) == 1
    assert [row["reason"] for row in _signals(w)] == ["symbol_has_open_position"]


# --- retries, window, first run -----------------------------------------------------


def test_older_bar_that_cannot_be_evaluated_yet_is_retried_without_holding_the_tip(
    rig, monkeypatch
):
    r = rig({_h(9), _h(10)})
    r.write_state(_h(8))
    w = r.watcher()
    real_fault = v1_mod.series_integrity_fault
    broken = {"on": True}

    def _fault(series_1h, series_4h):
        if broken["on"] and series_1h[-1]["time"] == _h(9):
            return "1h_gap"
        return real_fault(series_1h, series_4h)

    monkeypatch.setattr(v1_mod, "series_integrity_fault", _fault)

    r.tick(w, _h(11, 5))
    # 09:00 waits for the store; the 10:00 tip is not held back.
    assert r.evaluated() == [_h(10)]
    assert len(r.opens) == 1
    assert w.heartbeat()["watermarks"] == {SYM: _h(8).isoformat()}
    assert w.heartbeat()["pending_bars"] == 1

    broken["on"] = False
    r.tick(w, _h(11, 6))
    assert r.evaluated() == [_h(10), _h(9)]
    assert [row["setup_bar_time"] for row in _signals(w, "missed")] == [_h(9).isoformat()]
    assert w.heartbeat()["watermarks"] == {SYM: _h(10).isoformat()}
    assert w.heartbeat()["pending_bars"] == 0
    assert len(r.opens) == 1


def test_bars_beyond_the_catch_up_window_are_recorded_as_not_evaluated(rig):
    r = rig()
    tip = _h(12)
    r.write_state(tip - timedelta(hours=MAX_CATCHUP_BARS + 6))
    w = r.watcher()

    r.tick(w, tip + timedelta(hours=1, minutes=5))

    [row] = _signals(w, "not_evaluated")
    assert row["reason"] == "beyond_catchup_window"
    assert row["bars"] == 6
    assert row["setup_bar_time"] == (tip - timedelta(hours=MAX_CATCHUP_BARS + 5)).isoformat()
    assert row["last_bar_time"] == (tip - timedelta(hours=MAX_CATCHUP_BARS)).isoformat()
    assert row["note"].startswith("missed: 6 bars not evaluated (beyond_catchup_window")
    assert len(r.evaluated()) == MAX_CATCHUP_BARS
    assert r.evaluated()[0] == tip - timedelta(hours=MAX_CATCHUP_BARS - 1)
    status = w.regime_status()
    assert status["unevaluated_bars"] == 6
    assert status["heartbeat"]["watermarks"] == {SYM: tip.isoformat()}


def test_first_run_without_state_evaluates_only_the_tip(rig, monkeypatch):
    monkeypatch.setattr(v1_mod, "closed_tip_is_current", lambda *_a, **_k: True)
    r = rig()
    w = r.watcher()  # no state file yet

    r.tick(w, _h(12, 5))

    assert r.evaluated() == [_h(11)]
    assert json.loads(r.state_path.read_text(encoding="utf-8"))["watermarks"] == {
        SYM: _h(11).isoformat()
    }
    # From then on every bar is caught up.
    r.tick(w, _h(14, 5))
    assert r.evaluated() == [_h(11), _h(12), _h(13)]


def test_watcher_without_a_state_path_writes_nothing(rig, tmp_path):
    r = rig()
    w = PassedPaperWatcher(enabled=True, paper_engine=r.eng)
    w._watermarks[SYM] = _h(9).isoformat()
    r.tick(w, _h(12, 5))
    assert r.evaluated() == [_h(10), _h(11)]
    assert list(tmp_path.iterdir()) == []
    assert w.heartbeat()["state_file"] == ""


# --- heartbeat --------------------------------------------------------------------


def test_heartbeat_reports_lag_and_goes_stale_when_evaluation_stops(rig):
    r = rig()
    r.write_state(_h(10))
    w = r.watcher()
    r.tick(w, _h(12, 2))

    r.store.now = _h(12, 30)
    beat = w.heartbeat()
    assert beat["last_eval_at"] == _h(12, 2).isoformat()
    assert beat["last_eval_bar"] == _h(11).isoformat()
    assert beat["last_eval_lag_seconds"] == 120.0
    assert beat["seconds_since_last_eval"] == 1680.0
    assert beat["stale"] is False

    r.store.now = _h(13, 20)
    assert w.heartbeat()["stale"] is True


def test_route_exposes_missed_signals_and_heartbeat(rig, monkeypatch):
    from app.api import routes

    r = rig({_h(9)})
    r.write_state(_h(8))
    w = r.watcher()
    r.tick(w, _h(10, 56))
    monkeypatch.setattr(watcher_mod, "_watcher", w)

    body = asyncio.run(routes.paper_passed_regime())

    assert body["missed_signals"] == 1
    assert body["recent_missed"][0]["note"] == "missed: evaluated late (56m)"
    assert body["heartbeat"]["last_eval_bar"] == _h(9).isoformat()
    json.dumps(body)
