"""Check persisted OHLCV coverage for C1/C2 research test symbols."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.config import get_settings
from app.services.database import db_manager


async def main() -> None:
    settings = get_settings()
    print("database_enabled", settings.database_enabled)
    await db_manager.connect(settings)
    print("status", db_manager.status, "enabled", db_manager.enabled)
    if not db_manager.enabled or db_manager.engine is None:
        print("NO_DB")
        return
    async with db_manager.engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT symbol, timeframe, count(*) AS n,
                       min(time) AS t0, max(time) AS t1
                FROM ohlcv
                WHERE symbol = ANY(:syms)
                  AND timeframe = ANY(:tfs)
                GROUP BY symbol, timeframe
                ORDER BY symbol, timeframe
                """
            ),
            {
                "syms": [
                    "BTCUSDT",
                    "ETHUSDT",
                    "SOLUSDT",
                    "BNBUSDT",
                    "XRPUSDT",
                    "LINKUSDT",
                    "DOGEUSDT",
                    "SUIUSDT",
                ],
                "tfs": ["5m", "15m", "1h"],
            },
        )
        rows = result.fetchall()
        print("coverage_rows", len(rows))
        for row in rows:
            print(row[0], row[1], "n=", row[2], "from", row[3], "to", row[4])
    await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
