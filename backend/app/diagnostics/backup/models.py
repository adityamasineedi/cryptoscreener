"""Backup / snapshot status vocabulary — evidence-driven only."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class BackupType(str, Enum):
    DATABASE = "DATABASE"
    RESEARCH = "RESEARCH"
    CONFIG = "CONFIG"
    DIAGNOSTICS = "DIAGNOSTICS"
    FULL = "FULL"


class BackupJobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"


class ChecksumStatus(str, Enum):
    CHECKSUM_VERIFIED = "CHECKSUM_VERIFIED"
    CHECKSUM_FAILED = "CHECKSUM_FAILED"
    NOT_VERIFIED = "NOT_VERIFIED"


class RestoreTestStatus(str, Enum):
    NOT_TESTED = "NOT_TESTED"
    RESTORE_TEST_AVAILABLE = "RESTORE_TEST_AVAILABLE"
    RESTORE_TEST_CAPABLE = "RESTORE_TEST_CAPABLE"
    RESTORE_TEST_UNAVAILABLE = "RESTORE_TEST_UNAVAILABLE"
    RESTORE_TEST_PASSED = "RESTORE_TEST_PASSED"
    RESTORE_TEST_FAILED = "RESTORE_TEST_FAILED"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


@dataclass
class BackupRecord:
    backup_id: str
    backup_type: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: float | None = None
    database_name: str | None = None
    size_bytes: int | None = None
    destination: str | None = None
    destination_dir: str | None = None
    checksum: str | None = None
    checksum_algorithm: str = "sha256"
    verification_status: str = ChecksumStatus.NOT_VERIFIED.value
    validation_status: str | None = None
    restore_test_status: str = RestoreTestStatus.NOT_TESTED.value
    tool: str | None = None
    tool_version: str | None = None
    git_commit: str | None = None
    run_id: str | None = None
    error: str | None = None
    health_status: str = "UNKNOWN"
    health_reason: str | None = None
    timeline: dict[str, Any] = field(default_factory=dict)
    pre_backup_snapshot: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    heartbeat_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["started_at"] = _iso(self.started_at)
        d["finished_at"] = _iso(self.finished_at)
        d["heartbeat_at"] = _iso(self.heartbeat_at)
        d["verified"] = self.verification_status == ChecksumStatus.CHECKSUM_VERIFIED.value
        return d


def derive_backup_health(record: BackupRecord) -> tuple[str, str]:
    """HEALTHY only when completed + checksum verified + readable."""
    st = record.status
    if st in (BackupJobStatus.UNAVAILABLE.value,):
        return "UNAVAILABLE", record.error or "backup capability unavailable"
    if st == BackupJobStatus.FAILED.value:
        return "FAILED", record.error or "backup failed"
    if st in (
        BackupJobStatus.QUEUED.value,
        BackupJobStatus.RUNNING.value,
        BackupJobStatus.VERIFYING.value,
    ):
        return "WAITING", f"job {st}"
    if st == BackupJobStatus.BLOCKED.value:
        return "UNAVAILABLE", record.error or "blocked"
    if st != BackupJobStatus.COMPLETED.value:
        return "UNKNOWN", f"status={st}"

    if record.verification_status == ChecksumStatus.CHECKSUM_FAILED.value:
        return "FAILED", "checksum verification failed"
    if record.verification_status != ChecksumStatus.CHECKSUM_VERIFIED.value:
        return "UNKNOWN", "checksum not verified"

    # Completed + checksum verified → DEGRADED until restore tested
    if record.restore_test_status in (
        RestoreTestStatus.RESTORE_TEST_PASSED.value,
    ):
        return "HEALTHY", "completed, checksum verified, restore tested"
    if record.restore_test_status == RestoreTestStatus.RESTORE_TEST_FAILED.value:
        return "FAILED", "restore test failed"
    return "DEGRADED", "completed and checksum verified; restore test not performed"
