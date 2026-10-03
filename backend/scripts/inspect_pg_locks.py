"""Inspect PostgreSQL activity/locks during startup hangs (read-only)."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _ser(v):
    if isinstance(v, timedelta):
        return v.total_seconds()
    return str(v) if v is not None and not isinstance(v, (str, int, float, bool)) else v


async def main() -> None:
    eng = create_async_engine(
        "postgresql+asyncpg://screener:screener@localhost:5432/cryptoscreener",
        pool_pre_ping=True,
    )
    async with eng.connect() as conn:
        await conn.execute(text("SET statement_timeout = '8000'"))
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT pid, usename, application_name, state,
                           wait_event_type, wait_event,
                           EXTRACT(EPOCH FROM (now() - query_start)) AS duration_s,
                           left(query, 240) AS query
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                    ORDER BY query_start NULLS LAST
                    """
                )
            )
        ).mappings().all()
        print("=== pg_stat_activity ===")
        print(json.dumps([{k: _ser(v) for k, v in dict(r).items()} for r in rows], indent=2))

        locks = (
            await conn.execute(
                text(
                    """
                    SELECT a.pid, a.state, a.wait_event_type, a.wait_event,
                           EXTRACT(EPOCH FROM (now() - a.query_start)) AS duration_s,
                           l.locktype, l.mode, l.granted,
                           left(a.query, 200) AS query
                    FROM pg_locks l
                    JOIN pg_stat_activity a ON a.pid = l.pid
                    WHERE a.datname = current_database()
                      AND (NOT l.granted OR a.wait_event_type IS NOT NULL)
                    ORDER BY l.granted, a.query_start NULLS LAST
                    LIMIT 80
                    """
                )
            )
        ).mappings().all()
        print("=== locks/waiting ===")
        print(json.dumps([{k: _ser(v) for k, v in dict(r).items()} for r in locks], indent=2))

        blocked = (
            await conn.execute(
                text(
                    """
                    SELECT blocked.pid AS blocked_pid,
                           blocking.pid AS blocking_pid,
                           EXTRACT(EPOCH FROM (now() - blocked.query_start)) AS blocked_duration_s,
                           left(blocked.query, 200) AS blocked_query,
                           left(blocking.query, 200) AS blocking_query,
                           blocked.wait_event_type,
                           blocked.wait_event
                    FROM pg_stat_activity blocked
                    JOIN pg_locks bl ON bl.pid = blocked.pid AND NOT bl.granted
                    JOIN pg_locks kl ON kl.locktype = bl.locktype
                      AND kl.DATABASE IS NOT DISTINCT FROM bl.DATABASE
                      AND kl.relation IS NOT DISTINCT FROM bl.relation
                      AND kl.page IS NOT DISTINCT FROM bl.page
                      AND kl.tuple IS NOT DISTINCT FROM bl.tuple
                      AND kl.virtualxid IS NOT DISTINCT FROM bl.virtualxid
                      AND kl.transactionid IS NOT DISTINCT FROM bl.transactionid
                      AND kl.classid IS NOT DISTINCT FROM bl.classid
                      AND kl.objid IS NOT DISTINCT FROM bl.objid
                      AND kl.objsubid IS NOT DISTINCT FROM bl.objsubid
                      AND kl.pid <> bl.pid
                      AND kl.granted
                    JOIN pg_stat_activity blocking ON blocking.pid = kl.pid
                    WHERE blocked.datname = current_database()
                    """
                )
            )
        ).mappings().all()
        print("=== blocked ===")
        print(json.dumps([{k: _ser(v) for k, v in dict(r).items()} for r in blocked], indent=2))

        idx = (
            await conn.execute(
                text(
                    """
                    SELECT * FROM pg_stat_progress_create_index
                    """
                )
            )
        ).mappings().all()
        print("=== create_index_progress ===")
        print(json.dumps([{k: _ser(v) for k, v in dict(r).items()} for r in idx], indent=2))

    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
