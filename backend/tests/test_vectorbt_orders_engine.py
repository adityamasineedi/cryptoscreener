"""Engine-family trades -> VectorBT replay orders: field mapping and parity."""

from __future__ import annotations

import pytest

pytest.importorskip("vectorbt")

from app.research.trade_fees import enrich_trade_execution  # noqa: E402
from app.research.vectorbt_orders_engine import (  # noqa: E402
    ReplayUnsupported,
    engine_replay_orders,
)
from app.research.vectorbt_replay import check_parity  # noqa: E402

SIZING = {
    "risk_usd": 20.0,
    "taker_fee": 0.0004,
    "maker_fee": 0.0002,
    "leverage": 2.0,
    "account_equity": 1000.0,
}


def _multicap_trade(entry_i, exit_i, entry, stop, exit_px, tp1, outcome, *, entry_type=None):
    """ResearchTrade.to_dict() shape, enriched as engine_backtest._run_multicap does."""
    raw = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "combination_id": "MID_CAP_FVG_DISCOUNT",
        "entry_index": entry_i,
        "signal_time": "2024-01-01T00:00:00+00:00",
        "direction": "LONG",
        "entry_price": entry,
        "stop_price": stop,
        "tp1": tp1,
        "tp2": None,
        "tp3": None,
        "rr": 2.0,
        "exit_index": exit_i,
        "exit_price": exit_px,
        "outcome": outcome,
        "condition_snapshot": {"entry_type": entry_type} if entry_type else {},
    }
    return enrich_trade_execution(raw, **SIZING)


def _ose_trade(direction, entry_i, exit_i, entry, stop, exit_px, target, outcome):
    """engine_backtest._run_ose record shape."""
    return enrich_trade_execution(
        {
            "direction": direction,
            "entry_price": entry,
            "stop_price": stop,
            "exit_price": exit_px,
            "outcome": outcome,
            "entry_type": "MARKET",
            "entry_index": entry_i,
            "exit_index": exit_i,
            "target_price": target,
            "tp1": target,
        },
        **SIZING,
    )


def _c12_trade(direction, entry_i, exit_i, entry, stop, raw_exit, tp1, outcome, slip=0.0002):
    """engine_backtest._run_candle12 shape: exit_price already the slipped fill."""
    fill = raw_exit * (1 - slip) if direction == "LONG" else raw_exit * (1 + slip)
    return enrich_trade_execution(
        {
            "direction": direction,
            "entry_index": entry_i,
            "exit_index": exit_i,
            "entry_price": entry,
            "stop_price": stop,
            "tp1": tp1,
            "outcome": outcome,
            "exit_price": fill,
            "raw_exit_price": raw_exit,
            "condition_snapshot": {"entry_type": "MARKET"},
        },
        **SIZING,
    )


def _v3_trade(direction, entry_i, exit_i, entry, stop, fill, tp1, outcome, *, qty=0.05, fee=0.0004, legs=None):
    """candle12_v3.run_candle12_v3 closed-trade shape (its own _economics)."""
    long_ = direction == "LONG"
    legs = legs or [{"bar": exit_i, "qty": qty, "raw_exit": fill, "fill": fill, "reason": outcome, "tag": None}]
    fee_entry = fee * qty * entry
    gross = sum(lg["qty"] * ((lg["fill"] - entry) if long_ else (entry - lg["fill"])) for lg in legs)
    fee_exit = sum(fee * lg["qty"] * lg["fill"] for lg in legs)
    net = gross - fee_entry - fee_exit
    risk = qty * abs(entry - stop)
    return {
        "trade_id": f"v3-{entry_i}",
        "direction": direction,
        "entry_index": entry_i,
        "exit_index": exit_i,
        "entry_price": entry,
        "stop_price": stop,
        "tp1": tp1,
        "qty": qty,
        "outcome": outcome,
        "legs": legs,
        "fee_entry_usd": -fee_entry,
        "fee_exit_usd": -fee_exit,
        "funding_usd": 0.0,
        "risk_usd": risk,
        "r_net": net / risk,
        "exit_price": sum(lg["fill"] * lg["qty"] for lg in legs) / sum(lg["qty"] for lg in legs),
    }


# ------------------------------------------------------------------ mapping


def test_multicap_mapping_reads_engine_fields():
    t = _multicap_trade(10, 14, 100.0, 98.0, 104.0, 104.0, "TP1")
    t["trade_id"] = "t1"
    (o,) = engine_replay_orders("MID_CAP_FVG_DISCOUNT", [t])
    assert o.trade_key == "t1"
    assert (o.entry_index, o.exit_index) == (10, 14)
    assert (o.entry_price, o.exit_price) == (100.0, 104.0)
    assert o.qty == t["qty"] and o.risk_usd == t["risk_usd"]
    assert (o.entry_fee_rate, o.exit_fee_rate) == (0.0004, 0.0004)
    assert (o.stop, o.target, o.outcome) == (98.0, 104.0, "TP1")
    assert o.net_r == t["r_net"]
    assert check_parity([o]).passed


def test_maker_entry_rate_is_read_from_the_trade():
    t = _multicap_trade(3, 7, 100.0, 98.0, 98.0, 104.0, "SL", entry_type="LIMIT_RETEST")
    (o,) = engine_replay_orders("LARGE_CAP_SWEEP_CHOCH", [t])
    assert (o.entry_fee_rate, o.exit_fee_rate) == (0.0002, 0.0004)
    assert check_parity([o]).passed


def test_leverage_capped_trade_uses_realized_risk():
    # |entry-stop| tiny -> qty capped by equity*leverage; r_net is on realized risk.
    t = _multicap_trade(3, 5, 100.0, 99.99, 100.5, 100.02, "TP1")
    assert t["capped_by_leverage"]
    (o,) = engine_replay_orders("SMALL_CAP_VOLUME_BOS", [t])
    assert o.risk_usd == pytest.approx(t["risk_usd"]) and o.risk_usd < 20.0
    assert check_parity([o]).passed


def test_candle12_v2_uses_slipped_exit_fill_not_raw_level():
    t = _c12_trade("SHORT", 20, 20, 99.97, 101.0, 101.0, 97.0, "SL")
    (o,) = engine_replay_orders("CANDLE12_V2", [t])
    assert o.exit_price == t["exit_price"] != t["raw_exit_price"]
    assert o.entry_index == o.exit_index == 20  # same-bar exit
    assert check_parity([o]).passed


def test_ose_long_and_short_sequence_with_back_to_back_bar():
    a = _ose_trade("LONG", 5, 9, 100.0, 98.0, 104.0, 104.0, "target")
    b = _ose_trade("SHORT", 9, 9, 104.0, 105.0, 105.0, 102.0, "stop")  # enters on a's exit bar
    c = _ose_trade("LONG", 12, 31, 101.0, 99.0, 100.5, 105.0, "time")
    for i, t in enumerate((a, b, c), 1):
        t["trade_id"] = f"t{i}"
    orders = engine_replay_orders("OSE_A_corrected_baseline", [a, b, c])
    res = check_parity(orders)
    assert res.passed, res.details
    assert res.bar_loop_trades == res.vectorbt_trades == 3


def test_candle12_v3_mapping_with_explicit_and_derived_fee_rate():
    t = _v3_trade("LONG", 40, 44, 200.04, 196.0, 207.9, 208.0, "TP1")
    (o,) = engine_replay_orders("CANDLE12_V3_EXEC_FIX", [t], taker_fee=0.0004)
    (o2,) = engine_replay_orders("CANDLE12_V3_EXEC_FIX", [t])  # rate read back from fees
    assert o.exit_price == t["legs"][0]["fill"] and o.exit_index == 44
    assert o.entry_fee_rate == 0.0004 and o2.entry_fee_rate == pytest.approx(0.0004, rel=1e-12)
    assert o.risk_usd == t["risk_usd"] and o.net_r == t["r_net"]
    assert check_parity([o]).passed and check_parity([o2]).passed


# ------------------------------------------------------------------ unsupported


def test_missing_bar_index_is_unsupported():
    t = _ose_trade("LONG", 5, 9, 100.0, 98.0, 104.0, 104.0, "target")
    t.pop("entry_index")
    with pytest.raises(ReplayUnsupported, match="entry_index"):
        engine_replay_orders("OSE_B_acceptance", [t])


def test_open_trade_is_unsupported():
    t = _ose_trade("LONG", 5, 9, 100.0, 98.0, 104.0, 104.0, "OPEN")
    with pytest.raises(ReplayUnsupported, match="not closed"):
        engine_replay_orders("OSE_C_retest", [t])


def _slipped_trade(direction, entry_i, exit_i, entry, stop, exit_px, tp1, outcome, slip=0.0005):
    """SD-style row: trade_fees slips both legs (slippage_rate > 0)."""
    return enrich_trade_execution(
        {"direction": direction, "entry_price": entry, "stop_price": stop, "exit_price": exit_px,
         "tp1": tp1, "outcome": outcome, "entry_type": "NEXT_OPEN",
         "entry_index": entry_i, "exit_index": exit_i},
        slippage_rate=slip,
        **SIZING,
    )


def test_trade_fees_slippage_rows_replay_on_slipped_fills_and_pass():
    # enrich_trade_execution prices gross P&L on the same slipped fills it
    # charges fees on (00e0673), so one fill per leg reproduces r_net.
    trades = [
        _slipped_trade("LONG", 1, 4, 100.0, 98.0, 104.0, 104.0, "TP1"),
        _slipped_trade("SHORT", 6, 6, 100.0, 102.0, 102.0, 96.0, "SL"),  # same-bar exit
    ]
    orders = engine_replay_orders("MID_CAP_FVG_DISCOUNT", trades)
    assert orders[0].entry_price == pytest.approx(100.0 * 1.0005, rel=1e-15)
    assert orders[0].exit_price == pytest.approx(104.0 * 0.9995, rel=1e-15)
    assert orders[1].entry_price == pytest.approx(100.0 * 0.9995, rel=1e-15)
    assert orders[1].exit_price == pytest.approx(102.0 * 1.0005, rel=1e-15)
    assert [o.net_r for o in orders] == [t["r_net"] for t in trades]
    res = check_parity(orders)
    assert res.passed, res.to_dict()
    assert res.bar_loop_trades == res.vectorbt_trades == 2


def test_slipped_row_with_pre_fix_gross_fails_on_net_r():
    # Negative control: r_net as the old helper computed it (gross on the
    # unslipped entry, fees on the slipped fills) must not pass.
    t = _slipped_trade("LONG", 1, 4, 100.0, 98.0, 104.0, 104.0, "TP1")
    exit_fill = 104.0 * 0.9995
    t["r_net"] = (t["qty"] * (exit_fill - 100.0) + t["fee_total_usd"]) / t["risk_usd"]
    res = check_parity(engine_replay_orders("MID_CAP_FVG_DISCOUNT", [t]))
    assert not res.passed and res.first_mismatch_field == "net_r"


@pytest.mark.parametrize("field", ["notional_entry_usd", "notional_exit_usd"])
def test_slipped_notional_disagreeing_with_fill_is_unsupported(field):
    t = _slipped_trade("SHORT", 1, 4, 100.0, 102.0, 96.0, 96.0, "TP1")
    t[field] = t[field] * 1.001
    with pytest.raises(ReplayUnsupported, match=field):
        engine_replay_orders("OSE_B_acceptance", [t])


def test_negative_slippage_rate_is_unsupported():
    t = _slipped_trade("LONG", 1, 4, 100.0, 98.0, 104.0, 104.0, "TP1")
    t["slippage_rate"] = -0.0005
    with pytest.raises(ReplayUnsupported, match="slippage_rate"):
        engine_replay_orders("MID_CAP_FVG_DISCOUNT", [t])


def test_v3_partial_exit_legs_are_unsupported():
    legs = [
        {"bar": 44, "qty": 0.025, "raw_exit": 204.0, "fill": 203.96, "reason": "TP1", "tag": None},
        {"bar": 50, "qty": 0.025, "raw_exit": 210.0, "fill": 209.96, "reason": "TP2", "tag": None},
    ]
    t = _v3_trade("LONG", 40, 50, 200.04, 196.0, 0.0, 204.0, "TP1+TP2", legs=legs)
    with pytest.raises(ReplayUnsupported, match="2 exit legs"):
        engine_replay_orders("CANDLE12_V3_EXEC_FIX", [t])


def test_combo_id_is_not_an_engine_strategy():
    with pytest.raises(ReplayUnsupported):
        engine_replay_orders("COMBO_01", [])


# ------------------------------------------------------------------ negative controls


def test_wrong_engine_net_r_fails_on_net_r():
    t = _multicap_trade(10, 14, 100.0, 98.0, 104.0, 104.0, "TP1")
    t["r_net"] = t["r_net"] + 1e-6
    res = check_parity(engine_replay_orders("MID_CAP_FVG_DISCOUNT", [t]))
    assert not res.passed and res.first_mismatch_field == "net_r"


def test_overlapping_positions_are_caught():
    a = _ose_trade("LONG", 5, 12, 100.0, 98.0, 104.0, 104.0, "target")
    b = _ose_trade("LONG", 8, 15, 101.0, 99.0, 105.0, 105.0, "target")
    res = check_parity(engine_replay_orders("OSE_D_faster_confirmation", [a, b]))
    assert not res.passed


# ------------------------------------------------------------------ fixture end to end


def test_fixture_is_deterministic():
    from scripts.vectorbt_parity.fixture import FIXTURE_WARMUP, fixture_candles

    a = fixture_candles(400)
    assert a == fixture_candles(400)
    assert a[0]["time"].isoformat() == "2023-01-01T00:00:00+00:00"
    assert all(c["low"] <= min(c["open"], c["close"]) <= max(c["open"], c["close"]) <= c["high"] for c in a)
    assert FIXTURE_WARMUP == 1000


@pytest.mark.parametrize(
    "sid,direction",
    [
        ("OSE_A_corrected_baseline", "SHORT"),
        ("CANDLE12_V3_EXEC_FIX", "LONG"),
        ("MID_CAP_FVG_DISCOUNT", "LONG"),
    ],
)
def test_fixture_end_to_end_parity(sid, direction):
    from scripts.vectorbt_parity.run_engine_parity import run_cell
    from scripts.vectorbt_parity.fixture import FIXTURE_WARMUP, fixture_candles

    cell = run_cell(sid, "fixture", direction, (fixture_candles(), FIXTURE_WARMUP, None), None)
    assert cell["status"] == "PASS", cell
    assert cell["bar_loop_trades"] > 0
    assert cell["bar_loop_trades"] == cell["vectorbt_trades"]
