from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger("rate_limiter")


@dataclass
class RateLimitStats:
    acquired: int = 0
    rejected: int = 0
    waited_ms_total: float = 0.0


class RateLimiter:
    """Token-bucket + concurrency gated rate limiter for exchange/provider REST."""

    def __init__(
        self,
        *,
        name: str,
        capacity: float,
        refill_per_second: float,
        max_concurrency: int = 5,
        backoff_base: float = 0.5,
        backoff_max: float = 60.0,
    ) -> None:
        self.name = name
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.refill_per_second = float(refill_per_second)
        self.max_concurrency = max_concurrency
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(max_concurrency)
        self.stats = RateLimitStats()
        self._recent_waits: deque[float] = deque(maxlen=100)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self._last_refill = now

    async def acquire(self, weight: float = 1.0, timeout: float | None = 30.0) -> None:
        start = time.monotonic()
        deadline = None if timeout is None else start + timeout
        async with self._sem:
            while True:
                async with self._lock:
                    self._refill()
                    if self.tokens >= weight:
                        self.tokens -= weight
                        self.stats.acquired += 1
                        waited = (time.monotonic() - start) * 1000
                        self.stats.waited_ms_total += waited
                        self._recent_waits.append(waited)
                        return
                    deficit = weight - self.tokens
                    sleep_for = deficit / self.refill_per_second if self.refill_per_second else 0.1
                if deadline is not None and time.monotonic() + sleep_for > deadline:
                    self.stats.rejected += 1
                    raise TimeoutError(f"RateLimiter[{self.name}] acquire timeout")
                await asyncio.sleep(min(max(sleep_for, 0.01), 1.0))

    def backoff_delay(self, attempt: int) -> float:
        exp = min(self.backoff_max, self.backoff_base * (2**attempt))
        jitter = random.uniform(0, exp * 0.25)
        return exp + jitter

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "tokens": round(self.tokens, 2),
            "capacity": self.capacity,
            "refill_per_second": self.refill_per_second,
            "max_concurrency": self.max_concurrency,
            "acquired": self.stats.acquired,
            "rejected": self.stats.rejected,
            "avg_wait_ms": round(
                (sum(self._recent_waits) / len(self._recent_waits))
                if self._recent_waits
                else 0.0,
                2,
            ),
        }


class RateLimiterRegistry:
    def __init__(self) -> None:
        self._limiters: dict[str, RateLimiter] = {}

    def get_or_create(
        self,
        name: str,
        *,
        capacity: float,
        refill_per_second: float,
        max_concurrency: int = 5,
        backoff_base: float = 0.5,
        backoff_max: float = 60.0,
    ) -> RateLimiter:
        if name not in self._limiters:
            self._limiters[name] = RateLimiter(
                name=name,
                capacity=capacity,
                refill_per_second=refill_per_second,
                max_concurrency=max_concurrency,
                backoff_base=backoff_base,
                backoff_max=backoff_max,
            )
            logger.info("rate_limiter_created", name=name, capacity=capacity)
        return self._limiters[name]

    def all_snapshots(self) -> list[dict]:
        return [lim.snapshot() for lim in self._limiters.values()]


rate_limiters = RateLimiterRegistry()
