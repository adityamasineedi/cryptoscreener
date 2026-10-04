"""Strict server-side state transitions for strategy_candidate_registry.

Background jobs may never promote to PAPER_VALIDATING or APPROVED.
Only explicit operator-approved API actions may do that.
"""

from __future__ import annotations

from typing import FrozenSet

# Canonical states (WATCHLIST is a display/research label, not an executable state).
CANDIDATE_STATES: FrozenSet[str] = frozenset(
    {
        "DISCOVERED",
        "DATA_PENDING",
        "DATA_READY",
        "BACKTEST_QUEUED",
        "BACKTEST_RUNNING",
        "BACKTEST_COMPLETED",
        "RESEARCH_REJECTED",
        "PROMISING",
        "OOS_PENDING",
        "OOS_FAILED",
        "SHORT_RESEARCH_CANDIDATE",
        "V2_PAPER_CANDIDATE",
        "PAPER_VALIDATING",
        "APPROVED",
        "SUSPENDED",
        "ARCHIVED",
    }
)

# Display-only research tier (never a registry state that enables execution).
DISPLAY_LABELS: FrozenSet[str] = frozenset({"WATCHLIST", "INSUFFICIENT_DATA"})

# States that background jobs must never enter.
OPERATOR_ONLY_STATES: FrozenSet[str] = frozenset({"PAPER_VALIDATING", "APPROVED"})

# Allowed directed edges. Missing edge → reject transition.
ALLOWED_TRANSITIONS: dict[str, FrozenSet[str]] = {
    "DISCOVERED": frozenset({"DATA_PENDING", "ARCHIVED"}),
    "DATA_PENDING": frozenset({"DATA_READY", "ARCHIVED", "DATA_PENDING"}),
    "DATA_READY": frozenset({"BACKTEST_QUEUED", "DATA_PENDING", "ARCHIVED"}),
    "BACKTEST_QUEUED": frozenset({"BACKTEST_RUNNING", "DATA_PENDING", "ARCHIVED"}),
    "BACKTEST_RUNNING": frozenset(
        {
            "PROMISING",
            "RESEARCH_REJECTED",
            "BACKTEST_COMPLETED",
            "DATA_PENDING",
            "ARCHIVED",
        }
    ),
    "BACKTEST_COMPLETED": frozenset(
        {
            "PROMISING",
            "RESEARCH_REJECTED",
            "SHORT_RESEARCH_CANDIDATE",
            "OOS_FAILED",
            "ARCHIVED",
        }
    ),
    "RESEARCH_REJECTED": frozenset({"DATA_PENDING", "ARCHIVED", "SUSPENDED"}),
    "PROMISING": frozenset(
        {"OOS_PENDING", "RESEARCH_REJECTED", "SHORT_RESEARCH_CANDIDATE", "ARCHIVED"}
    ),
    "OOS_PENDING": frozenset(
        {
            "OOS_FAILED",
            "V2_PAPER_CANDIDATE",
            "SHORT_RESEARCH_CANDIDATE",
            "RESEARCH_REJECTED",
            "ARCHIVED",
        }
    ),
    "OOS_FAILED": frozenset({"OOS_PENDING", "ARCHIVED", "SUSPENDED"}),
    # SHORT research terminal — never transitions into paper/production.
    "SHORT_RESEARCH_CANDIDATE": frozenset({"ARCHIVED", "SUSPENDED", "OOS_FAILED"}),
    "V2_PAPER_CANDIDATE": frozenset(
        {"PAPER_VALIDATING", "SUSPENDED", "ARCHIVED", "OOS_PENDING"}
    ),
    "PAPER_VALIDATING": frozenset({"APPROVED", "SUSPENDED", "ARCHIVED"}),
    "APPROVED": frozenset({"SUSPENDED", "ARCHIVED"}),
    "SUSPENDED": frozenset({"ARCHIVED", "V2_PAPER_CANDIDATE"}),
    "ARCHIVED": frozenset(),
}


class IllegalStateTransition(ValueError):
    """Raised when a transition is not on the allow-list."""


class OperatorOnlyTransition(PermissionError):
    """Raised when a background job attempts PAPER_VALIDATING / APPROVED."""


def assert_transition_allowed(
    current: str,
    new_state: str,
    *,
    operator_approved_action: bool = False,
) -> None:
    """Fail closed unless the edge is explicitly allowed.

    ``operator_approved_action`` must be True for any transition into
    PAPER_VALIDATING or APPROVED (explicit API/UI only).
    """
    cur = str(current or "").upper().strip()
    nxt = str(new_state or "").upper().strip()
    if cur not in CANDIDATE_STATES:
        raise IllegalStateTransition(f"unknown current state: {current}")
    if nxt not in CANDIDATE_STATES:
        raise IllegalStateTransition(f"unknown target state: {new_state}")
    if cur == nxt:
        return
    allowed = ALLOWED_TRANSITIONS.get(cur, frozenset())
    if nxt not in allowed:
        raise IllegalStateTransition(f"illegal transition {cur} → {nxt}")
    if nxt in OPERATOR_ONLY_STATES and not operator_approved_action:
        raise OperatorOnlyTransition(
            f"transition to {nxt} requires explicit operator approval"
        )


def badge_for_state(state: str, *, state_reason: str | None = None) -> str:
    """UI badge category — never implies v1 eligibility or bare 'approved'."""
    s = str(state or "").upper()
    reason = str(state_reason or "").upper()
    if s == "DISCOVERED":
        return "DISCOVERY ONLY"
    if s in ("DATA_PENDING",) or reason.startswith("BLOCKED"):
        return "DATA BLOCKED"
    if s == "RESEARCH_REJECTED":
        return "RESEARCH REJECTED"
    if s == "OOS_FAILED":
        return "OOS FAILED"
    if s in (
        "DATA_READY",
        "BACKTEST_QUEUED",
        "BACKTEST_RUNNING",
        "BACKTEST_COMPLETED",
        "PROMISING",
        "OOS_PENDING",
        "SHORT_RESEARCH_CANDIDATE",
    ):
        return "RESEARCH ONLY"
    if s == "V2_PAPER_CANDIDATE":
        return "V2 PAPER CANDIDATE"
    if s == "PAPER_VALIDATING":
        return "EXPERIMENTAL PAPER"
    if s == "APPROVED":
        return "PRODUCTION APPROVED (future, not v1)"
    if s == "SUSPENDED":
        return "SUSPENDED"
    if s == "ARCHIVED":
        return "SUSPENDED"
    return "RESEARCH ONLY"


def research_tier_for_row(row: dict) -> str:
    """Research result label — never conflated with operator/production approval.

    Prefer the base backtest tier (e.g. PROMISING) over lifecycle states so UI
    can show Research: PROMISING separately from Operational: PAPER_VALIDATING.
    """
    state = str(row.get("state") or "").upper()
    backtest_tier = str(row.get("backtest_tier") or "").upper()
    oos_status = str(row.get("oos_status") or "").upper()
    if state == "RESEARCH_REJECTED" or backtest_tier in ("REJECT", "RESEARCH_REJECTED"):
        return "RESEARCH_REJECTED"
    if state == "OOS_FAILED" or oos_status in ("OOS_FAIL", "OOS_FAILED"):
        return "OOS_FAILED"
    if backtest_tier == "INSUFFICIENT_DATA":
        return "INSUFFICIENT_DATA"
    if state == "SHORT_RESEARCH_CANDIDATE" or oos_status == "SHORT_RESEARCH_CANDIDATE":
        return "SHORT_RESEARCH_CANDIDATE"
    if backtest_tier == "PROMISING":
        return "PROMISING"
    # WATCHLIST is display-only and never paper-eligible.
    if backtest_tier == "WATCHLIST":
        return "RESEARCH_REJECTED"
    if state in ("V2_PAPER_CANDIDATE", "PAPER_VALIDATING") or oos_status == "V2_PAPER_CANDIDATE":
        return backtest_tier or "V2_PAPER_CANDIDATE"
    if state in ("PROMISING", "OOS_PENDING"):
        return "PROMISING"
    if state in ("DISCOVERED", "DATA_PENDING", "DATA_READY", "BACKTEST_QUEUED", "BACKTEST_RUNNING"):
        return "INSUFFICIENT_DATA"
    return backtest_tier or state or "INSUFFICIENT_DATA"


def operational_state_for_row(row: dict) -> str:
    """Operational/approval lifecycle — distinct from research tier.

    Never returns bare 'approved' / 'not approved'.
    """
    state = str(row.get("state") or "").upper()
    if state == "SUSPENDED":
        return "SUSPENDED"
    if state == "APPROVED":
        return "PRODUCTION_APPROVED"
    if state == "PAPER_VALIDATING":
        return "PAPER_VALIDATING"
    if bool(row.get("operator_approved")):
        return "PAPER_APPROVED"
    return "NOT_APPROVED_FOR_PAPER"


def operator_paper_approval_label(row: dict) -> str:
    """Never returns bare 'approved' — experimental paper vs not-for-paper."""
    if str(row.get("state") or "").upper() == "PAPER_VALIDATING" or bool(
        row.get("operator_approved")
    ):
        return "EXPERIMENTAL_PAPER_APPROVED"
    return "NOT_APPROVED_FOR_PAPER"


def production_approval_label(row: dict) -> str:
    # This change never creates a production path; APPROVED is future-only.
    if str(row.get("state") or "").upper() == "APPROVED" and bool(
        row.get("production_approved")
    ):
        return "PRODUCTION_APPROVED"
    return "NOT_APPROVED_FOR_PRODUCTION"


def telegram_eligibility_label(row: dict) -> str:
    # Dynamic candidates are always Telegram-disabled.
    _ = row
    return "TELEGRAM_DISABLED"


def safety_badges_for_row(row: dict) -> list[str]:
    """Badges for experimental paper / v2 research rows."""
    state = str(row.get("state") or "").upper()
    badges = ["V2 RESEARCH", "NOT V1", "NOT PRODUCTION", "TELEGRAM OFF"]
    if state in ("PAPER_VALIDATING", "V2_PAPER_CANDIDATE") or bool(
        row.get("operator_approved")
    ):
        badges.insert(1, "PAPER ONLY")
    return badges


def portfolio_status_for_row(row: dict) -> str:
    report = row.get("portfolio_report") or {}
    if not isinstance(report, dict) or not report:
        return "NOT_CHECKED"
    if report.get("last_block_reason") == "blocked_portfolio_risk_cap":
        return "BLOCKED_PORTFOLIO_RISK_CAP"
    if report.get("portfolio_fail"):
        return "FAIL"
    # Prefer explicit persisted pass/fail (never infer from mere report presence).
    explicit = str(report.get("portfolio_status") or "").upper()
    if explicit in ("PASS", "REVIEWED_PASS", "FAIL"):
        return explicit
    if str(row.get("state") or "").upper() in (
        "V2_PAPER_CANDIDATE",
        "PAPER_VALIDATING",
    ):
        # Operator-reviewed paper-only recommendation counts as REVIEWED_PASS.
        rec = str(report.get("recommendation") or "").upper()
        if rec in ("V2_PAPER_ONLY", "REVIEWED_PASS", "PASS"):
            return "REVIEWED_PASS" if rec == "V2_PAPER_ONLY" else "PASS"
        return "PASS"
    if report.get("recommendation"):
        return str(report.get("recommendation"))
    return "CHECKED"
