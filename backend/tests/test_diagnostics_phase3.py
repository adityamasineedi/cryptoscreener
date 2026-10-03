"""Phase 3 — backups, checksums, snapshots, retention visibility."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.diagnostics.backup.checksum import sha256_file, verify_file_checksum
from app.diagnostics.backup.disk import atomic_replace, check_free_space, ensure_destination
from app.diagnostics.backup.locks import BackupLockRegistry
from app.diagnostics.backup.models import (
    BackupJobStatus,
    BackupRecord,
    ChecksumStatus,
    RestoreTestStatus,
    derive_backup_health,
)
from app.diagnostics.backup.restore import assess_restore_capability
from app.diagnostics.redact import redact_value


def test_checksum_roundtrip(tmp_path: Path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"hello-backup")
    digest = sha256_file(p)
    assert len(digest) == 64
    assert verify_file_checksum(p, digest) == ChecksumStatus.CHECKSUM_VERIFIED.value


def test_checksum_detects_corruption(tmp_path: Path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"hello-backup")
    digest = sha256_file(p)
    p.write_bytes(b"tampered")
    assert verify_file_checksum(p, digest) == ChecksumStatus.CHECKSUM_FAILED.value


def test_atomic_replace(tmp_path: Path):
    final = tmp_path / "out.zip"
    tmp = tmp_path / "out.zip.tmp"
    tmp.write_bytes(b"payload")
    atomic_replace(tmp, final)
    assert final.is_file()
    assert not tmp.exists()
    assert final.read_bytes() == b"payload"


def test_insufficient_disk(tmp_path: Path):
    with patch("app.diagnostics.backup.disk.disk_free_bytes", return_value=100):
        out = check_free_space(tmp_path, min_free_gb=5.0, estimated_bytes=None)
    assert out["allowed"] is False
    assert out["reason"] == "BACKUP_BLOCKED_INSUFFICIENT_DISK"


def test_unknown_free_space(tmp_path: Path):
    with patch("app.diagnostics.backup.disk.disk_free_bytes", return_value=None):
        out = check_free_space(tmp_path, min_free_gb=5.0)
    assert out["status"] == "UNKNOWN"
    assert out["allowed"] is False


@pytest.mark.asyncio
async def test_backup_lock_prevents_duplicate():
    reg = BackupLockRegistry()
    a = await reg.try_acquire("CONFIG", "/tmp/x", "id-1")
    b = await reg.try_acquire("CONFIG", "/tmp/x", "id-2")
    assert a["acquired"] is True
    assert b["acquired"] is False
    assert b["error_code"] == "BACKUP_ALREADY_RUNNING"
    assert b["active_backup_id"] == "id-1"
    await reg.release("CONFIG", "/tmp/x", "id-1")
    c = await reg.try_acquire("CONFIG", "/tmp/x", "id-3")
    assert c["acquired"] is True


def test_derive_health_semantics():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    base = dict(
        backup_id="x",
        backup_type="CONFIG",
        started_at=now,
    )
    completed = BackupRecord(
        **base,
        status=BackupJobStatus.COMPLETED.value,
        verification_status=ChecksumStatus.CHECKSUM_VERIFIED.value,
        restore_test_status=RestoreTestStatus.NOT_TESTED.value,
    )
    h, reason = derive_backup_health(completed)
    assert h == "DEGRADED"
    assert "restore" in reason.lower()

    verified_restore = BackupRecord(
        **base,
        status=BackupJobStatus.COMPLETED.value,
        verification_status=ChecksumStatus.CHECKSUM_VERIFIED.value,
        restore_test_status=RestoreTestStatus.RESTORE_TEST_PASSED.value,
    )
    assert derive_backup_health(verified_restore)[0] == "HEALTHY"

    failed = BackupRecord(
        **base,
        status=BackupJobStatus.COMPLETED.value,
        verification_status=ChecksumStatus.CHECKSUM_FAILED.value,
    )
    assert derive_backup_health(failed)[0] == "FAILED"

    mere_exists = BackupRecord(
        **base,
        status=BackupJobStatus.COMPLETED.value,
        verification_status=ChecksumStatus.NOT_VERIFIED.value,
    )
    assert derive_backup_health(mere_exists)[0] == "UNKNOWN"


def test_secret_redaction_in_backup_payload():
    payload = {
        "database_url": "postgresql://user:secretpass@localhost/db",
        "api_key": "sk-abc",
        "ok": 1,
    }
    safe = redact_value(payload)
    assert "secretpass" not in json.dumps(safe)
    assert safe["api_key"] == "***REDACTED***"


def test_restore_capability_unavailable_without_tools():
    settings = MagicMock()
    settings.backup_pg_dump_path = ""
    settings.backup_pg_restore_path = ""
    with patch("app.diagnostics.backup.restore.resolve_pg_tool", return_value=(None, "NOT_FOUND")):
        out = assess_restore_capability(settings)
    assert out["capable"] is False
    assert out["status"] == RestoreTestStatus.RESTORE_TEST_UNAVAILABLE.value


@pytest.mark.asyncio
async def test_config_backup_creates_checksummed_artifact(tmp_path: Path):
    from app.diagnostics.backup.runners import run_config_backup, finish_record
    from datetime import datetime, timezone

    settings = MagicMock()
    settings.diag_ui_refresh_seconds = 5
    settings.diag_ws_stale_seconds = 120
    settings.diag_db_long_query_seconds = 30
    settings.diagnostics_db_detail_cache_seconds = 60
    settings.backup_min_free_space_gb = 0.001
    settings.backup_retention_days = 90
    settings.backup_destination = str(tmp_path)
    settings.snapshot_destination = str(tmp_path)
    settings.database_enabled = True
    settings.use_real_data = True

    record = BackupRecord(
        backup_id="DIAG-20261003-TESTCFG1",
        backup_type="CONFIG",
        status=BackupJobStatus.RUNNING.value,
        started_at=datetime.now(timezone.utc),
        git_commit="abc123",
        destination_dir=str(tmp_path),
    )
    await run_config_backup(record, settings, tmp_path)
    finish_record(record)
    assert record.status == BackupJobStatus.COMPLETED.value
    assert record.verification_status == ChecksumStatus.CHECKSUM_VERIFIED.value
    assert record.checksum
    assert Path(record.destination).is_file()
    assert record.health_status == "DEGRADED"  # restore not tested


@pytest.mark.asyncio
async def test_backup_service_blocks_on_disk():
    from app.diagnostics.backup import service as svc_mod

    settings = MagicMock()
    settings.backup_destination = "data/diagnostics/backups"
    settings.backup_min_free_space_gb = 999999
    with patch.object(svc_mod, "ensure_destination", return_value={"writable": True, "path": "/x"}), patch.object(
        svc_mod,
        "check_free_space",
        return_value={
            "allowed": False,
            "reason": "BACKUP_BLOCKED_INSUFFICIENT_DISK",
            "available_bytes": 1,
        },
    ):
        out = await svc_mod.backup_service.start_backup(settings, backup_type="CONFIG")
    assert out["error_code"] == "BACKUP_BLOCKED_INSUFFICIENT_DISK"


@pytest.mark.asyncio
async def test_snapshot_create_redacts_and_checksums(tmp_path: Path):
    from app.diagnostics import snapshots as snap_mod

    settings = MagicMock()
    settings.snapshot_destination = str(tmp_path)
    settings.diag_snapshot_retention_days = 90
    settings.diag_expensive_metrics_seconds = 30

    fake_svc = MagicMock()
    fake_svc.database = AsyncMock(
        return_value={"status": "HEALTHY", "last_checked": "t", "mode": "fast"}
    )
    fake_svc.database_detail = AsyncMock(
        return_value={"status": "HEALTHY", "cached": True, "tables": []}
    )
    fake_svc.websocket = AsyncMock(
        return_value={"status": "LIVE", "connections": [], "by_stream_type": {}}
    )
    fake_svc.rest = AsyncMock(return_value={"status": "HEALTHY", "providers": []})
    fake_svc.data_health = AsyncMock(
        return_value={"status": "HEALTHY", "datasets": []}
    )
    fake_svc.jobs = AsyncMock(return_value={"status": "HEALTHY", "jobs": [], "locks": []})
    fake_svc.list_issues = AsyncMock(return_value={"count": 0, "issues": []})
    fake_svc.logs = AsyncMock(return_value={"count": 0, "entries": [], "status": "HEALTHY"})

    with patch("app.diagnostics.service.diagnostic_service", fake_svc), patch(
        "app.diagnostics.snapshots.backup_service.list_backups",
        new=AsyncMock(
            return_value={
                "status": "UNKNOWN",
                "reason": "none",
                "summary": {},
                "retention": {},
                "restore_capability": {"status": "RESTORE_TEST_UNAVAILABLE"},
                "backups": [],
            }
        ),
    ), patch(
        "app.diagnostics.snapshots.resource_sampler.sample",
        return_value={"disk": {}, "memory": {}, "cpu": {}, "sampled_at": "t"},
    ), patch(
        "app.diagnostics.snapshots.collect_git_info",
        return_value={
            "branch": "main",
            "commit": "abc",
            "commit_short": "abc",
            "dirty": False,
            "worktree_status": "CLEAN",
        },
    ):
        out = await snap_mod.snapshot_service.create(settings, label="test")

    assert out["status"] in ("VERIFIED", "FAILED")
    assert out["checksum"]
    assert Path(out["artifact_path"]).is_file()
    ai = await snap_mod.snapshot_service.ai_package(settings, out["snapshot_id"])
    assert ai is not None
    assert "SYSTEM SNAPSHOT" in ai["markdown"]
    assert "RECOVERY CONTEXT" in ai["markdown"]
    assert "sk-abc" not in ai["markdown"]
    assert "postgresql://user:secretpass" not in ai["markdown"]


def test_destination_writable(tmp_path: Path):
    out = ensure_destination(tmp_path / "nested")
    assert out["writable"] is True
