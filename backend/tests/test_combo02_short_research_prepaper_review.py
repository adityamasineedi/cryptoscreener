"""Final pre-paper review: independent replay gate, sample/recon, no paper enable."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.research.combo02_short_research_runner import (
    run_short_research_batch,
    short_research_report_store,
)
from app.research.combo02_short_research import short_research_registry
from app.research.short_research_constants import STRATEGY_ID
from app.research.short_research_forensics import (
    EVIDENCE_DERIVED,
    EVIDENCE_DIRECT,
    EVIDENCE_UNKNOWN,
    apply_independent_replay_to_trade,
    audit_trades_for_lookahead,
    build_trade_forensic_audit,
    enrich_trade_forensic_fields,
    independent_candle_replay_audit,
)
from app.research.short_research_prepaper_review import (
    REVIEW_BLOCKED,
    REVIEW_READY,
    RESEARCH_ONLY_CONTINUES,
    build_prepaper_review,
    oos_validation_sample_warning,
    sample_class,
)
from app.research.short_research_quality import (
    configuration_fingerprint,
    dataset_fingerprint,
    reconcile_research_blotter,
)
from app.research.short_research_windows import (
    DEFAULT_SHORT_RESEARCH_WINDOWS,
    validate_research_windows,
)


def _ts(h: int) -> datetime:
    return datetime(2025, 2, 1, tzinfo=timezone.utc) + timedelta(hours=h)


def _c(h: int, *, o=100.0, hi=101.0, lo=99.0, cl=100.0) -> dict[str, Any]:
    return {"time": _ts(h), "open": o, "high": hi, "low": lo, "close": cl, "volume": 1.0}


def _series_1h(n: int = 48) -> list[dict[str, Any]]:
    return [_c(i, cl=100 - i * 0.01) for i in range(n)]


def _series_4h(n: int = 20) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        out.append(
            {
                "time": datetime(2025, 1, 20, tzinfo=timezone.utc) + timedelta(hours=4 * i),
                "open": 110.0,
                "high": 111.0,
                "low": 108.0,
                "close": 109.0,
                "volume": 1.0,
            }
        )
    return out


@pytest.fixture(autouse=True)
def _clean():
    short_research_report_store.clear()
    short_research_registry._rows.clear()
    yield
    short_research_report_store.clear()
    short_research_registry._rows.clear()


def test_derived_only_cannot_produce_research_quality_pass():
    t = enrich_trade_forensic_fields(
        {
            "signal_time": "2025-02-01T10:00:00+00:00",
            "entry_time": "2025-02-01T10:00:00+00:00",
            "exit_time": "2025-02-01T12:00:00+00:00",
            "entry_price": 100.0,
            "stop_price": 105.0,
            "take_profit_price": 90.0,
            "holding_bars": 2,
            "direction": "SHORT",
        }
    )
    assert t["forensic_evidence_class"] == EVIDENCE_DERIVED
    audit = build_trade_forensic_audit(t)
    assert audit["lookahead_status"] == "PASS"
    assert audit["evidence_class"] == EVIDENCE_DERIVED
    assert audit["research_quality"] == "REVIEW_REQUIRED"
    bundle = audit_trades_for_lookahead([t])
    assert bundle["forensic_pass_for_promotion"] is False
    assert bundle["research_quality"] == "REVIEW_REQUIRED"


def test_direct_candle_replay_can_produce_forensic_pass():
    candles_1h = _series_1h(60)
    candles_4h = _series_4h(30)
    signal = _ts(20)
    entry = _ts(20)
    replay = independent_candle_replay_audit(
        candles_1h=candles_1h,
        candles_4h=candles_4h,
        signal_time=signal,
        entry_time=entry,
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=90.0,
        direction="SHORT",
        htf_refs={"require_htf": True},
    )
    assert replay["ok"] is True
    assert replay["evidence_class"] == EVIDENCE_DIRECT
    assert replay["research_quality"] == "PASS"
    trade = apply_independent_replay_to_trade(
        {
            "signal_time": signal.isoformat(),
            "entry_time": entry.isoformat(),
            "exit_time": _ts(22).isoformat(),
            "entry_price": 100.0,
            "stop_price": 105.0,
            "take_profit_price": 90.0,
            "direction": "SHORT",
        },
        candles_1h=candles_1h,
        candles_4h=candles_4h,
    )
    assert trade["forensic_evidence_class"] == EVIDENCE_DIRECT
    audit = build_trade_forensic_audit(trade)
    assert audit["research_quality"] == "PASS"
    assert audit["evidence_class"] == EVIDENCE_DIRECT


def test_unknown_replay_evidence_produces_review_required():
    replay = independent_candle_replay_audit(
        candles_1h=[],
        candles_4h=[],
        signal_time="2025-02-01T00:00:00+00:00",
        entry_time="2025-02-01T00:00:00+00:00",
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=90.0,
    )
    assert replay["ok"] is False
    assert replay["evidence_class"] == EVIDENCE_UNKNOWN
    assert replay["research_quality"] == "REVIEW_REQUIRED"


def test_future_htf_bos_swing_rejected():
    signal = _ts(20)
    candles_1h = _series_1h(40)
    candles_4h = _series_4h(10)
    bad_htf = independent_candle_replay_audit(
        candles_1h=candles_1h,
        candles_4h=candles_4h,
        signal_time=signal,
        entry_time=signal,
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=90.0,
        htf_refs={
            "require_htf": True,
            "future_candle_open": (signal + timedelta(hours=8)).isoformat(),
            "role": "future",
            "must_be_rejected": True,
        },
    )
    assert bad_htf["ok"] is False
    assert "future_htf_candle" in bad_htf["errors"]

    bad_bos = independent_candle_replay_audit(
        candles_1h=candles_1h,
        candles_4h=candles_4h,
        signal_time=signal,
        entry_time=signal,
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=90.0,
        structure_refs={"bos_candle_timestamp": (signal + timedelta(hours=2)).isoformat()},
        htf_refs={"require_htf": True},
    )
    assert bad_bos["ok"] is False
    assert any("future_structure" in e for e in bad_bos["errors"])

    bad_swing = independent_candle_replay_audit(
        candles_1h=candles_1h,
        candles_4h=candles_4h,
        signal_time=signal,
        entry_time=signal,
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=90.0,
        structure_refs={
            "swing_low_timestamps": [(signal + timedelta(hours=3)).isoformat()]
        },
        htf_refs={"require_htf": True},
    )
    assert bad_swing["ok"] is False
    assert "future_swing" in bad_swing["errors"]


def test_stop_tp_after_entry_and_exit_scan_rejected():
    signal = _ts(20)
    candles_1h = _series_1h(40)
    candles_4h = _series_4h(10)
    bad_stp = independent_candle_replay_audit(
        candles_1h=candles_1h,
        candles_4h=candles_4h,
        signal_time=signal,
        entry_time=signal,
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=90.0,
        structure_refs={
            "stop_tp_computed_at": (signal + timedelta(hours=1)).isoformat()
        },
        htf_refs={"require_htf": True},
    )
    assert bad_stp["ok"] is False
    assert "stop_tp_after_entry" in bad_stp["errors"]

    # No candles after entry → exit scan fails
    short_series = [_c(i) for i in range(21)]  # last open == entry
    bad_exit = independent_candle_replay_audit(
        candles_1h=short_series,
        candles_4h=candles_4h,
        signal_time=signal,
        entry_time=signal,
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=90.0,
        htf_refs={"require_htf": True},
    )
    assert bad_exit["ok"] is False
    assert "no_exit_scan_candle_after_entry" in bad_exit["errors"]


def test_limit_fill_before_order_rejected():
    signal = _ts(20)
    replay = independent_candle_replay_audit(
        candles_1h=_series_1h(40),
        candles_4h=_series_4h(10),
        signal_time=signal,
        entry_time=signal,
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=90.0,
        entry_type="LIMIT_RETEST",
        order_created_time=signal,
        first_eligible_fill_candle=signal - timedelta(hours=1),
        htf_refs={"require_htf": True},
    )
    assert replay["ok"] is False
    assert "retroactive_limit_fill" in replay["errors"]


def test_windows_disjoint_and_validation_only_oos_status():
    assert validate_research_windows(DEFAULT_SHORT_RESEARCH_WINDOWS)["ok"] is True
    review = build_prepaper_review(
        {
            "window_status": "OK",
            "research_windows": DEFAULT_SHORT_RESEARCH_WINDOWS.to_dict(),
            "oos": {
                "oos_status": "SHORT_RESEARCH_CANDIDATE",
                "determines_status": "oos_validation",
            },
            "oos_validation": {"trade_count": 40},
            "partition_counts": {
                "base_trades": 40,
                "oos_dev_trades": 10,
                "oos_val_trades": 40,
            },
            "forensic_lookahead": {
                "forensic_pass_for_promotion": True,
                "evidence_classes": [EVIDENCE_DIRECT],
                "audits": [{"evidence_class": EVIDENCE_DIRECT, "research_quality": "PASS"}],
            },
            "reconciliation": {
                "ok": True,
                "equity_reconciliation": "PASS",
                "fee_reconciliation": "PASS",
                "trade_order_reconciliation": "PASS",
            },
            "data_health": {
                "ok": True,
                "data_health_status": "HEALTHY",
                "requested_range_available": True,
            },
            "base_research": {"trade_count": 40},
            "research_quality": "SHORT_RESEARCH_CANDIDATE",
        }
    )
    assert review["determines_oos_status"] == "oos_validation"
    assert review["review_status"] == REVIEW_READY
    assert review["paper_eligible"] is False
    assert review["human_label"] == "Ready for separate human review"


def test_oos_sample_warning_not_overridable():
    w = oos_validation_sample_warning(12)
    assert w["passed"] is False
    assert w["overridable"] is False
    assert sample_class(12) == "SMALL"
    assert sample_class(0) == "NO_TRADES"
    assert sample_class(30) == "USABLE_FOR_REVIEW"
    blocked = build_prepaper_review(
        {
            "window_status": "OK",
            "research_windows": DEFAULT_SHORT_RESEARCH_WINDOWS.to_dict(),
            "oos": {"oos_status": "PROMISING", "determines_status": "oos_validation"},
            "oos_validation": {"trade_count": 12},
            "partition_counts": {"base_trades": 40, "oos_dev_trades": 5, "oos_val_trades": 12},
            "forensic_lookahead": {
                "forensic_pass_for_promotion": True,
                "evidence_classes": [EVIDENCE_DIRECT],
            },
            "reconciliation": {"ok": True},
            "data_health": {"requested_range_available": True},
            "research_quality": "PROMISING_BUT_LOW_SAMPLE",
        },
        operator_confirm_override=True,
    )
    assert blocked["operator_override_honored"] is False
    assert any("oos_validation" in str(b) for b in blocked["blockers"])
    # Sample < 30 is a hard gate → REVIEW_BLOCKED (not overridable)
    assert blocked["review_status"] == REVIEW_BLOCKED
    assert blocked["paper_eligible"] is False


def test_metric_reconciliation_pass_and_fail_closed():
    good = [
        {"net_pnl": 10.0, "gross_pnl": 12.0, "fees": 2.0, "entry_time": "2025-02-01T00:00:00+00:00"},
        {"net_pnl": -4.0, "gross_pnl": -3.0, "fees": 1.0, "entry_time": "2025-02-02T00:00:00+00:00"},
    ]
    ok = reconcile_research_blotter(
        good, starting_equity=1000.0, reported_net_pnl=6.0, reported_fees=3.0
    )
    assert ok["equity_reconciliation"] == "PASS"
    assert ok["fee_reconciliation"] == "PASS"
    assert ok["trade_order_reconciliation"] == "PASS"
    assert ok["ok"] is True

    bad = reconcile_research_blotter(
        good, starting_equity=1000.0, reported_net_pnl=999.0, reported_fees=3.0
    )
    assert bad["ok"] is False
    assert bad["equity_reconciliation"] == "PASS"
    review = build_prepaper_review(
        {
            "window_status": "OK",
            "oos": {"determines_status": "oos_validation"},
            "oos_validation": {"trade_count": 40},
            "partition_counts": {"base_trades": 40, "oos_dev_trades": 10, "oos_val_trades": 40},
            "forensic_lookahead": {
                "forensic_pass_for_promotion": True,
                "evidence_classes": [EVIDENCE_DIRECT],
            },
            "reconciliation": bad,
            "data_health": {"requested_range_available": True},
            "research_quality": "REVIEW_REQUIRED",
        }
    )
    assert review["review_status"] == REVIEW_BLOCKED


def test_range_mismatch_and_hash_stability():
    health = {
        "requested_range_available": False,
        "requested_start": "2024-01-01",
        "coverage_start": "2025-01-01",
    }
    review = build_prepaper_review(
        {
            "window_status": "OK",
            "oos": {"determines_status": "oos_validation"},
            "oos_validation": {"trade_count": 40},
            "partition_counts": {"base_trades": 40, "oos_dev_trades": 10, "oos_val_trades": 40},
            "forensic_lookahead": {
                "forensic_pass_for_promotion": True,
                "evidence_classes": [EVIDENCE_DIRECT],
            },
            "reconciliation": {"ok": True},
            "data_health": health,
            "research_quality": "REVIEW_REQUIRED",
        }
    )
    assert "requested_range_available" in review["blockers"] or (
        "requested range unavailable" in review["blockers"]
    )
    assert review["review_status"] == REVIEW_BLOCKED

    a = [_c(i) for i in range(5)]
    b = [dict(x) for x in a]
    assert dataset_fingerprint({"1h": a}) == dataset_fingerprint({"1h": b})
    b[-1]["close"] = 999.0
    assert dataset_fingerprint({"1h": a}) != dataset_fingerprint({"1h": b})
    h1 = configuration_fingerprint(window={"base_start": "2025-01-01"}, symbols=["BTCUSDT"])
    h2 = configuration_fingerprint(window={"base_start": "2025-01-01"}, symbols=["BTCUSDT"])
    assert h1 == h2


def _healthy(symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "ok": True,
        "health_status": "OK",
        "data_health_status": "HEALTHY",
        "requested_range_available": True,
        "requested_start": "2025-01-01",
        "requested_end": "2026-01-31",
        "coverage_start": "2025-01-01T00:00:00+00:00",
        "coverage_end": "2026-01-31T00:00:00+00:00",
        "bars_used": 500,
        "bars_4h": 200,
        "completeness": 1.0,
        "dataset_hash": f"ds-{symbol}",
    }


async def _bt(service, symbol, *, start, end, window, **_k):
    prefix = "2025-08" if str(start).startswith("2025-07") else (
        "2025-05" if str(start).startswith("2025-05") else "2025-02"
    )
    n = 12 if prefix.startswith("2025-08") else 25
    trades = []
    for i in range(3):
        entry = f"{prefix}-{i+1:02d}T00:00:00+00:00"
        trades.append(
            {
                "signal_time": entry,
                "entry_time": entry,
                "exit_time": f"{prefix}-{i+1:02d}T06:00:00+00:00",
                "entry_price": 100.0,
                "stop_price": 105.0,
                "take_profit_price": 90.0,
                "holding_bars": 6,
                "direction": "SHORT",
                "net_pnl": 8.0,
                "gross_pnl": 9.0,
                "fees": 1.0,
                "r_net": 0.3,
            }
        )
    return {
        "run_status": "COMPLETED",
        "trade_count": n,
        "wins": n // 2,
        "losses": n // 2,
        "win_rate": 0.5,
        "gross_pnl": 27.0,
        "net_pnl": 24.0,
        "total_fees": 3.0,
        "gross_avg_r": 0.35,
        "net_avg_r": 0.3,
        "profit_factor": 1.5,
        "max_drawdown_r": 2.0,
        "max_losing_streak": 2,
        "fees_over_gross_pnl": 0.1,
        "closed_trades": trades,
        "direction": "SHORT",
        "strategy_id": STRATEGY_ID,
    }


@pytest.mark.asyncio
async def test_runner_forces_research_only_identity_and_review_blocked():
    report = await run_short_research_batch(
        symbols=["BTCUSDT"],
        run_id="prepaper-1",
        health_fn=_healthy,
        backtest_fn=_bt,
        service=object(),
        persist=False,
    )
    c = report["candidates"][0]
    assert c["strategy_id"] == STRATEGY_ID
    assert c["direction"] == "SHORT"
    assert c["paper_eligible"] is False
    assert c["production_approved"] is False
    assert c["telegram_eligible"] is False
    assert c["prepaper_review"]["review_status"] == REVIEW_BLOCKED
    assert c["prepaper_review"]["paper_eligible"] is False
    assert c["research_quality"] == "REVIEW_REQUIRED"
    assert any(
        w["rule"] == "oos_validation_sample_size" for w in (c.get("quality_warnings") or [])
    )


@pytest.mark.asyncio
async def test_no_paper_live_telegram_invoked():
    open_mock = MagicMock()
    v1 = AsyncMock()
    v2 = AsyncMock()
    with (
        patch("app.services.v1_paper_watcher.V1PaperWatcher.on_closed_1h", v1),
        patch(
            "app.services.v2_candidate_paper_watcher.V2CandidatePaperWatcher.on_closed_1h",
            v2,
        ),
        patch("app.services.paper_trade.PaperTradeEngine.on_setup_signal", open_mock),
        patch(
            "app.services.paper_trade.PaperTradeEngine.open_v1_combo_position", open_mock
        ),
    ):
        await run_short_research_batch(
            symbols=["ETHUSDT"],
            run_id="prepaper-iso",
            health_fn=_healthy,
            backtest_fn=_bt,
            service=object(),
            persist=False,
        )
    v1.assert_not_called()
    v2.assert_not_called()
    open_mock.assert_not_called()
