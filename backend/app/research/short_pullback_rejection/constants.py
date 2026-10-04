"""SHORT pullback-rejection research — research-only identity.

Completely separate from COMBO_02 v1 (LONG) and COMBO_02_SHORT_RESEARCH (BOS entry).
Never enables paper, live, production, or Telegram.
"""

from __future__ import annotations

from typing import Any

STRATEGY_ID = "SHORT_PULLBACK_REJECTION_RESEARCH"
COMBO_VERSION = "v1-short-pullback-rejection"
SOURCE = "SHORT_RESEARCH_PIPELINE"
DIRECTION = "SHORT"
COMBO_ID = "COMBO_02"  # research lineage only — not v1 production

PAPER_ELIGIBLE = False
PRODUCTION_APPROVED = False
TELEGRAM_ELIGIBLE = False

# Rejection confirmation types (deterministic).
REJECTION_BEARISH_ENGULFING = "BEARISH_ENGULFING"
REJECTION_UPPER_WICK = "UPPER_WICK_REJECTION"
REJECTION_LOWER_HIGH_FAILURE = "LOWER_HIGH_FAILURE"
REJECTION_BEARISH_MICRO_BOS = "BEARISH_MICRO_BOS"

REJECTION_TYPES = (
    REJECTION_BEARISH_ENGULFING,
    REJECTION_UPPER_WICK,
    REJECTION_LOWER_HIGH_FAILURE,
    REJECTION_BEARISH_MICRO_BOS,
)

# Stop buffer ATR variants (research-only).
STOP_BUFFER_0 = "stop_buffer_0_0"
STOP_BUFFER_025 = "stop_buffer_0_25"
STOP_BUFFER_050 = "stop_buffer_0_50"

STOP_BUFFER_BY_VARIANT: dict[str, float] = {
    STOP_BUFFER_0: 0.0,
    STOP_BUFFER_025: 0.25,
    STOP_BUFFER_050: 0.50,
}

# TP mode variants (research-only).
TP_NEAREST_SUPPORT = "nearest_support"
TP_FIXED_1R = "1.0R"
TP_FIXED_15R = "1.5R"
TP_FIXED_2R = "2.0R"

TP_R_BY_VARIANT: dict[str, float | None] = {
    TP_NEAREST_SUPPORT: None,
    TP_FIXED_1R: 1.0,
    TP_FIXED_15R: 1.5,
    TP_FIXED_2R: 2.0,
}

FORBIDDEN_PAPER_STATES = frozenset(
    {"V2_PAPER_CANDIDATE", "PAPER_VALIDATING", "PRODUCTION_APPROVED", "APPROVED"}
)

DEFAULT_THRESHOLDS: dict[str, float | int] = {
    "atr_period": 14,
    "swing_left": 2,
    "swing_right": 2,
    "zone_width_atr": 0.35,
    "retest_tolerance_atr": 0.25,
    "max_entry_extension_atr": 0.75,
    "major_4h_support_atr": 0.50,
    "min_support_distance_atr": 0.35,
    "upper_wick_body_ratio": 1.5,
    "upper_wick_range_frac": 0.55,
    "micro_bos_lookback": 5,
    "pullback_lookback": 40,
    "rejection_expiry_bars": 8,
    "max_hold_bars": 48,
    "risk_usd": 20.0,
    "principal_usd": 1000.0,
    "default_stop_buffer_atr": 0.25,
}

# Fresh experiment split — excludes prior SHORT OOS (2025-05/06) from all partitions.
# Prior SHORT BOS research used base=2025-01..04 / oos_dev=2025-05..06 / oos_val=2025-07+.
# Prior entry research consumed 2025-07+ as OOS. This split is disjoint for tuning.
DEFAULT_PULLBACK_REJECTION_WINDOWS = {
    "requested_start": "2024-01-01",
    "requested_end": "2025-04-30",
    "base_start": "2024-01-01",
    "base_end": "2024-08-31",
    "oos_dev_start": "2024-09-01",
    "oos_dev_end": "2024-12-31",
    "oos_val_start": "2025-01-01",
    "oos_val_end": "2025-04-30",
    "policy": "POLICY_A_STRICT_DISJOINT",
    "oos_policy": "VALIDATION_DETERMINES_STATUS",
    "note": (
        "Fresh pullback-rejection experiment split. "
        "Does not reuse prior SHORT OOS 2025-05-01..2025-06-30 for tuning."
    ),
}

DEFAULT_UNIVERSE: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "LINKUSDT",
    "SUIUSDT",
    "XRPUSDT",
    "DOGEUSDT",
)

DISCLAIMER = (
    "SHORT_PULLBACK_REJECTION_RESEARCH only. Not COMBO_02 v1. "
    "Not a modification of COMBO_02_SHORT_RESEARCH. "
    "Not paper-eligible. Not Telegram-eligible. Not production-approved."
)

STRATEGY_FINGERPRINT_TEXT = (
    "SHORT PULLBACK REJECTION RESEARCH, 4h bearish LH/LL, 1h pullback into "
    "broken S/R, explicit rejection confirmation, stop above rejection swing, "
    "reachable support / R TP. Research metrics only — never v1 / never paper / "
    "never Telegram."
)

SAFETY_STAMPS: dict[str, Any] = {
    "strategy_id": STRATEGY_ID,
    "combo_version": COMBO_VERSION,
    "source": SOURCE,
    "direction": DIRECTION,
    "combo_id": COMBO_ID,
    "paper_eligible": False,
    "production_approved": False,
    "telegram_eligible": False,
    "v1_unchanged": True,
    "combo02_v1_long_only": True,
    "short_paper_disabled": True,
    "short_live_disabled": True,
    "short_production_disabled": True,
    "short_telegram_disabled": True,
    "research_only": True,
    "not_combo02_short_research": True,
}
