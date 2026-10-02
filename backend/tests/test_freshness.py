from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.schemas import DataStatus
from app.services.freshness import classify_freshness


def test_freshness_live_stale_unavailable():
    now = datetime.now(timezone.utc)
    assert (
        classify_freshness(now, stale_after=15, unavailable_after=60, currently_live=True)
        == DataStatus.LIVE
    )
    assert (
        classify_freshness(
            now - timedelta(seconds=20),
            stale_after=15,
            unavailable_after=60,
            currently_live=True,
        )
        == DataStatus.STALE
    )
    assert (
        classify_freshness(
            now - timedelta(seconds=120),
            stale_after=15,
            unavailable_after=60,
            currently_live=True,
        )
        == DataStatus.UNAVAILABLE
    )
    assert classify_freshness(None, stale_after=15, unavailable_after=60) == DataStatus.UNAVAILABLE
