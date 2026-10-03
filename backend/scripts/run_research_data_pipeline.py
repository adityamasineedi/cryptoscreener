#!/usr/bin/env python3
"""CLI for the isolated research historical data pipeline.

Examples:
  # Section 40 — small smoke validation (BTC/ETH/SOL, ~1y, features)
  python -m scripts.run_research_data_pipeline --mode smoke

  # Full universe download + dataset quality (no strategy optimization)
  python -m scripts.run_research_data_pipeline --mode full --discover

Does NOT modify live signal engines, WS ingestion, or screener behavior.
Writes missing OHLCV via ON CONFLICT DO NOTHING only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow `python scripts/run_research_data_pipeline.py` from backend/
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import get_settings
from app.research.data_pipeline import PipelineConfig, ResearchDataPipeline
from app.services.database import db_manager


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Research historical data pipeline")
    p.add_argument(
        "--mode",
        choices=("smoke", "full", "sync"),
        default="smoke",
        help="smoke=BTC/ETH/SOL ~1y; full=legacy download; sync=DB-first incremental",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan only: inspect Postgres coverage, zero Binance requests",
    )
    p.add_argument("--symbols", nargs="*", default=None, help="Optional symbol list")
    p.add_argument(
        "--discover",
        action="store_true",
        help="Discover Binance USDⓈ-M perpetuals dynamically",
    )
    p.add_argument("--period-start", default=None, help="YYYY-MM-DD")
    p.add_argument("--period-end", default=None, help="YYYY-MM-DD")
    p.add_argument("--start", dest="period_start_alias", default=None, help="Alias for --period-start")
    p.add_argument("--end", dest="period_end_alias", default=None, help="Alias for --period-end")
    p.add_argument(
        "--timeframes",
        nargs="*",
        default=None,
        help="e.g. 5m 15m 1h 4h or 5m,15m,1h,4h",
    )
    p.add_argument("--max-workers", type=int, default=4)
    p.add_argument("--chunk-days", type=int, default=90)
    p.add_argument(
        "--no-features",
        action="store_true",
        help="Download/validate only; skip feature/event store",
    )
    p.add_argument(
        "--evaluate-masks",
        action="store_true",
        help="After features, count S1–S3/C1–C4 event matches (not optimization)",
    )
    p.add_argument("--dataset-label", default="v1")
    p.add_argument(
        "--out",
        default=None,
        help="Write JSON result to this path",
    )
    return p.parse_args()


async def _main() -> int:
    args = _parse_args()
    settings = get_settings()
    await db_manager.connect(settings)

    period_start = args.period_start or args.period_start_alias
    period_end = args.period_end or args.period_end_alias
    tfs = args.timeframes
    if tfs and len(tfs) == 1 and "," in tfs[0]:
        tfs = [x.strip() for x in tfs[0].split(",") if x.strip()]
    default_tfs = ("5m", "15m", "1h", "4h") if args.mode in ("full", "sync") else ("15m",)
    cfg = PipelineConfig.from_mapping(
        {
            "max_workers": args.max_workers,
            "chunk_days": args.chunk_days,
            "period_start": period_start
            or (
                "2020-10-01"
                if args.mode in ("full", "sync")
                else PipelineConfig().period_start
            ),
            "period_end": period_end,
            "timeframes": tuple(tfs) if tfs else default_tfs,
            "stop_after_dataset": not args.evaluate_masks,
        }
    )

    pipeline = ResearchDataPipeline(cfg)
    result = await pipeline.run(
        symbols=args.symbols,
        discover=args.discover
        or (args.mode in ("full", "sync") and not args.symbols),
        build_features=(not args.no_features)
        and not args.dry_run
        and args.mode != "sync",
        evaluate_event_masks=args.evaluate_masks,
        dataset_label=args.dataset_label,
        mode=args.mode,
        dry_run=args.dry_run,
    )

    text = json.dumps(result, indent=2, default=str)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    ok = result.get("status") in ("DONE", "PARTIAL", "DRY_RUN", "SKIP")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
