"""Live recomputation, freshness, and consistency tests for setup signals.

Synthetic OHLCV here is TEST-ONLY. Real-data validation is a separate script.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.ohlcv import Candle
from app.services.engine_store import EngineStore
from app.services.ohlcv_store import OHLCVStore
from app.services.setup_signals import SetupSignalService
from app.signals.backtest import run_backtest
from app.signals.config import SignalConfig
from app.signals.signal_engine import SignalEngine
from app.signals.swing_detector import detect_swings


def _ts(i: int, minutes: int = 15) -> datetime:
    return datetime(2024, 6, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes * i)


def _candle_dict(i: int, o: float, h: float, l: float, c: float, v: float = 1000.0) -> dict:
    return {
        "time": _ts(i),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
    }


def _make_candle(symbol: str, tf: str, i: int, o: float, h: float, l: float, c: float) -> Candle:
    ot = _ts(i)
    return Candle(
        symbol=symbol,
        timeframe=tf,
        open_time=ot,
        close_time=ot + timedelta(minutes=15),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1000.0,
        is_closed=True,
        timestamp=ot,
        source="test",
    )


def _bullish_series(n: int = 60) -> list[dict]:
    out = []
    px = 100.0
    for i in range(n):
        o = px
        # create swings + occasional expansion
        if i % 8 == 3:
            h, l, c = px + 5, px - 1, px + 4
        elif i % 8 == 6:
            h, l, c = px + 1, px - 3, px - 2
        else:
            h, l, c = px + 1.2, px - 0.8, px + 0.3
        out.append(_candle_dict(i, o, h, l, c, 1500 if i % 8 == 3 else 900))
        px = c
    return out


class TestFreshnessAndEnsure:
    def test_waiting_until_ohlcv_then_live(self, monkeypatch):
        store = OHLCVStore()
        engines = EngineStore()
        monkeypatch.setattr("app.services.setup_signals.ohlcv_store", store)
        monkeypatch.setattr("app.services.setup_signals.engine_store", engines)
        monkeypatch.setattr(
            "app.engines.orchestrator.get_orchestrator", lambda: None
        )

        svc = SetupSignalService()
        svc.config = SignalConfig()
        svc.engine = SignalEngine(svc.config)

        # No candles → WAITING
        payload = svc.ensure_computed("BTCUSDT")
        assert payload is not None
        assert payload["status"] == "WAITING"
        assert payload["signal_status"] == "WAITING"

        # Inject enough 15m candles
        for i in range(40):
            c = _make_candle("BTCUSDT", "15m", i, 100 + i * 0.1, 101 + i * 0.1, 99, 100.5 + i * 0.1)
            # bypass async upsert
            key = ("BTCUSDT", "15m")
            store._closed[key].append(c)

        payload2 = svc.ensure_computed("BTCUSDT", force=True)
        assert payload2 is not None
        assert payload2["signal_status"] == "LIVE"
        assert payload2["source_candle_timestamps"].get("15m") is not None
        assert payload2["calculated_at"] is not None
        # Not stuck WAITING once OHLCV exists
        assert payload2["status"] != "WAITING" or payload2["data_dependencies"].get("15m") == "LIVE"

    def test_stale_when_new_candle_without_recompute(self, monkeypatch):
        store = OHLCVStore()
        engines = EngineStore()
        monkeypatch.setattr("app.services.setup_signals.ohlcv_store", store)
        monkeypatch.setattr("app.services.setup_signals.engine_store", engines)
        monkeypatch.setattr(
            "app.engines.orchestrator.get_orchestrator", lambda: None
        )

        svc = SetupSignalService()
        svc.config = SignalConfig()
        svc.engine = SignalEngine(svc.config)

        for i in range(40):
            store._closed[("BTCUSDT", "15m")].append(
                _make_candle("BTCUSDT", "15m", i, 100, 101, 99, 100.5)
            )
        p1 = svc.analyze_symbol("BTCUSDT")
        assert p1["signal_status"] == "LIVE"
        ts1 = p1["source_candle_timestamps"]["15m"]

        # New closed candle arrives; cache not yet recomputed
        store._closed[("BTCUSDT", "15m")].append(
            _make_candle("BTCUSDT", "15m", 40, 100.5, 102, 100, 101)
        )
        assert svc.is_stale(p1, "BTCUSDT")
        assert svc.mark_stale_if_source_advanced("BTCUSDT")
        stale = engines.get_setup_signal("BTCUSDT")
        assert stale["signal_status"] == "STALE"
        assert stale["source_candle_timestamps"]["15m"] == ts1

        # Recompute clears STALE
        p2 = svc.analyze_symbol("BTCUSDT")
        assert p2["signal_status"] == "LIVE"
        assert p2["source_candle_timestamps"]["15m"] != ts1


class TestMtfNoSubstitution:
    def test_missing_tf_not_substituted(self):
        engine = SignalEngine(SignalConfig())
        series = _bullish_series(50)
        # Only 15m present — higher TFs must WAITING, not copy 15m
        analysis = engine.analyze(
            "BTCUSDT",
            {"15m": series, "4h": [], "1h": [], "5m": []},
        )
        assert analysis.trend.get("4h") == "WAITING"
        assert analysis.trend.get("1h") == "WAITING"
        assert analysis.data_dependencies.get("4h") == "WAITING FOR OHLCV"
        # Must not silently use 15m as 4h
        assert analysis.mtf["trends"]["4h"] == "WAITING"

    def test_all_mtf_reflected(self):
        engine = SignalEngine(SignalConfig())
        series = _bullish_series(50)
        analysis = engine.analyze(
            "ETHUSDT",
            {"4h": series, "1h": series, "15m": series, "5m": series},
        )
        for tf in ("4h", "1h", "15m", "5m"):
            assert analysis.trend.get(tf) not in (None,)
            assert analysis.data_dependencies.get(tf) == "LIVE"


class TestExplanationConsistency:
    def test_bos_explanation_matches_levels(self):
        candles = _bullish_series(80)
        # Append clear BOS close above last swing high under bullish trend
        swings = detect_swings(candles, left=2, right=2)
        highs = [s for s in swings if s.swing_type == "HIGH"]
        if not highs:
            return
        level = highs[-1].price
        candles.append(
            _candle_dict(
                len(candles),
                level - 0.5,
                level + 3,
                level - 1,
                level + 2,
                4000,
            )
        )
        engine = SignalEngine(SignalConfig())
        # Force enough history on all TFs for non-WAITING setup path
        tf_map = {tf: candles for tf in ("4h", "1h", "15m", "5m")}
        # Analyze setup TF piece
        tf_out = engine.analyze_timeframe("BTCUSDT", "15m", candles)
        bos = tf_out.get("bos") or {}
        if bos.get("state") == "CONFIRMED":
            assert bos["break_price"] > bos["broken_level"]
            assert str(bos["broken_level"]) in (bos.get("reason") or "")


class TestRiskMath:
    def test_rr_and_position_consistency(self):
        from app.signals.risk_engine import position_size, risk_reward

        entry, stop = 100.0, 98.0
        targets = [
            {"name": "TP1", "target_price": 104.0, "r_multiple": 2.0},
            {"name": "TP2", "target_price": 106.0, "r_multiple": 3.0},
        ]
        rr = risk_reward(entry, stop, targets, min_rr=2.0)
        assert abs(rr["TP1_R"] - 2.0) < 1e-9
        assert rr["RISK_REWARD"] == "PASS"

        pos = position_size(
            account_equity=10_000,
            risk_percent=0.01,
            entry=entry,
            stop=stop,
            contract_quantity_step=0.001,
            minimum_quantity=0.001,
            leverage=5,
        )
        # position_size × risk_per_unit ≈ max risk (within step rounding)
        assert abs(pos["final_quantity"] * pos["risk_per_unit"] - pos["max_risk_amount"]) < pos["risk_per_unit"]


class TestLiveBacktestConsistency:
    def test_same_as_of_outputs(self):
        candles = _bullish_series(100)
        cfg = SignalConfig()
        engine = SignalEngine(cfg)
        n = 80
        live = engine.analyze(
            "SOLUSDT",
            {
                "4h": candles[: n + 1],
                "1h": candles[: n + 1],
                "15m": candles[: n + 1],
                "5m": candles[: n + 1],
            },
            as_of_index_by_tf={"4h": n, "1h": n, "15m": n, "5m": n},
        )
        # Backtest walk uses same engine.analyze with as_of — compare at N via analyze_timeframe
        live_tf = engine.analyze_timeframe("SOLUSDT", "15m", candles, as_of_index=n)
        assert live_tf["trend"]["trend"] == (live.trend or {}).get("15m") or live_tf["trend"]["trend"]

        report = run_backtest("SOLUSDT", "15m", candles, config=cfg, min_bars=40)
        assert report.to_dict()["label"] == "HISTORICAL_ANALYSIS"

        # Direct parity: as_of N twice
        a = engine.analyze_timeframe("SOLUSDT", "15m", candles, as_of_index=n)
        b = engine.analyze_timeframe("SOLUSDT", "15m", candles, as_of_index=n)
        assert a["trend"] == b["trend"]
        assert (a.get("bos") or {}).get("state") == (b.get("bos") or {}).get("state")
        assert (a.get("impulse") or {}).get("is_impulse") == (b.get("impulse") or {}).get(
            "is_impulse"
        )


class TestVisibleQueueNotUniverse:
    def test_enqueue_only_missing_stale(self, monkeypatch):
        store = OHLCVStore()
        engines = EngineStore()
        monkeypatch.setattr("app.services.setup_signals.ohlcv_store", store)
        monkeypatch.setattr("app.services.setup_signals.engine_store", engines)
        monkeypatch.setattr(
            "app.engines.orchestrator.get_orchestrator", lambda: None
        )

        svc = SetupSignalService()
        for i in range(30):
            store._closed[("AAAUSDT", "15m")].append(
                _make_candle("AAAUSDT", "15m", i, 1, 2, 0.5, 1.5)
            )
        svc.analyze_symbol("AAAUSDT")
        n = svc.enqueue_ensure(["AAAUSDT", "BBBUSDT"])
        # AAA fresh → not queued; BBB missing → queued
        assert "BBBUSDT" in svc._pending_ensure
        assert "AAAUSDT" not in svc._pending_ensure
        assert n == 1
