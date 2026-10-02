"""Confirmed Break Of Structure — close beyond swing, never wick-only."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr as calc_atr
from app.signals._candle_utils import candle_time, ohlc, series_ohlcv, volume_at
from app.signals.schemas import EventState, SwingRecord, TrendState


def detect_bos(
    candles: Sequence[Mapping[str, Any]],
    swings: list[SwingRecord],
    trend: dict[str, Any],
    *,
    symbol: str,
    timeframe: str,
    atr_period: int = 14,
    rvol: float | None = None,
    as_of_index: int | None = None,
) -> dict[str, Any] | None:
    """Bullish BOS: close > confirmed swing high while trend bullish.
    Bearish BOS: close < confirmed swing low while trend bearish.

    CHOCH is handled separately — this only emits BOS when structure aligns.
    """
    if not candles or not swings:
        return None
    end = len(candles) - 1 if as_of_index is None else min(as_of_index, len(candles) - 1)
    if end < 1:
        return None

    highs_sw = [s for s in swings if s.swing_type == "HIGH" and s.bar_index < end]
    lows_sw = [s for s in swings if s.swing_type == "LOW" and s.bar_index < end]
    if not highs_sw or not lows_sw:
        return None

    _, highs, lows, closes, vols = series_ohlcv(list(candles[: end + 1]))
    atr_val = calc_atr(highs, lows, closes, atr_period)
    o, h, l, c = ohlc(candles, end)
    t = candle_time(candles[end])
    vol = volume_at(candles, end)
    trend_state = str(trend.get("trend") or "")

    last_sh = highs_sw[-1]
    last_sl = lows_sw[-1]

    # Scan from oldest to newest after swing confirmation for first BOS close
    # but report latest active BOS relative to as-of.
    if trend_state == TrendState.BULLISH.value and c > last_sh.price:
        # Ensure wick-only does not count: require close above level
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": "BULLISH_BOS",
            "state": EventState.CONFIRMED.value,
            "broken_level": last_sh.price,
            "break_price": c,
            "break_timestamp": t.isoformat() if t else None,
            "confirmation_candle": end,
            "volume": vol,
            "rvol": rvol,
            "atr": atr_val,
            "impulse_candidate": True,
            "reason": f"{timeframe.upper()} candle closed above {last_sh.price} swing high.",
        }

    if trend_state == TrendState.BEARISH.value and c < last_sl.price:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": "BEARISH_BOS",
            "state": EventState.CONFIRMED.value,
            "broken_level": last_sl.price,
            "break_price": c,
            "break_timestamp": t.isoformat() if t else None,
            "confirmation_candle": end,
            "volume": vol,
            "rvol": rvol,
            "atr": atr_val,
            "impulse_candidate": True,
            "reason": f"{timeframe.upper()} candle closed below {last_sl.price} swing low.",
        }

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": None,
        "state": EventState.NONE.value,
        "broken_level": None,
        "break_price": None,
        "break_timestamp": None,
        "confirmation_candle": None,
        "volume": vol,
        "rvol": rvol,
        "atr": atr_val,
        "impulse_candidate": False,
        "reason": "No confirmed BOS on close vs aligned structure",
        "last_swing_high": last_sh.price,
        "last_swing_low": last_sl.price,
        "close": c,
        "high": h,
        "low": l,
        "open": o,
        "avg_volume_ref": (sum(vols[-20:]) / min(20, len(vols))) if vols else None,
    }
