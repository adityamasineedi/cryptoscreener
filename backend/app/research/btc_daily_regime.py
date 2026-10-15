"""BTC daily trend used as the market regime.

Confirmed swings on closed daily bars, classified by the legacy last-two-swing
rule. No I/O and no store access.
"""

from __future__ import annotations

from datetime import timezone
from typing import Any, Mapping, Sequence

from app.signals._candle_utils import candle_time
from app.signals.config import SignalConfig
from app.signals.swing_detector import detect_swings
from app.signals.trend_engine import infer_trend

REGIME_SYMBOL = "BTCUSDT"
REGIME_TIMEFRAME = "1d"
MIN_DAILY_BARS = 40

BULLISH = "BULLISH"
BEARISH = "BEARISH"
NEUTRAL = "NEUTRAL"


def _as_of_bar(candles: Sequence[Mapping[str, Any]]) -> str:
    if not candles:
        return ""
    ts = candle_time(candles[-1])
    if ts is None:
        return ""
    return ts.astimezone(timezone.utc).isoformat()


def daily_trend(
    candles_1d: Sequence[Mapping[str, Any]],
    signal_config: SignalConfig,
) -> dict[str, Any]:
    """Trend as of the last bar in ``candles_1d``.

    The caller must pass closed daily bars only, oldest first. Returns
    ``trend`` (BULLISH, BEARISH, or NEUTRAL), ``as_of_bar`` (ISO UTC open time
    of the last bar, "" when there are none), and ``bars``. Fewer than
    ``MIN_DAILY_BARS`` bars is NEUTRAL.
    """
    candles = list(candles_1d or [])
    out = {"trend": NEUTRAL, "as_of_bar": _as_of_bar(candles), "bars": len(candles)}
    if len(candles) < MIN_DAILY_BARS:
        return out
    cfg = signal_config.swing_for(REGIME_TIMEFRAME)
    swings = detect_swings(
        candles,
        left=cfg.swing_left_bars,
        right=cfg.swing_right_bars,
        symbol=REGIME_SYMBOL,
        timeframe=REGIME_TIMEFRAME,
        as_of_index=len(candles) - 1,
    )
    trend = str(infer_trend(list(swings)).get("trend") or "")
    if trend in {BULLISH, BEARISH}:
        out["trend"] = trend
    return out
