"""Backfill major USDT-M perps: 5m, 15m, 1h, 4h, 1d back two years into Postgres.

Uses the existing OHLCV expand helpers (real Binance Futures klines, upsert).
Does not hydrate full series into memory and does not change signal logic.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.ingestion.binance_rest import BinanceRestClient
from app.ingestion.klines import TIMEFRAME_MS
from app.research.ohlcv_expand import (
    db_earliest_ms,
    db_latest_ms,
    expand_series_backward,
    expand_series_forward,
    parse_until_ms,
)
from app.research.v1_production import V1_SYMBOLS
from app.services.database import db_manager
from app.services.ohlcv_store import ohlcv_store

TFS = ["1d", "4h", "1h", "15m", "5m"]
# 5m × 2y ≈ 210k bars / 1500 ≈ 141 pages. Headroom for listing gaps.
MAX_PAGES = 180


def _iso(ms: int | None) -> str:
    if ms is None:
        return "-"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


async def refill_forward(
    rest: BinanceRestClient,
    symbol: str,
    timeframe: str,
    *,
    until_ms: int,
    max_pages: int,
) -> int:
    """Upsert every closed bar from until_ms to now, including internal gaps."""
    from app.ingestion.klines import normalize_rest_kline, normalize_timeframe

    tf = normalize_timeframe(timeframe)
    step = TIMEFRAME_MS[tf]
    cursor = until_ms
    now_ms = int(time.time() * 1000)
    written = 0
    pages = 0
    # Binance returns the oldest `limit` bars when the window is larger than limit.
    window = step * 1499
    while pages < max_pages and cursor < now_ms - step:
        end_ms = min(cursor + window, now_ms)
        raw = await rest.futures_klines(
            symbol,
            tf,
            limit=1500,
            start_time=cursor,
            end_time=end_ms,
        )
        if not raw:
            break
        candles = []
        for row in raw:
            c = normalize_rest_kline(symbol, tf, row)
            if c and c.is_closed:
                candles.append(c)
        if candles:
            written += await ohlcv_store.ingest_history(candles)
            while ohlcv_store._pending_db:  # noqa: SLF001
                await ohlcv_store.flush_db()
        newest = max(int(r[0]) for r in raw)
        next_cursor = newest + step
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        pages += 1
        await asyncio.sleep(0.12)
    return written


async def main() -> None:
    tfs = [t.strip() for t in sys.argv[1].split(",")] if len(sys.argv) > 1 else list(TFS)
    refill = "--refill" in sys.argv
    until_day = (datetime.now(timezone.utc) - timedelta(days=730)).strftime("%Y-%m-%d")
    until_ms = parse_until_ms(until_day)
    symbols = sorted(V1_SYMBOLS)
    settings = get_settings()
    await db_manager.connect(settings)
    if not db_manager.enabled or db_manager.engine is None:
        print("NO_DB")
        return
    rest = BinanceRestClient(settings)
    await rest.start()
    total = 0
    now_ms = int(time.time() * 1000)
    print(
        f"start until={until_day} symbols={len(symbols)} tfs={tfs} "
        f"max_pages={MAX_PAGES} refill={refill}",
        flush=True,
    )
    print("symbols " + ",".join(symbols), flush=True)
    try:
        for sym in symbols:
            for tf in tfs:
                step = TIMEFRAME_MS[tf]
                t0 = await db_earliest_ms(sym, tf)
                t1 = await db_latest_ms(sym, tf)
                tip_ok = t1 is not None and t1 >= now_ms - step * 3
                back_ok = t0 is not None and t0 <= until_ms
                if refill:
                    written = await refill_forward(
                        rest, sym, tf, until_ms=until_ms, max_pages=MAX_PAGES
                    )
                    t0b = await db_earliest_ms(sym, tf)
                    t1b = await db_latest_ms(sym, tf)
                    total += written
                    print(
                        f"{sym} {tf}: refill +{written}  {_iso(t0b)} -> {_iso(t1b)}  "
                        f"total={total}",
                        flush=True,
                    )
                    continue
                if tip_ok and back_ok:
                    print(
                        f"{sym} {tf}: already {_iso(t0)} -> {_iso(t1)}",
                        flush=True,
                    )
                    continue
                written = 0
                t_cell = time.time()
                if not back_ok:
                    n = await expand_series_backward(
                        rest,
                        sym,
                        tf,
                        until_ms=until_ms,
                        max_pages=MAX_PAGES,
                    )
                    written += n
                if not tip_ok:
                    n = await expand_series_forward(
                        rest,
                        sym,
                        tf,
                        max_pages=min(40, MAX_PAGES),
                    )
                    written += n
                while ohlcv_store._pending_db:  # noqa: SLF001
                    await ohlcv_store.flush_db()
                t0b = await db_earliest_ms(sym, tf)
                t1b = await db_latest_ms(sym, tf)
                total += written
                print(
                    f"{sym} {tf}: +{written}  {_iso(t0b)} -> {_iso(t1b)}  "
                    f"({time.time() - t_cell:.0f}s)  total={total}",
                    flush=True,
                )
    finally:
        while ohlcv_store._pending_db:  # noqa: SLF001
            await ohlcv_store.flush_db()
        await rest.close()
        await db_manager.close()
    print(f"DONE total_written={total}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
