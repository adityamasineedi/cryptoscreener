"""Tests for research-only pullback forensic diagnostics.

Does not modify production pullback/impulse/BOS engines.
"""

from __future__ import annotations

from copy import deepcopy

from app.research.bos_strategy_comparison.config import StrategyResearchConfig
from app.research.bos_strategy_comparison.pullback_diagnostics import (
    TIMEFRAME_MAPPING,
    evaluate_pullback_no_future,
    pullback_engine_unchanged_check,
    run_pullback_forensic_diagnostic,
)
from app.signals.config import SignalConfig
from app.signals.pullback_engine import detect_pullback
from app.signals.signal_engine import SignalEngine

from tests.test_bos_strategy_comparison import make_htf_from_setup, make_uptrend_series


def test_pullback_diagnostic_does_not_change_production_output():
    candles = make_uptrend_series(120)
    bos = {
        "state": "CONFIRMED",
        "direction": "BULLISH_BOS",
        "broken_level": 110.0,
    }
    impulse = {
        "is_impulse": True,
        "direction": "BULLISH",
        "bar_index": 80,
        "impulse_origin": 108.0,
        "impulse_end": 115.0,
    }
    cfg = SignalConfig()
    before = detect_pullback(candles, bos, impulse, cfg, as_of_index=90)
    chk = pullback_engine_unchanged_check(
        candles, bos, impulse, cfg, as_of_index=90
    )
    after = detect_pullback(candles, bos, impulse, cfg, as_of_index=90)
    assert chk["identical"] is True
    assert before == after == chk["output_a"]


def test_same_ohlcv_same_pullback_result():
    candles = make_uptrend_series(100)
    cfg = SignalConfig()
    bos = {
        "state": "CONFIRMED",
        "direction": "BULLISH_BOS",
        "broken_level": 105.0,
    }
    impulse = {
        "is_impulse": True,
        "direction": "BULLISH",
        "bar_index": 70,
        "impulse_origin": 102.0,
        "impulse_end": 110.0,
    }
    a = detect_pullback(candles, bos, impulse, cfg, as_of_index=85)
    b = detect_pullback(deepcopy(candles), deepcopy(bos), deepcopy(impulse), cfg, as_of_index=85)
    assert a == b


def test_as_of_index_respected_and_no_future():
    candles = make_uptrend_series(100)
    cfg = SignalConfig()
    bos = {
        "state": "CONFIRMED",
        "direction": "BULLISH_BOS",
        "broken_level": 105.0,
    }
    impulse = {
        "is_impulse": True,
        "direction": "BULLISH",
        "bar_index": 60,
        "impulse_origin": 100.0,
        "impulse_end": 108.0,
    }
    nf = evaluate_pullback_no_future(
        candles, bos, impulse, cfg, as_of_index=75
    )
    assert nf["full_equals_truncated"] is True
    assert nf["truncated_len"] == 76


def test_waiting_for_bars_after_impulse_on_same_bar():
    candles = make_uptrend_series(80)
    cfg = SignalConfig()
    bos = {
        "state": "CONFIRMED",
        "direction": "BULLISH_BOS",
        "broken_level": 105.0,
    }
    impulse = {
        "is_impulse": True,
        "direction": "BULLISH",
        "bar_index": 70,
        "impulse_origin": 100.0,
        "impulse_end": 108.0,
    }
    pb = detect_pullback(candles, bos, impulse, cfg, as_of_index=70)
    assert pb["pullback_state"] == "WAITING"
    assert "Waiting for bars after impulse" in pb["reason"]
    # WAITING must not be treated as FAIL by diagnostic keying
    assert pb["pullback_state"] != "FAILED"


def test_post_bos_candle_count_correct():
    candles = make_uptrend_series(100)
    bos_index = 60
    post = len(candles) - 1 - bos_index
    assert post == 39
    report = run_pullback_forensic_diagnostic(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        candles_4h=make_htf_from_setup(candles, 16),
        candles_1h=make_htf_from_setup(candles, 4),
        candles_5m=make_htf_from_setup(candles, 1),
        index_start=40,
        signal_config=SignalConfig(),
        research_config=StrategyResearchConfig(min_bars=20),
        max_traces=3,
    )
    assert report["status"] == "OK"
    assert "post_bos_candle_distribution" in report
    for ev in report.get("parent_events_sample") or []:
        idx = ev["bos_index"]
        assert ev["bars_after_bos"] == len(candles) - 1 - idx


def test_waiting_not_converted_to_fail_in_report():
    candles = make_uptrend_series(160)
    report = run_pullback_forensic_diagnostic(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        candles_4h=make_htf_from_setup(candles, 16),
        candles_1h=make_htf_from_setup(candles, 4),
        index_start=40,
        signal_config=SignalConfig(),
        research_config=StrategyResearchConfig(min_bars=20),
    )
    assert report["lifecycle"]["waiting_not_converted_to_fail"] is True
    assert report["data_quality"]["invariant_checks"]["waiting_not_converted_to_fail"] is True
    # Status keys must use engine states, not invented FAIL for WAITING
    for status in (report.get("statuses") or {}):
        assert status in (
            "WAITING",
            "ACTIVE",
            "CONFIRMED",
            "FAILED",
            "INVALIDATED",
            "NONE",
        )


def test_research_production_invocation_differences_detected():
    candles = make_uptrend_series(160)
    report = run_pullback_forensic_diagnostic(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        candles_4h=make_htf_from_setup(candles, 16),
        candles_1h=make_htf_from_setup(candles, 4),
        index_start=40,
        signal_config=SignalConfig(),
        research_config=StrategyResearchConfig(min_bars=20),
    )
    assert report["lifecycle"]["research_matches_production"] is True
    assert report["lifecycle"]["research_matches_pullback_engine_required_lifecycle"] is False
    assert len(report["lifecycle"]["mismatch_reasons"]) >= 1
    assert "production_vs_research" in report
    assert report["counter_model"]["model"] == "INDEPENDENT_SIBLINGS_ON_BOS_BARS"


def test_timeframe_mapping_correct():
    assert TIMEFRAME_MAPPING["primary_structure_setup_timeframe"] == "15m"
    assert TIMEFRAME_MAPPING["execution_entry_timeframe"] == "5m"
    assert "4h" in TIMEFRAME_MAPPING["htf_timeframes"]
    assert "1h" in TIMEFRAME_MAPPING["htf_timeframes"]
    cfg = SignalConfig()
    assert cfg.mtf_setup == "15m"
    assert cfg.mtf_entry == "5m"
    assert cfg.mtf_primary == "1h"
    assert cfg.mtf_major == "4h"


def test_no_fabricated_candles_flag():
    candles = make_uptrend_series(120)
    report = run_pullback_forensic_diagnostic(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        index_start=40,
        signal_config=SignalConfig(),
        research_config=StrategyResearchConfig(min_bars=20),
    )
    assert report["data_quality"]["fabricated_candles"] is False
    assert report["live_engines_unchanged"] is True
    assert report["pullback_logic_unchanged"] is True


def test_analyze_timeframe_same_bar_cannot_confirm_pullback_when_impulse_passes():
    """Structural proof: SignalEngine pairs impulse.bar_index == as_of_index."""
    candles = make_uptrend_series(160)
    eng = SignalEngine(SignalConfig())
    for i in range(50, len(candles)):
        tf = eng.analyze_timeframe("TESTUSDT", "15m", candles, as_of_index=i)
        bos = tf.get("bos")
        impulse = tf.get("impulse")
        pullback = tf.get("pullback")
        if not (bos and bos.get("state") == "CONFIRMED"):
            continue
        if not (impulse and impulse.get("is_impulse")):
            continue
        assert impulse.get("bar_index") == i
        assert pullback.get("pullback_state") == "WAITING"
        assert "Waiting for bars after impulse" in str(pullback.get("reason") or "")


def test_counters_are_independent_siblings_not_strict_funnel():
    candles = make_uptrend_series(200)
    report = run_pullback_forensic_diagnostic(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        candles_4h=make_htf_from_setup(candles, 16),
        candles_1h=make_htf_from_setup(candles, 4),
        index_start=40,
        signal_config=SignalConfig(),
        research_config=StrategyResearchConfig(min_bars=20),
    )
    cm = report["counter_model"]
    # Sibling counts on BOS bars — impulse need not equal HTF
    assert cm["impulse_without_htf"] + cm["impulse_and_htf"] == cm["impulse_candidates"]
    assert cm["htf_without_impulse"] + cm["impulse_and_htf"] == cm["htf_aligned_candidates"]
