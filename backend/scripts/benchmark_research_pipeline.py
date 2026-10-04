#!/usr/bin/env python3
"""Benchmark research OHLCV load: PostgreSQL vs Parquet cache (research-only).

Does not claim a percentage improvement until measured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _split(raw: str) -> list[str]:
    return [p.strip() for p in str(raw).split(",") if p.strip()]


async def _pg_pass(symbols: list[str], timeframes: list[str], start: str, end: str) -> dict:
    from app.research.data_cache.metrics import ResearchCacheMetrics, sample_rss_mb
    from app.research.data_cache.postgres_loader import bulk_load_ohlcv_from_postgres
    from datetime import datetime, timezone

    metrics = ResearchCacheMetrics()
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    from datetime import timedelta

    end_ex = end_dt + timedelta(days=1)
    rows = 0
    t0 = time.monotonic()
    for s in symbols:
        for tf in timeframes:
            candles = await bulk_load_ohlcv_from_postgres(
                symbol=s,
                timeframe=tf,
                start_time=start_dt,
                end_time=end_ex,
                metrics=metrics,
            )
            rows += len(candles)
    metrics.mark_done()
    metrics.peak_memory_mb = sample_rss_mb()
    return {
        "mode": "postgresql_bulk",
        "rows": rows,
        "elapsed_seconds": round(time.monotonic() - t0, 3),
        "metrics": metrics.to_dict(),
    }


async def _cache_pass(
    symbols: list[str],
    timeframes: list[str],
    start: str,
    end: str,
    *,
    force: bool,
    cache_dir: Path,
) -> dict:
    from app.research.data_cache.config import ResearchCacheConfig
    from app.research.data_cache.prepare import prepare_research_dataset

    cfg = ResearchCacheConfig(enabled=True, cache_root=cache_dir)
    t0 = time.monotonic()
    bundle = await prepare_research_dataset(
        symbols=symbols,
        timeframes=timeframes,
        start_time=start,
        end_time=end,
        config=cfg,
        force_refresh=force,
        build_features=False,
    )
    return {
        "mode": "parquet_cache" + ("_miss" if force else "_hit_or_build"),
        "rows": sum(ds.row_count for ds in bundle.datasets.values()),
        "elapsed_seconds": round(time.monotonic() - t0, 3),
        "metrics": bundle.metrics,
        "keys": [f"{s}/{t}" for s, t in bundle.keys()],
    }


async def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--timeframes", default="15m,1h")
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2025-02-01")
    p.add_argument(
        "--cache-dir",
        default=str(ROOT / "data" / "research_cache_benchmark"),
    )
    args = p.parse_args(argv)
    symbols = _split(args.symbols)
    timeframes = _split(args.timeframes)
    cache_dir = Path(args.cache_dir)

    from app.config import get_settings
    from app.services.database import db_manager

    settings = get_settings()
    # Skip schema DDL — research reads only; avoids lock timeouts vs live uvicorn.
    await db_manager.connect(settings, ensure_schema=False)
    if db_manager.engine is None:
        print(
            json.dumps(
                {
                    "error": "DATABASE unavailable",
                    "status": db_manager.status,
                    "hint": (
                        "Postgres must be reachable via DATABASE_URL. "
                        "Run from backend/ (not backend/backend). "
                        "Space args: --timeframes 15m,1h --start 2025-01-01"
                    ),
                },
                indent=2,
            )
        )
        return 2

    out: dict = {"symbols": symbols, "timeframes": timeframes, "start": args.start, "end": args.end}
    try:
        out["A_postgresql"] = await _pg_pass(symbols, timeframes, args.start, args.end)
        out["B_cache_first"] = await _cache_pass(
            symbols, timeframes, args.start, args.end, force=True, cache_dir=cache_dir
        )
        out["C_cache_second"] = await _cache_pass(
            symbols, timeframes, args.start, args.end, force=False, cache_dir=cache_dir
        )
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
        print(json.dumps(out, indent=2, default=str))
        return 2

    a_q = out["A_postgresql"]["metrics"]["postgres_queries"]
    c_q = out["C_cache_second"]["metrics"].get("postgres_queries", 0)
    out["summary"] = {
        "postgres_queries_A": a_q,
        "postgres_queries_C_second_cache_run": c_q,
        "second_run_fewer_pg_reads": c_q < a_q,
        "note": "Do not claim percentage speedup beyond these measured fields.",
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
