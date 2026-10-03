#!/usr/bin/env python3
"""Manual / scheduled dynamic candidate discovery (daily, not every 15m tick).

Does not create paper trades, Telegram alerts, or v1 promotions.

Usage (from backend/):
  python scripts/run_dynamic_candidate_discovery.py
  python scripts/run_dynamic_candidate_discovery.py --top-n 30
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
from app.research.dynamic_candidate_discovery import run_dynamic_discovery  # noqa: E402
from app.services.database import db_manager  # noqa: E402


async def _connect_db_light() -> bool:
    """Attach to Postgres without running full schema DDL (avoids lock fights)."""
    settings = get_settings()
    if not settings.database_enabled:
        return False
    if db_manager.enabled and db_manager.engine is not None:
        return True
    try:
        engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=2,
        )
        async with engine.begin() as conn:
            from sqlalchemy import text

            await conn.execute(text("SELECT 1"))
        db_manager.engine = engine
        db_manager.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        db_manager.enabled = True
        db_manager.status = "ok"
        db_manager.schema_ready = True
        return True
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "error": f"database_unavailable:{type(exc).__name__}:{exc}",
                    "hint": "Set DATABASE_ENABLED=true and ensure Postgres is reachable.",
                },
                indent=2,
            )
        )
        return False


async def _main(top_n: int) -> int:
    if not await _connect_db_light():
        return 1
    try:
        # Registry DDL only (IF NOT EXISTS) — cheap vs full app schema init.
        await strategy_candidate_registry_ensure()
        summary = await run_dynamic_discovery(top_n=top_n)
    finally:
        await db_manager.close()

    print(json.dumps(summary, indent=2, default=str))
    print(
        f"\nDynamic discovery completed:\n"
        f"selected = {summary.get('selected')}\n"
        f"new = {summary.get('new')}\n"
        f"already_registered = {summary.get('already_registered')}\n"
        f"excluded = {summary.get('excluded')}\n"
        f"reasons = {summary.get('reasons')}"
    )
    if int(summary.get("selected") or 0) == 0 and int(summary.get("excluded") or 0) == 0:
        print(
            "\nNOTE: empty universe — no futures metadata/volumes found in DB/market_store.",
            file=sys.stderr,
        )
        return 2
    return 0


async def strategy_candidate_registry_ensure() -> None:
    from app.research.strategy_candidate_registry import strategy_candidate_registry

    await strategy_candidate_registry.ensure_schema()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-n", type=int, default=30)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args.top_n)))
