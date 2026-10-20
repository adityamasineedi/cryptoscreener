"""Strategy sides that passed the 10 Oct large/mid cap book.

A side passes when its combined net is above $0 and it has at least 5 trades.
Paper uses this list only. It does not change entry, stop, or target rules.
"""

from __future__ import annotations

LARGE = (
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "TRXUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "BCHUSDT",
    "LTCUSDT",
    "TONUSDT",
)
MID = (
    "NEARUSDT",
    "ATOMUSDT",
    "APTUSDT",
    "ARBUSDT",
    "OPUSDT",
    "SUIUSDT",
    "INJUSDT",
    "AAVEUSDT",
    "UNIUSDT",
    "FILUSDT",
    "ETCUSDT",
    "ICPUSDT",
    "RENDERUSDT",
)

# combination_id, timeframe, direction
# COMBO_02_V2_1B and COMBO_02_V2_1_A SHORT were disarmed on 2026-10-20 by the owner
# (research audit ruling D3, PROPOSED_LIVE_CHANGES P5): their selection backtest read
# the 4h bar the setup hour sits in before it closed.
PASSED_SIDES: tuple[tuple[str, str, str], ...] = (
    ("COMBO_02_V2", "1h", "SHORT"),
    ("COMBO_02", "1h", "SHORT"),
    ("COMBO_02_CLOSED_HTF", "1h", "SHORT"),
    ("COMBO_03_TRANSITION", "1h", "SHORT"),
    ("COMBO_03_TRANSITION_B", "1h", "SHORT"),
    ("COMBO_03_TRANSITION_B", "1h", "LONG"),
    ("COMBO_01", "1h", "SHORT"),
    ("COMBO_02_LOCAL", "1h", "SHORT"),
)

SOURCE_WATCHER = "PASSED_CAP_BOOK"
PRINCIPAL_USD = 1000.0


def cap_symbols() -> tuple[str, ...]:
    return LARGE + MID


def cap_group(symbol: str) -> str:
    return "large" if str(symbol or "").upper() in LARGE else "mid"


def passed_stream(combination_id: str, direction: str) -> str:
    return f"PASSED|{str(combination_id).upper()}|{str(direction).upper()}"


def is_passed_side(combination_id: str, timeframe: str, direction: str) -> bool:
    key = (
        str(combination_id or ""),
        str(timeframe or "").lower(),
        str(direction or "").upper(),
    )
    return key in PASSED_SIDES


def passed_risk_percent(symbol: str, direction: str, market: str) -> float:
    """Same stake as the cap book: $20 large / $10 mid on $1,000, halved against BTC daily."""
    stake = 0.02 if cap_group(symbol) == "large" else 0.01
    side = str(direction or "").upper()
    trend = str(market or "").upper()
    if trend == "BULLISH" and side == "SHORT":
        return stake * 0.5
    if trend == "BEARISH" and side == "LONG":
        return stake * 0.5
    return stake
