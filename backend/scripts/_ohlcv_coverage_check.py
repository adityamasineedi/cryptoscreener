"""Print OHLCV min/max/count for research symbols."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.config import get_settings
from app.services.database import db_manager

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TFS = ["15m", "1h", "4h"]


async def main() -> None:
    await db_manager.connect(get_settings())
    try:
        async with db_manager.engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT symbol, timeframe, COUNT(*) AS n,
                           MIN(time) AS mn, MAX(time) AS mx
                    FROM ohlcv
                    WHERE symbol = ANY(:syms) AND timeframe = ANY(:tfs)
                    GROUP BY 1, 2
                    ORDER BY 1, 2
                    """
                ),
                {"syms": SYMBOLS, "tfs": TFS},
            )
            rows = result.fetchall()
            if not rows:
                print("NO_ROWS")
            for r in rows:
                print(f"{r[0]} {r[1]}: n={r[2]} min={r[3]} max={r[4]}")
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
