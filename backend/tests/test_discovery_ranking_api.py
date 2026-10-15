"""Read-only API coverage for ranking a saved discovery run."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routes import research_market_structure_discovery_v1_ranking
from app.research import repository
from app.research.market_structure_discovery_v1.ranking import DISCLAIMER


@pytest.mark.asyncio
async def test_saved_passing_run_is_ranked_but_not_approved(monkeypatch) -> None:
    saved = {
        "run_id": "fixture-run",
        "results": [
            {
                "combination_id": "COMBO_01",
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "direction": "LONG",
                "research_passed": True,
            }
        ],
        "patterns": {
            "by_symbol": {
                "BTCUSDT": {
                    "clusters": [
                        {"cluster_id": 0},
                        {"cluster_id": 1},
                        {"cluster_id": 2},
                    ]
                }
            }
        },
        "embargo": {
            "train_indices": [0, 1, 2],
            "excluded_train_indices": [3],
            "embargo_indices": [4],
            "test_indices": [5, 6],
            "nested": {"preserved": True},
            "operator_approved": True,
            "production_approved": True,
            "paper_eligible": True,
            "live_eligible": True,
            "telegram_eligible": True,
        },
    }
    loader = AsyncMock(return_value=saved)
    monkeypatch.setattr(repository, "get_research_run", loader)

    response = await research_market_structure_discovery_v1_ranking("fixture-run")

    loader.assert_awaited_once_with("fixture-run")
    assert response["run_id"] == "fixture-run"
    assert response["disclaimer"] == DISCLAIMER
    assert len(response["rows"]) == 1
    row = response["rows"][0]
    assert row["research_passed"] is True
    assert row["sample_size"] is None
    assert row["promotion_status"] == "EXPERIMENTAL"
    assert row["operator_approved"] is False
    assert row["production_approved"] is False
    assert row["paper_eligible"] is False
    assert row["live_eligible"] is False
    assert row["telegram_eligible"] is False
    embargo = response["embargo"]
    assert embargo["train_indices"] == [0, 1, 2]
    assert embargo["excluded_train_indices"] == [3]
    assert embargo["embargo_indices"] == [4]
    assert embargo["test_indices"] == [5, 6]
    assert embargo["nested"] == {"preserved": True}
    assert embargo["operator_approved"] is False
    assert embargo["production_approved"] is False
    assert embargo["paper_eligible"] is False
    assert embargo["live_eligible"] is False
    assert embargo["telegram_eligible"] is False
    assert saved["embargo"]["operator_approved"] is True
    assert saved["embargo"]["production_approved"] is True
    patterns = response["patterns"]
    btc = patterns["by_symbol"]["BTCUSDT"]
    assert btc["cluster_count"] == 3
    assert "cluster_count" not in saved["patterns"]["by_symbol"]["BTCUSDT"]
    for payload in [patterns, btc, *btc["clusters"]]:
        assert payload["operator_approved"] is False
        assert payload["production_approved"] is False
        assert payload["paper_eligible"] is False
        assert payload["live_eligible"] is False
        assert payload["telegram_eligible"] is False


@pytest.mark.asyncio
async def test_saved_run_without_patterns_returns_null(monkeypatch) -> None:
    saved = {"run_id": "no-patterns", "results": []}
    loader = AsyncMock(return_value=saved)
    monkeypatch.setattr(repository, "get_research_run", loader)

    response = await research_market_structure_discovery_v1_ranking("no-patterns")

    loader.assert_awaited_once_with("no-patterns")
    assert response["rows"] == []
    assert response["embargo"] is None
    assert response["patterns"] is None


@pytest.mark.asyncio
async def test_missing_run_returns_not_found_without_rows(monkeypatch) -> None:
    loader = AsyncMock(return_value=None)
    monkeypatch.setattr(repository, "get_research_run", loader)

    with pytest.raises(HTTPException) as caught:
        await research_market_structure_discovery_v1_ranking("missing-run")

    loader.assert_awaited_once_with("missing-run")
    assert caught.value.status_code == 404
    assert "missing-run" in str(caught.value.detail)
    assert "not found" in str(caught.value.detail).lower()
    assert "rows" not in str(caught.value.detail).lower()
