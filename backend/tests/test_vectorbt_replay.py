"""VectorBT order replay: same fills, same order inside a bar, overlaps caught."""

from __future__ import annotations

import pytest

pytest.importorskip("vectorbt")

from app.research.vectorbt_replay import ReplayOrder, check_parity  # noqa: E402

FEE = 0.0004


def _order(key, direction, entry_i, exit_i, entry_px, exit_px, *, qty=0.5, outcome="TP1"):
    stop = entry_px * (0.99 if direction == "LONG" else 1.01)
    risk = abs(entry_px - stop) * qty
    gross = qty * ((exit_px - entry_px) if direction == "LONG" else (entry_px - exit_px))
    fees = qty * entry_px * FEE + qty * exit_px * FEE
    return ReplayOrder(
        trade_key=key,
        direction=direction,
        entry_index=entry_i,
        exit_index=exit_i,
        entry_price=entry_px,
        exit_price=exit_px,
        qty=qty,
        entry_fee_rate=FEE,
        exit_fee_rate=FEE,
        risk_usd=risk,
        stop=stop,
        target=entry_px * 1.02,
        outcome=outcome,
        net_r=(gross - fees) / risk,
    )


def test_long_short_and_same_bar_sequences_match():
    orders = [
        _order("a", "LONG", 5, 9, 100.0, 102.0),
        # enters on the bar ``a`` exits on, and exits on its own entry bar
        _order("b", "SHORT", 9, 9, 101.0, 102.0, outcome="SL"),
        _order("c", "SHORT", 10, 12, 101.5, 99.0),
        _order("d", "LONG", 15, 15, 100.0, 99.0, outcome="SL"),
    ]
    res = check_parity(orders)
    assert res.passed, res.to_dict()
    assert res.bar_loop_trades == res.vectorbt_trades == 4
    assert [t.outcome for t in res.replayed] == ["TP1", "SL", "TP1", "SL"]


def test_overlapping_positions_are_a_mismatch():
    orders = [
        _order("a", "LONG", 5, 9, 100.0, 102.0),
        _order("b", "LONG", 7, 11, 101.0, 103.0),
    ]
    res = check_parity(orders)
    assert not res.passed
    assert res.mismatches == 2
    assert res.first_mismatch_field == "entry_price"


def test_wrong_engine_net_r_is_caught():
    good = _order("a", "LONG", 5, 9, 100.0, 102.0)
    bad = ReplayOrder(**{**good.__dict__, "net_r": good.net_r + 0.01})
    res = check_parity([bad])
    assert res.mismatches == 1
    assert res.first_mismatch_field == "net_r"


def test_invalid_order_is_a_recorded_failure_not_an_exception():
    bad = ReplayOrder(**{**_order("a", "LONG", 5, 9, 100.0, 102.0).__dict__, "qty": 0.0})
    res = check_parity([bad])
    assert not res.passed
    assert res.first_mismatch_field == "vectorbt_error"
    assert "qty" in (res.error or "")


def test_empty_trade_list_passes_trivially():
    res = check_parity([])
    assert res.passed and res.bar_loop_trades == 0 and res.vectorbt_trades == 0
