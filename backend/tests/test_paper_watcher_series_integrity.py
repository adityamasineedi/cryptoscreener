"""The passed-book, V2.1-A and v2 candidate watchers refuse a faulty 1h/4h series.

Same gate as the v1 watcher (``series_integrity_fault``). Real BTCUSDT 1h/4h
candles; the evaluator and the paper engine are stubs. Nothing here reads the
database, the network, or the wall clock.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.passed_paper_watcher as passed_mod
import app.services.v1_paper_watcher as v1_mod
from app.research.dynamic_candidate_constants import MAX_OVERRIDE_RISK
from app.services import ohlcv_store as store_mod
from app.services.passed_paper_watcher import PassedPaperWatcher
from app.services.v1_paper_watcher import MAX_OPEN_LAG_SECONDS, series_integrity_fault
from app.services.v21a_paper_validation import reset_v21a_validation_monitor_for_tests
from app.services.v21a_paper_watcher import (
    V21APaperWatcher,
    reset_v21a_paper_watcher_for_tests,
)
from app.services.v2_candidate_paper_watcher import V2CandidatePaperWatcher

# These tests observe the passed watcher's gates through an open, so they run
# with the regime book open. Production keeps it closed.
pytestmark = pytest.mark.usefixtures("passed_book_open")

SYMBOL = "BTCUSDT"
STORE = 500
FIXTURES = Path(__file__).parent / "fixtures"
H1 = timedelta(hours=1)
H4 = timedelta(hours=4)
SIDE = "COMBO_01"


def _load(tf: str) -> list[dict[str, Any]]:
    raw = json.loads(
        (FIXTURES / f"btcusdt_{tf}_audit_2026-10-12.json").read_text(encoding="utf-8")
    )
    return [
        {
            "time": datetime.fromisoformat(t),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
        }
        for t, o, h, l, c, v in raw["candles"]
    ]


@pytest.fixture(scope="module")
def h1() -> list[dict[str, Any]]:
    """The 500-bar store window ending on the last 1h bar."""
    return _load("1h")[-STORE:]


@pytest.fixture(scope="module")
def h4(h1) -> list[dict[str, Any]]:
    """4h bars fully closed when the 1h tip closes."""
    decision = h1[-1]["time"] + H1
    return [c for c in _load("4h") if c["time"] + H4 <= decision]


def _m15(tip_1h: dict[str, Any], n: int = 120) -> list[dict[str, Any]]:
    """``n`` flat 15m bars ending at the 1h tip's close."""
    last = tip_1h["time"] + timedelta(minutes=45)
    return [
        {
            "time": last - timedelta(minutes=15 * (n - 1 - i)),
            "open": tip_1h["close"],
            "high": tip_1h["close"],
            "low": tip_1h["close"],
            "close": tip_1h["close"],
            "volume": 1.0,
        }
        for i in range(n)
    ]


def _gapped(h1: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return h1[:-5] + h1[-4:]


def _out_of_order_tip(h1: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return h1[:-1] + [h1[-2]]


def _trade_roll_tip(h1: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return h1[:-1] + [{**h1[-1], "source": "binance_trade_roll"}]


FAULTS = [
    (_gapped, "1h_gap"),
    (_out_of_order_tip, "1h_out_of_order"),
    (_trade_roll_tip, "1h_tip_unconfirmed"),
]


def test_fixture_window_is_clean(h1, h4):
    assert series_integrity_fault(h1, h4) is None
    for build, fault in FAULTS:
        assert series_integrity_fault(build(h1), h4) == fault


# --- passed cap-book watcher --------------------------------------------------


class _StubPaper:
    """Records each open it is asked for. Never opens anything real."""

    def __init__(self) -> None:
        self.opens: list[dict[str, Any]] = []

    def open_passed_position(self, **kwargs: Any) -> Any:
        self.opens.append(kwargs)
        return SimpleNamespace(symbol=kwargs["symbol"], status="OPEN")

    def open_symbols(self, stream: str | None = None) -> set[str]:
        return set()


@pytest.fixture
def passed(monkeypatch, h1):
    """Watcher whose COMBO_01 SHORT side wants to enter; 10 minutes after the close."""
    paper = _StubPaper()
    watcher = PassedPaperWatcher(enabled=True, paper_engine=paper)
    # Seeded: the first-sight rule reads the real wall clock and is not under test.
    watcher._seeded.add(SYMBOL)
    evals: list[dict[str, Any]] = []

    def _fake_eval(**kwargs):
        evals.append(kwargs)
        if kwargs["combination"].combination_id != SIDE:
            return {"status": "NO_SETUP"}
        return {"status": "SHORT_ENTRY_CANDIDATE", "entry_price": 100.0,
                "stop_price": 102.0, "tp1": 96.0}

    monkeypatch.setattr(passed_mod, "evaluate_combination_at_bar", _fake_eval)
    clock = SimpleNamespace(seconds=600.0)
    monkeypatch.setattr(
        v1_mod,
        "_utc_now",
        lambda: h1[-1]["time"] + H1 + timedelta(seconds=clock.seconds),
    )

    def _run(series_1h, series_4h, *, force: bool = False) -> list[Any]:
        return watcher.on_closed_1h(
            SYMBOL,
            candles_1h=series_1h,
            candles_4h=series_4h,
            candles_15m=_m15(h1[-1]),
            market_direction="BEARISH",
            force=force,
            # The causal fill bar: the bar after the tip, opening at the plan.
            next_bar={"time": series_1h[-1]["time"] + H1, "open": 100.0},
        )

    return SimpleNamespace(paper=paper, watcher=watcher, evals=evals, run=_run, clock=clock)


@pytest.mark.parametrize("build,fault", FAULTS)
def test_passed_faulty_series_opens_nothing_and_is_not_consumed(passed, h1, h4, build, fault):
    assert passed.run(build(h1), h4) == []
    assert passed.watcher.last_skip == f"{SYMBOL}:data_integrity:{fault}"
    assert passed.evals == []
    assert passed.paper.opens == []
    assert passed.watcher._processed == set()
    assert passed.watcher.opened_count == 0


@pytest.mark.parametrize("build,fault", FAULTS)
def test_passed_force_does_not_bypass_the_gate(passed, h1, h4, build, fault):
    assert passed.run(build(h1), h4, force=True) == []
    assert passed.watcher.last_skip == f"{SYMBOL}:data_integrity:{fault}"
    assert passed.evals == []
    assert passed.paper.opens == []


def test_passed_4h_faults_are_refused_too(passed, h1, h4):
    assert passed.run(h1, h4[:-1]) == []
    assert passed.watcher.last_skip == f"{SYMBOL}:data_integrity:4h_tip_behind"
    forming = {**h4[-1], "time": h4[-1]["time"] + H4}
    assert passed.run(h1, h4 + [forming]) == []
    assert passed.watcher.last_skip == f"{SYMBOL}:data_integrity:4h_tip_not_closed"
    assert passed.evals == []
    assert passed.paper.opens == []
    assert passed.watcher._processed == set()


def test_passed_contiguous_closed_series_reaches_evaluation_and_opens(passed, h1, h4):
    opened = passed.run(h1, h4)
    assert len(opened) == 1
    assert [(o["combination_id"], o["direction"]) for o in passed.paper.opens] == [(SIDE, "SHORT")]
    tip_iso = h1[-1]["time"].isoformat()
    assert passed.paper.opens[0]["setup_bar_time_utc"] == tip_iso
    assert f"{SYMBOL}|{tip_iso}" in passed.watcher._processed
    # Every 1h side the BEARISH trend allows was evaluated on the series that passed the gate.
    allowed = passed_mod.allowed_passed_sides("BEARISH")
    assert len(passed.evals) == sum(1 for _c, tf, _d in allowed if tf == "1h") == 9
    assert all(call["candles_1h"] == h1 and call["candles_4h"] == h4 for call in passed.evals)


def test_passed_same_bar_is_retried_once_the_series_heals(passed, h1, h4):
    tip_key = f"{SYMBOL}|{h1[-1]['time'].isoformat()}"
    assert passed.run(_trade_roll_tip(h1), h4) == []
    assert tip_key not in passed.watcher._processed
    # The exchange kline replaced the rolled bar.
    assert len(passed.run(h1, h4)) == 1
    assert tip_key in passed.watcher._processed
    # And it is not opened twice.
    assert passed.run(h1, h4) == []
    assert passed.watcher.last_skip == f"{SYMBOL}:already_processed"
    assert len(passed.paper.opens) == 1


def test_passed_bar_healed_after_the_open_bound_is_not_opened(passed, h1, h4):
    tip_key = f"{SYMBOL}|{h1[-1]['time'].isoformat()}"
    assert passed.run(_gapped(h1), h4) == []
    assert tip_key not in passed.watcher._processed
    passed.clock.seconds = MAX_OPEN_LAG_SECONDS + 300
    assert passed.run(h1, h4) == []
    assert passed.watcher.last_skip == f"{SYMBOL}:late_tip"
    assert tip_key in passed.watcher._processed
    assert passed.paper.opens == []


def test_passed_live_store_series_is_gated(passed, monkeypatch, h1, h4):
    """The orchestrator path: no series passed in, everything read from the store."""
    store: dict[str, list[dict[str, Any]]] = {
        "1h": _gapped(h1),
        "4h": h4,
        "15m": _m15(h1[-1]),
    }
    monkeypatch.setattr(
        store_mod.ohlcv_store,
        "get_candles_for_engine",
        lambda _sym, tf, include_open=False: list(store[tf]),
    )
    # The forming bar after the tip is the causal fill bar.
    monkeypatch.setattr(
        store_mod.ohlcv_store,
        "get_open",
        lambda _sym, _tf: SimpleNamespace(open_time=h1[-1]["time"] + H1, open=100.0),
    )
    watcher = passed.watcher
    assert watcher.on_closed_1h(SYMBOL, market_direction="BEARISH") == []
    assert watcher.last_skip == f"{SYMBOL}:data_integrity:1h_gap"
    assert passed.evals == []
    assert watcher._processed == set()

    store["1h"] = h1
    assert len(watcher.on_closed_1h(SYMBOL, market_direction="BEARISH")) == 1
    assert len(passed.paper.opens) == 1


# --- V2.1-A watcher -----------------------------------------------------------


class _NoPaper:
    """Any use of the paper engine fails the test."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"paper engine touched: {name}")


@pytest.fixture
def v21a(monkeypatch):
    reset_v21a_paper_watcher_for_tests()
    reset_v21a_validation_monitor_for_tests()
    watcher = V21APaperWatcher(
        enabled=True,
        symbols=[SYMBOL],
        paper_engine=_NoPaper(),
        replay_mode=True,
        emit_alerts=False,
        risk_percent=0.02,
    )
    evals: list[dict[str, Any]] = []

    def _fake_eval(**kwargs):
        evals.append(kwargs)
        return {"status": "NO_SETUP", "reason": "stub"}

    monkeypatch.setattr(watcher, "evaluate_at_bar", _fake_eval)
    yield SimpleNamespace(watcher=watcher, evals=evals)
    reset_v21a_paper_watcher_for_tests()
    reset_v21a_validation_monitor_for_tests()


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("build,fault", FAULTS)
def test_v21a_faulty_series_is_not_evaluated_or_watermarked(v21a, h1, h4, build, fault, force):
    watcher = v21a.watcher
    pos = watcher.on_closed_1h(
        SYMBOL,
        candles_1h=build(h1),
        candles_4h=h4,
        candles_15m=_m15(h1[-1]),
        force=force,
    )
    assert pos is None
    assert watcher._last_skip == f"{SYMBOL}:data_integrity:{fault}"
    assert v21a.evals == []
    assert watcher._processed_bars == set()
    assert watcher._watermarks == {}
    assert watcher._processed_signals == set()


def test_v21a_contiguous_closed_series_reaches_evaluation_after_a_fault(v21a, h1, h4):
    watcher = v21a.watcher
    m15 = _m15(h1[-1])
    assert (
        watcher.on_closed_1h(
            SYMBOL, candles_1h=_trade_roll_tip(h1), candles_4h=h4, candles_15m=m15
        )
        is None
    )
    assert v21a.evals == []
    assert watcher.on_closed_1h(SYMBOL, candles_1h=h1, candles_4h=h4, candles_15m=m15) is None
    assert len(v21a.evals) == 1
    assert watcher._last_skip == f"{SYMBOL}:no_entry:stub"
    assert watcher._watermarks == {SYMBOL: h1[-1]["time"].isoformat()}
    assert len(watcher._processed_bars) == 1


# --- v2 candidate watcher -----------------------------------------------------


@pytest.fixture
def v2():
    watcher = V2CandidatePaperWatcher(
        enabled=True, replay_mode=True, emit_alerts=False, paper_engine=_NoPaper()
    )
    evals: list[dict[str, Any]] = []

    async def _eligible() -> list[dict[str, Any]]:
        return [
            {
                "symbol": SYMBOL,
                "risk_percent": MAX_OVERRIDE_RISK,
                "operator_approved": True,
                "telegram_eligible": False,
                "production_approved": False,
            }
        ]

    def _fake_eval(*_a: Any, **kwargs: Any) -> dict[str, Any]:
        evals.append(kwargs)
        return {"status": "NO_SETUP"}

    watcher.eligible_symbols = _eligible  # type: ignore[method-assign]
    watcher.evaluate_closed_bar = _fake_eval  # type: ignore[method-assign]
    return SimpleNamespace(watcher=watcher, evals=evals)


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("build,fault", FAULTS)
async def test_v2_faulty_series_is_not_evaluated_or_marked_processed(v2, h1, h4, build, fault, force):
    watcher = v2.watcher
    pos = await watcher.on_closed_1h(SYMBOL, candles_1h=build(h1), candles_4h=h4, force=force)
    assert pos is None
    assert watcher._last_skip == f"{SYMBOL}:data_integrity:{fault}"
    assert v2.evals == []
    assert watcher._processed_bars == set()


@pytest.mark.asyncio
async def test_v2_contiguous_closed_series_reaches_evaluation_after_a_fault(v2, h1, h4):
    watcher = v2.watcher
    assert await watcher.on_closed_1h(SYMBOL, candles_1h=_gapped(h1), candles_4h=h4) is None
    assert v2.evals == []
    assert await watcher.on_closed_1h(SYMBOL, candles_1h=h1, candles_4h=h4) is None
    assert len(v2.evals) == 1
    assert watcher._last_skip == f"{SYMBOL}:no_long_entry:NO_SETUP"
    assert watcher._processed_bars == {f"{SYMBOL}|1h|{h1[-1]['time'].isoformat()}"}
    # Same bar again is deduped, not re-evaluated.
    assert await watcher.on_closed_1h(SYMBOL, candles_1h=h1, candles_4h=h4) is None
    assert watcher._last_skip == f"{SYMBOL}:already_processed"
    assert len(v2.evals) == 1
