"""COMBO_02 SHORT entry-timing research — research-only identity.

Never enables paper, live, production, Telegram, or COMBO_02 v1 SHORT.
"""

from __future__ import annotations

from typing import Any

STRATEGY_ID = "COMBO_02_SHORT_ENTRY_RESEARCH"
COMBO_VERSION = "v2-short-entry-research"
SOURCE = "SHORT_ENTRY_DIAGNOSTIC_PIPELINE"
DIRECTION = "SHORT"
COMBO_ID = "COMBO_02"

PARENT_STRATEGY_ID = "COMBO_02_SHORT_RESEARCH"
PARENT_RUN_ID = "20261004T060446Z-b5d71081"

PAPER_ELIGIBLE = False
PRODUCTION_APPROVED = False
TELEGRAM_ELIGIBLE = False

# Entry variants
VARIANT_BASELINE = "baseline"
VARIANT_RETEST = "retest"
VARIANT_EXTENSION_FILTER = "extension_filter"
VARIANT_CHASING_EXCLUSION = "chasing_exclusion"

# Stop variants (research-only)
STOP_CURRENT = "stop_current_swing_high"
STOP_SWING_ATR = "stop_swing_high_atr_buffer"
STOP_BOS_HIGH_ATR = "stop_bos_candle_high_atr_buffer"
STOP_FIXED_ATR = "stop_fixed_atr"

# TP variants (research-only)
TP_CURRENT = "tp_current"
TP_SUPPORT = "tp_nearby_support"
TP_FIXED_1R = "tp_fixed_1_0R"
TP_FIXED_15R = "tp_fixed_1_5R"
TP_FIXED_2R = "tp_fixed_2_0R"

ENTRY_EARLY = "EARLY"
ENTRY_RETEST = "RETEST"
ENTRY_IMMEDIATE_BREAK = "IMMEDIATE_BREAK"
ENTRY_LATE = "LATE"
ENTRY_CHASING = "CHASING"

STOP_TOO_TIGHT = "TOO_TIGHT"
STOP_GOOD = "GOOD"
STOP_TOO_WIDE = "TOO_WIDE"

# Configurable filter defaults (never hard-coded inside signal engines).
DEFAULT_FILTER_THRESHOLDS: dict[str, float | int] = {
    "chase_extension_atr": 0.75,
    "late_delay_bars": 2,
    "late_extension_atr": 0.75,
    "early_extension_atr": 0.25,
    "extension_filter_max_atr": 0.50,
    "extension_filter_max_delay_bars": 1,
    "retest_expiry_bars": 12,
    "retest_tolerance_atr": 0.25,
    "stop_atr_buffer": 0.5,
    "fixed_atr_stop_mult": 1.5,
    "stop_tight_atr": 0.6,
    "stop_wide_atr": 2.5,
}

DISCLAIMER = (
    "COMBO_02 SHORT entry-timing research only. Not COMBO_02 v1. "
    "Not paper-eligible. Not Telegram-eligible. Not production-approved. "
    "Exploratory variants require untouched OOS validation."
)

STRATEGY_FINGERPRINT_TEXT = (
    "COMBO_02 SHORT ENTRY RESEARCH, 1h setup, bearish HTF, "
    "entry-timing / retest / extension / chasing-exclusion variants. "
    "Research metrics only — never v1 / never paper / never Telegram."
)

SAFETY_STAMPS: dict[str, Any] = {
    "strategy_id": STRATEGY_ID,
    "combo_version": COMBO_VERSION,
    "source": SOURCE,
    "direction": DIRECTION,
    "combo_id": COMBO_ID,
    "parent_strategy_id": PARENT_STRATEGY_ID,
    "parent_run_id": PARENT_RUN_ID,
    "paper_eligible": False,
    "production_approved": False,
    "telegram_eligible": False,
    "v1_unchanged": True,
    "short_paper_disabled": True,
    "short_live_disabled": True,
    "short_production_disabled": True,
    "short_telegram_disabled": True,
    "research_only": True,
}

# Windows matching parent diagnostic / fee-fixed SHORT batch.
DEFAULT_ENTRY_RESEARCH_WINDOWS = {
    "requested_start": "2024-01-01",
    "requested_end": "2026-09-30",
    "base_start": "2024-01-01",
    "base_end": "2025-06-30",
    "oos_dev_start": "2025-07-01",
    "oos_dev_end": "2025-12-31",
    "oos_val_start": "2026-01-01",
    "oos_val_end": "2026-09-30",
    "policy": "POLICY_A_STRICT_DISJOINT",
}
