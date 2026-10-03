"""Documented research-only classification thresholds.

These are DESCRIPTIVE labels for forensics. They are NOT production thresholds
and MUST NOT be copied into live Trade Plan / signal engines.
"""

from __future__ import annotations

# Sample-size caution (Phase 19)
SAMPLE_INSUFFICIENT_MAX = 29
SAMPLE_LIMITED_MAX = 99

# Entry timing vs bars_since_BOS (setup TF bars) — research classification only.
# EARLY: entry on the BOS confirmation bar (bars_since_BOS == 0)
# TIMELY: 1–3 bars after BOS
# LATE: 4–8 bars after BOS
# VERY_LATE: > 8 bars after BOS
TIMING_EARLY_MAX = 0
TIMING_TIMELY_MAX = 3
TIMING_LATE_MAX = 8

# BOS distance (ATR multiples) — research classification only.
ENTRY_AT_BOS_ATR_MAX = 0.25
ENTRY_AFTER_BOS_ATR_MAX = 1.0
# Beyond this → ENTRY_TOO_FAR_AFTER_BOS
ENTRY_TOO_FAR_BOS_ATR = 1.0

# Regime (research-only descriptive)
REGIME_ATR_PCT_EXPANSION = 2.5  # ATR% of price
REGIME_ATR_PCT_CONTRACTION = 0.8
REGIME_RANGE_ATR_SIDEWAYS_MAX = 3.5
REGIME_DIR_CHANGES_CHOP_MIN = 4
REGIME_TREND_STRENGTH_MIN = 0.45
REGIME_BOS_FREQ_BREAKOUT_MIN = 0.08  # BOS events / bars in lookback

# Liquidity sweep (research heuristic on prior swing)
SWEEP_WEAK_ATR_MAX = 0.15
SWEEP_DEEP_ATR_MIN = 0.75

# SL tightness vs ATR
SL_TIGHT_ATR_MAX = 0.6
SL_WIDE_ATR_MIN = 2.0

# Session UTC hour bins
SESSION_BINS = (
    ("00-04", range(0, 4)),
    ("04-08", range(4, 8)),
    ("08-12", range(8, 12)),
    ("12-16", range(12, 16)),
    ("16-20", range(16, 20)),
    ("20-24", range(20, 24)),
)

# Optional session labels (UTC approximations; documented)
# Asia ~ 00-08, London ~ 07-16, New York ~ 13-22 (overlap possible)
SESSION_REGION_RULES = (
    ("ASIA_UTC", range(0, 8)),
    ("LONDON_UTC", range(7, 16)),
    ("NEW_YORK_UTC", range(13, 22)),
)

HTF_TFS = ("5m", "15m", "1h", "4h", "1d")

SAFETY_NO_PROD_CHANGE = "NO PRODUCTION TRADING LOGIC WAS CHANGED."
SAFETY_NO_STRATEGY_SELECT = (
    "THIS REPORT IDENTIFIES ASSOCIATIONS/FAILURE CHARACTERISTICS. "
    "IT DOES NOT SELECT OR RECOMMEND A NEW TRADING STRATEGY."
)
