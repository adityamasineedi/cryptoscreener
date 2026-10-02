"""Explainable market-structure trading-analysis engine.

States: ENTRY_CANDIDATE / NO_SETUP / WAITING / INVALIDATED / CONFLICT
Never claims profitability. Never fabricates production market data.
"""

from app.signals.config import SignalConfig
from app.signals.market_signal_engine import classify_market_signal
from app.signals.signal_engine import SignalEngine
from app.signals.schemas import SetupAnalysis, SignalStatus

__all__ = [
    "SignalConfig",
    "SignalEngine",
    "SetupAnalysis",
    "SignalStatus",
    "classify_market_signal",
]
