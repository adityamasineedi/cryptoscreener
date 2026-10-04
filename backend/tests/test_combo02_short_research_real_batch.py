"""Real-symbol SHORT research batch: hard gates, all symbols, no paper."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.research.combo02_short_research_runner import (
    api_list_response,
    run_short_research_batch,
    short_research_report_store,
)
from app.research.combo02_short_research import short_research_registry
from app.research.short_research_constants import STRATEGY_ID, TERMINAL_PASS_STATE
from app.research.short_research_forensics import (
    EVIDENCE_DERIVED,
    EVIDENCE_DIRECT,
    audit_trades_for_lookahead,
    build_trade_forensic_audit,
    enrich_trade_forensic_fields,
)
from app.research.short_research_hard_gates import (
    build_batch_summary,
    evaluate_hard_acceptance_gates,
)
from app.research.short_research_windows import (
    DEFAULT_SHORT_RESEARCH_WINDOWS,
    ResearchWindows,
    validate_research_windows,
)


def _healthy(symbol: str, *, available: bool = True) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "ok": available,
        "health_status": "OK" if available else "BLOCKED",
        "data_health_status": "HEALTHY" if available else "EMPTY",
        "requested_range_available": available,
        "requested_start": "2025-01-01",
        "requested_end": "2026-01-31",
        "coverage_start": "2025-01-01T00:00:00+00:00" if available else None,
        "coverage_end": "2026-01-31T00:00:00+00:00" if available else None,
        "bars_used": 500 if available else 0,
        "bars_4h": 200 if available else 0,
        "completeness": 1.0 if available else 0.0,
        "dataset_hash": f"ds-{symbol}",
    }


def _trade(entry: str) -> dict[str, Any]:
    return {
        "signal_time": entry,
        "entry_time": entry,
        "exit_time": entry.replace("T00:", "T06:"),
        "entry_price": 100.0,
        "stop_price": 105.0,
        "take_profit_price": 90.0,
        "holding_bars": 6,
        "direction": "SHORT",
        "net_pnl": 1.0,
        "gross_pnl": 1.2,
        "fees": -0.2,
        "fee_sign": "NEGATIVE_COST",
        "r_net": 0.3,
    }


async def _bt(service, symbol, *, start, end, window, **_k):
    if str(start).startswith("2025-07"):
        prefix, n = "2025-08", 12
    elif str(start).startswith("2025-05"):
        prefix, n = "2025-05", 8
    else:
        prefix, n = "2025-02", 25
    trades = [_trade(f"{prefix}-{i+1:02d}T00:00:00+00:00") for i in range(min(n, 3))]
    return {
        "run_status": "COMPLETED",
        "trade_count": n,
        "wins": n // 2,
        "losses": n // 2,
        "win_rate": 0.5,
        "gross_pnl": sum(t["gross_pnl"] for t in trades),
        "net_pnl": sum(t["net_pnl"] for t in trades),
        "total_fees": sum(t["fees"] for t in trades),
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


@pytest.fixture(autouse=True)
def _clean():
    short_research_report_store.clear()
    short_research_registry._rows.clear()
    yield
    short_research_report_store.clear()
    short_research_registry._rows.clear()


@pytest.mark.asyncio
async def test_batch_includes_every_configured_symbol_including_data_failures():
    symbols = ["BTCUSDT", "ETHUSDT", "FAILUSDT"]

    def health(sym: str):
        return _healthy(sym, available=sym != "FAILUSDT")

    report = await run_short_research_batch(
        symbols=symbols,
        run_id="real-all",
        health_fn=health,
        backtest_fn=_bt,
        service=object(),
        persist=False,
        evidence_mode="DIRECT_CANDLE_REPLAY",
    )
    got = {c["symbol"] for c in report["candidates"]}
    assert got == set(symbols)
    summary = report["batch_summary"]
    assert summary["total_symbols"] == 3
    assert summary["data_failures"] >= 1
    assert len(summary["reports"]) == 3
    fail = next(r for r in summary["reports"] if r["symbol"] == "FAILUSDT")
    assert fail["review_status"] == "REVIEW_BLOCKED"
    assert fail["paper_eligible"] is False


def test_requested_range_mismatch_visible_in_hard_gates():
    cand = {
        "data_health": {
            "ok": True,
            "data_health_status": "HEALTHY",
            "requested_range_available": False,
        },
        "window_status": "OK",
        "oos": {
            "oos_status": TERMINAL_PASS_STATE,
            "determines_status": "oos_validation",
        },
        "oos_validation": {"trade_count": 40},
        "partition_counts": {"base_trades": 40, "oos_val_trades": 40},
        "forensic_lookahead": {
            "forensic_pass_for_promotion": True,
            "evidence_classes": [EVIDENCE_DIRECT],
            "audits": [{"evidence_class": EVIDENCE_DIRECT}],
        },
        "reconciliation": {
            "ok": True,
            "equity_reconciliation": "PASS",
            "fee_reconciliation": "PASS",
            "trade_order_reconciliation": "PASS",
        },
        "base_research": {"trade_count": 40},
    }
    hard = evaluate_hard_acceptance_gates(cand)
    assert hard["gates"]["requested_range_available"] is False
    assert hard["passed"] is False


def test_derived_evidence_cannot_satisfy_hard_gate():
    t = enrich_trade_forensic_fields(
        {
            "signal_time": "2025-02-01T00:00:00+00:00",
            "entry_time": "2025-02-01T00:00:00+00:00",
            "exit_time": "2025-02-01T06:00:00+00:00",
            "entry_price": 100.0,
            "stop_price": 105.0,
            "take_profit_price": 90.0,
            "holding_bars": 2,
            "direction": "SHORT",
        }
    )
    bundle = audit_trades_for_lookahead([t])
    cand = {
        "data_health": {
            "ok": True,
            "data_health_status": "HEALTHY",
            "requested_range_available": True,
        },
        "window_status": "OK",
        "oos": {
            "oos_status": TERMINAL_PASS_STATE,
            "determines_status": "oos_validation",
        },
        "oos_validation": {"trade_count": 40},
        "partition_counts": {"base_trades": 40, "oos_val_trades": 40},
        "forensic_lookahead": bundle,
        "reconciliation": {
            "ok": True,
            "equity_reconciliation": "PASS",
            "fee_reconciliation": "PASS",
            "trade_order_reconciliation": "PASS",
        },
        "base_research": {"trade_count": 40},
        "trade_forensic_audits": bundle["audits"],
    }
    hard = evaluate_hard_acceptance_gates(cand)
    assert hard["gates"]["direct_candle_replay"] is False
    assert EVIDENCE_DERIVED in (bundle.get("evidence_classes") or [])


def test_oos_validation_29_blocked_30_passes_sample_gate():
    base = {
        "data_health": {
            "ok": True,
            "data_health_status": "HEALTHY",
            "requested_range_available": True,
        },
        "window_status": "OK",
        "oos": {
            "oos_status": TERMINAL_PASS_STATE,
            "determines_status": "oos_validation",
        },
        "forensic_lookahead": {
            "forensic_pass_for_promotion": True,
            "evidence_classes": [EVIDENCE_DIRECT],
            "audits": [{"evidence_class": EVIDENCE_DIRECT}],
        },
        "reconciliation": {
            "ok": True,
            "equity_reconciliation": "PASS",
            "fee_reconciliation": "PASS",
            "trade_order_reconciliation": "PASS",
        },
        "base_research": {"trade_count": 40},
        "partition_counts": {"base_trades": 40},
    }
    c29 = {
        **base,
        "oos_validation": {"trade_count": 29},
        "partition_counts": {**base["partition_counts"], "oos_val_trades": 29},
    }
    c30 = {
        **base,
        "oos_validation": {"trade_count": 30},
        "partition_counts": {**base["partition_counts"], "oos_val_trades": 30},
    }
    assert evaluate_hard_acceptance_gates(c29)["gates"]["oos_validation_sample_ge_30"] is False
    assert evaluate_hard_acceptance_gates(c29)["passed"] is False
    assert evaluate_hard_acceptance_gates(c30)["gates"]["oos_validation_sample_ge_30"] is True
    assert evaluate_hard_acceptance_gates(c30)["passed"] is True


def test_windows_disjoint_and_validation_only_oos():
    assert validate_research_windows(DEFAULT_SHORT_RESEARCH_WINDOWS)["ok"] is True
    overlapping = ResearchWindows(
        requested_start="2025-01-01",
        requested_end="2026-01-31",
        base_start="2025-01-01",
        base_end="2025-08-01",
        oos_dev_start="2025-05-01",
        oos_dev_end="2025-06-30",
        oos_val_start="2025-07-01",
        oos_val_end="2026-01-31",
    )
    assert validate_research_windows(overlapping)["ok"] is False

    cand = {
        "data_health": {
            "ok": True,
            "data_health_status": "HEALTHY",
            "requested_range_available": True,
        },
        "window_status": "OK",
        "oos": {"oos_status": TERMINAL_PASS_STATE, "determines_status": "combined"},
        "oos_validation": {"trade_count": 40},
        "partition_counts": {"base_trades": 40, "oos_val_trades": 40},
        "forensic_lookahead": {
            "forensic_pass_for_promotion": True,
            "evidence_classes": [EVIDENCE_DIRECT],
            "audits": [{"evidence_class": EVIDENCE_DIRECT}],
        },
        "reconciliation": {
            "ok": True,
            "equity_reconciliation": "PASS",
            "fee_reconciliation": "PASS",
            "trade_order_reconciliation": "PASS",
        },
        "base_research": {"trade_count": 40},
    }
    hard = evaluate_hard_acceptance_gates(cand)
    assert hard["gates"]["oos_validation_pass"] is False


def test_failed_reconciliation_blocks_review():
    cand = {
        "data_health": {
            "ok": True,
            "data_health_status": "HEALTHY",
            "requested_range_available": True,
        },
        "window_status": "OK",
        "oos": {
            "oos_status": TERMINAL_PASS_STATE,
            "determines_status": "oos_validation",
        },
        "oos_validation": {"trade_count": 40},
        "partition_counts": {"base_trades": 40, "oos_val_trades": 40},
        "forensic_lookahead": {
            "forensic_pass_for_promotion": True,
            "evidence_classes": [EVIDENCE_DIRECT],
            "audits": [{"evidence_class": EVIDENCE_DIRECT}],
        },
        "reconciliation": {
            "ok": False,
            "equity_reconciliation": "FAIL",
            "fee_reconciliation": "PASS",
            "trade_order_reconciliation": "PASS",
        },
        "base_research": {"trade_count": 40},
    }
    hard = evaluate_hard_acceptance_gates(cand)
    assert hard["passed"] is False
    assert hard["gates"]["equity_reconciliation"] is False


def test_direct_evidence_persisted_on_audit_and_not_from_derived_only():
    direct = {
        "signal_time": "2025-02-01T10:00:00+00:00",
        "entry_time": "2025-02-01T10:00:00+00:00",
        "exit_time": "2025-02-01T12:00:00+00:00",
        "entry_price": 100.0,
        "stop_price": 105.0,
        "take_profit_price": 90.0,
        "trend_data_end_time": "2025-02-01T09:00:00+00:00",
        "bos_data_end_time": "2025-02-01T09:00:00+00:00",
        "htf_data_end_time": "2025-02-01T08:00:00+00:00",
        "entry_data_end_time": "2025-02-01T10:00:00+00:00",
        "stop_tp_data_end_time": "2025-02-01T10:00:00+00:00",
        "exit_scan_start_time": "2025-02-01T11:00:00+00:00",
        "htf_candle_closed_before_signal": True,
        "forensic_evidence_class": EVIDENCE_DIRECT,
        "direction": "SHORT",
    }
    audit = build_trade_forensic_audit(direct)
    assert audit["evidence_class"] == EVIDENCE_DIRECT
    assert audit["research_quality"] == "PASS"
    # Derived enrichment must not claim DIRECT
    derived = enrich_trade_forensic_fields(
        {k: v for k, v in direct.items() if k != "forensic_evidence_class"}
    )
    # enrich overwrites non-DIRECT to DERIVED
    assert derived["forensic_evidence_class"] == EVIDENCE_DERIVED


def test_batch_summary_counts_blocked():
    summary = build_batch_summary(
        {
            "run_id": "s1",
            "strategy_id": STRATEGY_ID,
            "universe": ["AUSDT", "BUSDT"],
            "evidence_mode": "DIRECT_CANDLE_REPLAY",
            "candidates": [
                {
                    "symbol": "AUSDT",
                    "data_health": {"ok": True, "data_health_status": "HEALTHY"},
                    "prepaper_review": {
                        "review_status": "REVIEW_READY_FOR_SEPARATE_APPROVAL"
                    },
                    "oos_validation": {"trade_count": 40},
                    "forensic_lookahead": {"forensic_pass_for_promotion": True},
                    "partition_counts": {"oos_val_trades": 40},
                },
                {
                    "symbol": "BUSDT",
                    "data_health": {"ok": False, "data_health_status": "EMPTY"},
                    "prepaper_review": {"review_status": "REVIEW_BLOCKED"},
                    "oos_validation": {"trade_count": 0},
                    "forensic_lookahead": {"forensic_pass_for_promotion": False},
                    "partition_counts": {"oos_val_trades": 0},
                },
            ],
        }
    )
    assert summary["review_ready"] == 1
    assert summary["review_blocked"] == 1
    assert summary["data_failures"] == 1
    assert summary["paper_eligible"] == 0
    assert summary["production_approved"] == 0
    assert summary["telegram_eligible"] == 0


@pytest.mark.asyncio
async def test_api_returns_research_only_identity_and_batch_summary():
    await run_short_research_batch(
        symbols=["BTCUSDT", "ETHUSDT"],
        run_id="api-real",
        health_fn=_healthy,
        backtest_fn=_bt,
        service=object(),
        persist=False,
    )
    out = api_list_response()
    assert out["direction"] == "SHORT"
    assert out["paper_eligible"] is False
    assert out["production_approved"] is False
    assert out["telegram_eligible"] is False
    assert out["batch_summary"]
    assert out["batch_summary"]["total_symbols"] == 2
    for c in out["candidates"]:
        assert c["paper_eligible"] is False
        assert c["strategy_id"] == STRATEGY_ID


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
        report = await run_short_research_batch(
            symbols=["SOLUSDT"],
            run_id="iso-real",
            health_fn=_healthy,
            backtest_fn=_bt,
            service=object(),
            persist=False,
        )
    v1.assert_not_called()
    v2.assert_not_called()
    open_mock.assert_not_called()
    assert report["batch_summary"]["paper_eligible"] == 0
