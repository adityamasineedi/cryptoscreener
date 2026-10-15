"""Cache BTCUSDT/ETHUSDT 1h candles and Binance funding history for the LCSC validation study.

Candles come from the local ohlcv table. Funding comes from Binance's public
GET /fapi/v1/fundingRate and is written only to out/funding_<SYM>.json (no DB writes).
"""

from __future__ import annotations

import asyncio
import json
import pickle
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
# Freeze: no bar opening on or after this instant is loaded.
DATA_END_EXCL = datetime(2026, 10, 15, tzinfo=timezone.utc)


async def load_candles() -> None:
    from sqlalchemy import text

    from app.config import get_settings
    from app.services.database import db_manager

    await db_manager.connect(get_settings(), ensure_schema=False)
    async with db_manager.engine.begin() as conn:
        for sym in SYMBOLS:
            rows = (
                await conn.execute(
                    text(
                        "select time, open, high, low, close, volume from ohlcv "
                        "where symbol=:s and timeframe='1h' and time < :e order by time"
                    ),
                    {"s": sym, "e": DATA_END_EXCL},
                )
            ).fetchall()
            candles = [
                {
                    "time": r[0],
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "volume": float(r[5] or 0.0),
                }
                for r in rows
            ]
            (OUT / f"candles_{sym}_1h.pkl").write_bytes(pickle.dumps(candles))
            print(sym, len(candles), candles[0]["time"], candles[-1]["time"])


def fetch_funding(sym: str, start_ms: int, end_ms: int) -> list[dict]:
    rows: list[dict] = []
    cur = start_ms
    while cur < end_ms:
        url = (
            "https://fapi.binance.com/fapi/v1/fundingRate"
            f"?symbol={sym}&startTime={cur}&endTime={end_ms}&limit=1000"
        )
        with urllib.request.urlopen(url, timeout=30) as resp:
            page = json.loads(resp.read())
        if not page:
            break
        rows.extend(
            {"t": int(p["fundingTime"]), "rate": float(p["fundingRate"]), "mark": p.get("markPrice")}
            for p in page
        )
        cur = int(page[-1]["fundingTime"]) + 1
        time.sleep(0.3)
    return rows


def main() -> None:
    OUT.mkdir(exist_ok=True)
    if "--funding-only" not in sys.argv:
        asyncio.run(load_candles())
    start = int(datetime(2022, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(DATA_END_EXCL.timestamp() * 1000)
    for sym in SYMBOLS:
        rows = fetch_funding(sym, start, end)
        (OUT / f"funding_{sym}.json").write_text(json.dumps(rows))
        print(sym, "funding rows", len(rows), rows[0] if rows else None, rows[-1] if rows else None)


if __name__ == "__main__":
    main()
