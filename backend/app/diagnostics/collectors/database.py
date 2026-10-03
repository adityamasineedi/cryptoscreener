"""PostgreSQL forensics — fast health vs detailed catalog (cached).

Never scan OHLCV rows. Expensive catalog work is isolated to /database/detail.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from sqlalchemy import text

from app.diagnostics.constants import HealthStatus
from app.diagnostics.redact import redact_string

WATCH_TABLES = (
    "ohlcv",
    "signals",
    "structure_events",
    "setup_analyses",
    "setup_events",
    "supply_demand_zones",
    "funding_rates",
    "open_interest",
    "liquidations",
    "research_runs",
    "research_results",
    "research_trades",
    "research_pipeline_runs",
    "research_data_manifest",
    "research_sync_tasks",
    "research_sync_locks",
    "research_data_conflicts",
    "diagnostic_issues",
    "diagnostic_events",
    "paper_trades",
    "alerts",
    "sentiment_snapshots",
)

# Marker used by tests to assert fast path never touches heavy SQL.
HEAVY_SQL_MARKERS = (
    "pg_total_relation_size",
    "pg_stat_user_tables",
    "last_vacuum",
    "n_dead_tup",
    "make_interval",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _safe_db_name(database_url: str) -> str | None:
    try:
        redacted = redact_string(database_url) or database_url
        parsed = urlparse(redacted.replace("postgresql+asyncpg://", "postgresql://"))
        return parsed.path.lstrip("/") or None
    except Exception:  # noqa: BLE001
        return None


def _pool_stats(engine: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "pool_size": None,
        "checked_out": None,
        "checked_in": None,
        "overflow": None,
        "utilization_pct": None,
        "status": HealthStatus.UNKNOWN.value,
        "reason": None,
    }
    try:
        pool = engine.pool
        size = int(pool.size()) if hasattr(pool, "size") else None
        checked_out = int(pool.checkedout()) if hasattr(pool, "checkedout") else None
        checked_in = int(pool.checkedin()) if hasattr(pool, "checkedin") else None
        overflow = int(pool.overflow()) if hasattr(pool, "overflow") else None
        out.update(
            {
                "pool_size": size,
                "checked_out": checked_out,
                "checked_in": checked_in,
                "overflow": overflow,
            }
        )
        if size is not None and checked_out is not None and size > 0:
            util = 100.0 * checked_out / max(size + max(overflow or 0, 0), 1)
            out["utilization_pct"] = round(util, 2)
            out["status"] = HealthStatus.HEALTHY.value
            out["reason"] = f"pool checked_out={checked_out}/{size}"
        else:
            out["reason"] = "pool metrics partially unavailable"
    except Exception as exc:  # noqa: BLE001
        out["status"] = HealthStatus.UNKNOWN.value
        out["reason"] = f"pool_stats_failed:{exc.__class__.__name__}"
    return out


def _section_unknown(reason: str, *, duration_ms: float | None = None) -> dict[str, Any]:
    return {
        "status": HealthStatus.UNKNOWN.value,
        "reason": reason,
        "duration_ms": duration_ms,
        "error_category": reason.split(":")[0] if reason else "UNKNOWN",
    }


async def _measure(
    timings: dict[str, float | None],
    key: str,
    fn: Callable[[], Awaitable[Any]],
) -> tuple[Any, Exception | None]:
    t0 = time.perf_counter()
    try:
        result = await fn()
        timings[key] = round((time.perf_counter() - t0) * 1000, 2)
        return result, None
    except Exception as exc:  # noqa: BLE001
        timings[key] = round((time.perf_counter() - t0) * 1000, 2)
        return None, exc


def _apply_pool_issues(base: dict[str, Any], settings: Any) -> None:
    pool_util = (base.get("pool") or {}).get("utilization_pct")
    pool_warn = float(getattr(settings, "diag_db_pool_warning_pct", 80) or 80)
    pool_err = float(getattr(settings, "diag_db_pool_error_pct", 95) or 95)
    if pool_util is not None and pool_util >= pool_err:
        base["issues_candidates"].append(
            {
                "error_code": "DB_POOL_EXHAUSTED",
                "severity": "ERROR",
                "message": "Connection pool near exhaustion",
                "expected": f"utilization < {pool_err}%",
                "actual": f"{pool_util}%",
            }
        )
        base["status"] = HealthStatus.ERROR.value
        base["reason"] = "DB_POOL_EXHAUSTED"
    elif pool_util is not None and pool_util >= pool_warn:
        base["issues_candidates"].append(
            {
                "error_code": "DB_POOL_HIGH",
                "severity": "WARNING",
                "message": "Connection pool utilization high",
                "expected": f"utilization < {pool_warn}%",
                "actual": f"{pool_util}%",
            }
        )
        if base["status"] == HealthStatus.HEALTHY.value:
            base["status"] = HealthStatus.WARNING.value
            base["reason"] = "pool utilization high"


def _slowest_from_timings(timings: dict[str, float | None], *, n: int = 5) -> list[dict[str, Any]]:
    items = [
        {"name": k, "duration_ms": v}
        for k, v in timings.items()
        if isinstance(v, (int, float))
    ]
    items.sort(key=lambda x: float(x["duration_ms"]), reverse=True)
    return items[:n]


# ---------------------------------------------------------------------------
# FAST HEALTH — lightweight only
# ---------------------------------------------------------------------------


async def collect_database_fast(settings: Any) -> dict[str, Any]:
    """Lightweight DB health for overview + frequent UI polling."""
    from app.services.database import db_manager

    timings: dict[str, float | None] = {
        "connection_check_ms": None,
        "database_size_ms": None,
        "connections_ms": None,
        "pool_ms": None,
        "locks_ms": None,
    }
    t_total = time.perf_counter()
    checked = _utcnow_iso()
    base: dict[str, Any] = {
        "phase": 2.1,
        "mode": "fast",
        "last_checked": checked,
        "collector": "diagnostics.collectors.database.collect_database_fast",
        "file": "backend/app/diagnostics/collectors/database.py",
        "function": "collect_database_fast",
        "status": HealthStatus.UNKNOWN.value,
        "reason": None,
        "latency_ms": None,
        "database_name": None,
        "database_size_bytes": None,
        "connections": {},
        "pool": {},
        "lock_count": None,
        "timings": timings,
        "issues_candidates": [],
        "errors": [],
    }

    if not db_manager.enabled or db_manager.engine is None:
        base["status"] = HealthStatus.DISABLED.value
        base["reason"] = "DATABASE_ENABLED=false or engine unavailable"
        base["collection_duration_ms"] = round((time.perf_counter() - t_total) * 1000, 2)
        return base

    base["database_name"] = _safe_db_name(settings.database_url)

    t_pool = time.perf_counter()
    base["pool"] = _pool_stats(db_manager.engine)
    timings["pool_ms"] = round((time.perf_counter() - t_pool) * 1000, 2)

    # One round-trip: connectivity + size + connections + cheap lock count
    async def _fast_bundle() -> dict[str, Any]:
        async with db_manager.engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT
                          1 AS ok,
                          (SELECT count(*) FILTER (WHERE state = 'active')
                             FROM pg_stat_activity
                            WHERE datname = current_database()) AS active,
                          (SELECT count(*) FILTER (WHERE state = 'idle')
                             FROM pg_stat_activity
                            WHERE datname = current_database()) AS idle,
                          (SELECT count(*) FILTER (WHERE wait_event_type IS NOT NULL)
                             FROM pg_stat_activity
                            WHERE datname = current_database()) AS waiting,
                          (SELECT count(*)
                             FROM pg_stat_activity
                            WHERE datname = current_database()) AS total,
                          (SELECT setting::int FROM pg_settings
                            WHERE name = 'max_connections') AS max_connections,
                          pg_database_size(current_database()) AS db_size,
                          (SELECT count(*) FROM pg_locks WHERE NOT granted) AS lock_waits
                        """
                    )
                )
            ).one()
            return {
                "active": int(row[1] or 0),
                "idle": int(row[2] or 0),
                "waiting": int(row[3] or 0),
                "total": int(row[4] or 0),
                "max_connections": int(row[5]) if row[5] is not None else None,
                "database_size_bytes": int(row[6]) if row[6] is not None else None,
                "lock_count": int(row[7] or 0),
            }

    bundle, bundle_err = await _measure(timings, "connection_check_ms", _fast_bundle)
    # Same round-trip — report measured duration on sibling keys (not invented)
    timings["connections_ms"] = timings["connection_check_ms"]
    timings["database_size_ms"] = timings["connection_check_ms"]
    timings["locks_ms"] = timings["connection_check_ms"]
    base["latency_ms"] = timings["connection_check_ms"]

    if bundle_err is not None:
        base["status"] = HealthStatus.UNAVAILABLE.value
        base["reason"] = f"DB_CONNECTION_FAILED:{bundle_err.__class__.__name__}"
        base["errors"].append({"code": "DB_CONNECTION_FAILED", "error": str(bundle_err)})
        base["issues_candidates"].append(
            {
                "error_code": "DB_CONNECTION_FAILED",
                "severity": "CRITICAL",
                "message": f"PostgreSQL connection failed: {bundle_err.__class__.__name__}",
                "expected": "fast health bundle succeeds",
                "actual": str(bundle_err),
            }
        )
        base["connections"] = _section_unknown(
            f"INSUFFICIENT_PERMISSION_OR_ERROR:{bundle_err.__class__.__name__}",
            duration_ms=timings["connections_ms"],
        )
        base["connections"].update(
            {
                "active": None,
                "idle": None,
                "waiting": None,
                "total": None,
                "max_connections": None,
            }
        )
        base["database_size_bytes"] = None
        base["lock_count"] = None
        base["collection_duration_ms"] = round((time.perf_counter() - t_total) * 1000, 2)
        base["slowest_queries"] = _slowest_from_timings(timings)
        return base

    assert bundle is not None
    base["status"] = HealthStatus.HEALTHY.value
    base["reason"] = "fast health bundle succeeded"
    base["connections"] = {
        "active": bundle["active"],
        "idle": bundle["idle"],
        "waiting": bundle["waiting"],
        "total": bundle["total"],
        "max_connections": bundle["max_connections"],
        "status": HealthStatus.HEALTHY.value,
    }
    base["database_size_bytes"] = bundle["database_size_bytes"]
    base["lock_count"] = bundle["lock_count"]

    _apply_pool_issues(base, settings)
    base["collection_duration_ms"] = round((time.perf_counter() - t_total) * 1000, 2)
    base["slowest_queries"] = _slowest_from_timings(timings)
    return base


# ---------------------------------------------------------------------------
# DETAILED FORENSICS — expensive, cached
# ---------------------------------------------------------------------------


class _DetailCache:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._payload: dict[str, Any] | None = None
        self._collected_at: datetime | None = None
        self._collection_duration_ms: float | None = None

    def clear(self) -> None:
        self._payload = None
        self._collected_at = None
        self._collection_duration_ms = None

    def snapshot_meta(self, cache_seconds: float) -> dict[str, Any]:
        if self._payload is None or self._collected_at is None:
            return {
                "cached": False,
                "collected_at": None,
                "cache_age_seconds": None,
                "collection_duration_ms": None,
                "cache_ttl_seconds": cache_seconds,
            }
        age = (_utcnow() - self._collected_at).total_seconds()
        return {
            "cached": True,
            "collected_at": self._collected_at.isoformat(),
            "cache_age_seconds": round(age, 3),
            "collection_duration_ms": self._collection_duration_ms,
            "cache_ttl_seconds": cache_seconds,
            "cache_valid": age < cache_seconds,
        }


_detail_cache = _DetailCache()


def reset_database_detail_cache() -> None:
    """Test helper — clear detail cache."""
    _detail_cache.clear()


async def collect_database_detail_uncached(settings: Any) -> dict[str, Any]:
    """Run expensive catalog/query forensics once (no cache)."""
    from app.services.database import db_manager

    timings: dict[str, float | None] = {
        "connection_check_ms": None,
        "database_size_ms": None,
        "connections_ms": None,
        "pool_ms": None,
        "locks_ms": None,
        "long_queries_ms": None,
        "blocked_queries_ms": None,
        "oldest_transaction_ms": None,
        "table_sizes_ms": None,
        "vacuum_stats_ms": None,
    }
    t_total = time.perf_counter()
    collected_at = _utcnow()
    base: dict[str, Any] = {
        "phase": 2.1,
        "mode": "detail",
        "last_checked": collected_at.isoformat(),
        "collector": "diagnostics.collectors.database.collect_database_detail_uncached",
        "file": "backend/app/diagnostics/collectors/database.py",
        "function": "collect_database_detail_uncached",
        "status": HealthStatus.UNKNOWN.value,
        "reason": None,
        "latency_ms": None,
        "database_name": None,
        "database_size_bytes": None,
        "connections": {},
        "pool": {},
        "queries": {},
        "tables": [],
        "table_sizes": {"status": HealthStatus.UNKNOWN.value, "reason": None},
        "vacuum_stats": {"status": HealthStatus.UNKNOWN.value, "reason": None},
        "timings": timings,
        "timeline": {
            "collection_started": collected_at.isoformat(),
            "collection_completed": None,
            "duration_ms": None,
        },
        "issues_candidates": [],
        "errors": [],
    }

    if not db_manager.enabled or db_manager.engine is None:
        base["status"] = HealthStatus.DISABLED.value
        base["reason"] = "DATABASE_ENABLED=false or engine unavailable"
        base["collection_duration_ms"] = round((time.perf_counter() - t_total) * 1000, 2)
        base["timeline"]["collection_completed"] = _utcnow_iso()
        base["timeline"]["duration_ms"] = base["collection_duration_ms"]
        return base

    base["database_name"] = _safe_db_name(settings.database_url)
    t_pool = time.perf_counter()
    base["pool"] = _pool_stats(db_manager.engine)
    timings["pool_ms"] = round((time.perf_counter() - t_pool) * 1000, 2)

    async def _ping() -> None:
        async with db_manager.engine.begin() as conn:
            await conn.execute(text("SELECT 1"))

    _, err = await _measure(timings, "connection_check_ms", _ping)
    base["latency_ms"] = timings["connection_check_ms"]
    if err is not None:
        base["status"] = HealthStatus.UNAVAILABLE.value
        base["reason"] = f"DB_CONNECTION_FAILED:{err.__class__.__name__}"
        base["errors"].append({"code": "DB_CONNECTION_FAILED", "error": str(err)})
        base["issues_candidates"].append(
            {
                "error_code": "DB_CONNECTION_FAILED",
                "severity": "CRITICAL",
                "message": f"PostgreSQL connection failed: {err.__class__.__name__}",
                "expected": "SELECT 1 succeeds",
                "actual": str(err),
            }
        )
        base["collection_duration_ms"] = round((time.perf_counter() - t_total) * 1000, 2)
        base["timeline"]["collection_completed"] = _utcnow_iso()
        base["timeline"]["duration_ms"] = base["collection_duration_ms"]
        base["slowest_queries"] = _slowest_from_timings(timings)
        return base

    base["status"] = HealthStatus.HEALTHY.value
    base["reason"] = "SELECT 1 succeeded"

    # --- cheap reuse for context ---
    async def _connections() -> dict[str, Any]:
        async with db_manager.engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT
                          count(*) FILTER (WHERE state = 'active') AS active,
                          count(*) FILTER (WHERE state = 'idle') AS idle,
                          count(*) FILTER (WHERE wait_event_type IS NOT NULL) AS waiting,
                          count(*) AS total
                        FROM pg_stat_activity
                        WHERE datname = current_database()
                        """
                    )
                )
            ).one()
            lim = (await conn.execute(text("SHOW max_connections"))).scalar()
            return {
                "active": int(row[0] or 0),
                "idle": int(row[1] or 0),
                "waiting": int(row[2] or 0),
                "total": int(row[3] or 0),
                "max_connections": int(lim) if lim is not None else None,
                "status": HealthStatus.HEALTHY.value,
            }

    conn_stats, conn_err = await _measure(timings, "connections_ms", _connections)
    if conn_err is not None:
        base["connections"] = _section_unknown(
            f"INSUFFICIENT_PERMISSION_OR_ERROR:{conn_err.__class__.__name__}",
            duration_ms=timings["connections_ms"],
        )
        base["errors"].append({"code": "DB_CONN_STATS", "error": str(conn_err)})
    else:
        base["connections"] = conn_stats

    async def _size() -> int | None:
        async with db_manager.engine.begin() as conn:
            size = (
                await conn.execute(text("SELECT pg_database_size(current_database())"))
            ).scalar()
            return int(size) if size is not None else None

    size, size_err = await _measure(timings, "database_size_ms", _size)
    if size_err is not None:
        base["database_size_bytes"] = None
        base["errors"].append({"code": "DB_SIZE", "error": str(size_err)})
    else:
        base["database_size_bytes"] = size

    async def _lock_count() -> int:
        async with db_manager.engine.begin() as conn:
            n = (
                await conn.execute(
                    text("SELECT count(*) FROM pg_locks WHERE NOT granted")
                )
            ).scalar()
            return int(n or 0)

    locks, lock_err = await _measure(timings, "locks_ms", _lock_count)
    if lock_err is not None:
        base["lock_count"] = None
        base["errors"].append({"code": "DB_LOCK_COUNT", "error": str(lock_err)})
    else:
        base["lock_count"] = locks

    # --- expensive query forensics ---
    long_query_seconds = float(
        getattr(settings, "diag_db_long_query_seconds", 30) or 30
    )
    queries: dict[str, Any] = {
        "long_running": [],
        "blocked": [],
        "lock_waits": [],
        "oldest_transaction_age_seconds": None,
        "status": HealthStatus.UNKNOWN.value,
        "reason": None,
    }

    async def _long_queries() -> list[dict[str, Any]]:
        async with db_manager.engine.begin() as conn:
            long_rows = (
                await conn.execute(
                    text(
                        """
                        SELECT pid, usename, state, wait_event_type, wait_event,
                               EXTRACT(EPOCH FROM (now() - query_start)) AS age_s,
                               left(query, 200) AS query
                        FROM pg_stat_activity
                        WHERE datname = current_database()
                          AND pid <> pg_backend_pid()
                          AND state <> 'idle'
                          AND query_start IS NOT NULL
                          AND now() - query_start > make_interval(secs => :secs)
                        ORDER BY query_start ASC
                        LIMIT 20
                        """
                    ),
                    {"secs": long_query_seconds},
                )
            ).fetchall()
            return [
                {
                    "pid": r[0],
                    "user": r[1],
                    "state": r[2],
                    "wait_event_type": r[3],
                    "wait_event": r[4],
                    "age_seconds": float(r[5]) if r[5] is not None else None,
                    "query": r[6],
                }
                for r in long_rows
            ]

    long_rows, long_err = await _measure(timings, "long_queries_ms", _long_queries)
    if long_err is not None:
        queries["long_running_status"] = _section_unknown(
            f"INSUFFICIENT_PERMISSION:{long_err.__class__.__name__}",
            duration_ms=timings["long_queries_ms"],
        )
        base["errors"].append({"code": "DB_LONG_QUERIES", "error": str(long_err)})
    else:
        queries["long_running"] = long_rows or []

    async def _blocked() -> list[dict[str, Any]]:
        async with db_manager.engine.begin() as conn:
            blocked = (
                await conn.execute(
                    text(
                        """
                        SELECT blocked.pid AS blocked_pid,
                               left(blocked.query, 160) AS blocked_query,
                               blocking.pid AS blocking_pid,
                               left(blocking.query, 160) AS blocking_query
                        FROM pg_stat_activity blocked
                        JOIN pg_locks bl ON bl.pid = blocked.pid AND NOT bl.granted
                        JOIN pg_locks gl
                          ON gl.locktype = bl.locktype
                         AND gl.database IS NOT DISTINCT FROM bl.database
                         AND gl.relation IS NOT DISTINCT FROM bl.relation
                         AND gl.page IS NOT DISTINCT FROM bl.page
                         AND gl.tuple IS NOT DISTINCT FROM bl.tuple
                         AND gl.virtualxid IS NOT DISTINCT FROM bl.virtualxid
                         AND gl.transactionid IS NOT DISTINCT FROM bl.transactionid
                         AND gl.classid IS NOT DISTINCT FROM bl.classid
                         AND gl.objid IS NOT DISTINCT FROM bl.objid
                         AND gl.objsubid IS NOT DISTINCT FROM bl.objsubid
                         AND gl.pid <> bl.pid
                         AND gl.granted
                        JOIN pg_stat_activity blocking ON blocking.pid = gl.pid
                        WHERE blocked.datname = current_database()
                        LIMIT 20
                        """
                    )
                )
            ).fetchall()
            return [
                {
                    "blocked_pid": r[0],
                    "blocked_query": r[1],
                    "blocking_pid": r[2],
                    "blocking_query": r[3],
                }
                for r in blocked
            ]

    blocked_rows, blocked_err = await _measure(timings, "blocked_queries_ms", _blocked)
    if blocked_err is not None:
        queries["blocked_status"] = _section_unknown(
            f"INSUFFICIENT_PERMISSION:{blocked_err.__class__.__name__}",
            duration_ms=timings["blocked_queries_ms"],
        )
        base["errors"].append({"code": "DB_BLOCKED_QUERIES", "error": str(blocked_err)})
    else:
        queries["blocked"] = blocked_rows or []
        queries["lock_waits"] = list(queries["blocked"])

    async def _oldest_xact() -> float | None:
        async with db_manager.engine.begin() as conn:
            oldest = (
                await conn.execute(
                    text(
                        """
                        SELECT EXTRACT(EPOCH FROM (now() - min(xact_start)))
                        FROM pg_stat_activity
                        WHERE datname = current_database()
                          AND xact_start IS NOT NULL
                        """
                    )
                )
            ).scalar()
            return float(oldest) if oldest is not None else None

    oldest, oldest_err = await _measure(timings, "oldest_transaction_ms", _oldest_xact)
    if oldest_err is not None:
        queries["oldest_transaction_status"] = _section_unknown(
            f"INSUFFICIENT_PERMISSION:{oldest_err.__class__.__name__}",
            duration_ms=timings["oldest_transaction_ms"],
        )
        base["errors"].append({"code": "DB_OLDEST_XACT", "error": str(oldest_err)})
    else:
        queries["oldest_transaction_age_seconds"] = oldest

    if long_err is None and blocked_err is None:
        queries["status"] = HealthStatus.HEALTHY.value
        queries["reason"] = "pg_stat_activity sampled"
        if queries["long_running"]:
            queries["status"] = HealthStatus.WARNING.value
            queries["reason"] = f"{len(queries['long_running'])} long-running queries"
            for q in queries["long_running"][:5]:
                base["issues_candidates"].append(
                    {
                        "error_code": "DB_LONG_QUERY",
                        "severity": "WARNING",
                        "message": f"Long-running query pid={q.get('pid')}",
                        "expected": f"query age < {long_query_seconds}s",
                        "actual": f"{q.get('age_seconds')}s",
                        "details": q,
                    }
                )
        if queries["blocked"]:
            queries["status"] = HealthStatus.ERROR.value
            queries["reason"] = f"{len(queries['blocked'])} blocked queries"
            for q in queries["blocked"][:5]:
                base["issues_candidates"].append(
                    {
                        "error_code": "DB_BLOCKED_QUERY",
                        "severity": "ERROR",
                        "message": "Blocked query detected",
                        "expected": "no lock waits",
                        "actual": f"blocked_pid={q.get('blocked_pid')}",
                        "details": q,
                    }
                )
    elif queries.get("status") == HealthStatus.UNKNOWN.value:
        queries["reason"] = "partial query forensics unavailable"

    base["queries"] = queries

    # --- table sizes + vacuum (expensive) ---
    tables_out: list[dict[str, Any]] = []

    async def _table_sizes() -> list[dict[str, Any]]:
        async with db_manager.engine.begin() as conn:
            existing = (
                await conn.execute(
                    text(
                        """
                        SELECT tablename FROM pg_tables
                        WHERE schemaname = 'public'
                        """
                    )
                )
            ).fetchall()
            existing_names = {r[0] for r in existing}
            targets = [t for t in WATCH_TABLES if t in existing_names]
            large = (
                await conn.execute(
                    text(
                        """
                        SELECT c.relname AS table_name,
                               pg_total_relation_size(c.oid) AS total_bytes,
                               pg_relation_size(c.oid) AS table_bytes,
                               pg_indexes_size(c.oid) AS index_bytes,
                               s.n_live_tup AS live_rows,
                               s.n_dead_tup AS dead_tuples,
                               s.last_vacuum,
                               s.last_autovacuum,
                               s.last_analyze,
                               s.last_autoanalyze
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
                        WHERE n.nspname = 'public' AND c.relkind = 'r'
                        ORDER BY pg_total_relation_size(c.oid) DESC NULLS LAST
                        LIMIT 25
                        """
                    )
                )
            ).fetchall()
            seen: set[str] = set()
            out: list[dict[str, Any]] = []
            for r in large:
                name = r[0]
                seen.add(name)
                out.append(
                    {
                        "table": name,
                        "total_bytes": int(r[1]) if r[1] is not None else None,
                        "table_bytes": int(r[2]) if r[2] is not None else None,
                        "index_bytes": int(r[3]) if r[3] is not None else None,
                        "live_rows_est": int(r[4]) if r[4] is not None else None,
                        "dead_tuples": int(r[5]) if r[5] is not None else None,
                        "last_vacuum": r[6].isoformat() if r[6] else None,
                        "last_autovacuum": r[7].isoformat() if r[7] else None,
                        "last_analyze": r[8].isoformat() if r[8] else None,
                        "last_autoanalyze": r[9].isoformat() if r[9] else None,
                        "watched": name in targets,
                    }
                )
            for name in targets:
                if name in seen:
                    continue
                out.append(
                    {
                        "table": name,
                        "total_bytes": None,
                        "table_bytes": None,
                        "index_bytes": None,
                        "live_rows_est": None,
                        "dead_tuples": None,
                        "last_vacuum": None,
                        "last_autovacuum": None,
                        "last_analyze": None,
                        "last_autoanalyze": None,
                        "watched": True,
                        "note": "not in top-25 by size — size UNKNOWN without extra query",
                    }
                )
            return out

    tables, tables_err = await _measure(timings, "table_sizes_ms", _table_sizes)
    # vacuum_stats timing mirrors table_sizes (same query carries vacuum columns)
    timings["vacuum_stats_ms"] = timings["table_sizes_ms"]
    if tables_err is not None:
        base["table_sizes"] = _section_unknown(
            f"INSUFFICIENT_PERMISSION:{tables_err.__class__.__name__}",
            duration_ms=timings["table_sizes_ms"],
        )
        base["vacuum_stats"] = _section_unknown(
            f"INSUFFICIENT_PERMISSION:{tables_err.__class__.__name__}",
            duration_ms=timings["vacuum_stats_ms"],
        )
        base["errors"].append({"code": "DB_TABLE_STATS", "error": str(tables_err)})
        base["tables"] = []
    else:
        tables_out = tables or []
        base["tables"] = tables_out
        base["table_sizes"] = {
            "status": HealthStatus.HEALTHY.value,
            "reason": f"{len(tables_out)} tables measured",
            "duration_ms": timings["table_sizes_ms"],
            "count": len(tables_out),
        }
        base["vacuum_stats"] = {
            "status": HealthStatus.HEALTHY.value,
            "reason": "vacuum/analyze columns from pg_stat_user_tables",
            "duration_ms": timings["vacuum_stats_ms"],
        }

    _apply_pool_issues(base, settings)

    duration = round((time.perf_counter() - t_total) * 1000, 2)
    completed = _utcnow_iso()
    base["collection_duration_ms"] = duration
    base["timeline"] = {
        "collection_started": collected_at.isoformat(),
        "collection_completed": completed,
        "duration_ms": duration,
    }
    base["slowest_queries"] = _slowest_from_timings(timings)
    base["timings"] = timings
    return base


async def get_database_detail(
    settings: Any,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Cached detailed forensics. Default TTL 60s (DIAGNOSTICS_DB_DETAIL_CACHE_SECONDS)."""
    cache_seconds = float(
        getattr(settings, "diagnostics_db_detail_cache_seconds", None)
        or getattr(settings, "diag_db_detail_cache_seconds", 60)
        or 60
    )
    async with _detail_cache._lock:
        meta = _detail_cache.snapshot_meta(cache_seconds)
        if (
            not force_refresh
            and _detail_cache._payload is not None
            and _detail_cache._collected_at is not None
            and meta.get("cache_valid")
        ):
            payload = dict(_detail_cache._payload)
            payload["cached"] = True
            payload["collected_at"] = _detail_cache._collected_at.isoformat()
            payload["cache_age_seconds"] = meta["cache_age_seconds"]
            payload["collection_duration_ms"] = _detail_cache._collection_duration_ms
            payload["cache_ttl_seconds"] = cache_seconds
            payload["force_refresh"] = False
            return payload

        payload = await collect_database_detail_uncached(settings)
        _detail_cache._payload = payload
        _detail_cache._collected_at = _utcnow()
        _detail_cache._collection_duration_ms = payload.get("collection_duration_ms")
        out = dict(payload)
        out["cached"] = False
        out["collected_at"] = _detail_cache._collected_at.isoformat()
        out["cache_age_seconds"] = 0.0
        out["cache_ttl_seconds"] = cache_seconds
        out["force_refresh"] = force_refresh
        return out


# Backward-compatible name used by older tests — maps to uncached detail.
async def collect_database_forensics(settings: Any) -> dict[str, Any]:
    return await collect_database_detail_uncached(settings)
