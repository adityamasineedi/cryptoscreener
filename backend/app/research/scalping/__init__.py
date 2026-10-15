"""Versioned, fail-closed crypto-futures scalping research.

The package is deliberately separate from frozen COMBO strategies.  Its pure
engine is shared by historical replay and the live screener watcher.
"""

from .catalog import SCALPING_STRATEGIES, StrategyRegistration
from .engine import ScalpingStrategyEngine
from .models import ScalpCandidate

__all__ = [
    "SCALPING_STRATEGIES",
    "ScalpCandidate",
    "ScalpingStrategyEngine",
    "StrategyRegistration",
]
