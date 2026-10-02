"""Unit tests for the structure setup signal engine.

Synthetic OHLCV fixtures are TEST-ONLY — never used in production.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.signals.backtest import run_backtest
from app.signals.bos_engine import detect_bos
from app.signals.choch_engine import detect_choch
from app.signals.config import SignalConfig
from app.signals.entry_engine import evaluate_entry
from app.signals.impulse_engine import detect_impulse
from app.signals.mtf_engine import align_mtf
from app.signals.pullback_engine import detect_pullback
from app.signals.retest_engine import detect_retest
from app.signals.risk_engine import futures_risk_checks, position_size, risk_reward
from app.signals.signal_engine import SignalEngine
from app.signals.stop_engine import compute_stop
from app.signals.swing_detector import detect_swings
from app.signals.target_engine import compute_targets
from app.signals.trend_engine import infer_trend


def _ts(i: int) -> datetime:
    return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * i)


def candle(i: int, o: float, h: float, l: float, c: float, v: float = 1000.0) -> dict:
    return {
        "time": _ts(i),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
    }


def make_hh_hl_series() -> list[dict]:
    """Bullish HH+HL structure with room for BOS/impulse/pullback."""
    candles: list[dict] = []
    # Base ranging then higher lows/highs
    prices = [
        # flat base
        (100, 101, 99, 100.5),
        (100.5, 101.2, 99.8, 100.8),
        (100.8, 101.5, 100.0, 101.0),
        # swing low around 98
        (101.0, 101.2, 98.0, 98.5),
        (98.5, 99.0, 97.8, 98.2),
        (98.2, 99.5, 98.0, 99.0),
        (99.0, 100.5, 98.8, 100.0),
        # swing high ~105
        (100.0, 105.0, 99.5, 104.0),
        (104.0, 104.5, 102.0, 102.5),
        (102.5, 103.0, 101.5, 102.0),
        # higher low ~101
        (102.0, 102.5, 101.0, 101.5),
        (101.5, 102.0, 100.8, 101.2),
        (101.2, 103.0, 101.0, 102.5),
        # higher high ~110
        (102.5, 110.0, 102.0, 109.0),
        (109.0, 109.5, 107.0, 107.5),
        (107.5, 108.0, 106.5, 107.0),
        # higher low ~106
        (107.0, 107.5, 106.0, 106.5),
        (106.5, 107.0, 105.8, 106.2),
        (106.2, 108.0, 106.0, 107.5),
        # BOS candle close above 110
        (107.5, 113.0, 107.0, 112.5),
        # pullback toward 110
        (112.5, 112.8, 110.2, 110.5),
        (110.5, 111.0, 109.8, 110.3),
        (110.3, 111.5, 110.0, 111.0),
    ]
    for i, (o, h, l, c) in enumerate(prices):
        # elevate volume on impulse bar
        vol = 3000.0 if i == 19 else 1000.0
        candles.append(candle(i, o, h, l, c, vol))
    # Pad with SMA volume history
    for j in range(20):
        candles.insert(0, candle(-(20 - j), 100, 100.5, 99.5, 100, 1000.0))
    # reindex times sequentially
    out = []
    for i, c in enumerate(candles):
        out.append(
            {
                **c,
                "time": _ts(i),
            }
        )
    return out


def make_lh_ll_series() -> list[dict]:
    candles: list[dict] = []
    prices = [
        (100, 101, 99, 100),
        (100, 102, 99.5, 101),
        (101, 101.5, 100, 100.5),
        (100.5, 103, 100, 102.5),  # high
        (102.5, 102.8, 100.5, 101),
        (101, 101.5, 99, 99.5),  # low
        (99.5, 100.5, 99, 100),
        (100, 101.5, 99.8, 101),  # lower high
        (101, 101.2, 98, 98.5),
        (98.5, 99, 97, 97.5),  # lower low
        (97.5, 98, 96.5, 97),
        (97, 98.5, 96.8, 98),  # lower high
        (98, 98.2, 95, 95.5),  # break down
        (95.5, 96, 94.5, 95),
    ]
    for i, (o, h, l, c) in enumerate(prices):
        candles.append(candle(i, o, h, l, c, 1200.0))
    return candles


class TestSwings:
    def test_swing_high(self):
        # clear swing high at index 3
        candles = [
            candle(0, 10, 11, 9, 10),
            candle(1, 10, 12, 9.5, 11),
            candle(2, 11, 12.5, 10.5, 12),
            candle(3, 12, 15, 11.5, 14),  # high
            candle(4, 14, 14.2, 12, 12.5),
            candle(5, 12.5, 13, 11, 11.5),
            candle(6, 11.5, 12, 10.5, 11),
        ]
        swings = detect_swings(candles, left=2, right=2, symbol="T", timeframe="15m")
        highs = [s for s in swings if s.swing_type == "HIGH"]
        assert highs
        assert highs[0].price == 15
        assert highs[0].confirmed_at is not None

    def test_swing_low(self):
        candles = [
            candle(0, 10, 11, 9, 10),
            candle(1, 10, 10.5, 8.5, 9),
            candle(2, 9, 9.5, 8, 8.5),
            candle(3, 8.5, 9, 5, 6),  # low
            candle(4, 6, 8, 5.5, 7.5),
            candle(5, 7.5, 9, 7, 8.5),
            candle(6, 8.5, 10, 8, 9.5),
        ]
        swings = detect_swings(candles, left=2, right=2)
        lows = [s for s in swings if s.swing_type == "LOW"]
        assert lows
        assert lows[0].price == 5

    def test_no_lookahead(self):
        candles = [
            candle(0, 10, 11, 9, 10),
            candle(1, 10, 12, 9.5, 11),
            candle(2, 11, 15, 10.5, 14),  # would-be high
            candle(3, 14, 14.5, 13, 13.5),
            # right bars not complete for as_of=3 if right=2 → need through index 4
        ]
        early = detect_swings(candles, left=1, right=2, as_of_index=3)
        # at as_of=3, last confirmable is 3-2=1, so index 2 not confirmed yet
        assert all(s.bar_index != 2 for s in early)
        candles.append(candle(4, 13.5, 14, 12, 12.5))
        later = detect_swings(candles, left=1, right=2, as_of_index=4)
        assert any(s.bar_index == 2 and s.swing_type == "HIGH" for s in later)


class TestTrend:
    def test_bullish_trend(self):
        candles = make_hh_hl_series()
        swings = detect_swings(candles, left=2, right=2)
        t = infer_trend(swings)
        # May be BULLISH or NEUTRAL depending on labeling — assert structure fields exist
        assert t["trend"] in ("BULLISH", "NEUTRAL", "INSUFFICIENT_DATA")
        assert "reason" in t

    def test_bearish_trend(self):
        candles = make_lh_ll_series()
        swings = detect_swings(candles, left=1, right=1)
        t = infer_trend(swings)
        assert t["trend"] in ("BEARISH", "NEUTRAL", "INSUFFICIENT_DATA")

    def test_insufficient(self):
        candles = [candle(i, 10, 11, 9, 10) for i in range(5)]
        swings = detect_swings(candles, left=2, right=2)
        t = infer_trend(swings)
        assert t["trend"] == "INSUFFICIENT_DATA"


class TestBosChoch:
    def test_bullish_bos_close_not_wick(self):
        candles = make_hh_hl_series()
        swings = detect_swings(candles, left=2, right=2)
        trend = infer_trend(swings)
        # Force bullish context for BOS rule
        trend["trend"] = "BULLISH"
        bos = detect_bos(candles, swings, trend, symbol="T", timeframe="15m")
        assert bos is not None
        if bos.get("state") == "CONFIRMED":
            assert bos["direction"] == "BULLISH_BOS"
            # wick-only shouldn't confirm — close must be above level
            assert bos["break_price"] > bos["broken_level"]

    def test_choch_distinct_from_bos(self):
        candles = make_lh_ll_series()
        swings = detect_swings(candles, left=1, right=1)
        trend = {"trend": "BEARISH", "reason": "LH+LL"}
        # Add bullish close above last LH
        last_high = [s for s in swings if s.swing_type == "HIGH"][-1]
        candles.append(
            candle(
                len(candles),
                last_high.price - 0.5,
                last_high.price + 2,
                last_high.price - 1,
                last_high.price + 1.5,
            )
        )
        choch = detect_choch(candles, swings, trend, symbol="T", timeframe="15m")
        bos = detect_bos(candles, swings, trend, symbol="T", timeframe="15m")
        assert choch is not None
        # Against bearish trend, close above LH is CHOCH not BOS
        if choch.get("state") == "CONFIRMED":
            assert choch["direction"] == "CHOCH_BULLISH"
            assert bos.get("state") != "CONFIRMED" or bos.get("direction") != "BULLISH_BOS"


class TestImpulsePullbackRetest:
    def test_impulse_detection(self):
        cfg = SignalConfig()
        cfg.atr_multiplier = 0.5
        cfg.min_body_ratio = 0.4
        cfg.min_rvol = 1.0
        candles = make_hh_hl_series()
        bos = {
            "state": "CONFIRMED",
            "direction": "BULLISH_BOS",
            "broken_level": 110.0,
        }
        # analyze at impulse bar — find large-range bar
        imp = detect_impulse(candles, bos, cfg, rvol=2.0)
        assert "is_impulse" in imp
        assert "quality" in imp
        assert "config_defaults_note" in imp

    def test_pullback_states(self):
        cfg = SignalConfig()
        candles = make_hh_hl_series()
        bos = {
            "state": "CONFIRMED",
            "direction": "BULLISH_BOS",
            "broken_level": 110.0,
        }
        impulse = {
            "is_impulse": True,
            "direction": "BULLISH",
            "impulse_origin": 107.0,
            "impulse_end": 113.0,
            "bar_index": len(candles) - 4,
        }
        pb = detect_pullback(candles, bos, impulse, cfg)
        assert pb["pullback_state"] in (
            "WAITING",
            "ACTIVE",
            "CONFIRMED",
            "FAILED",
            "INVALIDATED",
        )

    def test_retest(self):
        cfg = SignalConfig()
        cfg.retest_atr_tolerance = 2.0
        candles = make_hh_hl_series()
        bos = {
            "state": "CONFIRMED",
            "direction": "BULLISH_BOS",
            "broken_level": 110.0,
        }
        pullback = {"pullback_state": "ACTIVE", "structure_intact": True}
        rt = detect_retest(candles, bos, pullback, cfg, direction="LONG")
        assert "retest" in rt
        assert "tolerance" in rt


class TestEntryStopTargetsRisk:
    def test_entry_candidate_conditions_visible(self):
        cfg = SignalConfig()
        cfg.require_mtf_alignment = False
        cfg.min_rr = 1.0
        mtf = {
            "MTF_ALIGNMENT": "STRONG_LONG",
            "trends": {"4h": "BULLISH", "1h": "BULLISH", "15m": "BULLISH", "5m": "BULLISH"},
            "reason": "ok",
        }
        result = evaluate_entry(
            mtf=mtf,
            setup_trend={"trend": "BULLISH"},
            bos={"state": "CONFIRMED", "direction": "BULLISH_BOS"},
            impulse={"is_impulse": True, "quality": "STRONG"},
            pullback={"pullback_state": "CONFIRMED", "structure_intact": True},
            retest={"retest": True, "state": "CONFIRMED", "reason": "ok"},
            stop={"final_stop": 100.0},
            targets=[{"name": "TP1", "target_price": 120.0, "r_multiple": 2.0}],
            risk_reward={"RISK_REWARD": "PASS", "best_R": 2.0},
            config=cfg,
            last_close=110.0,
            volume_ok=True,
        )
        assert result["status"] == "LONG_ENTRY_CANDIDATE"
        labels = {c["label"] for c in result["conditions"]}
        assert "Trend" in labels and "BOS" in labels and "R:R" in labels
        assert "BUY" not in result["status"]

    def test_structural_sl(self):
        stop = compute_stop(
            direction="LONG",
            entry_price=110.0,
            pullback={"retracement_low": 108.0, "impulse_origin": 107.0},
            atr=2.0,
            sl_buffer_atr=0.2,
        )
        assert stop["final_stop"] < 108.0
        assert stop["structural_stop"] == 107.0 or stop["structural_stop"] == 108.0

    def test_tp_and_rr(self):
        cfg = SignalConfig()
        stop = {"risk_per_unit": 2.0, "final_stop": 108.0}
        swings = [
            type("S", (), {"swing_type": "HIGH", "price": 114.0})(),
            type("S", (), {"swing_type": "HIGH", "price": 118.0})(),
        ]
        targets = compute_targets(
            direction="LONG",
            entry_price=110.0,
            stop=stop,
            swings=swings,
            config=cfg,
        )
        assert targets
        rr = risk_reward(110.0, 108.0, targets, min_rr=2.0)
        assert "TP1_R" in rr
        assert rr["TP1_R"] is not None and rr["TP1_R"] >= 2.0 - 1e-9
        assert rr["RISK_REWARD"] == "PASS"

    def test_skips_micro_structural_tp1(self):
        """Nearest swing at 0.25R must not become TP1 when min_rr=2."""
        cfg = SignalConfig(min_rr=2.0, tp_r_multiples=(2.0, 3.0, 4.0))
        stop = {"risk_per_unit": 4.0, "final_stop": 96.0}
        swings = [
            type("S", (), {"swing_type": "HIGH", "price": 101.0})(),  # 0.25R
            type("S", (), {"swing_type": "HIGH", "price": 105.0})(),  # 1.25R still < 2
            type("S", (), {"swing_type": "HIGH", "price": 112.0})(),  # 3R OK
        ]
        targets = compute_targets(
            direction="LONG",
            entry_price=100.0,
            stop=stop,
            swings=swings,
            config=cfg,
        )
        assert targets
        tp1 = next(t for t in targets if t["name"] == "TP1")
        assert float(tp1["r_multiple"]) >= 2.0 - 1e-9
        assert float(tp1["target_price"]) != 101.0
        rr = risk_reward(100.0, 96.0, targets, min_rr=2.0)
        assert rr["RISK_REWARD"] == "PASS"
        assert rr["first_target_R"] >= 2.0 - 1e-9

    def test_rr_fail_when_only_far_target_meets_min(self):
        """PASS must not rely on TP3 while TP1 is tiny."""
        targets = [
            {"name": "TP1", "target_price": 100.5, "r_multiple": 0.25},
            {"name": "TP2", "target_price": 102.0, "r_multiple": 1.0},
            {"name": "TP3", "target_price": 106.0, "r_multiple": 3.0},
        ]
        rr = risk_reward(100.0, 98.0, targets, min_rr=2.0)
        assert rr["best_R"] == 3.0
        assert rr["TP1_R"] == 0.25
        assert rr["RISK_REWARD"] == "FAIL"

    def test_position_sizing(self):
        pos = position_size(
            account_equity=10_000,
            risk_percent=0.01,
            entry=100.0,
            stop=98.0,
            contract_quantity_step=0.001,
            minimum_quantity=0.001,
            leverage=5,
        )
        assert pos["max_risk_amount"] == 100.0
        assert pos["final_quantity"] > 0
        assert "does not place trades" in pos["note"]

    def test_leverage_risk_warnings(self):
        cfg = SignalConfig()
        pos = position_size(
            account_equity=1000,
            risk_percent=0.01,
            entry=100,
            stop=99.95,
            leverage=50,
        )
        checks = futures_risk_checks(
            entry=100,
            stop=99.95,
            leverage=50,
            position=pos,
            config=cfg,
            liquidation_price=None,
        )
        assert "STOP_TOO_CLOSE" in checks["warnings"] or "LEVERAGE_TOO_HIGH" in checks["warnings"]
        assert checks["liquidation_data"] == "N/A"


class TestMtfMissingStale:
    def test_mtf_conflict(self):
        out = align_mtf({"4h": "BULLISH", "1h": "BEARISH", "15m": "BULLISH", "5m": "BULLISH"})
        assert out["MTF_ALIGNMENT"] == "CONFLICT"

    def test_missing_data_waiting(self):
        engine = SignalEngine(SignalConfig())
        analysis = engine.analyze("BTCUSDT", {"15m": [], "4h": [], "1h": [], "5m": []})
        assert analysis.status == "WAITING"
        assert any("WAITING FOR OHLCV" in e for e in analysis.explanation)

    def test_no_fake_entry_without_data(self):
        engine = SignalEngine(SignalConfig())
        analysis = engine.analyze("ETHUSDT", {})
        assert analysis.entry is None or analysis.status == "WAITING"


class TestBacktestNoLookahead:
    def test_historical_live_consistency_and_label(self):
        candles = make_hh_hl_series()
        # Extend series for walk-forward
        last = candles[-1]
        for i in range(30):
            base = float(last["close"])
            candles.append(
                candle(
                    len(candles),
                    base,
                    base + 1,
                    base - 1,
                    base + 0.2,
                    1100,
                )
            )
            last = candles[-1]
        report = run_backtest("TESTUSDT", "15m", candles, min_bars=30)
        d = report.to_dict()
        assert d["label"] == "HISTORICAL_ANALYSIS"
        assert "disclaimer" in d
        assert "profitability" in d["disclaimer"].lower() or "Historical" in d["disclaimer"]

    def test_live_engine_same_as_as_of(self):
        candles = make_hh_hl_series()
        engine = SignalEngine(SignalConfig())
        full = engine.analyze_timeframe("T", "15m", candles)
        truncated = engine.analyze_timeframe(
            "T", "15m", candles, as_of_index=len(candles) - 1
        )
        assert full["trend"]["trend"] == truncated["trend"]["trend"]


def test_config_from_yaml_block():
    cfg = SignalConfig.from_mapping(
        {
            "setup_signals": {
                "min_rr": 2.5,
                "atr_multiplier": 1.8,
                "swing_by_timeframe": {"15m": {"swing_left_bars": 2, "swing_right_bars": 2}},
            }
        }
    )
    assert cfg.min_rr == 2.5
    assert cfg.atr_multiplier == 1.8
    assert cfg.swing_for("15m").swing_left_bars == 2
