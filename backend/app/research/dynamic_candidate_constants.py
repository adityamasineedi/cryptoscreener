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
# Default requested risk: 0.25%. No override → max 0.25%. Explicit override → max 0.50%.
# Hard absolute cap: 0.50%. Override never permits risk above 0.50%.
DEFAULT_RISK_PERCENT = 0.0
DEFAULT_REQUESTED_RISK_PERCENT = 0.0025  # 0.25%
MAX_RISK_PERCENT_WITHOUT_OVERRIDE = 0.0025  # 0.25% default cap
MAX_RISK_PERCENT_WITH_OVERRIDE = 0.005  # 0.5% project max (needs explicit override)
DEFAULT_RISK = DEFAULT_REQUESTED_RISK_PERCENT
MAX_DEFAULT_RISK = MAX_RISK_PERCENT_WITHOUT_OVERRIDE
MAX_OVERRIDE_RISK = MAX_RISK_PERCENT_WITH_OVERRIDE
TELEGRAM_ELIGIBLE_DEFAULT = False
PRODUCTION_APPROVED_DEFAULT = False

# Explicit pass values required for paper approval (never mere artifact presence).
BACKTEST_PASS_STATUSES = frozenset({"COMPLETED", "PASS"})
# Pipeline stores V2_PAPER_CANDIDATE as the OOS-pass label; PASS/COMPLETED_PASS are aliases.
OOS_PASS_STATUSES = frozenset({"PASS", "COMPLETED_PASS", "V2_PAPER_CANDIDATE"})
PORTFOLIO_PASS_STATUSES = frozenset({"PASS", "REVIEWED_PASS"})
BLOCKED_PORTFOLIO_RISK_CAP = "blocked_portfolio_risk_cap"

SELECTOR_VERSION = "v2-dynamic-discovery"
EXPERIMENTAL_LABEL = "EXPERIMENTAL — NOT COMBO_02 v1"
DISCLAIMER = (
    "Frozen COMBO_02 v1 remains BTC/ETH/SOL only. Dynamic screener candidates "
    "cannot create v1 paper trades, cannot become Telegram eligible, and cannot "
    "be promoted without explicit operator approval and separate v2 validation."
)
