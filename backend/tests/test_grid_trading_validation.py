"""Sample split, walk-forward, and grid-count sweep stay on their own bars."""

from __future__ import annotations

import pytest

from app.grid_trading.config import GridConfig, GridConfigError
from app.grid_trading.engine import run_grid
from app.grid_trading.validation import robustness, split_sample, sweep_grid_count, walk_forward


def _candles(count: int) -> list[dict]:
    rows = []
    for i in range(count):
        price = 105.0 if i % 2 == 0 else 100.0
        rows.append(
            {
                "time": i,
                "open": 105.0,
                "high": max(112.0, price),
                "low": min(99.0, price),
                "close": price,
            }
        )
    return rows


def _config() -> GridConfig:
    return GridConfig(
        range_low=100.0,
        range_high=110.0,
        grid_count=1,
        capital=1000.0,
        fee_bps=0.0,
        slippage_bps=0.0,
        stop_price=90.0,
        max_drawdown=1.0,
        max_open_orders=5,
        intrabar_path="buy_then_sell",
    )


def test_split_does_not_share_bars():
    report = split_sample(_candles(10), _config(), is_fraction=0.6)
    assert report["in_sample_bars"] == 6
    assert report["out_of_sample_bars"] == 4
    assert report["in_sample"]["bar_count"] == 6
    assert report["out_of_sample"]["bar_count"] == 4


def test_walk_forward_windows_step_by_the_test_length():
    report = walk_forward(_candles(12), _config(), train_bars=4, test_bars=4)
    assert [window["start"] for window in report["windows"]] == [0, 4]
    assert report["windows"][0]["train"]["bar_count"] == 4
    assert report["windows"][1]["test"]["bar_count"] == 4


def test_sweep_reports_a_rejected_count_without_dropping_the_rest():
    report = sweep_grid_count(
        _candles(6),
        _config().model_copy(update={"funding_mode": "exclude"}),
        [1, 0, 2],
    )
    assert report["ranked"] is True
    assert report["label"] == "funding excluded"
    assert report["funding_status"] == "excluded"
    by_count = {row["grid_count"]: row for row in report["rows"]}
    assert by_count[1]["accepted"] is True
    assert by_count[0]["error"] == "grid_count"
    assert by_count[2]["grid_count"] == 2
    assert "overfit" in report["warning"].lower()
    scores = [row["robustness"] for row in report["rows"]]
    assert scores == sorted(scores, reverse=True)
    assert "robustness" in by_count[1]


def test_sweep_net_pnl_includes_funding():
    candles = []
    for time in range(6):
        candles.append(
            {
                "time": time,
                "open": 105.0,
                "high": 106.0,
                "low": 99.0 if time == 1 else 100.0,
                "close": 102.0,
            }
        )
    funding = [{"time": 1, "rate": 0.05, "mark_price": 100.0}]
    unfunded = _config().model_copy(update={"funding_mode": "exclude"})
    plain = run_grid(candles, unfunded)
    report = sweep_grid_count(candles, _config(), [1], funding)
    direct = run_grid(candles, _config(), funding)
    row = next(item for item in report["rows"] if item["grid_count"] == 1)
    assert plain["net_pnl"] > 0 > direct["net_pnl"]
    assert direct["funding_paid"] > 0
    assert row["funding_paid"] == pytest.approx(direct["funding_paid"])
    assert row["net_pnl"] == pytest.approx(direct["net_pnl"])
    without = robustness(candles, unfunded)
    with_funding = robustness(candles, _config(), funding)
    assert with_funding["profitable_neighbors"] < without["profitable_neighbors"]
    assert row["robustness"] == pytest.approx(with_funding["robustness"])


def test_charge_without_settlements_is_not_ranked():
    report = sweep_grid_count(_candles(6), _config(), [1, 2])
    assert report["funding_status"] == "unavailable"
    assert report["ranked"] is False
    assert report["rows"] == []
    assert "Funding is unavailable" in report["reason"]
    score = robustness(_candles(6), _config())
    assert score["ranked"] is False
    assert score["robustness"] is None


def test_short_split_is_rejected():
    with pytest.raises(GridConfigError):
        split_sample(_candles(3), _config(), is_fraction=0.7)
