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


# Binance funding is an interval print (typically 8h), not a 1s ticker.
FUNDING_INTERVAL_SECONDS = 8 * 3600
FUNDING_LIVE_QUOTE_SECONDS = 120


def wrap_funding_rate(
    rate: float | None,
    timestamp: datetime | None,
    source: str,
    *,
    next_funding_time: datetime | None = None,
) -> FreshValue:
    """Keep the posted funding rate while its interval is still active.

    A 15s/60s ticker window marks a valid 0% rate UNAVAILABLE and the UI
    then shows "feed not currently live" next to 0.0000%.
    """
    if rate is None or timestamp is None:
        return FreshValue.waiting(
            source or "binance_ws",
            methodology="Waiting for mark price / funding stream",
        )
    now = datetime.now(timezone.utc)
    ts = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
    age = max(0.0, (now - ts).total_seconds())
    nxt = next_funding_time
    if nxt is not None and nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=timezone.utc)
    interval_open = nxt is not None and nxt > now
    if age <= FUNDING_LIVE_QUOTE_SECONDS:
        status = DataStatus.LIVE
        methodology = None
    elif interval_open or age <= FUNDING_INTERVAL_SECONDS:
        status = DataStatus.CACHED
        methodology = "Current funding interval rate — updates at the next funding time"
    else:
        status = DataStatus.UNAVAILABLE
        methodology = "Funding print is older than one interval"
    return FreshValue(
        value=float(rate),
        timestamp=ts,
        source=source or "binance_ws",
        status=status,
        methodology=methodology,
    )


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
