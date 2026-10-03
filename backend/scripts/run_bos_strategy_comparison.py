#!/usr/bin/env python3
"""CLI: BOS strategy comparison research (historical only).

Examples:
  # Small validation first
  python scripts/run_bos_strategy_comparison.py --symbols BTCUSDT,ETHUSDT,SOLUSDT --limit 1500

  # Full eligible universe (uses whatever Postgres history exists — does not invent depth)
  python scripts/run_bos_strategy_comparison.py --all-eligible --persist

Research only — live engine unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load repo .env if present
_env = ROOT.parent / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


async def _main() -> int:
    parser = argparse.ArgumentParser(description="BOS strategy comparison research")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--all-eligible", action="store_true")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--start-date", default="2023-01-01")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--persist", action="store_true", default=True)
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--out", default=None, help="Write JSON summary path")
    args = parser.parse_args()

    from app.config import get_settings
    from app.services.database import db_manager
    from app.research.bos_strategy_comparison.service import (
        get_bos_strategy_comparison_service,
    )

    settings = get_settings()
    await db_manager.connect(settings)
    svc = get_bos_strategy_comparison_service()

    symbols = None
    if not args.all_eligible:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        print("=== DATA COVERAGE (explicit symbols; full audit skipped) ===")
        print(json.dumps({"symbols": symbols}, indent=2))
    else:
        coverage = await svc.data_coverage()
        universe = coverage.get("universe") or {}
        print("=== DATA COVERAGE ===")
        print(
            json.dumps(
                {
                    "eligible_count": universe.get("eligible_count"),
                    "excluded_count": universe.get("excluded_count"),
                    "full_universe_count": universe.get("full_universe_count"),
                    "audit": (universe.get("audit") or {}),
                    "note": (
                        "Postgres depth may be shorter than 2020-10-01. "
                        "Never fabricates missing history."
                    ),
                },
                indent=2,
                default=str,
            )
        )

    payload = await svc.compare(
        symbols=symbols,
        timeframe=args.timeframe,
        start_date=args.start_date,
        end_date=args.end_date,
        limit=args.limit,
        persist=not args.no_persist,
        include_walk_forward=args.walk_forward,
        max_symbols=args.max_symbols,
    )

    print("\n=== STRATEGIES / CONTROLS (Historical Result) ===")
    for row in payload.get("results") or []:
        m = row.get("metrics") or row
        print(
            f"{row.get('strategy_id')}: ALL n={m.get('sample_size')} "
            f"WR={m.get('win_rate')} E={m.get('expectancy_R')} "
            f"PF={m.get('profit_factor')} DD={m.get('max_drawdown_R')}"
        )
        long = row.get("long") or {}
        short = row.get("short") or {}
        print(
            f"  LONG n={long.get('sample_size')} E={long.get('expectancy_R')} | "
            f"SHORT n={short.get('sample_size')} E={short.get('expectancy_R')}"
        )

    print("\nResearch only — live engine unchanged.")
    print(f"run_id={payload.get('run_id')} trade_count={payload.get('trade_count')}")

    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.out}")

    await db_manager.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
