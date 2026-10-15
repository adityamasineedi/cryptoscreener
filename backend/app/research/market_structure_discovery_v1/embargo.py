"""Purge and embargo for research splits.

Training bars whose label horizon reaches the test window are removed from
training. A configurable embargo after the train end is removed from the test
window. This does not change walk-forward execution and does not claim
profitability.
"""

from __future__ import annotations

from typing import Any, Sequence

DISCLAIMER = (
    "Research-only label purge and embargo. Removing overlapping training "
    "labels does not establish profitability, a strategy, or an approval to trade."
)


def label_reaches_test(
    bar_index: int,
    horizon: int | None,
    *,
    test_start: int,
    test_end: int,
) -> bool | None:
    """Whether the label ending at ``bar_index + horizon`` enters the test window.

    The label for bar ``i`` uses the observation at index ``i + horizon``.
    A missing horizon stays ``None``.
    """
    _validate_bar_index(bar_index)
    _validate_half_open("test", test_start, test_end)
    if horizon is None:
        return None
    _validate_horizon(horizon)
    label_end = bar_index + horizon
    return label_end >= test_start and bar_index < test_end


def apply_embargo(
    *,
    train_start: int,
    train_end: int,
    test_start: int,
    test_end: int,
    horizon: int | None = None,
    horizons: Sequence[int | None] | None = None,
    embargo_bars: int = 0,
) -> dict[str, Any]:
    """Drop overlapping training labels and the embargo after ``train_end``.

    Ranges are half-open. ``horizons``, when given, aligns with the training
    slice ``[train_start, train_end)``. A missing horizon is left null and that
    bar is not placed in training. Test bars at or after ``train_end + embargo_bars``
    stay in the test window.
    """
    _validate_half_open("train", train_start, train_end)
    _validate_half_open("test", test_start, test_end)
    if isinstance(embargo_bars, bool) or not isinstance(embargo_bars, int) or embargo_bars < 0:
        raise ValueError("embargo_bars must be a non-negative integer")

    train_count = train_end - train_start
    resolved = _resolve_horizons(train_count, horizon, horizons)
    embargo_start = train_end
    embargo_end = train_end + embargo_bars

    rows: list[dict[str, Any]] = []
    train_indices: list[int] = []
    excluded_train_indices: list[int] = []
    unresolved_train_indices: list[int] = []
    for offset, bar_horizon in enumerate(resolved):
        bar_index = train_start + offset
        reaches = label_reaches_test(
            bar_index,
            bar_horizon,
            test_start=test_start,
            test_end=test_end,
        )
        if reaches is None:
            unresolved_train_indices.append(bar_index)
        elif reaches:
            excluded_train_indices.append(bar_index)
        else:
            train_indices.append(bar_index)
        rows.append(
            {
                "bar_index": bar_index,
                "horizon": bar_horizon,
                "label_reaches_test": reaches,
                "in_training": False if reaches is None else not reaches,
                "in_test": False,
                "in_embargo": False,
            }
        )

    embargo_indices = [
        index
        for index in range(test_start, test_end)
        if embargo_start <= index < embargo_end
    ]
    test_indices = [
        index
        for index in range(test_start, test_end)
        if index >= embargo_end or index < embargo_start
    ]
    embargo_set = set(embargo_indices)
    test_set = set(test_indices)
    known = {row["bar_index"] for row in rows}
    for index in range(test_start, test_end):
        if index in known:
            row = rows[index - train_start]
            row["in_embargo"] = index in embargo_set
            row["in_test"] = index in test_set
            continue
        rows.append(
            {
                "bar_index": index,
                "horizon": None,
                "label_reaches_test": None,
                "in_training": False,
                "in_test": index in test_set,
                "in_embargo": index in embargo_set,
            }
        )

    return {
        "train_indices": train_indices,
        "excluded_train_indices": excluded_train_indices,
        "unresolved_train_indices": unresolved_train_indices,
        "embargo_indices": embargo_indices,
        "test_indices": test_indices,
        "rows": rows,
        "research_only": True,
        "operator_approved": False,
        "production_approved": False,
        "is_strategy": False,
        "disclaimer": DISCLAIMER,
    }


def _resolve_horizons(
    train_count: int,
    horizon: int | None,
    horizons: Sequence[int | None] | None,
) -> list[int | None]:
    if horizons is None:
        if horizon is not None:
            _validate_horizon(horizon)
        return [horizon] * train_count
    if len(horizons) != train_count:
        raise ValueError("horizons must align with the half-open training range")
    resolved: list[int | None] = []
    for value in horizons:
        if value is not None:
            _validate_horizon(value)
        resolved.append(value)
    return resolved


def _validate_half_open(name: str, start: int, end: int) -> None:
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 0
        or end <= start
    ):
        raise ValueError(f"{name} range must be a non-empty half-open interval")


def _validate_bar_index(bar_index: int) -> None:
    if isinstance(bar_index, bool) or not isinstance(bar_index, int) or bar_index < 0:
        raise ValueError("bar_index must be a non-negative integer")


def _validate_horizon(horizon: int) -> None:
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
        raise ValueError("horizon must be a positive integer or null")
