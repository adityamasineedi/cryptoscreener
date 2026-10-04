"""Research-only live ↔ backtest parity constants.

Never enables live orders, production Telegram, or dynamic Telegram.
Does not alter COMBO_02 parameters or V1 production identity.
"""

from __future__ import annotations

STRATEGY_ID = "LIVE_BACKTEST_PARITY_RESEARCH"
SOURCE = "LIVE_BACKTEST_PARITY"
COMBO_ID = "COMBO_02"
SETUP_TIMEFRAME = "15m"
HTF_TIMEFRAMES = ("1h", "4h")

DEFAULT_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")

# Entry price check states (descriptive; not a trading recommendation).
ENTRY_PRICE_NOT_CHECKED = "NOT_CHECKED"
ENTRY_PRICE_VALID = "VALID"
ENTRY_PRICE_STALE = "STALE"
ENTRY_PRICE_UNAVAILABLE = "UNAVAILABLE"
STALE_ENTRY = "STALE_ENTRY"

# Parity outcomes
LIVE_BACKTEST_MATCH = "LIVE_BACKTEST_MATCH"
LIVE_BACKTEST_MISMATCH = "LIVE_BACKTEST_MISMATCH"

# Entry triad keys — never overwrite one with another.
BACKTEST_ENTRY = "backtest_entry"
LIVE_SIGNAL_PRICE = "live_signal_price"
PAPER_ENTRY = "paper_entry"

# Descriptive deviation buckets (pct). No bucket is declared valid/invalid.
DEVIATION_BUCKETS: tuple[tuple[str, float | None, float | None], ...] = (
    ("0-0.02%", 0.0, 0.02),
    ("0.02-0.05%", 0.02, 0.05),
    ("0.05-0.10%", 0.05, 0.10),
    ("0.10-0.20%", 0.10, 0.20),
    ("0.20-0.50%", 0.20, 0.50),
    (">0.50%", 0.50, None),
)

# Default research-only threshold (percent). Configurable via settings/env.
DEFAULT_ENTRY_DEVIATION_THRESHOLD_PCT = 0.10

PRICE_SOURCE_MARK = "mark_price"
PRICE_SOURCE_TICKER = "ticker_price"
PRICE_SOURCE_CLOSE = "candle_close"
PRICE_SOURCE_UNAVAILABLE = "unavailable"

PRODUCTION_APPROVED = False
TELEGRAM_ELIGIBLE = False
LIVE_ORDERS_ENABLED = False

DISCLAIMER = (
    "LIVE↔BACKTEST PARITY RESEARCH ONLY. No live orders. "
    "No dynamic production Telegram. No COMBO_02 parameter changes. "
    "No V1 modifications. Descriptive entry deviation only."
)
