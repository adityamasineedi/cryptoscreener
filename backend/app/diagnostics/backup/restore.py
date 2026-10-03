"""Restore readiness — never restore over production."""

from __future__ import annotations

from typing import Any

from app.diagnostics.backup.models import RestoreTestStatus
from app.diagnostics.backup.runners import resolve_pg_tool


def assess_restore_capability(settings: Any) -> dict[str, Any]:
    """Determine whether an isolated restore test is safely possible."""
    dump_path, dump_ver = resolve_pg_tool(settings, kind="dump")
    restore_path, restore_ver = resolve_pg_tool(settings, kind="restore")
    reasons: list[str] = []
    if not dump_path:
        reasons.append(f"pg_dump unavailable ({dump_ver})")
    if not restore_path:
        reasons.append(f"pg_restore unavailable ({restore_ver})")

    # Creating a temporary database requires CREATE DATABASE privilege + admin URL.
    # We do not auto-provision that in Phase 3.
    reasons.append(
        "Isolated temporary database provisioning is not configured "
        "(would require a dedicated admin connection and CREATE DATABASE privilege)"
    )

    capable = False  # Phase 3 default: do not claim capability without safe temp DB
    return {
        "status": (
            RestoreTestStatus.RESTORE_TEST_CAPABLE.value
            if capable
            else RestoreTestStatus.RESTORE_TEST_UNAVAILABLE.value
        ),
        "capable": capable,
        "pg_dump": dump_path is not None,
        "pg_dump_version": dump_ver if dump_path else None,
        "pg_restore": restore_path is not None,
        "pg_restore_version": restore_ver if restore_path else None,
        "reason": "; ".join(reasons),
        "note": "Do not claim restore verified unless an isolated restore was performed",
    }


async def run_restore_test(backup: dict[str, Any], settings: Any) -> dict[str, Any]:
    """Attempt restore test — currently reports UNAVAILABLE rather than faking."""
    capability = assess_restore_capability(settings)
    if not capability["capable"]:
        return {
            "status": RestoreTestStatus.RESTORE_TEST_UNAVAILABLE.value,
            "backup_id": backup.get("backup_id"),
            "reason": capability["reason"],
            "performed": False,
        }
    # Future: create temp DB, pg_restore, drop temp DB
    return {
        "status": RestoreTestStatus.RESTORE_TEST_UNAVAILABLE.value,
        "backup_id": backup.get("backup_id"),
        "reason": "RESTORE_TEST_NOT_IMPLEMENTED_SAFELY",
        "performed": False,
    }
