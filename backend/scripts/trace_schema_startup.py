"""Trace DatabaseManager.ensure_schema stages with timeouts (diagnostic)."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.services.database import _SCHEMA_STATEMENTS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def stage(name: str, coro, timeout: float = 30.0) -> dict:
    started = time.perf_counter()
    started_at = _now()
    try:
        await asyncio.wait_for(coro, timeout=timeout)
        status = "OK"
        error = None
    except asyncio.TimeoutError:
        status = "TIMEOUT"
        error = f"exceeded {timeout}s"
    except Exception as exc:  # noqa: BLE001
        status = "ERROR"
        error = f"{type(exc).__name__}: {exc}"
    dur = (time.perf_counter() - started) * 1000.0
    row = {
        "startup_stage": name,
        "started_at": started_at,
        "finished_at": _now(),
        "duration_ms": round(dur, 1),
        "status": status,
        "error": error,
    }
    print(json.dumps(row), flush=True)
    return row


async def main() -> None:
    settings = get_settings()
    results: list[dict] = []
    eng = create_async_engine(settings.database_url, pool_pre_ping=True, pool_size=2)

    async def db_connect():
        async with eng.begin() as conn:
            await conn.execute(text("SELECT 1"))

    results.append(await stage("DB_CONNECT", db_connect(), timeout=10))

    timescale = False

    async def timescale_check():
        nonlocal timescale
        try:
            async with eng.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
            timescale = True
        except Exception:  # noqa: BLE001
            timescale = False

    results.append(await stage("TIMESCALE_CHECK", timescale_check(), timeout=15))
    print(json.dumps({"timescale": timescale}), flush=True)

    # Run DDL in chunks to locate hang
    chunk_size = 5
    for i in range(0, len(_SCHEMA_STATEMENTS), chunk_size):
        chunk = _SCHEMA_STATEMENTS[i : i + chunk_size]
        stmts = list(chunk)

        async def run_chunk(statements=stmts, idx=i):
            async with eng.begin() as conn:
                for n, stmt in enumerate(statements):
                    t0 = time.perf_counter()
                    await conn.execute(text(stmt))
                    ms = (time.perf_counter() - t0) * 1000
                    preview = " ".join(stmt.split())[:100]
                    print(
                        json.dumps(
                            {
                                "startup_stage": "DDL_STMT",
                                "index": idx + n,
                                "duration_ms": round(ms, 1),
                                "preview": preview,
                            }
                        ),
                        flush=True,
                    )

        name = f"SCHEMA_INIT_CHUNK_{i}_{i + len(chunk) - 1}"
        row = await stage(name, run_chunk(), timeout=60)
        results.append(row)
        if row["status"] != "OK":
            break

    print(json.dumps({"summary": results, "timescale": timescale}, indent=2))
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
