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
        "RESEARCH_REJECTED",
        "PROMISING",
        "OOS_PENDING",
        "OOS_FAILED",
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
            "DATA_PENDING",
            "ARCHIVED",
        }
    ),
    "RESEARCH_REJECTED": frozenset({"DATA_PENDING", "ARCHIVED", "SUSPENDED"}),
    "PROMISING": frozenset({"OOS_PENDING", "RESEARCH_REJECTED", "ARCHIVED"}),
    "OOS_PENDING": frozenset(
        {"OOS_FAILED", "V2_PAPER_CANDIDATE", "RESEARCH_REJECTED", "ARCHIVED"}
    ),
    "OOS_FAILED": frozenset({"OOS_PENDING", "ARCHIVED", "SUSPENDED"}),
    "V2_PAPER_CANDIDATE": frozenset(
        {"PAPER_VALIDATING", "SUSPENDED", "ARCHIVED", "OOS_PENDING"}
    ),
    "PAPER_VALIDATING": frozenset({"APPROVED", "SUSPENDED", "ARCHIVED"}),
    "APPROVED": frozenset({"SUSPENDED", "ARCHIVED"}),
    "SUSPENDED": frozenset({"ARCHIVED", "V2_PAPER_CANDIDATE", "PAPER_VALIDATING"}),
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
    """UI badge category — never implies v1 eligibility."""
    s = str(state or "").upper()
    reason = str(state_reason or "").upper()
    if s == "DISCOVERED":
        return "DISCOVERY ONLY"
    if s in ("DATA_PENDING",) or reason.startswith("BLOCKED"):
        return "DATA BLOCKED"
    if s in (
        "DATA_READY",
        "BACKTEST_QUEUED",
        "BACKTEST_RUNNING",
        "PROMISING",
        "OOS_PENDING",
        "RESEARCH_REJECTED",
        "OOS_FAILED",
    ):
        return "RESEARCH ONLY"
    if s == "V2_PAPER_CANDIDATE":
        return "V2 PAPER CANDIDATE"
    if s == "PAPER_VALIDATING":
        return "EXPERIMENTAL PAPER"
    if s == "APPROVED":
        return "APPROVED (future, not v1)"
    if s == "SUSPENDED":
        return "SUSPENDED"
    if s == "ARCHIVED":
        return "SUSPENDED"
    return "RESEARCH ONLY"
