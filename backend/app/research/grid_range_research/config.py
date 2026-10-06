"""Controlled grid research variants — no parameter sweep."""

from __future__ import annotations

from dataclasses import dataclass


GRID_ENABLED_PRIMARY = frozenset(
    {
        "CHOPPY",
        "RANGE",
        "HIGH_VOLATILITY_RANGE",
    }
)

GRID_ENABLED_OPTIONAL = frozenset(
    {
        "LOW_VOLATILITY_COMPRESSION",
    }
)

GRID_KILL_REGIMES = frozenset(
    {
        "BULL_TREND",
        "BEAR_TREND",
        "HIGH_VOLATILITY_TREND",
        "TRANSITION",
        "UNKNOWN",
    }
)

# Research blotter parity with COMBO_02_V2 UI enrichment defaults.
RESEARCH_RISK_USD = 20.0
RESEARCH_ACCOUNT_EQUITY = 1000.0
RESEARCH_LEVERAGE = 2.0
SLIPPAGE_RATE = 0.0002  # SignalConfig.slippage_rate


@dataclass(frozen=True)
class GridVariantConfig:
    variant_id: str
    n_levels: int
    max_simultaneous: int
    include_lvc: bool = False

    @property
    def enabled_regimes(self) -> frozenset[str]:
        if self.include_lvc:
            return GRID_ENABLED_PRIMARY | GRID_ENABLED_OPTIONAL
        return GRID_ENABLED_PRIMARY


GRID_A = GridVariantConfig("GRID-A", n_levels=8, max_simultaneous=3)
GRID_B = GridVariantConfig("GRID-B", n_levels=10, max_simultaneous=3)
GRID_C = GridVariantConfig("GRID-C", n_levels=8, max_simultaneous=2)
# Optional LVC run — same geometry as GRID-A but LVC enabled.
GRID_A_LVC = GridVariantConfig(
    "GRID-A-LVC", n_levels=8, max_simultaneous=3, include_lvc=True
)

GRID_VARIANTS: dict[str, GridVariantConfig] = {
    GRID_A.variant_id: GRID_A,
    GRID_B.variant_id: GRID_B,
    GRID_C.variant_id: GRID_C,
    GRID_A_LVC.variant_id: GRID_A_LVC,
}


def get_variant(variant_id: str) -> GridVariantConfig:
    key = str(variant_id).strip().upper()
    # Normalize GRID-A / GRID_A
    key = key.replace("_", "-")
    if key not in GRID_VARIANTS:
        raise KeyError(f"unknown_grid_variant:{variant_id}")
    return GRID_VARIANTS[key]
