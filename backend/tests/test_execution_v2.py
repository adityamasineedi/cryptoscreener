"""Simulator v2 (corrected) vs v1 (archived): fee-helper slippage and gap-through-stop fixes."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from app.research.combination_backtest import evaluate_candidate_trades
from app.research.execution_v2 import (
    EXECUTION_MODEL_V2,
    enrich_trade_execution_v2,
    evaluate_candidate_trades_v2,
    resolve_bar_exit_v2,
)
from app.research.schemas import ResearchTrade
from app.research.trade_fees import enrich_trade_execution

SIZING = dict(risk_usd=20.0, taker_fee=0.0004, maker_fee=0.0002, leverage=2.0, account_equity=1000.0)
T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _trade(direction="LONG", entry=100.0, stop=98.0, exit_=104.0):
    return {"direction": direction, "entry_price": entry, "stop_price": stop, "exit_price": exit_, "outcome": "TP1"}


# ---- fix 1: fee helper ----------------------------------------------------------


def test_v1_fee_helper_ignores_entry_slippage_in_gross_v2_does_not():
    slip = 0.0005
    v1 = enrich_trade_execution(_trade(), slippage_rate=slip, **SIZING)
    v2 = enrich_trade_execution_v2(_trade(), slippage_rate=slip, **SIZING)
    qty = v1["qty"]
    fill_in, fill_out = 100.0 * (1 + slip), 104.0 * (1 - slip)
    # v1: the archived defect, kept so archived results reproduce.
    assert v1["gross_pnl_usd"] == pytest.approx(qty * (fill_out - 100.0))
    # v2: both fills slipped.
    assert v2["gross_pnl_usd"] == pytest.approx(qty * (fill_out - fill_in))
    assert v2["fill_entry_price"] == pytest.approx(fill_in) and v2["fill_exit_price"] == pytest.approx(fill_out)
    assert v2["fee_total_usd"] == v1["fee_total_usd"] and v2["qty"] == v1["qty"]
    assert v2["net_pnl_usd"] == pytest.approx(v2["gross_pnl_usd"] + v2["fee_total_usd"])
    assert v2["r_net"] == pytest.approx(v2["net_pnl_usd"] / v2["risk_usd"])
    assert v2["execution_model"] == EXECUTION_MODEL_V2


def test_fee_helper_short_side_and_zero_slippage_parity():
    slip = 0.0005
    short = _trade("SHORT", entry=100.0, stop=102.0, exit_=96.0)
    v2 = enrich_trade_execution_v2(short, slippage_rate=slip, **SIZING)
    assert v2["gross_pnl_usd"] == pytest.approx(v2["qty"] * (100.0 * (1 - slip) - 96.0 * (1 + slip)))
    for t in (_trade(), short):
        a = enrich_trade_execution(t, **SIZING)
        b = enrich_trade_execution_v2(t, **SIZING)
        for k in ("qty", "gross_pnl_usd", "net_pnl_usd", "fee_total_usd", "r_net"):
            assert b[k] == pytest.approx(a[k], abs=1e-12)


# ---- fix 2: gap through the stop ----------------------------------------------------


def test_bar_opening_beyond_the_stop_exits_at_the_open():
    assert resolve_bar_exit_v2(direction="LONG", open_=97.0, high=97.5, low=96.0, stop=98.0, targets=[104.0]) == \
        ("SL", 97.0, False, True)
    assert resolve_bar_exit_v2(direction="SHORT", open_=103.0, high=104.0, low=102.5, stop=102.0, targets=[96.0]) == \
        ("SL", 103.0, False, True)
    # Not gapped: v1 behaviour, including same-bar stop first.
    assert resolve_bar_exit_v2(direction="LONG", open_=100.0, high=105.0, low=97.0, stop=98.0, targets=[104.0]) == \
        ("SL", 98.0, True, False)


def _bar(i, o, h, l, c):
    return {"time": T0 + timedelta(hours=i), "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


def _cand(entry_index=0):
    return ResearchTrade(symbol="X", timeframe="1h", combination_id="LCSC", entry_index=entry_index,
                         signal_time=None, direction="LONG", entry_price=100.0, stop_price=98.0, tp1=104.0, tp2=None, tp3=None, rr=2.0)


def test_gap_stop_v1_vs_v2_on_a_trade_walk():
    candles = [_bar(0, 100, 100.5, 99.5, 100), _bar(1, 100, 101, 99, 100.5), _bar(2, 97, 97.5, 96, 96.5)]
    v1 = evaluate_candidate_trades(candles, [_cand()])[0]
    v2 = evaluate_candidate_trades_v2(candles, [_cand()])[0]
    assert v1.exit_price == 98.0 and v1.r_multiple == pytest.approx(-1.0)
    assert v2.exit_price == 97.0 and v2.r_multiple == pytest.approx(-1.5)
    assert v2.condition_snapshot["gap_stop"] is True and v1.exit_index == v2.exit_index == 2


def test_v2_equals_v1_without_gaps():
    candles = [_bar(0, 100, 100.5, 99.5, 100), _bar(1, 100, 101, 99, 100.5), _bar(2, 100.5, 104.5, 100, 104),
               _bar(3, 104, 104.2, 103, 103.5), _bar(4, 103.5, 103.8, 97.5, 98), _bar(5, 98, 99, 97, 98)]
    cands = [_cand(0), _cand(1), _cand(3)]
    a = evaluate_candidate_trades(candles, copy.deepcopy(cands))
    b = evaluate_candidate_trades_v2(candles, copy.deepcopy(cands))
    assert [(t.entry_index, t.exit_index, t.exit_price, t.outcome) for t in a] == \
        [(t.entry_index, t.exit_index, t.exit_price, t.outcome) for t in b]
