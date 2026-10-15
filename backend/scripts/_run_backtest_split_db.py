"""Run backtest_split on stored 15m OHLCV. Read-only."""
import asyncio
import os
import re
import sys
from pathlib import Path

import asyncpg
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest_split as bt

ROOT = Path(__file__).resolve().parents[2]
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def _dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not url:
        raise RuntimeError("DATABASE_URL not found")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)


async def load(symbol: str) -> pd.DataFrame:
    conn = await asyncpg.connect(_dsn(), server_settings={"default_transaction_read_only": "on"})
    try:
        rows = await conn.fetch(
            "SELECT time, open, high, low, close, volume FROM ohlcv "
            "WHERE symbol = $1 AND timeframe = $2 ORDER BY time ASC",
            symbol,
            "15m",
        )
    finally:
        await conn.close()
    df = pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    return df.set_index("datetime").sort_index()


async def main() -> None:
    outdir = Path(__file__).resolve().parent / "backtest_split_out"
    outdir.mkdir(exist_ok=True)
    for sym in SYMBOLS:
        df = await load(sym)
        print(f"\n######## {sym}  bars={len(df)}  {df.index.min()} -> {df.index.max()} ########")
        trades = bt.report(bt.backtest(bt.prepare(df)), label=sym)
        if trades is not None and not trades.empty:
            path = outdir / f"{sym}_trades_with_regimes.csv"
            trades.to_csv(path, index=False)
            print(
                f"saved {path}  n={len(trades)}  total_R={trades['R'].sum():.2f}  "
                f"pnl={trades['pnl'].sum():.0f}"
            )


if __name__ == "__main__":
    asyncio.run(main())
