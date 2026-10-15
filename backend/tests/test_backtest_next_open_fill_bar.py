"""A next-open entry is tested against its own fill bar; close-of-bar entries are not.

Drives the real walk on synthetic candles with a canned setup. No database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.research import combination_backtest as cb
from app.research.bos_combinations import CombinationDefinition

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
FILL = 60  # bar the canned setup enters on
N_BARS = 70

COMBO = CombinationDefinition(
    combination_id="TEST_FILL_BAR",
    name="TEST_FILL_BAR",
    description="canned setup",
    require_bos=False,
)

# Canned LONG plan: entry 100, stop 99 (risk 1), tp1 102 (+2R).
ENTRY, STOP, TP1 = 100.0, 99.0, 102.0
FLAT = (100.0, 100.2, 99.8, 100.0)  # open, high, low, close — touches nothing
PIERCE_STOP = (100.0, 100.2, 98.5, 99.5)
REACH_TP = (100.0, 102.5, 99.8, 102.0)
REACH_BOTH = (100.0, 102.5, 98.5, 100.0)
DIP_NO_STOP = (100.0, 100.2, 99.5, 100.0)


def _mirror(bar: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Reflect a bar about 100 so a LONG scenario becomes its SHORT twin."""
    o, h, l, c = bar
    return (200.0 - o, 200.0 - l, 200.0 - h, 200.0 - c)


def _candles(overrides: dict[int, tuple[float, float, float, float]], direction: str) -> list[dict[str, Any]]:
    out = []
    for i in range(N_BARS):
        bar = overrides.get(i, FLAT)
        o, h, l, c = bar if direction == "LONG" else _mirror(bar)
        out.append(
            {"time": T0 + timedelta(hours=i), "open": o, "high": h, "low": l, "close": c, "volume": 1000.0}
        )
    return out


def _run(
    monkeypatch,
    *,
    direction: str,
    entry_type: str,
    overrides: dict[int, tuple[float, float, float, float]],
    setup_bars: tuple[int, ...] = (FILL,),
) -> tuple[dict[str, Any], list[int]]:
    evaluated: list[int] = []
    px = (lambda p: p) if direction == "LONG" else (lambda p: 200.0 - p)

    def _fake_eval(**kwargs: Any) -> dict[str, Any]:
        i = int(kwargs["as_of_index"])
        evaluated.append(i)
        if i not in setup_bars:
            return {"status": "NO_SETUP", "reason": "canned"}
        return {
            "status": f"{direction}_ENTRY_CANDIDATE",
            "direction": direction,
            "entry_price": px(ENTRY),
            "entry_type": entry_type,
            "stop_price": px(STOP),
            "tp1": px(TP1),
        }

    monkeypatch.setattr(cb, "evaluate_combination_at_bar", _fake_eval)
    out = cb.run_combination_backtest("TESTUSDT", "1h", _candles(overrides, direction), COMBO)
    assert out["status"] == "OK"
    return out, evaluated


DIRECTIONS = pytest.mark.parametrize("direction", ["LONG", "SHORT"])


@DIRECTIONS
def test_next_open_fill_bar_piercing_the_stop_is_a_same_bar_stop(monkeypatch, direction):
    out, _ = _run(
        monkeypatch,
        direction=direction,
        entry_type="NEXT_OPEN",
        overrides={FILL: PIERCE_STOP, FILL + 1: REACH_TP},
    )
    (t,) = out["trades"]
    assert (t["entry_index"], t["exit_index"], t["outcome"]) == (FILL, FILL, "SL")
    assert t["exit_price"] == (STOP if direction == "LONG" else 200.0 - STOP)
    assert t["r_multiple"] == pytest.approx(-1.0)  # not the later +2R
    assert t["ambiguous"] is False
    assert t["exit_time"] == (T0 + timedelta(hours=FILL)).isoformat()
    # Same-bar exit: zero bars held, by the walk's i - entry_index formula.
    assert t["holding_bars"] == 0
    assert t["time_to_sl"] == 0
    # The fill bar's adverse wick (1.5 against entry) is in MAE.
    assert t["mae"] == pytest.approx(1.5)
    assert t["mae_r"] == pytest.approx(1.5)
    assert out["result"]["sample_size"] == 1


@DIRECTIONS
def test_next_open_fill_bar_reaching_tp1_only_is_a_same_bar_target(monkeypatch, direction):
    out, _ = _run(
        monkeypatch, direction=direction, entry_type="NEXT_OPEN", overrides={FILL: REACH_TP}
    )
    (t,) = out["trades"]
    assert (t["entry_index"], t["exit_index"], t["outcome"]) == (FILL, FILL, "TP1")
    assert t["exit_price"] == (TP1 if direction == "LONG" else 200.0 - TP1)
    assert t["r_multiple"] == pytest.approx(2.0)
    assert t["ambiguous"] is False
    assert (t["holding_bars"], t["time_to_tp1"], t["time_to_sl"]) == (0, 0, None)
    assert t["mfe"] == pytest.approx(2.5)


@DIRECTIONS
def test_next_open_fill_bar_reaching_both_is_stop_first_and_ambiguous(monkeypatch, direction):
    out, _ = _run(
        monkeypatch, direction=direction, entry_type="NEXT_OPEN", overrides={FILL: REACH_BOTH}
    )
    (t,) = out["trades"]
    assert (t["entry_index"], t["exit_index"], t["outcome"]) == (FILL, FILL, "SL")
    assert t["r_multiple"] == pytest.approx(-1.0)
    assert t["ambiguous"] is True


@DIRECTIONS
def test_next_open_fill_bar_touching_neither_exits_later_as_before(monkeypatch, direction):
    out, _ = _run(
        monkeypatch,
        direction=direction,
        entry_type="NEXT_OPEN",
        overrides={FILL: DIP_NO_STOP, FILL + 2: REACH_TP},
    )
    (t,) = out["trades"]
    # Hand-computed: nothing resolves on bars 60-61, bar 62 reaches tp1 only.
    assert (t["entry_index"], t["exit_index"], t["outcome"]) == (FILL, FILL + 2, "TP1")
    assert t["r_multiple"] == pytest.approx(2.0)
    assert (t["holding_bars"], t["time_to_tp1"]) == (2, 2)
    # The fill bar's 0.5 dip is the deepest adverse move and is now counted.
    assert t["mae"] == pytest.approx(0.5)


@DIRECTIONS
@pytest.mark.parametrize("entry_type", ["MARKET", "LIMIT_RETEST", None])
def test_close_of_bar_entry_does_not_test_its_fill_bar(monkeypatch, direction, entry_type):
    """Same candles as the same-bar stop case: the range precedes a close fill."""
    out, _ = _run(
        monkeypatch,
        direction=direction,
        entry_type=entry_type,
        overrides={FILL: PIERCE_STOP, FILL + 1: REACH_TP},
    )
    (t,) = out["trades"]
    assert (t["entry_index"], t["exit_index"], t["outcome"]) == (FILL, FILL + 1, "TP1")
    assert t["r_multiple"] == pytest.approx(2.0)
    assert (t["holding_bars"], t["time_to_tp1"], t["time_to_sl"]) == (1, 1, None)
    # Bar 60's 98.5 wick is not in the excursions; bar 61's 99.8 low is.
    assert t["mae"] == pytest.approx(0.2)


@DIRECTIONS
def test_close_of_bar_entry_with_both_levels_in_fill_bar_stays_open(monkeypatch, direction):
    out, _ = _run(
        monkeypatch, direction=direction, entry_type="MARKET", overrides={FILL: REACH_BOTH}
    )
    (t,) = out["trades"]
    assert (t["entry_index"], t["exit_index"], t["outcome"]) == (FILL, None, "OPEN")


@DIRECTIONS
def test_same_bar_exit_opens_no_second_trade_on_that_bar_and_frees_the_next(monkeypatch, direction):
    out, evaluated = _run(
        monkeypatch,
        direction=direction,
        entry_type="NEXT_OPEN",
        overrides={FILL: PIERCE_STOP, FILL + 2: REACH_TP},
        setup_bars=(FILL, FILL + 1),
    )
    first, second = out["trades"]
    assert (first["entry_index"], first["exit_index"], first["outcome"]) == (FILL, FILL, "SL")
    # The fill bar was evaluated once; the next bar is flat again and evaluated.
    assert evaluated.count(FILL) == 1
    assert evaluated.count(FILL + 1) == 1
    assert (second["entry_index"], second["exit_index"], second["outcome"]) == (
        FILL + 1,
        FILL + 2,
        "TP1",
    )
    assert second["r_multiple"] == pytest.approx(2.0)
    # The fill bar keeps its entry record; it is not relabelled a position bar.
    diag = out["entry_diagnostics"]
    assert diag[FILL]["status"] == f"{direction}_ENTRY_CANDIDATE"
    assert diag[FILL + 1]["status"] == f"{direction}_ENTRY_CANDIDATE"
    assert diag[FILL + 2]["status"] == "POSITION_ACTIVE"


@DIRECTIONS
def test_close_of_bar_entry_blocks_the_next_bar_as_before(monkeypatch, direction):
    out, evaluated = _run(
        monkeypatch,
        direction=direction,
        entry_type="MARKET",
        overrides={FILL: PIERCE_STOP, FILL + 2: REACH_TP},
        setup_bars=(FILL, FILL + 1),
    )
    (t,) = out["trades"]
    assert (t["entry_index"], t["exit_index"], t["outcome"]) == (FILL, FILL + 2, "TP1")
    assert FILL + 1 not in evaluated  # position open: managed, not evaluated
