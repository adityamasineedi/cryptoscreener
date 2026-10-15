"""Count historical gap events in the OHLCV database. Read-only.

1. Raw census, per timeframe: bars whose open lies outside the previous bar's
   high-low range, and missing-candle gaps, for every series with > 5,000 bars.
2. Engine scan: CANDLE12_V3_EXEC_FIX (default book config) LONG and SHORT books
   on every 1h series with > 5,000 bars and on BTC/ETH/SOL 15m. Counts
   GAP_ENTRY_FILL / GAP_STOP_FILL / GAP_TARGET_FILL events, including gap
   entries on setups the sizing rules rejected.

Bars after 2026-10-15 are never read.

Usage: python scripts/scan_candle12_v3_gaps.py [--out gaps.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "strategy_rank" / "multicap"))

from _db import _connect, load_candles  # noqa: E402
from app.research.candle12_v3 import Candle12V3Config, run_candle12_v3  # noqa: E402

UTC = timezone.utc
END_EXCLUSIVE = datetime(2026, 10, 16, tzinfo=UTC)
WARMUP = 1000
STEP = {"5m": timedelta(minutes=5), "15m": timedelta(minutes=15), "1h": timedelta(hours=1)}

CENSUS_SQL = """
WITH s AS (
    SELECT symbol FROM ohlcv WHERE timeframe = $1 AND time < $2
    GROUP BY symbol HAVING count(*) > 5000
), b AS (
    SELECT o.symbol, o.time, o.open,
           lag(o.high) OVER w AS ph, lag(o.low) OVER w AS pl, lag(o.time) OVER w AS pt
    FROM ohlcv o JOIN s USING (symbol)
    WHERE o.timeframe = $1 AND o.time < $2
    WINDOW w AS (PARTITION BY o.symbol ORDER BY o.time)
)
SELECT count(DISTINCT symbol) AS series,
       count(*) AS bars,
       count(*) FILTER (WHERE open > ph) AS open_above_prev_high,
       count(*) FILTER (WHERE open < pl) AS open_below_prev_low,
       count(*) FILTER (WHERE time - pt <> $3) AS missing_candle_gaps
FROM b WHERE pt IS NOT NULL
"""


async def census() -> dict[str, Any]:
    conn = await _connect()
    try:
        out = {}
        for tf, step in STEP.items():
            row = await conn.fetchrow(CENSUS_SQL, tf, END_EXCLUSIVE, step)
            out[tf] = dict(row)
        symbols = await conn.fetch(
            "SELECT symbol FROM ohlcv WHERE timeframe = '1h' AND time < $1 "
            "GROUP BY symbol HAVING count(*) > 5000 ORDER BY symbol",
            END_EXCLUSIVE,
        )
        return {"census": out, "symbols_1h": [r[0] for r in symbols]}
    finally:
        await conn.close()


def engine_scan(symbol: str, tf: str) -> dict[str, Any]:
    candles = load_candles(symbol, tf, datetime(2000, 1, 1, tzinfo=UTC), END_EXCLUSIVE)
    out: dict[str, Any] = {"bars": len(candles)}
    for side in ("LONG", "SHORT"):
        res = run_candle12_v3(symbol, tf, candles, config=Candle12V3Config(book_side=side), index_start=WARMUP)
        c = res["counts"]
        out[side] = {
            "trades": c["trades_closed"] + c["trades_open_at_end"],
            "gap_entry_fills": c["gap_entry_fills"],
            "gap_entry_on_rejected": sum(bool(r.get("gap_entry")) for r in res["rejected"]),
            "gap_stop_exits": c["gap_stop_exits"],
            "gap_target_exits": c["gap_target_exits"],
            "rejected": len(res["rejected"]),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    t0 = time.perf_counter()
    base = asyncio.run(census())
    cells = [(s, "1h") for s in base["symbols_1h"]] + [(s, "15m") for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")]
    scan = {}
    for sym, tf in cells:
        scan[f"{sym} {tf}"] = engine_scan(sym, tf)
        print(f"{sym} {tf} {json.dumps(scan[f'{sym} {tf}'])}", file=sys.stderr, flush=True)
    totals: dict[str, int] = {}
    for row in scan.values():
        for side in ("LONG", "SHORT"):
            for k, v in row[side].items():
                totals[k] = totals.get(k, 0) + v
    report = {
        "window_end_exclusive": END_EXCLUSIVE.isoformat(),
        "census": base["census"],
        "engine_cells": len(cells),
        "engine_totals": totals,
        "engine_by_cell": scan,
        "elapsed_seconds": round(time.perf_counter() - t0, 1),
    }
    text = json.dumps(report, indent=1, default=str)
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
