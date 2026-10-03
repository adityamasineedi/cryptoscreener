"""System diagnostic snapshots — point-in-time, redacted, checksummed."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.diagnostics.backup.checksum import ALGORITHM, sha256_file, verify_file_checksum
from app.diagnostics.backup.disk import atomic_replace, ensure_destination
from app.diagnostics.backup.service import backup_service
from app.diagnostics.git_info import collect_git_info
from app.diagnostics.ids import new_diagnostic_id
from app.diagnostics.redact import redact_value
from app.diagnostics.resources import resource_sampler


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _snap_dir(settings: Any) -> Path:
    raw = getattr(settings, "snapshot_destination", "data/diagnostics/snapshots") or (
        "data/diagnostics/snapshots"
    )
    p = Path(raw)
    if not p.is_absolute():
        root = Path(__file__).resolve().parents[3]  # backend/app/diagnostics → repo? 
        # __file__ = backend/app/diagnostics/snapshots.py → parents[0]=diagnostics, [1]=app, [2]=backend, [3]=repo
        p = root / p
    return p


class SnapshotService:
    def __init__(self) -> None:
        self._memory: dict[str, dict[str, Any]] = {}

    async def create(self, settings: Any, *, label: str | None = None) -> dict[str, Any]:
        started = _utcnow()
        snapshot_id = new_diagnostic_id()
        dest = _snap_dir(settings)
        ensure_destination(dest)

        from app.diagnostics.service import diagnostic_service

        collected: dict[str, Any] = {}
        sections: dict[str, Any] = {}

        git = collect_git_info()
        sections["git"] = git
        collected["git_collected_at"] = _utcnow().isoformat()

        resources = resource_sampler.sample(settings)
        sections["resources"] = resources
        collected["resources_collected_at"] = resources.get("sampled_at") or _utcnow().isoformat()

        db = await diagnostic_service.database(settings, detect_issues=False)
        sections["database"] = {
            k: db.get(k)
            for k in (
                "status",
                "reason",
                "latency_ms",
                "database_name",
                "database_size_bytes",
                "connections",
                "pool",
                "lock_count",
                "collection_duration_ms",
                "mode",
            )
        }
        collected["database_collected_at"] = db.get("last_checked")

        # Cached detail only — never force expensive refresh on snapshot hot path
        try:
            db_detail = await diagnostic_service.database_detail(
                settings, force_refresh=False, detect_issues=False
            )
            sections["database_detail"] = {
                "status": db_detail.get("status"),
                "cached": db_detail.get("cached"),
                "cache_age_seconds": db_detail.get("cache_age_seconds"),
                "collection_duration_ms": db_detail.get("collection_duration_ms"),
                "slowest_queries": (db_detail.get("slowest_queries") or [])[:5],
                "table_count": len(db_detail.get("tables") or []),
            }
            collected["database_detail_collected_at"] = db_detail.get("collected_at")
        except Exception as exc:  # noqa: BLE001
            sections["database_detail"] = {
                "status": "UNKNOWN",
                "reason": exc.__class__.__name__,
            }

        ws = await diagnostic_service.websocket(settings, detect_issues=False)
        sections["websocket"] = {
            "status": ws.get("status"),
            "reason": ws.get("reason"),
            "connections": ws.get("connections"),
            "by_stream_type": ws.get("by_stream_type"),
        }
        collected["websocket_collected_at"] = ws.get("last_checked")

        rest = await diagnostic_service.rest(settings, detect_issues=False)
        sections["rest"] = {
            "status": rest.get("status"),
            "reason": rest.get("reason"),
            "providers": rest.get("providers"),
        }
        collected["rest_collected_at"] = rest.get("last_checked")

        dh = await diagnostic_service.data_health(settings)
        sections["data_health"] = {
            "status": dh.get("status"),
            "reason": dh.get("reason"),
            "datasets": [
                {
                    "dataset": d.get("dataset"),
                    "status": d.get("status"),
                    "coverage_percent": d.get("coverage_percent"),
                    "fresh_count": d.get("fresh_count"),
                    "stale_count": d.get("stale_count"),
                    "missing_count": d.get("missing_count"),
                }
                for d in (dh.get("datasets") or [])
            ],
        }
        collected["data_health_collected_at"] = dh.get("last_checked")

        jobs = await diagnostic_service.jobs(settings, detect_issues=False)
        sections["research"] = {
            "status": jobs.get("status"),
            "reason": jobs.get("reason"),
            "jobs": (jobs.get("jobs") or [])[:30],
            "locks": (jobs.get("locks") or [])[:30],
        }
        collected["research_collected_at"] = jobs.get("last_checked")

        issues_payload = await diagnostic_service.list_issues(status="OPEN", limit=100)
        sections["issues"] = issues_payload
        collected["issues_collected_at"] = _utcnow().isoformat()

        logs = await diagnostic_service.logs(limit=100)
        sections["logs"] = {
            "count": logs.get("count"),
            "entries": logs.get("entries"),
            "status": logs.get("status"),
        }
        collected["logs_collected_at"] = _utcnow().isoformat()

        backups = await backup_service.list_backups(settings)
        sections["backups"] = {
            "status": backups.get("status"),
            "reason": backups.get("reason"),
            "summary": backups.get("summary"),
            "retention": backups.get("retention"),
            "restore_capability": backups.get("restore_capability"),
            "recent": (backups.get("backups") or [])[:10],
        }
        collected["backups_collected_at"] = _utcnow().isoformat()

        completed = _utcnow()
        payload = redact_value(
            {
                "snapshot_id": snapshot_id,
                "label": label,
                "phase": 3,
                "snapshot_started_at": started.isoformat(),
                "snapshot_completed_at": completed.isoformat(),
                "collected_at_by_subsystem": collected,
                "git": {
                    "branch": git.get("branch"),
                    "commit": git.get("commit"),
                    "commit_short": git.get("commit_short"),
                    "dirty": git.get("dirty"),
                    "worktree_status": git.get("worktree_status"),
                },
                "sections": sections,
            }
        )

        # Write artifacts
        final_json = dest / f"{snapshot_id}.json"
        tmp_json = dest / f".{snapshot_id}.json.tmp"
        tmp_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        digest = sha256_file(tmp_json)
        size = tmp_json.stat().st_size
        atomic_replace(tmp_json, final_json)
        vs = verify_file_checksum(final_json, digest)

        md = self._to_markdown(payload)
        final_md = dest / f"{snapshot_id}.md"
        tmp_md = dest / f".{snapshot_id}.md.tmp"
        tmp_md.write_text(md, encoding="utf-8")
        atomic_replace(tmp_md, final_md)

        status = "VERIFIED" if vs == "CHECKSUM_VERIFIED" else "FAILED"
        summary = {
            "system_hint": (sections.get("database") or {}).get("status"),
            "open_issues": (issues_payload.get("count") or 0),
            "backup_status": backups.get("status"),
            "ws_status": (sections.get("websocket") or {}).get("status"),
        }
        meta = {
            "snapshot_id": snapshot_id,
            "created_at": completed.isoformat(),
            "label": label,
            "status": status,
            "size_bytes": size,
            "checksum": digest,
            "checksum_algorithm": ALGORITHM,
            "verification_status": vs,
            "artifact_path": str(final_json),
            "markdown_path": str(final_md),
            "git_commit": git.get("commit_short") or git.get("commit"),
            "git_branch": git.get("branch"),
            "summary": summary,
            "snapshot_started_at": started.isoformat(),
            "snapshot_completed_at": completed.isoformat(),
        }
        self._memory[snapshot_id] = {**meta, "payload": payload}
        await self._persist_db(meta, payload)
        self._write_index(dest, meta)
        return redact_value({**meta, "payload": payload})

    def _write_index(self, dest: Path, meta: dict[str, Any]) -> None:
        index_path = dest / "snapshot_index.json"
        try:
            index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {"snapshots": {}}
        except Exception:  # noqa: BLE001
            index = {"snapshots": {}}
        index.setdefault("snapshots", {})[meta["snapshot_id"]] = {
            k: v for k, v in meta.items() if k != "payload"
        }
        tmp = index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")
        tmp.replace(index_path)

    async def _persist_db(self, meta: dict[str, Any], payload: dict[str, Any]) -> None:
        try:
            from app.services.database import db_manager

            if not db_manager.enabled or db_manager.engine is None:
                return
            async with db_manager.engine.begin() as conn:
                # Prefer enriched columns; fall back to minimal insert
                try:
                    await conn.execute(
                        text(
                            """
                            INSERT INTO diagnostic_snapshots (
                              id, created_at, label, git_commit, git_branch, status,
                              size_bytes, checksum, checksum_algorithm, artifact_path,
                              summary, payload, immutable, verification_status
                            ) VALUES (
                              :id, NOW(), :label, :git_commit, :git_branch, :status,
                              :size_bytes, :checksum, :checksum_algorithm, :artifact_path,
                              CAST(:summary AS jsonb), CAST(:payload AS jsonb), TRUE,
                              :verification_status
                            )
                            ON CONFLICT (id) DO NOTHING
                            """
                        ),
                        {
                            "id": meta["snapshot_id"],
                            "label": meta.get("label"),
                            "git_commit": meta.get("git_commit"),
                            "git_branch": meta.get("git_branch"),
                            "status": meta.get("status"),
                            "size_bytes": meta.get("size_bytes"),
                            "checksum": meta.get("checksum"),
                            "checksum_algorithm": meta.get("checksum_algorithm"),
                            "artifact_path": meta.get("artifact_path"),
                            "summary": json.dumps(meta.get("summary") or {}),
                            "payload": json.dumps(payload, default=str),
                            "verification_status": meta.get("verification_status"),
                        },
                    )
                except Exception:  # noqa: BLE001
                    await conn.execute(
                        text(
                            """
                            INSERT INTO diagnostic_snapshots (
                              id, created_at, label, git_commit, payload, immutable
                            ) VALUES (
                              :id, NOW(), :label, :git_commit, CAST(:payload AS jsonb), TRUE
                            )
                            ON CONFLICT (id) DO NOTHING
                            """
                        ),
                        {
                            "id": meta["snapshot_id"],
                            "label": meta.get("label"),
                            "git_commit": meta.get("git_commit"),
                            "payload": json.dumps(
                                {"meta": meta, "payload": payload}, default=str
                            ),
                        },
                    )
        except Exception:  # noqa: BLE001
            pass

    async def list_snapshots(self, settings: Any) -> dict[str, Any]:
        dest = _snap_dir(settings)
        by_id: dict[str, dict[str, Any]] = {}
        for sid, row in self._memory.items():
            by_id[sid] = {k: v for k, v in row.items() if k != "payload"}
        index_path = dest / "snapshot_index.json"
        if index_path.is_file():
            try:
                index = json.loads(index_path.read_text(encoding="utf-8"))
                for sid, row in (index.get("snapshots") or {}).items():
                    by_id[sid] = row
            except Exception:  # noqa: BLE001
                pass
        rows = sorted(
            by_id.values(), key=lambda r: r.get("created_at") or "", reverse=True
        )
        retention = int(getattr(settings, "diag_snapshot_retention_days", 90) or 90)
        return {
            "phase": 3,
            "count": len(rows),
            "retention_days": retention,
            "auto_delete": False,
            "destination": str(dest),
            "snapshots": rows,
        }

    async def get(self, settings: Any, snapshot_id: str) -> dict[str, Any] | None:
        if snapshot_id in self._memory:
            return redact_value(self._memory[snapshot_id])
        dest = _snap_dir(settings)
        path = dest / f"{snapshot_id}.json"
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            meta = {
                "snapshot_id": snapshot_id,
                "artifact_path": str(path),
                "size_bytes": path.stat().st_size,
                "status": "CREATED",
            }
            return redact_value({**meta, "payload": payload})
        return None

    async def ai_package(self, settings: Any, snapshot_id: str) -> dict[str, Any] | None:
        snap = await self.get(settings, snapshot_id)
        if not snap:
            return None
        payload = snap.get("payload") or {}
        sections = payload.get("sections") or {}
        md = self._to_markdown(payload)
        # Append recovery context
        backups = sections.get("backups") or {}
        summary = backups.get("summary") or {}
        last = summary.get("last_verified_backup") or {}
        lines = [
            md,
            "",
            "==================================================",
            "RECOVERY CONTEXT",
            "==================================================",
            f"Latest verified DB/config backup: {last.get('backup_id') or 'NONE'}",
            f"Backup type: {last.get('backup_type') or '—'}",
            f"Created: {last.get('started_at') or '—'}",
            f"Checksum: {last.get('verification_status') or 'NOT_VERIFIED'}",
            f"Restore test: {last.get('restore_test_status') or 'NOT_TESTED'}",
            f"Backup health: {(backups.get('status') or 'UNKNOWN')}",
            "",
            "Safety constraints:",
            "- Do not restore over production automatically",
            "- Do not delete backups automatically",
            "- Do not modify backend/app/signals/*",
            "- Never include or request secrets/API keys",
        ]
        markdown = "\n".join(lines)
        out_path = _snap_dir(settings) / f"{snapshot_id}_ai-package.md"
        out_path.write_text(markdown, encoding="utf-8")
        return redact_value(
            {
                "snapshot_id": snapshot_id,
                "format": "markdown",
                "markdown": markdown,
                "artifact_path": str(out_path),
                "generated_at": _utcnow().isoformat(),
            }
        )

    async def export_bundle(self, settings: Any, snapshot_id: str) -> dict[str, Any] | None:
        snap = await self.get(settings, snapshot_id)
        if not snap:
            return None
        payload = snap.get("payload") or {}
        sections = payload.get("sections") or {}
        dest = _snap_dir(settings)
        bundle_path = dest / f"{snapshot_id}_bundle.zip"
        tmp = dest / f".{snapshot_id}_bundle.zip.tmp"
        if tmp.exists():
            tmp.unlink()
        ai = await self.ai_package(settings, snapshot_id)
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("snapshot.json", json.dumps(redact_value(payload), indent=2, default=str))
            zf.writestr("snapshot.md", self._to_markdown(payload))
            if ai:
                zf.writestr("ai-fix-package.md", ai.get("markdown") or "")
            mapping = {
                "issues.json": sections.get("issues"),
                "resources.json": sections.get("resources"),
                "database.json": sections.get("database"),
                "websocket.json": sections.get("websocket"),
                "rest.json": sections.get("rest"),
                "data-health.json": sections.get("data_health"),
                "research.json": sections.get("research"),
                "git.json": sections.get("git") or payload.get("git"),
            }
            for name, data in mapping.items():
                zf.writestr(name, json.dumps(redact_value(data), indent=2, default=str))
        atomic_replace(tmp, bundle_path)
        digest = sha256_file(bundle_path)
        return {
            "snapshot_id": snapshot_id,
            "bundle_path": str(bundle_path),
            "size_bytes": bundle_path.stat().st_size,
            "checksum": digest,
            "checksum_algorithm": ALGORITHM,
        }

    def _to_markdown(self, payload: dict[str, Any]) -> str:
        sections = payload.get("sections") or {}
        git = payload.get("git") or {}
        collected = payload.get("collected_at_by_subsystem") or {}
        lines = [
            "==================================================",
            "SYSTEM SNAPSHOT",
            "==================================================",
            f"Snapshot ID: {payload.get('snapshot_id')}",
            f"Started: {payload.get('snapshot_started_at')}",
            f"Completed: {payload.get('snapshot_completed_at')}",
            "",
            "Git:",
            f"- Branch: {git.get('branch')}",
            f"- Commit: {git.get('commit_short') or git.get('commit')}",
            f"- Worktree: {git.get('worktree_status')}",
            "",
            "Collection timestamps (not a single instant):",
        ]
        for k, v in collected.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
        for key in (
            "resources",
            "database",
            "websocket",
            "rest",
            "data_health",
            "research",
            "backups",
        ):
            block = sections.get(key) or {}
            lines.append(f"{key}:")
            lines.append(f"- status: {block.get('status')}")
            lines.append(f"- reason: {block.get('reason')}")
            lines.append("")
        issues = sections.get("issues") or {}
        lines.append(f"Open issues: {issues.get('count')}")
        for iss in (issues.get("issues") or [])[:20]:
            lines.append(
                f"- {iss.get('severity')} {iss.get('diagnostic_id')} "
                f"{iss.get('error_code')}: {iss.get('message')}"
            )
        lines.append("")
        lines.append("All values are measured or UNKNOWN. Secrets redacted.")
        return "\n".join(lines)


snapshot_service = SnapshotService()
