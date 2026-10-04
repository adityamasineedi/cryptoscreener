"""Higher-timeframe alignment classification for research (no look-ahead)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.signals._candle_utils import candle_time, series_ohlcv
from app.signals.config import SignalConfig
from app.signals.schemas import SwingRecord
from app.signals.swing_detector import extend_swings, swings_for_timeframe
from app.signals.trend_engine import infer_trend

HTF_ALIGNED = "HTF_ALIGNED"
HTF_CONFLICT = "HTF_CONFLICT"
HTF_NEUTRAL_UNAVAILABLE = "HTF_NEUTRAL_UNAVAILABLE"


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def as_of_index_at_or_before(
    candles: Sequence[Mapping[str, Any]],
    as_of_ts: datetime | None,
) -> int | None:
    """Largest index with candle_time <= as_of_ts. Never uses future bars."""
    if not candles or as_of_ts is None:
        return None
    as_of_ts = _aware(as_of_ts)
    assert as_of_ts is not None
    best: int | None = None
    for i, c in enumerate(candles):
        t = _aware(candle_time(c))
        if t is None:
            continue
        if t <= as_of_ts:
            best = i
        else:
            break
    return best


def _trend_label_from_infer(trend: Mapping[str, Any]) -> str:
    label = str(trend.get("trend") or "INSUFFICIENT_DATA").upper()
    if label in ("WAITING", "INSUFFICIENT_DATA"):
        return "HTF_UNAVAILABLE" if label == "WAITING" else label
    if label in ("BULLISH", "BEARISH", "NEUTRAL"):
        return label
    return "HTF_UNAVAILABLE"


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
    out = _trend_label_from_infer(infer_trend(swings))
    if cache is not None:
        cache[key] = out
    return out


def precompute_htf_trend_cache(
    candles: Sequence[Mapping[str, Any]],
    *,
    timeframe: str,
    symbol: str,
    config: SignalConfig | None = None,
    cache: dict[tuple[str, int], str] | None = None,
) -> dict[tuple[str, int], str]:
    """Fill ``cache`` for every HTF bar index in O(n) via incremental swings.

    Semantically identical to calling ``trend_at_as_of`` for each index:
    closed-bar swings only, no future candles. Avoids O(n²) full re-detects
    when many setup bars map to distinct HTF as-of indices.
    """
    out = cache if cache is not None else {}
    if not candles:
        return out
    cfg = config or SignalConfig()
    sc = cfg.swing_for(timeframe)
    _, highs, lows, closes, _ = series_ohlcv(list(candles))
    swings: list[SwingRecord] = []
    for i in range(len(candles)):
        swings = extend_swings(
            swings,
            candles,
            left=sc.swing_left_bars,
            right=sc.swing_right_bars,
            symbol=symbol,
            timeframe=timeframe,
            atr_period=cfg.atr_period,
            minimum_swing_distance_atr=sc.minimum_swing_distance_atr,
            as_of_index=i,
            highs=highs,
            lows=lows,
            closes=closes,
        )
        out[(timeframe, i)] = _trend_label_from_infer(infer_trend(swings))
    return out


def build_htf_as_of_index_map(
    setup_candles: Sequence[Mapping[str, Any]],
    htf_candles: Sequence[Mapping[str, Any]],
) -> list[int | None]:
    """For each setup bar, largest HTF index with time <= setup time (closed).

    Two-pointer O(n+m). Equivalent to ``as_of_index_at_or_before`` per bar when
    both series are time-sorted ascending.
    """
    if not setup_candles:
        return []
    if not htf_candles:
        return [None] * len(setup_candles)
    out: list[int | None] = [None] * len(setup_candles)
    j = -1
    n_htf = len(htf_candles)
    for i, c in enumerate(setup_candles):
        as_of = _aware(candle_time(c))
        if as_of is None:
            out[i] = j if j >= 0 else None
            continue
        while j + 1 < n_htf:
            ts = _aware(candle_time(htf_candles[j + 1]))
            if ts is None:
                j += 1
                continue
            if ts <= as_of:
                j += 1
            else:
                break
        out[i] = j if j >= 0 else None
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
    idx_1h_map: Sequence[int | None] | None = None,
    idx_4h_map: Sequence[int | None] | None = None,
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

    if idx_4h_map is not None and 0 <= end < len(idx_4h_map):
        idx_4h = idx_4h_map[end]
    else:
        idx_4h = as_of_index_at_or_before(candles_4h or [], as_of_ts)
    if idx_1h_map is not None and 0 <= end < len(idx_1h_map):
        idx_1h = idx_1h_map[end]
    else:
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
