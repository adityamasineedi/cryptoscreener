"""Predefined BOS research strategies and controls.

Independent strategies — never merged. Not deployed to the live engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    strategy_name: str
    description: str
    kind: str  # STRATEGY | CONTROL
    require_bos: bool = True
    require_retest: bool = False
    require_impulse: bool = False
    require_pullback: bool = False
    require_sd: bool = False
    require_htf_alignment: bool = False
    require_entry_ready: bool = True  # stop/targets/RR via production engines
    conditions: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "description": self.description,
            "kind": self.kind,
            "require_bos": self.require_bos,
            "require_retest": self.require_retest,
            "require_impulse": self.require_impulse,
            "require_pullback": self.require_pullback,
            "require_sd": self.require_sd,
            "require_htf_alignment": self.require_htf_alignment,
            "require_entry_ready": self.require_entry_ready,
            "conditions": list(self.conditions),
            "gates": {
                "BOS": self.require_bos,
                "Retest": self.require_retest,
                "Impulse": self.require_impulse,
                "Pullback": self.require_pullback,
                "S/D": self.require_sd,
                "HTF_ALIGNMENT": self.require_htf_alignment,
                "ENTRY_READY": self.require_entry_ready,
            },
            "label": "Historical Result",
            "disclaimer": "Research only — live engine unchanged.",
        }


STRATEGIES: dict[str, StrategyDefinition] = {
    "STRATEGY_1": StrategyDefinition(
        strategy_id="STRATEGY_1",
        strategy_name="BOS + Retest + HTF Alignment",
        description=(
            "15M BOS + 15M retest + 4H/1H aligned with BOS direction; "
            "entry/SL/TP from production engines."
        ),
        kind="STRATEGY",
        require_bos=True,
        require_retest=True,
        require_htf_alignment=True,
        require_entry_ready=True,
        conditions=(
            "15M BOS confirmed",
            "15M retest",
            "4H and 1H aligned with BOS direction",
            "Production entry/SL/TP ready",
        ),
    ),
    "STRATEGY_2": StrategyDefinition(
        strategy_id="STRATEGY_2",
        strategy_name="BOS + Impulse + Pullback + Retest + HTF Alignment",
        description=(
            "15M BOS + impulse + pullback + retest + HTF alignment; "
            "entry/SL/TP from production engines."
        ),
        kind="STRATEGY",
        require_bos=True,
        require_impulse=True,
        require_pullback=True,
        require_retest=True,
        require_htf_alignment=True,
        require_entry_ready=True,
        conditions=(
            "15M BOS confirmed",
            "Impulse confirmed",
            "Pullback ACTIVE/CONFIRMED",
            "15M retest",
            "4H and 1H aligned with BOS direction",
            "Production entry/SL/TP ready",
        ),
    ),
    "STRATEGY_3": StrategyDefinition(
        strategy_id="STRATEGY_3",
        strategy_name="BOS + Pullback + S/D + Retest + HTF Alignment",
        description=(
            "15M BOS + pullback + Supply/Demand interaction + retest + HTF; "
            "uses existing SupplyDemandEngine (as_of truncated)."
        ),
        kind="STRATEGY",
        require_bos=True,
        require_pullback=True,
        require_sd=True,
        require_retest=True,
        require_htf_alignment=True,
        require_entry_ready=True,
        conditions=(
            "15M BOS confirmed",
            "Pullback ACTIVE/CONFIRMED",
            "Supply/Demand zone interaction",
            "15M retest",
            "4H and 1H aligned with BOS direction",
            "Production entry/SL/TP ready",
        ),
    ),
    "CONTROL_A": StrategyDefinition(
        strategy_id="CONTROL_A",
        strategy_name="BOS Only",
        description="Control: BOS confirmed only. Research control — not for deployment.",
        kind="CONTROL",
        require_bos=True,
        require_entry_ready=True,
        conditions=("15M BOS confirmed", "Production entry/SL/TP ready"),
    ),
    "CONTROL_B": StrategyDefinition(
        strategy_id="CONTROL_B",
        strategy_name="BOS + HTF Alignment",
        description="Control: BOS + HTF alignment. Research control — not for deployment.",
        kind="CONTROL",
        require_bos=True,
        require_htf_alignment=True,
        require_entry_ready=True,
        conditions=(
            "15M BOS confirmed",
            "4H and 1H aligned with BOS direction",
            "Production entry/SL/TP ready",
        ),
    ),
    "CONTROL_C": StrategyDefinition(
        strategy_id="CONTROL_C",
        strategy_name="BOS + Retest",
        description="Control: BOS + retest. Research control — not for deployment.",
        kind="CONTROL",
        require_bos=True,
        require_retest=True,
        require_entry_ready=True,
        conditions=(
            "15M BOS confirmed",
            "15M retest",
            "Production entry/SL/TP ready",
        ),
    ),
    "CONTROL_D": StrategyDefinition(
        strategy_id="CONTROL_D",
        strategy_name="BOS + Pullback + Retest",
        description="Control: BOS + pullback + retest. Research control — not for deployment.",
        kind="CONTROL",
        require_bos=True,
        require_pullback=True,
        require_retest=True,
        require_entry_ready=True,
        conditions=(
            "15M BOS confirmed",
            "Pullback ACTIVE/CONFIRMED",
            "15M retest",
            "Production entry/SL/TP ready",
        ),
    ),
}

# Condition-contribution ladder (observational; not a filter cascade for live)
CONTRIBUTION_LADDER: tuple[str, ...] = (
    "CONTROL_A",  # BOS
    "CONTROL_B",  # BOS + HTF
    "CONTROL_C",  # BOS + Retest  (note: HTF not required here)
    "STRATEGY_1",  # BOS + Retest + HTF
    "CONTROL_D",  # BOS + Pullback + Retest
    "STRATEGY_2",  # + impulse + HTF
    "STRATEGY_3",  # + S/D + HTF (no impulse)
)


def get_strategy(strategy_id: str) -> StrategyDefinition | None:
    key = strategy_id.upper().strip()
    if key in STRATEGIES:
        return STRATEGIES[key]
    for s in STRATEGIES.values():
        if s.strategy_name.upper() == key or s.strategy_name == strategy_id:
            return s
    return None


def list_strategies() -> list[dict[str, Any]]:
    return [s.to_dict() for s in STRATEGIES.values()]
