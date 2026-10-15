"""Last time a browser reported that the UI is open.

The backend cannot see the Vite process. Health is the age of this report.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.diagnostics.constants import HealthStatus

# The diagnostics page polls about every 5s. A few missed polls stay healthy.
FRESH_SECONDS = 20.0

_last_seen: datetime | None = None


def note_frontend_seen(now: datetime | None = None) -> datetime:
    global _last_seen
    seen = now or datetime.now(timezone.utc)
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    _last_seen = seen
    return seen


def frontend_last_seen() -> datetime | None:
    return _last_seen


def frontend_health(now: datetime | None = None) -> tuple[str, str, datetime | None, float | None]:
    """Return status, reason, last success, and age in seconds."""
    checked = now or datetime.now(timezone.utc)
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    seen = _last_seen
    if seen is None:
        return HealthStatus.UNKNOWN.value, "No UI heartbeat yet", None, None
    age = max(0.0, (checked - seen).total_seconds())
    if age <= FRESH_SECONDS:
        return HealthStatus.HEALTHY.value, "UI heartbeat received", seen, age
    return (
        HealthStatus.STALE.value,
        f"Last UI heartbeat was {int(age)}s ago",
        seen,
        age,
    )
