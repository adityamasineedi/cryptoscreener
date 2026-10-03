"""Identity constants for the dynamic COMBO_02 v2 research candidate pipeline.

Never conflate with frozen COMBO_02 v1 (BTC/ETH/SOL). All dynamic records must
carry these identifiers so Telegram / paper / UI gates fail closed for v1.
"""

from __future__ import annotations

STRATEGY_ID = "COMBO_02_V2_RESEARCH"
COMBO_VERSION = "v2-research"
COMBO_ID = "COMBO_02"
SOURCE_PIPELINE = "DYNAMIC_CANDIDATE_PIPELINE"
SOURCE_WATCHER = "V2_CANDIDATE_PAPER_WATCHER"
PATH_LABEL = "A"

# Hard defaults — operator approval may set risk only within caps.
DEFAULT_RISK_PERCENT = 0.0
DEFAULT_REQUESTED_RISK_PERCENT = 0.0025  # 0.25%
MAX_RISK_PERCENT_WITHOUT_OVERRIDE = 0.005  # 0.5%
TELEGRAM_ELIGIBLE_DEFAULT = False

SELECTOR_VERSION = "v2-dynamic-discovery"
EXPERIMENTAL_LABEL = "EXPERIMENTAL — NOT COMBO_02 v1"
DISCLAIMER = (
    "Frozen COMBO_02 v1 remains BTC/ETH/SOL only. Dynamic screener candidates "
    "cannot create v1 paper trades, cannot become Telegram eligible, and cannot "
    "be promoted without explicit operator approval and separate v2 validation."
)
