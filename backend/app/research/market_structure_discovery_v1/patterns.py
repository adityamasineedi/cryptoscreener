"""Point-in-time hidden-pattern exploration for research only.

Features for bar ``t`` use candles no later than ``t``. Forward returns are
attached separately as labels and are never included in clustering inputs.
Clusters are descriptive groups, not strategies or execution approvals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np


FEATURE_COLUMNS: tuple[str, ...] = (
    "return_1",
    "return_3",
    "range_pct",
    "body_pct",
    "close_location",
    "realized_volatility_5",
    "volume",
    "volume_change_1",
)
LABEL_COLUMN = "forward_return"
DISCLAIMER = (
    "Research-only descriptive clustering. A cluster is not a strategy, "
    "approval, profitability claim, or trade recommendation."
)


def _value(candle: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        if name not in candle or candle[name] is None:
            continue
        try:
            value = float(candle[name])
        except (TypeError, ValueError):
            return None
        return value if np.isfinite(value) else None
    return None


def _ratio_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return current / previous - 1.0


def build_point_in_time_features(
    candles: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build causal features without filling missing source values.

    Every calculation for row ``t`` reads only positions ``<= t``. A feature
    stays ``None`` when one of its required observations is unavailable.
    """
    closes = [_value(candle, "close", "c") for candle in candles]
    volumes = [_value(candle, "volume", "v") for candle in candles]
    one_bar_returns = [
        _ratio_change(closes[index], closes[index - 1]) if index >= 1 else None
        for index in range(len(candles))
    ]
    rows: list[dict[str, Any]] = []
    for index, candle in enumerate(candles):
        open_price = _value(candle, "open", "o")
        high = _value(candle, "high", "h")
        low = _value(candle, "low", "l")
        close = closes[index]
        candle_range = (
            high - low if high is not None and low is not None and high >= low else None
        )
        range_pct = (
            candle_range / close
            if candle_range is not None and close is not None and close != 0
            else None
        )
        body_pct = (
            (close - open_price) / open_price
            if close is not None and open_price is not None and open_price != 0
            else None
        )
        close_location = (
            (close - low) / candle_range
            if close is not None
            and low is not None
            and candle_range is not None
            and candle_range > 0
            else None
        )
        return_3 = (
            _ratio_change(close, closes[index - 3]) if index >= 3 else None
        )
        trailing_returns = (
            one_bar_returns[index - 4 : index + 1] if index >= 5 else []
        )
        realized_volatility = (
            float(np.std(np.asarray(trailing_returns, dtype=np.float64), ddof=0))
            if len(trailing_returns) == 5
            and all(value is not None for value in trailing_returns)
            else None
        )
        rows.append(
            {
                "bar_index": index,
                "time": candle.get("time", candle.get("timestamp")),
                "return_1": one_bar_returns[index],
                "return_3": return_3,
                "range_pct": range_pct,
                "body_pct": body_pct,
                "close_location": close_location,
                "realized_volatility_5": realized_volatility,
                "volume": volumes[index],
                "volume_change_1": (
                    _ratio_change(volumes[index], volumes[index - 1])
                    if index >= 1
                    else None
                ),
            }
        )
    return rows


def attach_forward_return_labels(
    feature_rows: Sequence[Mapping[str, Any]],
    candles: Sequence[Mapping[str, Any]],
    *,
    horizon: int = 1,
) -> list[dict[str, Any]]:
    """Attach a future return as an outcome label, never as a feature."""
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if len(feature_rows) != len(candles):
        raise ValueError("feature_rows and candles must have equal length")
    closes = [_value(candle, "close", "c") for candle in candles]
    labeled: list[dict[str, Any]] = []
    for position, row in enumerate(feature_rows):
        future_position = position + horizon
        label = (
            _ratio_change(closes[future_position], closes[position])
            if future_position < len(closes)
            else None
        )
        labeled.append({**dict(row), LABEL_COLUMN: label})
    return labeled


@dataclass(frozen=True)
class ClusterModel:
    """Immutable parameters learned from complete rows in the training slice."""

    feature_columns: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    centroids: tuple[tuple[float, ...], ...]
    train_end: int
    training_rows: int

    def assign(self, row: Mapping[str, Any]) -> int | None:
        vector = _complete_vector(row, self.feature_columns)
        if vector is None:
            return None
        normalized = (vector - np.asarray(self.means)) / np.asarray(self.scales)
        centers = np.asarray(self.centroids, dtype=np.float64)
        distances = np.sum((centers - normalized) ** 2, axis=1)
        return int(np.argmin(distances))


def _complete_vector(
    row: Mapping[str, Any], feature_columns: Sequence[str]
) -> np.ndarray | None:
    values: list[float] = []
    for column in feature_columns:
        value = row.get(column)
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(numeric):
            return None
        values.append(numeric)
    return np.asarray(values, dtype=np.float64)


def fit_cluster_model(
    rows: Sequence[Mapping[str, Any]],
    *,
    train_end: int,
    n_clusters: int = 3,
    feature_columns: Sequence[str] = FEATURE_COLUMNS,
    max_iterations: int = 100,
) -> ClusterModel:
    """Fit normalization and centroids on rows ``[0, train_end)`` only."""
    columns = tuple(feature_columns)
    if not columns:
        raise ValueError("at least one feature column is required")
    if LABEL_COLUMN in columns:
        raise ValueError("forward_return is a label and cannot be a cluster input")
    unknown = [column for column in columns if column not in FEATURE_COLUMNS]
    if unknown:
        raise ValueError(f"unsupported feature columns: {', '.join(unknown)}")
    if not 0 < train_end <= len(rows):
        raise ValueError("train_end must define a non-empty half-open training slice")
    if not 1 <= n_clusters <= 12:
        raise ValueError("n_clusters must be between 1 and 12")

    training_vectors = [
        vector
        for row in rows[:train_end]
        if (vector := _complete_vector(row, columns)) is not None
    ]
    if len(training_vectors) < n_clusters:
        raise ValueError("not enough complete training rows for requested clusters")
    matrix = np.vstack(training_vectors)
    means = np.mean(matrix, axis=0)
    raw_scales = np.std(matrix, axis=0, ddof=0)
    scales = np.where(raw_scales > 0, raw_scales, 1.0)
    normalized = (matrix - means) / scales

    # Deterministic, bounded initialization sampled across lexicographic order.
    order = np.lexsort(tuple(normalized[:, index] for index in reversed(range(normalized.shape[1]))))
    positions = np.linspace(0, len(order) - 1, num=n_clusters, dtype=int)
    centroids = normalized[order[positions]].copy()
    for _ in range(max_iterations):
        distances = np.sum(
            (normalized[:, np.newaxis, :] - centroids[np.newaxis, :, :]) ** 2,
            axis=2,
        )
        assignments = np.argmin(distances, axis=1)
        updated = centroids.copy()
        for cluster_id in range(n_clusters):
            members = normalized[assignments == cluster_id]
            if len(members):
                updated[cluster_id] = np.mean(members, axis=0)
        if np.allclose(updated, centroids, rtol=0.0, atol=1e-12):
            centroids = updated
            break
        centroids = updated

    return ClusterModel(
        feature_columns=columns,
        means=tuple(float(value) for value in means),
        scales=tuple(float(value) for value in scales),
        centroids=tuple(tuple(float(value) for value in center) for center in centroids),
        train_end=train_end,
        training_rows=len(training_vectors),
    )


def assign_clusters(
    rows: Sequence[Mapping[str, Any]], model: ClusterModel
) -> list[dict[str, Any]]:
    """Assign rows with the frozen training model; missing rows stay unassigned."""
    return [{**dict(row), "cluster_id": model.assign(row)} for row in rows]


def summarize_clusters(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Summarize labels by cluster without granting strategy or approval status."""
    cluster_ids = sorted(
        {int(row["cluster_id"]) for row in rows if row.get("cluster_id") is not None}
    )
    summaries: list[dict[str, Any]] = []
    for cluster_id in cluster_ids:
        members = [row for row in rows if row.get("cluster_id") == cluster_id]
        labels = [
            float(row[LABEL_COLUMN])
            for row in members
            if row.get(LABEL_COLUMN) is not None
            and np.isfinite(float(row[LABEL_COLUMN]))
        ]
        summaries.append(
            {
                "cluster_id": cluster_id,
                "sample_size": len(members),
                "labeled_sample_size": len(labels),
                "mean_forward_return": float(np.mean(labels)) if labels else None,
                "operator_approved": False,
                "production_approved": False,
                "is_strategy": False,
            }
        )
    return summaries


def discover_patterns(
    candles: Sequence[Mapping[str, Any]],
    *,
    train_end: int,
    n_clusters: int = 3,
    forward_horizon: int = 1,
    feature_columns: Sequence[str] = FEATURE_COLUMNS,
) -> dict[str, Any]:
    """Build features, attach labels, fit on training, then assign all bars."""
    features = build_point_in_time_features(candles)
    labeled = attach_forward_return_labels(
        features, candles, horizon=forward_horizon
    )
    model = fit_cluster_model(
        labeled,
        train_end=train_end,
        n_clusters=n_clusters,
        feature_columns=feature_columns,
    )
    assigned = assign_clusters(labeled, model)
    return {
        "status": "OK",
        "research_only": True,
        "operator_approved": False,
        "production_approved": False,
        "is_strategy": False,
        "train_slice": [0, train_end],
        "feature_columns": list(model.feature_columns),
        "label_column": LABEL_COLUMN,
        "model": {
            "means": list(model.means),
            "scales": list(model.scales),
            "centroids": [list(center) for center in model.centroids],
            "training_rows": model.training_rows,
        },
        "rows": assigned,
        "clusters": summarize_clusters(assigned),
        "disclaimer": DISCLAIMER,
    }


# Explicit name for callers that want to distinguish this from signal discovery.
discover_hidden_patterns = discover_patterns

