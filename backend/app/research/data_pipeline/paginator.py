"""Chunked time-window pagination for Binance kline downloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

from app.ingestion.klines import TIMEFRAME_MS, normalize_timeframe


@dataclass(frozen=True)
class TimeChunk:
    """Inclusive-start / exclusive-end UTC window for one download page batch."""

    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    chunk_index: int

    @property
    def start_iso(self) -> str:
        return datetime.fromtimestamp(self.start_ms / 1000, tz=timezone.utc).isoformat()

    @property
    def end_iso(self) -> str:
        return datetime.fromtimestamp(self.end_ms / 1000, tz=timezone.utc).isoformat()


def parse_day_ms(day: str) -> int:
    dt = datetime.strptime(day.strip()[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def align_open_ms(ms: int, timeframe: str) -> int:
    step = TIMEFRAME_MS[normalize_timeframe(timeframe)]
    return (ms // step) * step


def iter_day_chunks(
    *,
    symbol: str,
    timeframe: str,
    start_day: str,
    end_day: str,
    chunk_days: int,
    resume_after_ms: int | None = None,
) -> Iterator[TimeChunk]:
    """Yield non-overlapping day chunks respecting Binance page limits.

    Each chunk spans ``chunk_days`` calendar days. Resume skips completed chunks
    whose end_ms <= resume_after_ms.
    """
    tf = normalize_timeframe(timeframe)
    if tf not in TIMEFRAME_MS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    start_ms = parse_day_ms(start_day)
    end_ms = parse_day_ms(end_day) + 86_400_000  # exclusive end of end_day
    end_ms = min(end_ms, now_ms())
    if end_ms <= start_ms:
        return
    step_days = max(1, int(chunk_days))
    cursor = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
    idx = 0
    while cursor < end_dt:
        nxt = min(cursor + timedelta(days=step_days), end_dt)
        c_start = int(cursor.timestamp() * 1000)
        c_end = int(nxt.timestamp() * 1000)
        if resume_after_ms is not None and c_end <= resume_after_ms:
            cursor = nxt
            idx += 1
            continue
        yield TimeChunk(
            symbol=symbol.upper(),
            timeframe=tf,
            start_ms=c_start,
            end_ms=c_end,
            chunk_index=idx,
        )
        cursor = nxt
        idx += 1


def page_windows_within_chunk(
    chunk: TimeChunk,
    *,
    kline_limit: int = 1500,
) -> list[tuple[int, int]]:
    """Split a day-chunk into REST page windows of at most ``kline_limit`` bars."""
    step = TIMEFRAME_MS[chunk.timeframe]
    span = kline_limit * step
    out: list[tuple[int, int]] = []
    cursor = chunk.start_ms
    while cursor < chunk.end_ms:
        end = min(cursor + span, chunk.end_ms)
        out.append((cursor, end))
        cursor = end
    return out
