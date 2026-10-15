import asyncio
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from app.config import get_settings
from app.services.database import db_manager

OUT = Path(__file__).with_name("btc_ohlcv.csv")


async def main() -> None:
    await db_manager.connect(get_settings(), ensure_schema=False)
    try:
        async with db_manager.engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT time, open, high, low, close, volume
                    FROM ohlcv
                    WHERE symbol = :symbol AND timeframe = :tf
                    ORDER BY time ASC
                    """
                ),
                {"symbol": "BTCUSDT", "tf": "1h"},
            )
            rows = result.fetchall()
    finally:
        await db_manager.close()
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(None)
    df.to_csv(OUT, index=False)
    print(len(df), df["time"].iloc[0], df["time"].iloc[-1], OUT)


if __name__ == "__main__":
    asyncio.run(main())
