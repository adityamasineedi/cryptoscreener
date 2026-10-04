"""Report builders for SHORT pullback-rejection research."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.short_pullback_rejection.constants import (
    DISCLAIMER,
    SAFETY_STAMPS,
    STRATEGY_FINGERPRINT_TEXT,
    STRATEGY_ID,
)


def build_operator_text(report: Mapping[str, Any]) -> str:
    lines = [
        "Research phase: SHORT pullback-rejection strategy",
        f"Strategy: {report.get('strategy_id')}",
        f"Combo version: {report.get('combo_version')}",
        f"Source: {report.get('source')}",
        f"Run ID: {report.get('run_id')}",
        f"Hypothesis: {report.get('hypothesis')}",
        "",
        f"Signals generated: {report.get('signals_generated')}",
        f"Trades by rejection type: {report.get('trades_by_rejection_type_counts')}",
        "",
        f"Base metrics: {report.get('base_metrics')}",
        f"OOS development metrics: {report.get('oos_dev_metrics')}",
        f"OOS validation metrics: {report.get('oos_val_metrics')}",
        "",
        f"Stop variant results: {list((report.get('stop_variant_results') or {}).keys())}",
        f"TP variant results: {list((report.get('tp_variant_results') or {}).keys())}",
        f"Regime results: {list((report.get('regime_results') or {}).keys())}",
        "",
        f"DIRECT_CANDLE_REPLAY status: {report.get('direct_candle_replay_status')}",
        f"Reconciliation status: {report.get('reconciliation_status')}",
        f"Research classification: {report.get('research_classification')}",
        f"Paper status: DISABLED (paper_eligible=false)",
        f"Production status: DISABLED (production_approved=false)",
        f"Telegram status: DISABLED (telegram_eligible=false)",
        "",
        DISCLAIMER,
        "",
        "Confirmations:",
        "- This is a new strategy, not a modification of COMBO_02 v1.",
        "- Previous SHORT results remain unchanged.",
        "- No SHORT paper or live trading was enabled.",
        "- No production approval was granted.",
        "- No Telegram alerts were enabled.",
        "- COMBO_02 v1 remains LONG-only.",
        "- BTC/ETH/SOL v1 behavior remains unchanged.",
    ]
    return "\n".join(lines)


def build_final_report(
    *,
    run_id: str,
    configuration_hash: str,
    dataset_hash: str,
    strategy_fingerprint: str,
    windows: Mapping[str, Any],
    signals: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
    base_metrics: Mapping[str, Any],
    oos_dev_metrics: Mapping[str, Any],
    oos_val_metrics: Mapping[str, Any],
    trades_by_rejection_type: Mapping[str, Any],
    trades_by_rejection_type_counts: Mapping[str, int],
    regime_results: Mapping[str, Any],
    stop_variant_results: Mapping[str, Any],
    tp_variant_results: Mapping[str, Any],
    support_distance_distribution: Mapping[str, Any],
    entry_extension_distribution: Mapping[str, Any],
    classification: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    direct_replay: Mapping[str, Any],
    tests_note: str | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        **SAFETY_STAMPS,
        "strategy_id": STRATEGY_ID,
        "run_id": run_id,
        "configuration_hash": configuration_hash,
        "dataset_hash": dataset_hash,
        "strategy_fingerprint": strategy_fingerprint or STRATEGY_FINGERPRINT_TEXT,
        "hypothesis": (
            "Bearish 4h LH/LL + 1h pullback into broken S/R + explicit rejection "
            "confirmation yields positive expectancy vs failed BOS market-entry SHORT."
        ),
        "windows": dict(windows),
        "signals_generated": len(signals),
        "trade_count": len(trades),
        "trades_by_rejection_type_counts": dict(trades_by_rejection_type_counts),
        "trades_by_rejection_type": {
            k: {kk: vv for kk, vv in v.items() if kk != "closed_trades"}
            for k, v in trades_by_rejection_type.items()
        },
        "base_metrics": {k: v for k, v in base_metrics.items() if k != "closed_trades"},
        "oos_dev_metrics": {
            k: v for k, v in oos_dev_metrics.items() if k != "closed_trades"
        },
        "oos_val_metrics": {
            k: v for k, v in oos_val_metrics.items() if k != "closed_trades"
        },
        "regime_results": {
            k: {kk: vv for kk, vv in v.items() if kk != "closed_trades"}
            for k, v in regime_results.items()
        },
        "stop_variant_results": stop_variant_results,
        "tp_variant_results": tp_variant_results,
        "support_distance_distribution": support_distance_distribution,
        "entry_extension_distribution": entry_extension_distribution,
        "direct_candle_replay_status": (
            "PASS" if direct_replay.get("direct_candle_replay") else "FAIL"
        ),
        "reconciliation_status": {
            "equity": reconciliation.get("equity_reconciliation"),
            "fee": reconciliation.get("fee_reconciliation"),
            "trade_order": reconciliation.get("trade_order_reconciliation"),
            "ok": reconciliation.get("ok"),
        },
        "research_classification": classification.get("research_classification"),
        "oos_status": classification.get("oos_status"),
        "classification": dict(classification),
        "paper_status": "DISABLED",
        "production_status": "DISABLED",
        "telegram_status": "DISABLED",
        "disclaimer": DISCLAIMER,
        "confirmations": {
            "new_strategy_not_combo02_v1": True,
            "previous_short_results_unchanged": True,
            "short_paper_live_disabled": True,
            "no_production_approval": True,
            "no_telegram": True,
            "combo02_v1_long_only": True,
            "btc_eth_sol_v1_unchanged": True,
        },
        "tests_note": tests_note,
    }
    report["operator_text"] = build_operator_text(report)
    return report
