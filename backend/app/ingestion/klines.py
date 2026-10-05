from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable

from app.models.ohlcv import Candle, GapInfo, StreamShard
from app.models.schemas import DataStatus
from app.ingestion.normalizer import ms_to_dt

TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
    "1D": 86_400_000,
}

BINANCE_INTERVAL: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1D": "1d",
}


def normalize_timeframe(tf: str) -> str:
    """Canonicalize timeframe tokens (15M / 15m / 15min → 15m)."""
    if tf is None:
        return tf
    raw = str(tf).strip()
    if not raw:
        return raw
    if raw.upper() == "1D":
        return "1d"
    key = raw.lower().replace("_", "-").replace(" ", "")
    aliases = {
        "1min": "1m",
        "1-minute": "1m",
        "5min": "5m",
        "5-minute": "5m",
        "15min": "15m",
        "15-minute": "15m",
        "30min": "30m",
        "60m": "1h",
        "1hour": "1h",
        "1-hour": "1h",
        "4hour": "4h",
        "4-hour": "4h",
        "1day": "1d",
        "1-day": "1d",
    }
    return aliases.get(key, key)


def stream_name(symbol: str, timeframe: str) -> str:
    interval = BINANCE_INTERVAL.get(timeframe, timeframe.lower())
    return f"{symbol.lower()}@kline_{interval}"


def generate_kline_streams(
    symbols: Iterable[str], timeframes: Iterable[str]
) -> list[str]:
    streams: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        for tf in timeframes:
            name = stream_name(symbol, normalize_timeframe(tf))
            if name in seen:
                continue
            seen.add(name)
            streams.append(name)
    return streams


def shard_streams(
    streams: list[str],
    *,
    max_streams_per_connection: int,
) -> list[StreamShard]:
    if max_streams_per_connection <= 0:
        raise ValueError("max_streams_per_connection must be > 0")
    if not streams:
        return []
    n_conn = math.ceil(len(streams) / max_streams_per_connection)
    shards: list[StreamShard] = []
    for i in range(n_conn):
        start = i * max_streams_per_connection
        end = start + max_streams_per_connection
        shards.append(StreamShard(connection_index=i, streams=streams[start:end]))
    return shards


def required_connections(stream_count: int, max_streams_per_connection: int) -> int:
    if stream_count <= 0:
        return 0
    return math.ceil(stream_count / max_streams_per_connection)


def normalize_ws_kline(data: dict[str, Any]) -> Candle | None:
    """Normalize Binance futures kline WS payload (raw or combined stream)."""
    payload = data.get("data", data) if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return None
    k = payload.get("k")
    if not isinstance(k, dict):
        return None
    symbol = k.get("s") or payload.get("s")
    interval = k.get("i")
    if not symbol or not interval:
        return None
    try:
        return Candle(
            symbol=str(symbol).upper(),
            timeframe=normalize_timeframe(str(interval)),
            open_time=ms_to_dt(k.get("t")),
            close_time=ms_to_dt(k.get("T")),
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
            quote_volume=_f(k.get("q")),
            trade_count=_i(k.get("n")),
            taker_buy_volume=_f(k.get("V")),
            taker_buy_quote_volume=_f(k.get("Q")),
            is_closed=bool(k.get("x")),
            timestamp=ms_to_dt(payload.get("E") or k.get("T")),
            source="binance_ws",
            status=DataStatus.LIVE,
        )
    except (KeyError, TypeError, ValueError):
        return None


def normalize_rest_kline(
    symbol: str,
    timeframe: str,
    row: list[Any],
) -> Candle | None:
    """Normalize Binance REST kline array row."""
    if not isinstance(row, (list, tuple)) or len(row) < 11:
        return None
    try:
        open_time = ms_to_dt(row[0])
        close_time = ms_to_dt(row[6])
        now = datetime.now(timezone.utc)
        # Last REST bar is often still forming (close_time in the future)
        is_closed = close_time <= now
        return Candle(
            symbol=symbol.upper(),
            timeframe=normalize_timeframe(timeframe),
            open_time=open_time,
            close_time=close_time,
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            quote_volume=float(row[7]) if row[7] is not None else None,
            trade_count=int(row[8]) if row[8] is not None else None,
            taker_buy_volume=float(row[9]) if row[9] is not None else None,
            taker_buy_quote_volume=float(row[10]) if row[10] is not None else None,
            is_closed=is_closed,
            timestamp=close_time if is_closed else now,
            source="binance_rest",
            status=DataStatus.LIVE,
        )
    except (TypeError, ValueError, IndexError):
        return None


def is_trailing_stale(
    candles: list[Candle],
    timeframe: str,
    *,
    max_lag_intervals: float = 1.25,
    now: datetime | None = None,
) -> bool:
    """True when the newest closed/open bar lags wall-clock by > N intervals.

    Internal gaps are handled by find_gaps / missing_fetch_ranges. This catches
    the common failure mode where history looks COMPLETE but the tip is frozen
    because the kline websocket went silent.

    Callers SHOULD include the forming/open candle in ``candles`` when present.
    With only closed bars, a series past ~25% into the current interval looks
    stale (lag > 1.25×step) — that is intentional so we pull the forming tip.
    With the open candle included, tip.open_time is the current interval start
    and lag stays < 1.0×step for the whole bar (hence not stale).

    Default 1.25 intervals: a closed tip must advance into the current bar
    (e.g. daily must pick up today's forming candle once the day opens).
    """
    if not candles:
        return True
    tf = normalize_timeframe(timeframe)
    step = TIMEFRAME_MS.get(tf)
    if not step:
        return False
    tip = max(candles, key=lambda c: c.open_time)
    tip_ot = tip.open_time
    if tip_ot.tzinfo is None:
        tip_ot = tip_ot.replace(tzinfo=timezone.utc)
    wall = now or datetime.now(timezone.utc)
    # Interval-boundary fast path: tip already covers the current open interval.
    wall_ms = int(wall.timestamp() * 1000)
    tip_ms = int(tip_ot.timestamp() * 1000)
    current_open_ms = (wall_ms // step) * step
    if tip_ms >= current_open_ms:
        return False
    lag_ms = (wall - tip_ot).total_seconds() * 1000.0
    return lag_ms > step * max(max_lag_intervals, 1.0)


def detect_gaps(
    candles: list[Candle],
    timeframe: str,
) -> list[GapInfo]:
    """Detect missing closed candles between sorted history."""
    tf = normalize_timeframe(timeframe)
    step = TIMEFRAME_MS.get(tf)
    if not step or len(candles) < 2:
        return []
    ordered = sorted(candles, key=lambda c: c.open_time)
    gaps: list[GapInfo] = []
    for prev, nxt in zip(ordered, ordered[1:]):
        expected_ms = int(prev.open_time.timestamp() * 1000) + step
        actual_ms = int(nxt.open_time.timestamp() * 1000)
        while expected_ms < actual_ms:
            gaps.append(
                GapInfo(
                    symbol=prev.symbol,
                    timeframe=tf,
                    expected_open_time=datetime.fromtimestamp(
                        expected_ms / 1000.0, tz=timezone.utc
                    ),
                    previous_open_time=prev.open_time,
                    next_open_time=nxt.open_time,
                )
            )
            expected_ms += step
            # Cap runaway gap enumeration
            if len(gaps) > 500:
                return gaps
    return gaps


def missing_fetch_ranges(
    candles: list[Candle],
    timeframe: str,
    *,
    want_start_ms: int | None = None,
    want_end_ms: int | None = None,
    max_ranges: int = 8,
) -> list[tuple[int, int]]:
    """
    Compute HTTP fetch windows that avoid re-downloading existing candles.

    Example:
      existing  2026-09-01 → 2026-09-20
      requested 2026-08-20 → 2026-09-30
      returns   (08-20→08-31) and (09-21→09-30)
    """
    tf = normalize_timeframe(timeframe)
    step = TIMEFRAME_MS.get(tf)
    if not step:
        return []
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    end_ms = want_end_ms if want_end_ms is not None else now_ms
    if want_start_ms is None:
        # Default lookback ≈ 200 candles for seed coverage
        want_start_ms = end_ms - step * 220
    if want_start_ms >= end_ms:
        return []

    ordered = sorted(candles, key=lambda c: c.open_time)
    if not ordered:
        return [(want_start_ms, end_ms)]

    # Covered closed intervals [open_ms, open_ms + step)
    covered: list[tuple[int, int]] = []
    for c in ordered:
        a = int(c.open_time.timestamp() * 1000)
        b = a + step
        if covered and a <= covered[-1][1]:
            covered[-1] = (covered[-1][0], max(covered[-1][1], b))
        else:
            covered.append((a, b))

    missing: list[tuple[int, int]] = []
    cursor = want_start_ms
    for a, b in covered:
        if b <= want_start_ms:
            continue
        if a >= end_ms:
            break
        if cursor < a:
            missing.append((cursor, min(a, end_ms)))
        cursor = max(cursor, b)
        if len(missing) >= max_ranges:
            return missing
    if cursor < end_ms:
        missing.append((cursor, end_ms))
    # Drop empty / tiny ranges
    return [(s, e) for s, e in missing if e - s >= step][:max_ranges]


def _f(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _i(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
