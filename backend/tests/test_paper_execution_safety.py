"""Part A — v1 execution-safety: legacy auto-entry off, classification, Telegram, monitor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.alerts import AlertFeed
from app.services.paper_classification import (
    STRATEGY_COMBO_02_V1,
    SOURCE_V1_PAPER_WATCHER,
    STRATEGY_RESEARCH_15M,
    SOURCE_LEGACY_SETUP_SIGNAL,
)
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine
from app.services.telegram_alerts import is_v1_paper_alert
from app.research.v1_production import V1Book

pytestmark = pytest.mark.v1_freeze


def _path_a_payload(symbol: str = "TRXUSDT", timeframe: str = "15m") -> dict:
    return {
        "status": "WAITING",
        "direction": "LONG",
        "timeframe": timeframe,
        "trend": {
            "15m": {"trend": "BULLISH"},
            "1h": {"trend": "BULLISH"},
            "4h": {"trend": "BULLISH"},
        },
        "mtf": {"MTF_ALIGNMENT": "STRONG_LONG"},
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


def _v1_eval() -> dict:
    return {
        "status": "LONG_ENTRY_CANDIDATE",
        "entry_price": 100.0,
        "stop_price": 98.0,
        "tp1": 104.0,
        "htf": {
            "trend_1h": "BULLISH",
            "trend_4h": "BULLISH",
            "htf_alignment": "HTF_ALIGNED",
        },
    }


def test_legacy_auto_entry_default_off_blocks_paper_but_bos_alerts_still_emit(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
    feed = AlertFeed()
    eng = PaperTradeEngine(
        enabled=True,
        risk_policy=PaperRiskPolicy(enabled=False),
        v1_profile_enabled=False,
    )
    assert eng.legacy_auto_entry_enabled is False

    pos = eng.on_setup_signal("TRXUSDT", _path_a_payload())
    assert pos is None
    assert "legacy_auto_entry_disabled" in (eng._last_skip_reason or "")

    # Screener/BOS UI path continues independently of paper opens.
    bos_alert = feed.emit(
        alert_type="BOS",
        symbol="TRXUSDT",
        timeframe="15m",
        severity="watch",
        title="BULLISH_BOS confirmed",
        detail="structure only",
        payload={"direction": "BULLISH_BOS", "state": "CONFIRMED"},
        dedupe_key="bos|TRXUSDT|test",
    )
    assert bos_alert is not None
    assert bos_alert["type"] == "BOS"


def test_legacy_auto_entry_on_persists_research_15m_classification(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
    eng = PaperTradeEngine(
        enabled=True,
        risk_policy=PaperRiskPolicy(enabled=False),
        v1_profile_enabled=False,
    )
    eng.legacy_auto_entry_enabled = True
    pos = eng.on_setup_signal("QNTUSDT", _path_a_payload("QNTUSDT"))
    assert pos is not None
    snip = pos.signal_snippet
    assert snip["strategy_id"] == STRATEGY_RESEARCH_15M
    assert snip["source"] == SOURCE_LEGACY_SETUP_SIGNAL
    assert snip["combo_id"] is None
    assert snip["combo_version"] is None
    assert snip["path"] == "LEGACY"
    assert snip["timeframe"] == "15m"
    assert snip["telegram_eligible"] is False


def test_v1_watcher_trade_classified_telegram_eligible(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
    eng = PaperTradeEngine(
        enabled=True,
        risk_policy=PaperRiskPolicy(enabled=False),
        v1_profile_enabled=True,
    )
    book = V1Book("BTCUSDT", "1h", "core", 0.02, True)
    pos = eng.open_v1_combo_position(
        symbol="BTCUSDT",
        timeframe="1h",
        book=book,
        eval_result=_v1_eval(),
        setup_bar_time_utc="2026-10-02T14:00:00+00:00",
        emit_alert=False,
    )
    assert pos is not None
    snip = pos.signal_snippet
    assert snip["strategy_id"] == STRATEGY_COMBO_02_V1
    assert snip["source"] == SOURCE_V1_PAPER_WATCHER
    assert snip["combo_id"] == "COMBO_02"
    assert snip["combo_version"] == "v1-combo02-long-htf"
    assert snip["path"] == "A"
    assert snip["timeframe"] == "1h"
    assert snip["telegram_eligible"] is True


@pytest.mark.parametrize(
    "alert",
    [
        {
            "type": "PAPER_ENTRY",
            "symbol": "TRXUSDT",
            "timeframe": "15m",
            "telegram_eligible": False,
            "payload": {
                "symbol": "TRXUSDT",
                "timeframe": "15m",
                "signal_snippet": {
                    "strategy_id": "RESEARCH_15M",
                    "source": "LEGACY_SETUP_SIGNAL",
                    "path": "LEGACY",
                    "telegram_eligible": False,
                },
            },
        },
        {
            "type": "PAPER_ENTRY",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "payload": {
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "signal_snippet": {
                    "strategy_id": "RESEARCH_15M",
                    "source": "LEGACY_SETUP_SIGNAL",
                    "path": "LEGACY",
                    "telegram_eligible": False,
                    "combo_id": "COMBO_02",
                    "combo_version": "v1-combo02-long-htf",
                },
            },
        },
        {
            "type": "PAPER_ENTRY",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "payload": {
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "signal_snippet": {
                    "strategy_id": "EXPERIMENTAL_PATH_B",
                    "source": "LEGACY_SETUP_SIGNAL",
                    "path": "B",
                    "telegram_eligible": False,
                },
            },
        },
        {
            "type": "PAPER_ENTRY",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "strategy_id": "COMBO_02_V1",
            "source": "V1_PAPER_WATCHER",
            "telegram_eligible": True,
            "payload": {
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "signal_snippet": {
                    "strategy_id": "COMBO_02_V1",
                    "source": "V1_PAPER_WATCHER",
                    "combo_id": "COMBO_02",
                    "combo_version": "v1-combo02-long-htf",
                    "path": "A",
                    "timeframe": "1h",
                    "telegram_eligible": True,
                    # missing HTF — fail closed
                },
            },
        },
    ],
)
def test_telegram_rejects_legacy_path_b_and_incomplete_htf(alert):
    assert not is_v1_paper_alert(alert)


def test_telegram_accepts_full_v1_watcher_alert():
    alert = {
        "type": "PAPER_ENTRY",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "strategy_id": "COMBO_02_V1",
        "source": "V1_PAPER_WATCHER",
        "telegram_eligible": True,
        "payload": {
            "id": "t1",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "signal_snippet": {
                "strategy_id": "COMBO_02_V1",
                "source": "V1_PAPER_WATCHER",
                "combo_id": "COMBO_02",
                "combo_version": "v1-combo02-long-htf",
                "path": "A",
                "timeframe": "1h",
                "htf_alignment": "HTF_ALIGNED",
                "trend_1h": "BULLISH",
                "trend_4h": "BULLISH",
                "telegram_eligible": True,
                "v1_tier": "core",
            },
        },
    }
    assert is_v1_paper_alert(alert)


def test_v1_monitor_excludes_non_v1_records(tmp_path: Path):
    import importlib.util
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "v1_monitor_paper.py"
    spec = importlib.util.spec_from_file_location("v1_monitor_paper", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["v1_monitor_paper"] = mod
    spec.loader.exec_module(mod)
    partition_trades = mod.partition_trades
    summarize = mod.summarize

    trades = [
        {
            "status": "CLOSED",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "r_multiple": 1.5,
            "signal_snippet": {
                "strategy_id": "COMBO_02_V1",
                "source": "V1_PAPER_WATCHER",
                "combo_id": "COMBO_02",
                "combo_version": "v1-combo02-long-htf",
                "path": "A",
                "timeframe": "1h",
            },
        },
        {
            "status": "CLOSED",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "r_multiple": 2.0,
            "signal_snippet": {
                "strategy_id": "RESEARCH_15M",
                "source": "LEGACY_SETUP_SIGNAL",
                "path": "LEGACY",
                "timeframe": "15m",
            },
        },
        {
            "status": "CLOSED",
            "symbol": "ETHUSDT",
            "timeframe": "1h",
            "r_multiple": -1.0,
            "signal_snippet": {
                "strategy_id": "EXPERIMENTAL_PATH_B",
                "source": "LEGACY_SETUP_SIGNAL",
                "path": "B",
                "timeframe": "1h",
            },
        },
        {
            "status": "CLOSED",
            "symbol": "SOLUSDT",
            "timeframe": "4h",
            "r_multiple": 0.5,
            "signal_snippet": {
                "strategy_id": "RESEARCH_15M",
                "source": "LEGACY_SETUP_SIGNAL",
                "path": "LEGACY",
                "timeframe": "4h",
            },
        },
    ]
    part = partition_trades(trades)
    assert len(part["v1_eligible"]) == 1
    assert part["excluded_wrong_timeframe"] >= 1
    assert part["excluded_path_b"] == 1
    rows = summarize(part["v1_eligible"])
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["n"] == 1

    out = tmp_path / "closed.json"
    out.write_text(json.dumps(trades), encoding="utf-8")
    assert out.exists()


def test_close_legacy_requires_confirm_and_skips_v1(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
    eng = PaperTradeEngine(
        enabled=True,
        risk_policy=PaperRiskPolicy(enabled=False),
        v1_profile_enabled=False,
    )
    eng.legacy_auto_entry_enabled = True
    legacy = eng.on_setup_signal("ARBUSDT", _path_a_payload("ARBUSDT"))
    assert legacy is not None

    book = V1Book("BTCUSDT", "1h", "core", 0.02, True)
    v1 = eng.open_v1_combo_position(
        symbol="BTCUSDT",
        timeframe="1h",
        book=book,
        eval_result=_v1_eval(),
        setup_bar_time_utc="2026-10-02T15:00:00+00:00",
        emit_alert=False,
    )
    assert v1 is not None

    denied = eng.close_legacy_paper_positions(confirm=False)
    assert denied["ok"] is False
    assert eng.status()["open_count"] == 2

    result = eng.close_legacy_paper_positions(confirm=True, reason="legacy_cleanup")
    assert result["ok"] is True
    assert result["closed_count"] == 1
    assert "BTCUSDT" in result["skipped_v1"]
    assert eng.get_open("BTCUSDT", "V1") is not None
    assert eng.get_open("ARBUSDT", "LEGACY") is None
    archived = eng._closed[0]
    assert archived.exit_reason == "legacy_cleanup"
    assert archived.signal_snippet.get("telegram_eligible") is False
