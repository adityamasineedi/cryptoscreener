"""Confirmed Break Of Structure — close beyond swing, never wick-only."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr as calc_atr
from app.signals._candle_utils import candle_time, ohlc, series_ohlcv, volume_at
from app.signals.config import SignalConfig
from app.signals.impulse_engine import detect_impulse
from app.signals.schemas import EventState, SwingRecord, TrendState

_IMPULSE_DEFAULTS = SignalConfig()


def _measured_impulse_candidate(
    candles: Sequence[Mapping[str, Any]],
    *,
    bos_direction: str,
    end: int,
    atr_val: float | None,
    rvol: float | None,
    volumes: Sequence[float] | None,
) -> bool:
    """True only when the break candle meets impulse range, body, and RVOL.

    Uses the same defaults as ``detect_impulse`` (ATR multiple, body ratio,
    relative volume). A close beyond the swing is not enough.
    """
    measured = detect_impulse(
        candles,
        {"direction": bos_direction},
        _IMPULSE_DEFAULTS,
        rvol=rvol,
        as_of_index=end,
        atr_value=atr_val,
        volumes=volumes,
    )
    return bool(measured.get("is_impulse"))


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
    atr_value: float | None = None,
    volumes: Sequence[float] | None = None,
    avg_volume_ref: float | None = None,
    high_swings: Sequence[SwingRecord] | None = None,
    low_swings: Sequence[SwingRecord] | None = None,
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

    # Research feature builders maintain already-confirmed per-side lists. Using
    # them avoids filtering the full accumulated swing history on every bar.
    highs_sw = (
        high_swings
        if high_swings is not None
        else [s for s in swings if s.swing_type == "HIGH" and s.bar_index < end]
    )
    lows_sw = (
        low_swings
        if low_swings is not None
        else [s for s in swings if s.swing_type == "LOW" and s.bar_index < end]
    )
    if not highs_sw or not lows_sw:
        return None

    if atr_value is None:
        _, highs, lows, closes, _ = series_ohlcv(candles[: end + 1])
        atr_val = calc_atr(highs, lows, closes, atr_period)
    else:
        atr_val = atr_value
    o, h, l, c = ohlc(candles, end)
    t = candle_time(candles[end])
    vol = volume_at(candles, end)
    trend_state = str(trend.get("trend") or "")

    last_sh = highs_sw[-1]
    last_sl = lows_sw[-1]

    # Scan from oldest to newest after swing confirmation for first BOS close
    # but report latest active BOS relative to as-of.
    if trend_state == TrendState.BULLISH.value and c > last_sh.price:
        # Ensure wick-only does not count: require close above level.
        # Impulse is a separate measurement (range, body, volume), not the break itself.
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
            "impulse_candidate": _measured_impulse_candidate(
                candles,
                bos_direction="BULLISH_BOS",
                end=end,
                atr_val=atr_val,
                rvol=rvol,
                volumes=volumes,
            ),
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
            "impulse_candidate": _measured_impulse_candidate(
                candles,
                bos_direction="BEARISH_BOS",
                end=end,
                atr_val=atr_val,
                rvol=rvol,
                volumes=volumes,
            ),
            "reason": f"{timeframe.upper()} candle closed below {last_sl.price} swing low.",
        }

    if avg_volume_ref is None:
        if volumes is not None:
            prefix_end = min(end + 1, len(volumes))
            window_start = max(0, prefix_end - 20)
            volume_window = volumes[window_start:prefix_end]
            avg_volume_ref = (
                sum(float(value) for value in volume_window) / len(volume_window)
                if len(volume_window)
                else None
            )
        else:
            # Non-backtest callers do not provide the precomputed volume series.
            # Only the final 20 values are needed for this diagnostic field.
            window_start = max(0, end + 1 - 20)
            volume_window = [
                volume_at(candles, i) for i in range(window_start, end + 1)
            ]
            avg_volume_ref = (
                sum(volume_window) / len(volume_window) if volume_window else None
            )

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
        "avg_volume_ref": avg_volume_ref,
    }
