"""Aggregate diagnostic labels for COMBO_02 SHORT research (no PAPER_CANDIDATE)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.short_research_diagnostics.constants import (
    ENTRY_CHASING,
    ENTRY_IMMEDIATE_BREAK,
    ENTRY_LATE,
    ENTRY_RETEST,
    LABEL_ENTRY_TIMING_PROBLEM,
    LABEL_FEE_SENSITIVITY,
    LABEL_IMPLEMENTATION_DEFECT,
    LABEL_INSUFFICIENT_SAMPLE,
    LABEL_MARKET_REGIME_DEPENDENT,
    LABEL_RESEARCH_REJECTED,
    LABEL_STOP_PLACEMENT_PROBLEM,
    LABEL_TP_PLACEMENT_PROBLEM,
    REGIME_SIDEWAYS,
    REGIME_STRONG_BEAR,
    REGIME_STRONG_BULL,
    STOP_TOO_TIGHT,
    STOP_TOO_WIDE,
    STOP_WRONG_SIDE,
)


def classify_short_diagnostics(
    *,
    implementation: Mapping[str, Any],
    diagnostic_rows: Sequence[Mapping[str, Any]],
    entry_summary: Mapping[str, Any],
    stop_summary: Mapping[str, Any],
    tp_findings: Mapping[str, Any],
    regime_summary: Mapping[str, Any],
    fee_sensitivity: Mapping[str, Any],
    baseline_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    labels: list[str] = []
    details: dict[str, Any] = {}

    if not implementation.get("ok"):
        labels.append(LABEL_IMPLEMENTATION_DEFECT)
        details["implementation"] = "mirrored_or_geometry_failed"
        return {
            "labels": labels,
            "details": details,
            "primary": LABEL_IMPLEMENTATION_DEFECT,
            "paper_status": "DISABLED",
            "production_status": "DISABLED",
            "telegram_status": "DISABLED",
        }

    n = len(diagnostic_rows)
    if n < 30:
        labels.append(LABEL_INSUFFICIENT_SAMPLE)

    # Entry timing
    retest = entry_summary.get(ENTRY_RETEST) or {}
    imm = entry_summary.get(ENTRY_IMMEDIATE_BREAK) or {}
    late = entry_summary.get(ENTRY_LATE) or {}
    chase = entry_summary.get(ENTRY_CHASING) or {}
    retest_r = retest.get("average_net_r")
    imm_r = imm.get("average_net_r")
    late_share = (int(late.get("trade_count") or 0) + int(chase.get("trade_count") or 0)) / n if n else 0
    if late_share >= 0.35 or (
        retest_r is not None
        and imm_r is not None
        and float(retest_r) > float(imm_r) + 0.1
    ):
        labels.append(LABEL_ENTRY_TIMING_PROBLEM)
        details["entry"] = {
            "late_or_chase_share": late_share,
            "retest_avg_r": retest_r,
            "immediate_break_avg_r": imm_r,
        }

    # Stops
    tight_n = int((stop_summary.get(STOP_TOO_TIGHT) or {}).get("trade_count") or 0)
    wide_n = int((stop_summary.get(STOP_TOO_WIDE) or {}).get("trade_count") or 0)
    wrong_n = int((stop_summary.get(STOP_WRONG_SIDE) or {}).get("trade_count") or 0)
    if n and (tight_n + wide_n + wrong_n) / n >= 0.3:
        labels.append(LABEL_STOP_PLACEMENT_PROBLEM)
        details["stops"] = {"too_tight": tight_n, "too_wide": wide_n, "wrong_side": wrong_n}

    # TP / RR
    if tp_findings.get("poor_rr_after_fees") or tp_findings.get("mfe_exceeds_tp_frequently"):
        labels.append(LABEL_TP_PLACEMENT_PROBLEM)
        details["tp"] = tp_findings

    # Regime dependence
    by_reg = regime_summary or {}
    strong_bear = by_reg.get(REGIME_STRONG_BEAR) or {}
    sideways = by_reg.get(REGIME_SIDEWAYS) or {}
    strong_bull = by_reg.get(REGIME_STRONG_BULL) or {}
    sb_r = strong_bear.get("average_net_r")
    side_r = sideways.get("average_net_r")
    bull_r = strong_bull.get("average_net_r")
    if sb_r is not None and (
        (side_r is not None and float(sb_r) > float(side_r) + 0.15)
        or (bull_r is not None and float(sb_r) > float(bull_r) + 0.15)
        or (float(sb_r) > 0 and float(baseline_metrics.get("net_avg_r") or baseline_metrics.get("average_net_r") or -1) < 0)
    ):
        labels.append(LABEL_MARKET_REGIME_DEPENDENT)
        details["regime"] = {
            "strong_bear_avg_r": sb_r,
            "sideways_avg_r": side_r,
            "strong_bull_avg_r": bull_r,
        }

    # Fees
    interp = fee_sensitivity.get("interpretation") or {}
    if interp.get("fees_flip_sign") or (
        abs(float((fee_sensitivity.get("live_model") or {}).get("fees") or 0))
        > abs(float((fee_sensitivity.get("live_model") or {}).get("gross_pnl") or 0)) * 0.7
        and float((fee_sensitivity.get("live_model") or {}).get("gross_pnl") or 0) != 0
    ):
        labels.append(LABEL_FEE_SENSITIVITY)
        details["fees"] = interp

    # Always research-rejected in this diagnostic phase (no paper candidate)
    labels.append(LABEL_RESEARCH_REJECTED)

    # Dedupe preserving order
    seen = set()
    ordered = []
    for lab in labels:
        if lab not in seen:
            seen.add(lab)
            ordered.append(lab)

    primary = ordered[0] if ordered and ordered[0] != LABEL_RESEARCH_REJECTED else (
        ordered[1] if len(ordered) > 1 else LABEL_RESEARCH_REJECTED
    )
    return {
        "labels": ordered,
        "details": details,
        "primary": primary,
        "paper_status": "DISABLED",
        "production_status": "DISABLED",
        "telegram_status": "DISABLED",
    }
