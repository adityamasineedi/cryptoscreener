"""Higher-timeframe alignment classification for research (no look-ahead)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.signals._candle_utils import candle_time, series_ohlcv
from app.signals.config import SignalConfig
from app.signals.schemas import SwingRecord
from app.signals.swing_detector import extend_swings, swings_for_timeframe
from app.signals.trend_engine import infer_trend

HTF_ALIGNED = "HTF_ALIGNED"
HTF_CONFLICT = "HTF_CONFLICT"
HTF_NEUTRAL_UNAVAILABLE = "HTF_NEUTRAL_UNAVAILABLE"

_TF_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
}


def timeframe_seconds(timeframe: str) -> int:
    key = (timeframe or "").lower().strip()
    if key not in _TF_SECONDS:
        raise ValueError(f"Unsupported timeframe for HTF duration: {timeframe!r}")
    return _TF_SECONDS[key]


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


def as_of_index_fully_closed(
    candles: Sequence[Mapping[str, Any]],
    decision_ts: datetime | None,
    *,
    htf_duration_seconds: int,
) -> int | None:
    """Largest index where HTF bar close time <= decision_ts (fully closed).

    Bar close = open_time + htf_duration_seconds. Never uses a forming HTF bar.
    """
    if not candles or decision_ts is None:
        return None
    decision_ts = _aware(decision_ts)
    assert decision_ts is not None
    dur = int(htf_duration_seconds)
    if dur <= 0:
        return None
    best: int | None = None
    for i, c in enumerate(candles):
        t = _aware(candle_time(c))
        if t is None:
            continue
        close_ts = t.timestamp() + dur
        if close_ts <= decision_ts.timestamp():
            best = i
        else:
            break
    return best


def decision_timestamp_for_setup_bar(
    setup_candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    *,
    setup_timeframe: str,
) -> datetime | None:
    """Setup bar close time = open_time + setup TF duration."""
    end = min(int(as_of_index), len(setup_candles) - 1)
    if end < 0:
        return None
    open_ts = _aware(candle_time(setup_candles[end]))
    if open_ts is None:
        return None
    return open_ts + timedelta(seconds=timeframe_seconds(setup_timeframe))


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

    Note: comparing HTF *open* time to setup *open* time can select a still-
    forming HTF candle (e.g. 4h) whose final OHLC is not yet known at the 1h
    decision. Use ``build_htf_as_of_index_map_fully_closed`` for closed-only.
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


def build_htf_as_of_index_map_fully_closed(
    setup_candles: Sequence[Mapping[str, Any]],
    htf_candles: Sequence[Mapping[str, Any]],
    *,
    setup_timeframe: str,
    htf_timeframe: str,
) -> list[int | None]:
    """For each setup bar, last HTF index whose bar is fully closed by setup close.

    Decision time = setup open + setup TF duration.
    HTF bar is eligible iff HTF open + HTF duration <= decision time.
    """
    if not setup_candles:
        return []
    if not htf_candles:
        return [None] * len(setup_candles)
    setup_dur = timeframe_seconds(setup_timeframe)
    htf_dur = timeframe_seconds(htf_timeframe)
    out: list[int | None] = [None] * len(setup_candles)
    j = -1
    n_htf = len(htf_candles)
    for i, c in enumerate(setup_candles):
        open_ts = _aware(candle_time(c))
        if open_ts is None:
            out[i] = j if j >= 0 else None
            continue
        decision = open_ts + timedelta(seconds=setup_dur)
        while j + 1 < n_htf:
            ts = _aware(candle_time(htf_candles[j + 1]))
            if ts is None:
                j += 1
                continue
            htf_close = ts + timedelta(seconds=htf_dur)
            if htf_close <= decision:
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
    setup_timeframe: str = "15m",
    htf_require_fully_closed: bool = False,
) -> dict[str, Any]:
    """Resolve 4H/1H/(optional 5M) trends as-of the setup bar timestamp."""
    cfg = config or SignalConfig()
    end = min(as_of_index, len(setup_candles) - 1)
    as_of_ts = candle_time(setup_candles[end]) if end >= 0 else None
    decision_ts = (
        decision_timestamp_for_setup_bar(
            setup_candles, end, setup_timeframe=setup_timeframe
        )
        if htf_require_fully_closed
        else None
    )

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
    elif htf_require_fully_closed:
        idx_4h = as_of_index_fully_closed(
            candles_4h or [],
            decision_ts,
            htf_duration_seconds=timeframe_seconds("4h"),
        )
    else:
        idx_4h = as_of_index_at_or_before(candles_4h or [], as_of_ts)
    if idx_1h_map is not None and 0 <= end < len(idx_1h_map):
        idx_1h = idx_1h_map[end]
    elif htf_require_fully_closed:
        idx_1h = as_of_index_fully_closed(
            candles_1h or [],
            decision_ts,
            htf_duration_seconds=timeframe_seconds("1h"),
        )
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
        "decision_ts": decision_ts.isoformat() if decision_ts else None,
        "htf_require_fully_closed": bool(htf_require_fully_closed),
        "idx_4h": idx_4h,
        "idx_1h": idx_1h,
        "idx_5m": idx_5m,
    }
