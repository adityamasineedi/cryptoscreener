#!/usr/bin/env python3
"""Prepare research Parquet OHLCV caches (research-only).

Example:
  python -m scripts.prepare_research_dataset \\
    --symbols BTCUSDT,ETHUSDT,SOLUSDT \\
    --timeframes 15m,1h \\
    --start 2024-01-01 --end 2025-01-01 \\
    --workers 2

Does not touch live trading / signals / WebSocket ingestion.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow `python -m scripts.prepare_research_dataset` from backend/
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _split(raw: str) -> list[str]:
    return [p.strip() for p in str(raw).split(",") if p.strip()]


async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare research OHLCV Parquet cache")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--timeframes", default="15m,1h")
    parser.add_argument("--start", default=None, help="UTC start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="UTC end date YYYY-MM-DD (exclusive)")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--no-features", action="store_true")
    args = parser.parse_args(argv)

    from app.config import get_settings
    from app.research.data_cache.config import ResearchCacheConfig
    from app.research.data_cache.prepare import prepare_research_dataset
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
                        "Start Postgres (DATABASE_URL in repo-root .env) then retry. "
                        "Do not nest `cd backend` — run from E:\\cryptoscreener\\backend."
                    ),
                },
                indent=2,
            )
        )
        return 2

    cfg = ResearchCacheConfig(enabled=True)
    if args.cache_dir:
        from pathlib import Path as P

        cfg.cache_root = P(args.cache_dir).expanduser().resolve()
    cfg.db_max_concurrency = max(1, min(8, int(args.workers)))

    bundle = await prepare_research_dataset(
        symbols=_split(args.symbols),
        timeframes=_split(args.timeframes),
        start_time=args.start,
        end_time=args.end,
        config=cfg,
        force_refresh=bool(args.force_refresh),
        build_features=not bool(args.no_features),
        max_concurrency=cfg.db_max_concurrency,
    )
    summary = {
        "run_id": bundle.run_id,
        "dataset_version": bundle.dataset_version,
        "keys": [f"{s}/{t}" for s, t in bundle.keys()],
        "rows": {f"{s}/{t}": ds.row_count for (s, t), ds in bundle.datasets.items()},
        "metrics": bundle.metrics,
        "cache_root": str(cfg.cache_root),
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0 if not (bundle.metrics or {}).get("errors") else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
