"""Disk safety checks before backup — never invent free space."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


def ensure_destination(path: Path) -> dict[str, Any]:
    """Create destination if needed; report writable status."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {
            "exists": True,
            "writable": True,
            "path": str(path.resolve()),
            "status": "OK",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "exists": path.exists(),
            "writable": False,
            "path": str(path),
            "status": "NOT_WRITABLE",
            "error": f"{exc.__class__.__name__}:{exc}",
        }


def disk_free_bytes(path: Path) -> int | None:
    try:
        usage = shutil.disk_usage(str(path if path.exists() else path.parent or Path(".")))
        return int(usage.free)
    except Exception:  # noqa: BLE001
        return None


def check_free_space(
    path: Path,
    *,
    min_free_gb: float,
    estimated_bytes: int | None = None,
) -> dict[str, Any]:
    free = disk_free_bytes(path)
    min_bytes = int(float(min_free_gb) * (1024**3))
    required = None
    if estimated_bytes is not None:
        required = int(estimated_bytes) + min_bytes
    out: dict[str, Any] = {
        "available_bytes": free,
        "available_gb": round(free / (1024**3), 3) if free is not None else None,
        "safety_margin_gb": float(min_free_gb),
        "safety_margin_bytes": min_bytes,
        "estimated_bytes": estimated_bytes,
        "required_bytes": required,
        "status": "UNKNOWN",
        "reason": None,
        "allowed": False,
    }
    if free is None:
        out["status"] = "UNKNOWN"
        out["reason"] = "FREE_SPACE_UNAVAILABLE"
        out["allowed"] = False
        return out
    need = required if required is not None else min_bytes
    if free < need:
        out["status"] = "INSUFFICIENT"
        out["reason"] = "BACKUP_BLOCKED_INSUFFICIENT_DISK"
        out["allowed"] = False
        return out
    out["status"] = "OK"
    out["reason"] = "sufficient free space"
    out["allowed"] = True
    return out


def atomic_replace(tmp_path: Path, final_path: Path) -> None:
    """Atomically publish a completed backup artifact."""
    final_path.parent.mkdir(parents=True, exist_ok=True)
    # Ensure data flushed
    with tmp_path.open("rb") as f:
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(str(tmp_path), str(final_path))
