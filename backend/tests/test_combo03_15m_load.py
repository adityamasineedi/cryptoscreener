"""strategy_matrix loads 15m strategy candles for COMBO_03 variants that need them."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.research.combo03_transition.variants import (
    COMBO_03_FAMILY,
    combination_requires_15m,
    require_15m_confirmation,
    variant_for_combination_id,
)

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
_STEP = {"15m": timedelta(minutes=15), "1h": timedelta(hours=1)}


def _candles(timeframe: str, n: int = 400) -> list[dict]:
    step = _STEP.get(timeframe, timedelta(hours=1))
    return [
        {"time": T0 + step * i, "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1.0}
        for i in range(n)
    ]


def _run_matrix(monkeypatch, combination_id: str) -> tuple[list[str], dict]:
    import app.research.backtest_cpu_pool as pool
    import app.research.market_structure as ms
    import app.research.service as service

    loaded: list[str] = []
    walk: dict = {}

    async def fake_load(symbol, timeframe, **kwargs):
        loaded.append(timeframe)
        return _candles(timeframe), 0, {"symbol": symbol, "timeframe": timeframe}

    async def fake_htf(symbol, **kwargs):
        return None, None

    async def fake_walk(**kwargs):
        walk.update(kwargs)
        return {"trades": [], "result": {}, "status": "SUCCESS_EMPTY", "sample_size": 0}

    async def fake_post(data, **kwargs):
        return {"combination_id": data["combination_id"], "direction": data["direction"], "trades": []}

    monkeypatch.setattr(service, "_load_research_candles", fake_load)
    monkeypatch.setattr(service, "_load_htf_candles_for_combo", fake_htf)
    monkeypatch.setattr(pool, "run_combination_backtest_isolated", fake_walk)
    monkeypatch.setattr(pool, "run_matrix_postprocess_isolated", fake_post)
    monkeypatch.setattr(ms, "resolve_analytics_flags", lambda settings: (False, False))
    asyncio.run(
        service.BosResearchService().strategy_matrix(
            combination_id=combination_id, symbols=["BTCUSDT"], timeframes=["1h"], direction="LONG"
        )
    )
    return loaded, walk


@pytest.mark.parametrize(
    ("combination_id", "expected"),
    [
        ("COMBO_03_TRANSITION", True),
        ("COMBO_03_TRANSITION_B", False),
        ("COMBO_03_TRANSITION_C", True),
        ("COMBO_03", False),
        ("COMBO_02_V2", False),
        ("", False),
        (None, False),
    ],
)
def test_combination_requires_15m(combination_id, expected) -> None:
    assert combination_requires_15m(combination_id) is expected


@pytest.mark.parametrize("combination_id", sorted(COMBO_03_FAMILY))
def test_matrix_loads_15m_exactly_when_variant_requires_it(monkeypatch, combination_id) -> None:
    needs_15m = require_15m_confirmation(variant_for_combination_id(combination_id))
    loaded, walk = _run_matrix(monkeypatch, combination_id)

    assert walk, "backtest walk was not called"
    assert ("15m" in loaded) is needs_15m
    if needs_15m:
        c15 = walk["candles_15m"]
        assert c15 and c15[1]["time"] - c15[0]["time"] == timedelta(minutes=15)
    else:
        assert walk["candles_15m"] is None


@pytest.mark.parametrize(
    ("combination_id", "needs_15m"),
    [("COMBO_02_V2", True), ("COMBO_02", False)],
)
def test_matrix_15m_load_unchanged_for_other_families(monkeypatch, combination_id, needs_15m) -> None:
    loaded, walk = _run_matrix(monkeypatch, combination_id)
    assert ("15m" in loaded) is needs_15m
    assert (walk["candles_15m"] is not None) is needs_15m
