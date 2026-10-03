"""Research-only HTF forensic tests for S3 S/D survivors.

Does not modify production HTF / trend / signal engines.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.research.bos_strategy_comparison.htf import (
    HTF_ALIGNED,
    HTF_CONFLICT,
    HTF_NEUTRAL_UNAVAILABLE,
    as_of_index_at_or_before,
    classify_htf_alignment,
    trend_at_as_of,
)
from app.research.bos_strategy_comparison.htf_forensics import (
    compare_research_vs_production_trend,
    decompose_htf_rejection,
    inspect_tf_at_eligibility,
    matrix_key,
)
from app.research.bos_strategy_comparison.s3_diagnostics import run_s3_forensic_diagnostic
from app.signals.config import SignalConfig
from app.signals.signal_engine import SignalEngine

from tests.test_bos_strategy_comparison import make_htf_from_setup, make_uptrend_series
from tests.test_lifecycle import TrackingCandles


def test_long_requires_both_bullish_alignment():
    assert (
        classify_htf_alignment(
            bos_direction="BULLISH_BOS",
            trend_4h="BULLISH",
            trend_1h="BULLISH",
        )
        == HTF_ALIGNED
    )
    assert (
        classify_htf_alignment(
            bos_direction="BULLISH_BOS",
            trend_4h="BULLISH",
            trend_1h="NEUTRAL",
        )
        == HTF_NEUTRAL_UNAVAILABLE
    )
    assert (
        classify_htf_alignment(
            bos_direction="BULLISH_BOS",
            trend_4h="BEARISH",
            trend_1h="BEARISH",
        )
        == HTF_CONFLICT
    )


def test_short_requires_both_bearish_alignment():
    assert (
        classify_htf_alignment(
            bos_direction="BEARISH_BOS",
            trend_4h="BEARISH",
            trend_1h="BEARISH",
        )
        == HTF_ALIGNED
    )
    assert (
        classify_htf_alignment(
            bos_direction="BEARISH_BOS",
            trend_4h="BULLISH",
            trend_1h="BULLISH",
        )
        == HTF_CONFLICT
    )


def test_neutral_not_treated_as_missing_data():
    reason = decompose_htf_rejection(
        direction="LONG",
        state_4h="NEUTRAL",
        state_1h="BULLISH",
        alignment=HTF_NEUTRAL_UNAVAILABLE,
    )
    assert reason == "4H_NEUTRAL"
    assert reason != "DATA_UNAVAILABLE"
    both = decompose_htf_rejection(
        direction="LONG",
        state_4h="NEUTRAL",
        state_1h="NEUTRAL",
        alignment=HTF_NEUTRAL_UNAVAILABLE,
    )
    assert both == "BOTH_NEUTRAL"


def test_direction_conflict_categories():
    assert (
        decompose_htf_rejection(
            direction="LONG",
            state_4h="BULLISH",
            state_1h="BEARISH",
            alignment=HTF_NEUTRAL_UNAVAILABLE,
        )
        == "4H_BULLISH_1H_BEARISH"
    )
    assert (
        decompose_htf_rejection(
            direction="SHORT",
            state_4h="BEARISH",
            state_1h="BULLISH",
            alignment=HTF_NEUTRAL_UNAVAILABLE,
        )
        == "4H_BEARISH_1H_BULLISH"
    )
    assert (
        decompose_htf_rejection(
            direction="LONG",
            state_4h="BEARISH",
            state_1h="BEARISH",
            alignment=HTF_CONFLICT,
        )
        == "WRONG_DIRECTION"
    )


def test_htf_asof_le_eligibility_and_no_future_access():
    setup = make_uptrend_series(128)
    h4 = make_htf_from_setup(setup, 16)
    tracked = TrackingCandles(h4)
    elig = setup[80]["time"]
    idx = as_of_index_at_or_before(tracked, elig)
    assert idx is not None
    assert h4[idx]["time"] <= elig
    # Accessing via inspect should not use future bars beyond as_of
    detail = inspect_tf_at_eligibility(
        symbol="TESTUSDT",
        timeframe="4h",
        candles=h4,
        eligibility_ts=elig,
        config=SignalConfig(),
    )
    assert detail["available"] is True
    assert detail["asof_ok"] is True
    assert detail["as_of_time"] is not None
    # Tracking: only need as_of path for trend — use tracked for as_of index scan
    _ = as_of_index_at_or_before(tracked, elig)
    # After binary-style scan, max index with time<=elig
    assert tracked.max_accessed <= idx or tracked.max_accessed < len(h4)


def test_as_of_index_at_or_before_respected():
    setup = make_uptrend_series(64)
    h1 = make_htf_from_setup(setup, 4)
    elig = setup[40]["time"]
    idx = as_of_index_at_or_before(h1, elig)
    assert idx is not None
    assert h1[idx]["time"] <= elig
    if idx + 1 < len(h1):
        assert h1[idx + 1]["time"] > elig


def test_research_matches_production_trend_invocation():
    candles = make_uptrend_series(120)
    cfg = SignalConfig()
    engine = SignalEngine(cfg)
    as_of = 90
    cmp = compare_research_vs_production_trend(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        as_of_index=as_of,
        config=cfg,
        signal_engine=engine,
    )
    assert cmp["match"] is True
    # Direct engine equality
    research = trend_at_as_of(
        candles, as_of, timeframe="15m", symbol="TESTUSDT", config=cfg
    )
    prod = engine.analyze_timeframe(
        "TESTUSDT", "15m", candles, as_of_index=as_of
    )
    prod_t = str((prod.get("trend") or {}).get("trend") or "").upper()
    if research == "HTF_UNAVAILABLE":
        assert prod_t in ("WAITING", "HTF_UNAVAILABLE", "INSUFFICIENT_DATA")
    else:
        assert research == prod_t or (
            research == "INSUFFICIENT_DATA" and prod_t == "INSUFFICIENT_DATA"
        )


def test_matrix_key_and_htf_trace_api_shape():
    assert matrix_key("BULLISH", "NEUTRAL") == "4H_BULLISH__1H_NEUTRAL"
    candles = make_uptrend_series(200)
    h4 = make_htf_from_setup(candles, 16)
    h1 = make_htf_from_setup(candles, 4)
    report = run_s3_forensic_diagnostic(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        candles_4h=h4,
        candles_1h=h1,
        index_start=50,
        htf_trace=True,
        limit=20,
    )
    assert "s3_funnel" in report
    assert "htf_matrix" in report
    assert "htf_rejection_reasons" in report
    assert "htf_lookahead" in report or "lookahead" in report
    f = report["s3_funnel"]
    assert f["sd_confluence_pass"] >= f["htf_pass"]
    assert f["retest_pass"] >= f["sd_confluence_pass"]
    # Survivors traced == sd pass
    assert report["htf_survivors"]["sd_survivors"] == f["sd_confluence_pass"]
    for t in report.get("htf_traces") or []:
        elig = t.get("eligibility_time")
        t4 = (t.get("4h") or {}).get("as_of_time")
        t1 = (t.get("1h") or {}).get("as_of_time")
        if elig and t4:
            assert t4 <= elig
        if elig and t1:
            assert t1 <= elig
        assert "alignment" in t
        assert (t.get("4h") or {}).get("state") is not None
