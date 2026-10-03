"""Backup orchestration — background jobs, no blocking HTTP for long dumps."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.diagnostics.backup.checksum import verify_file_checksum
from app.diagnostics.backup.disk import check_free_space, ensure_destination
from app.diagnostics.backup.locks import backup_locks
from app.diagnostics.backup.models import (
    BackupJobStatus,
    BackupRecord,
    BackupType,
    ChecksumStatus,
    RestoreTestStatus,
    derive_backup_health,
)
from app.diagnostics.backup.restore import assess_restore_capability, run_restore_test
from app.diagnostics.backup.runners import RUNNERS, build_pre_backup_snapshot, finish_record
from app.diagnostics.backup.store import backup_meta_store
from app.diagnostics.constants import HealthStatus
from app.diagnostics.git_info import collect_git_info
from app.diagnostics.ids import new_diagnostic_id
from app.diagnostics.redact import redact_value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dest_dir(settings: Any) -> Path:
    raw = getattr(settings, "backup_destination", "data/diagnostics/backups") or (
        "data/diagnostics/backups"
    )
    p = Path(raw)
    if not p.is_absolute():
        # relative to repo root (parent of backend/)
        root = Path(__file__).resolve().parents[4]
        p = root / p
    return p


class BackupService:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    async def list_backups(self, settings: Any) -> dict[str, Any]:
        dest = _dest_dir(settings)
        dest_info = ensure_destination(dest)
        space = check_free_space(
            dest,
            min_free_gb=float(getattr(settings, "backup_min_free_space_gb", 5) or 5),
        )
        rows = await backup_meta_store.list_all(dest)
        # Enrich health on each row if missing
        for r in rows:
            if "health_status" not in r:
                rec = BackupRecord(
                    backup_id=str(r.get("backup_id")),
                    backup_type=str(r.get("backup_type") or "UNKNOWN"),
                    status=str(r.get("status") or "UNKNOWN"),
                    started_at=_utcnow(),
                    verification_status=str(
                        r.get("verification_status") or ChecksumStatus.NOT_VERIFIED.value
                    ),
                    restore_test_status=str(
                        r.get("restore_test_status") or RestoreTestStatus.NOT_TESTED.value
                    ),
                    error=r.get("error"),
                )
                h, reason = derive_backup_health(rec)
                r["health_status"] = h
                r["health_reason"] = reason

        successful = [
            r
            for r in rows
            if r.get("status") == BackupJobStatus.COMPLETED.value
            and r.get("verification_status") == ChecksumStatus.CHECKSUM_VERIFIED.value
        ]
        last_ok = successful[0] if successful else None
        last_verified = last_ok
        restore_tested = [
            r
            for r in rows
            if r.get("restore_test_status")
            == RestoreTestStatus.RESTORE_TEST_PASSED.value
        ]
        sizes = [int(r["size_bytes"]) for r in rows if r.get("size_bytes") is not None]
        retention_days = int(getattr(settings, "backup_retention_days", 90) or 90)
        capability = assess_restore_capability(settings)

        # Aggregate health semantics
        if not rows:
            overall = HealthStatus.UNKNOWN.value
            reason = "No backups recorded"
        elif any(r.get("health_status") == "FAILED" for r in rows[:5]):
            overall = HealthStatus.ERROR.value
            reason = "Recent backup verification failed"
        elif last_ok and last_ok.get("health_status") == "HEALTHY":
            overall = HealthStatus.HEALTHY.value
            reason = "Latest backup completed, checksum verified, restore tested"
        elif last_ok:
            overall = HealthStatus.DEGRADED.value
            reason = "Latest backup completed and checksum verified; restore not tested"
        elif any(r.get("status") == BackupJobStatus.UNAVAILABLE.value for r in rows):
            overall = HealthStatus.UNAVAILABLE.value
            reason = "Backup capability unavailable for requested type"
        else:
            overall = HealthStatus.UNKNOWN.value
            reason = "No verified successful backup"

        dump_ok = capability.get("pg_dump") is True
        types_available = {
            BackupType.CONFIG.value: True,
            BackupType.DIAGNOSTICS.value: True,
            BackupType.RESEARCH.value: True,
            BackupType.DATABASE.value: dump_ok,
            BackupType.FULL.value: True,
        }

        return redact_value(
            {
                "phase": 3,
                "status": overall,
                "reason": reason,
                "verified": bool(
                    last_verified
                    and last_verified.get("verification_status")
                    == ChecksumStatus.CHECKSUM_VERIFIED.value
                ),
                "destination": dest_info,
                "disk": space,
                "types_available": types_available,
                "types_unavailable": {
                    k: ("PG_DUMP_NOT_FOUND" if k == "DATABASE" and not dump_ok else None)
                    for k, v in types_available.items()
                    if not v
                },
                "restore_capability": capability,
                "retention": {
                    "configured_days": retention_days,
                    "auto_delete": False,
                    "note": "Retention visibility only — no automatic deletion in Phase 3",
                    "backup_count": len(rows),
                    "oldest": rows[-1].get("started_at") if rows else None,
                    "newest": rows[0].get("started_at") if rows else None,
                    "total_size_bytes": sum(sizes) if sizes else 0,
                },
                "summary": {
                    "last_successful_backup": last_ok,
                    "last_verified_backup": last_verified,
                    "last_restore_test": restore_tested[0] if restore_tested else None,
                    "active_locks": await backup_locks.snapshot(),
                },
                "backups": rows,
                "count": len(rows),
            }
        )

    async def get_backup(self, settings: Any, backup_id: str) -> dict[str, Any] | None:
        rows = await backup_meta_store.list_all(_dest_dir(settings))
        for r in rows:
            if r.get("backup_id") == backup_id:
                return redact_value(r)
        return None

    async def start_backup(
        self,
        settings: Any,
        *,
        backup_type: str,
    ) -> dict[str, Any]:
        btype = str(backup_type or "").upper()
        if btype not in RUNNERS:
            return {
                "status": "FAILED",
                "error_code": "INVALID_BACKUP_TYPE",
                "allowed": list(RUNNERS.keys()),
            }

        dest = _dest_dir(settings)
        dest_info = ensure_destination(dest)
        if not dest_info.get("writable"):
            return {
                "status": BackupJobStatus.BLOCKED.value,
                "error_code": "DESTINATION_NOT_WRITABLE",
                "destination": dest_info,
            }

        space = check_free_space(
            dest,
            min_free_gb=float(getattr(settings, "backup_min_free_space_gb", 5) or 5),
            estimated_bytes=None,  # UNKNOWN estimate — do not invent
        )
        if not space.get("allowed"):
            return {
                "status": BackupJobStatus.BLOCKED.value,
                "error_code": space.get("reason") or "BACKUP_BLOCKED_INSUFFICIENT_DISK",
                "disk": space,
            }

        git = collect_git_info()
        backup_id = new_diagnostic_id()
        lock = await backup_locks.try_acquire(btype, str(dest), backup_id)
        if not lock.get("acquired"):
            return {
                "status": BackupJobStatus.BLOCKED.value,
                "error_code": "BACKUP_ALREADY_RUNNING",
                "active_backup_id": lock.get("active_backup_id"),
                "lock_key": lock.get("lock_key"),
            }

        record = BackupRecord(
            backup_id=backup_id,
            backup_type=btype,
            status=BackupJobStatus.QUEUED.value,
            started_at=_utcnow(),
            destination_dir=str(dest),
            git_commit=git.get("commit_short") or git.get("commit"),
            run_id=backup_id,
            restore_test_status=RestoreTestStatus.NOT_TESTED.value,
            timeline={"queued": _utcnow().isoformat()},
        )
        await backup_meta_store.persist(record, dest)

        task = asyncio.create_task(self._run_job(record, settings, dest))
        self._tasks[backup_id] = task
        return redact_value(
            {
                "status": BackupJobStatus.QUEUED.value,
                "backup_id": backup_id,
                "backup_type": btype,
                "message": "Backup job started in background",
                "backup": record.to_dict(),
            }
        )

    async def _run_job(
        self, record: BackupRecord, settings: Any, dest: Path
    ) -> None:
        try:
            record.status = BackupJobStatus.RUNNING.value
            record.timeline["started"] = _utcnow().isoformat()
            record.heartbeat_at = _utcnow()
            record.pre_backup_snapshot = await build_pre_backup_snapshot(settings)
            await backup_meta_store.persist(record, dest)

            runner = RUNNERS[record.backup_type]
            await runner(record, settings, dest)
            finish_record(record)
            record.timeline["completed"] = _utcnow().isoformat()
        except Exception as exc:  # noqa: BLE001
            record.status = BackupJobStatus.FAILED.value
            record.error = f"{exc.__class__.__name__}:{exc}"
            finish_record(record)
        finally:
            await backup_meta_store.persist(record, dest)
            await backup_locks.release(
                record.backup_type, str(dest), record.backup_id
            )
            self._tasks.pop(record.backup_id, None)

    async def verify_backup(self, settings: Any, backup_id: str) -> dict[str, Any]:
        row = await self.get_backup(settings, backup_id)
        if not row:
            return {"status": "NOT_FOUND", "backup_id": backup_id}
        path = row.get("destination")
        checksum = row.get("checksum")
        if not path or not checksum:
            return {
                "status": ChecksumStatus.NOT_VERIFIED.value,
                "backup_id": backup_id,
                "reason": "missing path or checksum",
            }
        p = Path(str(path))
        if not p.is_file():
            return {
                "status": ChecksumStatus.CHECKSUM_FAILED.value,
                "backup_id": backup_id,
                "reason": "artifact missing",
            }
        t0 = time.perf_counter()
        vs = verify_file_checksum(p, str(checksum))
        ms = round((time.perf_counter() - t0) * 1000, 2)
        # Update record
        rows = await backup_meta_store.list_all(_dest_dir(settings))
        for r in rows:
            if r.get("backup_id") == backup_id:
                r["verification_status"] = vs
                r["verified"] = vs == ChecksumStatus.CHECKSUM_VERIFIED.value
                r.setdefault("timeline", {})["reverified"] = _utcnow().isoformat()
                # Rebuild BackupRecord-ish for persist via memory index
                from app.diagnostics.backup.models import BackupRecord as BR

                rec = BR(
                    backup_id=backup_id,
                    backup_type=str(r.get("backup_type") or "UNKNOWN"),
                    status=str(r.get("status") or "UNKNOWN"),
                    started_at=_utcnow(),
                    finished_at=_utcnow(),
                    size_bytes=r.get("size_bytes"),
                    destination=r.get("destination"),
                    destination_dir=str(_dest_dir(settings)),
                    checksum=r.get("checksum"),
                    verification_status=vs,
                    validation_status=r.get("validation_status"),
                    restore_test_status=str(
                        r.get("restore_test_status") or RestoreTestStatus.NOT_TESTED.value
                    ),
                    tool=r.get("tool"),
                    tool_version=r.get("tool_version"),
                    git_commit=r.get("git_commit"),
                    run_id=r.get("run_id"),
                    error=r.get("error"),
                    timeline=r.get("timeline") or {},
                    metadata=r.get("metadata") or {},
                )
                if vs == ChecksumStatus.CHECKSUM_FAILED.value:
                    rec.status = BackupJobStatus.FAILED.value
                    rec.error = "checksum re-verification failed"
                h, reason = derive_backup_health(rec)
                rec.health_status = h
                rec.health_reason = reason
                await backup_meta_store.persist(rec, _dest_dir(settings))
                break
        return {
            "backup_id": backup_id,
            "status": vs,
            "duration_ms": ms,
            "size_bytes": p.stat().st_size,
            "performed": True,
        }

    async def restore_test(self, settings: Any, backup_id: str) -> dict[str, Any]:
        row = await self.get_backup(settings, backup_id)
        if not row:
            return {"status": "NOT_FOUND", "backup_id": backup_id}
        result = await run_restore_test(row, settings)
        # Persist status onto record
        from app.diagnostics.backup.models import BackupRecord as BR

        rec = BR(
            backup_id=backup_id,
            backup_type=str(row.get("backup_type") or "UNKNOWN"),
            status=str(row.get("status") or "UNKNOWN"),
            started_at=_utcnow(),
            finished_at=_utcnow(),
            size_bytes=row.get("size_bytes"),
            destination=row.get("destination"),
            destination_dir=str(_dest_dir(settings)),
            checksum=row.get("checksum"),
            verification_status=str(
                row.get("verification_status") or ChecksumStatus.NOT_VERIFIED.value
            ),
            validation_status=row.get("validation_status"),
            restore_test_status=str(result.get("status")),
            tool=row.get("tool"),
            tool_version=row.get("tool_version"),
            git_commit=row.get("git_commit"),
            run_id=row.get("run_id"),
            error=row.get("error"),
            timeline={**(row.get("timeline") or {}), "restore_tested": _utcnow().isoformat()},
            metadata={**(row.get("metadata") or {}), "restore_test": result},
        )
        h, reason = derive_backup_health(rec)
        rec.health_status = h
        rec.health_reason = reason
        await backup_meta_store.persist(rec, _dest_dir(settings))
        return redact_value(result)


backup_service = BackupService()
