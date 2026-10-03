"""Expand persisted OHLCV with real Binance Futures REST history.

Research helper only — fetches real candles, does not fabricate data,
does not change live signal thresholds.

Examples:
  # Pull BTC/ETH/SOL 15m+1h back to 2023-01-01
  python scripts/expand_ohlcv_history.py --until 2023-01-01 --symbols BTCUSDT,ETHUSDT,SOLUSDT --tfs 15m,1h
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.ingestion.binance_rest import BinanceRestClient
from app.research.ohlcv_expand import expand_series_backward, parse_until_ms
from app.services.database import db_manager
from app.services.ohlcv_store import ohlcv_store

DEFAULT_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
]
DEFAULT_TFS = ["15m", "1h"]


async def main() -> None:
    p = argparse.ArgumentParser(description="Backfill Binance Futures OHLCV into Postgres")
    p.add_argument(
        "--until",
        default=None,
        help="UTC day YYYY-MM-DD to walk history back to (e.g. 2023-01-01)",
    )
    p.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    p.add_argument("--tfs", default=",".join(DEFAULT_TFS))
    p.add_argument(
        "--max-pages",
        type=int,
        default=200,
        help="Safety cap on 1500-bar REST pages per symbol/TF (default 200)",
    )
    args = p.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    tfs = [t.strip() for t in args.tfs.split(",") if t.strip()]
    until_ms = parse_until_ms(args.until) if args.until else None

    settings = get_settings()
    await db_manager.connect(settings)
    await ohlcv_store.load_from_db(
        symbols=symbols, timeframes=tfs, limit_per_series=200_000
    )
    rest = BinanceRestClient(settings)
    await rest.start()
    total = 0
    try:
        print(
            f"expand until={args.until or 'pages-only'} symbols={symbols} tfs={tfs} "
            f"max_pages={args.max_pages}"
        )
        for sym in symbols:
            for tf in tfs:
                before = len(ohlcv_store.get_closed(sym, tf))
                n = await expand_series_backward(
                    rest,
                    sym,
                    tf,
                    until_ms=until_ms,
                    max_pages=max(1, args.max_pages),
                )
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
