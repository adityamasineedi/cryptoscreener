"""Agent 1 - data inventory for the sleeve study (current-universe retrospective).

Read-only. For the 27 V1_SYMBOLS x {1d,4h,1h,15m}: first bar, last bar, bar count,
missing bars inside [first,last] grouped by UTC day. No strategy is run.
Writes scripts/sleeve_study/out/a1_inventory.json.
"""
from __future__ import annotations
import asyncio, json, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND)); sys.path.insert(0, str(BACKEND / "scripts" / "strategy_rank" / "multicap"))
from _db import _connect  # read-only asyncpg session
from app.research.v1_production import V1_SYMBOLS

TFS = {"1d": 86400, "4h": 14400, "1h": 3600, "15m": 900}
OUT = Path(__file__).resolve().parent / "out"


async def main() -> None:
    conn = await _connect()
    inv = []
    try:
        for sym in sorted(V1_SYMBOLS):
            for tf, sec in TFS.items():
                rows = await conn.fetch("SELECT time FROM ohlcv WHERE symbol=$1 AND timeframe=$2 ORDER BY time", sym, tf)
                ts = [r[0] if r[0].tzinfo else r[0].replace(tzinfo=timezone.utc) for r in rows]
                if not ts:
                    inv.append(dict(symbol=sym, tf=tf, n=0)); continue
                have = set(int(t.timestamp()) for t in ts)
                a, b = int(ts[0].timestamp()), int(ts[-1].timestamp())
                miss = [x for x in range(a, b + 1, sec) if x not in have]
                by_day = defaultdict(int)
                for x in miss:
                    by_day[datetime.fromtimestamp(x, timezone.utc).date().isoformat()] += 1
                inv.append(dict(symbol=sym, tf=tf, first=ts[0].isoformat(), last=ts[-1].isoformat(), n=len(ts),
                                expected=(b - a) // sec + 1, missing_bars=len(miss), missing_days=dict(by_day)))
    finally:
        await conn.close()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "a1_inventory.json").write_text(json.dumps(inv, indent=1), encoding="utf-8")
    for r in inv:
        print(r["symbol"], r["tf"], r.get("first", "")[:16], r.get("last", "")[:16], r["n"], "missing", r.get("missing_bars"), list(r.get("missing_days", {}).items())[:6])

asyncio.run(main())
