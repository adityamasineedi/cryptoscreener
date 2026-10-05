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
    assert recommended_risk_usd("BTCUSDT", "1h") == pytest.approx(20.0)
    assert recommended_risk_usd("ETHUSDT", "1h") == pytest.approx(20.0)
    assert recommended_risk_usd("SOLUSDT", "1h") == pytest.approx(20.0)
    assert recommended_risk_usd("BTCUSDT", "4h") == pytest.approx(20.0)
    # Research books keep fallback (default $20)
    assert recommended_risk_usd("BTCUSDT", "15m") == pytest.approx(20.0)


def test_paper_universe_and_secondary_flag():
    ok, tier = paper_symbol_allowed("BTCUSDT")
    assert ok and tier == "core"
    ok, tier = paper_symbol_allowed("ETHUSDT", secondary_enabled=True)
    assert ok and tier == "secondary"
    ok, tier = paper_symbol_allowed("BNBUSDT", secondary_enabled=True)
    assert ok and tier == "secondary"
    ok, reason = paper_symbol_allowed("ETHUSDT", secondary_enabled=False)
    assert not ok and reason == "secondary_disabled"
    ok, reason = paper_symbol_allowed("DOGEUSDT", secondary_enabled=False)
    assert not ok and reason == "secondary_disabled"
    ok, reason = paper_symbol_allowed("SOMEOBSCUREUSDT")
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


def test_path_a_legacy_and_v1_coexist_on_same_symbol(monkeypatch):
    """LEGACY + V1 streams track separately — same symbol may have both open."""
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
    eng = PaperTradeEngine(
        entry_mode="path_a",
        starting_equity=1000,
        risk_percent=0.02,
        risk_policy=PaperRiskPolicy(enabled=False),
        v1_profile_enabled=True,
    )
    eng.legacy_auto_entry_enabled = True
    eng.v1_watcher_owns_entries = False

    legacy = eng.on_setup_signal("BTCUSDT", _path_a())
    assert legacy is not None
    assert legacy.signal_snippet.get("source") == "LEGACY_SETUP_SIGNAL"
    assert eng.has_open("BTCUSDT", "LEGACY")

    from app.research.v1_production import V1Book

    v1 = eng.open_v1_combo_position(
        symbol="BTCUSDT",
        timeframe="1h",
        book=V1Book("BTCUSDT", "1h", "core", 0.02, True),
        eval_result={
            "status": "LONG_ENTRY_CANDIDATE",
            "entry_price": 100.0,
            "stop_price": 98.0,
            "tp1": 104.0,
            "htf": {
                "htf_alignment": "HTF_ALIGNED",
                "trend_1h": "BULLISH",
                "trend_4h": "BULLISH",
            },
        },
        setup_bar_time_utc="2026-10-02T15:00:00+00:00",
        emit_alert=False,
    )
    assert v1 is not None
    assert v1.signal_snippet.get("source") == "V1_PAPER_WATCHER"
    assert eng.has_open("BTCUSDT", "V1")
    assert eng.status()["open_count"] == 2
    assert eng.status()["open_by_stream"]["LEGACY"] == 1
    assert eng.status()["open_by_stream"]["V1"] == 1


def test_path_a_legacy_allows_outside_v1_universe():
    """RESEARCH_15M must not be blocked by the v1 BTC/ETH/SOL universe."""
    eng = PaperTradeEngine(
        entry_mode="path_a",
        starting_equity=1000,
        risk_policy=PaperRiskPolicy(enabled=False),
        v1_profile_enabled=True,
        v1_universe_only=True,
    )
    eng.legacy_auto_entry_enabled = True
    eng.v1_watcher_owns_entries = False
    pos = eng.on_setup_signal("BATUSDT", _path_a())
    assert pos is not None
    assert pos.signal_snippet.get("strategy_id") == "RESEARCH_15M"
    assert pos.signal_snippet.get("source") == "LEGACY_SETUP_SIGNAL"


def test_path_a_legacy_may_open_btc_when_v1_profile_off():
    """Without v1 profile, BTC remains a normal RESEARCH_15M Path A symbol."""
    eng = PaperTradeEngine(
        entry_mode="path_a",
        starting_equity=1000,
        risk_policy=PaperRiskPolicy(enabled=False),
        v1_profile_enabled=False,
    )
    eng.legacy_auto_entry_enabled = True
    eng.v1_watcher_owns_entries = False
    pos = eng.on_setup_signal("BTCUSDT", _path_a())
    assert pos is not None
    assert pos.signal_snippet.get("strategy_id") == "RESEARCH_15M"
    assert pos.signal_snippet.get("telegram_eligible") is False
    assert paper_risk_percent("ETHUSDT") == pytest.approx(0.02)
