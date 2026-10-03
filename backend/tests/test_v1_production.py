"""COMBO_02 v1 production profile — risk books + Path A wiring."""

from __future__ import annotations

import pytest

from app.research.v1_production import (
    classify_tier,
    paper_risk_percent,
    paper_symbol_allowed,
    profile_summary,
    recommended_risk_usd,
)
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine

pytestmark = pytest.mark.v1_freeze


def test_classify_core_secondary_research():
    assert classify_tier("BTCUSDT", "1h") == "core"
    assert classify_tier("ETHUSDT", "1h") == "secondary"
    assert classify_tier("SOLUSDT", "1h") == "secondary"
    assert classify_tier("BTCUSDT", "4h") == "secondary"
    assert classify_tier("BTCUSDT", "15m") == "research"


def test_recommended_risk_usd_at_1k():
    assert recommended_risk_usd("BTCUSDT", "1h") == pytest.approx(15.0)
    assert recommended_risk_usd("ETHUSDT", "1h") == pytest.approx(5.0)
    assert recommended_risk_usd("SOLUSDT", "1h") == pytest.approx(5.0)
    assert recommended_risk_usd("BTCUSDT", "4h") == pytest.approx(10.0)
    # Research books keep fallback (default $20)
    assert recommended_risk_usd("BTCUSDT", "15m") == pytest.approx(20.0)


def test_paper_universe_and_secondary_flag():
    ok, tier = paper_symbol_allowed("BTCUSDT")
    assert ok and tier == "core"
    ok, tier = paper_symbol_allowed("ETHUSDT", secondary_enabled=True)
    assert ok and tier == "secondary"
    ok, reason = paper_symbol_allowed("ETHUSDT", secondary_enabled=False)
    assert not ok and reason == "secondary_disabled"
    ok, reason = paper_symbol_allowed("DOGEUSDT")
    assert not ok and reason == "outside_v1_universe"


def test_profile_summary_shape():
    s = profile_summary()
    assert s["combo_id"] == "COMBO_02"
    assert s["combo_version"] == "v1-combo02-long-htf"
    assert any(b["tier"] == "core" for b in s["books"])
    assert "do_not" in s


@pytest.fixture(autouse=True)
def _live_near_entry(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)


def _path_a(
    *,
    timeframe: str = "15m",
    trend_4h: str = "BULLISH",
    trend_1h: str = "BULLISH",
) -> dict:
    return {
        "status": "WAITING",
        "direction": "LONG",
        "timeframe": timeframe,
        "trend": {
            timeframe: {"trend": "BULLISH"},
            "1h": {"trend": trend_1h},
            "4h": {"trend": trend_4h},
        },
        "mtf": {
            "MTF_ALIGNMENT": (
                "STRONG_LONG" if trend_4h == trend_1h == "BULLISH" else "MIXED"
            )
        },
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
        "source_candle_timestamps": {timeframe: "2026-10-02T13:15:00+00:00"},
        "calculated_at": "2026-10-02T13:20:00+00:00",
        "ohlcv_freshness": "OK",
    }


def test_path_a_v1_applies_btc_core_risk():
    eng = PaperTradeEngine(
        entry_mode="path_a",
        starting_equity=1000,
        risk_percent=0.02,
        risk_policy=PaperRiskPolicy(enabled=False),
        v1_profile_enabled=True,
    )
    eng.legacy_auto_entry_enabled = True
    pos = eng.on_setup_signal("BTCUSDT", _path_a())
    assert pos is not None
    assert pos.risk_usd == pytest.approx(15.0)
    # Legacy setup path is RESEARCH_15M — never COMBO_02_V1 identity.
    assert pos.signal_snippet.get("strategy_id") == "RESEARCH_15M"
    assert pos.signal_snippet.get("source") == "LEGACY_SETUP_SIGNAL"
    assert pos.signal_snippet.get("telegram_eligible") is False
    assert pos.signal_snippet.get("trend_1h") == "BULLISH"
    assert pos.signal_snippet.get("trend_4h") == "BULLISH"
    assert pos.signal_snippet.get("htf_alignment") in ("STRONG_LONG", "HTF_ALIGNED")


def test_path_a_v1_eth_secondary_risk():
    eng = PaperTradeEngine(
        entry_mode="path_a",
        starting_equity=1000,
        risk_policy=PaperRiskPolicy(enabled=False),
        v1_profile_enabled=True,
        v1_secondary_enabled=True,
    )
    eng.legacy_auto_entry_enabled = True
    pos = eng.on_setup_signal("ETHUSDT", _path_a())
    assert pos is not None
    assert pos.risk_usd == pytest.approx(5.0)
    assert pos.signal_snippet.get("strategy_id") == "RESEARCH_15M"
    assert paper_risk_percent("ETHUSDT") == pytest.approx(0.005)


def test_path_a_v1_blocks_outside_universe():
    eng = PaperTradeEngine(
        entry_mode="path_a",
        risk_policy=PaperRiskPolicy(enabled=False),
        v1_profile_enabled=True,
        v1_universe_only=True,
    )
    eng.legacy_auto_entry_enabled = True
    assert eng.on_setup_signal("BATUSDT", _path_a()) is None
    assert "v1_outside_v1_universe" in (eng.status()["last_skip_reason"] or "")


def test_path_a_v1_secondary_disabled_blocks_eth():
    eng = PaperTradeEngine(
        entry_mode="path_a",
        risk_policy=PaperRiskPolicy(enabled=False),
        v1_profile_enabled=True,
        v1_secondary_enabled=False,
    )
    eng.legacy_auto_entry_enabled = True
    assert eng.on_setup_signal("ETHUSDT", _path_a()) is None
    assert "secondary_disabled" in (eng.status()["last_skip_reason"] or "")
