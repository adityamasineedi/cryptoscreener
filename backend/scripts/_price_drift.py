import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.research.postgres_ohlcv import load_ohlcv_series_tail
from app.services.database import db_manager


async def main() -> None:
    await db_manager.connect(get_settings())
    try:
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            for tf in ["15m", "1h"]:
                candles = await load_ohlcv_series_tail(sym, tf, limit=1200)
                if not candles:
                    print(sym, tf, "no_data")
                    continue
                a = float(candles[0]["close"])
                b = float(candles[-1]["close"])
                t0 = candles[0].get("open_time") or candles[0].get("t")
                t1 = candles[-1].get("open_time") or candles[-1].get("t")
                print(
                    sym,
                    tf,
                    "change_pct",
                    round((b - a) / a * 100, 2),
                    "start",
                    a,
                    "end",
                    b,
                    "from",
                    t0,
                    "to",
                    t1,
                    "n",
                    len(candles),
                )
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
