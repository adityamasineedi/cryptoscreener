"""Tests for research-only SHORT policy sensitivity."""

from __future__ import annotations

from app.research.htf_alignment_sensitivity import annotate_rows
from app.research.short_policy_sensitivity import (
    SCENARIO_A,
    SCENARIO_B,
    SCENARIO_C,
    SCENARIO_D,
    SCENARIO_E,
    SCENARIO_F,
    build_report,
    scenario_includes,
)


def _row(**kwargs):
    base = {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "direction": "SHORT",
        "entry_time": "2026-09-19T08:15:00+00:00",
        "exit_time": "2026-09-19T15:00:00+00:00",
        "entry_price": 100.0,
        "exit_price": 101.0,
        "result": "SL",
        "R": -1.0,
        "MAE": 2.0,
        "MFE": 0.5,
        "MAE_R": 1.0,
        "MFE_R": 0.25,
        "setup_trend": "BEARISH",
        "h1_trend": "BULLISH",
        "h4_trend": "BULLISH",
        "regime_category": "HTF_CONFLICT",
        "HTF_alignment": "CONFLICT",
    }
    base.update(kwargs)
    return base


def test_short_policy_scenario_rules():
    long_row = annotate_rows([_row(direction="LONG", regime_category="LOCAL_ONLY", R=2.0, result="TP1")])[0]
    short_conflict = annotate_rows([_row()])[0]
    short_aligned = annotate_rows(
        [_row(regime_category="ALIGNED_TREND", timeframe="15m", R=-1.0)]
    )[0]
    short_1h = annotate_rows([_row(timeframe="1h", regime_category="ALIGNED_TREND")])[0]

    assert scenario_includes(SCENARIO_A, short_conflict)
    assert scenario_includes(SCENARIO_B, long_row)
    assert not scenario_includes(SCENARIO_B, short_conflict)

    assert scenario_includes(SCENARIO_C, long_row)
    assert not scenario_includes(SCENARIO_C, short_conflict)
    assert scenario_includes(SCENARIO_C, short_aligned)

    assert scenario_includes(SCENARIO_D, short_aligned)
    assert not scenario_includes(SCENARIO_D, short_1h)

    assert scenario_includes(SCENARIO_E, short_1h)
    assert not scenario_includes(SCENARIO_E, short_aligned)


def test_short_policy_scenario_rules_1h_and_aligned():
    short_aligned_15 = annotate_rows(
        [_row(regime_category="ALIGNED_TREND", timeframe="15m")]
    )[0]
    short_1h = annotate_rows([_row(timeframe="1h", regime_category="ALIGNED_TREND")])[0]
    short_conflict_15 = annotate_rows([_row(timeframe="15m")])[0]

    assert scenario_includes(SCENARIO_E, short_1h)
    assert not scenario_includes(SCENARIO_E, short_aligned_15)

    assert scenario_includes(SCENARIO_F, short_aligned_15)
    assert not scenario_includes(SCENARIO_F, short_1h)
    assert not scenario_includes(SCENARIO_F, short_conflict_15)


def test_build_report_gate_draft_long_only_holds():
    rows = []
    # 12 losing shorts
    for i in range(12):
        rows.append(
            _row(
                entry_time=f"2026-09-0{(i % 9) + 1}T08:00:00+00:00",
                R=-1.0,
                result="SL",
                direction="SHORT",
                regime_category="HTF_CONFLICT",
            )
        )
    # 12 winning longs
    for i in range(12):
        rows.append(
            _row(
                entry_time=f"2026-09-1{(i % 9)}T08:00:00+00:00",
                R=2.0,
                result="TP1",
                direction="LONG",
                regime_category="ALIGNED_TREND",
                setup_trend="BULLISH",
            )
        )
    report = build_report(rows)
    assert report["gate_draft"]["holds_for_gate_draft"] is True
    assert report["gate_draft"]["suggested_defaults"]["research_gate_block_shorts"] is True
    assert report["scenarios"][SCENARIO_B]["by_direction"]["SHORT"]["n"] == 0
    assert report["scenarios"][SCENARIO_B]["mean_R"] > report["scenarios"][SCENARIO_A]["mean_R"]
