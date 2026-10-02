"""Tests for research-only HTF alignment sensitivity analysis."""

from __future__ import annotations

import json
from pathlib import Path

from app.research.htf_alignment_sensitivity import (
    HTF_ALIGNED,
    HTF_CONFLICT,
    HTF_NEUTRAL_OR_UNAVAILABLE,
    SCENARIO_A,
    SCENARIO_B,
    SCENARIO_C,
    SCENARIO_D,
    annotate_rows,
    build_report,
    compute_metrics,
    research_htf_state,
    scenario_includes,
)


def _row(**kwargs):
    base = {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "direction": "LONG",
        "entry_time": "2026-09-19T08:15:00+00:00",
        "exit_time": "2026-09-19T15:00:00+00:00",
        "entry_price": 100.0,
        "exit_price": 101.0,
        "result": "TP1",
        "R": 2.0,
        "MAE": 1.0,
        "MFE": 3.0,
        "MAE_R": 0.5,
        "MFE_R": 1.5,
        "setup_trend": "BULLISH",
        "h1_trend": "BULLISH",
        "h4_trend": "BULLISH",
        "regime_category": "ALIGNED_TREND",
        "HTF_alignment": "ALIGNED",
    }
    base.update(kwargs)
    return base


def test_research_htf_state_mapping():
    assert research_htf_state(_row(regime_category="ALIGNED_TREND")) == HTF_ALIGNED
    assert research_htf_state(_row(regime_category="HTF_CONFLICT")) == HTF_CONFLICT
    assert (
        research_htf_state(_row(regime_category="LOCAL_ONLY"))
        == HTF_NEUTRAL_OR_UNAVAILABLE
    )
    assert (
        research_htf_state(_row(regime_category="NEUTRAL_STRUCTURE"))
        == HTF_NEUTRAL_OR_UNAVAILABLE
    )


def test_scenario_inclusion_rules():
    aligned = annotate_rows([_row(regime_category="ALIGNED_TREND")])[0]
    conflict = annotate_rows([_row(regime_category="HTF_CONFLICT")])[0]
    local = annotate_rows([_row(regime_category="LOCAL_ONLY")])[0]

    assert scenario_includes(SCENARIO_A, aligned)
    assert scenario_includes(SCENARIO_A, conflict)
    assert scenario_includes(SCENARIO_A, local)

    assert scenario_includes(SCENARIO_B, aligned)
    assert not scenario_includes(SCENARIO_B, conflict)
    assert not scenario_includes(SCENARIO_B, local)

    assert scenario_includes(SCENARIO_C, aligned)
    assert not scenario_includes(SCENARIO_C, conflict)
    assert scenario_includes(SCENARIO_C, local)

    assert scenario_includes(SCENARIO_D, aligned)
    assert not scenario_includes(SCENARIO_D, conflict)
    assert scenario_includes(SCENARIO_D, local)


def test_does_not_mutate_entry_fields():
    original = _row(
        entry_price=81269.2,
        R=3.0,
        result="TP2",
        regime_category="HTF_CONFLICT",
    )
    report = build_report([original])
    # Original dict unchanged
    assert original["entry_price"] == 81269.2
    assert original["R"] == 3.0
    assert original["result"] == "TP2"
    # Scenario B excludes conflict → retained 0
    assert report["scenarios"][SCENARIO_B]["retained_trades"] == 0
    assert report["scenarios"][SCENARIO_A]["retained_trades"] == 1


def test_loss_share_vs_loss_rate_separated():
    rows = [
        _row(regime_category="ALIGNED_TREND", R=2.0, result="TP1", direction="LONG"),
        _row(
            regime_category="ALIGNED_TREND",
            R=-1.0,
            result="SL",
            direction="LONG",
            entry_time="2026-09-20T08:15:00+00:00",
        ),
        _row(
            regime_category="HTF_CONFLICT",
            R=-1.0,
            result="SL",
            direction="SHORT",
            entry_time="2026-09-21T08:15:00+00:00",
        ),
        _row(
            regime_category="HTF_CONFLICT",
            R=-1.0,
            result="SL",
            direction="SHORT",
            entry_time="2026-09-22T08:15:00+00:00",
        ),
        _row(
            regime_category="LOCAL_ONLY",
            R=-1.0,
            result="SL",
            direction="SHORT",
            entry_time="2026-09-23T08:15:00+00:00",
        ),
    ]
    report = build_report(rows)
    lsr = report["loss_share_vs_loss_rate"]
    assert lsr["total_losers"] == 4
    aligned = lsr["by_htf_state"][HTF_ALIGNED]
    conflict = lsr["by_htf_state"][HTF_CONFLICT]
    # ALIGNED: 1 loss / 2 trades → loss_rate 0.5; share 1/4 = 0.25
    assert aligned["loss_rate"] == 0.5
    assert aligned["loss_share_of_all_losers"] == 0.25
    # CONFLICT: 2 losses / 2 trades → rate 1.0; share 2/4 = 0.5
    assert conflict["loss_rate"] == 1.0
    assert conflict["loss_share_of_all_losers"] == 0.5


def test_sample_size_flags():
    # n=2 → INSUFFICIENT
    m = compute_metrics([_row(), _row(entry_time="2026-09-20T08:15:00+00:00", R=-1.0, result="SL")])
    assert m["sample_size_flag"] == "INSUFFICIENT_SAMPLE"
    rows = [
        _row(
            entry_time=f"2026-09-{(i % 28) + 1:02d}T08:15:00+00:00",
            R=1.0 if i % 3 else -1.0,
            result="TP1" if i % 3 else "SL",
        )
        for i in range(30)
    ]
    m30 = compute_metrics(rows)
    assert m30["n"] == 30
    assert m30["sample_size_flag"] == "SAMPLE_SIZE_OK"


def test_5m_reported_as_no_data():
    report = build_report([_row(timeframe="15m"), _row(timeframe="1h", entry_time="2026-09-20T00:00:00+00:00")])
    assert report["dataset"]["5m_status"] == "NO DATA"
    for sid in (SCENARIO_A, SCENARIO_B, SCENARIO_C, SCENARIO_D):
        assert report["scenarios"][sid]["by_timeframe"]["5m"]["status"] == "NO DATA"


def test_long_short_separated_in_scenarios():
    rows = [
        _row(direction="LONG", R=2.0, result="TP1", regime_category="ALIGNED_TREND"),
        _row(
            direction="SHORT",
            R=-1.0,
            result="SL",
            regime_category="ALIGNED_TREND",
            entry_time="2026-09-20T08:15:00+00:00",
        ),
    ]
    report = build_report(rows)
    a = report["scenarios"][SCENARIO_A]
    assert a["LONG"]["n"] == 1
    assert a["SHORT"]["n"] == 1
    assert a["LONG"]["wins"] == 1
    assert a["SHORT"]["losses"] == 1


def test_acceptance_flags_present():
    report = build_report([_row()])
    acc = report["acceptance"]
    assert acc["live_engine_unchanged"] is True
    assert acc["existing_trades_unchanged"] is True
    assert acc["no_trade_entry_recalculation"] is True
    assert acc["long_short_separated"] is True
    assert "MULTIPLE_TESTING_RISK" in report["multiple_testing_warning"]


def test_real_trade_rows_file_loads_if_present():
    path = Path(__file__).resolve().parents[1] / "scripts" / "trend_regime_trade_rows.json"
    if not path.is_file():
        return
    rows = json.loads(path.read_text(encoding="utf-8"))
    report = build_report(rows)
    assert report["dataset"]["n_trades"] == len(rows)
    assert report["scenarios"][SCENARIO_A]["retained_trades"] == len(rows)
    # B retains only ALIGNED_TREND
    n_aligned = sum(1 for r in rows if r.get("regime_category") == "ALIGNED_TREND")
    assert report["scenarios"][SCENARIO_B]["retained_trades"] == n_aligned
    # C and D exclude conflict; with zero NEUTRAL_STRUCTURE they match
    n_conflict = sum(1 for r in rows if r.get("regime_category") == "HTF_CONFLICT")
    assert report["scenarios"][SCENARIO_D]["excluded_trades"] == n_conflict
    assert (
        report["scenarios"][SCENARIO_C]["retained_trades"]
        == report["scenarios"][SCENARIO_D]["retained_trades"]
    )
