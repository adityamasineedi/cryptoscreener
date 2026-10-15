"""Strategy lifecycle, separate from individual setup status.

DRAFT → BACKTEST_RUNNING → BACKTEST_FAILED / INSUFFICIENT_EVIDENCE / VALIDATED
→ PAPER_MONITORING → SIGNAL_ENABLED → SUSPENDED

SIGNAL_ENABLED is operator-only and requires the frozen paper policy.
Implementation never claims that forward validation has passed.
"""

from __future__ import annotations

from app.research.intraday_bos_sd.spec import PAPER_MIN_CALENDAR_DAYS, PAPER_MIN_CANDIDATES, PAPER_POLICY_ID

STATES = frozenset(
    {
        "DRAFT",
        "BACKTEST_RUNNING",
        "BACKTEST_FAILED",
        "INSUFFICIENT_EVIDENCE",
        "VALIDATED",
        "PAPER_MONITORING",
        "SIGNAL_ENABLED",
        "SUSPENDED",
    }
)

EDGES = {
    "DRAFT": {"BACKTEST_RUNNING"},
    "BACKTEST_RUNNING": {"BACKTEST_FAILED", "INSUFFICIENT_EVIDENCE", "VALIDATED"},
    "BACKTEST_FAILED": {"BACKTEST_RUNNING", "SUSPENDED"},
    "INSUFFICIENT_EVIDENCE": {"BACKTEST_RUNNING", "SUSPENDED"},
    "VALIDATED": {"PAPER_MONITORING", "SUSPENDED"},
    "PAPER_MONITORING": {"SIGNAL_ENABLED", "SUSPENDED", "BACKTEST_RUNNING"},
    "SIGNAL_ENABLED": {"SUSPENDED"},
    "SUSPENDED": {"DRAFT"},
}


class IllegalLifecycle(ValueError):
    pass


def transition(current: str, new: str, *, operator: bool = False) -> str:
    cur = current.upper()
    nxt = new.upper()
    if cur not in STATES or nxt not in STATES:
        raise IllegalLifecycle(f"unknown state {current} -> {new}")
    if cur == nxt:
        return cur
    if nxt not in EDGES.get(cur, set()):
        raise IllegalLifecycle(f"illegal {cur} -> {nxt}")
    if nxt == "SIGNAL_ENABLED" and not operator:
        raise IllegalLifecycle("SIGNAL_ENABLED requires an operator action")
    return nxt


def paper_policy_met(*, calendar_days: int, candidates: int, mismatches: int, historical_status: str) -> bool:
    return (
        historical_status == "VALIDATED"
        and calendar_days >= PAPER_MIN_CALENDAR_DAYS
        and candidates >= PAPER_MIN_CANDIDATES
        and mismatches == 0
    )


def actionable(status: str) -> bool:
    return status == "SIGNAL_ENABLED"


def publication_kind(status: str) -> str:
    if status == "SIGNAL_ENABLED":
        return "ACTIONABLE_SIGNAL"
    if status == "PAPER_MONITORING":
        return "PAPER_CANDIDATE"
    return "RESEARCH_ONLY"


def paper_policy_document() -> dict:
    return {
        "policy_id": PAPER_POLICY_ID,
        "frozen_before_forward_results": True,
        "minimum_calendar_days": PAPER_MIN_CALENDAR_DAYS,
        "minimum_candidates": PAPER_MIN_CANDIDATES,
        "require_zero_replay_mismatches": True,
        "require_historical_status": "VALIDATED",
        "forward_validation_passed": False,
    }
