from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.schemas import DataStatus
from app.services.freshness import classify_freshness, wrap_funding_rate


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


def test_funding_rate_stays_cached_for_the_interval_not_the_ticker_window():
    now = datetime.now(timezone.utc)
    aged = wrap_funding_rate(
        0.0,
        now - timedelta(minutes=10),
        "postgresql",
        next_funding_time=now + timedelta(hours=6),
    )
    assert aged.value == 0.0
    assert aged.status == DataStatus.CACHED
    assert aged.status != DataStatus.UNAVAILABLE

    fresh = wrap_funding_rate(0.0001, now, "binance_ws")
    assert fresh.status == DataStatus.LIVE

    expired = wrap_funding_rate(
        0.0001,
        now - timedelta(hours=10),
        "binance_ws",
        next_funding_time=now - timedelta(hours=2),
    )
    assert expired.status == DataStatus.UNAVAILABLE
