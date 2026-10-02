"""Print OHLCV bar coverage so you know max long-strategy lookback."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
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
            for sym in SYMBOLS:
                for tf in TFS:
                    r = await conn.execute(
                        text(
                            """
                            SELECT COUNT(*), MIN(time), MAX(time)
                            FROM ohlcv
                            WHERE symbol = :s AND timeframe = :tf
                            """
                        ),
                        {"s": sym, "tf": tf},
                    )
                    n, t0, t1 = r.fetchone()
                    days = None
                    if t0 and t1:
                        days = round((t1 - t0).total_seconds() / 86400, 1)
                    print(f"{sym:10} {tf:4} bars={n:6} days~{days}  {t0} -> {t1}")
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
