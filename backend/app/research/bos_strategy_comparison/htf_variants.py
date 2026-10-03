"""Research-only S3 HTF eligibility variants.

Does NOT modify production classify_htf_alignment or STRATEGY_3.
Variants only change the research HTF gate; all other S3 gates stay identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.research.bos_strategy_comparison.htf import (
    HTF_ALIGNED,
    HTF_CONFLICT,
    HTF_NEUTRAL_UNAVAILABLE,
    classify_htf_alignment,
)

DirectionalCheck = Callable[[str | None, str | None, str | None], tuple[bool, str]]


def _want(direction: str | None) -> tuple[str | None, str | None]:
    d = (direction or "").upper()
    if d in ("LONG", "BULLISH", "BULLISH_BOS"):
        return "BULLISH", "BEARISH"
    if d in ("SHORT", "BEARISH", "BEARISH_BOS"):
        return "BEARISH", "BULLISH"
    return None, None


def htf_gate_baseline(
    direction: str | None,
    trend_4h: str | None,
    trend_1h: str | None,
) -> tuple[bool, str]:
    """Exact existing S3 rule via classify_htf_alignment."""
    bos = None
    d = (direction or "").upper()
    if d in ("LONG", "BULLISH"):
        bos = "BULLISH_BOS"
    elif d in ("SHORT", "BEARISH"):
        bos = "BEARISH_BOS"
    else:
        bos = direction
    status = classify_htf_alignment(
        bos_direction=bos,
        trend_4h=trend_4h,
        trend_1h=trend_1h,
    )
    return status == HTF_ALIGNED, status


def htf_gate_h4_only(
    direction: str | None,
    trend_4h: str | None,
    trend_1h: str | None,
) -> tuple[bool, str]:
    """4H same direction; 1H ignored for eligibility."""
    want, _ = _want(direction)
    t4 = (trend_4h or "").upper()
    if want is None:
        return False, HTF_NEUTRAL_UNAVAILABLE
    if t4 == want:
        return True, "HTF_H4_ONLY_ALIGNED"
    if t4 in ("BULLISH", "BEARISH") and t4 != want:
        return False, HTF_CONFLICT
    return False, HTF_NEUTRAL_UNAVAILABLE


def htf_gate_h4_neutral_1h(
    direction: str | None,
    trend_4h: str | None,
    trend_1h: str | None,
) -> tuple[bool, str]:
    """4H same direction; 1H may be NEUTRAL; 1H opposite = conflict."""
    want, opposite = _want(direction)
    t4 = (trend_4h or "").upper()
    t1 = (trend_1h or "").upper()
    if want is None:
        return False, HTF_NEUTRAL_UNAVAILABLE
    if t4 != want:
        if t4 in ("BULLISH", "BEARISH"):
            return False, HTF_CONFLICT
        return False, HTF_NEUTRAL_UNAVAILABLE
    if t1 == opposite:
        return False, "HTF_1H_OPPOSITE_CONFLICT"
    if t1 in (want, "NEUTRAL"):
        return True, "HTF_H4_ALIGNED_1H_OK_OR_NEUTRAL"
    # UNAVAILABLE / INSUFFICIENT_DATA — not treated as NEUTRAL pass
    return False, HTF_NEUTRAL_UNAVAILABLE


def htf_gate_h1_only(
    direction: str | None,
    trend_4h: str | None,
    trend_1h: str | None,
) -> tuple[bool, str]:
    """1H same direction; 4H ignored for eligibility."""
    want, _ = _want(direction)
    t1 = (trend_1h or "").upper()
    if want is None:
        return False, HTF_NEUTRAL_UNAVAILABLE
    if t1 == want:
        return True, "HTF_H1_ONLY_ALIGNED"
    if t1 in ("BULLISH", "BEARISH") and t1 != want:
        return False, HTF_CONFLICT
    return False, HTF_NEUTRAL_UNAVAILABLE


def htf_gate_no_htf(
    direction: str | None,
    trend_4h: str | None,
    trend_1h: str | None,
) -> tuple[bool, str]:
    """Control: no HTF eligibility gate."""
    return True, "HTF_GATE_DISABLED"


@dataclass(frozen=True)
class HtfVariant:
    variant_id: str
    name: str
    description: str
    kind: str  # BASELINE | HYPOTHESIS | CONTROL
    gate: DirectionalCheck


HTF_VARIANTS: dict[str, HtfVariant] = {
    "S3_BASELINE": HtfVariant(
        variant_id="S3_BASELINE",
        name="S3 baseline 4H+1H aligned",
        description="Existing S3: 4H and 1H both same direction as trade.",
        kind="BASELINE",
        gate=htf_gate_baseline,
    ),
    "S3_H4": HtfVariant(
        variant_id="S3_H4",
        name="S3 with 4H only",
        description="Research: 4H same direction; 1H ignored.",
        kind="HYPOTHESIS",
        gate=htf_gate_h4_only,
    ),
    "S3_H4_NEUTRAL_1H": HtfVariant(
        variant_id="S3_H4_NEUTRAL_1H",
        name="S3 4H + 1H may be NEUTRAL",
        description="Research: 4H same direction; 1H NEUTRAL allowed; opposite conflict.",
        kind="HYPOTHESIS",
        gate=htf_gate_h4_neutral_1h,
    ),
    "S3_H1": HtfVariant(
        variant_id="S3_H1",
        name="S3 with 1H only",
        description="Research: 1H same direction; 4H ignored.",
        kind="HYPOTHESIS",
        gate=htf_gate_h1_only,
    ),
    "S3_NO_HTF": HtfVariant(
        variant_id="S3_NO_HTF",
        name="S3 without HTF gate (control)",
        description="Control: measure incremental HTF effect. Not for production.",
        kind="CONTROL",
        gate=htf_gate_no_htf,
    ),
}


def list_htf_variants() -> list[dict[str, str]]:
    return [
        {
            "variant_id": v.variant_id,
            "name": v.name,
            "description": v.description,
            "kind": v.kind,
            "disclaimer": "Research only — not a production strategy change.",
        }
        for v in HTF_VARIANTS.values()
    ]
