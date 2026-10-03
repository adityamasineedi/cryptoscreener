"""Load research OHLCV directly from PostgreSQL (bypass in-memory 500-bar store).

Used by:
- Candle-1/Candle-2 V2 research scripts (full-history series loads)
- BOS Combination Research API (date-bounded / limited series loads)

This module loads raw OHLCV only. It does NOT load or mix research *result*
datasets (BOS combination metrics vs Candle-12 V2 trade JSON).
Never fabricates, interpolates, or substitutes another timeframe.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.ingestion.klines import TIMEFRAME_MS
from app.research.query_utils import normalize_research_symbol, normalize_research_timeframe
from app.services.database import db_manager


async def list_symbols_with_ohlcv(timeframe: str | None = None) -> list[str]:
    """Return distinct symbols that have persisted OHLCV (data-driven eligibility)."""
    if db_manager.engine is None:
        raise RuntimeError("DATABASE unavailable")
    async with db_manager.engine.begin() as conn:
        if timeframe:
            result = await conn.execute(
                text(
                    """
                    SELECT DISTINCT symbol
                    FROM ohlcv
                    WHERE timeframe = :tf
                    ORDER BY symbol ASC
                    """
                ),
                {"tf": timeframe},
            )
        else:
            result = await conn.execute(
                text("SELECT DISTINCT symbol FROM ohlcv ORDER BY symbol ASC")
            )
        return [str(r[0]).upper() for r in result.fetchall()]


def _rows_to_candles(rows: Any) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    for r in rows:
        candles.append(
            {
                "time": r[0],
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5] or 0),
            }
        )
    return candles


async def load_ohlcv_series(
    symbol: str,
    timeframe: str,
) -> list[dict[str, Any]]:
    """Load one symbol/timeframe series ordered by open time ascending."""
    if db_manager.engine is None:
        raise RuntimeError("DATABASE unavailable")
    sym = normalize_research_symbol(symbol)
    tf = normalize_research_timeframe(timeframe)
    async with db_manager.engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT time, open, high, low, close, volume
                FROM ohlcv
                WHERE symbol = :symbol AND timeframe = :tf
                ORDER BY time ASC
                """
            ),
            {"symbol": sym, "tf": tf},
        )
        rows = result.fetchall()
    return _rows_to_candles(rows)


async def load_ohlcv_series_tail(
    symbol: str,
    timeframe: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Load the most recent `limit` candles ascending (for unbounded compare)."""
    if db_manager.engine is None:
        raise RuntimeError("DATABASE unavailable")
    if limit <= 0:
        return []
    sym = normalize_research_symbol(symbol)
    tf = normalize_research_timeframe(timeframe)
    async with db_manager.engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT time, open, high, low, close, volume
                FROM (
                    SELECT time, open, high, low, close, volume
                    FROM ohlcv
                    WHERE symbol = :symbol AND timeframe = :tf
                    ORDER BY time DESC
                    LIMIT :lim
                ) recent
                ORDER BY time ASC
                """
            ),
            {"symbol": sym, "tf": tf, "lim": int(limit)},
        )
        rows = result.fetchall()
    return _rows_to_candles(rows)


async def load_ohlcv_series_range(
    symbol: str,
    timeframe: str,
    *,
    start: datetime | None,
    end_exclusive: datetime | None,
    warmup_bars: int = 0,
) -> list[dict[str, Any]]:
    """Load a date-bounded slice (+ warmup) — avoids pulling multi-year full series."""
    if db_manager.engine is None:
        raise RuntimeError("DATABASE unavailable")
    sym = normalize_research_symbol(symbol)
    tf = normalize_research_timeframe(timeframe)
    start_bound = start
    if start is not None and warmup_bars > 0:
        step_ms = TIMEFRAME_MS.get(tf) or 60_000
        warm = timedelta(milliseconds=step_ms * int(warmup_bars))
        start_bound = start - warm
        if start_bound.tzinfo is None:
            start_bound = start_bound.replace(tzinfo=timezone.utc)

    # Build predicates explicitly — asyncpg cannot infer types for
    # `(:ts IS NULL OR time >= :ts)` when the same bind is reused.
    clauses = ["symbol = :symbol", "timeframe = :tf"]
    params: dict[str, Any] = {"symbol": sym, "tf": tf}
    if start_bound is not None:
        clauses.append("time >= :start_ts")
        params["start_ts"] = start_bound
    if end_exclusive is not None:
        clauses.append("time < :end_ts")
        params["end_ts"] = end_exclusive
    sql = f"""
        SELECT time, open, high, low, close, volume
        FROM ohlcv
        WHERE {" AND ".join(clauses)}
        ORDER BY time ASC
    """
    async with db_manager.engine.begin() as conn:
        result = await conn.execute(text(sql), params)
        rows = result.fetchall()
    return _rows_to_candles(rows)


async def count_ohlcv(symbol: str, timeframe: str) -> int:
    if db_manager.engine is None:
        raise RuntimeError("DATABASE unavailable")
    sym = normalize_research_symbol(symbol)
    tf = normalize_research_timeframe(timeframe)
    async with db_manager.engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT COUNT(*) FROM ohlcv
                WHERE symbol = :symbol AND timeframe = :tf
                """
            ),
            {"symbol": sym, "tf": tf},
        )
        row = result.fetchone()
        return int(row[0]) if row else 0
