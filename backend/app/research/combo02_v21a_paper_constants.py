"""COMBO_02 V2.1-A paper-trading identity — research/paper only, never live orders.

Does not alter VARIANT_A / CHOPPY→WAIT strategy logic.
"""

from __future__ import annotations

STRATEGY_ID = "COMBO_02_V2_1_A"
COMBINATION_ID = "COMBO_02_V2_1_A"
COMBO_VERSION = "v2.1-a-choppy-wait"
SOURCE_WATCHER = "V21A_PAPER_WATCHER"
STREAM_ID = "V21A"
MODE = "PAPER"
SETUP_TIMEFRAME = "1h"

# Default watch universe (same multi-market research set); override via settings.
DEFAULT_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "LINKUSDT",
    "DOGEUSDT",
    "SUIUSDT",
)

# LIVE must never auto-enable for this strategy.
LIVE_EXECUTION_ALLOWED = False
