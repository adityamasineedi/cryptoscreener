"""DATA_MANIFEST.json: read-only coverage and quality audit for the 27 V1_SYMBOLS x {15m,1h,4h,1d}.

Per symbol/timeframe: first/last bar, count, expected count, missing bars (with the largest gaps), duplicate
timestamps, misaligned timestamps, invalid OHLC, non-positive prices, warm-up days before the common window,
coverage of each split. Also: contract metadata (symbols table), funding-history coverage, listing/delisting data.
Usage: python -B data_manifest.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone

from common2 import COMMON, HERE, LATE, TF_SEC, TFS, UNIVERSE, WINDOWS, COINS_15M
from _db import _connect
from app.research.v1_production import V1_SYMBOLS

UTC = timezone.utc


def _iso(x: int) -> str:
    return datetime.fromtimestamp(x, UTC).isoformat()


async def main() -> None:
    conn = await _connect()
    out = {"generated_utc": datetime.now(UTC).isoformat(), "source": "postgres ohlcv, read-only session",
           "read_only": {"default_transaction_read_only": await conn.fetchval("SHOW default_transaction_read_only")},
           "v1_symbols": sorted(V1_SYMBOLS), "common_window": [COMMON[0].isoformat(), COMMON[1].isoformat()],
           "series": [], "excluded": {}, "notes": []}
    digest = hashlib.sha256()
    try:
        for sym in sorted(V1_SYMBOLS):
            for tf in TFS:
                sec = TF_SEC[tf]
                rows = await conn.fetch("SELECT time, open, high, low, close, volume FROM ohlcv WHERE symbol=$1 AND "
                                        "timeframe=$2 ORDER BY time", sym, tf)
                if not rows:
                    out["series"].append({"symbol": sym, "tf": tf, "n": 0}); continue
                t = [int(r[0].timestamp()) for r in rows]
                dup = len(t) - len(set(t))
                mis = sum(1 for x in t if x % sec)
                bad = nonpos = 0
                for r in rows:
                    o, h, l, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
                    if min(o, h, l, c) <= 0:
                        nonpos += 1
                    if h < max(o, c) - 1e-12 or l > min(o, c) + 1e-12 or h < l:
                        bad += 1
                    digest.update(f"{sym}{tf}{r[0].isoformat()}{o}{h}{l}{c}".encode())
                st = sorted(set(t))
                gaps = []
                for a, b in zip(st, st[1:]):
                    if b - a > sec:
                        gaps.append((a, b, (b - a) // sec - 1))
                gaps.sort(key=lambda g: -g[2])
                rec = {"symbol": sym, "tf": tf, "first": _iso(st[0]), "last": _iso(st[-1]), "n": len(t),
                       "expected": (st[-1] - st[0]) // sec + 1, "missing_bars": sum(g[2] for g in gaps),
                       "gap_count": len(gaps), "largest_gaps": [{"after": _iso(a), "next": _iso(b), "missing": m}
                                                                for a, b, m in gaps[:5]],
                       "duplicates": dup, "misaligned": mis, "invalid_ohlc": bad, "nonpositive": nonpos,
                       "warmup_days_before_common": max(0.0, round((COMMON[0].timestamp() - st[0]) / 86400, 1))}
                cov = {}
                for name, (a, b) in WINDOWS.items():
                    exp = int((b.timestamp() - a.timestamp()) // sec)
                    have = sum(1 for x in st if a.timestamp() <= x < b.timestamp())
                    cov[name] = round(have / exp, 4) if exp else None
                rec["coverage"] = cov
                out["series"].append(rec)
                print(sym, tf, rec["first"][:16], rec["last"][:16], rec["n"], "miss", rec["missing_bars"], "dup", dup,
                      "bad", bad, cov, flush=True)
        meta = await conn.fetch("SELECT symbol, status, contract_type, price_precision, qty_precision, listed_at, "
                                "delisted_at, updated_at FROM symbols WHERE symbol = ANY($1)", sorted(V1_SYMBOLS))
        out["contracts"] = {r[0]: {"status": r[1], "contract_type": r[2], "price_precision": r[3],
                                   "qty_precision": r[4], "listed_at": r[5] and r[5].isoformat(),
                                   "delisted_at": r[6] and r[6].isoformat(), "snapshot_utc": r[7].isoformat()}
                            for r in meta}
        out["symbols_table"] = {
            "rows": await conn.fetchval("SELECT count(*) FROM symbols"),
            "with_listed_at": await conn.fetchval("SELECT count(*) FROM symbols WHERE listed_at IS NOT NULL"),
            "with_delisted_at": await conn.fetchval("SELECT count(*) FROM symbols WHERE delisted_at IS NOT NULL"),
            "statuses": {r[0]: r[1] for r in await conn.fetch("SELECT status, count(*) FROM symbols GROUP BY 1")}}
        out["funding"] = {
            "grid_funding_settlements": [dict(symbol=r[0], first=r[1].isoformat(), last=r[2].isoformat(), n=r[3])
                                         for r in await conn.fetch("SELECT symbol, min(funding_time), max(funding_time),"
                                                                   " count(*) FROM grid_funding_settlements GROUP BY 1")],
            "funding_rates_btc": [dict(source=r[0], first=r[1].isoformat(), last=r[2].isoformat())
                                  for r in await conn.fetch("SELECT source, min(time), max(time) FROM funding_rates "
                                                            "WHERE symbol='BTCUSDT' GROUP BY 1")]}
    finally:
        await conn.close()
    out["ohlcv_sha256"] = digest.hexdigest()

    # eligibility decisions
    by = {(r["symbol"], r["tf"]): r for r in out["series"]}
    for s in LATE:
        out["excluded"][s] = ("stored bars start 2024-10-09 on every timeframe: no bars in research folds F1-F3 "
                              "(2023-06..2025-01); also the 2026-10-14 strategy-search holdout coins")
    out["eligible"] = {"universe": UNIVERSE, "15m_setups": COINS_15M,
                       "15m_excluded": {s: f"15m starts {by[(s, '15m')]['first'][:10]}" for s in UNIVERSE
                                        if s not in COINS_15M},
                       "1h_note": {"AVAXUSDT": f"stored 1h starts {by[('AVAXUSDT', '1h')]['first'][:10]}; earlier 1h "
                                   "bars are built from complete groups of four stored 15m bars (common2.hourly)"}}
    out["notes"] += [
        "Five-year coverage is NOT present: only BTC/ETH/SOL 4h start 2021-10-15; BTC/ETH/SOL 1h/15m start 2022-10-15; "
        "most other coins start 2023-01-01; SUI 2023-05-03; ARB 2023-03-23; the 11 LATE coins 2024-10-09.",
        "1d bars end 2026-10-12 (10-13 for BTC); the common window ends 2026-10-13 00:00 UTC (exclusive).",
        "1m and 5m are excluded from this study.",
        "Listing/delisting history: symbols.listed_at and delisted_at are NULL for every row, every row is "
        "status TRADING (current snapshot only). The ~500 other perpetuals have 1d bars only from 2026. "
        "A verified historical annual top-20 quote-volume universe cannot be built -> not researched.",
        "Funding: stored settlements cover BTCUSDT from 2025-09-01 only (grid_funding_settlements) and live "
        "snapshots from 2026-10-01 (funding_rates). Not verified for the study window -> hypothetical funding "
        "0.01% of notional per 00/08/16 UTC boundary, charged to both sides (a stress cost, not history).",
        "Lot sizes: symbols.qty_precision (current snapshot) is used as the lot step 10^-precision; historical "
        "lot-size changes are not stored. Minimum notional is not stored: assumed 5 USDT (repo paper_sizing default).",
    ]
    (HERE / "DATA_MANIFEST.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("ohlcv_sha256", out["ohlcv_sha256"])


asyncio.run(main())
