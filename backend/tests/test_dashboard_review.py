"""Read-only dashboard snapshot. No engine is created and no file is written."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.dashboard_review_routes import paper_dashboard_review, router
from app.services.dashboard_review import build_dashboard_review, missed_breakdown


def _settings(**flags: bool) -> SimpleNamespace:
    base = {
        "paper_v1_watcher_enabled": False,
        "paper_passed_watcher_enabled": False,
        "paper_candle12_v2_btc_long_enabled": False,
        "paper_book16_4h_enabled": False,
        "paper_legacy_auto_entry_enabled": False,
        "paper_v21a_watcher_enabled": False,
        "paper_v1_telegram_enabled": False,
        "paper_v21a_telegram_enabled": False,
    }
    base.update(flags)
    return SimpleNamespace(**base)


def test_route_is_get_only_and_does_not_create_the_engine(monkeypatch):
    methods = {tuple(sorted(getattr(route, "methods", []) or [])) for route in router.routes}
    assert methods == {("GET",)}
    import app.services.paper_trade as paper

    monkeypatch.setattr(paper, "_engine", None)
    seen = {}

    def _build(**kwargs):
        seen.update(kwargs)
        return {"read_only": True}

    monkeypatch.setattr("app.api.dashboard_review_routes.build_dashboard_review", _build)
    import asyncio

    assert asyncio.run(paper_dashboard_review()) == {"read_only": True}
    assert seen["engine"] is None
    assert paper._engine is None


def test_snapshot_reads_flags_and_writes_nothing(tmp_path):
    now = datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc)
    snap = build_dashboard_review(
        root=tmp_path,
        settings=_settings(paper_v1_watcher_enabled=True, paper_v1_telegram_enabled=True),
        engine=None,
        now=now,
    )
    assert list(tmp_path.rglob("*")) == []
    assert snap["read_only"] is True
    assert snap["reset_blocked"] is True
    assert [row["id"] for row in snap["forward_tests"] if row["running"]] == ["V1"]
    passed = next(row for row in snap["watchers"] if row["id"] == "PASSED")
    assert len(passed["coins"]) == 27
    atom = next(row for row in passed["coins"] if row["symbol"] == "ATOMUSDT")
    assert atom == {"symbol": "ATOMUSDT", "group": "mid", "risk_percent": 0.01}
    assert "V2.1-A" not in snap["telegram_text"]
    assert "COMBO_02 v1" in snap["telegram_text"]


def test_missed_breakdown_keeps_unknown_rows_unknown(tmp_path):
    paper = tmp_path / "data" / "paper"
    paper.mkdir(parents=True)
    (paper / "passed_paper_watermark.json").write_text(
        json.dumps(
            {
                "missed_signals": 160,
                "unevaluated_bars": 34,
                "recent_missed": [
                    {
                        "symbol": "DOGEUSDT",
                        "reason": "late_tip",
                        "strategy_id": "COMBO_02",
                        "recorded_at": "2026-10-19T17:44:00+00:00",
                        "setup_bar_time": "2026-10-18T19:00:00+00:00",
                    },
                    {
                        "symbol": "TONUSDT",
                        "reason": "history_short",
                        "strategy_id": "COMBO_01",
                        "recorded_at": "2026-10-21T03:50:00+00:00",
                        "setup_bar_time": "2026-10-21T02:00:00+00:00",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    snap = build_dashboard_review(root=tmp_path, settings=_settings(), engine=None)
    missed = snap["missed"]
    assert missed["counter"] == 160
    assert missed["by_reason"]["late bar"] == 1
    assert missed["by_reason"]["ohlcv_catchup"] == 1
    assert missed["by_reason"]["sleep"] == 0
    assert missed["by_reason"]["crash"] == 0
    assert missed["unretained"] == 159
    assert missed["after_restart_unknown"] == []
    assert missed["after_restart"][0]["bucket"] == "ohlcv_catchup"
    assert missed_breakdown([], {"missed_signals": 3, "recent_missed": []})["unretained"] == 3


def test_candle12_v2_heartbeat_is_the_state_write_not_the_watermark(tmp_path):
    """The watermark is the fixed forward start; the state file's last write is the heartbeat."""
    import os

    state_dir = tmp_path / "backend" / "data" / "candle12_v2_paper"
    state_dir.mkdir(parents=True)
    path = state_dir / "btcusdt_long.json"
    path.write_text(json.dumps({
        "symbol": "BTCUSDT", "risk_usd": 20.0, "account_equity": 1000.0,
        "watermark": "2026-10-19T17:00:00+00:00", "open": None,
        "closed": [{"net_pnl_usd": -21.35, "outcome": "SL"}],
    }), encoding="utf-8")
    written = datetime(2026, 10, 22, 7, 0, tzinfo=timezone.utc)
    os.utime(path, (written.timestamp(), written.timestamp()))
    now = datetime(2026, 10, 22, 7, 30, tzinfo=timezone.utc)
    snap = build_dashboard_review(root=tmp_path, settings=_settings(), engine=None, now=now)
    row = next(r for r in snap["runners"] if r["runner"] == "CANDLE12_V2")
    assert row["status"] == "FRESH"
    assert row["age_seconds"] == 1800.0
    c12 = snap["candle12_v2"]
    assert c12["trades"] == 1
    assert abs(c12["realized_pnl"] - (-21.35)) < 1e-9
    assert abs(c12["net_r"] - (-21.35 / 20.0)) < 1e-9
    assert abs(c12["equity"] - 978.65) < 1e-9


def test_v1_card_lists_only_the_watcher_symbols(tmp_path):
    """Owner D200 (DECISIONS D233): the card follows paper_v1_watcher_symbols, as the watcher does."""
    from app.research.v1_production import V1_BOOKS
    from app.services.dashboard_review import v1_watcher_symbols

    every = [b.symbol for b in V1_BOOKS if b.timeframe == "1h" and b.enabled_by_default]
    assert {"BTCUSDT", "ETHUSDT", "SOLUSDT"} <= set(every) and len(every) > 3

    def v1(settings):
        snap = build_dashboard_review(root=tmp_path, settings=settings, engine=None)
        return snap, [c["symbol"] for c in next(w for w in snap["watchers"] if w["id"] == "V1")["coins"]]

    snap, coins = v1(_settings(paper_v1_telegram_enabled=True, paper_v1_watcher_symbols=" btcusdt, ETHUSDT ,SOLUSDT,"))
    assert coins == [s for s in every if s in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}]
    assert "COMBO_02 v1 (3 1h books)" in snap["telegram_text"]

    # Empty or missing setting: the watcher keeps every book, so the card does too.
    assert v1(_settings(paper_v1_watcher_symbols=""))[1] == every
    assert v1(_settings())[1] == every
    assert v1_watcher_symbols(_settings(paper_v1_watcher_symbols="BTCUSDT,,")) == frozenset({"BTCUSDT"})
