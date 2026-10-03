"""Focused tests for Multi-Cap baseline forensics helpers (research only)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.research.multi_cap_strategies.baseline_forensics import (
    aggregate_metrics,
    build_cap_transitions,
    calendar_days,
    classify_cell_history_bucket,
    funnel_gap_explanation,
    history_bucket,
    reconcile_artifact_totals,
    reference_comparison_aggregate,
    summarize_cap_transitions,
)
from app.research.config import split_period_indices


class _LK:
    def __init__(self, group: str):
        self.strategy_cap_group = group


def test_history_bucket_classification():
    assert history_bucket(0) == "A_<14d"
    assert history_bucket(13.9) == "A_<14d"
    assert history_bucket(14) == "B_14_30d"
    assert history_bucket(29.9) == "B_14_30d"
    assert history_bucket(30) == "C_30_90d"
    assert history_bucket(90) == "D_90_180d"
    assert history_bucket(180) == "E_180d_plus"
    assert history_bucket(None) == "UNKNOWN"


def test_classify_cell_history_bucket():
    cell = {
        "strategy_id": "MID_CAP_FVG_DISCOUNT",
        "symbol": "AAVEUSDT",
        "timeframe": "5m",
        "cap_group": "MID_CAP",
        "requested_start": "2026-09-30T00:00:00+00:00",
        "requested_end": "2026-10-03T00:00:00+00:00",
        "actual_start": "2026-09-29T00:00:00+00:00",
        "actual_end": "2026-10-03T00:00:00+00:00",
        "bars": 800,
        "signals_detected": 10,
        "cap_eligible_signals": 4,
        "candidates": 5,
        "entries": 4,
        "closed_trades": 3,
        "open_trades": 0,
    }
    row = classify_cell_history_bucket(cell)
    assert row["history_bucket"] == "A_<14d"
    assert row["calendar_days"] == 3.0


def test_cap_transition_aggregation():
    obs = {
        "LINKUSDT": [
            {"effective_time": "2025-10-04T00:00:00+00:00", "market_cap": 2e9},
            {"effective_time": "2026-01-01T00:00:00+00:00", "market_cap": 12e9},
            {"effective_time": "2026-06-01T00:00:00+00:00", "market_cap": 12e9},
        ]
    }

    def classify(sym, et, observations):
        # emulate as-of: pick latest <= et from observations' market_cap thresholds
        best = None
        for o in observations:
            t = datetime.fromisoformat(o["effective_time"].replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t <= et:
                best = o
        if best is None:
            return _LK("UNAVAILABLE")
        mc = float(best["market_cap"])
        if mc >= 10e9:
            return _LK("LARGE_CAP")
        if mc >= 1e9:
            return _LK("MID_CAP")
        if mc >= 50e6:
            return _LK("SMALL_CAP")
        return _LK("UNAVAILABLE")

    rows = build_cap_transitions(obs, classify_fn=classify)
    assert len(rows) == 1
    assert rows[0]["transition_count"] == 1
    assert "MID_CAP" in rows[0]["groups_seen"] and "LARGE_CAP" in rows[0]["groups_seen"]
    summary = summarize_cap_transitions(rows)
    assert summary["symbols_with_1_transition"] == 1
    assert summary["symbols_with_overlapping_group_membership"] == 1


def test_funnel_reconciliation():
    expl = funnel_gap_explanation(
        candidates=23906,
        cap_eligible=17002,
        cross_cap=6791,
        entries=17002,
        closed=9651,
        open_trades=26,
    )
    assert expl["entries_equals_cap_eligible"] is True
    assert expl["evaluated_trades"] == 9677
    assert expl["entries_minus_evaluated"] == 17002 - 9677
    assert expl["suppression_interpretation"] == "LIKELY_ONE_OPEN_AT_A_TIME_SKIP"


def test_cost_reconciliation_aggregate():
    trades = [
        {
            "outcome": "TP1",
            "r_multiple": 2.0,
            "r_net": 1.8,
            "fee_total_usd": 20.0,
            "entry_price": 100.0,
            "stop_price": 99.0,
        },
        {
            "outcome": "SL",
            "r_multiple": -1.0,
            "r_net": -1.1,
            "fee_total_usd": 10.0,
            "entry_price": 100.0,
            "stop_price": 99.0,
        },
    ]
    m = aggregate_metrics(trades, slippage_rate=0.0002, risk_usd=100.0, group="TEST")
    assert m["trades"] == 2
    assert m["gross_R"] == 1.0
    assert m["fees_R"] == pytest.approx(0.3)  # 30/100
    assert m["slippage_R"] is not None
    assert m["net_R"] is not None
    # net = r_net - slip each
    assert m["net_R"] < m["gross_R"]


def test_chronological_split_validation():
    splits = split_period_indices(1000, train_fraction=0.6, validation_fraction=0.2, oos_fraction=0.2)
    t0, t1 = splits["TRAINING_PERIOD"]
    v0, v1 = splits["VALIDATION_PERIOD"]
    o0, o1 = splits["OUT_OF_SAMPLE_PERIOD"]
    assert t0 == 0
    assert t1 == v0
    assert v1 == o0
    assert o1 == 1000
    assert t1 <= v1 <= o1


def test_reconcile_artifact_totals_pass():
    totals = {
        "cells_run": 2,
        "bars_processed": 100,
        "signals_detected": 10,
        "cap_eligible_signals": 5,
        "candidates": 6,
        "entries": 5,
        "closed_trades": 3,
        "open_trades": 1,
    }
    cells = [
        {
            "closed_trades": 2,
            "period_metrics": {
                "TRAIN": {"trade_count": 1},
                "VALIDATION": {"trade_count": 1},
                "OOS": {"trade_count": 0},
            },
        },
        {
            "closed_trades": 1,
            "period_metrics": {
                "TRAIN": {"trade_count": 0},
                "VALIDATION": {"trade_count": 0},
                "OOS": {"trade_count": 1},
            },
        },
    ]
    funnel = [
        {
            "bars": 40,
            "signals_detected": 4,
            "cap_eligible_signals": 2,
            "candidates": 3,
            "entries": 2,
            "closed_trades": 2,
            "open_trades": 0,
        },
        {
            "bars": 60,
            "signals_detected": 6,
            "cap_eligible_signals": 3,
            "candidates": 3,
            "entries": 3,
            "closed_trades": 1,
            "open_trades": 1,
        },
    ]
    period = [
        {"key": "TRAIN", "closed_trades": 1},
        {"key": "VALIDATION", "closed_trades": 1},
        {"key": "OOS", "closed_trades": 1},
    ]
    recon = reconcile_artifact_totals(
        run_manifest={"run_id": "x", "dataset_label": "y", "totals": totals},
        funnel_rows=funnel,
        summary_rows=[{"closed_trades": 2}, {"closed_trades": 1}],
        period_rows=period,
        timeframe_rows=[{"closed_trades": 3}],
        symbol_rows=[{"closed_trades": 2}, {"closed_trades": 1}],
        direction_rows=[{"closed_trades": 3}],
        cells=cells,
    )
    assert recon["status"] == "PASS"
    assert recon["entries_minus_evaluated"] == 1


def test_reference_comparison_aggregation():
    rows = [
        {
            "strategy_id": "LARGE_CAP_SWEEP_CHOCH",
            "reference_detected": "True",
            "production_detected": "True",
            "difference_reason": "",
        },
        {
            "strategy_id": "LARGE_CAP_SWEEP_CHOCH",
            "reference_detected": "True",
            "production_detected": "False",
            "difference_reason": "MISSING_PRODUCTION",
        },
        {
            "strategy_id": "MID_CAP_FVG_DISCOUNT",
            "reference_detected": "False",
            "production_detected": "True",
            "difference_reason": "MISSING_REFERENCE",
        },
    ]
    agg = reference_comparison_aggregate(rows)
    by = {r["strategy"]: r for r in agg}
    assert by["LARGE_CAP_SWEEP_CHOCH"]["matched"] == 1
    assert by["LARGE_CAP_SWEEP_CHOCH"]["mismatched"] == 1
    assert by["MID_CAP_FVG_DISCOUNT"]["missing_reference"] == 1


def test_calendar_days():
    d = calendar_days("2026-01-01T00:00:00+00:00", "2026-01-11T00:00:00+00:00")
    assert d == 10.0
