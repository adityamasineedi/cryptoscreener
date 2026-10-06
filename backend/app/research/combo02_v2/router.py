"""Deterministic regime → playbook router for COMBO_02 v2."""

from __future__ import annotations

PLAYBOOK_TREND = "TREND_FOLLOWING"
PLAYBOOK_RANGE = "RANGE_MEAN_REVERSION"
PLAYBOOK_REVERSAL = "REVERSAL"
PLAYBOOK_WAIT = "WAIT"

TREND_REGIMES = frozenset(
    {"BULL_TREND", "BEAR_TREND", "HIGH_VOLATILITY_TREND"}
)
RANGE_REGIMES = frozenset(
    {
        "CHOPPY",
        "RANGE",
        "HIGH_VOLATILITY_RANGE",
        "LOW_VOLATILITY_COMPRESSION",
    }
)
REVERSAL_REGIMES = frozenset({"TRANSITION"})

# Safety priority if multiple playbooks somehow qualify (regimes are exclusive).
PLAYBOOK_PRIORITY = (PLAYBOOK_REVERSAL, PLAYBOOK_TREND, PLAYBOOK_RANGE)


def route_playbook(market_regime: str | None) -> str:
    regime = str(market_regime or "UNKNOWN").upper()
    if regime in REVERSAL_REGIMES:
        return PLAYBOOK_REVERSAL
    if regime in TREND_REGIMES:
        return PLAYBOOK_TREND
    if regime in RANGE_REGIMES:
        return PLAYBOOK_RANGE
    return PLAYBOOK_WAIT
