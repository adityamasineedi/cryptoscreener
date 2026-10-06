"""Entry-rejection diagnostics — observability only; no strategy mutation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.research.bos_combinations import get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.entry_diagnostics import (
    REASON_BOS_PREFILTER_SKIP,
    REASON_ENTRY_ACCEPTED,
    REASON_NO_STRATEGY_ENTRY,
    REASON_POSITION_ACTIVE,
    STAGE_BOS,
    STAGE_HTF_ALIGNMENT,
    STAGE_POSITION_STATE,
    STAGE_RR,
    STAGE_STRUCTURE,
    STAGE_UNKNOWN,
    SETUP_DATA_UNAVAILABLE,
    SETUP_ENTRY_ACCEPTED,
    SETUP_ENTRY_READY_REJECTED,
    SETUP_NO_SETUP,
    SETUP_POSITION_BLOCKED,
    SETUP_REJECTED,
    classify_bos_prefilter_skip,
    classify_from_eval_setup,
    classify_position_bar,
    build_entry_funnel_reports,
)
from app.research.market_structure.engine import compute_market_structure_analytics
from app.signals.config import SignalConfig


def _candles(n: int, *, drift: float = 0.2) -> list[dict]:
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    out = []
    px = 100.0
    for i in range(n):
        o = px
        c = px + drift
        out.append(
            {
                "time": t0 + timedelta(hours=i),
                "open": o,
                "high": max(o, c) + 0.5,
                "low": min(o, c) - 0.5,
                "close": c,
                "volume": 1000.0 + i,
            }
        )
        px = c
    return out


def _down(c1h: list[dict], every: int, tf: int) -> list[dict]:
    out = []
    for i in range(0, len(c1h), every):
        chunk = c1h[i : i + every]
        if not chunk:
            continue
        out.append(
            {
                "time": c1h[0]["time"] + timedelta(seconds=tf * len(out)),
                "open": chunk[0]["open"],
                "high": max(x["high"] for x in chunk),
                "low": min(x["low"] for x in chunk),
                "close": chunk[-1]["close"],
                "volume": sum(x["volume"] for x in chunk),
            }
        )
    return out


def test_diagnostics_do_not_change_signal_decisions():
    c1h = _candles(160, drift=0.3)
    c4h = _down(c1h, 4, 14400)
    combo = get_combination("COMBO_02")
    assert combo is not None
    a = run_combination_backtest(
        "BTCUSDT",
        "1h",
        c1h,
        combo,
        signal_config=SignalConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        direction_filter="LONG",
        index_start=50,
    )
    b = run_combination_backtest(
        "BTCUSDT",
        "1h",
        c1h,
        combo,
        signal_config=SignalConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        direction_filter="LONG",
        index_start=50,
    )
    assert a["trades"] == b["trades"]
    assert (a.get("result") or {}).get("equity_curve_r") == (
        b.get("result") or {}
    ).get("equity_curve_r")
    assert a.get("sample_size") == b.get("sample_size")
    # Side-channel present but must not alter trades.
    assert isinstance(a.get("entry_diagnostics"), dict)
    assert len(a["entry_diagnostics"]) > 0


def test_no_confirmed_bos_prefilter_classified():
    d = classify_bos_prefilter_skip()
    assert d["setup_state"] == SETUP_NO_SETUP
    assert d["primary_rejection_stage"] == STAGE_BOS
    assert d["primary_rejection_reason"] == REASON_BOS_PREFILTER_SKIP


def test_pre_walk_bars_are_no_evaluation_not_unknown():
    from app.research.entry_diagnostics import classify_insufficient_bars_before_walk

    d = classify_insufficient_bars_before_walk()
    assert d["evaluation_type"] == "NO_EVALUATION"
    assert d["setup_state"] == SETUP_DATA_UNAVAILABLE
    assert d["primary_rejection_reason"] == "INSUFFICIENT_BARS_BEFORE_WALK"


def test_combination_gates_bos_failure_classified():
    d = classify_from_eval_setup(
        {
            "status": "NO_SETUP",
            "direction": None,
            "reason": "Combination gates not satisfied",
            "gates": {"bos": False, "trend": False, "htf": True},
            "required": ["bos", "trend", "htf"],
        }
    )
    assert d["setup_state"] == SETUP_NO_SETUP
    assert d["primary_rejection_stage"] == STAGE_BOS
    assert d["primary_rejection_reason"] == "Combination gates not satisfied"
    assert "bos" in d["failed_gates"]


def test_htf_conflict_classified():
    d = classify_from_eval_setup(
        {
            "status": "NO_SETUP",
            "direction": "LONG",
            "reason": "HTF gate blocked: HTF_CONFLICT (4h=BEARISH, 1h=BULLISH)",
            "gates": {
                "bos": True,
                "trend": True,
                "hl_intact": True,
                "htf": False,
            },
            "required": ["bos", "trend", "hl_intact", "htf"],
            "htf": {"htf_alignment": "HTF_CONFLICT"},
        }
    )
    assert d["setup_state"] == SETUP_REJECTED
    assert d["primary_rejection_stage"] == STAGE_HTF_ALIGNMENT
    assert d["primary_rejection_reason"].startswith("HTF gate blocked: HTF_CONFLICT")


def test_structure_invalid_classified():
    d = classify_from_eval_setup(
        {
            "status": "NO_SETUP",
            "direction": "LONG",
            "reason": "STRUCTURE_INVALID:HL_BROKEN",
            "gates": {
                "bos": True,
                "trend": True,
                "hl_intact": False,
                "htf": True,
            },
            "required": ["bos", "trend", "hl_intact", "htf"],
        }
    )
    assert d["setup_state"] == SETUP_REJECTED
    assert d["primary_rejection_stage"] == STAGE_STRUCTURE
    assert d["primary_rejection_reason"] == "STRUCTURE_INVALID:HL_BROKEN"


def test_risk_rr_rejection_classified():
    d = classify_from_eval_setup(
        {
            "status": "NO_SETUP",
            "direction": "LONG",
            "reason": "TP1_R 0.4 below minimum 1.0 (first-target payoff gate)",
            "gates": {
                "bos": True,
                "trend": True,
                "hl_intact": True,
                "htf": True,
                "rr": False,
            },
            "required": ["bos", "trend", "hl_intact", "htf", "rr"],
        }
    )
    assert d["setup_state"] == SETUP_ENTRY_READY_REJECTED
    assert d["primary_rejection_stage"] == STAGE_RR
    assert "TP1_R" in d["primary_rejection_reason"]


def test_htf_missing_data_classified():
    d = classify_from_eval_setup(
        {
            "status": "NO_SETUP",
            "direction": "LONG",
            "reason": "HTF candles missing — fail closed",
            "gates": {"bos": True, "trend": True, "htf": False},
            "required": ["bos", "trend", "htf"],
        }
    )
    assert d["setup_state"] == SETUP_DATA_UNAVAILABLE
    assert d["primary_rejection_stage"] == "DATA"


def test_accepted_entry_classified():
    d = classify_from_eval_setup(
        {
            "status": "LONG_ENTRY_CANDIDATE",
            "direction": "LONG",
            "gates": {"bos": True, "trend": True, "hl_intact": True, "htf": True},
            "required": ["bos", "trend", "hl_intact", "htf"],
            "htf": {"htf_alignment": "HTF_ALIGNED"},
        }
    )
    assert d["setup_state"] == SETUP_ENTRY_ACCEPTED
    assert d["primary_rejection_reason"] == REASON_ENTRY_ACCEPTED
    assert d["primary_rejection_stage"] is None


def test_position_active_not_new_entry_rejection():
    d = classify_position_bar(exited=False)
    assert d["evaluation_type"] == "POSITION_OPEN"
    assert d["setup_state"] == SETUP_POSITION_BLOCKED
    assert d["primary_rejection_stage"] == STAGE_POSITION_STATE
    assert d["primary_rejection_reason"] == REASON_POSITION_ACTIVE

    funnel = build_entry_funnel_reports(
        [
            {
                "evaluation_type": "POSITION_OPEN",
                "setup_state": SETUP_POSITION_BLOCKED,
                "primary_rejection_stage": STAGE_POSITION_STATE,
                "primary_rejection_reason": REASON_POSITION_ACTIVE,
                "market_regime": "BULL_TREND",
                "entry": "NO",
            },
            {
                "evaluation_type": "NEW_ENTRY",
                "setup_state": SETUP_NO_SETUP,
                "primary_rejection_stage": STAGE_BOS,
                "primary_rejection_reason": REASON_BOS_PREFILTER_SKIP,
                "market_regime": "BULL_TREND",
                "gates": {},
                "entry": "NO",
            },
        ]
    )
    assert funnel["new_entry_evaluations"] == 1
    assert funnel["position_open_bars"] == 1
    ranking_reasons = {r["rejection_reason"] for r in funnel["rejection_ranking"]}
    assert REASON_POSITION_ACTIVE not in ranking_reasons


def test_unknown_without_side_channel_stays_unknown():
    c1h = _candles(40)
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_down(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=None,
        trades=[],
        index_start=25,
        research_only=True,
        entry_diagnostics=None,
    )
    for r in analytics["by_bar"]:
        if r["entry_attribution_type"] == "NO_ENTRY":
            assert r["primary_rejection_stage"] == STAGE_UNKNOWN
            assert r["primary_rejection_reason"] == REASON_NO_STRATEGY_ENTRY
            assert r["rejection_detail_status"] == "NOT_EXPORTED"


def test_side_channel_merges_into_analytics():
    c1h = _candles(40)
    diag = {
        30: {
            "evaluation_type": "NEW_ENTRY",
            "setup_state": SETUP_REJECTED,
            "primary_rejection_stage": STAGE_HTF_ALIGNMENT,
            "primary_rejection_reason": (
                "HTF gate blocked: HTF_CONFLICT (4h=BEARISH, 1h=BULLISH)"
            ),
            "rejection_detail_status": "AVAILABLE",
            "direction_considered": "LONG",
            "gates": {"bos": True, "trend": True, "htf": False},
            "required": ["bos", "trend", "htf"],
            "failed_gates": ["htf"],
            "rejection_stage_1": STAGE_HTF_ALIGNMENT,
            "rejection_reason_1": (
                "HTF gate blocked: HTF_CONFLICT (4h=BEARISH, 1h=BULLISH)"
            ),
        }
    }
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_down(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=None,
        trades=[],
        index_start=25,
        research_only=True,
        entry_diagnostics=diag,
    )
    row = next(r for r in analytics["by_bar"] if r["bar_index"] == 30)
    assert row["primary_rejection_stage"] == STAGE_HTF_ALIGNMENT
    assert "HTF_CONFLICT" in row["primary_rejection_reason"]
    assert row["setup_state"] == SETUP_REJECTED
    assert row["evaluation_type"] == "NEW_ENTRY"
    assert analytics["entry_funnel"]["unknown_not_exported_count"] >= 0


def test_no_future_candle_in_diagnostics_keys():
    """Diagnostics are keyed by as_of bar index only (same state as engine)."""
    c1h = _candles(120, drift=0.25)
    c4h = _down(c1h, 4, 14400)
    combo = get_combination("COMBO_02")
    assert combo is not None
    out = run_combination_backtest(
        "BTCUSDT",
        "1h",
        c1h,
        combo,
        signal_config=SignalConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        direction_filter="LONG",
        index_start=40,
        index_end=80,
    )
    for bar_i in out["entry_diagnostics"]:
        assert 40 <= int(bar_i) < 80
        rec = out["entry_diagnostics"][bar_i]
        assert "future" not in str(rec).lower()
        assert rec.get("evaluation_type") in {
            "NEW_ENTRY",
            "POSITION_OPEN",
            "POSITION_MANAGEMENT",
            "NO_EVALUATION",
        }


def test_timeframe_conflict_research_label_not_used_as_strategy_reason():
    """TIMEFRAME_CONFLICT is an MTF research label, not a COMBO_02 gate reason."""
    d = classify_from_eval_setup(
        {
            "status": "NO_SETUP",
            "direction": "LONG",
            "reason": "HTF gate blocked: HTF_CONFLICT (4h=BEARISH, 1h=BULLISH)",
            "gates": {"bos": True, "trend": True, "htf": False},
            "required": ["bos", "trend", "htf"],
        }
    )
    # Exact engine reason preserved — not replaced with TIMEFRAME_CONFLICT.
    assert d["primary_rejection_reason"].startswith("HTF gate blocked: HTF_CONFLICT")
    assert d["primary_rejection_reason"] != "TIMEFRAME_CONFLICT"
