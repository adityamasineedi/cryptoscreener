"""COMBO_03_TRANSITION baseline parameters — research only, not optimized.

Reuse SWEEP_LOOKBACK from COMBO_02 v2 liquidity-sweep helper. Expiry windows
are explicit because the repo has no existing sweep→shift→15m expiry knobs.
"""

from __future__ import annotations

# Eligible regimes from the existing market_structure classifier (exact labels).
ELIGIBLE_REGIMES = frozenset(
    {
        "CHOPPY",
        "RANGE",
        "HIGH_VOLATILITY_RANGE",
        "TRANSITION",
    }
)

# Same lookback as combo02_v2.playbooks.SWEEP_LOOKBACK.
SWEEP_LOOKBACK = 24

# Max 1h bars from sweep detection to structure-shift confirmation.
MAX_BARS_SWEEP_TO_STRUCTURE = 12

# Max 1h bars from structure shift to genuine 15m confirmation.
MAX_BARS_STRUCTURE_TO_15M = 8

# Playbook label for diagnostics / snapshots.
PLAYBOOK_TRANSITION = "CHOPPY_TRANSITION_BREAKOUT"
