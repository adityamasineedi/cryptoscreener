"""GET /paper/passed-regime is a pure read of the passed-book regime view.

The watcher is the real class with a stubbed daily trend and a recording paper
engine. The route must not call the engine, must not change ``watcher.enabled``
and must not evaluate a bar.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes
from app.services import passed_paper_watcher as watcher_mod

STATUS_KEYS = {
    "enabled",
    "trend",
    "as_of_daily_bar",
    "daily_bars",
    "allowed",
    "skipped",
    "allowed_strategy_ids",
    "skipped_strategy_ids",
    "latest_signals",
    "last_evaluated_bar",
    "last_skip",
    "opened_count",
    "daily_read_ok",
    "daily_read_error",
    "regime_bar",
    "regime_read_at",
    "min_daily_bars",
    "short_history",
}
ROW_KEYS = {"strategy_id", "timeframe", "direction"}


class _RecordingEngine:
    """Any attribute access is a call the route was not allowed to make."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        self.calls.append(name)

        def _refuse(*_a: Any, **_kw: Any) -> Any:
            raise AssertionError(f"route called paper engine: {name}")

        return _refuse


class _Clock:
    """Stands in for RegimeClock: the trend in ``regime``, the daily bar due at each moment."""

    def __init__(self, regime: dict[str, Any]) -> None:
        self.regime = regime

    def at(self, moment: datetime) -> dict[str, Any]:
        day = moment.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        return {"trend": self.regime["trend"], "as_of_bar": day.isoformat(), "bars": self.regime["bars"]}


def _due_daily_bar(regime_bar: str) -> str:
    close = datetime.fromisoformat(regime_bar) + timedelta(hours=1)
    return (close.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)).isoformat()


@pytest.fixture()
def harness(monkeypatch):
    engine = _RecordingEngine()
    watcher_mod.reset_passed_paper_watcher_for_tests()
    watcher = watcher_mod.get_passed_paper_watcher(enabled=True, paper_engine=engine)
    regime = {"trend": "BULLISH", "bars": 120}
    monkeypatch.setattr(
        watcher,
        "read_daily_snapshot",
        lambda: watcher_mod.DailySnapshot(_Clock(regime), read_at="2026-10-14T00:00:00+00:00"),
    )
    settings = SimpleNamespace(paper_passed_watcher_enabled=True)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "get_orchestrator", lambda: None)

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    yield SimpleNamespace(
        engine=engine,
        watcher=watcher,
        regime=regime,
        settings=settings,
        client=TestClient(app),
    )
    watcher_mod.reset_passed_paper_watcher_for_tests()


def test_route_is_get_only():
    methods = {
        method
        for route in routes.router.routes
        if getattr(route, "path", "") == "/paper/passed-regime"
        for method in route.methods
    }
    assert methods == {"GET"}


def test_returns_regime_status_keys(harness):
    res = harness.client.get("/api/paper/passed-regime")
    assert res.status_code == 200
    body = res.json()
    assert STATUS_KEYS <= set(body)
    assert body["trend"] == "BULLISH"
    assert body["daily_read_ok"] is True
    assert body["daily_read_error"] == ""
    assert body["as_of_daily_bar"] == _due_daily_bar(body["regime_bar"])
    assert body["daily_bars"] == 120
    assert body["min_daily_bars"] == 40
    assert body["short_history"] is False
    assert body["watcher_loop_enabled"] is True
    assert body["timestamp"]
    assert body["allowed"], "a bullish trend allows at least one passed side"
    for row in body["allowed"] + body["skipped"]:
        assert set(row) == ROW_KEYS
    # The route adds nothing to the book: it is the watcher's own answer.
    status = harness.watcher.regime_status()
    assert body["allowed"] == status["allowed"]
    assert body["skipped"] == status["skipped"]
    assert body["latest_signals"] == []
    assert body["last_evaluated_bar"] is None
    assert body["opened_count"] == 0


@pytest.mark.parametrize("trend", ["BULLISH", "BEARISH", "NEUTRAL"])
def test_trend_is_passed_through(harness, trend):
    harness.regime["trend"] = trend
    body = harness.client.get("/api/paper/passed-regime").json()
    assert body["trend"] == trend
    if trend == "NEUTRAL":
        assert body["allowed"] == []


@pytest.mark.parametrize("enabled", [True, False])
def test_read_changes_nothing(harness, enabled):
    watcher = harness.watcher
    watcher.enabled = enabled
    watcher._record_signal(
        strategy_id="COMBO_03_TRANSITION_B",
        direction="LONG",
        timeframe="1h",
        symbol="BTCUSDT",
        setup_bar_time="2026-10-13T09:00:00+00:00",
        trend="BULLISH",
        as_of_daily_bar="2026-10-13T00:00:00+00:00",
        outcome="not_opened",
        reason="late_tip",
    )
    before = (
        watcher.enabled,
        watcher._paper,
        watcher.last_skip,
        watcher.opened_count,
        watcher.last_evaluated_bar,
        len(watcher._signals),
        set(watcher._processed),
        set(watcher._seeded),
    )

    for _ in range(3):
        body = harness.client.get("/api/paper/passed-regime").json()

    assert body["enabled"] is enabled
    assert len(body["latest_signals"]) == 1
    assert body["latest_signals"][0]["reason"] == "late_tip"
    assert harness.engine.calls == []
    assert watcher_mod.get_passed_paper_watcher() is watcher
    assert before == (
        watcher.enabled,
        watcher._paper,
        watcher.last_skip,
        watcher.opened_count,
        watcher.last_evaluated_bar,
        len(watcher._signals),
        set(watcher._processed),
        set(watcher._seeded),
    )


def test_reports_the_loop_setting_separately(harness):
    harness.settings.paper_passed_watcher_enabled = False
    body = harness.client.get("/api/paper/passed-regime").json()
    assert body["watcher_loop_enabled"] is False
    assert body["enabled"] is True
    assert harness.watcher.enabled is True


def test_mutating_methods_are_not_routed(harness):
    for method in ("post", "put", "delete", "patch"):
        res = getattr(harness.client, method)("/api/paper/passed-regime")
        assert res.status_code == 405
    assert harness.engine.calls == []
