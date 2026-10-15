"""Causality, missing-data, and approval safety for hidden-pattern research."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.research.market_structure_discovery_v1.patterns import (
    assign_clusters,
    attach_forward_return_labels,
    build_point_in_time_features,
    fit_cluster_model,
    summarize_clusters,
)


def _candles(count: int = 30) -> list[dict]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows: list[dict] = []
    price = 100.0
    for index in range(count):
        close = price * (1.002 + 0.0002 * (index % 4))
        rows.append(
            {
                "time": start + timedelta(hours=index),
                "open": price,
                "high": max(price, close) * 1.004,
                "low": min(price, close) * 0.996,
                "close": close,
                "volume": 1000.0 + 25.0 * index,
            }
        )
        price = close
    return rows


def test_mutating_candles_after_t_does_not_change_feature_at_t() -> None:
    candles = _candles()
    t = 15
    before = build_point_in_time_features(candles)[t]

    mutated = [dict(candle) for candle in candles]
    for index in range(t + 1, len(mutated)):
        mutated[index].update(
            open=1_000_000.0,
            high=2_000_000.0,
            low=1.0,
            close=1_500_000.0,
            volume=9_000_000.0,
        )
    after = build_point_in_time_features(mutated)[t]

    assert before == after


def test_null_volume_stays_null() -> None:
    candles = _candles()
    candles[10]["volume"] = None
    features = build_point_in_time_features(candles)

    assert features[10]["volume"] is None
    assert features[10]["volume_change_1"] is None
    assert features[11]["volume_change_1"] is None


def test_positive_cluster_return_cannot_set_production_approved() -> None:
    candles = _candles(40)
    features = build_point_in_time_features(candles)
    labeled = attach_forward_return_labels(features, candles, horizon=1)
    model = fit_cluster_model(labeled, train_end=25, n_clusters=2)
    assigned = assign_clusters(labeled, model)
    summaries = summarize_clusters(assigned)

    positive = [
        cluster
        for cluster in summaries
        if cluster["mean_forward_return"] is not None
        and cluster["mean_forward_return"] > 0
    ]
    assert positive
    assert all(cluster["production_approved"] is False for cluster in positive)
    assert all(cluster["operator_approved"] is False for cluster in positive)
    assert all(cluster["is_strategy"] is False for cluster in positive)


def test_cluster_fit_ignores_rows_after_training_slice() -> None:
    candles = _candles(40)
    rows = attach_forward_return_labels(
        build_point_in_time_features(candles), candles, horizon=1
    )
    original = fit_cluster_model(rows, train_end=25, n_clusters=2)

    changed_later = [dict(row) for row in rows]
    for row in changed_later[25:]:
        for column in original.feature_columns:
            if row.get(column) is not None:
                row[column] = float(row[column]) * 1_000_000.0 + 123.0
    refit = fit_cluster_model(changed_later, train_end=25, n_clusters=2)

    assert refit.means == original.means
    assert refit.scales == original.scales
    assert refit.centroids == original.centroids
