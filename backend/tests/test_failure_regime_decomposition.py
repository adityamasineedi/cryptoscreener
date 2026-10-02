"""Tests for research-only failure-regime decomposition."""

from __future__ import annotations

from app.research.failure_regime_decomposition import (
    FailureTradeRow,
    build_report,
    failure_diagnostic_flags,
    research_diagnostic_tags,
    win_loss_feature_stats,
    bucket_analyses,
)


def _row(**kwargs):
    base = {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "direction": "LONG",
        "entry_time": "2026-09-19T08:15:00+00:00",
        "exit_time": "2026-09-19T15:00:00+00:00",
        "result": "TP1",
        "R": 2.0,
        "setup_trend": "BULLISH",
        "trend_strength": 0.5,
        "trend_duration_bars": 10,
        "HH_HL_sequence_length": 3,
        "LH_LL_sequence_length": 0,
        "bos_direction": "BULLISH_BOS",
        "BOS_ATR_distance": 0.8,
        "bars_since_BOS": 2,
        "ATR_percent": 0.002,
        "recent_range_ATR": 4.0,
        "direction_changes": 2,
        "range_width_ATR": 4.0,
        "swing_count": 8,
        "structure_change_frequency": 0.25,
        "h1_trend": "BULLISH",
        "h4_trend": "BULLISH",
        "HTF_alignment": "ALIGNED",
        "regime_category": "ALIGNED_TREND",
        "range_like_candidate": False,
        "diagnostic_tags": [],
        "failure_flags": [],
        "series_bars": 1200,
        "history_bucket": "1000-4999",
        "data_quality": "OK",
        "entry_index": 100,
        "MAE_R": 0.5,
        "MFE_R": 2.0,
    }
    base.update(kwargs)
    tags = research_diagnostic_tags(base)
    base["diagnostic_tags"] = tags
    base["failure_flags"] = failure_diagnostic_flags({**base, "diagnostic_tags": tags})
    return FailureTradeRow(**base)


def test_aligned_and_clear_tags():
    r = _row(regime_category="ALIGNED_TREND", trend_strength=0.5, direction_changes=1)
    assert "ALIGNED" in r.diagnostic_tags
    assert "CLEAR_TREND" in r.diagnostic_tags
    assert "HTF_CONFLICT" not in r.diagnostic_tags


def test_conflict_tag():
    r = _row(
        regime_category="HTF_CONFLICT",
        HTF_alignment="CONFLICT",
        h1_trend="BEARISH",
        result="SL",
        R=-1.0,
    )
    assert "HTF_CONFLICT" in r.diagnostic_tags
    assert "HTF_CONFLICT" in r.failure_flags


def test_possible_chop_tag():
    r = _row(
        regime_category="LOCAL_ONLY",
        HTF_alignment="LOCAL_OR_NEUTRAL_HTF",
        trend_strength=0.3,
        direction_changes=5,
        recent_range_ATR=3.0,
        result="SL",
        R=-1.0,
    )
    assert "POSSIBLE_CHOP" in r.diagnostic_tags
    assert "POSSIBLE_CHOP" in r.failure_flags
    assert "LOCAL_ONLY" in r.failure_flags


def test_weak_bos_and_directional_short_flags():
    r = _row(
        direction="SHORT",
        regime_category="ALIGNED_TREND",
        BOS_ATR_distance=0.2,
        result="SL",
        R=-1.0,
        setup_trend="BEARISH",
        LH_LL_sequence_length=3,
        HH_HL_sequence_length=0,
    )
    assert "WEAK_BOS" in r.failure_flags
    assert "DIRECTIONAL_SHORT" in r.failure_flags


def test_winners_have_empty_failure_flags():
    r = _row(result="TP1", R=2.0)
    assert r.failure_flags == []


def test_long_short_never_pooled_in_feature_stats():
    rows = [
        _row(direction="LONG", R=2.0, result="TP1", trend_strength=0.6),
        _row(
            direction="SHORT",
            R=-1.0,
            result="SL",
            trend_strength=0.2,
            entry_time="2026-09-20T08:15:00+00:00",
            regime_category="HTF_CONFLICT",
            HTF_alignment="CONFLICT",
        ),
    ]
    report = build_report(rows)
    assert report["by_direction"]["LONG"]["overall"]["n"] == 1
    assert report["by_direction"]["SHORT"]["overall"]["n"] == 1
    long_stats = report["winner_vs_loser_feature_stats"]["LONG"]["trend_strength"]
    short_stats = report["winner_vs_loser_feature_stats"]["SHORT"]["trend_strength"]
    assert long_stats["winner_n"] == 1
    assert short_stats["loser_n"] == 1


def test_predeclared_buckets_present():
    rows = [
        _row(direction_changes=1, recent_range_ATR=2.0, trend_strength=0.3, BOS_ATR_distance=0.2),
        _row(
            direction_changes=4,
            recent_range_ATR=4.0,
            trend_strength=0.5,
            BOS_ATR_distance=0.7,
            entry_time="2026-09-20T08:15:00+00:00",
            R=-1.0,
            result="SL",
        ),
        _row(
            direction_changes=7,
            recent_range_ATR=6.0,
            trend_strength=0.8,
            BOS_ATR_distance=1.5,
            entry_time="2026-09-21T08:15:00+00:00",
            R=-1.0,
            result="SL",
        ),
    ]
    b = bucket_analyses([r.to_dict() for r in rows])
    assert set(b["bucket_definitions"]["direction_changes"]) == {"0-2", "3-5", "6+"}
    assert "LONG" in b and "SHORT" in b
    assert b["ALL"]["direction_changes"]["0-2"]["n"] >= 1


def test_acceptance_and_no_best_claims_in_meta():
    report = build_report([_row()])
    acc = report["acceptance"]
    assert acc["live_engine_unchanged"] is True
    assert acc["no_parameter_optimization"] is True
    assert acc["no_production_sideways_detector"] is True
    assert acc["long_short_separated"] is True
    assert report["by_timeframe"]["5m"]["status"] == "NO DATA"
    assert "best" not in json_safe_lower(report["factual_observations"])


def json_safe_lower(obs) -> str:
    return " ".join(str(x).lower() for x in obs)


def test_win_loss_feature_stats_shape():
    rows = [
        _row(R=2.0, result="TP1", trend_strength=0.6),
        _row(
            R=-1.0,
            result="SL",
            trend_strength=0.3,
            entry_time="2026-09-20T08:15:00+00:00",
        ),
    ]
    stats = win_loss_feature_stats([r.to_dict() for r in rows])
    assert "trend_strength" in stats
    assert stats["trend_strength"]["winner_mean"] == 0.6
    assert stats["trend_strength"]["loser_mean"] == 0.3
