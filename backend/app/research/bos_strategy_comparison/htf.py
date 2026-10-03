"""Higher-timeframe alignment classification for research (no look-ahead)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.signals._candle_utils import candle_time
from app.signals.config import SignalConfig
from app.signals.swing_detector import swings_for_timeframe
from app.signals.trend_engine import infer_trend

HTF_ALIGNED = "HTF_ALIGNED"
HTF_CONFLICT = "HTF_CONFLICT"
HTF_NEUTRAL_UNAVAILABLE = "HTF_NEUTRAL_UNAVAILABLE"


def as_of_index_at_or_before(
    candles: Sequence[Mapping[str, Any]],
    as_of_ts: datetime | None,
) -> int | None:
    """Largest index with candle_time <= as_of_ts. Never uses future bars."""
    if not candles or as_of_ts is None:
        return None
    if as_of_ts.tzinfo is None:
        as_of_ts = as_of_ts.replace(tzinfo=timezone.utc)
    best: int | None = None
    for i, c in enumerate(candles):
        t = candle_time(c)
        if t is None:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t <= as_of_ts:
            best = i
        else:
            break
    return best


def trend_at_as_of(
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int | None,
    *,
    timeframe: str,
    symbol: str,
    config: SignalConfig | None = None,
    cache: dict[tuple[str, int], str] | None = None,
) -> str:
    """Infer trend using only candles[:as_of_index+1] via confirmed swings."""
    if as_of_index is None or as_of_index < 0 or not candles:
        return "HTF_UNAVAILABLE"
    key = (timeframe, as_of_index)
    if cache is not None and key in cache:
        return cache[key]
    cfg = config or SignalConfig()
    swings = swings_for_timeframe(
        candles,
        cfg,
        timeframe,
        symbol=symbol,
        as_of_index=as_of_index,
    )
    trend = infer_trend(swings)
    label = str(trend.get("trend") or "INSUFFICIENT_DATA").upper()
    if label in ("WAITING", "INSUFFICIENT_DATA"):
        out = "HTF_UNAVAILABLE" if label == "WAITING" else label
    elif label in ("BULLISH", "BEARISH", "NEUTRAL"):
        out = label
    else:
        out = "HTF_UNAVAILABLE"
    if cache is not None:
        cache[key] = out
    return out


def classify_htf_alignment(
    *,
    bos_direction: str | None,
    trend_4h: str | None,
    trend_1h: str | None,
) -> str:
    """Classify HTF vs setup BOS. Conflicts are reported, never silently dropped."""
    t4 = (trend_4h or "").upper()
    t1 = (trend_1h or "").upper()
    bos = (bos_direction or "").upper()

    want_bull = bos in ("BULLISH_BOS", "LONG", "BULLISH")
    want_bear = bos in ("BEARISH_BOS", "SHORT", "BEARISH")
    if not want_bull and not want_bear:
        return HTF_NEUTRAL_UNAVAILABLE

    directional = {"BULLISH", "BEARISH"}
    if t4 not in directional or t1 not in directional:
        return HTF_NEUTRAL_UNAVAILABLE

    if want_bull:
        if t4 == "BULLISH" and t1 == "BULLISH":
            return HTF_ALIGNED
        if t4 == "BEARISH" and t1 == "BEARISH":
            return HTF_CONFLICT
        return HTF_NEUTRAL_UNAVAILABLE

    if t4 == "BEARISH" and t1 == "BEARISH":
        return HTF_ALIGNED
    if t4 == "BULLISH" and t1 == "BULLISH":
        return HTF_CONFLICT
    return HTF_NEUTRAL_UNAVAILABLE


def htf_trends_for_setup_bar(
    *,
    symbol: str,
    setup_candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    candles_4h: Sequence[Mapping[str, Any]] | None,
    candles_1h: Sequence[Mapping[str, Any]] | None,
    candles_5m: Sequence[Mapping[str, Any]] | None = None,
    config: SignalConfig | None = None,
    trend_15m: str | None = None,
    trend_cache: dict[tuple[str, int], str] | None = None,
    include_5m: bool = False,
) -> dict[str, Any]:
    """Resolve 4H/1H/(optional 5M) trends as-of the setup bar timestamp."""
    cfg = config or SignalConfig()
    end = min(as_of_index, len(setup_candles) - 1)
    as_of_ts = candle_time(setup_candles[end]) if end >= 0 else None

    if trend_15m is None:
        trend_15m = trend_at_as_of(
            setup_candles,
            as_of_index,
            timeframe="15m",
            symbol=symbol,
            config=cfg,
            cache=trend_cache,
        )

    idx_4h = as_of_index_at_or_before(candles_4h or [], as_of_ts)
    idx_1h = as_of_index_at_or_before(candles_1h or [], as_of_ts)
    idx_5m = (
        as_of_index_at_or_before(candles_5m or [], as_of_ts)
        if include_5m and candles_5m
        else None
    )

    trend_4h = trend_at_as_of(
        candles_4h or [],
        idx_4h,
        timeframe="4h",
        symbol=symbol,
        config=cfg,
        cache=trend_cache,
    )
    trend_1h = trend_at_as_of(
        candles_1h or [],
        idx_1h,
        timeframe="1h",
        symbol=symbol,
        config=cfg,
        cache=trend_cache,
    )
    trend_5m = (
        trend_at_as_of(
            candles_5m or [],
            idx_5m,
            timeframe="5m",
            symbol=symbol,
            config=cfg,
            cache=trend_cache,
        )
        if include_5m and candles_5m
        else "HTF_UNAVAILABLE"
    )

    return {
        "trend_4h": trend_4h,
        "trend_1h": trend_1h,
        "trend_15m": trend_15m,
        "trend_5m": trend_5m,
        "as_of_ts": as_of_ts.isoformat() if as_of_ts else None,
        "idx_4h": idx_4h,
        "idx_1h": idx_1h,
        "idx_5m": idx_5m,
    }
