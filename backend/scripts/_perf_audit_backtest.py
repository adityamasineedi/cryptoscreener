#!/usr/bin/env python3
"""Profile COMBO_02 combination backtest stages with real parquet/PG data."""

from __future__ import annotations

import asyncio
import json
import time
import tracemalloc
from pathlib import Path


async def main() -> None:
    from app.config import get_settings
    from app.research.bos_combinations import get_combination
    from app.research.combination_backtest import run_combination_backtest
    from app.research.config import ResearchConfig
    from app.research.service import _load_research_candles
    from app.services.database import db_manager

    settings = get_settings()
    await db_manager.connect(settings)

    combo = get_combination("COMBO_02")
    assert combo is not None

    cases = [
        ("small", ["BTCUSDT"], "2026-09-01", "2026-10-05"),
        ("medium", ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "2025-07-01", "2026-09-30"),
    ]

    results = []
    for name, symbols, start, end in cases:
        print(f"\n=== CASE {name} symbols={symbols} {start}->{end} ===")
        tracemalloc.start()
        t_all = time.perf_counter()

        t0 = time.perf_counter()
        by_sym: dict = {}
        for sym in symbols:
            c1h, _, meta1 = await _load_research_candles(
                sym, "1h", start_date=start, end_date=end, use_research_cache=True
            )
            c4h, _, meta4 = await _load_research_candles(
                sym, "4h", start_date=start, end_date=end, use_research_cache=True
            )
            by_sym[sym] = {
                "1h": c1h,
                "4h": c4h,
                "n1h": len(c1h),
                "n4h": len(c4h),
                "src1h": meta1.get("source"),
                "src4h": meta4.get("source"),
            }
        load_s = time.perf_counter() - t0
        print(
            "load_s",
            round(load_s, 3),
            {
                s: (v["n1h"], v["n4h"], v["src1h"], v["src4h"])
                for s, v in by_sym.items()
            },
        )

        t0 = time.perf_counter()
        all_trades = []
        digests = []
        for sym in symbols:
            out = run_combination_backtest(
                symbol=sym,
                timeframe="1h",
                candles=by_sym[sym]["1h"],
                combination=combo,
                research_config=ResearchConfig(),
                candles_4h=by_sym[sym]["4h"],
            )
            trades = out.get("trades") or []
            all_trades.extend(trades)
            digests.append(
                {
                    "symbol": sym,
                    "trades": len(trades),
                    "metrics": out.get("result") or {},
                }
            )
        bt_s = time.perf_counter() - t0
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        total = time.perf_counter() - t_all
        summary = {
            "case": name,
            "symbols": symbols,
            "load_s": round(load_s, 3),
            "backtest_s": round(bt_s, 3),
            "total_s": round(total, 3),
            "trade_count": len(all_trades),
            "rss_peak_mb": round(peak / (1024 * 1024), 1),
            "bars_1h": {s: by_sym[s]["n1h"] for s in symbols},
            "per_symbol": digests,
        }
        print(json.dumps(summary, indent=2, default=str))
        results.append(summary)

    out_path = Path("reports/_perf_audit_backtest_baseline.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print("wrote", out_path)
    await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
