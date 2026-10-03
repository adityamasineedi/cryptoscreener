"""Presentation-only Futures Screener safety — discovery vs COMBO_02 v1.

Does not open paper trades or alter strategy engines.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.screener_presentation import (
    STATUS_DISCOVERY_ONLY,
    STATUS_V1_DATA_UNAVAILABLE,
    STATUS_V1_ELIGIBLE,
    STATUS_V1_PAPER_OPEN,
    STATUS_V1_WAITING_NO_BOS,
    build_potential_levels,
    classify_v1_status,
    enrich_screener_row_dict,
)


def _fv(value: Any, status: str = "LIVE") -> dict[str, Any]:
    return {
        "value": value,
        "timestamp": None,
        "source": "test",
        "status": status,
        "methodology": None,
    }


def test_non_v1_symbol_always_discovery_only():
    status = classify_v1_status(
        symbol="NEARUSDT",
        snapshot={
            "eval": {
                "status": "LONG_ENTRY_CANDIDATE",
                "direction": "LONG",
                "combination_id": "COMBO_02",
                "gates": {"bos": True, "trend": True, "htf": True},
                "htf": {
                    "htf_alignment": "HTF_ALIGNED",
                    "trend_1h": "BULLISH",
                    "trend_4h": "BULLISH",
                },
                "entry_price": 1.0,
                "stop_price": 0.9,
            }
        },
    )
    assert status["in_v1_universe"] is False
    assert status["status"] == STATUS_DISCOVERY_ONLY
    assert status["label"] == "DISCOVERY ONLY"


def test_fifteen_m_bullish_row_never_becomes_v1_eligible():
    """A generic 15m row cannot be displayed as V1 ELIGIBLE from screen fields."""
    row = {
        "symbol": "NEARUSDT",
        "setup_trend": _fv("BULLISH"),
        "market_signal": _fv("BUY"),
        "setup_signal": _fv("WAITING"),
        "setup_entry": _fv(None, "WAITING"),
        "setup_sl": _fv(4.7692),
        "setup_tp1": _fv(4.836),
        "setup_rr": _fv(4.0),
    }
    enriched = enrich_screener_row_dict(
        row,
        screen_timeframe="15m",
        v1_snapshots={},  # no watcher snapshot
        open_trades={},
    )
    assert enriched["is_telegram_eligible"] is False
    assert enriched["v1_status"]["status"] == STATUS_DISCOVERY_ONLY
    assert enriched["local_trend"] == "BULLISH"
    assert enriched["screen_setup"] == "WAITING"
    # Even if someone later mistags NEAR as v1 with empty snap → UNAVAILABLE, never ELIGIBLE
    eth_like = enrich_screener_row_dict(
        {**row, "symbol": "ETHUSDT"},
        screen_timeframe="15m",
        v1_snapshots={},
        open_trades={},
    )
    assert eth_like["v1_status"]["status"] == STATUS_V1_DATA_UNAVAILABLE
    assert eth_like["v1_status"]["status"] != STATUS_V1_ELIGIBLE


def test_v1_status_comes_from_watcher_snapshot_not_15m():
    # Screen looks "ready" but snapshot says waiting / no BOS
    row = {
        "symbol": "ETHUSDT",
        "setup_trend": _fv("BULLISH"),
        "market_signal": _fv("BUY"),
        "setup_signal": _fv("ENTRY_READY"),
        "setup_entry": _fv(3000.0),
        "setup_sl": _fv(2900.0),
        "setup_tp1": _fv(3200.0),
        "setup_rr": _fv(2.0),
    }
    snap = {
        "evaluated_at_utc": "2026-01-01T00:00:00+00:00",
        "tip_bar": "2026-01-01T00:00:00+00:00",
        "eval": {
            "status": "NO_SETUP",
            "gates": {"bos": False, "trend": True, "htf": True},
            "htf": {
                "htf_alignment": "HTF_ALIGNED",
                "trend_1h": "BULLISH",
                "trend_4h": "BULLISH",
            },
        },
    }
    enriched = enrich_screener_row_dict(
        row,
        screen_timeframe="15m",
        v1_snapshots={"ETHUSDT": snap},
        open_trades={},
        v1_books={"ETHUSDT": {"tier": "secondary", "risk_percent": 0.005}},
    )
    assert enriched["v1_status"]["status"] == STATUS_V1_WAITING_NO_BOS
    assert enriched["v1_status"]["trend_1h"] == "BULLISH"
    assert enriched["is_telegram_eligible"] is False


def test_missing_snapshot_is_data_unavailable_not_inferred():
    status = classify_v1_status(symbol="BTCUSDT", snapshot=None)
    assert status["status"] == STATUS_V1_DATA_UNAVAILABLE
    assert "never inferred" in (status.get("tooltip") or "").lower() or "unavailable" in (
        status.get("label") or ""
    ).lower()


def test_v1_eligible_only_from_is_v1_long_entry_snapshot():
    good = {
        "eval": {
            "status": "LONG_ENTRY_CANDIDATE",
            "direction": "LONG",
            "combination_id": "COMBO_02",
            "gates": {"bos": True, "trend": True, "htf": True},
            "required": ["bos", "trend", "htf"],
            "htf": {
                "htf_alignment": "HTF_ALIGNED",
                "trend_1h": "BULLISH",
                "trend_4h": "BULLISH",
            },
            "entry_price": 100.0,
            "stop_price": 95.0,
        },
        "evaluated_at_utc": "2026-01-01T00:00:00+00:00",
    }
    status = classify_v1_status(symbol="BTCUSDT", snapshot=good)
    assert status["status"] == STATUS_V1_ELIGIBLE

    # Same screen-looking data but incomplete gates → not eligible
    bad = {
        "eval": {
            "status": "LONG_ENTRY_CANDIDATE",
            "direction": "LONG",
            "combination_id": "COMBO_02",
            "gates": {"bos": True, "trend": True, "htf": False},
            "htf": {
                "htf_alignment": "HTF_MISALIGNED",
                "trend_1h": "BULLISH",
                "trend_4h": "BEARISH",
            },
            "entry_price": 100.0,
            "stop_price": 95.0,
        }
    }
    blocked = classify_v1_status(symbol="BTCUSDT", snapshot=bad)
    assert blocked["status"] != STATUS_V1_ELIGIBLE
    assert "BLOCKED" in blocked["status"]


def test_open_trade_shows_paper_open():
    status = classify_v1_status(
        symbol="SOLUSDT",
        snapshot=None,
        open_trade_id="abc-123",
    )
    assert status["status"] == STATUS_V1_PAPER_OPEN
    assert "abc-123" in (status.get("label") or "")


def test_waiting_conflict_potential_levels_are_reference_only():
    for setup in ("WAITING", "CONFLICT"):
        levels = build_potential_levels(
            {
                "setup_signal": _fv(setup),
                "setup_entry": _fv(None, "WAITING"),
                "setup_sl": _fv(1.23),
                "setup_tp1": _fv(1.45),
                "setup_rr": _fv(4.0),
            }
        )
        assert levels["is_confirmed"] is False
        assert levels["reference_only"] is True
        assert levels["display_mode"] == "candidate"
        assert "Reference only" in levels["tooltip"]
        assert levels["entry"] is None
        assert levels["stop"] == 1.23


def test_enrich_always_telegram_ineligible():
    for sym in ("NEARUSDT", "ETHUSDT", "BTCUSDT", "1000SHIBUSDT"):
        out = enrich_screener_row_dict(
            {"symbol": sym, "setup_signal": _fv("WAITING")},
            screen_timeframe="15m",
            v1_snapshots={
                "ETHUSDT": {
                    "eval": {
                        "status": "LONG_ENTRY_CANDIDATE",
                        "direction": "LONG",
                        "combination_id": "COMBO_02",
                        "gates": {"bos": True, "trend": True, "htf": True},
                        "required": ["bos", "trend", "htf"],
                        "htf": {
                            "htf_alignment": "HTF_ALIGNED",
                            "trend_1h": "BULLISH",
                            "trend_4h": "BULLISH",
                        },
                        "entry_price": 1.0,
                        "stop_price": 0.9,
                    }
                }
            },
        )
        assert out["is_telegram_eligible"] is False


def test_enrich_payload_does_not_open_trades(monkeypatch):
    """enrich_screener_payload must not call paper open / watcher on_closed."""
    from app.services import screener_presentation as sp

    opened: list[str] = []

    class FakeWatcher:
        books = []

        def get_presentation_snapshots(self, *, refresh: bool = True):
            return {}

    class FakePaper:
        def open_v1_combo_position(self, *a, **k):
            opened.append("open")
            return None

        _open = {}
        _lock = __import__("threading").RLock()

    monkeypatch.setattr(
        "app.services.v1_paper_watcher.get_v1_paper_watcher",
        lambda: FakeWatcher(),
    )
    monkeypatch.setattr(
        "app.services.paper_trade.get_paper_trade_engine",
        lambda: FakePaper(),
    )
    monkeypatch.setattr(
        "app.services.setup_signals.get_setup_signal_service",
        lambda: type("S", (), {"config": type("C", (), {"mtf_setup": "15m"})()})(),
    )

    payload = {
        "rows": [
            {
                "symbol": "NEARUSDT",
                "setup_trend": _fv("BULLISH"),
                "setup_signal": _fv("CONFLICT"),
                "setup_sl": _fv(1.0),
            }
        ]
    }
    out = sp.enrich_screener_payload(payload, screen_timeframe="15m")
    assert opened == []
    assert out["is_telegram_eligible"] is False
    assert out["rows"][0]["is_telegram_eligible"] is False
    assert out["rows"][0]["v1_status"]["status"] == STATUS_DISCOVERY_ONLY
    assert out["screen_timeframe"] == "15m"
    assert out["screener_identity"]["title"] == "GENERAL MARKET SCREENER"


@pytest.mark.v1_freeze
def test_store_presentation_snapshot_never_opens(monkeypatch):
    from app.services.v1_paper_watcher import V1PaperWatcher, reset_v1_paper_watcher_for_tests
    from app.services.paper_trade import PaperTradeEngine

    reset_v1_paper_watcher_for_tests()
    paper = PaperTradeEngine(enabled=True, entry_mode="path_a", v1_profile_enabled=True)
    paper.v1_watcher_owns_entries = True
    watcher = V1PaperWatcher(paper_engine=paper, emit_alerts=False)
    watcher._store_presentation_snapshot(
        "BTCUSDT",
        eval_result={
            "status": "LONG_ENTRY_CANDIDATE",
            "direction": "LONG",
            "combination_id": "COMBO_02",
            "gates": {"bos": True, "trend": True, "htf": True},
            "required": ["bos", "trend", "htf"],
            "htf": {
                "htf_alignment": "HTF_ALIGNED",
                "trend_1h": "BULLISH",
                "trend_4h": "BULLISH",
            },
            "entry_price": 100.0,
            "stop_price": 95.0,
        },
        tip_bar="2026-01-01T00:00:00+00:00",
    )
    snaps = watcher.get_presentation_snapshots(refresh=False)
    assert "BTCUSDT" in snaps
    assert snaps["BTCUSDT"]["read_only"] is True
    assert snaps["BTCUSDT"]["opens_trades"] is False
    assert paper._open == {}
