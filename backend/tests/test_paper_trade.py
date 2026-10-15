"""Unit tests for virtual paper trade engine.

v1 GUARDRAIL (tag: v1-combo02-long-htf): Path A HTF enforcement tests in this
file are freeze-critical — see docs/v1_freeze.md. Path B remains experimental.
"""

from __future__ import annotations

import pytest

from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperPosition, PaperTradeEngine

# Path A HTF / COMBO_02 v1 tests share this marker with research HTF suite.
pytestmark = pytest.mark.v1_freeze

# Existing entry/exit tests focus on Path A/B fills — not cap gates
_NO_GATES = PaperRiskPolicy(enabled=False)


@pytest.fixture(autouse=True)
def _live_near_entry(monkeypatch):
    """Default: live mark at 100 so open gates pass unless a test overrides."""
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)


@pytest.fixture(autouse=True)
def _disable_risk_gates_and_enable_legacy(monkeypatch):
    """Isolate Path A/B tests; enable legacy auto-entry (prod default is OFF)."""
    orig = PaperTradeEngine.__init__

    def _init(self, *args, **kwargs):
        kwargs.setdefault("risk_policy", _NO_GATES)
        result = orig(self, *args, **kwargs)
        self.legacy_auto_entry_enabled = True
        return result

    monkeypatch.setattr(PaperTradeEngine, "__init__", _init)


def _candidate(
    *,
    symbol: str = "BTCUSDT",
    entry: float = 100.0,
    stop: float = 98.0,
    tp1: float = 104.0,
    qty: float = 10.0,
    source_ts: str = "2026-10-02T09:45:00+00:00",
    status: str = "LONG_ENTRY_CANDIDATE",
    ohlcv_freshness: str = "OK",
    trend_4h: str = "BULLISH",
    trend_1h: str = "BULLISH",
    include_path_a_structure: bool = True,
) -> dict:
    """LONG_ENTRY_CANDIDATE payload.

    Default includes Path A structure (BOS + HTF) so default entry_mode=path_a
    still opens under COMBO_02 v1 HTF enforcement. Set
    ``include_path_a_structure=False`` to simulate a Path-B-only candidate.
    """
    payload: dict = {
        "symbol": symbol,
        "status": status,
        "direction": "LONG",
        "timeframe": "15m",
        "entry": {"entry_price": entry, "status": status},
        "stop": {
            "final_stop": stop,
            "provisional": False,
            "reason": "structural pullback low",
        },
        "targets": [{"target_price": tp1, "r_multiple": 2.0, "target_type": "previous_swing_high"}],
        "impulse": {"is_impulse": True},
        "pullback": {"pullback_state": "CONFIRMED"},
        "risk_management": {
            "final_quantity": qty,
            "entry": entry,
            "stop": stop,
            "max_risk_amount": abs(entry - stop) * qty,
        },
        "source_candle_timestamps": {"15m": source_ts},
        "calculated_at": "2026-10-02T10:00:00+00:00",
        "ohlcv_freshness": ohlcv_freshness,
    }
    if include_path_a_structure:
        payload["trend"] = {
            "15m": {"trend": "BULLISH"},
            "1h": {"trend": trend_1h},
            "4h": {"trend": trend_4h},
        }
        payload["mtf"] = {
            "MTF_ALIGNMENT": (
                "STRONG_LONG" if trend_4h == trend_1h == "BULLISH" else "MIXED"
            )
        }
        payload["bos"] = {
            "state": "CONFIRMED",
            "direction": "BULLISH_BOS",
            "broken_level": entry,
        }
        payload["risk_reward"] = {"RISK_REWARD": "PASS"}
    return payload


def _path_a_payload(
    *,
    entry_bos: float = 100.0,
    stop: float = 98.0,
    tp1: float = 104.0,
    freshness: str = "OK",
    source_ts: str = "2026-10-02T13:15:00+00:00",
    trend_4h: str = "BULLISH",
    trend_1h: str = "BULLISH",
) -> dict:
    return {
        "status": "WAITING",
        "direction": "LONG",
        "timeframe": "15m",
        "trend": {
            "15m": {"trend": "BULLISH"},
            "1h": {"trend": trend_1h},
            "4h": {"trend": trend_4h},
        },
        "mtf": {"MTF_ALIGNMENT": "STRONG_LONG" if trend_4h == trend_1h == "BULLISH" else "MIXED"},
        "bos": {
            "state": "CONFIRMED",
            "direction": "BULLISH_BOS",
            "broken_level": entry_bos,
        },
        "entry": {},
        "stop": {"final_stop": stop},
        "targets": [{"target_price": tp1}],
        "risk_reward": {"RISK_REWARD": "PASS"},
        "risk_management": {},
        "source_candle_timestamps": {"15m": source_ts},
        "calculated_at": "2026-10-02T13:20:00+00:00",
        "ohlcv_freshness": freshness,
    }


def test_opens_on_long_entry_candidate(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
    eng = PaperTradeEngine(starting_equity=1000, risk_percent=0.02, enabled=True)
    pos = eng.on_setup_signal("BTCUSDT", _candidate())
    assert pos is not None
    assert pos.status == "OPEN"
    assert pos.entry_price == 100.0
    assert pos.stop_price == 98.0
    assert pos.tp1_price == 104.0
    assert pos.quantity == 10.0
    assert pos.signal_snippet.get("path") == "LEGACY"
    assert pos.signal_snippet.get("strategy_id") == "RESEARCH_15M"
    assert pos.signal_snippet.get("source") == "LEGACY_SETUP_SIGNAL"
    assert pos.signal_snippet.get("combo_id") is None
    assert pos.signal_snippet.get("telegram_eligible") is False
    assert eng.status()["open_count"] == 1


def test_path_a_blocks_entry_candidate_without_htf(monkeypatch):
    """COMBO_02 v1: LONG_ENTRY_CANDIDATE must not bypass Path A HTF gate."""
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
    eng = PaperTradeEngine(entry_mode="path_a", starting_equity=1000, risk_percent=0.02)
    assert (
        eng.on_setup_signal(
            "BTCUSDT",
            _candidate(trend_4h="BEARISH", trend_1h="BULLISH"),
        )
        is None
    )
    assert (
        eng.on_setup_signal(
            "BTCUSDT",
            _candidate(include_path_a_structure=False),
        )
        is None
    )


def test_path_b_opens_entry_candidate_without_path_a_structure(monkeypatch):
    """Path B experimental may open on ENTRY_CANDIDATE without COMBO_02 HTF."""
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
    eng = PaperTradeEngine(entry_mode="path_b", starting_equity=1000, risk_percent=0.02)
    pos = eng.on_setup_signal(
        "BTCUSDT",
        _candidate(include_path_a_structure=False),
    )
    assert pos is not None
    assert pos.signal_snippet.get("path") == "B"
    assert pos.signal_snippet.get("strategy_id") == "EXPERIMENTAL_PATH_B"
    assert pos.signal_snippet.get("telegram_eligible") is False


def test_dedupe_same_source_candle(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
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


def test_waiting_path_a_does_not_open(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
    eng = PaperTradeEngine(entry_mode="path_a", starting_equity=1000, risk_percent=0.02)
    pos = eng.on_setup_signal("BATUSDT", _path_a_payload())
    assert pos is None
    reason = eng.status()["last_skip_reason"] or ""
    assert "path_a_rejected" in reason
    assert "closed_eval_WAITING" in reason
    assert eng.status()["open_count"] == 0


def test_path_a_blocks_without_htf_bullish(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
    eng = PaperTradeEngine(entry_mode="path_a", starting_equity=1000, risk_percent=0.02)
    assert (
        eng.on_setup_signal(
            "BATUSDT",
            _path_a_payload(trend_4h="BEARISH", trend_1h="BULLISH"),
        )
        is None
    )
    assert (
        eng.on_setup_signal(
            "BATUSDT",
            _path_a_payload(trend_4h="BEARISH", trend_1h="BEARISH"),
        )
        is None
    )


def test_path_b_mode_skips_trend_bos_only():
    eng = PaperTradeEngine(entry_mode="path_b")
    assert eng.on_setup_signal("BATUSDT", _path_a_payload()) is None


def test_stop_exit_fills_at_stop_about_minus_one_r(monkeypatch):
    import app.services.paper_trade as paper_mod
    from app.services.paper_sizing import net_paper_pnl

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
    eng = PaperTradeEngine()
    pos = eng.on_setup_signal("BTCUSDT", _candidate(entry=100, stop=98, tp1=104, qty=10))
    assert pos is not None
    # Mark gaps through stop — paper fill stays at stop level
    closed = eng.tick({"BTCUSDT": 97.5})
    assert len(closed) == 1
    assert closed[0].exit_reason == "STOP"
    assert closed[0].status == "CLOSED"
    assert closed[0].exit_price == 98.0
    expected = net_paper_pnl(
        entry_price=pos.entry_price,
        exit_price=98.0,
        quantity=pos.quantity,
        risk_usd=pos.risk_usd,
        entry_fee_usd=(pos.signal_snippet.get("paper_execution") or {}).get("entry_fee_usd"),
        fee_rate=(pos.signal_snippet.get("paper_execution") or {}).get("fee_rate") or 0.0004,
        slippage_rate=(pos.signal_snippet.get("paper_execution") or {}).get("slippage_rate")
        or 0.0002,
    )
    assert closed[0].pnl_usd == pytest.approx(expected["pnl_usd"])
    assert closed[0].pnl_usd < -20.0  # fees/slippage worsen the stop loss
    assert closed[0].r_multiple == pytest.approx(expected["r_multiple"])
    assert eng.status()["open_count"] == 0
    assert eng.realized_pnl == pytest.approx(expected["pnl_usd"])


def test_tp1_exit_fills_at_tp1(monkeypatch):
    import app.services.paper_trade as paper_mod
    from app.services.paper_sizing import net_paper_pnl

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
    eng = PaperTradeEngine()
    pos = eng.on_setup_signal("BTCUSDT", _candidate(entry=100, stop=98, tp1=104, qty=10))
    assert pos is not None
    closed = eng.tick({"BTCUSDT": 105.0})
    assert len(closed) == 1
    assert closed[0].exit_reason == "TP1"
    assert closed[0].exit_price == 104.0
    expected = net_paper_pnl(
        entry_price=pos.entry_price,
        exit_price=104.0,
        quantity=pos.quantity,
        risk_usd=pos.risk_usd,
        entry_fee_usd=(pos.signal_snippet.get("paper_execution") or {}).get("entry_fee_usd"),
        fee_rate=(pos.signal_snippet.get("paper_execution") or {}).get("fee_rate") or 0.0004,
        slippage_rate=(pos.signal_snippet.get("paper_execution") or {}).get("slippage_rate")
        or 0.0002,
    )
    assert closed[0].pnl_usd == pytest.approx(expected["pnl_usd"])
    assert closed[0].pnl_usd < 40.0  # fees/slippage reduce winner
    assert eng.equity == pytest.approx(1000.0 + expected["pnl_usd"])


def test_disabled_skips_open():
    eng = PaperTradeEngine(enabled=False)
    assert eng.on_setup_signal("BTCUSDT", _candidate()) is None


def test_skip_trailing_stale_ohlcv(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 0.2486)
    eng = PaperTradeEngine(entry_mode="path_a")
    pos = eng.on_setup_signal(
        "EIGENUSDT",
        _path_a_payload(entry_bos=0.2486, stop=0.2411, tp1=0.2643, freshness="TRAILING_STALE"),
    )
    assert pos is None


def test_plan_fills_when_live_already_through_stop(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 0.2387)
    eng = PaperTradeEngine(entry_mode="path_a")
    pos = eng.on_setup_signal(
        "EIGENUSDT",
        _candidate(symbol="EIGENUSDT", entry=0.2486, stop=0.2411, tp1=0.2643, qty=0),
    )
    assert pos is not None
    assert pos.signal_snippet.get("execution") == "backtest_plan"
    # Generic tick is 0.01, so the plan is kept on the nearest tick.
    assert pos.entry_price == pytest.approx(0.25)
    assert pos.stop_price == pytest.approx(0.24)
    assert pos.tp1_price == pytest.approx(0.26)


def test_plan_fills_when_live_is_deep_into_risk(monkeypatch):
    import app.services.paper_trade as paper_mod

    # Live below entry by more than 10% of the risk distance, still above stop.
    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 99.5)
    eng = PaperTradeEngine(entry_mode="path_a")
    pos = eng.on_setup_signal("BATUSDT", _candidate(symbol="BATUSDT"))
    assert pos is not None
    assert pos.entry_price == pytest.approx(100.0)
    assert pos.stop_price == pytest.approx(98.0)
    assert pos.tp1_price == pytest.approx(104.0)


def test_plan_fills_without_live_price(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: None)
    eng = PaperTradeEngine()
    pos = eng.on_setup_signal("BTCUSDT", _candidate())
    assert pos is not None
    assert pos.entry_price == pytest.approx(100.0)
    assert pos.stop_price == pytest.approx(98.0)
    assert pos.tp1_price == pytest.approx(104.0)


def test_invalidated_setup_does_not_cancel_open(monkeypatch):
    """Restart warmup often emits INVALIDATED — must not wipe an open fill."""
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
    eng = PaperTradeEngine(entry_mode="path_b")
    pos = eng.on_setup_signal("BTCUSDT", _candidate())
    assert pos is not None
    assert eng.status()["open_count"] == 1
    assert (
        eng.on_setup_signal(
            "BTCUSDT", _candidate(status="INVALIDATED", source_ts="other")
        )
        is None
    )
    assert eng.status()["open_count"] == 1
    assert eng.get_open("BTCUSDT", "LEGACY").status == "OPEN"
    assert eng.status()["closed_count"] == 0


def test_hydrated_open_survives_invalidated_setup(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 200.0)
    eng = PaperTradeEngine(starting_equity=1000.0)
    eng.hydrate_from_rows(
        [
            {
                "id": "open-1",
                "symbol": "ETHUSDT",
                "side": "LONG",
                "status": "OPEN",
                "entry_price": 200.0,
                "stop_price": 190.0,
                "tp1_price": 220.0,
                "quantity": 1.0,
                "risk_usd": 10.0,
                "opened_at": "2026-10-02T10:00:00+00:00",
                "source_candle_ts": "ts-eth",
                "timeframe": "15m",
                "signal_snippet": {"path": "PATH_A", "bos_level": 200.0},
            }
        ]
    )
    assert eng.status()["open_count"] == 1
    eng.on_setup_signal(
        "ETHUSDT",
        {
            "status": "INVALIDATED",
            "direction": "LONG",
            "timeframe": "15m",
            "invalidation_reason": "warmup",
            "source_candle_timestamps": {"15m": "ts-new"},
        },
    )
    assert eng.status()["open_count"] == 1
    assert eng.get_open("ETHUSDT", "LEGACY").id == "open-1"


def test_hydrate_from_rows_restores_book_and_equity(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 200.0)
    eng = PaperTradeEngine(starting_equity=1000.0)
    stats = eng.hydrate_from_rows(
        [
            {
                "id": "open-1",
                "symbol": "ETHUSDT",
                "side": "LONG",
                "status": "OPEN",
                "entry_price": 200.0,
                "stop_price": 190.0,
                "tp1_price": 220.0,
                "quantity": 1.0,
                "risk_usd": 10.0,
                "opened_at": "2026-10-02T10:00:00+00:00",
                "source_candle_ts": "ts-eth",
                "timeframe": "15m",
                "signal_snippet": {"path": "PATH_A", "bos_level": 200.0},
            },
            {
                "id": "closed-1",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "status": "CLOSED",
                "entry_price": 100.0,
                "stop_price": 98.0,
                "tp1_price": 104.0,
                "quantity": 10.0,
                "risk_usd": 20.0,
                "opened_at": "2026-10-02T09:00:00+00:00",
                "closed_at": "2026-10-02T11:00:00+00:00",
                "exit_price": 104.0,
                "exit_reason": "TP1",
                "pnl_usd": 40.0,
                "r_multiple": 2.0,
                "source_candle_ts": "ts-btc",
                "timeframe": "15m",
                "signal_snippet": {"path": "PATH_A", "bos_level": 100.0},
            },
        ]
    )
    assert stats["open"] == 1
    assert stats["closed"] == 1
    assert eng.status()["open_count"] == 1
    assert eng.status()["closed_count"] == 1
    assert eng.realized_pnl == 40.0
    assert eng.equity == 1040.0
    # Same source candle must not open again after hydrate
    assert (
        eng.on_setup_signal(
            "ETHUSDT",
            {
                "status": "NO_SETUP",
                "direction": "LONG",
                "timeframe": "15m",
                "trend": {"15m": {"trend": "BULLISH"}},
                "bos": {
                    "state": "CONFIRMED",
                    "direction": "BULLISH_BOS",
                    "broken_level": 200.0,
                },
                "stop": {"final_stop": 190.0},
                "targets": [{"target_price": 220.0}],
                "risk_reward": {"RISK_REWARD": "PASS"},
                "source_candle_timestamps": {"15m": "ts-eth"},
                "ohlcv_freshness": "OK",
            },
        )
        is None
    )


def test_hydrate_adopts_ledger_open_without_clobbering_live_book():
    eng = PaperTradeEngine(risk_policy=PaperRiskPolicy(enabled=False))
    live = PaperPosition(
        id="live-btc",
        symbol="BTCUSDT",
        side="LONG",
        status="OPEN",
        entry_price=100.0,
        stop_price=90.0,
        tp1_price=120.0,
        quantity=1.0,
        risk_usd=10.0,
        opened_at="2026-10-02T09:00:00+00:00",
        timeframe="15m",
        signal_snippet={"source": "RESEARCH_15M"},
    )
    eng._open[eng.book_key("BTCUSDT", "LEGACY")] = live
    out = eng.hydrate_from_rows(
        [
            {
                "id": "x",
                "symbol": "SOLUSDT",
                "side": "SHORT",
                "status": "OPEN",
                "entry_price": 1,
                "stop_price": 1.1,
                "quantity": 1,
                "opened_at": "2026-10-02T10:00:00+00:00",
                "source_candle_ts": "2026-10-02T09:00:00+00:00",
                "timeframe": "1h",
                "signal_snippet": {
                    "source": "PASSED_CAP_BOOK",
                    "stream": "PASSED|COMBO_01|SHORT",
                    "strategy_id": "COMBO_01",
                },
            }
        ]
    )
    assert out.get("adopted") == 1
    assert eng.get_open("BTCUSDT", "LEGACY") is live
    assert eng.get_open("SOLUSDT", "PASSED|COMBO_01|SHORT") is not None
    again = eng.open_passed_position(
        symbol="SOLUSDT",
        combination_id="COMBO_01",
        timeframe="1h",
        direction="SHORT",
        eval_result={
            "status": "SHORT_ENTRY_CANDIDATE",
            "entry_price": 1.0,
            "stop_price": 1.1,
            "tp1": 0.8,
        },
        risk_percent=0.02,
        setup_bar_time_utc="2026-10-02T09:00:00+00:00",
    )
    assert again is None


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
            "trend": {
                "15m": {"trend": "BULLISH"},
                "1h": {"trend": "BULLISH"},
                "4h": {"trend": "BULLISH"},
            },
            "mtf": {"MTF_ALIGNMENT": "STRONG_LONG"},
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
            "trend": {
                "15m": {"trend": "BULLISH"},
                "1h": {"trend": "BULLISH"},
                "4h": {"trend": "BULLISH"},
            },
            "mtf": {"MTF_ALIGNMENT": "STRONG_LONG"},
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
    # NO_SETUP is not an auto entry, even with Trend+BOS+HTF and a stop.
    assert tiers.get("BBBUSDT") == "NEAR"
    assert tiers.get("WAITUSDT") == "WAITING"
    assert rows[0]["symbol"] == "AAAUSDT"


def test_chance_words_match_bearish_columns_and_skip_non_ascii(monkeypatch):
    import app.services.engine_store as es
    import app.services.paper_trade as paper_mod
    from app.services.engine_store import EngineStore

    store = EngineStore()
    store.set_setup_signal(
        "ARBUSDT",
        {
            "status": "NO_SETUP",
            "direction": "SHORT",
            "timeframe": "15m",
            "trend": {"15m": {"trend": "BEARISH"}},
            "bos": {"state": "CONFIRMED", "direction": "BEARISH_BOS"},
            "impulse": {},
            "pullback": {},
            "retest": {},
            "entry": {},
            "stop": {},
            "targets": [],
            "data_dependencies": {"15m": "LIVE"},
        },
    )
    store.set_setup_signal(
        "WAITUSDT",
        {
            "status": "WAITING",
            "timeframe": "15m",
            "trend": {},
            "bos": {},
            "data_dependencies": {"15m": "LIVE"},
        },
    )
    store.set_setup_signal("龙虾USDT", {"status": "NO_SETUP", "timeframe": "15m"})
    monkeypatch.setattr(es, "engine_store", store)
    monkeypatch.setattr(paper_mod, "engine_store", store, raising=False)

    rows = paper_mod.list_trade_opportunities(limit=20, include_waiting=True)
    by_sym = {r["symbol"]: r for r in rows}
    assert "龙虾USDT" not in by_sym
    assert "BEARISH" in by_sym["ARBUSDT"]["chance"]
    assert "BEARISH_BOS" in by_sym["ARBUSDT"]["chance"]
    assert "missing: trend" not in by_sym["ARBUSDT"]["chance"]
    assert by_sym["WAITUSDT"]["chance"] == "Not ready — setup conditions unmet (OHLCV LIVE)"
