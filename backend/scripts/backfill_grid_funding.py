"""Fetch Binance USD-M funding settlements into grid_funding_* tables.

Explicit command. Not imported by the application and not run at startup.

    python -m scripts.backfill_grid_funding --symbol BTCUSDT --start 2026-08-02 --end 2026-10-11
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from sqlalchemy import text

from app.config import get_settings
from app.grid_trading.settlements import (
    GRID_FUNDING_DDL,
    FundingHttpError,
    MemorySettlementStore,
    backfill_symbol,
)
from app.services.database import db_manager


class UrllibFundingClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def funding_rate_history(
        self,
        symbol: str,
        *,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        query = urlencode(
            {
                "symbol": symbol,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": limit,
            }
        )
        url = f"{self.base_url}/fapi/v1/fundingRate?{query}"
        try:
            with urlopen(url, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in (429, 418):
                raise FundingHttpError(exc.code) from exc
            raise
        if not isinstance(payload, list):
            raise RuntimeError("funding history response was not a list")
        return payload


def _parse_day(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


async def _load(symbol: str) -> MemorySettlementStore:
    store = MemorySettlementStore()
    async with db_manager.engine.begin() as conn:
        fetched = (
            await conn.execute(
                text(
                    """
                    SELECT symbol, "from", "to", fetched_at
                    FROM grid_funding_fetch_ranges
                    WHERE symbol = :symbol
                    """
                ),
                {"symbol": symbol},
            )
        ).fetchall()
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT symbol, funding_time, funding_rate, mark_price
                    FROM grid_funding_settlements
                    WHERE symbol = :symbol
                    """
                ),
                {"symbol": symbol},
            )
        ).fetchall()
    for row in fetched:
        store.fetches.append((row[0], row[1], row[2], row[3]))
    store.upsert(
        [
            {
                "symbol": row[0],
                "funding_time": row[1],
                "funding_rate": float(row[2]),
                "mark_price": None if row[3] is None else float(row[3]),
            }
            for row in rows
        ]
    )
    return store


async def _save(store: MemorySettlementStore) -> None:
    async with db_manager.engine.begin() as conn:
        await conn.execute(text(GRID_FUNDING_DDL))
        for row in store.rows.values():
            await conn.execute(
                text(
                    """
                    INSERT INTO grid_funding_settlements
                        (symbol, funding_time, funding_rate, mark_price)
                    VALUES (:symbol, :funding_time, :funding_rate, :mark_price)
                    ON CONFLICT (symbol, funding_time) DO UPDATE SET
                        funding_rate = EXCLUDED.funding_rate,
                        mark_price = EXCLUDED.mark_price
                    """
                ),
                row,
            )
        for symbol, start, end, fetched_at in store.fetches:
            await conn.execute(
                text(
                    """
                    INSERT INTO grid_funding_fetch_ranges (symbol, "from", "to", fetched_at)
                    VALUES (:symbol, :start, :end, :fetched_at)
                    ON CONFLICT (symbol, "from", "to") DO UPDATE SET
                        fetched_at = EXCLUDED.fetched_at
                    """
                ),
                {"symbol": symbol, "start": start, "end": end, "fetched_at": fetched_at},
            )


async def _run(symbol: str, start: datetime, end: datetime) -> dict[str, Any]:
    settings = get_settings()
    await db_manager.connect(settings, ensure_schema=False)
    try:
        async with db_manager.engine.begin() as conn:
            await conn.execute(text(GRID_FUNDING_DDL))
        store = await _load(symbol.upper())
        result = backfill_symbol(
            symbol,
            start,
            end,
            client=UrllibFundingClient(settings.binance_futures_rest),
            store=store,
        )
        await _save(store)
        return result
    finally:
        if db_manager.engine is not None:
            await db_manager.engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill grid funding settlements from Binance.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True, help="UTC date or ISO timestamp, inclusive")
    parser.add_argument("--end", required=True, help="UTC date or ISO timestamp, inclusive")
    args = parser.parse_args()
    result = asyncio.run(_run(args.symbol, _parse_day(args.start), _parse_day(args.end)))
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
