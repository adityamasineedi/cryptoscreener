from app.research.regime_backtest_job import slim_result, start


def test_slim_result_keeps_summary_and_the_latest_closed_trades():
    result = {
        "summary": {
            "trades": 2,
            "pnl_usd_net": 5,
            "by_strategy": {
                "COMBO_01|SHORT": {"strategy_id": "COMBO_01", "pnl_usd_net": 5},
                "COMBO_02|SHORT": {"strategy_id": "COMBO_02", "pnl_usd_net": -1},
            },
        },
        "trades": [
            {"symbol": "BTCUSDT", "net_pnl_usd": 1, "exit_time": "2026-01-01T00:00:00+00:00"},
            {"symbol": "ETHUSDT", "net_pnl_usd": None, "exit_time": "2026-01-02T00:00:00+00:00"},
            {"symbol": "SOLUSDT", "net_pnl_usd": 4, "exit_time": "2026-01-03T00:00:00+00:00"},
        ],
    }
    slim = slim_result(result, trade_limit=1)
    assert [row["strategy_id"] for row in slim["summary"]["by_strategy"]] == ["COMBO_01", "COMBO_02"]
    assert slim["trades"] == [
        {"symbol": "SOLUSDT", "strategy_id": None, "direction": None, "trend_at_entry": None,
         "signal_time": None, "exit_time": "2026-01-03T00:00:00+00:00", "outcome": None,
         "net_pnl_usd": 4, "r_net": None}
    ]


def test_start_rejects_symbols_outside_the_cap_book():
    import asyncio

    out = asyncio.run(start(["WIFUSDT"], 100))
    assert out["status"] == "REJECTED"
