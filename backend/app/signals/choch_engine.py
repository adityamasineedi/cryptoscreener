"""Change Of Character — distinct from BOS.

Rules:
- CHOCH_BULLISH: prior structure was Lower High → Lower Low (bearish), then
  candle CLOSES above the relevant confirmed lower high.
- CHOCH_BEARISH: prior structure was Higher High → Higher Low (bullish), then
  candle CLOSES below the relevant confirmed higher low.

BOS requires trend alignment; CHOCH marks a character shift against prior trend.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.signals._candle_utils import candle_time, ohlc
from app.signals.schemas import EventState, SwingRecord, TrendState


def detect_choch(
    candles: Sequence[Mapping[str, Any]],
    swings: list[SwingRecord],
    trend: dict[str, Any],
    *,
    symbol: str,
    timeframe: str,
    as_of_index: int | None = None,
) -> dict[str, Any] | None:
    if not candles or not swings:
        return None
    end = len(candles) - 1 if as_of_index is None else min(as_of_index, len(candles) - 1)
    highs_sw = [s for s in swings if s.swing_type == "HIGH" and s.bar_index < end]
    lows_sw = [s for s in swings if s.swing_type == "LOW" and s.bar_index < end]
    if not highs_sw or not lows_sw:
        return None

    _, _, _, c = ohlc(candles, end)
    t = candle_time(candles[end])
    trend_state = str(trend.get("trend") or "")
    last_sh = highs_sw[-1]
    last_sl = lows_sw[-1]

    # Bullish CHOCH against bearish / LH+LL context
    if trend_state in (TrendState.BEARISH.value, TrendState.NEUTRAL.value):
        if last_sh.label == "LH" and c > last_sh.price:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": "CHOCH_BULLISH",
                "state": EventState.CONFIRMED.value,
                "level": last_sh.price,
                "break_price": c,
                "break_timestamp": t.isoformat() if t else None,
                "confirmation_candle": end,
                "reason": (
                    "Previous LH→LL structure; close above confirmed lower high "
                    f"{last_sh.price}."
                ),
            }

    # Bearish CHOCH against bullish / HH+HL context
    if trend_state in (TrendState.BULLISH.value, TrendState.NEUTRAL.value):
        if last_sl.label == "HL" and c < last_sl.price:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": "CHOCH_BEARISH",
                "state": EventState.CONFIRMED.value,
                "level": last_sl.price,
                "break_price": c,
                "break_timestamp": t.isoformat() if t else None,
                "confirmation_candle": end,
                "reason": (
                    "Previous HH→HL structure; close below confirmed higher low "
                    f"{last_sl.price}."
                ),
            }

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": None,
        "state": EventState.NONE.value,
        "level": None,
        "break_price": None,
        "reason": "No CHOCH on close vs opposite structure",
    }
