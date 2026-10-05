"""Canonical research fee calculation + reconciliation diagnostics."""

from __future__ import annotations

import pytest

from app.research.short_research_quality import (
    EXECUTION_MODEL,
    fee_reconciliation_tolerance,
    reconcile_research_blotter,
)
from app.research.trade_fees import (
    FEE_SIGN_NEGATIVE_COST,
    FEE_SIGN_POSITIVE_COST,
    FeeCalculationError,
    calculate_execution_fee,
    enrich_trade_execution,
    enrich_trades,
)


def test_1_market_entry_taker_fee():
    fee = calculate_execution_fee(
        price=100.0, quantity=20.0, fee_type="TAKER", taker_rate=0.0004, maker_rate=0.0002
    )
    assert fee == pytest.approx(-0.8)


def test_2_limit_entry_maker_fee():
    fee = calculate_execution_fee(
        price=100.0, quantity=20.0, fee_type="MAKER", taker_rate=0.0004, maker_rate=0.0002
    )
    assert fee == pytest.approx(-0.4)


def test_3_market_exit_taker_fee():
    fee = calculate_execution_fee(
        price=102.0, quantity=20.0, fee_type="MARKET", taker_rate=0.0004, maker_rate=0.0002
    )
    assert fee == pytest.approx(-0.816)


def test_4_entry_plus_exit_fee_aggregation():
    entry = calculate_execution_fee(price=100.0, quantity=20.0, fee_type="TAKER")
    exit_ = calculate_execution_fee(price=102.0, quantity=20.0, fee_type="TAKER")
    assert entry + exit_ == pytest.approx(-1.616)


def test_5_long_fee_calculation():
    trade = {
        "direction": "LONG",
        "entry_price": 100.0,
        "stop_price": 99.0,
        "exit_price": 102.0,
        "outcome": "TP1",
        "r_multiple": 2.0,
        "condition_snapshot": {"entry_type": "MARKET"},
    }
    row = enrich_trade_execution(
        trade, risk_usd=20.0, taker_fee=0.0004, maker_fee=0.0002, leverage=2.0
    )
    assert row["qty"] == 20.0
    assert row["gross_pnl_usd"] == 40.0
    assert row["entry_fee"] == pytest.approx(-0.8)
    assert row["exit_fee"] == pytest.approx(-0.816)
    assert row["total_fee"] == pytest.approx(-1.616)
    assert row["net_pnl_usd"] == pytest.approx(40.0 + row["total_fee"])
    assert row["fee_sign"] == FEE_SIGN_NEGATIVE_COST
    assert row["leverage"] == 2.0
    assert row["margin_usd"] == pytest.approx(1000.0)


def test_6_short_fee_calculation():
    trade = {
        "direction": "SHORT",
        "entry_price": 100.0,
        "stop_price": 105.0,
        "exit_price": 90.0,
        "outcome": "TP1",
        "condition_snapshot": {"entry_type": "MARKET"},
    }
    # risk $20 → qty = 20 / 5 = 4
    row = enrich_trade_execution(trade, risk_usd=20.0)
    assert row["qty"] == pytest.approx(4.0)
    assert row["gross_pnl_usd"] == pytest.approx(40.0)  # 4*(100-90)
    assert row["entry_fee"] == pytest.approx(-(4 * 100 * 0.0004))
    assert row["exit_fee"] == pytest.approx(-(4 * 90 * 0.0004))
    assert row["net_pnl"] == pytest.approx(row["gross_pnl"] + row["total_fee"])


def test_7_negative_fee_convention():
    fee = calculate_execution_fee(
        price=50.0, quantity=2.0, fee_type="TAKER", fee_sign=FEE_SIGN_NEGATIVE_COST
    )
    assert fee < 0


def test_8_positive_cost_convention_supported():
    fee = calculate_execution_fee(
        price=50.0, quantity=2.0, fee_type="TAKER", fee_sign=FEE_SIGN_POSITIVE_COST
    )
    assert fee == pytest.approx(0.04)
    trade = {
        "direction": "LONG",
        "entry_price": 100.0,
        "stop_price": 99.0,
        "exit_price": 101.0,
        "outcome": "TP1",
        "condition_snapshot": {"entry_type": "MARKET"},
    }
    row = enrich_trade_execution(trade, risk_usd=20.0, fee_sign=FEE_SIGN_POSITIVE_COST)
    assert row["total_fee"] > 0
    assert row["net_pnl"] == pytest.approx(row["gross_pnl"] - row["total_fee"])


def test_9_gross_plus_fees_equals_net():
    trade = {
        "direction": "LONG",
        "entry_price": 100.0,
        "stop_price": 99.0,
        "exit_price": 101.0,
        "outcome": "TP1",
        "condition_snapshot": {"entry_type": "LIMIT_RETEST"},
    }
    row = enrich_trade_execution(trade, risk_usd=20.0)
    assert row["net_pnl"] == pytest.approx(row["gross_pnl"] + row["total_fee"])


def test_10_report_fees_equal_sum_of_trade_fees():
    trades = enrich_trades(
        [
            {
                "direction": "LONG",
                "entry_price": 100.0,
                "stop_price": 99.0,
                "exit_price": 102.0,
                "outcome": "TP1",
                "condition_snapshot": {"entry_type": "MARKET"},
            },
            {
                "direction": "LONG",
                "entry_price": 100.0,
                "stop_price": 99.0,
                "exit_price": 101.0,
                "outcome": "TP1",
                "condition_snapshot": {"entry_type": "LIMIT_RETEST"},
            },
        ],
        risk_usd=20.0,
    )
    reported = sum(float(t["total_fee"]) for t in trades)
    recon = reconcile_research_blotter(trades, reported_fees=reported, reported_net_pnl=sum(t["net_pnl"] for t in trades))
    assert recon["fee_reconciliation"] == "PASS"
    assert recon["fee_reconciliation_diagnostics"]["difference"] == pytest.approx(0.0)


def test_11_rounding_does_not_create_false_failure():
    trades = [
        {
            "trade_id": "a",
            "net_pnl": 1.23456789,
            "gross_pnl": 1.3,
            "fees": -0.06543211,
            "fee_sign": "NEGATIVE_COST",
        }
    ]
    # Report rounded to 4dp while trade sum keeps full precision
    recon = reconcile_research_blotter(
        trades,
        reported_fees=-0.0654,
        reported_net_pnl=1.2346,
    )
    assert recon["fee_reconciliation"] == "PASS"
    assert abs(recon["fee_reconciliation_diagnostics"]["difference"]) <= fee_reconciliation_tolerance(-0.0654)


def test_12_material_mismatch_fails():
    trades = [
        {
            "trade_id": "bad",
            "net_pnl": 10.0,
            "gross_pnl": 12.0,
            "fees": -2.0,
            "fee_sign": "NEGATIVE_COST",
        }
    ]
    recon = reconcile_research_blotter(trades, reported_fees=-5.0, reported_net_pnl=10.0)
    assert recon["fee_reconciliation"] == "FAIL"
    assert abs(recon["fee_reconciliation_diagnostics"]["difference"]) > 0.01


def test_13_partial_exit_fees():
    # Research model currently uses a single exit notional; partial close = one filled exit qty.
    fee_full = calculate_execution_fee(price=100.0, quantity=10.0, fee_type="TAKER")
    fee_half = calculate_execution_fee(price=100.0, quantity=5.0, fee_type="TAKER")
    assert fee_half == pytest.approx(fee_full / 2)


def test_14_tp1_exit_fees():
    trade = {
        "direction": "SHORT",
        "entry_price": 100.0,
        "stop_price": 110.0,
        "exit_price": 80.0,
        "outcome": "TP1",
        "condition_snapshot": {"entry_type": "MARKET"},
    }
    row = enrich_trade_execution(trade, risk_usd=20.0)
    assert row["exit_fee_type"] == "TAKER"
    assert row["exit_fee"] < 0
    assert row["net_pnl"] == pytest.approx(row["gross_pnl"] + row["entry_fee"] + row["exit_fee"])


def test_15_quantity_notional_changes_at_exit():
    # Same qty, different exit price → different exit notional/fee.
    entry = calculate_execution_fee(price=100.0, quantity=3.0, fee_type="TAKER")
    exit_lo = calculate_execution_fee(price=90.0, quantity=3.0, fee_type="TAKER")
    exit_hi = calculate_execution_fee(price=110.0, quantity=3.0, fee_type="TAKER")
    assert abs(exit_lo) < abs(entry)
    assert abs(exit_hi) > abs(entry)


def test_16_zero_quantity_rejection():
    with pytest.raises(FeeCalculationError, match="zero_quantity"):
        calculate_execution_fee(price=100.0, quantity=0.0, fee_type="TAKER")


def test_17_missing_fee_type_rejection():
    with pytest.raises(FeeCalculationError, match="missing_fee_type"):
        calculate_execution_fee(price=100.0, quantity=1.0, fee_type="")


def test_18_quote_currency_fee_persistence():
    trade = {
        "direction": "LONG",
        "entry_price": 100.0,
        "stop_price": 99.0,
        "exit_price": 101.0,
        "outcome": "TP1",
        "condition_snapshot": {"entry_type": "MARKET"},
    }
    row = enrich_trade_execution(trade, risk_usd=20.0)
    assert row["fee_currency"] == "QUOTE"
    assert row["fee_basis"] == "EXECUTED_NOTIONAL"
    assert "entry_fee" in row and "exit_fee" in row and "total_fee" in row


def test_19_leverage_does_not_multiply_fees():
    trade = {
        "direction": "LONG",
        "entry_price": 100.0,
        "stop_price": 99.0,
        "exit_price": 101.0,
        "outcome": "TP1",
        "condition_snapshot": {"entry_type": "MARKET"},
    }
    # Wide enough stop that both leverages stay under the notional cap.
    a = enrich_trade_execution(
        trade, risk_usd=20.0, leverage=2.0, account_equity=1000.0
    )
    b = enrich_trade_execution(
        trade, risk_usd=20.0, leverage=10.0, account_equity=1000.0
    )
    assert a["capped_by_leverage"] is False
    assert b["capped_by_leverage"] is False
    assert a["qty"] == pytest.approx(b["qty"])
    assert a["total_fee"] == pytest.approx(b["total_fee"])
    assert a["margin_usd"] != b["margin_usd"]


def test_leverage_cap_reduces_tight_stop_qty():
    """BTC-like tight stop must not exceed equity × leverage notional."""
    trade = {
        "direction": "LONG",
        "entry_price": 78246.8,
        "stop_price": 77973.88016131721,
        "exit_price": 77973.88016131721,
        "outcome": "SL",
        "r_multiple": -1.0,
        "condition_snapshot": {"entry_type": "MARKET"},
    }
    row = enrich_trade_execution(
        trade,
        risk_usd=20.0,
        leverage=2.0,
        account_equity=1000.0,
        taker_fee=0.0004,
        maker_fee=0.0002,
    )
    assert row["capped_by_leverage"] is True
    assert row["notional"] == pytest.approx(2000.0)
    assert row["margin_usd"] == pytest.approx(1000.0)
    assert row["qty"] == pytest.approx(2000.0 / 78246.8)
    assert row["requested_risk_usd"] == pytest.approx(20.0)
    assert row["risk_usd"] < 20.0
    assert row["gross_pnl_usd"] == pytest.approx(-row["risk_usd"])
    assert row["r_net"] < -1.0  # fees push below -1R on actual risk


def test_leverage_cap_btc_wide_stop_uncapped():
    trade = {
        "direction": "LONG",
        "entry_price": 80046.5,
        "stop_price": 77817.98072682723,
        "exit_price": 84503.53854634555,
        "outcome": "TP1",
        "r_multiple": 2.0,
        "condition_snapshot": {"entry_type": "MARKET"},
    }
    row = enrich_trade_execution(
        trade, risk_usd=20.0, leverage=2.0, account_equity=1000.0
    )
    assert row["capped_by_leverage"] is False
    assert row["risk_usd"] == pytest.approx(20.0)
    assert row["notional"] < 2000.0
    assert row["gross_pnl_usd"] == pytest.approx(40.0)


def test_20_fee_reconciliation_status_and_diagnostics_persisted():
    trades = [
        {
            "trade_id": "t1",
            "net_pnl": 8.0,
            "gross_pnl": 10.0,
            "entry_fee": -1.0,
            "exit_fee": -1.0,
            "fees": -2.0,
            "fee_sign": "NEGATIVE_COST",
        }
    ]
    recon = reconcile_research_blotter(trades, reported_fees=-2.0, reported_net_pnl=8.0)
    diag = recon["fee_reconciliation_diagnostics"]
    assert recon["fee_reconciliation"] == "PASS"
    assert diag["status"] == "PASS"
    assert diag["reported_fees"] == -2.0
    assert diag["sum_trade_fees"] == pytest.approx(-2.0)
    assert diag["currency"] == "QUOTE"
    assert diag["basis"] == "EXECUTED_NOTIONAL"
    assert diag["fee_sign"] == "NEGATIVE_COST"
    assert diag["failed_trade_ids"] == []
    assert EXECUTION_MODEL["fee_sign"] == "NEGATIVE_COST"
    assert EXECUTION_MODEL["fee_basis"] == "EXECUTED_NOTIONAL"


def test_limit_retest_uses_maker_on_entry():
    trade = {
        "direction": "LONG",
        "entry_price": 100.0,
        "stop_price": 99.0,
        "exit_price": 101.0,
        "outcome": "TP1",
        "condition_snapshot": {"entry_type": "LIMIT_RETEST"},
    }
    row = enrich_trade_execution(trade, risk_usd=20.0, taker_fee=0.0004, maker_fee=0.0002)
    assert row["fee_entry_rate"] == 0.0002
    assert row["fee_exit_rate"] == 0.0004
    assert row["entry_fee_type"] == "MAKER"
    assert row["exit_fee_type"] == "TAKER"
