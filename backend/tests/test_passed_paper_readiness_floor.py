"""The cap-book paper watcher waits for the catch-up history size.

A bar is evaluated only once the store holds 250 closed 1h bars, 80 closed 4h
bars and 100 closed 15m bars. Clocks are controlled; the paper engine is a stub.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.passed_paper_watcher as watcher_mod
import app.services.v1_paper_watcher as v1_mod
from app.services.passed_paper_watcher import (
    MIN_CLOSED_15M_BARS,
    MIN_CLOSED_1H_BARS,
    MIN_CLOSED_4H_BARS,
    PassedPaperWatcher,
)

# These tests observe the passed watcher's gates through an open, so they run
# with the regime book open. Production keeps it closed.
pytestmark = pytest.mark.usefixtures("passed_book_open")

# The 1h tip opens at TIP_OPEN and closes one hour later.
TIP_OPEN = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
TIP_CLOSE = TIP_OPEN + timedelta(hours=1)
BAR_KEY = f"BTCUSDT|{TIP_OPEN.isoformat()}"
# The last 4h bar fully closed at TIP_CLOSE (the 12:00 bar is still forming).
LAST_CLOSED_4H = datetime(2026, 10, 10, 8, tzinfo=timezone.utc)
SIDE = "COMBO_01"


def _bars(n: int, *, minutes: int = 60, last: datetime = TIP_OPEN) -> list[dict[str, Any]]:
    """``n`` closed bars whose last one opens at ``last``."""
    step = timedelta(minutes=minutes)
    return [
        {"time": last - step * (n - 1 - i), "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 1000.0}
        for i in range(n)
    ]


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
def rig(monkeypatch):
    """Watcher whose COMBO_01 SHORT side wants to enter; 10 minutes after the close."""
    paper = _StubPaper()
    watcher = PassedPaperWatcher(enabled=True, paper_engine=paper)
    # Seeded: the first-sight rule reads the real wall clock and is not under test.
    watcher._seeded.add("BTCUSDT")
    evals: list[dict[str, Any]] = []

    def _fake_eval(**kwargs):
        evals.append(kwargs)
        if kwargs["combination"].combination_id != SIDE:
            return {"status": "NO_SETUP"}
        return {"status": "SHORT_ENTRY_CANDIDATE", "entry_price": 100.0,
                "stop_price": 102.0, "tp1": 96.0}

    monkeypatch.setattr(watcher_mod, "evaluate_combination_at_bar", _fake_eval)
    monkeypatch.setattr(v1_mod, "_utc_now", lambda: TIP_CLOSE + timedelta(seconds=600))

    def _run(n_1h: int, n_4h: int, n_15m: int, *, force: bool = False) -> list[Any]:
        return watcher.on_closed_1h(
            "BTCUSDT",
            candles_1h=_bars(n_1h),
            candles_4h=_bars(n_4h, minutes=240, last=LAST_CLOSED_4H),
            candles_15m=_bars(n_15m, minutes=15),
            market_direction="BEARISH",
            force=force,
            # The causal fill bar: the bar after the tip, opening at the plan.
            next_bar={"time": TIP_CLOSE, "open": 100.0},
        )

    return SimpleNamespace(paper=paper, watcher=watcher, evals=evals, run=_run)


def _orchestrator_catchup_targets() -> dict[str, int]:
    """Parse ``targets = (...)`` out of the orchestrator's catch-up routine."""
    import app.engines.orchestrator as orch_mod

    owner = next(
        obj
        for obj in vars(orch_mod).values()
        if inspect.isclass(obj) and "_paper_history_catchup" in vars(obj)
    )
    src = textwrap.dedent(inspect.getsource(owner._paper_history_catchup))
    for node in ast.walk(ast.parse(src)):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "targets"
        ):
            return dict(ast.literal_eval(node.value))
    raise AssertionError("catch-up targets not found in _paper_history_catchup")


def test_floor_is_250_80_100():
    assert (MIN_CLOSED_1H_BARS, MIN_CLOSED_4H_BARS, MIN_CLOSED_15M_BARS) == (250, 80, 100)


def test_floor_equals_the_orchestrator_catchup_targets():
    assert _orchestrator_catchup_targets() == {
        "1h": MIN_CLOSED_1H_BARS,
        "4h": MIN_CLOSED_4H_BARS,
        "15m": MIN_CLOSED_15M_BARS,
    }


@pytest.mark.parametrize("counts", [(249, 80, 100), (250, 79, 100), (250, 80, 99)])
def test_one_bar_short_skips_without_evaluating_or_marking(rig, counts):
    assert rig.run(*counts) == []
    assert rig.watcher.last_skip == "BTCUSDT:ohlcv_catchup"
    assert rig.evals == []
    assert rig.paper.opens == []
    assert rig.watcher._processed == set()


def test_not_ready_symbol_is_not_seeded(rig):
    rig.watcher._seeded.clear()
    assert rig.run(250, 79, 100) == []
    assert rig.watcher.last_skip == "BTCUSDT:ohlcv_catchup"
    assert rig.watcher._seeded == set()
    assert rig.watcher._processed == set()


def test_old_floor_no_longer_evaluates(rig):
    assert rig.run(80, 20, 20) == []
    assert rig.watcher.last_skip == "BTCUSDT:ohlcv_catchup"
    assert rig.evals == []


def test_exactly_at_the_floor_evaluates_and_opens(rig):
    opened = rig.run(250, 80, 100)
    assert len(opened) == 1
    assert [(o["combination_id"], o["direction"]) for o in rig.paper.opens] == [(SIDE, "SHORT")]
    assert rig.paper.opens[0]["setup_bar_time_utc"] == TIP_OPEN.isoformat()
    assert BAR_KEY in rig.watcher._processed
    # The evaluator gets every bar that was supplied, uncapped.
    call = rig.evals[0]
    assert len(call["candles"]) == 250 and call["as_of_index"] == 249
    assert len(call["candles_1h"]) == 250
    assert len(call["candles_4h"]) == 80
    assert len(call["candles_15m"]) == 100


def test_longer_series_are_passed_through_uncapped(rig):
    assert len(rig.run(400, 120, 300)) == 1
    call = rig.evals[0]
    assert (len(call["candles_1h"]), len(call["candles_4h"]), len(call["candles_15m"])) == (
        400, 120, 300,
    )


def test_bar_skipped_as_not_ready_is_evaluated_once_history_arrives(rig):
    assert rig.run(250, 79, 100) == []
    assert rig.watcher.last_skip == "BTCUSDT:ohlcv_catchup"
    assert BAR_KEY not in rig.watcher._processed
    # Same tip, catch-up finished.
    opened = rig.run(250, 80, 100)
    assert len(opened) == 1
    assert BAR_KEY in rig.watcher._processed
    # And it is not opened twice.
    assert rig.run(250, 80, 100) == []
    assert rig.watcher.last_skip == "BTCUSDT:already_processed"


def test_force_bypasses_the_4h_and_15m_floor(rig):
    opened = rig.run(250, 5, 0, force=True)
    assert len(opened) == 1
    assert len(rig.evals[0]["candles_4h"]) == 5
    assert rig.evals[0]["candles_15m"] is None


def test_force_does_not_bypass_the_1h_check(rig):
    assert rig.run(249, 80, 100, force=True) == []
    assert rig.watcher.last_skip == "BTCUSDT:missing_1h"
    assert rig.evals == []
    assert rig.paper.opens == []
    assert rig.watcher._processed == set()
