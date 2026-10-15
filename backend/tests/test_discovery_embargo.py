"""Label purge and embargo stay research-only and leave missing horizons null."""

from __future__ import annotations

import pytest

from app.research.market_structure_discovery_v1.embargo import (
    apply_embargo,
    label_reaches_test,
)


def test_label_that_reaches_test_is_excluded_from_training() -> None:
    result = apply_embargo(
        train_start=0,
        train_end=10,
        test_start=10,
        test_end=15,
        horizon=3,
        embargo_bars=0,
    )

    assert label_reaches_test(7, 3, test_start=10, test_end=15) is True
    assert 7 in result["excluded_train_indices"]
    assert 6 in result["train_indices"]
    assert 7 not in result["train_indices"]
    assert result["rows"][7]["in_training"] is False
    assert result["rows"][6]["label_reaches_test"] is False
    assert "profitability" in result["disclaimer"]
    assert result["research_only"] is True
    assert result["is_strategy"] is False


def test_bars_after_embargo_stay_in_the_test() -> None:
    result = apply_embargo(
        train_start=0,
        train_end=10,
        test_start=10,
        test_end=16,
        horizon=1,
        embargo_bars=2,
    )

    assert result["embargo_indices"] == [10, 11]
    assert result["test_indices"] == [12, 13, 14, 15]
    assert 10 not in result["test_indices"]
    assert 12 in result["test_indices"]


def test_missing_horizon_stays_null() -> None:
    result = apply_embargo(
        train_start=0,
        train_end=4,
        test_start=4,
        test_end=6,
        horizons=[1, None, 2, None],
        embargo_bars=0,
    )

    assert label_reaches_test(1, None, test_start=4, test_end=6) is None
    assert result["rows"][1]["horizon"] is None
    assert result["rows"][1]["label_reaches_test"] is None
    assert result["rows"][3]["label_reaches_test"] is None
    assert 1 in result["unresolved_train_indices"]
    assert 1 not in result["train_indices"]
    assert 1 not in result["excluded_train_indices"]
    assert result["rows"][2]["label_reaches_test"] is True
    assert 0 in result["train_indices"]


def test_embargo_bars_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="embargo_bars"):
        apply_embargo(
            train_start=0,
            train_end=2,
            test_start=2,
            test_end=4,
            horizon=1,
            embargo_bars=-1,
        )
