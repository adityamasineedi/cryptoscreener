"""A closed bar is not opened long after it closed.

Clocks are controlled; nothing sleeps. The V1 paper engine is a stub and the
passed-book engine is standalone. Nothing touches the live book.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.v1_paper_watcher as v1_mod
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine
from app.services.passed_paper_watcher import PassedPaperWatcher
from app.services.v1_paper_watcher import (
    MAX_OPEN_LAG_SECONDS,
    V1PaperWatcher,
    is_v1_long_entry,
    reset_v1_paper_watcher_for_tests,
    tip_open_lag_seconds,
)

# These tests observe the passed watcher's gates through an open, so they run
# with the regime book open. Production keeps it closed.
pytestmark = pytest.mark.usefixtures("passed_book_open")

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
# 81 hourly bars: the tip opens at T0+80h and closes at T0+81h.
TIP_OPEN = T0 + timedelta(hours=80)
TIP_CLOSE = T0 + timedelta(hours=81)

V1_ENTRY = {
    "status": "LONG_ENTRY_CANDIDATE",
    "direction": "LONG",
    "combination_id": "COMBO_02",
    "gates": {"bos": True, "trend": True, "htf": True, "hl_intact": True},
    "required": ["bos", "trend", "hl_intact", "htf"],
    "htf": {"htf_alignment": "HTF_ALIGNED", "trend_1h": "BULLISH", "trend_4h": "BULLISH"},
    "entry_price": 100.0,
    "stop_price": 98.0,
    "tp1": 104.0,
}


@pytest.fixture(autouse=True)
def _reset_watcher():
    reset_v1_paper_watcher_for_tests()
    yield
    reset_v1_paper_watcher_for_tests()


def _bars(n: int, *, hours: int = 1) -> list[dict[str, Any]]:
    return [
        {"time": T0 + timedelta(hours=hours * i), "open": 100.0, "high": 101.0, "low": 99.0,
         "close": 100.0, "volume": 1000.0}
        for i in range(n)
    ]


def _clock(monkeypatch, seconds_after_close: float) -> None:
    now = TIP_CLOSE + timedelta(seconds=seconds_after_close)
    monkeypatch.setattr(v1_mod, "_utc_now", lambda: now)


# --- helper -----------------------------------------------------------------


def test_bound_is_900_seconds():
    assert MAX_OPEN_LAG_SECONDS == 900.0


def test_lag_is_measured_from_the_bar_close():
    assert tip_open_lag_seconds(TIP_OPEN, "1h", now=TIP_CLOSE + timedelta(seconds=600)) == 600.0
    assert tip_open_lag_seconds(TIP_OPEN, "1h", now=TIP_CLOSE) == 0.0
    assert tip_open_lag_seconds(TIP_OPEN, "15m", now=TIP_OPEN + timedelta(minutes=20)) == 300.0
    # Naive values are UTC.
    naive_tip = TIP_OPEN.replace(tzinfo=None)
    naive_now = (TIP_CLOSE + timedelta(seconds=1200)).replace(tzinfo=None)
    assert tip_open_lag_seconds(naive_tip, "1h", now=naive_now) == 1200.0
    assert tip_open_lag_seconds(None, "1h", now=TIP_CLOSE) is None
    assert tip_open_lag_seconds(TIP_OPEN, "nonsense", now=TIP_CLOSE) is None


def test_lag_uses_the_module_clock_when_now_is_omitted(monkeypatch):
    _clock(monkeypatch, 42)
    assert tip_open_lag_seconds(TIP_OPEN, "1h") == 42.0


# --- V1 watcher -------------------------------------------------------------


class _StubPaper:
    """Records each v1 open it is asked for. Never opens anything real."""

    def __init__(self) -> None:
        self.bars: list[str] = []

    def open_v1_combo_position(self, **kwargs: Any) -> Any:
        self.bars.append(kwargs["setup_bar_time_utc"])
        return SimpleNamespace(symbol=kwargs["symbol"], status="OPEN")


def _v1(paper: _StubPaper, monkeypatch, *, replay_mode: bool = False) -> V1PaperWatcher:
    """Watcher whose gates always say "enter", already seeded on the bar before the tip."""
    assert is_v1_long_entry(V1_ENTRY)
    watcher = V1PaperWatcher(paper_engine=paper, replay_mode=replay_mode, emit_alerts=False)
    monkeypatch.setattr(watcher, "evaluate_at_bar", lambda **_kw: dict(V1_ENTRY))
    monkeypatch.setattr(watcher, "_bos_break_allows_entry", lambda *_a, **_kw: True)
    watcher.seed_watermark("BTCUSDT", last_closed_bar_time=TIP_OPEN - timedelta(hours=1))
    return watcher


@pytest.mark.parametrize("seconds", [51, 600, 900])
def test_v1_tip_inside_the_bound_opens_as_today(monkeypatch, seconds):
    paper = _StubPaper()
    watcher = _v1(paper, monkeypatch)
    _clock(monkeypatch, seconds)
    pos = watcher.on_closed_1h("BTCUSDT", candles_1h=_bars(81), candles_4h=_bars(40, hours=4))
    assert pos is not None
    assert paper.bars == [TIP_OPEN.isoformat()]
    assert "BTCUSDT" not in watcher.status()["last_skip_by_symbol"]


@pytest.mark.parametrize("seconds", [901, 1200, 3302])
def test_v1_late_tip_does_not_open_and_records_late_tip(monkeypatch, seconds):
    paper = _StubPaper()
    watcher = _v1(paper, monkeypatch)
    _clock(monkeypatch, seconds)
    bars = _bars(81)
    assert watcher.on_closed_1h("BTCUSDT", candles_1h=bars, candles_4h=_bars(40, hours=4)) is None
    # The paper engine was never asked.
    assert paper.bars == []
    assert watcher.status()["last_skip_by_symbol"]["BTCUSDT"] == {
        "reason": "BTCUSDT:late_tip",
        "bar": TIP_OPEN.isoformat(),
    }
    # Watermark and processed set moved exactly as for any evaluated bar.
    assert watcher._watermarks["BTCUSDT"] == TIP_OPEN.isoformat()
    assert watcher.on_closed_1h("BTCUSDT", candles_1h=bars, candles_4h=_bars(40, hours=4)) is None
    assert watcher._last_skip == "BTCUSDT:already_processed"
    assert paper.bars == []


def test_v1_force_and_replay_are_not_bounded(monkeypatch):
    _clock(monkeypatch, 1200)
    forced = _StubPaper()
    watcher = _v1(forced, monkeypatch)
    assert (
        watcher.on_closed_1h(
            "BTCUSDT", candles_1h=_bars(81), candles_4h=_bars(40, hours=4), force=True
        )
        is not None
    )
    assert forced.bars == [TIP_OPEN.isoformat()]

    replayed = _StubPaper()
    replay_watcher = _v1(replayed, monkeypatch, replay_mode=True)
    assert (
        replay_watcher.on_closed_1h(
            "BTCUSDT", candles_1h=_bars(81), candles_4h=_bars(40, hours=4)
        )
        is not None
    )
    assert replayed.bars == [TIP_OPEN.isoformat()]


# --- passed cap-book watcher --------------------------------------------------

PASSED_SIDE = "COMBO_01"
PASSED_BAR_KEY = f"BTCUSDT|{TIP_OPEN.isoformat()}"


def _passed(monkeypatch, *, raise_on_passes: tuple[int, ...] = ()):
    """Standalone book + watcher. COMBO_01 SHORT wants to enter on every pass.

    ``raise_on_passes`` lists the 1-based passes on which that side's evaluation raises.
    """
    eng = PaperTradeEngine(enabled=True, risk_policy=PaperRiskPolicy(enabled=False))
    watcher = PassedPaperWatcher(enabled=True, paper_engine=eng)
    # Seeded: first-sight rules are not under test here.
    watcher._seeded.add("BTCUSDT")
    state = {"pass": 0}

    def _fake_eval(**kwargs):
        if kwargs["combination"].combination_id != PASSED_SIDE:
            return {"status": "NO_SETUP"}
        if state["pass"] in raise_on_passes:
            raise RuntimeError("eval blew up")
        return {
            "status": "SHORT_ENTRY_CANDIDATE",
            "entry_price": 100.0,
            "stop_price": 102.0,
            "tp1": 96.0,
        }

    monkeypatch.setattr(
        "app.services.passed_paper_watcher.evaluate_combination_at_bar", _fake_eval
    )

    def _run(seconds_after_close: float, *, force: bool = False) -> list[Any]:
        state["pass"] += 1
        _clock(monkeypatch, seconds_after_close)
        # 250 hourly bars ending on the same tip (TIP_OPEN): the readiness floor.
        candles = [{**b, "time": b["time"] - timedelta(hours=169)} for b in _bars(250)]
        # 80 4h bars; the last opens at T0+76h, the bar fully closed at TIP_CLOSE.
        closed_4h = [
            {**b, "time": b["time"] - timedelta(hours=240)} for b in _bars(80, hours=4)
        ]
        return watcher.on_closed_1h(
            "BTCUSDT",
            candles_1h=candles,
            candles_4h=closed_4h,
            candles_15m=candles,
            market_direction="BEARISH",
            force=force,
        )

    return eng, watcher, _run


def test_passed_combination_exposes_the_id_the_fake_eval_keys_on():
    from app.research.bos_combinations import get_combination

    assert get_combination(PASSED_SIDE).combination_id == PASSED_SIDE


def test_passed_tip_inside_the_bound_opens_as_today(monkeypatch):
    eng, watcher, run = _passed(monkeypatch)
    opened = run(600)
    assert [(p.signal_snippet["strategy_id"], p.side) for p in opened] == [(PASSED_SIDE, "SHORT")]
    # Fill stays at the plan.
    assert (opened[0].entry_price, opened[0].stop_price, opened[0].tp1_price) == (100.0, 102.0, 96.0)
    assert opened[0].source_candle_ts == TIP_OPEN.isoformat()
    assert PASSED_BAR_KEY in watcher._processed


def test_passed_late_tip_does_not_open_and_is_marked_processed(monkeypatch):
    eng, watcher, run = _passed(monkeypatch)
    assert run(1200) == []
    assert watcher.last_skip == "BTCUSDT:late_tip"
    assert PASSED_BAR_KEY in watcher._processed
    assert eng.status()["open_count"] == 0
    assert eng.pending_persist_count() == 0
    # Not retried on the next 60 s pass.
    assert run(1260) == []
    assert watcher.last_skip == "BTCUSDT:already_processed"


def test_passed_side_that_raised_is_retried_inside_the_bound(monkeypatch):
    eng, watcher, run = _passed(monkeypatch, raise_on_passes=(1,))
    assert run(300) == []
    assert PASSED_BAR_KEY not in watcher._processed
    opened = run(600)
    assert len(opened) == 1
    assert PASSED_BAR_KEY in watcher._processed


def test_passed_side_that_raised_is_not_opened_after_the_bound(monkeypatch):
    eng, watcher, run = _passed(monkeypatch, raise_on_passes=(1,))
    assert run(300) == []
    assert PASSED_BAR_KEY not in watcher._processed
    assert run(1200) == []
    assert watcher.last_skip == "BTCUSDT:late_tip"
    assert PASSED_BAR_KEY in watcher._processed
    assert eng.status()["open_count"] == 0


def test_passed_side_still_raising_after_the_bound_is_not_retried_forever(monkeypatch):
    eng, watcher, run = _passed(monkeypatch, raise_on_passes=(1, 2))
    assert run(300) == []
    assert PASSED_BAR_KEY not in watcher._processed
    assert run(1200) == []
    assert PASSED_BAR_KEY in watcher._processed
    assert run(1260) == []
    assert watcher.last_skip == "BTCUSDT:already_processed"
    assert eng.status()["open_count"] == 0


def test_passed_force_is_not_bounded(monkeypatch):
    eng, watcher, run = _passed(monkeypatch)
    assert len(run(1200, force=True)) == 1
