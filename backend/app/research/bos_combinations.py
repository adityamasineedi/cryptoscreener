"""Explicit BOS research combination definitions.

No combination is assumed superior. Conditions are stored with each backtest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CombinationDefinition:
    combination_id: str
    name: str
    description: str
    require_bos: bool = True
    require_trend: bool = False
    require_impulse: bool = False
    require_pullback: bool = False
    require_rvol: bool = False
    require_sd: bool = False
    require_rr: bool = False
    # Hard 4h+1h structure alignment (fail closed). Independent of live
    # SignalConfig.require_mtf_alignment — research must pass HTF candles.
    require_htf_alignment: bool = False
    conditions: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "combination_id": self.combination_id,
            "name": self.name,
            "description": self.description,
            "require_bos": self.require_bos,
            "require_trend": self.require_trend,
            "require_impulse": self.require_impulse,
            "require_pullback": self.require_pullback,
            "require_rvol": self.require_rvol,
            "require_sd": self.require_sd,
            "require_rr": self.require_rr,
            "require_htf_alignment": self.require_htf_alignment,
            "conditions": list(self.conditions),
            "gates": {
                "BOS": self.require_bos,
                "Trend": self.require_trend,
                "Impulse": self.require_impulse,
                "Pullback": self.require_pullback,
                "RVOL": self.require_rvol,
                "S/D": self.require_sd,
                "R:R": self.require_rr,
                "HTF": self.require_htf_alignment,
            },
            "direction_filter_notes": {
                "setup_tf_only": not self.require_htf_alignment,
                "htf_hard_gate": self.require_htf_alignment,
            },
        }


COMBINATIONS: dict[str, CombinationDefinition] = {
    "COMBO_01": CombinationDefinition(
        combination_id="COMBO_01",
        name="BOS_ONLY",
        description="BOS confirmed.",
        require_bos=True,
        conditions=("BOS confirmed",),
    ),
    # v1 freeze (tag: v1-combo02-long-htf) — do not weaken HTF; see docs/v1_freeze.md
    "COMBO_02": CombinationDefinition(
        combination_id="COMBO_02",
        name="TREND_BOS",
        description=(
            "BOS confirmed AND setup-TF trend agrees AND 4h+1h HTF "
            "structure aligned with BOS direction (fail closed)."
        ),
        require_bos=True,
        require_trend=True,
        require_htf_alignment=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "4h+1h HTF structure aligned (hard gate)",
        ),
    ),
    # Legacy setup-TF-only baseline — research A/B only.
    # NOT COMBO_02 v1 / NOT HTF-gated production; do not use for v1 claims.
    "COMBO_02_LOCAL": CombinationDefinition(
        combination_id="COMBO_02_LOCAL",
        name="TREND_BOS_SETUP_TF_ONLY",
        description=(
            "LEGACY/RESEARCH-ONLY (not COMBO_02 v1): BOS + setup-TF trend only "
            "(no 4h+1h HTF hard gate). For before/after comparison vs COMBO_02."
        ),
        require_bos=True,
        require_trend=True,
        require_htf_alignment=False,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "Setup-TF only (HTF not required) — not v1 production",
        ),
    ),
    "COMBO_03": CombinationDefinition(
        combination_id="COMBO_03",
        name="TREND_BOS_PULLBACK",
        description="BOS confirmed AND trend agrees AND pullback confirmed.",
        require_bos=True,
        require_trend=True,
        require_pullback=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "Pullback confirmed",
        ),
    ),
    "COMBO_04": CombinationDefinition(
        combination_id="COMBO_04",
        name="TREND_BOS_IMPULSE_PULLBACK",
        description="BOS AND trend AND impulse AND pullback.",
        require_bos=True,
        require_trend=True,
        require_impulse=True,
        require_pullback=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "Impulse confirmed",
            "Pullback confirmed",
        ),
    ),
    "COMBO_05": CombinationDefinition(
        combination_id="COMBO_05",
        name="TREND_BOS_IMPULSE_PULLBACK_RVOL",
        description="TREND_BOS_IMPULSE_PULLBACK AND RVOL confirmation.",
        require_bos=True,
        require_trend=True,
        require_impulse=True,
        require_pullback=True,
        require_rvol=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "Impulse confirmed",
            "Pullback confirmed",
            "RVOL confirmation",
        ),
    ),
    "COMBO_06": CombinationDefinition(
        combination_id="COMBO_06",
        name="TREND_BOS_PULLBACK_SD",
        description="BOS AND trend AND pullback AND Supply/Demand confluence.",
        require_bos=True,
        require_trend=True,
        require_pullback=True,
        require_sd=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "Pullback confirmed",
            "Supply/Demand confluence",
        ),
    ),
    "COMBO_07": CombinationDefinition(
        combination_id="COMBO_07",
        name="TREND_BOS_IMPULSE_PULLBACK_RVOL_SD",
        description="All prior gates including impulse, RVOL, and S/D.",
        require_bos=True,
        require_trend=True,
        require_impulse=True,
        require_pullback=True,
        require_rvol=True,
        require_sd=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "Impulse confirmed",
            "Pullback confirmed",
            "RVOL confirmation",
            "Supply/Demand confluence",
        ),
    ),
    "COMBO_08": CombinationDefinition(
        combination_id="COMBO_08",
        name="TREND_BOS_IMPULSE_PULLBACK_RVOL_SD_RR",
        description="All gates AND R:R >= configured minimum.",
        require_bos=True,
        require_trend=True,
        require_impulse=True,
        require_pullback=True,
        require_rvol=True,
        require_sd=True,
        require_rr=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "Impulse confirmed",
            "Pullback confirmed",
            "RVOL confirmation",
            "Supply/Demand confluence",
            "R:R >= configured minimum",
        ),
    ),
}


def get_combination(combination_id: str) -> CombinationDefinition | None:
    key = combination_id.upper().strip()
    if key in COMBINATIONS:
        return COMBINATIONS[key]
    for combo in COMBINATIONS.values():
        if combo.name == key or combo.name == combination_id:
            return combo
    return None


def list_combinations() -> list[dict[str, Any]]:
    return [c.to_dict() for c in COMBINATIONS.values()]
