"""In-process backup concurrency lock — one active job per type+destination."""

from __future__ import annotations

import asyncio
from typing import Any


class BackupLockRegistry:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active: dict[str, str] = {}  # key -> backup_id

    @staticmethod
    def key(backup_type: str, destination: str) -> str:
        return f"{backup_type}|{destination}"

    async def try_acquire(self, backup_type: str, destination: str, backup_id: str) -> dict[str, Any]:
        k = self.key(backup_type, destination)
        async with self._lock:
            if k in self._active:
                return {
                    "acquired": False,
                    "error_code": "BACKUP_ALREADY_RUNNING",
                    "active_backup_id": self._active[k],
                    "lock_key": k,
                }
            self._active[k] = backup_id
            return {"acquired": True, "lock_key": k, "backup_id": backup_id}

    async def release(self, backup_type: str, destination: str, backup_id: str) -> None:
        k = self.key(backup_type, destination)
        async with self._lock:
            if self._active.get(k) == backup_id:
                del self._active[k]

    async def snapshot(self) -> dict[str, str]:
        async with self._lock:
            return dict(self._active)


backup_locks = BackupLockRegistry()
