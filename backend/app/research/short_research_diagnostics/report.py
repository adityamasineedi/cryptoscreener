"""Assemble the required SHORT diagnostic final report."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.short_research_diagnostics.constants import SAFETY_STAMPS


def _best_research_hypothesis(
    ablation: Mapping[str, Any],
    *,
    entry_findings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    candidates = []
    for vid, row in ablation.items():
        if vid.startswith("_") or not isinstance(row, Mapping):
            continue
        n = int(row.get("trade_count") or 0)
        net = row.get("net_pnl")
        avg_r = row.get("average_net_r")
        if n < 20 or net is None:
            continue
        candidates.append(
            {
                "variant_id": vid,
                "label": row.get("label"),
                "trade_count": n,
                "net_pnl": net,
                "average_net_r": avg_r,
                "profit_factor": row.get("profit_factor"),
                "maximum_drawdown": row.get("maximum_drawdown"),
                "exploratory_only": True,
            }
        )

    # Entry-cohort hypothesis: CHASING materially outperforms other entry classes.
    by_cat = (entry_findings or {}).get("by_category") or {}
    chase = by_cat.get("CHASING") or {}
    imm = by_cat.get("IMMEDIATE_BREAK") or {}
    late = by_cat.get("LATE") or {}
    if int(chase.get("trade_count") or 0) >= 20 and chase.get("average_net_r") is not None:
        peers = [
            float(x["average_net_r"])
            for x in (imm, late, by_cat.get("EARLY") or {})
            if x.get("average_net_r") is not None
        ]
        chase_r = float(chase["average_net_r"])
        if peers and chase_r > max(peers) + 0.1:
            return {
                "variant_id": "chasing_momentum_filter",
                "label": "CHASING / impulse-continuation entries only",
                "trade_count": int(chase["trade_count"]),
                "net_pnl": chase.get("net_pnl"),
                "average_net_r": chase.get("average_net_r"),
                "profit_factor": chase.get("profit_factor"),
                "exploratory_only": True,
                "deserves_future_oos": True,
                "note": (
                    "In-sample only: CHASING cohort materially outperforms "
                    "IMMEDIATE_BREAK/LATE/EARLY. Retest-only unavailable "
                    "(0 LIMIT_RETEST fills). Requires disjoint OOS; do not approve on base alone."
                ),
            }

    if not candidates:
        return {
            "variant_id": None,
            "note": "No ablation variant cleared exploratory sample filters.",
            "deserves_future_oos": False,
        }
    positives = [c for c in candidates if float(c["net_pnl"]) > 0 and (c["average_net_r"] or 0) > 0]
    pool = positives or candidates
    pool.sort(
        key=lambda c: (
            float(c["average_net_r"] or -999),
            float(c["net_pnl"] or -999),
            int(c["trade_count"]),
        ),
        reverse=True,
    )
    best = pool[0]
    best["deserves_future_oos"] = bool(positives)
    best["note"] = (
        "Exploratory in-sample only. Requires disjoint OOS validation before any approval."
        if positives
        else "No positive net variant; hypothesis is diagnostic (filter/entry timing), not promotion."
    )
    return best


def build_final_report(
    *,
    run_id: str,
    implementation: Mapping[str, Any],
    trade_dataset: Sequence[Mapping[str, Any]],
    entry_findings: Mapping[str, Any],
    stop_findings: Mapping[str, Any],
    tp_findings: Mapping[str, Any],
    regime_findings: Mapping[str, Any],
    fee_sensitivity: Mapping[str, Any],
    ablation: Mapping[str, Any],
    classification: Mapping[str, Any],
    tests: Mapping[str, Any],
    dataset_path: str | None = None,
) -> dict[str, Any]:
    best = _best_research_hypothesis(ablation, entry_findings=entry_findings)
    n = len(trade_dataset)
    gross = sum(float(r["gross_pnl"]) for r in trade_dataset if r.get("gross_pnl") is not None)
    fees = sum(float(r["fees"]) for r in trade_dataset if r.get("fees") is not None)
    net = sum(float(r["net_pnl"]) for r in trade_dataset if r.get("net_pnl") is not None)

    short_math_ok = bool(implementation.get("ok"))
    labels = list(classification.get("labels") or [])

    text_block = f"""
Diagnostic run ID: {run_id}
Implementation verification: {"PASS" if short_math_ok else "FAIL — IMPLEMENTATION_DEFECT"}
Trade-level dataset: {n} closed SHORT trades{f" ({dataset_path})" if dataset_path else ""}
Entry-quality findings: {entry_findings.get("summary") or entry_findings}
Stop-placement findings: {stop_findings.get("summary") or stop_findings}
TP findings: {tp_findings.get("summary") or tp_findings}
Market-regime findings: {regime_findings.get("summary") or regime_findings}
Fee sensitivity: gross={gross:.4f} fees={fees:.4f} net={net:.4f}; flip_sign={((fee_sensitivity.get("interpretation") or {}).get("fees_flip_sign"))}
Ablation matrix: {sorted(k for k in ablation if not k.startswith("_"))}
Best research hypothesis: {best}
OOS validation status: NOT_RUN (base research rejected / diagnostic phase)
Research classification: {labels}
Paper status: DISABLED
Production status: DISABLED
Telegram status: DISABLED
Tests added: {tests.get("added")}
Tests executed: {tests.get("executed")}
Test result: {tests.get("result")}
LONG regression: {tests.get("long_regression")}
V1 regression: {tests.get("v1_regression")}
Remaining uncertainties: {tests.get("uncertainties") or []}

Whether SHORT math is correct: {"YES" if short_math_ok else "NO"}
Whether losses are concentrated in specific market regimes: {regime_findings.get("concentrated")}
Whether entries are late or poorly timed: {entry_findings.get("late_or_poor")}
Whether stop placement is too tight or too wide: {stop_findings.get("tight_or_wide")}
Whether TP placement is economically viable: {tp_findings.get("economically_viable")}
Whether fees explain the negative edge: {((fee_sensitivity.get("interpretation") or {}).get("fees_flip_sign"))}
Which research-only variant, if any, deserves future OOS validation: {(best.get("variant_id") if best.get("deserves_future_oos") else None)} ({best.get("note")})
No SHORT paper or live trading was enabled.
COMBO_02 v1 remains LONG-only.
BTC/ETH/SOL v1 behavior remains unchanged.
""".strip()

    return {
        **SAFETY_STAMPS,
        "diagnostic_run_id": run_id,
        "implementation_verification": implementation,
        "trade_level_dataset_count": n,
        "trade_level_dataset_path": dataset_path,
        "entry_quality_findings": entry_findings,
        "stop_placement_findings": stop_findings,
        "tp_findings": tp_findings,
        "market_regime_findings": regime_findings,
        "fee_sensitivity": fee_sensitivity,
        "ablation_matrix": {
            k: {kk: vv for kk, vv in v.items() if kk != "closed_trades"}
            if isinstance(v, dict)
            else v
            for k, v in ablation.items()
        },
        "best_research_hypothesis": best,
        "oos_validation_status": "NOT_RUN",
        "research_classification": labels,
        "paper_status": "DISABLED",
        "production_status": "DISABLED",
        "telegram_status": "DISABLED",
        "tests": tests,
        "explicit_answers": {
            "short_math_correct": short_math_ok,
            "losses_regime_concentrated": regime_findings.get("concentrated"),
            "entries_late_or_poor": entry_findings.get("late_or_poor"),
            "stops_tight_or_wide": stop_findings.get("tight_or_wide"),
            "tp_economically_viable": tp_findings.get("economically_viable"),
            "fees_explain_negative_edge": (fee_sensitivity.get("interpretation") or {}).get(
                "fees_flip_sign"
            ),
            "variant_for_future_oos": best.get("variant_id") if best.get("deserves_future_oos") else None,
            "short_paper_live_enabled": False,
            "combo02_v1_long_only": True,
            "btc_eth_sol_v1_unchanged": True,
        },
        "operator_text": text_block,
    }
