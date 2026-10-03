"""Multi-process research sync locks (DB-backed + advisory).

Granularity: operation + symbol + timeframe + range.
Unrelated symbols/timeframes may run concurrently.
"""

from __future__ import annotations

import hashlib
import os
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from sqlalchemy import text

from app.core.logging import get_logger
from app.services.database import db_manager

logger = get_logger("research_data_pipeline.locks")

OP_DATA_SYNC = "DATA_SYNC"
OP_FEATURE_BUILD = "FEATURE_BUILD"
OP_STRATEGY_BACKTEST = "STRATEGY_BACKTEST"

STATUS_ACTIVE = "ACTIVE"
STATUS_STALE = "STALE"
STATUS_RELEASED = "RELEASED"

SYNC_ALREADY_RUNNING = "SYNC_ALREADY_RUNNING"

DEFAULT_STALE_SECONDS = 300

def _advisory_keys(lock_key: str) -> tuple[int, int]:
    digest = hashlib.sha256(lock_key.encode("utf-8")).digest()
    k1 = int.from_bytes(digest[:4], "big", signed=True)
    k2 = int.from_bytes(digest[4:8], "big", signed=True)
    return k1, k2

def make_lock_key(
    operation: str,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> str:
    return f"{operation}|{symbol.upper()}|{timeframe}|{start_ms}|{end_ms}"

@dataclass
class LockHandle:
    lock_key: str
    operation: str
    symbol: str
    timeframe: str
    range_start_ms: int
    range_end_ms: int
    run_id: str
    acquired: bool
    reason: str | None = None

async def try_acquire_range_lock(
    *,
    operation: str,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    run_id: str,
    stale_after_seconds: int = DEFAULT_STALE_SECONDS,
) -> LockHandle:
    """Acquire non-overlapping range lock. Overlapping ACTIVE locks → SYNC_ALREADY_RUNNING."""
    sym = symbol.upper()
    lock_key = make_lock_key(operation, sym, timeframe, start_ms, end_ms)
    handle = LockHandle(
        lock_key=lock_key,
        operation=operation,
        symbol=sym,
        timeframe=timeframe,
        range_start_ms=start_ms,
        range_end_ms=end_ms,
        run_id=run_id,
        acquired=False,
    )
    if db_manager.engine is None:
        handle.reason = "DATABASE_UNAVAILABLE"
        return handle

    pid = os.getpid()
    host = socket.gethostname()
    k1, k2 = _advisory_keys(lock_key)
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=stale_after_seconds)

    async with db_manager.engine.begin() as conn:
        # Mark stale heartbeats
        await conn.execute(
            text(
                """
                UPDATE research_sync_locks
                SET status = :stale
                WHERE status = :active
                  AND last_heartbeat < :stale_before
                """
            ),
            {
                "stale": STATUS_STALE,
                "active": STATUS_ACTIVE,
                "stale_before": stale_before,
            },
        )

        # Overlap check for same symbol+tf+operation
        overlap = (
            await conn.execute(
                text(
                    """
                    SELECT lock_key, run_id, pid
                    FROM research_sync_locks
                    WHERE operation = :op
                      AND symbol = :s
                      AND timeframe = :tf
                      AND status = :active
                      AND range_start_ms < :end_ms
                      AND range_end_ms > :start_ms
                    LIMIT 1
                    """
                ),
                {
                    "op": operation,
                    "s": sym,
                    "tf": timeframe,
                    "active": STATUS_ACTIVE,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                },
            )
        ).fetchone()
        if overlap:
            handle.reason = SYNC_ALREADY_RUNNING
            logger.info(
                "sync_lock_busy",
                lock_key=lock_key,
                holder=overlap[1],
                holder_pid=overlap[2],
            )
            return handle

        got = (
            await conn.execute(
                text("SELECT pg_try_advisory_lock(:k1, :k2)"),
                {"k1": k1, "k2": k2},
            )
        ).scalar()
        if not got:
            handle.reason = SYNC_ALREADY_RUNNING
            return handle

        await conn.execute(
            text(
                """
                INSERT INTO research_sync_locks (
                    lock_key, operation, symbol, timeframe,
                    range_start_ms, range_end_ms, run_id, pid, hostname,
                    status, acquired_at, last_heartbeat
                ) VALUES (
                    :lock_key, :op, :s, :tf,
                    :start_ms, :end_ms, :run_id, :pid, :host,
                    :active, NOW(), NOW()
                )
                ON CONFLICT (lock_key) DO UPDATE SET
                    run_id = EXCLUDED.run_id,
                    pid = EXCLUDED.pid,
                    hostname = EXCLUDED.hostname,
                    status = EXCLUDED.status,
                    acquired_at = NOW(),
                    last_heartbeat = NOW(),
                    range_start_ms = EXCLUDED.range_start_ms,
                    range_end_ms = EXCLUDED.range_end_ms
                """
            ),
            {
                "lock_key": lock_key,
                "op": operation,
                "s": sym,
                "tf": timeframe,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "run_id": run_id,
                "pid": pid,
                "host": host,
                "active": STATUS_ACTIVE,
            },
        )

    handle.acquired = True
    return handle

async def heartbeat_lock(lock_key: str) -> None:
    if db_manager.engine is None:
        return
    async with db_manager.engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE research_sync_locks
                SET last_heartbeat = NOW()
                WHERE lock_key = :k AND status = :active
                """
            ),
            {"k": lock_key, "active": STATUS_ACTIVE},
        )

async def release_range_lock(handle: LockHandle) -> None:
    if not handle.acquired or db_manager.engine is None:
        return
    k1, k2 = _advisory_keys(handle.lock_key)
    async with db_manager.engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE research_sync_locks
                SET status = :released, last_heartbeat = NOW()
                WHERE lock_key = :k
                """
            ),
            {"released": STATUS_RELEASED, "k": handle.lock_key},
        )
        await conn.execute(
            text("SELECT pg_advisory_unlock(:k1, :k2)"),
            {"k1": k1, "k2": k2},
        )
    handle.acquired = False

@asynccontextmanager
async def range_lock(
    *,
    operation: str,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    run_id: str,
) -> AsyncIterator[LockHandle]:
    handle = await try_acquire_range_lock(
        operation=operation,
        symbol=symbol,
        timeframe=timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        run_id=run_id,
    )
    try:
        yield handle
    finally:
        if handle.acquired:
            await release_range_lock(handle)

async def count_active_data_sync_runs() -> int:
    if db_manager.engine is None:
        return 0
    async with db_manager.engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT run_id)
                    FROM research_sync_locks
                    WHERE operation = :op AND status = :active
                    """
                ),
                {"op": OP_DATA_SYNC, "active": STATUS_ACTIVE},
            )
        ).fetchone()
    return int(row[0] or 0) if row else 0

def warn_global_concurrency(active_runs: int, max_global: int) -> dict[str, Any]:
    return {
        "active_data_sync_runs": active_runs,
        "max_global_research_workers_hint": max_global,
        "warning": (
            f"Concurrent DATA_SYNC runs detected: {active_runs}. "
            "Cap per-run workers to avoid aggregate Binance rate-limit pressure."
            if active_runs > 1
            else None
        ),
    }

