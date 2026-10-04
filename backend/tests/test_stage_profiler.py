"""Stage profiler must be off by default and no-op when disabled."""

from __future__ import annotations

import os

from app.research.data_cache.stage_profiler import StageProfiler, profile_enabled


def test_profile_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RESEARCH_PROFILE_ENABLED", raising=False)
    assert profile_enabled() is False


def test_profiler_noop_when_disabled():
    p = StageProfiler(enabled=False)
    with p.time("structure"):
        pass
    snap = p.snapshot()
    assert snap["stages"] == {}
    assert snap["enabled"] is False


def test_profiler_aggregates_when_enabled(monkeypatch):
    monkeypatch.setenv("RESEARCH_PROFILE_ENABLED", "true")
    p = StageProfiler()
    p.reset(symbol="BTCUSDT")
    assert p.enabled is True
    with p.time("structure"):
        x = sum(range(1000))
    assert x >= 0
    snap = p.snapshot()
    assert snap["stages"]["structure"]["calls"] == 1
    assert snap["stages"]["structure"]["elapsed_ms"] >= 0
