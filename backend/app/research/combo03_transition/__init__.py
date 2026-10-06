"""COMBO_03_TRANSITION — CHOPPY → transition / breakout research (rollback-friendly).

Separate from COMBO_02 v1/v2 and from legacy COMBO_03 (TREND_BOS_PULLBACK).
Uses existing regime classifier, liquidity-sweep helper, BOS/CHoCH engines,
and stop/target/RR risk finalize path. Disabled for production by default.
"""

from app.research.combo03_transition.evaluate import evaluate_combo03_transition_at_bar
from app.research.combo03_transition.variants import COMBO_03_FAMILY

__all__ = ["evaluate_combo03_transition_at_bar", "COMBO_03_FAMILY"]
