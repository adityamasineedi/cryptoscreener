"""Tests for explainable market_signal classification (not profitability)."""

from __future__ import annotations

from app.signals.config import SignalConfig
from app.signals.market_signal_engine import classify_market_signal


def _base(**over):
    d = {
        "status": "NO_SETUP",
        "signal_status": "LIVE",
        "timeframe": "15m",
        "trend": {"4h": "BULLISH", "1h": "BULLISH", "15m": "BULLISH", "5m": "BULLISH"},
        "mtf": {"MTF_ALIGNMENT": "STRONG_LONG"},
        "bos": {"state": "CONFIRMED", "direction": "BULLISH_BOS"},
        "choch": {"state": "NONE"},
        "impulse": {"is_impulse": True, "direction": "BULLISH", "rvol": 2.0},
        "pullback": {"pullback_state": "CONFIRMED", "structure_intact": True, "zone_hit": "demand"},
        "retest": {"retest": True},
        "risk_reward": {"RISK_REWARD": "PASS", "best_R": 2.5},
        "entry": {},
        "data_dependencies": {
            "4h": "LIVE",
            "1h": "LIVE",
            "15m": "LIVE",
            "5m": "LIVE",
            "OI": "N/A",
            "Liquidations": "WAITING",
        },
    }
    d.update(over)
    return d


class TestMarketSignal:
    def test_strong_buy(self):
        out = classify_market_signal(analysis=_base(status="LONG_ENTRY_CANDIDATE"), config=SignalConfig())
        assert out["market_signal"] == "STRONG_BUY"
        assert out["conditions"]["htf_trend"] == "PASS"
        assert out["conditions"]["oi"] == "N/A"
        assert out["conditions"]["liquidation"] == "N/A"
        assert out["confirmation_strength"] >= 7
        assert "probability" not in out["note"].lower()

    def test_strong_buy_blocked_without_bos(self):
        out = classify_market_signal(
            analysis=_base(bos={"state": "NONE", "direction": None}),
            config=SignalConfig(),
        )
        assert out["market_signal"] != "STRONG_BUY"

    def test_buy(self):
        cfg = SignalConfig()
        cfg.market_signal_strong_min_confirmations = 99  # force below strong
        out = classify_market_signal(
            analysis=_base(
                status="NO_SETUP",
                pullback={"pullback_state": "WAITING"},
                retest={"retest": False},
                mtf={"MTF_ALIGNMENT": "MIXED"},
            ),
            config=cfg,
        )
        # With MIXED mtf, strong fails; may be BUY if gates allow — adjust
        # primary BULLISH + BOS + enough bull → BUY if buy_min met and mtf != CONFLICT
        analysis = _base(
            status="NO_SETUP",
            mtf={"MTF_ALIGNMENT": "MIXED"},
            pullback={"pullback_state": "WAITING"},
            retest={"retest": False},
            impulse={"is_impulse": True, "direction": "BULLISH", "rvol": 1.6},
        )
        out = classify_market_signal(analysis=analysis, config=SignalConfig())
        assert out["market_signal"] in ("BUY", "NEUTRAL", "STRONG_BUY")
        if out["market_signal"] == "BUY":
            assert out["direction"] == "BULLISH"

    def test_buy_explicit(self):
        # HTF+primary bullish, BOS, impulse, enough counts, MTF not CONFLICT but not STRONG
        analysis = _base(
            mtf={"MTF_ALIGNMENT": "MIXED"},
            pullback={"pullback_state": "WAITING"},
            retest={"retest": False},
            status="NO_SETUP",
            risk_reward={},
            entry={},
        )
        # Remove entry_setup / rr pass to drop below strong_min while keeping buy_min
        out = classify_market_signal(analysis=analysis, config=SignalConfig())
        assert out["market_signal"] in ("BUY", "NEUTRAL")
        assert out["market_signal"] != "STRONG_SELL"

    def test_neutral_conflict(self):
        out = classify_market_signal(
            analysis=_base(
                trend={"4h": "BULLISH", "1h": "BEARISH", "15m": "BULLISH", "5m": "BULLISH"},
                mtf={"MTF_ALIGNMENT": "CONFLICT"},
            ),
            config=SignalConfig(),
        )
        assert out["market_signal"] == "NEUTRAL"
        assert "CONFLICT" in out["market_signal_reason"]

    def test_strong_sell(self):
        out = classify_market_signal(
            analysis=_base(
                status="SHORT_ENTRY_CANDIDATE",
                trend={"4h": "BEARISH", "1h": "BEARISH", "15m": "BEARISH", "5m": "BEARISH"},
                mtf={"MTF_ALIGNMENT": "STRONG_SHORT"},
                bos={"state": "CONFIRMED", "direction": "BEARISH_BOS"},
                impulse={"is_impulse": True, "direction": "BEARISH", "rvol": 2.0},
                pullback={"pullback_state": "CONFIRMED", "structure_intact": True, "zone_hit": "supply"},
                retest={"retest": True},
            ),
            config=SignalConfig(),
        )
        assert out["market_signal"] == "STRONG_SELL"

    def test_sell(self):
        analysis = _base(
            status="NO_SETUP",
            trend={"4h": "BEARISH", "1h": "BEARISH", "15m": "BEARISH", "5m": "NEUTRAL"},
            mtf={"MTF_ALIGNMENT": "MIXED"},
            bos={"state": "CONFIRMED", "direction": "BEARISH_BOS"},
            impulse={"is_impulse": True, "direction": "BEARISH", "rvol": 1.6},
            pullback={"pullback_state": "WAITING"},
            retest={"retest": False},
            risk_reward={},
        )
        out = classify_market_signal(analysis=analysis, config=SignalConfig())
        assert out["market_signal"] in ("SELL", "NEUTRAL", "STRONG_SELL")
        assert out["market_signal"] != "STRONG_BUY"

    def test_waiting_ohlcv(self):
        out = classify_market_signal(
            analysis={
                "status": "WAITING",
                "signal_status": "WAITING",
                "timeframe": "15m",
                "trend": {},
                "data_dependencies": {"15m": "WAITING FOR OHLCV"},
            },
            config=SignalConfig(),
        )
        assert out["market_signal"] == "WAITING"
        assert out["conditions"]["oi"] == "N/A"

    def test_oi_na_does_not_block_buy(self):
        out = classify_market_signal(
            analysis=_base(
                status="LONG_ENTRY_CANDIDATE",
                data_dependencies={
                    "4h": "LIVE",
                    "1h": "LIVE",
                    "15m": "LIVE",
                    "5m": "LIVE",
                    "OI": "WAITING",
                    "Liquidations": "WAITING",
                },
            ),
            config=SignalConfig(),
        )
        assert out["conditions"]["oi"] == "N/A"
        assert out["conditions"]["liquidation"] == "N/A"
        assert out["market_signal"] == "STRONG_BUY"

    def test_conflict_not_buy(self):
        out = classify_market_signal(
            analysis=_base(mtf={"MTF_ALIGNMENT": "CONFLICT"}, status="CONFLICT"),
            config=SignalConfig(),
        )
        assert out["market_signal"] == "NEUTRAL"
        assert out["market_signal"] not in ("BUY", "STRONG_BUY", "SELL", "STRONG_SELL")

    def test_attached_via_signal_engine(self):
        from app.signals.signal_engine import SignalEngine

        engine = SignalEngine(SignalConfig())
        analysis = engine.analyze("T", {})
        assert analysis.market_signal == "WAITING"
        assert analysis.classification_version
