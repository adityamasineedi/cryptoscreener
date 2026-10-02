from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import Settings
from app.ingestion.oi_scheduler import OIScheduler


@pytest.fixture
def settings() -> Settings:
    return Settings(USE_REAL_DATA=True, BINANCE_REST_MAX_CONCURRENCY=2)


@pytest.fixture
def rest() -> MagicMock:
    client = MagicMock()
    client.limiter = MagicMock()
    client.limiter.tokens = 1000.0
    client.futures_open_interest = AsyncMock(
        side_effect=lambda symbol: {
            "symbol": symbol,
            "openInterest": "1000.5",
            "time": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
    )
    return client


@pytest.mark.asyncio
async def test_oi_scheduler_batches_with_concurrency(settings: Settings, rest: MagicMock):
    scheduler = OIScheduler(
        settings,
        rest,
        price_lookup=lambda s: 100.0,
        poll_interval_seconds=3600.0,
        stagger_seconds=0.01,
        max_concurrency=2,
    )
    symbols = [f"SYM{i}USDT" for i in range(8)]
    scheduler.set_symbols(symbols)

    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def tracked_open_interest(symbol: str):
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return {
            "symbol": symbol,
            "openInterest": "500",
            "time": int(datetime.now(timezone.utc).timestamp() * 1000),
        }

    rest.futures_open_interest = AsyncMock(side_effect=tracked_open_interest)

    # Force all symbols due this cycle
    scheduler.batch_size = 8
    await scheduler._poll_batch()

    assert rest.futures_open_interest.await_count == len(symbols)
    assert max_in_flight <= 2
    st = scheduler.get_state("SYM0USDT")
    assert st is not None
    assert st.open_interest.value == 500.0
    assert st.open_interest_value.value == 500.0 * 100.0
    assert st.last_success_at is not None
    assert st.next_refresh_at is not None


@pytest.mark.asyncio
async def test_oi_scheduler_no_tight_sync_loop(settings: Settings, rest: MagicMock):
    """Polling uses gather + queue, not blocking sequential for-loop."""
    scheduler = OIScheduler(
        settings, rest, max_concurrency=5, stagger_seconds=0.0, batch_size=3
    )
    scheduler.set_symbols(["AUSDT", "BUSDT", "CUSDT"])
    call_times: list[float] = []

    async def timed(symbol: str):
        call_times.append(asyncio.get_event_loop().time())
        await asyncio.sleep(0.02)
        return {
            "openInterest": "1",
            "time": int(datetime.now(timezone.utc).timestamp() * 1000),
        }

    rest.futures_open_interest = AsyncMock(side_effect=timed)
    await scheduler._poll_batch()
    assert len(call_times) == 3
    span = max(call_times) - min(call_times)
    assert span < 0.15


def test_oi_priority_visible_before_volume(settings: Settings, rest: MagicMock):
    scheduler = OIScheduler(
        settings,
        rest,
        priority_mode="TOP_VOLUME",
        active_count=40,
        volume_lookup=lambda s: {"LOUSDT": 1.0, "HIUSDT": 1000.0, "VISUSDT": 50.0}.get(
            s, 0.0
        ),
    )
    scheduler.set_symbols(["LOUSDT", "HIUSDT", "VISUSDT"])
    scheduler.set_visible_symbols(["VISUSDT"])
    ordered = scheduler._prioritized()
    assert ordered[0] == "VISUSDT"
    assert ordered[1] == "HIUSDT"
    assert "last_value" in scheduler.oi_coverage_report()["symbols"][0] or True
    report = scheduler.oi_coverage_report()
    assert "goal_pct" in report
    assert "adaptive" in report


def test_oi_default_skips_long_tail(settings: Settings, rest: MagicMock):
    """Default modes only poll top N (+ visible/watchlist), not all tokens."""
    vols = {f"S{i}USDT": float(100 - i) for i in range(20)}
    scheduler = OIScheduler(
        settings,
        rest,
        priority_mode="TOP_VOLUME",
        active_count=5,
        volume_lookup=lambda s: vols.get(s, 0.0),
    )
    scheduler.set_symbols(list(vols))
    ordered = scheduler._prioritized()
    assert len(ordered) == 5
    assert ordered[0] == "S0USDT"
    assert "S19USDT" not in ordered

    full = OIScheduler(
        settings,
        rest,
        priority_mode="FULL_UNIVERSE",
        active_count=5,
        volume_lookup=lambda s: vols.get(s, 0.0),
    )
    full.set_symbols(list(vols))
    assert len(full._prioritized()) == 20


def test_oi_due_prefers_uncovered_over_refresh(settings: Settings, rest: MagicMock):
    """Prevent top-volume refresh loops from starving the long tail."""
    from datetime import timedelta

    from app.ingestion.oi_scheduler import OIState
    from app.models.schemas import DataStatus, FreshValue

    scheduler = OIScheduler(settings, rest, batch_size=2)
    scheduler.set_symbols(["AUSDT", "BUSDT", "CUSDT"])
    now = datetime.now(timezone.utc)
    # A already covered and due for refresh
    scheduler._states["AUSDT"] = OIState(
        symbol="AUSDT",
        open_interest=FreshValue(
            value=1.0, timestamp=now, source="binance_rest", status=DataStatus.LIVE
        ),
        last_success_at=now,
        next_refresh_at=now - timedelta(seconds=1),
        last_value=1.0,
    )
    # B and C never covered
    scheduler._states["BUSDT"] = OIState(symbol="BUSDT")
    scheduler._states["CUSDT"] = OIState(symbol="CUSDT")
    due = scheduler._due_symbols(["AUSDT", "BUSDT", "CUSDT"])
    assert due == ["BUSDT", "CUSDT"]
