"""Unit tests for COMBO_02_V1_1_RISK_CONTROLLED portfolio gates + OOS classification."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.research.combo02_v1_1_risk_controlled import (
    CLUSTER_HEAT_CAP,
    DIAGNOSTIC_FAILED_V1_OOS_END,
    DIAGNOSTIC_FAILED_V1_OOS_START,
    OOS_END,
    OOS_START,
    REJECT_CLUSTER_HEAT,
    REJECT_DAILY_LOSS,
    REJECT_MAX_CONCURRENT,
    REJECT_STRATEGY_DD,
    REJECT_SYMBOL_DD,
    REJECT_SYMBOL_STREAK,
    RISK_PERCENT_BY_SYMBOL,
    SMOKE_OOS_END,
    SMOKE_OOS_START,
    RiskControlledState,
    assert_not_failed_parent_for_acceptance,
    classify_v1_1_oos,
    replay_candidates_with_risk_controls,
    risk_percent_for,
)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def test_frozen_per_symbol_risk_not_flat():
    assert RISK_PERCENT_BY_SYMBOL["BTCUSDT"] == 0.015
    assert RISK_PERCENT_BY_SYMBOL["ETHUSDT"] == 0.005
    assert RISK_PERCENT_BY_SYMBOL["SOLUSDT"] == 0.005
    assert risk_percent_for("BTCUSDT") != risk_percent_for("ETHUSDT")


def test_formal_oos_window_constants():
    assert OOS_START == "2026-10-06"
    assert OOS_END == "2027-03-31"
    assert SMOKE_OOS_START == "2026-10-01"
    assert SMOKE_OOS_END == "2026-10-05"


def test_max_two_concurrent_rejects_with_max_concurrent_exceeded():
    st = RiskControlledState()
    ok1, _ = st.try_open(symbol="BTCUSDT", signal_time=_ts("2026-10-01T01:00:00Z"))
    ok2, _ = st.try_open(symbol="ETHUSDT", signal_time=_ts("2026-10-01T02:00:00Z"))
    ok3, reason = st.try_open(symbol="SOLUSDT", signal_time=_ts("2026-10-01T03:00:00Z"))
    assert ok1 and ok2
    assert not ok3
    assert reason == REJECT_MAX_CONCURRENT
    assert reason == "MAX_CONCURRENT_EXCEEDED"
    assert st.reject_counts[REJECT_MAX_CONCURRENT] == 1
    assert REJECT_CLUSTER_HEAT not in st.reject_counts


def test_cluster_heat_exceeded_explicit_reason():
    st = RiskControlledState()
    # Force heat breach with oversized requested risk while keeping concurrent room
    st.try_open(symbol="BTCUSDT", signal_time=_ts("2026-10-01T01:00:00Z"), risk_percent=0.03)
    ok, reason = st.try_open(
        symbol="ETHUSDT",
        signal_time=_ts("2026-10-01T02:00:00Z"),
        risk_percent=0.02,
    )
    assert not ok
    assert reason == REJECT_CLUSTER_HEAT
    assert reason == "CLUSTER_HEAT_EXCEEDED"
    assert st.open_risk_pct() + 0.02 > CLUSTER_HEAT_CAP
    assert REJECT_MAX_CONCURRENT not in st.reject_counts


def test_daily_loss_halt_blocks_new_entries_only():
    st = RiskControlledState()
    ok, _ = st.try_open(symbol="BTCUSDT", signal_time=_ts("2026-10-01T01:00:00Z"))
    assert ok
    st.on_close(symbol="BTCUSDT", exit_time=_ts("2026-10-01T05:00:00Z"), r_multiple=-2.0)
    ok2, reason = st.try_open(symbol="ETHUSDT", signal_time=_ts("2026-10-01T06:00:00Z"))
    assert not ok2
    assert reason == REJECT_DAILY_LOSS
    assert "BTCUSDT" not in st.open


def test_symbol_consecutive_loss_pause_24h():
    st = RiskControlledState()
    windows = [
        ("2026-10-01T01:00:00Z", "2026-10-01T05:00:00Z"),
        ("2026-10-02T01:00:00Z", "2026-10-02T05:00:00Z"),
        ("2026-10-03T01:00:00Z", "2026-10-03T05:00:00Z"),
    ]
    for open_t, close_t in windows:
        ok, _ = st.try_open(symbol="ETHUSDT", signal_time=_ts(open_t))
        assert ok
        st.on_close(symbol="ETHUSDT", exit_time=_ts(close_t), r_multiple=-1.0)
    ok, reason = st.try_open(symbol="ETHUSDT", signal_time=_ts("2026-10-03T10:00:00Z"))
    assert not ok
    assert reason == REJECT_SYMBOL_STREAK
    ok2, reason2 = st.try_open(symbol="ETHUSDT", signal_time=_ts("2026-10-04T06:00:00Z"))
    assert ok2 and reason2 == "OK"


def test_strategy_drawdown_halt():
    st = RiskControlledState()
    st.strategy_equity_r = 0.0
    st.strategy_peak_r = 6.0
    ok, reason = st.try_open(symbol="BTCUSDT", signal_time=_ts("2026-10-01T01:00:00Z"))
    assert not ok
    assert reason == REJECT_STRATEGY_DD


def test_symbol_drawdown_halt():
    st = RiskControlledState()
    st.symbol_equity_r["SOLUSDT"] = 0.0
    st.symbol_peak_r["SOLUSDT"] = 4.0
    ok, reason = st.try_open(symbol="SOLUSDT", signal_time=_ts("2026-10-01T01:00:00Z"))
    assert not ok
    assert reason == REJECT_SYMBOL_DD


def test_replay_rejects_have_explicit_max_concurrent_reason():
    cands = [
        {
            "symbol": "BTCUSDT",
            "signal_time": "2026-10-01T01:00:00+00:00",
            "exit_time": "2026-10-01T10:00:00+00:00",
            "r_multiple": -1.0,
            "r_net": -1.0,
        },
        {
            "symbol": "ETHUSDT",
            "signal_time": "2026-10-01T02:00:00+00:00",
            "exit_time": "2026-10-01T11:00:00+00:00",
            "r_multiple": -1.0,
            "r_net": -1.0,
        },
        {
            "symbol": "SOLUSDT",
            "signal_time": "2026-10-01T03:00:00+00:00",
            "exit_time": "2026-10-01T12:00:00+00:00",
            "r_multiple": 2.0,
            "r_net": 1.9,
        },
    ]
    out = replay_candidates_with_risk_controls(cands)
    assert len(out["accepted_trades"]) == 2
    assert out["reject_counts"].get(REJECT_MAX_CONCURRENT) == 1
    assert all("reason" in r for r in out["rejected"])


def test_window_not_started_disables_classification():
    out = classify_v1_1_oos(
        combined_trades=40,
        average_net_r=0.5,
        max_drawdown_r=1.0,
        max_losing_streak=1,
        data_completeness_ok=True,
        lookahead_ok=True,
        window_start_date=OOS_START,
        window_end_date=OOS_END,
        now=_ts("2026-10-05T12:00:00Z"),  # before 2026-10-06 start
    )
    assert out["status"] == "INSUFFICIENT_SAMPLE"
    assert "WINDOW_NOT_STARTED" in out["guards"]


def test_five_day_incomplete_window_insufficient_sample():
    """Smoke window 2026-10-01→05 still filling / not for acceptance."""
    out = classify_v1_1_oos(
        combined_trades=0,
        average_net_r=None,
        max_drawdown_r=0.0,
        max_losing_streak=0,
        data_completeness_ok=False,
        lookahead_ok=True,
        window_start_date=SMOKE_OOS_START,
        window_end_date=SMOKE_OOS_END,
        now=_ts("2026-10-05T12:00:00Z"),  # before inclusive end
        acceptance_run=False,
    )
    assert out["status"] == "INSUFFICIENT_SAMPLE"
    assert "WINDOW_NOT_ELAPSED" in out["guards"] or "DATA_COMPLETENESS_BELOW_MIN" in out[
        "guards"
    ]


def test_zero_trade_complete_window_insufficient_sample():
    out = classify_v1_1_oos(
        combined_trades=0,
        average_net_r=None,
        max_drawdown_r=0.0,
        max_losing_streak=0,
        data_completeness_ok=True,
        lookahead_ok=True,
        window_start_date=OOS_START,
        window_end_date=OOS_END,
        now=_ts("2027-04-01T00:00:00Z"),
    )
    assert out["status"] == "INSUFFICIENT_SAMPLE"
    assert "COMBINED_TRADES_BELOW_15" in out["guards"]


def test_drawdown_over_6r_blocked_with_adequate_sample():
    out = classify_v1_1_oos(
        combined_trades=30,
        average_net_r=0.1,
        max_drawdown_r=6.5,
        max_losing_streak=3,
        data_completeness_ok=True,
        lookahead_ok=True,
        window_start_date=OOS_START,
        window_end_date=OOS_END,
        now=_ts("2027-04-01T00:00:00Z"),
        per_symbol_trades={"BTCUSDT": 12, "ETHUSDT": 10, "SOLUSDT": 8},
    )
    assert out["status"] == "BLOCKED"
    assert any("MAX_DD" in x for x in out["hard_fails"])
    assert any("BTCUSDT" in r for r in out["reviews"])  # per-symbol claim guard


def test_ready_for_paper_requires_30_trades():
    out = classify_v1_1_oos(
        combined_trades=30,
        average_net_r=0.2,
        max_drawdown_r=3.0,
        max_losing_streak=2,
        data_completeness_ok=True,
        lookahead_ok=True,
        window_start_date=OOS_START,
        window_end_date=OOS_END,
        now=_ts("2027-04-01T00:00:00Z"),
        per_symbol_trades={"BTCUSDT": 20, "ETHUSDT": 20, "SOLUSDT": 20},
    )
    assert out["status"] == "READY_FOR_PAPER"


def test_pass_with_review_for_15_to_29():
    out = classify_v1_1_oos(
        combined_trades=20,
        average_net_r=0.15,
        max_drawdown_r=2.0,
        max_losing_streak=1,
        data_completeness_ok=True,
        lookahead_ok=True,
        window_start_date=OOS_START,
        window_end_date=OOS_END,
        now=_ts("2027-04-01T00:00:00Z"),
    )
    assert out["status"] == "OOS_PASS_WITH_REVIEW"


def test_failed_parent_window_forbidden_for_acceptance():
    with pytest.raises(ValueError, match="must not be used"):
        assert_not_failed_parent_for_acceptance(
            DIAGNOSTIC_FAILED_V1_OOS_START, DIAGNOSTIC_FAILED_V1_OOS_END
        )
    out = classify_v1_1_oos(
        combined_trades=40,
        average_net_r=0.5,
        max_drawdown_r=1.0,
        max_losing_streak=1,
        data_completeness_ok=True,
        lookahead_ok=True,
        window_start_date=DIAGNOSTIC_FAILED_V1_OOS_START,
        window_end_date=DIAGNOSTIC_FAILED_V1_OOS_END,
        now=_ts("2026-10-01T00:00:00Z"),
        acceptance_run=True,
    )
    assert out["status"] == "INSUFFICIENT_SAMPLE"
    assert "FAILED_PARENT_WINDOW_FORBIDDEN" in out["guards"]
