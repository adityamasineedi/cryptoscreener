"""Research-only market-structure family taxonomy.

This module describes discovery labels only. It does not define entry rules or
register combinations for signal generation or execution.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final


BOS_CONTINUATION: Final = "bos_continuation"
BOS_PULLBACK: Final = "bos_pullback"
SWEEP_PLUS_STRUCTURE_SHIFT: Final = "sweep_plus_structure_shift"
CHOCH_REVERSAL_ONLY: Final = "choch_reversal_only"
CHOCH_THEN_CONFIRMING_BOS: Final = "choch_then_confirming_bos"
BOS_FVG_RETEST: Final = "bos_fvg_retest"

FAMILIES: Final = (
    BOS_CONTINUATION,
    BOS_PULLBACK,
    SWEEP_PLUS_STRUCTURE_SHIFT,
    CHOCH_REVERSAL_ONLY,
    CHOCH_THEN_CONFIRMING_BOS,
    BOS_FVG_RETEST,
)

# ``None`` is deliberate: these labels are research questions, not entry rules.
FAMILY_COMBINATION_IDS: Final = MappingProxyType(
    {
        BOS_CONTINUATION: "COMBO_01",
        BOS_PULLBACK: "COMBO_03",
        SWEEP_PLUS_STRUCTURE_SHIFT: "COMBO_03_TRANSITION",
        CHOCH_REVERSAL_ONLY: None,
        CHOCH_THEN_CONFIRMING_BOS: None,
        BOS_FVG_RETEST: None,
    }
)

MAPPED_COMBINATIONS: Final = MappingProxyType(
    {
        family: combination_id
        for family, combination_id in FAMILY_COMBINATION_IDS.items()
        if combination_id is not None
    }
)

RESEARCH_ONLY: Final = True
OPERATOR_APPROVED: Final = False
PRODUCTION_APPROVED: Final = False
PAPER_ELIGIBLE: Final = False
LIVE_ELIGIBLE: Final = False
TELEGRAM_ELIGIBLE: Final = False

