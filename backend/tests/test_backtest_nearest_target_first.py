"""The legacy walk exits at the nearest target the bar reached, not the first slot.

Target slots can be out of distance order. Synthetic candles, no database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.research import combination_backtest as cb
from app.research.bos_combinations import CombinationDefinition
from app.research.combination_backtest import (
    _bar_hits_levels,
    resolve_intrabar_outcome,
    simulate_research_trade,
)
from app.research.config import AMBIGUOUS_CONSERVATIVE
from app.research.schemas import ResearchTrade

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

# LONG entry 100 / stop 99; SHORT entry 100 / stop 101.
LONG_UNORDERED = [107.0, 103.0, 104.0]
SHORT_UNORDERED = [93.0, 97.0, 96.0]
LONG_ORDERED = [102.0, 103.0, 104.0]
SHORT_ORDERED = [98.0, 97.0, 96.0]


def _slot_order_reference(direction: str, high: float, low: float, targets: list[float]) -> int | None:
    """The previous rule: first slot the bar reached."""
    for i, tp in enumerate(targets):
        if direction == "LONG" and high >= tp:
            return i
        if direction == "SHORT" and low <= tp:
            return i
    return None


def _resolve(direction: str, high: float, low: float, stop: float, targets: list[float]):
    return resolve_intrabar_outcome(
        direction=direction,
        high=high,
        low=low,
        stop=stop,
        targets=targets,
        handling=AMBIGUOUS_CONSERVATIVE,
    )


@pytest.mark.parametrize("high", [103.2, 104.5, 107.5])
def test_long_out_of_order_targets_exit_at_the_lowest_reached(high):
    # 107.5 used to book ("TP1", 107) although price passed 103 first.
    assert _bar_hits_levels(
        direction="LONG", high=high, low=99.5, stop=99.0, targets=LONG_UNORDERED
    ) == (False, True, 1)
    assert _resolve("LONG", high, 99.5, 99.0, LONG_UNORDERED) == ("TP2", 103.0, False)


@pytest.mark.parametrize("low", [96.8, 95.5, 92.5])
def test_short_out_of_order_targets_exit_at_the_highest_reached(low):
    assert _bar_hits_levels(
        direction="SHORT", high=100.5, low=low, stop=101.0, targets=SHORT_UNORDERED
    ) == (False, True, 1)
    assert _resolve("SHORT", 100.5, low, 101.0, SHORT_UNORDERED) == ("TP2", 97.0, False)


def test_label_is_the_slot_actually_exited_at():
    assert _resolve("LONG", 110.0, 99.5, 99.0, [107.0, 104.0, 103.0]) == ("TP3", 103.0, False)
    assert _resolve("SHORT", 100.5, 90.0, 101.0, [93.0, 96.0, 97.0]) == ("TP3", 97.0, False)
    # Equal prices keep the lower slot.
    assert _resolve("LONG", 110.0, 99.5, 99.0, [103.0, 103.0, 107.0]) == ("TP1", 103.0, False)


def test_bar_short_of_every_target_resolves_nothing():
    assert _resolve("LONG", 102.9, 99.5, 99.0, LONG_UNORDERED) == (None, None, False)
    assert _resolve("SHORT", 100.5, 97.1, 101.0, SHORT_UNORDERED) == (None, None, False)


@pytest.mark.parametrize("high", [101.9, 102.0, 102.5, 103.0, 103.9, 104.0, 110.0])
def test_long_distance_ordered_targets_match_slot_order(high):
    expected_idx = _slot_order_reference("LONG", high, 99.5, LONG_ORDERED)
    assert expected_idx == (None if high < 102.0 else 0)
    assert _bar_hits_levels(
        direction="LONG", high=high, low=99.5, stop=99.0, targets=LONG_ORDERED
    ) == (False, expected_idx is not None, expected_idx)
    assert _resolve("LONG", high, 99.5, 99.0, LONG_ORDERED) == (
        (None, None, False) if expected_idx is None else ("TP1", 102.0, False)
    )


@pytest.mark.parametrize("low", [98.1, 98.0, 97.5, 97.0, 96.1, 96.0, 90.0])
def test_short_distance_ordered_targets_match_slot_order(low):
    expected_idx = _slot_order_reference("SHORT", 100.5, low, SHORT_ORDERED)
    assert expected_idx == (None if low > 98.0 else 0)
    assert _bar_hits_levels(
        direction="SHORT", high=100.5, low=low, stop=101.0, targets=SHORT_ORDERED
    ) == (False, expected_idx is not None, expected_idx)
    assert _resolve("SHORT", 100.5, low, 101.0, SHORT_ORDERED) == (
        (None, None, False) if expected_idx is None else ("TP1", 98.0, False)
    )


@pytest.mark.parametrize(
    "targets",
    [[102.0], [102.0, 104.0], [102.0, 102.0, 105.0], [101.5, 103.0, 103.0], [100.5, 101.0, 109.0]],
)
def test_any_distance_ordered_long_list_matches_slot_order_on_a_price_sweep(targets):
    for step in range(0, 121):
        high = 99.0 + step * 0.1
        got = _bar_hits_levels(direction="LONG", high=high, low=99.5, stop=99.0, targets=targets)
        assert got[2] == _slot_order_reference("LONG", high, 99.5, targets)
        mirrored = [200.0 - t for t in targets]
        got_short = _bar_hits_levels(
            direction="SHORT", high=100.5, low=200.0 - high, stop=101.0, targets=mirrored
        )
        assert got_short[2] == _slot_order_reference("SHORT", 100.5, 200.0 - high, mirrored)


@pytest.mark.parametrize(
    "direction,high,low,stop,targets",
    [
        ("LONG", 107.5, 98.9, 99.0, LONG_UNORDERED),
        ("LONG", 102.5, 99.0, 99.0, LONG_ORDERED),
        ("SHORT", 101.1, 92.5, 101.0, SHORT_UNORDERED),
        ("SHORT", 101.0, 97.5, 101.0, SHORT_ORDERED),
    ],
)
def test_stop_and_target_in_one_bar_is_still_the_stop(direction, high, low, stop, targets):
    assert _resolve(direction, high, low, stop, targets) == ("SL", stop, True)


def test_stop_alone_is_unchanged():
    assert _resolve("LONG", 100.5, 98.9, 99.0, LONG_UNORDERED) == ("SL", 99.0, False)
    assert _resolve("SHORT", 101.1, 99.5, 101.0, SHORT_UNORDERED) == ("SL", 101.0, False)


def _bars(n: int, overrides: dict[int, tuple[float, float]]) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        high, low = overrides.get(i, (100.2, 99.8))
        out.append(
            {"time": T0 + timedelta(hours=i), "open": 100.0, "high": high, "low": low, "close": 100.0, "volume": 1000.0}
        )
    return out


def _trade(direction: str, stop: float, targets: list[float], entry_index: int = 0) -> ResearchTrade:
    return ResearchTrade(
        symbol="TESTUSDT",
        timeframe="1h",
        combination_id="TEST",
        entry_index=entry_index,
        signal_time=None,
        direction=direction,
        entry_price=100.0,
        stop_price=stop,
        tp1=targets[0],
        tp2=targets[1],
        tp3=targets[2],
        rr=None,
    )


def test_simulate_long_out_of_order_exits_at_the_nearest_and_times_only_that_level():
    t = simulate_research_trade(
        _trade("LONG", 99.0, LONG_UNORDERED), _bars(5, {2: (107.5, 99.8)})
    )
    assert (t.outcome, t.exit_price, t.exit_index) == ("TP2", 103.0, 2)
    assert t.r_multiple == pytest.approx(3.0)  # was +7R at the tp1 slot
    assert t.holding_bars == 2
    assert t.time_to_tp2 == 2
    assert t.time_to_tp1 is None
    assert t.time_to_tp3 is None
    assert t.time_to_sl is None


def test_simulate_long_tp1_price_never_touched_leaves_time_to_tp1_unset():
    t = simulate_research_trade(
        _trade("LONG", 99.0, LONG_UNORDERED), _bars(5, {2: (103.2, 99.8)})
    )
    assert (t.outcome, t.exit_price, t.exit_index) == ("TP2", 103.0, 2)
    assert t.time_to_tp1 is None
    assert t.time_to_tp2 == 2


def test_simulate_short_out_of_order_exits_at_the_nearest():
    t = simulate_research_trade(
        _trade("SHORT", 101.0, SHORT_UNORDERED), _bars(5, {3: (100.2, 92.5)})
    )
    assert (t.outcome, t.exit_price, t.exit_index) == ("TP2", 97.0, 3)
    assert t.r_multiple == pytest.approx(3.0)
    assert (t.time_to_tp1, t.time_to_tp2, t.time_to_tp3) == (None, 3, None)


def test_simulate_ordered_targets_are_unchanged():
    t = simulate_research_trade(
        _trade("LONG", 99.0, LONG_ORDERED), _bars(5, {2: (110.0, 99.8)})
    )
    assert (t.outcome, t.exit_price, t.exit_index) == ("TP1", 102.0, 2)
    assert t.r_multiple == pytest.approx(2.0)
    assert (t.time_to_tp1, t.time_to_tp2, t.time_to_tp3) == (2, None, None)


def test_simulate_stop_and_target_in_one_bar_is_the_stop():
    t = simulate_research_trade(
        _trade("LONG", 99.0, LONG_UNORDERED), _bars(5, {1: (107.5, 98.9)})
    )
    assert (t.outcome, t.exit_price, t.ambiguous) == ("SL", 99.0, True)
    assert t.r_multiple == pytest.approx(-1.0)
    assert (t.time_to_sl, t.time_to_tp1, t.time_to_tp2) == (1, None, None)


FILL = 60
COMBO = CombinationDefinition(
    combination_id="TEST_NEAREST_TARGET",
    name="TEST_NEAREST_TARGET",
    description="canned setup",
    require_bos=False,
)


def _walk(monkeypatch, direction: str, stop: float, targets: list[float], overrides):
    def _fake_eval(**kwargs: Any) -> dict[str, Any]:
        if int(kwargs["as_of_index"]) != FILL:
            return {"status": "NO_SETUP", "reason": "canned"}
        return {
            "status": f"{direction}_ENTRY_CANDIDATE",
            "direction": direction,
            "entry_price": 100.0,
            "entry_type": "MARKET",
            "stop_price": stop,
            "tp1": targets[0],
            "tp2": targets[1],
            "tp3": targets[2],
        }

    monkeypatch.setattr(cb, "evaluate_combination_at_bar", _fake_eval)
    out = cb.run_combination_backtest("TESTUSDT", "1h", _bars(70, overrides), COMBO)
    assert out["status"] == "OK"
    (t,) = out["trades"]
    return t


def test_walk_manager_long_out_of_order_exits_at_the_nearest(monkeypatch):
    t = _walk(monkeypatch, "LONG", 99.0, LONG_UNORDERED, {FILL + 2: (107.5, 99.8)})
    assert (t["outcome"], t["exit_price"], t["exit_index"]) == ("TP2", 103.0, FILL + 2)
    assert t["r_multiple"] == pytest.approx(3.0)
    assert (t["time_to_tp1"], t["time_to_tp2"], t["time_to_tp3"]) == (None, 2, None)
    assert (t["tp1"], t["tp2"], t["tp3"]) == (107.0, 103.0, 104.0)  # values untouched


def test_walk_manager_short_out_of_order_exits_at_the_nearest(monkeypatch):
    t = _walk(monkeypatch, "SHORT", 101.0, SHORT_UNORDERED, {FILL + 2: (100.2, 92.5)})
    assert (t["outcome"], t["exit_price"], t["exit_index"]) == ("TP2", 97.0, FILL + 2)
    assert t["r_multiple"] == pytest.approx(3.0)
    assert (t["time_to_tp1"], t["time_to_tp2"], t["time_to_tp3"]) == (None, 2, None)


def test_walk_manager_ordered_targets_are_unchanged(monkeypatch):
    t = _walk(monkeypatch, "LONG", 99.0, LONG_ORDERED, {FILL + 2: (110.0, 99.8)})
    assert (t["outcome"], t["exit_price"], t["exit_index"]) == ("TP1", 102.0, FILL + 2)
    assert t["r_multiple"] == pytest.approx(2.0)
    assert (t["time_to_tp1"], t["time_to_tp2"], t["time_to_tp3"]) == (2, None, None)


def test_walk_manager_stop_and_target_in_one_bar_is_the_stop(monkeypatch):
    t = _walk(monkeypatch, "LONG", 99.0, LONG_UNORDERED, {FILL + 1: (107.5, 98.9)})
    assert (t["outcome"], t["exit_price"], t["ambiguous"]) == ("SL", 99.0, True)
    assert t["r_multiple"] == pytest.approx(-1.0)
