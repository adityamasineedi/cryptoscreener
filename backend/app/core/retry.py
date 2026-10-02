from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    backoff_base: float = 0.5
    backoff_max: float = 60.0
    jitter_ratio: float = 0.25

    def delay(self, attempt: int) -> float:
        exp = min(self.backoff_max, self.backoff_base * (2**attempt))
        jitter = random.uniform(0, exp * self.jitter_ratio)
        return exp + jitter


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    retry_if: Callable[[Exception], bool] | None = None,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(policy.max_attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if retry_if is not None and not retry_if(exc):
                raise
            if attempt >= policy.max_attempts - 1:
                break
            await asyncio.sleep(policy.delay(attempt))
    assert last_exc is not None
    raise last_exc
