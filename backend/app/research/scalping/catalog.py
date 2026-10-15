from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyRegistration:
    strategy_id: str
    version: str
    variant: str = "default"
    specification_complete: bool = True
    research_only: bool = True

    @property
    def key(self) -> str:
        return f"{self.strategy_id}:{self.variant}"


# Registration is static and happens before evaluation.  Nothing here implies
# historical validation, paper eligibility, or profitability.
SCALPING_STRATEGIES: tuple[StrategyRegistration, ...] = (
    StrategyRegistration("scalp_bos_retest_v1", "1"),
    StrategyRegistration("scalp_sweep_reclaim_v1", "1", "reclaim_only"),
    StrategyRegistration("scalp_sweep_reclaim_v1", "1", "reclaim_choch"),
    StrategyRegistration("scalp_vwap_pullback_v1", "1"),
    # The supplied request ends at "Setup: On".  Keep the identifier visible
    # but impossible to evaluate or publish until its rules are supplied.
    StrategyRegistration(
        "scalp_compression_retest_v1", "1", specification_complete=False
    ),
)


def registration(strategy_id: str, variant: str = "default") -> StrategyRegistration:
    for item in SCALPING_STRATEGIES:
        if item.strategy_id == strategy_id and item.variant == variant:
            return item
    raise KeyError(f"unregistered scalping strategy: {strategy_id}:{variant}")
