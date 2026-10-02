from app.research.trade_fees import enrich_trade_execution


def test_long_taker_round_trip_fees():
    trade = {
        "direction": "LONG",
        "entry_price": 100.0,
        "stop_price": 99.0,
        "exit_price": 102.0,
        "outcome": "TP1",
        "r_multiple": 2.0,
        "condition_snapshot": {"entry_type": "MARKET"},
    }
    # risk $20 → qty = 20 / 1 = 20
    row = enrich_trade_execution(
        trade, risk_usd=20.0, taker_fee=0.0004, maker_fee=0.0002
    )
    assert row["qty"] == 20.0
    assert row["gross_pnl_usd"] == 40.0  # 20 * (102-100)
    # fees: entry 20*100*0.0004=0.8; exit 20*102*0.0004=0.816
    assert abs(row["fee_entry_usd"] - 0.8) < 1e-9
    assert abs(row["fee_exit_usd"] - 0.816) < 1e-9
    assert abs(row["net_pnl_usd"] - (40.0 - 0.8 - 0.816)) < 1e-9
    assert abs(row["r_net"] - row["net_pnl_usd"] / 20.0) < 1e-9


def test_limit_retest_uses_maker_on_entry():
    trade = {
        "direction": "LONG",
        "entry_price": 100.0,
        "stop_price": 99.0,
        "exit_price": 101.0,
        "outcome": "TP1",
        "condition_snapshot": {"entry_type": "LIMIT_RETEST"},
    }
    row = enrich_trade_execution(
        trade, risk_usd=20.0, taker_fee=0.0004, maker_fee=0.0002
    )
    assert row["fee_entry_rate"] == 0.0002
    assert row["fee_exit_rate"] == 0.0004
