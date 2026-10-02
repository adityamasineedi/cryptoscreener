from __future__ import annotations

import asyncio

import pytest

from app.core.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_allows_within_capacity():
    lim = RateLimiter(
        name="test",
        capacity=10,
        refill_per_second=100,
        max_concurrency=2,
    )
    for _ in range(5):
        await lim.acquire(1)
    snap = lim.snapshot()
    assert snap["acquired"] == 5
    assert snap["rejected"] == 0


@pytest.mark.asyncio
async def test_rate_limiter_timeout_when_exhausted():
    lim = RateLimiter(
        name="test_timeout",
        capacity=1,
        refill_per_second=0.01,
        max_concurrency=1,
    )
    await lim.acquire(1)
    with pytest.raises(TimeoutError):
        await lim.acquire(1, timeout=0.05)


@pytest.mark.asyncio
async def test_backoff_increases():
    lim = RateLimiter(
        name="backoff",
        capacity=1,
        refill_per_second=1,
        backoff_base=0.5,
        backoff_max=10,
    )
    d0 = lim.backoff_delay(0)
    d3 = lim.backoff_delay(3)
    assert d3 >= d0
