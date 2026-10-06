"""GRID_RANGE_RESEARCH — research-only regime-gated grid experiment.

Does not modify COMBO_02 / COMBO_02_V2 / production risk or execution.
"""

from app.research.grid_range_research.config import (
    GRID_VARIANTS,
    GridVariantConfig,
    get_variant,
)
from app.research.grid_range_research.engine import run_grid_backtest
from app.research.grid_range_research.levels import build_grid_levels, grid_spacing

__all__ = [
    "GRID_VARIANTS",
    "GridVariantConfig",
    "get_variant",
    "build_grid_levels",
    "grid_spacing",
    "run_grid_backtest",
]
