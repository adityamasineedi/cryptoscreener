#!/usr/bin/env python3
"""One-shot DB performance audit measurements (dev only)."""

from __future__ import annotations

import asyncio
import time


async def main() -> None:
    from sqlalchemy import text

    from app.config import get_settings
    from app.services.database import db_manager

    s = get_settings()
    t0 = time.perf_counter()
    await db_manager.connect(s)
    print(
        "db_connect_ms",
        round((time.perf_counter() - t0) * 1000, 1),
        "status",
        db_manager.status,
        "schema",
        db_manager.schema_ready,
    )
    if db_manager.engine is None:
        print("NO_ENGINE")
        return

    queries = [
        ("count_ohlcv", "SELECT COUNT(*) FROM ohlcv"),
        (
            "count_btc_1h",
            "SELECT COUNT(*) FROM ohlcv WHERE symbol='BTCUSDT' AND timeframe='1h'",
        ),
        (
            "range_btc_1h",
            "SELECT MIN(time), MAX(time) FROM ohlcv WHERE symbol='BTCUSDT' AND timeframe='1h'",
        ),
        (
            "distinct_sym_tf",
            "SELECT COUNT(DISTINCT symbol), COUNT(DISTINCT timeframe) FROM ohlcv",
        ),
        (
            "idx_check",
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='ohlcv' ORDER BY indexname",
        ),
        (
            "explain_range",
            """
            EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
            SELECT time, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol='BTCUSDT' AND timeframe='1h'
              AND time >= NOW() - INTERVAL '30 days'
              AND time < NOW()
            ORDER BY time ASC
            """,
        ),
        (
            "explain_full_series",
            """
            EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
            SELECT time, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol='BTCUSDT' AND timeframe='1h'
            ORDER BY time ASC
            """,
        ),
        (
            "explain_coverage",
            """
            EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
            SELECT COUNT(*), MIN(time), MAX(time)
            FROM ohlcv
            WHERE symbol='BTCUSDT' AND timeframe='1h'
            """,
        ),
        (
            "explain_n_range_queries",
            """
            EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
            SELECT symbol, timeframe, COUNT(*), MIN(time), MAX(time)
            FROM ohlcv
            WHERE symbol = ANY(ARRAY['BTCUSDT','ETHUSDT','SOLUSDT'])
              AND timeframe = ANY(ARRAY['1h','4h'])
            GROUP BY symbol, timeframe
            """,
        ),
    ]

    async with db_manager.engine.begin() as conn:
        for name, sql in queries:
            t0 = time.perf_counter()
            result = await conn.execute(text(sql))
            rows = result.fetchall()
            ms = round((time.perf_counter() - t0) * 1000, 1)
            if name.startswith("explain"):
                print(f"\n=== {name} ({ms}ms wall) ===")
                for row in rows:
                    print(row[0])
            elif name == "idx_check":
                print(f"\n=== indexes ({ms}ms) ===")
                for row in rows:
                    print(row[0], "::", (row[1] or "")[:140])
            else:
                print(name, rows[0] if rows else None, f"{ms}ms")

    await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
