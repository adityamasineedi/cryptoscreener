"""Unit tests for virtual paper trade engine."""

from __future__ import annotations

from app.services.paper_trade import PaperTradeEngine


def _candidate(
    *,
    symbol: str = "BTCUSDT",
    entry: float = 100.0,
    stop: float = 98.0,
    tp1: float = 104.0,
    qty: float = 10.0,
    source_ts: str = "2026-10-02T09:45:00+00:00",
    status: str = "LONG_ENTRY_CANDIDATE",
) -> dict:
    return {
        "symbol": symbol,
        "status": status,
        "direction": "LONG",
        "timeframe": "15m",
        "entry": {"entry_price": entry, "status": status},
        "stop": {"final_stop": stop},
        "targets": [{"target_price": tp1, "r_multiple": 2.0}],
        "risk_management": {
            "final_quantity": qty,
            "entry": entry,
            "stop": stop,
            "max_risk_amount": abs(entry - stop) * qty,
        },
        "source_candle_timestamps": {"15m": source_ts},
        "calculated_at": "2026-10-02T10:00:00+00:00",
    }


def test_opens_on_long_entry_candidate():
    eng = PaperTradeEngine(starting_equity=1000, risk_percent=0.02, enabled=True)
    pos = eng.on_setup_signal("BTCUSDT", _candidate())
    assert pos is not None
    assert pos.status == "OPEN"
    assert pos.entry_price == 100.0
    assert pos.stop_price == 98.0
    assert pos.tp1_price == 104.0
    assert pos.quantity == 10.0
    assert eng.status()["open_count"] == 1


def test_dedupe_same_source_candle():
    eng = PaperTradeEngine()
    a = eng.on_setup_signal("BTCUSDT", _candidate(source_ts="ts-1"))
    b = eng.on_setup_signal("BTCUSDT", _candidate(source_ts="ts-1"))
    assert a is not None
    assert b is None
    assert eng.status()["open_count"] == 1


def test_no_open_on_no_setup():
    eng = PaperTradeEngine(entry_mode="path_b")
    pos = eng.on_setup_signal("BTCUSDT", _candidate(status="NO_SETUP"))
    assert pos is None
    assert eng.status()["open_count"] == 0


def test_opens_on_path_a_trend_bos():
    eng = PaperTradeEngine(entry_mode="path_a", starting_equity=1000, risk_percent=0.02)
    payload = {
        "status": "WAITING",
        "direction": "LONG",
        "timeframe": "15m",
        "trend": {"15m": {"trend": "BULLISH"}},
        "bos": {
            "state": "CONFIRMED",
            "direction": "BULLISH_BOS",
            "broken_level": 100.0,
        },
        "entry": {},
        "stop": {"final_stop": 98.0},
        "targets": [{"target_price": 104.0}],
        "risk_reward": {"RISK_REWARD": "PASS"},
        "risk_management": {},
        "source_candle_timestamps": {"15m": "2026-10-02T13:15:00+00:00"},
        "calculated_at": "2026-10-02T13:20:00+00:00",
    }
    pos = eng.on_setup_signal("BATUSDT", payload)
    assert pos is not None
    assert pos.entry_price == 100.0
    assert pos.stop_price == 98.0
    assert pos.tp1_price == 104.0
    assert pos.signal_snippet.get("path") == "PATH_A"
    assert eng.status()["entry_mode"] == "path_a"


def test_path_b_mode_skips_trend_bos_only():
    eng = PaperTradeEngine(entry_mode="path_b")
    payload = {
        "status": "WAITING",
        "direction": "LONG",
        "timeframe": "15m",
        "trend": {"15m": {"trend": "BULLISH"}},
        "bos": {"state": "CONFIRMED", "direction": "BULLISH_BOS", "broken_level": 100.0},
        "stop": {"final_stop": 98.0},
        "targets": [{"target_price": 104.0}],
        "risk_reward": {"RISK_REWARD": "PASS"},
        "source_candle_timestamps": {"15m": "ts-a"},
    }
    assert eng.on_setup_signal("BATUSDT", payload) is None


def test_stop_exit_closes_near_minus_one_r():
    eng = PaperTradeEngine()
    eng.on_setup_signal("BTCUSDT", _candidate(entry=100, stop=98, tp1=104, qty=10))
    closed = eng.tick({"BTCUSDT": 97.5})
    assert len(closed) == 1
    assert closed[0].exit_reason == "STOP"
    assert closed[0].status == "CLOSED"
    assert closed[0].pnl_usd is not None and closed[0].pnl_usd < 0
    # 10 qty * (97.5-100) = -25; risk was 20 → about -1.25R
    assert closed[0].r_multiple is not None and closed[0].r_multiple < -1.0
    assert eng.status()["open_count"] == 0
    assert eng.realized_pnl < 0


def test_tp1_exit():
    eng = PaperTradeEngine()
    eng.on_setup_signal("BTCUSDT", _candidate(entry=100, stop=98, tp1=104, qty=10))
    closed = eng.tick({"BTCUSDT": 104.0})
    assert len(closed) == 1
    assert closed[0].exit_reason == "TP1"
    assert closed[0].pnl_usd == 40.0  # 10 * 4
    assert eng.equity == 1040.0


def test_disabled_skips_open():
    eng = PaperTradeEngine(enabled=False)
    assert eng.on_setup_signal("BTCUSDT", _candidate()) is None


def test_list_opportunities_tiers(monkeypatch):
    from app.services.engine_store import EngineStore
    import app.services.paper_trade as paper_mod
    import app.services.engine_store as es

    store = EngineStore()
    store.set_setup_signal(
        "AAAUSDT",
        {
            "status": "LONG_ENTRY_CANDIDATE",
            "direction": "LONG",
            "timeframe": "15m",
            "trend": {"15m": {"trend": "BULLISH"}},
            "bos": {"state": "CONFIRMED", "direction": "BULLISH_BOS"},
            "impulse": {"is_impulse": True, "quality": "STRONG"},
            "pullback": {"pullback_state": "CONFIRMED"},
            "retest": {"confirmed": True},
            "entry": {"entry_price": 100},
            "stop": {"final_stop": 98},
            "targets": [{"target_price": 104}],
            "risk_reward": {"RISK_REWARD": "PASS"},
            "conditions": [],
        },
    )
    store.set_setup_signal(
        "BBBUSDT",
        {
            "status": "NO_SETUP",
            "direction": "LONG",
            "timeframe": "15m",
            "trend": {"15m": {"trend": "BULLISH"}},
            "bos": {"state": "CONFIRMED", "direction": "BULLISH_BOS"},
            "impulse": {"is_impulse": False},
            "pullback": {"pullback_state": "WAITING"},
            "retest": {},
            "entry": {},
            "stop": {"final_stop": 90},
            "targets": [{"target_price": 110}],
            "risk_reward": {"RISK_REWARD": "FAIL"},
            "conditions": [],
        },
    )
    store.set_setup_signal(
        "WAITUSDT",
        {
            "status": "WAITING",
            "timeframe": "15m",
            "trend": {},
            "bos": {},
            "data_dependencies": {"15m": "WAITING FOR OHLCV"},
        },
    )

    monkeypatch.setattr(es, "engine_store", store)

    rows = paper_mod.list_trade_opportunities(limit=10, include_waiting=True)
    tiers = {r["symbol"]: r["tier"] for r in rows}
    assert tiers.get("AAAUSDT") == "READY"
    # Path A mode: Trend+BOS+stop is READY (research gate), not merely NEAR
    assert tiers.get("BBBUSDT") == "READY"
    assert tiers.get("WAITUSDT") == "WAITING"
    assert rows[0]["symbol"] == "AAAUSDT"
