"""Live ↔ backtest entry parity research package.

Research / paper validation only. Does not enable live orders, production
Telegram, or modify COMBO_02 / V1 strategy rules.
"""

from app.research.live_backtest_parity.constants import (
    DISCLAIMER,
    LIVE_BACKTEST_MATCH,
    LIVE_BACKTEST_MISMATCH,
)
from app.research.live_backtest_parity.runner import (
    run_parity_validation,
    run_parity_validation_sync,
)

__all__ = [
    "DISCLAIMER",
    "LIVE_BACKTEST_MATCH",
    "LIVE_BACKTEST_MISMATCH",
    "run_parity_validation",
    "run_parity_validation_sync",
]
