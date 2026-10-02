from __future__ import annotations

from datetime import datetime, timezone

from app.models.schemas import DataStatus, FreshValue


def classify_freshness(
    timestamp: datetime | None,
    *,
    stale_after: float,
    unavailable_after: float,
    currently_live: bool = True,
) -> DataStatus:
    if timestamp is None:
        return DataStatus.UNAVAILABLE
    now = datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age = (now - timestamp).total_seconds()
    if age < 0:
        age = 0
    if age <= stale_after and currently_live:
        return DataStatus.LIVE
    if age <= stale_after:
        return DataStatus.CACHED
    if age <= unavailable_after:
        return DataStatus.STALE
    return DataStatus.UNAVAILABLE


def wrap_value(
    value,
    timestamp: datetime | None,
    source: str,
    *,
    stale_after: float,
    unavailable_after: float,
    currently_live: bool = True,
) -> FreshValue:
    if value is None or timestamp is None:
        return FreshValue.unavailable(source)
    status = classify_freshness(
        timestamp,
        stale_after=stale_after,
        unavailable_after=unavailable_after,
        currently_live=currently_live,
    )
    # Keep last known value even when STALE/UNAVAILABLE so UI can show CACHED-like
    # numbers with status badge — never invent a replacement value.
    return FreshValue(value=value, timestamp=timestamp, source=source, status=status)
