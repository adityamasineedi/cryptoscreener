from app.services.paper_trade import PaperTradeEngine

eng = PaperTradeEngine(starting_equity=1000, risk_percent=0.02, enabled=True)
payload = {
    "symbol": "TESTUSDT",
    "status": "LONG_ENTRY_CANDIDATE",
    "direction": "LONG",
    "timeframe": "15m",
    "entry": {"entry_price": 100.0, "status": "LONG_ENTRY_CANDIDATE"},
    "stop": {"final_stop": 98.0},
    "targets": [{"target_price": 104.0, "r_multiple": 2.0}],
    "risk_management": {
        "final_quantity": 10.0,
        "entry": 100.0,
        "stop": 98.0,
        "max_risk_amount": 20.0,
    },
    "source_candle_timestamps": {"15m": "2026-10-02T10:00:00+00:00"},
    "calculated_at": "2026-10-02T10:01:00+00:00",
}

opened = eng.on_setup_signal("TESTUSDT", payload)
assert opened is not None and opened.status == "OPEN"
print(f"PLACE_OK entry={opened.entry_price} qty={opened.quantity} stop={opened.stop_price} tp1={opened.tp1_price}")

mid = eng.tick({"TESTUSDT": 101.0})
assert len(mid) == 0 and eng.status()["open_count"] == 1
u = list(eng._open.values())[0]
print(f"HOLD_OK mark={u.mark_price} uR={u.unrealized_r:.3f}")

tp = eng.tick({"TESTUSDT": 104.0})
assert len(tp) == 1 and tp[0].exit_reason == "TP1"
print(f"CLOSE_TP1_OK pnl={tp[0].pnl_usd} equity={eng.equity} open={eng.status()['open_count']}")

payload2 = dict(payload)
payload2["source_candle_timestamps"] = {"15m": "2026-10-02T10:15:00+00:00"}
opened2 = eng.on_setup_signal("TESTUSDT", payload2)
assert opened2 is not None
stop = eng.tick({"TESTUSDT": 97.0})
assert len(stop) == 1 and stop[0].exit_reason == "STOP"
print(f"CLOSE_STOP_OK pnl={stop[0].pnl_usd} closed={eng.status()['closed_count']}")
print("ALL_PASS")
