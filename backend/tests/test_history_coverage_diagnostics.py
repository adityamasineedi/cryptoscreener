"""Tests for history coverage audit + retest stage diagnostics."""

from __future__ import annotations

from app.research.bos_strategy_comparison.diagnostics import (
    categorize_retest_rejection,
    run_stage_funnel_diagnostic,
)
from app.research.bos_strategy_comparison.config import StrategyResearchConfig
from app.research.data_pipeline.history_coverage import _bucket_label, _years_between
from app.signals.config import SignalConfig

from tests.test_bos_strategy_comparison import make_htf_from_setup, make_uptrend_series


def test_year_bucket_labels():
    assert _bucket_label(6.2) == "6y+"
    assert _bucket_label(5.5) == "5y"
    assert _bucket_label(4.1) == "4y"
    assert _bucket_label(3.0) == "3y"
    assert _bucket_label(2.5) == "2y"
    assert _bucket_label(1.2) == "<2y"
    assert _bucket_label(None) is None


def test_years_between_non_negative():
    from datetime import datetime, timezone

    a = datetime(2020, 10, 1, tzinfo=timezone.utc)
    b = datetime(2026, 10, 1, tzinfo=timezone.utc)
    y = _years_between(a, b)
    assert y is not None and 5.9 < y < 6.1
    assert _years_between(b, a) == 0.0


def test_categorize_retest_rejection_reasons():
    bos = {"state": "CONFIRMED", "broken_level": 100.0, "direction": "BULLISH_BOS"}
    assert (
        categorize_retest_rejection(
            retest={"reason": "Waiting for pullback"},
            bos=bos,
            pullback={"pullback_state": "WAITING"},
            candles_len=100,
            as_of_index=50,
        )
        == "waiting_for_pullback"
    )
    assert (
        categorize_retest_rejection(
            retest={
                "reason": "No bullish retest hold near 100",
                "distance": 5.0,
                "tolerance": 1.0,
            },
            bos=bos,
            pullback={"pullback_state": "ACTIVE", "structure_intact": True},
            candles_len=100,
            as_of_index=50,
        )
        == "price_did_not_revisit_bos_level"
    )
    assert (
        categorize_retest_rejection(
            retest={"reason": "x"},
            bos=bos,
            pullback={"pullback_state": "ACTIVE", "structure_intact": False},
            candles_len=100,
            as_of_index=50,
        )
        == "structure_invalidated"
    )
    assert (
        categorize_retest_rejection(
            retest=None,
            bos=bos,
            pullback=None,
            candles_len=5,
            as_of_index=2,
        )
        == "insufficient_candles"
    )


def test_stage_funnel_diagnostic_runs_on_synthetic():
    candles = make_uptrend_series(160)
    h4 = make_htf_from_setup(candles, 16)
    h1 = make_htf_from_setup(candles, 4)
    m5 = make_htf_from_setup(candles, 1)
    report = run_stage_funnel_diagnostic(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        candles_4h=h4,
        candles_1h=h1,
        candles_5m=m5,
        index_start=40,
        signal_config=SignalConfig(),
        research_config=StrategyResearchConfig(min_bars=20),
        strategy_ids=["CONTROL_C", "CONTROL_A"],
    )
    assert report["status"] == "OK"
    assert "bos_count" in report
    assert "retest_count" in report
    assert "retest_rejection_reasons" in report
    assert report["live_engines_unchanged"] is True
    assert report["retest_logic_unchanged"] is True
    assert "CONTROL_C" in report["per_strategy"]
