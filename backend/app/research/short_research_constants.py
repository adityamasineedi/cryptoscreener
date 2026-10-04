"""COMBO_02 SHORT research-only identity.

Never conflate with frozen COMBO_02 v1 (BTC/ETH/SOL LONG) or dynamic v2 paper.
Production approval, Telegram, and paper trading remain disabled.
"""

from __future__ import annotations

STRATEGY_ID = "COMBO_02_SHORT_RESEARCH"
COMBO_VERSION = "v2-short-research"
COMBO_ID = "COMBO_02"
SOURCE = "SHORT_RESEARCH_PIPELINE"
DIRECTION = "SHORT"
PATH_LABEL = "A"
SETUP_TIMEFRAME = "1h"

PRODUCTION_APPROVED = False
TELEGRAM_ELIGIBLE = False
PAPER_ELIGIBLE = False

# Research lifecycle (no paper / production terminals).
SHORT_RESEARCH_STATES = frozenset(
    {
        "DISCOVERED",
        "DATA_PENDING",
        "DATA_READY",
        "BACKTEST_COMPLETED",
        "RESEARCH_REJECTED",
        "PROMISING",
        "OOS_FAILED",
        "SHORT_RESEARCH_CANDIDATE",
    }
)
TERMINAL_PASS_STATE = "SHORT_RESEARCH_CANDIDATE"
FORBIDDEN_PAPER_STATES = frozenset(
    {"V2_PAPER_CANDIDATE", "PAPER_VALIDATING", "PRODUCTION_APPROVED", "APPROVED"}
)

REJECTION_REASON = "short_research_only"

DISCLAIMER = (
    "COMBO_02 SHORT research only. Not COMBO_02 v1. Not paper-eligible. "
    "Not Telegram-eligible. Not production-approved. Bearish HTF + SHORT "
    "geometry required. Existing SHORT backtests are not profitability claims."
)

STRATEGY_FINGERPRINT_TEXT = (
    "COMBO_02 SHORT RESEARCH, 1h setup, 1h+4h bearish HTF_ALIGNED, "
    "LH/LL + BEARISH_BOS, stop above entry, TP below entry. "
    "Research metrics only — never v1 / never paper / never Telegram."
)

# Research-only universe — never joins frozen COMBO_02 v1 books.
DEFAULT_SHORT_RESEARCH_UNIVERSE: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "LINKUSDT",
    "SUIUSDT",
    "XRPUSDT",
    "DOGEUSDT",
)

RESEARCH_SIMULATION_LABEL = "RESEARCH SIMULATION ONLY"
NO_POSITION_CREATED_LABEL = "NO LIVE OR PAPER POSITION CREATED"
