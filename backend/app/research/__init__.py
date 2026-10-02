"""BOS combination research / backtest module.

Read-only historical analysis. Does not modify live signal thresholds,
live caches, or production setup signals. Does not place trades.
Results are historical evidence only — not profitability claims.
"""

from app.research.bos_combinations import COMBINATIONS, get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.config import RESEARCH_ENGINE_VERSION, ResearchConfig

__all__ = [
    "COMBINATIONS",
    "RESEARCH_ENGINE_VERSION",
    "ResearchConfig",
    "get_combination",
    "run_combination_backtest",
]
