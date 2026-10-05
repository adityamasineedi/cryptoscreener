"""Explicit, auditable label taxonomies for market-structure analytics."""

from __future__ import annotations

TREND_STATES = frozenset(
    {
        "BULLISH",
        "BEARISH",
        "NEUTRAL",
        "TRANSITION",
        "UNKNOWN_DATA_MISSING",
        "UNKNOWN_INSUFFICIENT_HISTORY",
    }
)

STRUCTURE_STATES = frozenset(
    {
        "UPTREND_HH_HL",
        "DOWNTREND_LH_LL",
        "BULLISH_STRUCTURE",
        "BEARISH_STRUCTURE",
        "RANGE_STRUCTURE",
        "MIXED_STRUCTURE",
        "TRANSITION_STRUCTURE",
        "UNKNOWN",
    }
)

BOS_STATES = frozenset(
    {
        "BULLISH_BOS",
        "BEARISH_BOS",
        "NO_CONFIRMED_BOS",
        "BOS_PENDING_CONFIRMATION",
        "UNKNOWN",
    }
)

CHOCH_STATES = frozenset(
    {
        "BULLISH_CHOCH",
        "BEARISH_CHOCH",
        "NO_CONFIRMED_CHOCH",
        "UNKNOWN",
    }
)

PULLBACK_STATES = frozenset(
    {
        "BULLISH_PULLBACK",
        "BEARISH_PULLBACK",
        "NO_PULLBACK",
        "DEEP_PULLBACK",
        "PULLBACK_UNKNOWN",
    }
)

PRIMARY_REGIMES = frozenset(
    {
        "BULL_TREND",
        "BEAR_TREND",
        "RANGE",
        "CHOPPY",
        "HIGH_VOLATILITY_TREND",
        "HIGH_VOLATILITY_RANGE",
        "LOW_VOLATILITY_COMPRESSION",
        "TRANSITION",
        "UNKNOWN",
    }
)

MTF_ALIGNMENT_LABELS = frozenset(
    {
        "FULL_BULL_ALIGNMENT",
        "FULL_BEAR_ALIGNMENT",
        "BULLISH_HIGHER_TIMEFRAME_BUT_15M_WEAK",
        "BEARISH_HIGHER_TIMEFRAME_BUT_15M_WEAK",
        "1H_15M_BULLISH_AGAINST_4H",
        "1H_15M_BEARISH_AGAINST_4H",
        "TIMEFRAME_CONFLICT",
        "RANGE_ALIGNED",
        "VOLATILITY_ALIGNED",
        "TRANSITION_ALIGNED",
        "INSUFFICIENT_DATA",
    }
)

RESEARCH_OPPORTUNITY_LABELS = frozenset(
    {
        "TREND_FOLLOWING_CANDIDATE",
        "SHORT_TREND_FOLLOWING_CANDIDATE",
        "BREAKOUT_CANDIDATE",
        "MEAN_REVERSION_CANDIDATE",
        "PULLBACK_CANDIDATE",
        "PULLBACK_OR_WAIT",
        "NO_TRADE_CHOPPY",
        "LOW_VOLATILITY_COMPRESSION",
        "HIGH_VOLATILITY_RISK_REDUCTION",
        "TIMEFRAME_CONFLICT_AVOID",
        "TRANSITION_WAIT",
        "NONE",
    }
)

VOLATILITY_STATES = frozenset(
    {"LOW", "NORMAL", "HIGH", "UNKNOWN", "INSUFFICIENT_HISTORY"}
)

MOMENTUM_STATES = frozenset(
    {
        "BULLISH",
        "BEARISH",
        "NEUTRAL",
        "ACCELERATING",
        "DECELERATING",
        "UNKNOWN",
    }
)

RANGE_STATES = frozenset(
    {"INSIDE_RANGE", "RANGE_EXPANSION", "RANGE_COMPRESSION", "NO_RANGE", "UNKNOWN"}
)

DATA_QUALITY_STATES = frozenset(
    {
        "OK",
        "UNKNOWN_DATA_MISSING",
        "UNKNOWN_INSUFFICIENT_HISTORY",
        "PARTIAL",
    }
)

DIRECTIONS = frozenset({"BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"})
