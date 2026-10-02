"""Expand persisted OHLCV with real Binance Futures REST history.

Research helper only — fetches real candles, does not fabricate data,
does not change live signal thresholds.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.ingestion.binance_rest import BinanceRestClient
from app.ingestion.klines import TIMEFRAME_MS, normalize_rest_kline
from app.services.database import db_manager
from app.services.ohlcv_store import ohlcv_store

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "LINKUSDT",
    "DOGEUSDT",
    "SUIUSDT",
]
TIMEFRAMES = ["5m", "15m", "1h"]
# Pages of 1500 (Binance max) walking backward from earliest known / now
PAGES_PER_SERIES = 8


async def expand_series(
    rest: BinanceRestClient,
    symbol: str,
    timeframe: str,
    pages: int,
) -> int:
    step = TIMEFRAME_MS[timeframe]
    existing = ohlcv_store.get_closed(symbol, timeframe)
    known = {c.open_time for c in existing}
    # Start from earliest persisted open, or now
    if existing:
        cursor_end = int(min(c.open_time for c in existing).timestamp() * 1000) - 1
    else:
        cursor_end = int(time.time() * 1000)

    written = 0
    for _ in range(pages):
        start_ms = cursor_end - step * 1500
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
            # Persist in chunks
            while ohlcv_store._pending_db:  # noqa: SLF001
                await ohlcv_store.flush_db()
        # Walk further back
        oldest = min(int(r[0]) for r in raw)
        if oldest >= cursor_end:
            break
        cursor_end = oldest - 1
        await asyncio.sleep(0.15)
    return written


async def main() -> None:
    settings = get_settings()
    await db_manager.connect(settings)
    await ohlcv_store.load_from_db(
        symbols=SYMBOLS, timeframes=TIMEFRAMES, limit_per_series=50_000
    )
    rest = BinanceRestClient(settings)
    await rest.start()
    total = 0
    try:
        for sym in SYMBOLS:
            for tf in TIMEFRAMES:
                before = len(ohlcv_store.get_closed(sym, tf))
                n = await expand_series(rest, sym, tf, PAGES_PER_SERIES)
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
