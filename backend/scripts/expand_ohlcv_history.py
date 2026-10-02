"""Expand persisted OHLCV with real Binance Futures REST history.

Research helper only — fetches real candles, does not fabricate data,
does not change live signal thresholds.

Examples:
  # Pull BTC/ETH/SOL 15m+1h back to 2023-01-01
  python scripts/expand_ohlcv_history.py --until 2023-01-01 --symbols BTCUSDT,ETHUSDT,SOLUSDT --tfs 15m,1h
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.ingestion.binance_rest import BinanceRestClient
from app.ingestion.klines import TIMEFRAME_MS, normalize_rest_kline
from app.services.database import db_manager
from app.services.ohlcv_store import ohlcv_store

DEFAULT_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
]
DEFAULT_TFS = ["15m", "1h"]


def _parse_until(s: str) -> int:
    """Return UTC ms for the start of the given calendar day."""
    dt = datetime.strptime(s.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


async def _db_earliest_ms(symbol: str, timeframe: str) -> int | None:
    """True earliest open time in Postgres (memory store is capped at 500)."""
    if db_manager.engine is None:
        return None
    from sqlalchemy import text

    async with db_manager.engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT MIN(time) FROM ohlcv
                    WHERE symbol = :symbol AND timeframe = :tf
                    """
                ),
                {"symbol": symbol.upper(), "tf": timeframe},
            )
        ).fetchone()
    if not row or row[0] is None:
        return None
    ts = row[0]
    if hasattr(ts, "timestamp"):
        return int(ts.timestamp() * 1000)
    return None


async def expand_series(
    rest: BinanceRestClient,
    symbol: str,
    timeframe: str,
    *,
    until_ms: int | None,
    max_pages: int,
) -> int:
    step = TIMEFRAME_MS[timeframe]
    existing = ohlcv_store.get_closed(symbol, timeframe)
    known = {c.open_time for c in existing}
    # Prefer DB earliest — in-memory deque is capped (default 500) and is not full history
    db_earliest = await _db_earliest_ms(symbol, timeframe)
    if db_earliest is not None:
        cursor_end = db_earliest - 1
    elif existing:
        cursor_end = int(min(c.open_time for c in existing).timestamp() * 1000) - 1
    else:
        cursor_end = int(time.time() * 1000)

    written = 0
    pages = 0
    while pages < max_pages:
        if until_ms is not None and cursor_end < until_ms:
            break
        start_ms = cursor_end - step * 1500
        if until_ms is not None:
            start_ms = max(start_ms, until_ms)
        raw = await rest.futures_klines(
            symbol,
            timeframe,
            limit=1500,
            start_time=start_ms,
            end_time=cursor_end,
        )
        if not raw:
            break
        candles = []
        for row in raw:
            c = normalize_rest_kline(symbol, timeframe, row)
            if c and c.is_closed and c.open_time not in known:
                candles.append(c)
                known.add(c.open_time)
        if candles:
            n = await ohlcv_store.ingest_history(candles)
            written += n
            while ohlcv_store._pending_db:  # noqa: SLF001
                await ohlcv_store.flush_db()
        oldest = min(int(r[0]) for r in raw)
        if oldest >= cursor_end:
            break
        cursor_end = oldest - 1
        pages += 1
        if until_ms is not None and oldest <= until_ms:
            break
        await asyncio.sleep(0.15)
    return written


async def main() -> None:
    p = argparse.ArgumentParser(description="Backfill Binance Futures OHLCV into Postgres")
    p.add_argument(
        "--until",
        default=None,
        help="UTC day YYYY-MM-DD to walk history back to (e.g. 2023-01-01)",
    )
    p.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    p.add_argument("--tfs", default=",".join(DEFAULT_TFS))
    p.add_argument(
        "--max-pages",
        type=int,
        default=200,
        help="Safety cap on 1500-bar REST pages per symbol/TF (default 200)",
    )
    args = p.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    tfs = [t.strip() for t in args.tfs.split(",") if t.strip()]
    until_ms = _parse_until(args.until) if args.until else None

    settings = get_settings()
    await db_manager.connect(settings)
    await ohlcv_store.load_from_db(
        symbols=symbols, timeframes=tfs, limit_per_series=200_000
    )
    rest = BinanceRestClient(settings)
    await rest.start()
    total = 0
    try:
        print(
            f"expand until={args.until or 'pages-only'} symbols={symbols} tfs={tfs} "
            f"max_pages={args.max_pages}"
        )
        for sym in symbols:
            for tf in tfs:
                before = len(ohlcv_store.get_closed(sym, tf))
                n = await expand_series(
                    rest,
                    sym,
                    tf,
                    until_ms=until_ms,
                    max_pages=max(1, args.max_pages),
                )
                after = len(ohlcv_store.get_closed(sym, tf))
                total += n
                print(f"{sym} {tf}: +{n} written, store {before} -> {after}")
    finally:
        await rest.close()
        while ohlcv_store._pending_db:  # noqa: SLF001
            await ohlcv_store.flush_db()
        await db_manager.close()
    print(f"DONE total_new_ingested={total}")


if __name__ == "__main__":
    asyncio.run(main())
