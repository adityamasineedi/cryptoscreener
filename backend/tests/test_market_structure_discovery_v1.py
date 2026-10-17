"""Safety and orchestration tests for market-structure discovery v1."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.research.bos_combinations import COMBINATIONS
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.config import ResearchConfig
from app.research.market_structure_discovery_v1 import (
    MARKETS,
    DiscoveryConfig,
    run_discovery,
)
from app.research.repository import _MEMORY_RUNS, _MEMORY_TRADES, memory_clear
from app.signals.config import SignalConfig


def _candles(count: int = 80) -> list[dict]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    price = 100.0
    for index in range(count):
        close = price + 0.4 + (0.2 if index % 7 == 0 else 0.0)
        rows.append(
            {
                "time": start + timedelta(hours=index),
                "open": price,
                "high": close + 0.8 + 2.0 * ((index % 10) / 10.0),
                "low": price - 0.6,
                "close": close,
                "volume": 3000.0 if index % 19 == 0 else 1000.0,
            }
        )
        price = close
    return rows


def test_future_bars_after_t_do_not_change_signal_at_t() -> None:
    series = _candles(100)
    kwargs = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "as_of_index": 60,
        "combination": COMBINATIONS["COMBO_01"],
        "signal_config": SignalConfig(),
        "research_config": ResearchConfig(min_bars=40),
        "compute_sd": False,
    }
    with_future = evaluate_combination_at_bar(candles=series, **kwargs)
    at_t_only = evaluate_combination_at_bar(candles=series[:61], **kwargs)

    assert with_future.get("status") == at_t_only.get("status")
    assert with_future.get("direction") == at_t_only.get("direction")
    assert with_future.get("gates") == at_t_only.get("gates")


@pytest.mark.anyio
async def test_passing_result_cannot_set_production_approved(monkeypatch) -> None:
    # Keep persistence in memory. An earlier test in a full run
    # (test_combo02_v2_causal_execution's causal replay) connects the global
    # db_manager and leaves it connected; without this the fake run below is
    # sent to the real research_runs table (asyncpg rejects the ISO-string
    # created_at, so it errors instead of writing) and the _MEMORY_* asserts
    # never run.
    from app.services.database import db_manager

    monkeypatch.setattr(db_manager, "enabled", False)
    memory_clear()
    calls = {"backtest": 0, "walk_forward": 0}

    def fake_backtest(symbol, timeframe, candles, combination, **kwargs):
        calls["backtest"] += 1
        direction = kwargs["direction_filter"]
        return {
            "status": "OK",
            "sample_size": 2,
            "candles_processed": len(candles),
            "setups_processed": 2,
            "result": {"sample_size": 2},
            "trades": [
                {
                    "combination_id": combination,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "direction": direction,
                    "entry_index": 10,
                    "signal_time": "2025-01-01T10:00:00+00:00",
                }
            ],
        }

    def fake_walk_forward(*args, **kwargs):
        calls["walk_forward"] += 1
        return {"windows": [{"status": "OK", "sample_size": 1}], "window_count": 1}

    monkeypatch.setattr(
        "app.research.market_structure_discovery_v1.runner.run_combination_backtest",
        fake_backtest,
    )
    monkeypatch.setattr(
        "app.research.market_structure_discovery_v1.runner.run_walk_forward",
        fake_walk_forward,
    )
    datasets = {symbol: {"candles": _candles(20)} for symbol in MARKETS}
    summary = await run_discovery(
        datasets,
        config=DiscoveryConfig(
            combination_ids=("COMBO_01",),
            research_config=ResearchConfig(
                min_bars=5,
                walk_forward_train_bars=10,
                walk_forward_test_bars=5,
            ),
        ),
    )

    assert calls == {"backtest": 6, "walk_forward": 6}
    assert summary["operator_approved"] is False
    assert summary["production_approved"] is False
    assert all(row["research_passed"] is True for row in summary["results"])
    assert all(row["production_approved"] is False for row in summary["results"])
    assert all(row["assessment"]["production_approved"] is False for row in summary["results"])
    assert all(trade["production_approved"] is False for trade in summary["trades"])
    assert summary["run_id"] in _MEMORY_RUNS
    assert len(_MEMORY_TRADES) == 6


def test_rejects_unknown_combo_and_unbounded_inputs() -> None:
    with pytest.raises(ValueError, match="unknown COMBO"):
        DiscoveryConfig(combination_ids=("COMBO_NEW",)).validated_combination_ids()

    datasets = {symbol: {"candles": _candles(20)} for symbol in MARKETS}
    datasets.pop("SOLUSDT")
    with pytest.raises(ValueError, match="exactly"):
        # Validation occurs before any worker/backtester call.
        import asyncio

        asyncio.run(run_discovery(datasets, persist=False))
