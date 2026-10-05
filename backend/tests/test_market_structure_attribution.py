"""Attribution + 15m status + isolation tests for market-structure analytics."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.research.bos_combinations import get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.market_structure.artifacts import write_market_structure_artifacts
from app.research.market_structure.attribution import (
    attribute_bar,
    build_index_maps,
    classify_15m_status,
    normalize_ledger_trades,
)
from app.research.market_structure.config import assert_regime_filtering_safe
from app.research.market_structure.engine import compute_market_structure_analytics
from app.research.market_structure.attach import attach_market_structure_to_row
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


def _expand_15m(c1h: list[dict]) -> list[dict]:
    out = []
    for c in c1h:
        for k in range(4):
            out.append(
                {
                    "time": c["time"] + timedelta(minutes=15 * k),
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c["volume"] / 4,
                }
            )
    return out


def test_one_execution_row_per_trade_id():
    c1h = _candles(80)
    trades = [
        {
            "trade_no": 1,
            "entry_index": 40,
            "exit_index": 45,
            "signal_time": c1h[40]["time"].isoformat(),
            "exit_time": c1h[45]["time"].isoformat(),
            "direction": "LONG",
            "outcome": "TP1",
            "r_multiple": 2.0,
        },
        {
            "trade_no": 2,
            "entry_index": 50,
            "exit_index": 52,
            "signal_time": c1h[50]["time"].isoformat(),
            "exit_time": c1h[52]["time"].isoformat(),
            "direction": "LONG",
            "outcome": "SL",
            "r_multiple": -1.0,
        },
    ]
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_down(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=_expand_15m(c1h),
        trades=trades,
        index_start=30,
        research_only=True,
    )
    assert analytics["status"] == "OK"
    exec_rows = [
        r for r in analytics["by_bar"] if r["entry_attribution_type"] == "EXECUTION_BAR"
    ]
    assert len(exec_rows) == 2
    ids = [str(r["trade_id"]) for r in exec_rows]
    assert sorted(ids) == ["1", "2"]
    assert analytics["quality_report"]["duplicate_execution_entries_per_trade"] == 0
    # no consecutive duplicate YES for same trade
    yes = [r for r in analytics["by_bar"] if r["entry"] == "YES"]
    assert len(yes) == 2


def test_decision_iso_no_longer_double_marks_entry():
    """Regression: signal_time == prior decision_iso must not create second entry."""
    c1h = _candles(60)
    # signal_time equals open of bar 40; decision_time of bar 39 equals that open+0? 
    # decision of bar 39 open = close of bar 39 = open of bar 40 = signal_time
    trades = [
        {
            "trade_no": 7,
            "entry_index": 40,
            "exit_index": 42,
            "signal_time": c1h[40]["time"].isoformat(),
            "direction": "LONG",
            "outcome": "TP1",
            "r_multiple": 1.5,
        }
    ]
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_down(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=None,
        trades=trades,
        index_start=35,
        research_only=True,
    )
    yes = [r for r in analytics["by_bar"] if r["entry"] == "YES"]
    assert len(yes) == 1
    assert yes[0]["bar_index"] == 40
    assert yes[0]["entry_attribution_type"] == "EXECUTION_BAR"


def test_signal_and_execution_same_bar_for_combo_style_fill():
    c1h = _candles(50)
    trades = [
        {
            "trade_no": 3,
            "entry_index": 30,
            "exit_index": 33,
            "signal_time": c1h[30]["time"].isoformat(),
            "direction": "LONG",
            "outcome": "TP1",
            "r_multiple": 2.0,
        }
    ]
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_down(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=None,
        trades=trades,
        index_start=20,
        research_only=True,
    )
    row = next(r for r in analytics["by_bar"] if r["bar_index"] == 30)
    assert row["signal_bar"] is True
    assert row["execution_bar"] is True
    assert row["entry_attribution_type"] == "EXECUTION_BAR"


def test_position_active_not_counted_as_entry():
    c1h = _candles(50)
    trades = [
        {
            "trade_no": 9,
            "entry_index": 25,
            "exit_index": 28,
            "signal_time": c1h[25]["time"].isoformat(),
            "direction": "LONG",
            "outcome": "TP1",
            "r_multiple": 2.0,
        }
    ]
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_down(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=None,
        trades=trades,
        index_start=20,
        research_only=True,
    )
    mid = next(r for r in analytics["by_bar"] if r["bar_index"] == 26)
    assert mid["entry"] == "NO"
    assert mid["entry_attribution_type"] == "POSITION_ACTIVE"
    assert mid["accepted_signal"] is False
    exit_row = next(r for r in analytics["by_bar"] if r["bar_index"] == 28)
    assert exit_row["row_role"] == "TRADE_EXIT_BAR"


def test_unique_trade_count_equals_ledger():
    c1h = _candles(70)
    trades = [
        {
            "trade_no": i + 1,
            "entry_index": 30 + i * 5,
            "exit_index": 32 + i * 5,
            "signal_time": c1h[30 + i * 5]["time"].isoformat(),
            "direction": "LONG",
            "outcome": "TP1",
            "r_multiple": 1.0,
            "gross_pnl_usd": 10.0,
            "fee_total_usd": 1.0,
            "net_pnl_usd": 9.0,
        }
        for i in range(3)
    ]
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_down(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=None,
        trades=trades,
        index_start=20,
        research_only=True,
    )
    assert analytics["row_counts"]["unique_executed_trades"] == 3
    assert analytics["row_counts"]["ledger_trade_count"] == 3
    assert len(analytics["trade_context"]) == 3
    assert analytics["row_counts"]["raw_decision_rows"] > 3
    assert analytics["quality_report"]["trade_context_reconciles_to_ledger"] is True


def test_15m_source_vs_feature_availability():
    c1h = _candles(40)
    # Source present but very short 15m history → some unknown/insufficient
    c15 = _expand_15m(c1h[:5])  # truncated source relative to full 1h
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_down(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=c15,
        trades=[],
        index_start=10,
        research_only=True,
    )
    q = analytics["quality_report"]
    assert analytics["15m_source_available"] is True
    assert q["15m_source_available_rows"] == q["total_1h_decision_bars"]
    # Feature availability must be reported separately and can be lower
    assert "15m_feature_available_rows" in q
    assert q["15m_feature_available_rows"] <= q["15m_source_available_rows"]
    assert sum(q["15m_status_distribution"].values()) == q["total_1h_decision_bars"]


def test_15m_unavailable_when_source_missing():
    c1h = _candles(40)
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_down(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=None,
        trades=[],
        index_start=20,
        research_only=True,
    )
    assert analytics["15m_source_available"] is False
    assert analytics["15m_status"] == "UNAVAILABLE"
    for r in analytics["by_bar"]:
        assert r["15m_source_available"] is False
        assert r["15m_feature_available"] is False
        assert r["15m_status"] == "UNAVAILABLE"


def test_missing_features_do_not_create_trades():
    c1h = _candles(40)
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=None,
        candles_1h=c1h,
        candles_15m=None,
        trades=[],
        index_start=20,
        research_only=True,
    )
    assert analytics["row_counts"]["unique_executed_trades"] == 0
    assert all(r["entry"] == "NO" for r in analytics["by_bar"])


def test_research_labels_do_not_change_rejection_reasons():
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
    )
    for r in analytics["by_bar"]:
        if r["entry_attribution_type"] == "NO_ENTRY":
            assert r["primary_rejection_reason"] == "NO_STRATEGY_ENTRY_AT_BAR"
            assert r["primary_rejection_stage"] == "UNKNOWN_NOT_EXPORTED"
            assert r["rejection_detail_status"] == "NOT_EXPORTED"
            # research label must not overwrite rejection
            assert r["research_label"] != r["primary_rejection_reason"] or r[
                "research_label"
            ] in {None, "NONE", "NO_TRADE_CHOPPY", "TRANSITION_WAIT", "TIMEFRAME_CONFLICT_AVOID", "TREND_FOLLOWING_CANDIDATE", "PULLBACK_OR_WAIT", "MEAN_REVERSION_CANDIDATE", "BREAKOUT_CANDIDATE", "SHORT_TREND_FOLLOWING_CANDIDATE"}


def test_analytics_does_not_change_strategy_outputs():
    c1h = _candles(120, drift=0.25)
    c4h = _down(c1h, 4, 14400)
    combo = get_combination("COMBO_02")
    assert combo is not None
    baseline = run_combination_backtest(
        "BTCUSDT",
        "1h",
        c1h,
        combo,
        signal_config=SignalConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        direction_filter="LONG",
        index_start=40,
    )
    observed = run_combination_backtest(
        "BTCUSDT",
        "1h",
        c1h,
        combo,
        signal_config=SignalConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        direction_filter="LONG",
        index_start=40,
    )
    assert baseline.get("trades") == observed.get("trades")
    assert (baseline.get("result") or {}).get("equity_curve_r") == (
        observed.get("result") or {}
    ).get("equity_curve_r")
    assert baseline.get("configuration_hash") == observed.get("configuration_hash")

    row = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "trades": list(observed.get("trades") or []),
        "configuration_fingerprint": "fp_config_abc",
        "dataset_fingerprint": "fp_dataset_xyz",
        "equity_curve_r": list((observed.get("result") or {}).get("equity_curve_r") or []),
        "pnl_usd_net": 42.0,
    }
    attached = attach_market_structure_to_row(
        row,
        setup_candles=c1h,
        candles_4h=c4h,
        candles_1h=c1h,
        candles_15m=None,
        index_start=40,
        analytics_enabled=True,
        write_artifacts=False,
        research_only=True,
    )
    assert attached["trades"] == row["trades"]
    assert attached["equity_curve_r"] == row["equity_curve_r"]
    assert attached["pnl_usd_net"] == 42.0
    assert attached["configuration_fingerprint"] == "fp_config_abc"
    assert attached["dataset_fingerprint"] == "fp_dataset_xyz"
    ms = attached["market_structure"]
    assert ms["quality_report"]["duplicate_execution_entries_per_trade"] == 0


def test_trade_context_one_row_per_trade_and_exports(tmp_path: Path):
    c1h = _candles(60)
    trades = [
        {
            "trade_no": 1,
            "entry_index": 35,
            "exit_index": 38,
            "signal_time": c1h[35]["time"].isoformat(),
            "exit_time": c1h[38]["time"].isoformat(),
            "direction": "LONG",
            "outcome": "TP1",
            "r_multiple": 2.0,
            "gross_pnl_usd": 20.0,
            "fee_total_usd": 1.0,
            "net_pnl_usd": 19.0,
        }
    ]
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_down(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=_expand_15m(c1h),
        trades=trades,
        index_start=30,
        research_only=True,
        run_id="attrib_fix",
    )
    assert len(analytics["trade_context"]) == 1
    assert analytics["trade_context"][0]["context_match_status"] in {
        "MATCHED_UNIQUE",
        "MATCHED_WITH_UNKNOWN_FEATURES",
    }
    # grouped R reconciles
    exec_r = sum(
        float(r["r_multiple"])
        for r in analytics["by_bar"]
        if r["entry_attribution_type"] == "EXECUTION_BAR" and r.get("r_multiple") is not None
    )
    ctx_r = sum(
        float(r["r_multiple"])
        for r in analytics["trade_context"]
        if r.get("r_multiple") is not None
    )
    assert abs(exec_r - ctx_r) < 1e-9
    pnl = sum(float(r["net_pnl"]) for r in analytics["trade_context"] if r.get("net_pnl") is not None)
    assert abs(pnl - 19.0) < 1e-9

    paths = write_market_structure_artifacts(
        analytics, run_id="attrib_fix", reports_root=tmp_path
    )
    assert Path(paths["trade_context.csv"]).exists()
    assert Path(paths["market_structure_by_bar.csv"]).exists()
    assert Path(paths["unmatched_trade_context.csv"]).exists()


def test_unmatched_trade_reported_not_dropped():
    c1h = _candles(40)
    # entry_index outside analytics window
    trades = [
        {
            "trade_no": 99,
            "entry_index": 5,
            "exit_index": 6,
            "signal_time": c1h[5]["time"].isoformat(),
            "direction": "LONG",
            "outcome": "TP1",
            "r_multiple": 1.0,
        }
    ]
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_down(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=None,
        trades=trades,
        index_start=20,
        research_only=True,
    )
    assert len(analytics["trade_context"]) == 1
    assert analytics["trade_context"][0]["context_match_status"] == "UNMATCHED"
    assert len(analytics["unmatched_trade_context"]) == 1


def test_classify_15m_status_helpers():
    ok = classify_15m_status(
        source_available=True,
        snap_quality="OK",
        last_closed_candle_time="2024-01-01T01:00:00+00:00",
        decision_time=datetime(2024, 1, 1, 2, tzinfo=timezone.utc),
    )
    assert ok["15m_status"] == "OK"
    assert ok["15m_feature_available"] is True
    miss = classify_15m_status(
        source_available=True,
        snap_quality="UNKNOWN_DATA_MISSING",
        last_closed_candle_time=None,
        decision_time=None,
    )
    assert miss["15m_status"] == "UNKNOWN_DATA_MISSING"
    assert miss["15m_feature_available"] is False


def test_attribute_bar_maps():
    ledger = normalize_ledger_trades(
        [
            {
                "trade_no": 1,
                "entry_index": 10,
                "exit_index": 12,
                "signal_time": "2024-01-01T10:00:00+00:00",
            }
        ]
    )
    by_entry, active, by_exit = build_index_maps(ledger)
    a = attribute_bar(10, by_entry=by_entry, active=active, by_exit=by_exit)
    assert a["entry_attribution_type"] == "EXECUTION_BAR"
    b = attribute_bar(11, by_entry=by_entry, active=active, by_exit=by_exit)
    assert b["entry_attribution_type"] == "POSITION_ACTIVE"
    c = attribute_bar(5, by_entry=by_entry, active=active, by_exit=by_exit)
    assert c["entry_attribution_type"] == "NO_ENTRY"


def test_regime_filtering_still_blocked():
    with pytest.raises(ValueError):
        assert_regime_filtering_safe(enable_regime_filtering=True, research_only=False)


def test_no_paper_live_side_effects_in_attribution_module():
    src = Path(
        __import__("app.research.market_structure.attribution", fromlist=["x"]).__file__
    ).read_text(encoding="utf-8").lower()
    assert "telegram" not in src
    assert "paper_trade" not in src
    assert "live_trade" not in src
