"""COMBO_02 SHORT research diagnostics — research-only identity.

Never enables paper, production, Telegram, or COMBO_02 v1 SHORT.
"""

from __future__ import annotations

from app.research.short_research_constants import (
    COMBO_VERSION,
    DEFAULT_SHORT_RESEARCH_UNIVERSE,
    DIRECTION,
    PAPER_ELIGIBLE,
    PRODUCTION_APPROVED,
    SOURCE,
    STRATEGY_ID,
    TELEGRAM_ELIGIBLE,
)

DIAGNOSTIC_STRATEGY_ID = "COMBO_02_SHORT_DIAGNOSTICS"
DIAGNOSTIC_SOURCE = "SHORT_RESEARCH_DIAGNOSTICS"

# Diagnostic classification labels (phase: never PAPER_CANDIDATE).
LABEL_IMPLEMENTATION_DEFECT = "IMPLEMENTATION_DEFECT"
LABEL_MARKET_REGIME_DEPENDENT = "MARKET_REGIME_DEPENDENT"
LABEL_ENTRY_TIMING_PROBLEM = "ENTRY_TIMING_PROBLEM"
LABEL_STOP_PLACEMENT_PROBLEM = "STOP_PLACEMENT_PROBLEM"
LABEL_TP_PLACEMENT_PROBLEM = "TP_PLACEMENT_PROBLEM"
LABEL_FEE_SENSITIVITY = "FEE_SENSITIVITY"
LABEL_INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
LABEL_RESEARCH_REJECTED = "RESEARCH_REJECTED"

ENTRY_EARLY = "EARLY"
ENTRY_LATE = "LATE"
ENTRY_CHASING = "CHASING"
ENTRY_RETEST = "RETEST"
ENTRY_IMMEDIATE_BREAK = "IMMEDIATE_BREAK"

STOP_GOOD = "GOOD_STOP"
STOP_TOO_TIGHT = "TOO_TIGHT"
STOP_TOO_WIDE = "TOO_WIDE"
STOP_WRONG_SIDE = "WRONG_SIDE"

REGIME_STRONG_BEAR = "STRONG_BEAR"
REGIME_WEAK_BEAR = "WEAK_BEAR"
REGIME_SIDEWAYS = "SIDEWAYS"
REGIME_STRONG_BULL = "STRONG_BULL"
REGIME_WEAK_BULL = "WEAK_BULL"
REGIME_HIGH_VOL = "HIGH_VOLATILITY"
REGIME_LOW_VOL = "LOW_VOLATILITY"
REGIME_NEAR_SUPPORT = "NEAR_SUPPORT"

VARIANT_BASELINE = "baseline"
VARIANT_RETEST_ONLY = "retest_only"
VARIANT_STRONG_BEAR = "strong_bear_filter"
VARIANT_WIDER_STOP = "wider_stop"
VARIANT_SUPPORT_TP = "support_tp"
VARIANT_NO_HTF = "no_htf"

VARIANT_SPECS: dict[str, dict[str, str]] = {
    VARIANT_BASELINE: {
        "label": "Baseline",
        "trend": "LH/LL",
        "bos": "bearish BOS",
        "htf": "1h+4h bearish",
        "entry": "current",
        "stop": "current",
        "tp": "current",
        "fees": "live model",
        "combination_id": "COMBO_02",
    },
    VARIANT_RETEST_ONLY: {
        "label": "Retest-only",
        "trend": "LH/LL",
        "bos": "bearish BOS",
        "htf": "1h+4h bearish",
        "entry": "broken-level retest",
        "stop": "current",
        "tp": "current",
        "fees": "live model",
        "combination_id": "COMBO_02",
    },
    VARIANT_STRONG_BEAR: {
        "label": "Strong-bear filter",
        "trend": "LH/LL",
        "bos": "bearish BOS",
        "htf": "1h+4h bearish",
        "entry": "current",
        "stop": "current",
        "tp": "current",
        "fees": "live model",
        "combination_id": "COMBO_02",
    },
    VARIANT_WIDER_STOP: {
        "label": "Wider stop",
        "trend": "LH/LL",
        "bos": "bearish BOS",
        "htf": "1h+4h bearish",
        "entry": "current",
        "stop": "ATR buffer",
        "tp": "current",
        "fees": "live model",
        "combination_id": "COMBO_02",
    },
    VARIANT_SUPPORT_TP: {
        "label": "Support TP",
        "trend": "LH/LL",
        "bos": "bearish BOS",
        "htf": "1h+4h bearish",
        "entry": "current",
        "stop": "current",
        "tp": "support",
        "fees": "live model",
        "combination_id": "COMBO_02",
    },
    VARIANT_NO_HTF: {
        "label": "No-HTF variant",
        "trend": "LH/LL",
        "bos": "bearish BOS",
        "htf": "none",
        "entry": "current",
        "stop": "current",
        "tp": "current",
        "fees": "live model",
        "combination_id": "COMBO_02_LOCAL",
    },
}

# Entry classification thresholds (ATR multiples / bars) — research diagnostics only.
LATE_EXTENSION_ATR = 0.75
CHASE_BODY_ATR = 1.5
CHASE_BEAR_BARS = 3
RETEST_DISTANCE_ATR = 0.35
EARLY_ZONE_ATR = 0.25
STOP_TIGHT_ATR = 0.6
STOP_WIDE_ATR = 2.5
NEAR_SUPPORT_ATR = 1.0
HIGH_VOL_ATR_PERCENTILE = 0.75
LOW_VOL_ATR_PERCENTILE = 0.25

# Safeguards stamped on every diagnostic artifact.
SAFETY_STAMPS = {
    "strategy_id": STRATEGY_ID,
    "diagnostic_strategy_id": DIAGNOSTIC_STRATEGY_ID,
    "combo_version": COMBO_VERSION,
    "source": DIAGNOSTIC_SOURCE,
    "parent_source": SOURCE,
    "direction": DIRECTION,
    "paper_eligible": PAPER_ELIGIBLE,
    "production_approved": PRODUCTION_APPROVED,
    "telegram_eligible": TELEGRAM_ELIGIBLE,
    "v1_unchanged": True,
    "short_paper_disabled": True,
    "short_production_disabled": True,
    "short_telegram_disabled": True,
    "research_only": True,
}

DEFAULT_UNIVERSE = DEFAULT_SHORT_RESEARCH_UNIVERSE
