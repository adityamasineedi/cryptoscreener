"""COMBO_03_TRANSITION research variants — separate from COMBO_02 and legacy COMBO_03.

Baseline (A): eligible regime + sweep + rejection + 1H structure shift + mandatory 15M
Variant B: same without mandatory 15M (comparison only)
Variant C: baseline + displacement required (existing impulse engine)
"""

from __future__ import annotations

VARIANT_BASELINE = "TRANSITION_A"
VARIANT_B_NO_15M = "TRANSITION_B"
VARIANT_C_DISPLACEMENT = "TRANSITION_C"

COMBO_03_FAMILY = frozenset(
    {
        "COMBO_03_TRANSITION",
        "COMBO_03_TRANSITION_B",
        "COMBO_03_TRANSITION_C",
    }
)


def variant_for_combination_id(combination_id: str | None) -> str:
    cid = str(combination_id or "")
    if cid == "COMBO_03_TRANSITION_B":
        return VARIANT_B_NO_15M
    if cid == "COMBO_03_TRANSITION_C":
        return VARIANT_C_DISPLACEMENT
    return VARIANT_BASELINE


def require_15m_confirmation(variant: str) -> bool:
    return variant != VARIANT_B_NO_15M


def require_displacement(variant: str) -> bool:
    return variant == VARIANT_C_DISPLACEMENT
