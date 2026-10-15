"""Read-only API coverage for stored discovery trades."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routes import research_market_structure_discovery_v1_trades
from app.research.repository import memory_clear, save_research_run


@pytest.fixture(autouse=True)
def _clear_research_memory() -> None:
    memory_clear()
    yield
    memory_clear()


@pytest.mark.asyncio
async def test_stored_trade_is_returned_without_derived_profit(monkeypatch) -> None:
    monkeypatch.setattr("app.research.repository.db_manager.enabled", False)
    await save_research_run(
        {
            "run_id": "stored-trade-run",
            "trades": [
                {
                    "symbol": "BTCUSDT",
                    "combination_id": "COMBO_01",
                    "direction": "LONG",
                    "entry_index": 42,
                    "signal_time": "2026-01-02T03:00:00+00:00",
                    "entry_price": 100.0,
                    "stop_price": 95.0,
                    "outcome": "TP1",
                    "r_multiple": 2.0,
                    "profit": 10.0,
                    "operator_approved": True,
                }
            ],
        }
    )

    response = await research_market_structure_discovery_v1_trades(
        "stored-trade-run"
    )

    assert response == [
        {
            "symbol": "BTCUSDT",
            "combination_id": "COMBO_01",
            "direction": "LONG",
            "entry_index": 42,
            "signal_time": "2026-01-02T03:00:00+00:00",
            "entry_price": 100.0,
            "stop_price": 95.0,
            "tp1": None,
            "outcome": "TP1",
            "r_multiple": 2.0,
            "operator_approved": False,
            "production_approved": False,
            "paper_eligible": False,
            "live_eligible": False,
            "telegram_eligible": False,
        }
    ]
    assert "profit" not in response[0]


@pytest.mark.asyncio
async def test_missing_run_returns_not_found_and_no_trades(monkeypatch) -> None:
    monkeypatch.setattr("app.research.repository.db_manager.enabled", False)

    with pytest.raises(HTTPException) as caught:
        await research_market_structure_discovery_v1_trades("missing-run")

    assert caught.value.status_code == 404
    assert "missing-run" in str(caught.value.detail)
