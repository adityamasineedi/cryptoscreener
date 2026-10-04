#!/usr/bin/env python3
"""One-command advance for dynamic candidates: data-health → COMBO_02 backtest → OOS.

Research only. Never paper-trades, never Telegram, never joins v1.

Usage (from backend/):
  python scripts/advance_dynamic_candidates.py
  python scripts/advance_dynamic_candidates.py --symbol BNBUSDT
  python scripts/advance_dynamic_candidates.py --skip-oos
  python scripts/advance_dynamic_candidates.py --health-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT.parent / ".env")
load_dotenv(ROOT / ".env")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.research.advance_dynamic_candidates import advance_dynamic_candidates  # noqa: E402
from app.services.database import db_manager  # noqa: E402


async def _connect_db_light() -> bool:
    settings = get_settings()
    if not settings.database_enabled:
        print("ERROR: DATABASE_ENABLED=false", file=sys.stderr)
        return False
    if db_manager.enabled and db_manager.engine is not None:
        return True
    try:
        from sqlalchemy import text

        engine = create_async_engine(
            settings.database_url, pool_pre_ping=True, pool_size=2, max_overflow=2
        )
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        db_manager.engine = engine
        db_manager.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        db_manager.enabled = True
        db_manager.status = "ok"
        db_manager.schema_ready = True
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: database_unavailable: {exc}", file=sys.stderr)
        return False


def _print_table(summary: dict) -> None:
    rows = summary.get("candidates") or []
    print("\n=== Dynamic candidates (after advance) ===")
    print(
        f"{'SYMBOL':<12} {'STATE':<20} {'TIER':<16} {'BT_n':>5} {'avgR':>7} "
        f"{'OOS':<22} {'BLOCK':<40}"
    )
    for r in rows:
        block = (r.get("data_health_block_reason") or r.get("state_reason") or "")[:40]
        avg_r = r.get("backtest_net_avg_r")
        avg_s = f"{avg_r:.2f}" if avg_r is not None else "—"
        n = r.get("backtest_trade_count")
        n_s = str(n) if n is not None else "—"
        print(
            f"{str(r.get('symbol') or ''):<12} "
            f"{str(r.get('state') or ''):<20} "
            f"{str(r.get('backtest_tier') or '—'):<16} "
            f"{n_s:>5} "
            f"{avg_s:>7} "
            f"{str(r.get('oos_status') or '—'):<22} "
            f"{block:<40}"
        )
    run = summary.get("run_summary") or {}
    reg = summary.get("registry_summary") or {}
    print(
        "\nThis run: "
        f"health_ready={run.get('health_ready')}  "
        f"backtests_started={run.get('backtests_started')}  "
        f"oos_started={run.get('oos_started')}  "
        f"advanced={run.get('advanced')}  "
        f"rejected={run.get('rejected')}  "
        f"errors={run.get('errors')}"
    )
    print(
        "Registry: "
        f"discovered={reg.get('discovered')}  "
        f"data_pending={reg.get('data_pending')}  "
        f"data_ready={reg.get('data_ready')}  "
        f"backtest_completed={reg.get('backtest_completed')}  "
        f"research_rejected={reg.get('research_rejected')}  "
        f"oos_failed={reg.get('oos_failed')}  "
        f"v2_paper_candidate={reg.get('v2_paper_candidate')}  "
        f"paper_validating={reg.get('paper_validating')}  "
        f"production_approved={reg.get('production_approved')}  "
        f"suspended={reg.get('suspended')}"
    )
    blocked = [
        h
        for h in (summary.get("health") or [])
        if not h.get("ready") and h.get("block_reason")
    ]
    if blocked:
        print("\nData blockers (must fix 1h+4h before backtest):")
        for h in blocked:
            print(f"  {h.get('symbol')}: {h.get('block_reason')}")


async def _main(args: argparse.Namespace) -> int:
    if not await _connect_db_light():
        return 1
    try:
        from app.research.strategy_candidate_registry import strategy_candidate_registry

        await strategy_candidate_registry.ensure_schema()
        summary = await advance_dynamic_candidates(
            symbols=args.symbol or None,
            run_data_health=not args.backtest_only,
            run_backtest=not args.health_only,
            run_oos=not args.skip_oos and not args.health_only,
            limit=args.limit,
        )
    finally:
        await db_manager.close()

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        _print_table(summary)
        print(f"\n{summary.get('disclaimer')}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="Limit to symbol(s); repeatable. Default: all registry rows.",
    )
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--skip-oos", action="store_true")
    ap.add_argument("--health-only", action="store_true")
    ap.add_argument("--backtest-only", action="store_true")
    ap.add_argument("--json", action="store_true", help="Print full JSON summary")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args)))
