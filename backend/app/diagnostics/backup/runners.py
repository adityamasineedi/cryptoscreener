"""Backup runners — CONFIG / DIAGNOSTICS / RESEARCH / DATABASE / FULL."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from app.diagnostics.backup.checksum import ALGORITHM, sha256_file, verify_file_checksum
from app.diagnostics.backup.disk import atomic_replace
from app.diagnostics.backup.models import (
    BackupJobStatus,
    BackupRecord,
    BackupType,
    ChecksumStatus,
    RestoreTestStatus,
    derive_backup_health,
)
from app.diagnostics.git_info import collect_git_info
from app.diagnostics.redact import redact_value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _repo_root() -> Path:
    # backend/app/diagnostics/backup/runners.py → repo root
    return Path(__file__).resolve().parents[4]


def resolve_pg_tool(settings: Any, *, kind: str) -> tuple[str | None, str | None]:
    """Return (path, version) or (None, reason)."""
    configured = (
        getattr(settings, "backup_pg_dump_path", "")
        if kind == "dump"
        else getattr(settings, "backup_pg_restore_path", "")
    )
    name = "pg_dump" if kind == "dump" else "pg_restore"
    candidates: list[str] = []
    if configured:
        candidates.append(str(configured))
    which = shutil.which(name)
    if which:
        candidates.append(which)
    for c in candidates:
        if c and Path(c).exists():
            try:
                proc = subprocess.run(
                    [c, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                ver = (proc.stdout or proc.stderr or "").strip().splitlines()[:1]
                return c, (ver[0] if ver else "unknown")
            except Exception as exc:  # noqa: BLE001
                return None, f"{exc.__class__.__name__}"
    return None, "PG_TOOL_NOT_FOUND"


def safe_db_name(database_url: str) -> str | None:
    try:
        parsed = urlparse(
            database_url.replace("postgresql+asyncpg://", "postgresql://")
        )
        return parsed.path.lstrip("/") or None
    except Exception:  # noqa: BLE001
        return None


def _env_for_pg_dump(database_url: str) -> tuple[dict[str, str], list[str] | None]:
    """Build argv + env for pg_dump without embedding password in process list records."""
    url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(url)
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    host = parsed.hostname or "localhost"
    port = str(parsed.port or 5432)
    user = parsed.username or "postgres"
    db = (parsed.path or "/").lstrip("/") or "postgres"
    argv = [
        "--host",
        host,
        "--port",
        port,
        "--username",
        user,
        "--dbname",
        db,
        "--format=custom",
        "--no-owner",
        "--no-acl",
    ]
    return env, argv


async def build_pre_backup_snapshot(settings: Any) -> dict[str, Any]:
    """Lightweight pre-backup snapshot — no expensive catalog scans."""
    from app.diagnostics.collectors.database import collect_database_fast
    from app.diagnostics.resources import resource_sampler
    from app.diagnostics.store import diagnostic_store

    git = collect_git_info()
    resources = resource_sampler.sample(settings)
    db_fast = await collect_database_fast(settings)
    issues = await diagnostic_store.list_issues(status="OPEN", limit=20)
    return redact_value(
        {
            "collected_at": _utcnow().isoformat(),
            "git": {
                "branch": git.get("branch"),
                "commit": git.get("commit_short") or git.get("commit"),
                "dirty": git.get("dirty"),
            },
            "disk": (resources.get("disk") or {}),
            "database": {
                "status": db_fast.get("status"),
                "reason": db_fast.get("reason"),
                "latency_ms": db_fast.get("latency_ms"),
                "connections": db_fast.get("connections"),
                "collected_at": db_fast.get("last_checked"),
            },
            "open_issues": [
                {
                    "diagnostic_id": i.diagnostic_id,
                    "severity": i.severity,
                    "error_code": i.error_code,
                    "message": i.message,
                }
                for i in issues[:20]
            ],
        }
    )


def _finalize_artifact(
    record: BackupRecord,
    tmp_path: Path,
    final_path: Path,
) -> None:
    record.timeline["artifact_ready"] = _utcnow().isoformat()
    size = tmp_path.stat().st_size
    if size <= 0:
        raise RuntimeError("BACKUP_EMPTY")
    digest = sha256_file(tmp_path)
    atomic_replace(tmp_path, final_path)
    record.size_bytes = int(final_path.stat().st_size)
    record.checksum = digest
    record.checksum_algorithm = ALGORITHM
    record.destination = str(final_path)
    record.timeline["checksum"] = _utcnow().isoformat()
    # Independent verification
    record.status = BackupJobStatus.VERIFYING.value
    record.timeline["verify_started"] = _utcnow().isoformat()
    vs = verify_file_checksum(final_path, digest)
    record.verification_status = vs
    record.timeline["verified"] = _utcnow().isoformat()
    if vs != ChecksumStatus.CHECKSUM_VERIFIED.value:
        record.status = BackupJobStatus.FAILED.value
        record.error = f"checksum verification: {vs}"
        return
    record.status = BackupJobStatus.COMPLETED.value


async def run_config_backup(record: BackupRecord, settings: Any, dest_dir: Path) -> None:
    root = _repo_root()
    config_dir = root / "config"
    stamp = record.backup_id.replace(":", "-")
    final_path = dest_dir / f"{stamp}_CONFIG.zip"
    tmp_path = dest_dir / f".{stamp}_CONFIG.zip.tmp"
    if tmp_path.exists():
        tmp_path.unlink()

    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if config_dir.is_dir():
            for p in sorted(config_dir.rglob("*")):
                if p.is_file():
                    zf.write(p, arcname=f"config/{p.relative_to(config_dir).as_posix()}")
        # Redacted settings summary (no secrets)
        safe_settings = {
            "diag_ui_refresh_seconds": getattr(settings, "diag_ui_refresh_seconds", None),
            "diag_ws_stale_seconds": getattr(settings, "diag_ws_stale_seconds", None),
            "diag_db_long_query_seconds": getattr(
                settings, "diag_db_long_query_seconds", None
            ),
            "diagnostics_db_detail_cache_seconds": getattr(
                settings, "diagnostics_db_detail_cache_seconds", None
            ),
            "backup_min_free_space_gb": getattr(settings, "backup_min_free_space_gb", None),
            "backup_retention_days": getattr(settings, "backup_retention_days", None),
            "backup_destination": getattr(settings, "backup_destination", None),
            "snapshot_destination": getattr(settings, "snapshot_destination", None),
            "database_enabled": getattr(settings, "database_enabled", None),
            "use_real_data": getattr(settings, "use_real_data", None),
        }
        zf.writestr(
            "settings_redacted.json",
            json.dumps(redact_value(safe_settings), indent=2),
        )
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "backup_id": record.backup_id,
                    "type": "CONFIG",
                    "created_at": _utcnow().isoformat(),
                    "git_commit": record.git_commit,
                },
                indent=2,
            ),
        )
    record.tool = "zipfile"
    record.tool_version = f"python-zipfile"
    record.validation_status = "STRUCTURAL_OK"
    _finalize_artifact(record, tmp_path, final_path)


async def run_diagnostics_backup(
    record: BackupRecord, settings: Any, dest_dir: Path
) -> None:
    from app.diagnostics.store import diagnostic_store

    stamp = record.backup_id.replace(":", "-")
    final_path = dest_dir / f"{stamp}_DIAGNOSTICS.zip"
    tmp_path = dest_dir / f".{stamp}_DIAGNOSTICS.zip.tmp"
    if tmp_path.exists():
        tmp_path.unlink()

    issues = await diagnostic_store.list_issues(limit=500)
    # Events are in-memory; pull recent via list if available
    events: list[dict[str, Any]] = []
    try:
        for iss in issues[:50]:
            evs = await diagnostic_store.get_events_for_issue(iss)
            for e in evs[-20:]:
                events.append(e.to_dict())
    except Exception:  # noqa: BLE001
        pass

    payload = redact_value(
        {
            "backup_id": record.backup_id,
            "created_at": _utcnow().isoformat(),
            "git_commit": record.git_commit,
            "thresholds": {
                "diag_ws_stale_seconds": getattr(settings, "diag_ws_stale_seconds", None),
                "diag_db_pool_warning_pct": getattr(
                    settings, "diag_db_pool_warning_pct", None
                ),
                "backup_retention_days": getattr(settings, "backup_retention_days", None),
            },
            "issues": [i.to_dict() for i in issues],
            "events": events[:1000],
        }
    )
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("diagnostics.json", json.dumps(payload, indent=2, default=str))
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "backup_id": record.backup_id,
                    "type": "DIAGNOSTICS",
                    "issue_count": len(issues),
                    "event_count": len(events),
                },
                indent=2,
            ),
        )
    record.tool = "zipfile"
    record.tool_version = "python-zipfile"
    record.validation_status = "STRUCTURAL_OK"
    record.metadata["issue_count"] = len(issues)
    _finalize_artifact(record, tmp_path, final_path)


async def run_research_backup(
    record: BackupRecord, settings: Any, dest_dir: Path
) -> None:
    root = _repo_root()
    stamp = record.backup_id.replace(":", "-")
    final_path = dest_dir / f"{stamp}_RESEARCH.zip"
    tmp_path = dest_dir / f".{stamp}_RESEARCH.zip.tmp"
    if tmp_path.exists():
        tmp_path.unlink()

    # File artifacts only — do not duplicate full PostgreSQL research tables
    candidates: list[Path] = []
    scripts = root / "backend" / "scripts"
    if scripts.is_dir():
        for pattern in ("_bos_*.json", "_backtest_*.json", "_coverage_*.json", "*_result.json"):
            candidates.extend(scripts.glob(pattern))
    research_dir = root / "backend" / "app" / "research"
    # Include small config modules as metadata only (source text, not secrets)
    config_py = research_dir / "bos_strategy_comparison" / "config.py"
    if config_py.is_file():
        candidates.append(config_py)

    # Optional manifest summary from DB (aggregates only)
    manifest_summary: dict[str, Any] = {"status": "UNKNOWN"}
    try:
        from sqlalchemy import text
        from app.services.database import db_manager

        if db_manager.enabled and db_manager.engine is not None:
            async with db_manager.engine.begin() as conn:
                exists = (
                    await conn.execute(
                        text(
                            """
                            SELECT EXISTS (
                              SELECT 1 FROM information_schema.tables
                              WHERE table_schema='public'
                                AND table_name='research_data_manifest'
                            )
                            """
                        )
                    )
                ).scalar()
                if exists:
                    row = (
                        await conn.execute(
                            text(
                                """
                                SELECT count(*), max(updated_at)
                                FROM research_data_manifest
                                """
                            )
                        )
                    ).one()
                    manifest_summary = {
                        "status": "OK",
                        "row_count": int(row[0] or 0),
                        "max_updated_at": row[1].isoformat() if row[1] else None,
                        "note": "Summary only — full rows live in DATABASE backup",
                    }
    except Exception as exc:  # noqa: BLE001
        manifest_summary = {
            "status": "UNKNOWN",
            "reason": exc.__class__.__name__,
        }

    files_added = 0
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        seen: set[str] = set()
        for p in candidates:
            if not p.is_file():
                continue
            # Skip huge files
            try:
                if p.stat().st_size > 25 * 1024 * 1024:
                    continue
            except OSError:
                continue
            arc = p.relative_to(root).as_posix()
            if arc in seen:
                continue
            seen.add(arc)
            zf.write(p, arcname=arc)
            files_added += 1
        zf.writestr(
            "research_manifest_summary.json",
            json.dumps(redact_value(manifest_summary), indent=2, default=str),
        )
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "backup_id": record.backup_id,
                    "type": "RESEARCH",
                    "files_added": files_added,
                    "note": "Artifact backup — not a full DB dump",
                },
                indent=2,
            ),
        )

    if files_added == 0 and manifest_summary.get("status") != "OK":
        # Still a valid empty-ish research backup with summary
        pass
    record.tool = "zipfile"
    record.tool_version = "python-zipfile"
    record.validation_status = "STRUCTURAL_OK"
    record.metadata["files_added"] = files_added
    record.metadata["manifest_summary"] = manifest_summary
    _finalize_artifact(record, tmp_path, final_path)


async def run_database_backup(
    record: BackupRecord, settings: Any, dest_dir: Path
) -> None:
    dump_path, version_or_reason = resolve_pg_tool(settings, kind="dump")
    if not dump_path:
        record.status = BackupJobStatus.UNAVAILABLE.value
        record.error = f"PG_DUMP_NOT_FOUND:{version_or_reason}"
        record.restore_test_status = RestoreTestStatus.RESTORE_TEST_UNAVAILABLE.value
        record.health_status = "UNAVAILABLE"
        record.health_reason = record.error
        return

    db_url = getattr(settings, "database_url", "") or ""
    record.database_name = safe_db_name(db_url)
    record.tool = "pg_dump"
    record.tool_version = version_or_reason
    stamp = record.backup_id.replace(":", "-")
    final_path = dest_dir / f"{stamp}_DATABASE.dump"
    tmp_path = dest_dir / f".{stamp}_DATABASE.dump.tmp"
    if tmp_path.exists():
        tmp_path.unlink()

    env, argv_tail = _env_for_pg_dump(db_url)
    if argv_tail is None:
        record.status = BackupJobStatus.FAILED.value
        record.error = "INVALID_DATABASE_URL"
        return
    cmd = [dump_path, *argv_tail, f"--file={tmp_path}"]
    record.timeline["pg_dump_started"] = _utcnow().isoformat()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        record.status = BackupJobStatus.FAILED.value
        record.error = "PG_DUMP_TIMEOUT"
        return
    except Exception as exc:  # noqa: BLE001
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        record.status = BackupJobStatus.FAILED.value
        record.error = f"PG_DUMP_ERROR:{exc.__class__.__name__}"
        return

    record.timeline["pg_dump_finished"] = _utcnow().isoformat()
    if proc.returncode != 0 or not tmp_path.exists():
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        record.status = BackupJobStatus.FAILED.value
        err = (proc.stderr or proc.stdout or "")[:500]
        record.error = f"PG_DUMP_FAILED:rc={proc.returncode}:{err}"
        return

    _finalize_artifact(record, tmp_path, final_path)
    if record.status != BackupJobStatus.COMPLETED.value:
        return

    # Non-destructive structural validation via pg_restore --list
    restore_path, _ = resolve_pg_tool(settings, kind="restore")
    if not restore_path:
        record.validation_status = "NOT_VALIDATED"
        record.metadata["validation_reason"] = "PG_RESTORE_NOT_FOUND"
        return
    try:
        list_proc = subprocess.run(
            [restore_path, "--list", str(final_path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if list_proc.returncode == 0 and (list_proc.stdout or "").strip():
            lines = [
                ln
                for ln in (list_proc.stdout or "").splitlines()
                if ln.strip() and not ln.startswith(";")
            ]
            record.validation_status = "PG_RESTORE_LIST_OK"
            record.metadata["pg_restore_list_entries"] = len(lines)
            record.timeline["validated"] = _utcnow().isoformat()
        else:
            record.validation_status = "VALIDATION_FAILED"
            record.status = BackupJobStatus.FAILED.value
            record.error = "pg_restore --list failed or empty"
    except Exception as exc:  # noqa: BLE001
        record.validation_status = "NOT_VALIDATED"
        record.metadata["validation_error"] = exc.__class__.__name__


async def run_full_backup(record: BackupRecord, settings: Any, dest_dir: Path) -> None:
    """FULL = CONFIG + DIAGNOSTICS + RESEARCH artifacts (not DATABASE)."""
    stamp = record.backup_id.replace(":", "-")
    final_path = dest_dir / f"{stamp}_FULL.zip"
    tmp_path = dest_dir / f".{stamp}_FULL.zip.tmp"
    if tmp_path.exists():
        tmp_path.unlink()

    parts: dict[str, str] = {}
    for btype, runner in (
        ("CONFIG", run_config_backup),
        ("DIAGNOSTICS", run_diagnostics_backup),
        ("RESEARCH", run_research_backup),
    ):
        sub = BackupRecord(
            backup_id=f"{record.backup_id}-{btype}",
            backup_type=btype,
            status=BackupJobStatus.RUNNING.value,
            started_at=_utcnow(),
            git_commit=record.git_commit,
            run_id=record.run_id,
            destination_dir=str(dest_dir),
        )
        await runner(sub, settings, dest_dir)
        if sub.destination and Path(sub.destination).is_file():
            parts[btype] = sub.destination
            # Keep part files; also embed into FULL zip
        else:
            record.metadata.setdefault("part_errors", {})[btype] = sub.error or sub.status

    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, path in parts.items():
            zf.write(path, arcname=Path(path).name)
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "backup_id": record.backup_id,
                    "type": "FULL",
                    "parts": list(parts.keys()),
                    "note": "FULL excludes DATABASE dump — create DATABASE separately",
                },
                indent=2,
            ),
        )
    if not parts:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        record.status = BackupJobStatus.FAILED.value
        record.error = "FULL_NO_PARTS"
        return
    record.tool = "zipfile+parts"
    record.tool_version = "python-zipfile"
    record.validation_status = "STRUCTURAL_OK"
    record.metadata["parts"] = list(parts.keys())
    _finalize_artifact(record, tmp_path, final_path)


RUNNERS: dict[str, Callable] = {
    BackupType.CONFIG.value: run_config_backup,
    BackupType.DIAGNOSTICS.value: run_diagnostics_backup,
    BackupType.RESEARCH.value: run_research_backup,
    BackupType.DATABASE.value: run_database_backup,
    BackupType.FULL.value: run_full_backup,
}


def finish_record(record: BackupRecord) -> None:
    record.finished_at = _utcnow()
    if record.started_at and record.finished_at:
        record.duration_ms = round(
            (record.finished_at - record.started_at).total_seconds() * 1000, 2
        )
    health, reason = derive_backup_health(record)
    record.health_status = health
    record.health_reason = reason
    record.heartbeat_at = _utcnow()
