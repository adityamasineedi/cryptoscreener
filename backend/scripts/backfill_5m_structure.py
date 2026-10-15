"""Fill missing 5m futures candles back to 2022-10-08.

Uses the public kline endpoint in its own process so it does not share the
API server's rate limiter. Writes the same ohlcv and volume_history rows the
store uses. Safe to re-run: it starts at the earliest stored bar and stops
at the target.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import text

from app.config import get_settings
from app.services.database import db_manager

UNTIL = datetime(2022, 10, 8, tzinfo=timezone.utc)
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
URL = "https://fapi.binance.com/fapi/v1/klines"
STEP_MS = 300_000

OHLCV_SQL = text(
    """
    INSERT INTO ohlcv (
        time, symbol, timeframe, open, high, low, close, volume,
        quote_volume, trade_count, taker_buy_base, taker_buy_quote, source
    ) VALUES (
        :time, :symbol, :timeframe, :open, :high, :low, :close, :volume,
        :quote_volume, :trade_count, :taker_buy_base, :taker_buy_quote, :source
    )
    ON CONFLICT (time, symbol, timeframe) DO UPDATE SET
        open = EXCLUDED.open,
        high = EXCLUDED.high,
        low = EXCLUDED.low,
        close = EXCLUDED.close,
        volume = EXCLUDED.volume,
        quote_volume = EXCLUDED.quote_volume,
        trade_count = EXCLUDED.trade_count,
        taker_buy_base = EXCLUDED.taker_buy_base,
        taker_buy_quote = EXCLUDED.taker_buy_quote,
        source = EXCLUDED.source
    """
)
VOL_SQL = text(
    """
    INSERT INTO volume_history (time, symbol, timeframe, volume, quote_volume, source)
    VALUES (:time, :symbol, :timeframe, :volume, :quote_volume, :source)
    ON CONFLICT (time, symbol, timeframe) DO UPDATE SET
        volume = EXCLUDED.volume,
        quote_volume = EXCLUDED.quote_volume,
        source = EXCLUDED.source
    """
)


def _rows(symbol: str, raw: list, earliest_ms: int | None, now_ms: int) -> list[dict]:
    out = []
    for row in raw:
        open_ms = int(row[0])
        close_ms = int(row[6])
        if close_ms >= now_ms:
            continue
        if earliest_ms is not None and open_ms >= earliest_ms:
            continue
        stamp = datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc)
        out.append(
            {
                "time": stamp,
                "symbol": symbol,
                "timeframe": "5m",
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "quote_volume": float(row[7]),
                "trade_count": int(row[8]),
                "taker_buy_base": float(row[9]),
                "taker_buy_quote": float(row[10]),
                "source": "binance_futures_rest",
            }
        )
    return out


async def _bound(conn, symbol: str, which: str):
    fn = "MIN" if which == "min" else "MAX"
    return (
        await conn.execute(
            text(
                f"SELECT {fn}(time) FROM ohlcv WHERE symbol = :symbol AND timeframe = '5m'"
            ),
            {"symbol": symbol},
        )
    ).scalar()


async def _write(rows: list[dict]) -> None:
    if not rows:
        return
    assert db_manager.engine is not None
    async with db_manager.engine.begin() as conn:
        await conn.execute(OHLCV_SQL, rows)
        await conn.execute(
            VOL_SQL,
            [
                {
                    "time": row["time"],
                    "symbol": row["symbol"],
                    "timeframe": "5m",
                    "volume": row["volume"],
                    "quote_volume": row["quote_volume"],
                    "source": row["source"],
                }
                for row in rows
            ],
        )


async def _get(client: httpx.AsyncClient, params: dict) -> list:
    for attempt in range(6):
        response = await client.get(URL, params=params)
        if response.status_code == 429 or response.status_code == 418:
            await asyncio.sleep(2.0 * (attempt + 1))
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"klines failed after retries: {params}")


async def _walk_back(client: httpx.AsyncClient, symbol: str) -> int:
    until_ms = int(UNTIL.timestamp() * 1000)
    written = 0
    pages = 0
    while True:
        async with db_manager.engine.connect() as conn:
            earliest = await _bound(conn, symbol, "min")
        if earliest is None:
            cursor_end = int(time.time() * 1000)
            earliest_ms = None
        else:
            earliest_ms = int(earliest.timestamp() * 1000)
            if earliest_ms <= until_ms:
                print(f"{symbol} 5m reaches {earliest.isoformat()}", flush=True)
                return written
            cursor_end = earliest_ms - 1
        start_ms = max(until_ms, cursor_end - STEP_MS * 1500)
        raw = await _get(
            client,
            {
                "symbol": symbol,
                "interval": "5m",
                "startTime": start_ms,
                "endTime": cursor_end,
                "limit": 1500,
            },
        )
        pages += 1
        if not raw:
            print(f"{symbol} 5m no older pages at {start_ms}", flush=True)
            return written
        rows = _rows(symbol, raw, earliest_ms, int(time.time() * 1000))
        if not rows:
            print(f"{symbol} 5m page {pages} added nothing; stopping", flush=True)
            return written
        await _write(rows)
        written += len(rows)
        oldest = int(raw[0][0])
        print(
            f"{symbol} 5m back page {pages} +{len(rows)} oldest {datetime.fromtimestamp(oldest / 1000, tz=timezone.utc).isoformat()}",
            flush=True,
        )
        if oldest <= until_ms:
            return written
        await asyncio.sleep(0.35)


async def _walk_tip(client: httpx.AsyncClient, symbol: str) -> int:
    written = 0
    now_ms = int(time.time() * 1000)
    async with db_manager.engine.connect() as conn:
        latest = await _bound(conn, symbol, "max")
    if latest is None:
        return 0
    cursor = int(latest.timestamp() * 1000) + STEP_MS
    if cursor >= now_ms - STEP_MS:
        return 0
    raw = await _get(
        client,
        {
            "symbol": symbol,
            "interval": "5m",
            "startTime": cursor,
            "endTime": now_ms,
            "limit": 1500,
        },
    )
    rows = _rows(symbol, raw, None, now_ms)
    # _rows drops bars at or after earliest_ms; tip bars are newer, so pass None.
    await _write(rows)
    written += len(rows)
    print(f"{symbol} 5m tip +{len(rows)}", flush=True)
    return written


async def main() -> None:
    settings = get_settings()
    await db_manager.connect(settings, ensure_schema=False)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for symbol in SYMBOLS:
                back = await _walk_back(client, symbol)
                tip = await _walk_tip(client, symbol)
                print(f"{symbol} done back={back} tip={tip}", flush=True)
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
