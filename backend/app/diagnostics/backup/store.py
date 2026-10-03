"""Persist backup / snapshot metadata (DB when available + local index)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.diagnostics.backup.models import BackupRecord
from app.diagnostics.redact import redact_value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BackupMetaStore:
    def __init__(self) -> None:
        self._memory: dict[str, dict[str, Any]] = {}

    def _index_path(self, dest: Path) -> Path:
        return dest / "backup_index.json"

    def load_index(self, dest: Path) -> dict[str, Any]:
        p = self._index_path(dest)
        if not p.is_file():
            return {"backups": {}}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"backups": {}}

    def save_index(self, dest: Path, index: dict[str, Any]) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path(dest).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")
        tmp.replace(self._index_path(dest))

    def upsert_memory(self, record: BackupRecord) -> None:
        self._memory[record.backup_id] = redact_value(record.to_dict())

    def get_memory(self, backup_id: str) -> dict[str, Any] | None:
        return self._memory.get(backup_id)

    def list_memory(self) -> list[dict[str, Any]]:
        return sorted(
            self._memory.values(),
            key=lambda r: r.get("started_at") or "",
            reverse=True,
        )

    async def persist(self, record: BackupRecord, dest_dir: Path) -> None:
        data = redact_value(record.to_dict())
        self.upsert_memory(record)
        index = self.load_index(dest_dir)
        index.setdefault("backups", {})[record.backup_id] = data
        self.save_index(dest_dir, index)

        try:
            from app.services.database import db_manager

            if not db_manager.enabled or db_manager.engine is None:
                return
            async with db_manager.engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        INSERT INTO diagnostic_backup_runs (
                          id, started_at, finished_at, backup_type, status,
                          location, destination_dir, size_bytes, checksum,
                          checksum_algorithm, verified, verification_status,
                          validation_status, restore_test_status, tool,
                          tool_version, git_commit, run_id, database_name,
                          duration_ms, error, metadata
                        ) VALUES (
                          :id, :started_at, :finished_at, :backup_type, :status,
                          :location, :destination_dir, :size_bytes, :checksum,
                          :checksum_algorithm, :verified, :verification_status,
                          :validation_status, :restore_test_status, :tool,
                          :tool_version, :git_commit, :run_id, :database_name,
                          :duration_ms, :error, CAST(:metadata AS jsonb)
                        )
                        ON CONFLICT (id) DO UPDATE SET
                          finished_at = EXCLUDED.finished_at,
                          status = EXCLUDED.status,
                          location = EXCLUDED.location,
                          destination_dir = EXCLUDED.destination_dir,
                          size_bytes = EXCLUDED.size_bytes,
                          checksum = EXCLUDED.checksum,
                          checksum_algorithm = EXCLUDED.checksum_algorithm,
                          verified = EXCLUDED.verified,
                          verification_status = EXCLUDED.verification_status,
                          validation_status = EXCLUDED.validation_status,
                          restore_test_status = EXCLUDED.restore_test_status,
                          tool = EXCLUDED.tool,
                          tool_version = EXCLUDED.tool_version,
                          git_commit = EXCLUDED.git_commit,
                          run_id = EXCLUDED.run_id,
                          database_name = EXCLUDED.database_name,
                          duration_ms = EXCLUDED.duration_ms,
                          error = EXCLUDED.error,
                          metadata = EXCLUDED.metadata
                        """
                    ),
                    {
                        "id": record.backup_id,
                        "started_at": record.started_at,
                        "finished_at": record.finished_at,
                        "backup_type": record.backup_type,
                        "status": record.status,
                        "location": record.destination,
                        "destination_dir": record.destination_dir,
                        "size_bytes": record.size_bytes,
                        "checksum": record.checksum,
                        "checksum_algorithm": record.checksum_algorithm,
                        "verified": record.verification_status == "CHECKSUM_VERIFIED",
                        "verification_status": record.verification_status,
                        "validation_status": record.validation_status,
                        "restore_test_status": record.restore_test_status,
                        "tool": record.tool,
                        "tool_version": record.tool_version,
                        "git_commit": record.git_commit,
                        "run_id": record.run_id,
                        "database_name": record.database_name,
                        "duration_ms": record.duration_ms,
                        "error": record.error,
                        "metadata": json.dumps(
                            {
                                "timeline": record.timeline,
                                "pre_backup_snapshot": record.pre_backup_snapshot,
                                "health_status": record.health_status,
                                "health_reason": record.health_reason,
                                **(record.metadata or {}),
                            },
                            default=str,
                        ),
                    },
                )
        except Exception:  # noqa: BLE001
            # Column mismatch on older schema — memory+index still hold truth
            pass

    async def list_all(self, dest_dir: Path) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        for row in self.list_memory():
            by_id[row["backup_id"]] = row
        index = self.load_index(dest_dir)
        for bid, row in (index.get("backups") or {}).items():
            by_id[bid] = row
        try:
            from app.services.database import db_manager

            if db_manager.enabled and db_manager.engine is not None:
                async with db_manager.engine.begin() as conn:
                    rows = (
                        await conn.execute(
                            text(
                                """
                                SELECT id, started_at, finished_at, backup_type, status,
                                       location, size_bytes, checksum, verification_status,
                                       verified, error, metadata
                                FROM diagnostic_backup_runs
                                ORDER BY started_at DESC
                                LIMIT 200
                                """
                            )
                        )
                    ).fetchall()
                    for r in rows:
                        bid = r[0]
                        meta = r[11] if isinstance(r[11], dict) else {}
                        by_id[bid] = {
                            "backup_id": bid,
                            "started_at": r[1].isoformat() if r[1] else None,
                            "finished_at": r[2].isoformat() if r[2] else None,
                            "backup_type": r[3],
                            "status": r[4],
                            "destination": r[5],
                            "size_bytes": r[6],
                            "checksum": r[7],
                            "verification_status": r[8],
                            "verified": bool(r[9]),
                            "error": r[10],
                            **(meta if isinstance(meta, dict) else {}),
                        }
        except Exception:  # noqa: BLE001
            pass
        return sorted(
            by_id.values(),
            key=lambda r: r.get("started_at") or "",
            reverse=True,
        )


backup_meta_store = BackupMetaStore()
