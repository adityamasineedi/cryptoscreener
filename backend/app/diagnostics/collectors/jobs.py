"""Research job + lock forensics — real states only."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.diagnostics.constants import HealthStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Any) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(dt)


def _normalize_status(raw: str | None, *, finished_at: Any = None) -> str:
    if not raw:
        # Infer terminal state from finished_at when DB status is missing
        return "COMPLETED" if finished_at is not None else "UNKNOWN"
    u = str(raw).upper().strip()
    mapping = {
        "IDLE": "UNKNOWN",
        "PENDING": "QUEUED",
        "QUEUED": "QUEUED",
        "RUNNING": "RUNNING",
        "IN_PROGRESS": "RUNNING",
        "COMPLETE": "COMPLETED",
        "COMPLETED": "COMPLETED",
        "SUCCESS": "COMPLETED",
        "SUCCEEDED": "COMPLETED",
        "DONE": "COMPLETED",
        "FINISHED": "COMPLETED",
        "OK": "COMPLETED",
        "FAILED": "FAILED",
        "ERROR": "FAILED",
        "CANCELLED": "CANCELLED",
        "CANCELED": "CANCELLED",
        "BLOCKED": "BLOCKED",
        "STALE": "STALE",
        "PAUSED": "BLOCKED",
    }
    st = mapping.get(u, u if u in mapping.values() else "UNKNOWN")
    # Prefer measured completion over ambiguous raw labels
    if st == "UNKNOWN" and finished_at is not None:
        return "COMPLETED"
    if st == "RUNNING" and finished_at is not None:
        return "COMPLETED"
    return st


async def collect_jobs_forensics(settings: Any) -> dict[str, Any]:
    checked = _utcnow().isoformat()
    stale_heartbeat = float(
        getattr(settings, "diag_job_stale_heartbeat_seconds", 300) or 300
    )
    out: dict[str, Any] = {
        "phase": 2,
        "last_checked": checked,
        "collector": "diagnostics.collectors.jobs.collect_jobs_forensics",
        "file": "backend/app/diagnostics/collectors/jobs.py",
        "function": "collect_jobs_forensics",
        "stale_heartbeat_seconds": stale_heartbeat,
        "jobs": [],
        "locks": [],
        "status": HealthStatus.UNKNOWN.value,
        "reason": None,
        "issues_candidates": [],
        "errors": [],
    }

    jobs: list[dict[str, Any]] = []

    # Backtest job
    try:
        from app.research.backtest_job import backtest_job_service

        bj = backtest_job_service.status()
        if bj.get("job_id") or (bj.get("status") and bj.get("status") != "idle"):
            st = _normalize_status(bj.get("status"))
            jobs.append(
                {
                    "run_id": bj.get("job_id"),
                    "job_id": bj.get("job_id"),
                    "job_type": "backtest",
                    "status": st,
                    "stage": bj.get("stage") or bj.get("phase"),
                    "symbol": None,
                    "timeframe": None,
                    "symbols": bj.get("symbols"),
                    "timeframes": bj.get("timeframes"),
                    "started_at": bj.get("started_at"),
                    "updated_at": bj.get("updated_at"),
                    "completed_at": bj.get("finished_at") or bj.get("completed_at"),
                    "duration_seconds": bj.get("elapsed_seconds") or bj.get("duration"),
                    "worker": "api",
                    "progress": bj.get("progress"),
                    "last_checkpoint": bj.get("checkpoint"),
                    "error_count": 1 if st == "FAILED" else 0,
                    "warning_count": 0,
                    "error": bj.get("error"),
                }
            )
    except Exception as exc:  # noqa: BLE001
        out["errors"].append({"code": "BACKTEST_JOB", "error": str(exc)})

    # OHLCV expand job
    try:
        from app.research.ohlcv_expand import ohlcv_expand_service

        ej = ohlcv_expand_service.status()
        if ej.get("job_id") or (ej.get("status") and ej.get("status") != "idle"):
            st = _normalize_status(ej.get("status"))
            jobs.append(
                {
                    "run_id": ej.get("job_id"),
                    "job_id": ej.get("job_id"),
                    "job_type": "ohlcv_expand",
                    "status": st,
                    "stage": ej.get("stage"),
                    "symbol": None,
                    "timeframe": None,
                    "symbols": ej.get("symbols"),
                    "timeframes": ej.get("timeframes"),
                    "started_at": ej.get("started_at"),
                    "updated_at": ej.get("updated_at"),
                    "completed_at": ej.get("finished_at"),
                    "duration_seconds": ej.get("elapsed_seconds"),
                    "worker": "api",
                    "progress": ej.get("progress"),
                    "last_checkpoint": ej.get("checkpoint"),
                    "error_count": 1 if st == "FAILED" else 0,
                    "warning_count": 0,
                    "error": ej.get("error"),
                    "details": {
                        "results": ej.get("results"),
                    },
                }
            )
    except Exception as exc:  # noqa: BLE001
        out["errors"].append({"code": "OHLCV_EXPAND", "error": str(exc)})

    # Pipeline runs + sync tasks + locks from DB
    try:
        from app.services.database import db_manager

        if db_manager.enabled and db_manager.engine is not None:
            async with db_manager.engine.begin() as conn:
                # research_pipeline_runs
                exists = (
                    await conn.execute(
                        text(
                            """
                            SELECT EXISTS (
                              SELECT 1 FROM information_schema.tables
                              WHERE table_schema='public'
                                AND table_name='research_pipeline_runs'
                            )
                            """
                        )
                    )
                ).scalar()
                if exists:
                    rows = (
                        await conn.execute(
                            text(
                                """
                                SELECT run_id, status, created_at, finished_at,
                                       symbols, timeframes, metrics, failed_symbols
                                FROM research_pipeline_runs
                                ORDER BY created_at DESC
                                LIMIT 50
                                """
                            )
                        )
                    ).fetchall()
                    now = _utcnow()
                    for r in rows:
                        st = _normalize_status(r[1], finished_at=r[3])
                        # Pipeline runs lack heartbeats — mark long-idle RUNNING as STALE
                        if st == "RUNNING" and r[3] is None and r[2] is not None:
                            started = r[2] if getattr(r[2], "tzinfo", None) else r[2].replace(tzinfo=timezone.utc)
                            age = (now - started).total_seconds()
                            # No progress signal: only flag when age >> stale threshold
                            if age > stale_heartbeat * 6:
                                st = "STALE"
                                out["issues_candidates"].append(
                                    {
                                        "error_code": "JOB_STALE_HEARTBEAT",
                                        "severity": "WARNING",
                                        "message": f"Pipeline run idle without finish: {r[0]}",
                                        "expected": f"progress within {stale_heartbeat * 6:.0f}s",
                                        "actual": f"{age:.0f}s since start",
                                        "job_id": r[0],
                                        "run_id": r[0],
                                    }
                                )
                        jobs.append(
                            {
                                "run_id": r[0],
                                "job_id": r[0],
                                "job_type": "research_pipeline",
                                "status": st,
                                "stage": None,
                                "symbol": None,
                                "timeframe": None,
                                "symbols": r[4],
                                "timeframes": r[5],
                                "started_at": _iso(r[2]),
                                "updated_at": _iso(r[2]),
                                "completed_at": _iso(r[3]),
                                "duration_seconds": None,
                                "worker": None,
                                "progress": r[6],
                                "last_checkpoint": None,
                                "error_count": len(r[7] or []) if r[7] else 0,
                                "warning_count": 0,
                            }
                        )

                # research_sync_tasks
                exists_tasks = (
                    await conn.execute(
                        text(
                            """
                            SELECT EXISTS (
                              SELECT 1 FROM information_schema.tables
                              WHERE table_schema='public'
                                AND table_name='research_sync_tasks'
                            )
                            """
                        )
                    )
                ).scalar()
                if exists_tasks:
                    rows = (
                        await conn.execute(
                            text(
                                """
                                SELECT task_id, run_id, symbol, timeframe, status,
                                       claimed_by, attempt, rows_received, rows_inserted,
                                       last_error, created_at, updated_at, last_heartbeat,
                                       range_start_ms, range_end_ms
                                FROM research_sync_tasks
                                ORDER BY updated_at DESC NULLS LAST
                                LIMIT 100
                                """
                            )
                        )
                    ).fetchall()
                    now = _utcnow()
                    for r in rows:
                        st = _normalize_status(r[4], finished_at=None)
                        hb = r[12]
                        if st == "RUNNING" and hb is not None:
                            age = (now - (hb if hb.tzinfo else hb.replace(tzinfo=timezone.utc))).total_seconds()
                            if age > stale_heartbeat:
                                st = "STALE"
                                out["issues_candidates"].append(
                                    {
                                        "error_code": "JOB_STALE_HEARTBEAT",
                                        "severity": "WARNING",
                                        "message": f"Sync task stale heartbeat: {r[0]}",
                                        "expected": f"heartbeat < {stale_heartbeat}s",
                                        "actual": f"{age:.0f}s",
                                        "job_id": r[0],
                                        "symbol": r[2],
                                        "timeframe": r[3],
                                    }
                                )
                        jobs.append(
                            {
                                "run_id": r[1],
                                "job_id": r[0],
                                "job_type": "ohlcv_sync_task",
                                "status": st,
                                "stage": "DOWNLOAD",
                                "symbol": r[2],
                                "timeframe": r[3],
                                "started_at": _iso(r[10]),
                                "updated_at": _iso(r[11]),
                                "completed_at": _iso(r[11]) if st in ("COMPLETED", "FAILED", "CANCELLED") else None,
                                "duration_seconds": None,
                                "worker": r[5],
                                "progress": {
                                    "attempt": r[6],
                                    "rows_received": r[7],
                                    "rows_inserted": r[8],
                                    "range_start_ms": r[13],
                                    "range_end_ms": r[14],
                                },
                                "last_checkpoint": _iso(hb),
                                "error_count": 1 if r[9] else 0,
                                "warning_count": 0,
                                "error": r[9],
                            }
                        )

                # locks
                exists_locks = (
                    await conn.execute(
                        text(
                            """
                            SELECT EXISTS (
                              SELECT 1 FROM information_schema.tables
                              WHERE table_schema='public'
                                AND table_name='research_sync_locks'
                            )
                            """
                        )
                    )
                ).scalar()
                if exists_locks:
                    locks = (
                        await conn.execute(
                            text(
                                """
                                SELECT lock_key, operation, symbol, timeframe,
                                       range_start_ms, range_end_ms, run_id, pid,
                                       hostname, status, acquired_at, last_heartbeat
                                FROM research_sync_locks
                                ORDER BY last_heartbeat DESC NULLS LAST
                                LIMIT 100
                                """
                            )
                        )
                    ).fetchall()
                    now = _utcnow()
                    for r in locks:
                        hb = r[11]
                        age = None
                        stale = False
                        if hb is not None:
                            hbt = hb if hb.tzinfo else hb.replace(tzinfo=timezone.utc)
                            age = (now - hbt).total_seconds()
                            if str(r[9]).upper() == "ACTIVE" and age > stale_heartbeat:
                                stale = True
                                out["issues_candidates"].append(
                                    {
                                        "error_code": "STALE_LOCK",
                                        "severity": "WARNING",
                                        "message": f"Stale research sync lock: {r[0]}",
                                        "expected": f"heartbeat < {stale_heartbeat}s",
                                        "actual": f"{age:.0f}s",
                                        "details": {
                                            "lock_key": r[0],
                                            "symbol": r[2],
                                            "timeframe": r[3],
                                            "run_id": r[6],
                                        },
                                    }
                                )
                        out["locks"].append(
                            {
                                "lock_key": r[0],
                                "operation": r[1],
                                "symbol": r[2],
                                "timeframe": r[3],
                                "range_start_ms": r[4],
                                "range_end_ms": r[5],
                                "owner": r[8] or (str(r[7]) if r[7] is not None else None),
                                "run_id": r[6],
                                "pid": r[7],
                                "status": r[9],
                                "acquired_at": _iso(r[10]),
                                "heartbeat": _iso(hb),
                                "age_seconds": age,
                                "stale": stale,
                            }
                        )
        else:
            out["reason"] = "DATABASE disabled — in-process jobs only"
    except Exception as exc:  # noqa: BLE001
        out["errors"].append({"code": "JOBS_DB", "error": str(exc)})

    out["jobs"] = jobs
    if any(j.get("status") == "FAILED" for j in jobs):
        out["status"] = HealthStatus.ERROR.value
        out["reason"] = "One or more research jobs FAILED"
    elif any(j.get("status") == "STALE" for j in jobs) or any(
        l.get("stale") for l in out["locks"]
    ):
        out["status"] = HealthStatus.STALE.value
        out["reason"] = "Stale job heartbeat or lock detected"
    elif any(j.get("status") == "RUNNING" for j in jobs):
        out["status"] = HealthStatus.HEALTHY.value
        out["reason"] = "Research jobs running with measured state"
    elif jobs:
        out["status"] = HealthStatus.HEALTHY.value
        out["reason"] = f"{len(jobs)} jobs listed"
    else:
        out["status"] = HealthStatus.WAITING.value
        out["reason"] = "No research jobs currently recorded"
    return out
