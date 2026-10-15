"""Database-free unit coverage for the market-structure discovery CLI."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "run_market_structure_discovery_v1.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "run_market_structure_discovery_v1_script", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candles(symbol_offset: float, count: int = 18) -> list[dict[str, Any]]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    price = 100.0 + symbol_offset
    rows: list[dict[str, Any]] = []
    for index in range(count):
        close = price * (1.001 + 0.0001 * (index % 3))
        rows.append(
            {
                "time": start + timedelta(hours=index),
                "open": price,
                "high": close * 1.003,
                "low": price * 0.997,
                "close": close,
                "volume": 1000.0 + index,
            }
        )
        price = close
    return rows


@pytest.mark.asyncio
async def test_script_uses_fake_existing_candles_without_database() -> None:
    script = _load_script()
    source = {
        "BTCUSDT": _candles(0.0),
        "ETHUSDT": _candles(10.0),
        "SOLUSDT": _candles(20.0),
    }
    loads: list[tuple[str, str]] = []
    discovery_calls: list[dict[str, Any]] = []
    saved: list[dict[str, Any]] = []
    lifecycle: list[str] = []

    async def fake_loader(symbol: str, timeframe: str) -> list[dict[str, Any]]:
        loads.append((symbol, timeframe))
        return source[symbol]

    async def fake_discovery(datasets, *, config, persist):
        discovery_calls.append(
            {"datasets": datasets, "config": config, "persist": persist}
        )
        return {
            "run_id": "fixture-run",
            "symbols": list(script.MARKETS),
            "timeframes": ["1h"],
            "results": [],
            "trades": [],
            "operator_approved": False,
            "production_approved": False,
            "paper_eligible": False,
            "live_eligible": False,
            "telegram_eligible": False,
        }

    async def fake_saver(summary: dict[str, Any]) -> str:
        lifecycle.append("save")
        saved.append(summary)
        return str(summary["run_id"])

    def fake_embargo(**kwargs: Any) -> dict[str, Any]:
        lifecycle.append("embargo")
        assert kwargs == {
            "train_start": 0,
            "train_end": 9,
            "test_start": 9,
            "test_end": 11,
            "horizon": 1,
            "embargo_bars": 1,
        }
        return {
            "train_indices": list(range(8)),
            "excluded_train_indices": [8],
            "embargo_indices": [9],
            "test_indices": [10],
            "research_only": True,
            "operator_approved": False,
            "production_approved": False,
        }

    summary = await script.run_existing_postgres_discovery(
        candle_loader=fake_loader,
        discovery_runner=fake_discovery,
        embargo_applier=fake_embargo,
        run_saver=fake_saver,
    )

    assert loads == [(symbol, "1h") for symbol in script.MARKETS]
    assert len(discovery_calls) == 1
    call = discovery_calls[0]
    assert call["persist"] is False
    assert call["config"].combination_ids == (
        "COMBO_01",
        "COMBO_03",
        "COMBO_03_TRANSITION",
    )
    assert call["config"].timeframe == "1h"
    assert call["config"].to_dict()["directions"] == ["LONG", "SHORT"]
    assert call["config"].research_config.walk_forward_train_bars == 9
    assert call["config"].research_config.walk_forward_test_bars == 2
    assert set(call["datasets"]) == set(script.MARKETS)
    for symbol in script.MARKETS:
        assert call["datasets"][symbol]["candles"] is source[symbol]

    assert saved == [summary]
    assert lifecycle == ["embargo", "save"]
    assert summary["run_id"] == "fixture-run"
    assert summary["embargo"]["excluded_train_indices"] == [8]
    assert summary["embargo"]["embargo_indices"] == [9]
    assert set(summary["patterns"]["by_symbol"]) == set(script.MARKETS)
    assert all(
        summary["patterns"]["by_symbol"][symbol]["rows"]
        for symbol in script.MARKETS
    )
    for payload in [summary, summary["patterns"], summary["embargo"]]:
        assert payload["operator_approved"] is False
        assert payload["production_approved"] is False
        assert payload["paper_eligible"] is False
        assert payload["live_eligible"] is False
        assert payload["telegram_eligible"] is False


@pytest.mark.asyncio
async def test_missing_existing_candles_stops_before_discovery() -> None:
    script = _load_script()
    discovery_called = False

    async def fake_loader(symbol: str, timeframe: str) -> list[dict[str, Any]]:
        assert timeframe == "1h"
        return [] if symbol == "ETHUSDT" else _candles(0.0)

    async def fake_discovery(*args, **kwargs):
        nonlocal discovery_called
        discovery_called = True
        return {}

    with pytest.raises(RuntimeError, match="no rows for ETHUSDT"):
        await script.run_existing_postgres_discovery(
            candle_loader=fake_loader,
            discovery_runner=fake_discovery,
        )

    assert discovery_called is False
