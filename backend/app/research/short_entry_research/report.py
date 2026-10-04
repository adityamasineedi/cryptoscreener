"""Comparison table and final operator report for SHORT entry research."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.short_entry_research.constants import (
    PARENT_RUN_ID,
    PARENT_STRATEGY_ID,
    SAFETY_STAMPS,
    STRATEGY_ID,
)


def comparison_rows(variant_results: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    order = ["baseline", "retest", "extension_filter", "chasing_exclusion"]
    rows = []
    for vid in order:
        v = variant_results.get(vid) or {}
        base = v.get("base") or {}
        oos = v.get("oos_status") or {}
        rows.append(
            {
                "Variant": v.get("label") or vid,
                "variant_id": vid,
                "Trades": base.get("trade_count"),
                "Win rate": base.get("win_rate"),
                "Gross PnL": base.get("gross_pnl"),
                "Fees": base.get("fees"),
                "Net PnL": base.get("net_pnl"),
                "Avg net R": base.get("average_net_r"),
                "PF": base.get("profit_factor"),
                "Max DD": base.get("maximum_drawdown"),
                "OOS status": oos.get("oos_status"),
                "Review": oos.get("review_status"),
            }
        )
    return rows


def render_comparison_table(rows: Sequence[Mapping[str, Any]]) -> str:
    headers = [
        "Variant",
        "Trades",
        "Win rate",
        "Gross PnL",
        "Fees",
        "Net PnL",
        "Avg net R",
        "PF",
        "Max DD",
        "OOS status",
        "Review",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" if h == "Variant" or h in ("OOS status", "Review") else "---:" for h in headers) + " |",
    ]
    for r in rows:
        def fmt(key: str) -> str:
            val = r.get(key)
            if val is None:
                return ""
            if isinstance(val, float):
                if key in ("Win rate",):
                    return f"{val:.3f}"
                if key in ("Avg net R", "PF", "Max DD"):
                    return f"{val:.3f}"
                return f"{val:.2f}"
            return str(val)

        lines.append("| " + " | ".join(fmt(h) for h in headers) + " |")
    return "\n".join(lines)


def build_final_report(
    *,
    run_id: str,
    variant_run_ids: Mapping[str, str],
    variant_results: Mapping[str, Mapping[str, Any]],
    retest_status: Mapping[str, Any],
    entry_counts: Mapping[str, int],
    stop_counts: Mapping[str, int],
    tp_findings: Mapping[str, Any],
    tests: Mapping[str, Any],
    best_next: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    table_rows = comparison_rows(variant_results)
    table_md = render_comparison_table(table_rows)
    oos_by_variant = {
        k: (v.get("oos_status") or {}).get("oos_status")
        for k, v in variant_results.items()
    }
    classifications = sorted(
        {
            str((v.get("oos_status") or {}).get("research_classification") or "RESEARCH_REJECTED")
            for v in variant_results.values()
        }
    )
    retest_fills = int((variant_results.get("retest") or {}).get("base", {}).get("trade_count") or 0)
    retest_impl = str(retest_status.get("status") or "UNKNOWN")

    best = best_next or {
        "variant_id": None,
        "note": "No variant cleared untouched OOS validation.",
    }

    text = f"""
Diagnostic phase: SHORT entry-timing / path-geometry research
Parent run: {PARENT_RUN_ID} ({PARENT_STRATEGY_ID})
Variant run IDs: {dict(variant_run_ids)}
Retest implementation status: {retest_impl}
Retest fills: {retest_fills}
Entry classification results: {dict(entry_counts)}
Stop findings: {dict(stop_counts)}
TP findings: {tp_findings}
Variant comparison:
{table_md}
OOS status by variant: {oos_by_variant}
Research classification: {classifications}
Paper status: DISABLED
Production status: DISABLED
Telegram status: DISABLED
Tests added: {tests.get("added")}
Tests executed: {tests.get("executed")}
Test result: {tests.get("result")}
LONG regression: {tests.get("long_regression")}
V1 regression: {tests.get("v1_regression")}
Remaining uncertainties: {tests.get("uncertainties")}
Best next research candidate: {best}

Whether retest fills work correctly: {retest_status.get("fills_work_correctly")}
Whether retest entry improves expectancy out of sample: {retest_status.get("retest_improves_oos")}
Whether entry filters improve expectancy out of sample: {retest_status.get("entry_filters_improve_oos")}
Whether stop variants improve results out of sample: {retest_status.get("stop_variants_improve_oos")}
Whether TP variants are supported by MFE and OOS evidence: {retest_status.get("tp_supported_by_oos")}
No SHORT paper or live trading was enabled.
COMBO_02 v1 remains LONG-only.
BTC/ETH/SOL v1 behavior remains unchanged.
""".strip()

    return {
        **SAFETY_STAMPS,
        "strategy_id": STRATEGY_ID,
        "diagnostic_phase": "SHORT_ENTRY_TIMING_PATH_GEOMETRY",
        "parent_run_id": PARENT_RUN_ID,
        "run_id": run_id,
        "variant_run_ids": dict(variant_run_ids),
        "retest_implementation_status": retest_status,
        "retest_fills": retest_fills,
        "entry_classification_results": dict(entry_counts),
        "stop_findings": dict(stop_counts),
        "tp_findings": tp_findings,
        "variant_comparison": table_rows,
        "variant_comparison_markdown": table_md,
        "oos_status_by_variant": oos_by_variant,
        "research_classification": classifications,
        "variant_results": {
            k: {
                kk: vv
                for kk, vv in v.items()
                if kk not in ("base_trades", "oos_trades", "closed_trades")
            }
            for k, v in variant_results.items()
        },
        "paper_status": "DISABLED",
        "production_status": "DISABLED",
        "telegram_status": "DISABLED",
        "tests": dict(tests),
        "best_next_research_candidate": best,
        "operator_text": text,
        "explicit_answers": {
            "retest_fills_work_correctly": retest_status.get("fills_work_correctly"),
            "retest_improves_oos": retest_status.get("retest_improves_oos"),
            "entry_filters_improve_oos": retest_status.get("entry_filters_improve_oos"),
            "stop_variants_improve_oos": retest_status.get("stop_variants_improve_oos"),
            "tp_supported_by_oos": retest_status.get("tp_supported_by_oos"),
            "short_paper_live_enabled": False,
            "combo02_v1_long_only": True,
            "btc_eth_sol_v1_unchanged": True,
        },
    }
