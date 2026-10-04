"""Final pre-paper review gate for COMBO_02_SHORT_RESEARCH.

Never enables paper/live/Telegram/production. Review may only recommend a
*separate* approval process.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.short_research_forensics import (
    EVIDENCE_DERIVED,
    EVIDENCE_DIRECT,
    EVIDENCE_UNKNOWN,
)
from app.research.short_research_quality import sample_size_assessment

REVIEW_BLOCKED = "REVIEW_BLOCKED"
REVIEW_READY = "REVIEW_READY_FOR_SEPARATE_APPROVAL"
RESEARCH_ONLY_CONTINUES = "RESEARCH_ONLY_CONTINUES"

# Imported lazily inside build to avoid circular import with hard_gates.


def sample_class(trade_count: int) -> str:
    n = int(trade_count or 0)
    if n <= 0:
        return "NO_TRADES"
    if n <= 9:
        return "VERY_SMALL"
    if n <= 29:
        return "SMALL"
    return "USABLE_FOR_REVIEW"


def oos_validation_sample_warning(trade_count: int) -> dict[str, Any]:
    n = int(trade_count or 0)
    return {
        "rule": "oos_validation_sample_size",
        "actual": n,
        "required": ">= 30",
        "passed": n >= 30,
        "severity": "WARNING" if n < 30 else "INFO",
        "sample_class": sample_class(n),
        "overridable": False,
    }


def build_prepaper_review(
    candidate: Mapping[str, Any],
    *,
    operator_confirm_override: bool = False,
    evidence_mode: str = "DIRECT_CANDLE_REPLAY",
) -> dict[str, Any]:
    """Assemble immutable pre-paper review verdict.

    ``operator_confirm_override`` is accepted only to prove it cannot clear
    hard gates (DIRECT replay, OOS sample ≥ 30, windows, recon, health).
    """
    from app.research.short_research_hard_gates import classify_review_from_hard_gates

    forensic = candidate.get("forensic_lookahead") or {}
    evidence = set(forensic.get("evidence_classes") or [])
    if not evidence and forensic.get("audits"):
        evidence = {
            str(a.get("evidence_class") or EVIDENCE_UNKNOWN)
            for a in forensic.get("audits") or []
        }
    direct_ok = bool(forensic.get("forensic_pass_for_promotion"))

    oos = candidate.get("oos") or {}
    oos_val = candidate.get("oos_validation") or {}
    oos_val_n = int(oos_val.get("trade_count") or 0)
    oos_warn = oos_validation_sample_warning(oos_val_n)

    classified = classify_review_from_hard_gates(
        candidate, evidence_mode=evidence_mode
    )
    blockers = list(classified.get("blockers") or [])
    followups = list(classified.get("required_followups") or [])

    # Legacy blocker names for UI clarity
    if "direct_candle_replay" in blockers:
        blockers.append("DIRECT_CANDLE_REPLAY unavailable")
    if "oos_validation_sample_ge_30" in blockers:
        blockers.append("OOS validation sample below 30")
    if "requested_range_available" in blockers:
        blockers.append("requested range unavailable")
    if "data_health_healthy" in blockers:
        blockers.append("data gaps detected")
    if "windows_ok" in blockers:
        blockers.append("window overlap")
    if any(
        x in blockers
        for x in (
            "equity_reconciliation",
            "fee_reconciliation",
            "trade_order_reconciliation",
        )
    ):
        blockers.append("reconciliation failed")

    if operator_confirm_override:
        followups.append("operator_confirm_ignored_for_hard_gates")

    counts = candidate.get("partition_counts") or {}
    sample_summary = {
        "base_trade_count": int(counts.get("base_trades") or 0),
        "oos_dev_trade_count": int(counts.get("oos_dev_trades") or 0),
        "oos_validation_trade_count": oos_val_n,
        "combined_count": int(counts.get("base_trades") or 0)
        + int(counts.get("oos_dev_trades") or 0)
        + oos_val_n,
        "base_class": sample_class(int(counts.get("base_trades") or 0)),
        "oos_dev_class": sample_class(int(counts.get("oos_dev_trades") or 0)),
        "oos_validation_class": sample_class(oos_val_n),
    }

    return {
        "review_status": classified["review_status"],
        "research_quality": classified["research_quality"],
        "human_label": classified.get("human_label"),
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
        "blockers": blockers,
        "required_followups": followups,
        "oos_validation_sample_warning": oos_warn,
        "sample_summary": sample_summary,
        "evidence_classes": sorted(evidence),
        "forensic_pass_for_promotion": direct_ok,
        "hard_gates": classified.get("hard_gates"),
        "operator_override_honored": False,
        "window_policy": (candidate.get("research_windows") or {}).get("policy"),
        "determines_oos_status": oos.get("determines_status") or "oos_validation",
        "evidence_mode": evidence_mode,
    }


def attach_partition_sample_warnings(
    *,
    base_trades: int,
    oos_dev_trades: int,
    oos_val_trades: int,
) -> list[dict[str, Any]]:
    warnings = list(sample_size_assessment(base_trades)["warnings"])
    warnings.append(oos_validation_sample_warning(oos_val_trades))
    warnings.append(
        {
            "rule": "oos_development_sample_size",
            "actual": int(oos_dev_trades or 0),
            "required": "informational",
            "passed": True,
            "severity": "INFO",
            "sample_class": sample_class(oos_dev_trades),
            "overridable": False,
        }
    )
    return warnings
