"""Scalping extension for the existing intraday research pipeline.

The frozen ``INTRADAY_BOS_SD_V1`` specification remains unchanged.  These
independently versioned engines reuse its candle/time infrastructure but keep
separate registrations and validation evidence because their rules, targets,
holding periods, and execution timeframe differ.
"""

from app.research.scalping.backtest import run_backtest
from app.research.scalping.catalog import SCALPING_STRATEGIES
from app.research.scalping.engine import ScalpingStrategyEngine
from app.research.scalping.validation import validation_registry

__all__ = [
    "SCALPING_STRATEGIES",
    "ScalpingStrategyEngine",
    "run_backtest",
    "validation_registry",
]
