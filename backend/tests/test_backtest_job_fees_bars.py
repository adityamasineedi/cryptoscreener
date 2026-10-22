"""Backtest job request defaults: explicit 0 fees and completed bar counts."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.research.backtest_job import BacktestJobService, _completed_bars


def _fake_matrix(calls: list[dict[str, Any]]):
    async def fake_matrix(**kwargs):
        calls.append(kwargs)
        cb = kwargs.get("progress_callback")
        if cb is not None:
            cb({"bars_processed": 1000, "total_bars": 2160, "trades": 0})
        return {
            "status": "OK",
            "rows": [
                {
                    "symbol": "BTCUSDT",
                    "timeframe": "1h",
                    "sample_size": 0,
                    "period_start": "2024-01-01",
                    "period_end": "2024-03-31",
                    "bars_loaded": 2260,
                    "warmup_bars": 100,
                    "bars_evaluated": 2160,
                }
            ],
            "playbook": "test",
            "combination_name": "COMBO_02",
            "disclaimer": "x",
            "label": "LONG_STRATEGY_BACKTEST",
            "dataset_id": "ds",
            "timing": {},
        }

    return fake_matrix


async def _start_via_route(body: dict[str, Any]) -> tuple[dict, dict, list[dict]]:
    from app.api.routes import research_long_strategy_backtest_start

    svc = BacktestJobService()
    calls: list[dict[str, Any]] = []
    mock_svc = AsyncMock()
    mock_svc.strategy_matrix = _fake_matrix(calls)
    base = {
        "symbols": ["BTCUSDT"],
        "timeframes": ["1h"],
        "direction": "LONG",
        "combination_id": "COMBO_02",
        "strategy_id": "COMBO_02_V1",
        "start_date": "2024-01-01",
        "end_date": "2024-03-31",
        "principal_usd": 1000,
        "risk_usd": 20,
        "leverage": 2,
        "research_risk_override": True,
    }
    with (
        patch("app.research.backtest_job.backtest_job_service", svc),
        patch("app.research.service.get_bos_research_service", return_value=mock_svc),
    ):
        started = await research_long_strategy_backtest_start({**base, **body})
        for _ in range(100):
            if svc.status().get("status") in ("done", "error", "cancelled", "stalled"):
                break
            await asyncio.sleep(0.02)
        final = svc.status()
    return started, final, calls


@pytest.mark.parametrize("zero", [0, 0.0, "0"])
def test_explicit_zero_fees_reach_job_and_cell(zero):
    started, final, calls = asyncio.run(
        _start_via_route({"taker_fee_pct": zero, "maker_fee_pct": zero})
    )
    assert started["status"] == "STARTED", started
    job = started["job"]
    assert job["taker_fee_pct"] == 0
    assert job["maker_fee_pct"] == 0
    assert final["status"] == "done"
    assert len(calls) == 1
    assert calls[0]["taker_fee"] == 0.0
    assert calls[0]["maker_fee"] == 0.0


@pytest.mark.parametrize("missing", [None, "", "absent"])
def test_missing_fees_use_defaults(missing):
    body = {} if missing == "absent" else {
        "taker_fee_pct": missing,
        "maker_fee_pct": missing,
    }
    started, final, calls = asyncio.run(_start_via_route(body))
    assert started["status"] == "STARTED", started
    job = started["job"]
    # Owner D144 / D202 defaults: taker 0.05%, maker 0.02%.
    assert job["taker_fee_pct"] == pytest.approx(0.05)
    assert job["maker_fee_pct"] == pytest.approx(0.02)
    assert calls[0]["taker_fee"] == pytest.approx(0.0005)
    assert calls[0]["maker_fee"] == pytest.approx(0.0002)


def test_completed_bars_processed_matches_evaluated_bars():
    _, final, _ = asyncio.run(_start_via_route({}))
    assert final["status"] == "done"
    assert final["bars_processed"] == 2160
    assert final["total_bars"] == 2160
    assert final["bars_processed"] <= final["total_bars"]


def test_completed_bars_helper_fallbacks():
    assert _completed_bars({"bars_evaluated": 2160, "bars_loaded": 2260}, 5) == 2160
    assert _completed_bars({"bars_loaded": 2260, "warmup_bars": 100}, 5) == 2160
    assert _completed_bars({"bars_loaded": 1000}, 5) == 1000
    assert _completed_bars({}, 7) == 7
    assert _completed_bars(None, 3) == 3


def test_multi_cap_run_route_passes_explicit_zero_costs():
    from app.api.routes import research_multi_cap_strategies_run

    captured: dict[str, Any] = {}

    class _Svc:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return {"status": "OK"}

    with patch(
        "app.research.multi_cap_strategies.service.get_multi_cap_strategies_service",
        return_value=_Svc(),
    ):
        asyncio.run(
            research_multi_cap_strategies_run(
                {"taker_fee": 0, "fees": 0.0005, "maker_fee": "0", "slippage": 0}
            )
        )
        assert captured["taker_fee"] == 0
        assert captured["maker_fee"] == "0"
        assert captured["slippage_rate"] == 0

        captured.clear()
        asyncio.run(research_multi_cap_strategies_run({"taker_fee": None, "fees": 0.0005}))
        assert captured["taker_fee"] == 0.0005
        assert captured["maker_fee"] is None
        assert captured["slippage_rate"] is None
