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
            "conditions": list(self.conditions),
            "gates": {
                "BOS": self.require_bos,
                "Trend": self.require_trend,
                "Impulse": self.require_impulse,
                "Pullback": self.require_pullback,
                "RVOL": self.require_rvol,
                "S/D": self.require_sd,
                "R:R": self.require_rr,
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
    "COMBO_02": CombinationDefinition(
        combination_id="COMBO_02",
        name="TREND_BOS",
        description="BOS confirmed AND trend agrees with BOS direction.",
        require_bos=True,
        require_trend=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
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
