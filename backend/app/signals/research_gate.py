"""Optional research gates for live setup candidates.

Default OFF. When enabled via SignalConfig / env, demotes entry candidates
that match research findings (SHORT bleed, HTF conflict). Does not change
BOS/trend/entry math — only candidate publication.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.signals.config import SignalConfig
from app.signals.schemas import MTFAlignment, SignalStatus


def apply_research_gate(
    *,
    status: str,
    direction: str | None,
    mtf: Mapping[str, Any] | None,
    config: SignalConfig,
) -> dict[str, Any]:
    """Return possibly demoted status/direction plus audit fields.

    When research_gate_enabled is False, returns inputs unchanged.
    """
    out: dict[str, Any] = {
        "status": status,
        "direction": direction,
        "research_gate_enabled": bool(config.research_gate_enabled),
        "research_gate_applied": False,
        "research_gate_reason": None,
    }
    if not config.research_gate_enabled:
        return out

    candidate_statuses = {
        SignalStatus.LONG_ENTRY_CANDIDATE.value,
        SignalStatus.SHORT_ENTRY_CANDIDATE.value,
        SignalStatus.ENTRY_CANDIDATE.value,
    }
    if status not in candidate_statuses:
        return out

    align = str((mtf or {}).get("MTF_ALIGNMENT") or "")
    dir_u = (direction or "").upper() or None

    if config.research_gate_block_shorts and dir_u == "SHORT":
        out["status"] = SignalStatus.NO_SETUP.value
        out["direction"] = None
        out["research_gate_applied"] = True
        out["research_gate_reason"] = "research_gate_block_shorts"
        return out

    if config.research_gate_block_htf_conflict:
        if align == MTFAlignment.CONFLICT.value:
            out["status"] = SignalStatus.CONFLICT.value
            out["direction"] = None
            out["research_gate_applied"] = True
            out["research_gate_reason"] = "research_gate_block_htf_conflict"
            return out
        # Require strong alignment matching direction when blocking conflict mode
        if dir_u == "LONG" and align != MTFAlignment.STRONG_LONG.value:
            out["status"] = SignalStatus.NO_SETUP.value
            out["direction"] = None
            out["research_gate_applied"] = True
            out["research_gate_reason"] = "research_gate_require_strong_long_mtf"
            return out
        if dir_u == "SHORT" and align != MTFAlignment.STRONG_SHORT.value:
            out["status"] = SignalStatus.NO_SETUP.value
            out["direction"] = None
            out["research_gate_applied"] = True
            out["research_gate_reason"] = "research_gate_require_strong_short_mtf"
            return out

    return out
