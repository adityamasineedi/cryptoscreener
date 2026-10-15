"""Ranking stays experimental and does not fill in missing performance metrics."""

from __future__ import annotations

from app.research.market_structure_discovery_v1.ranking import rank_discovery_results


def _row(
    combination_id: str,
    *,
    research_passed: bool = True,
    sample_size: int | None = 20,
    profit_factor: float | None = 1.4,
    window_profit_factors: list[float | None] | None = None,
    walk_forward_windows: int | None = 2,
) -> dict:
    row = {
        "combination_id": combination_id,
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "direction": "LONG",
        "research_passed": research_passed,
        "profit_factor": profit_factor,
        "assessment": {
            "research_passed": research_passed,
            "sample_size": sample_size,
            "walk_forward_windows": walk_forward_windows,
            "operator_approved": False,
            "production_approved": False,
        },
    }
    if window_profit_factors is not None:
        row["walk_forward"] = {
            "window_count": walk_forward_windows,
            "windows": [
                {"status": "OK", "result": {"profit_factor": value}}
                for value in window_profit_factors
            ],
        }
    return row


def test_passing_row_is_not_production_approved() -> None:
    ranked = rank_discovery_results(
        [
            _row(
                "COMBO_01",
                research_passed=True,
                window_profit_factors=[1.8, 1.2],
            )
        ]
    )

    assert len(ranked) == 1
    row = ranked[0]
    assert row["research_passed"] is True
    assert row["promotion_status"] == "EXPERIMENTAL"
    assert row["operator_approved"] is False
    assert row["production_approved"] is False
    assert row["paper_eligible"] is False
    assert row["live_eligible"] is False
    assert row["telegram_eligible"] is False
    assert "profitability claim" in row["disclaimer"]


def test_missing_metrics_stay_null() -> None:
    ranked = rank_discovery_results(
        [
            {
                "combination_id": "COMBO_02",
                "research_passed": True,
            }
        ]
    )

    row = ranked[0]
    assert row["sample_size"] is None
    assert row["profit_factor"] is None
    assert row["walk_forward_windows"] is None
    assert row["oos_profit_factor"] is None
    assert row["oos_profit_factors"] is None
    assert row["sharpe"] is None
    assert row["sortino"] is None
    assert row["profit"] is None
    assert row["production_approved"] is False
    assert row["promotion_status"] == "EXPERIMENTAL"


def test_sort_order_is_stable() -> None:
    first = _row(
        "COMBO_A",
        sample_size=10,
        window_profit_factors=[1.1, 1.0],
        walk_forward_windows=2,
    )
    tied_left = _row(
        "COMBO_B",
        sample_size=10,
        window_profit_factors=[1.1, 1.0],
        walk_forward_windows=2,
    )
    tied_right = _row(
        "COMBO_C",
        sample_size=10,
        window_profit_factors=[1.1, 1.0],
        walk_forward_windows=2,
    )
    stronger = _row(
        "COMBO_D",
        sample_size=10,
        window_profit_factors=[1.4, 1.3],
        walk_forward_windows=2,
    )
    missing = {"combination_id": "COMBO_E", "research_passed": False}

    ranked = rank_discovery_results([first, tied_left, tied_right, stronger, missing])
    assert [row["combination_id"] for row in ranked] == [
        "COMBO_D",
        "COMBO_A",
        "COMBO_B",
        "COMBO_C",
        "COMBO_E",
    ]
    assert [row["rank"] for row in ranked] == [1, 2, 3, 4, 5]
    assert [row["source_index"] for row in ranked] == [3, 0, 1, 2, 4]
    assert ranked[-1]["profit_factor"] is None
    assert ranked[-1]["oos_profit_factors"] is None
    assert all(row["promotion_status"] == "EXPERIMENTAL" for row in ranked)
    assert all(row["production_approved"] is False for row in ranked)
    assert all(row["profit"] is None for row in ranked)
