from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class RequestQueue:
    """Bounded-concurrency request runner with optional stagger between starts."""

    def __init__(
        self,
        *,
        max_concurrency: int = 5,
        stagger_seconds: float = 0.0,
    ) -> None:
        self._max_concurrency = max(1, max_concurrency)
        self._sem = asyncio.Semaphore(self._max_concurrency)
        self._stagger = stagger_seconds
        self._stagger_lock = asyncio.Lock()
        self._last_start = 0.0
        self._active = 0
        self._gate = asyncio.Lock()

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    @max_concurrency.setter
    def max_concurrency(self, value: int) -> None:
        self._max_concurrency = max(1, int(value))

    async def _maybe_stagger(self) -> None:
        if self._stagger <= 0:
            return
        async with self._stagger_lock:
            now = time.monotonic()
            wait = self._stagger - (now - self._last_start)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_start = time.monotonic()

    async def run(self, fn: Callable[[], Awaitable[T]]) -> T:
        # Soft concurrency cap that can shrink/grow without recreating the semaphore
        while True:
            async with self._gate:
                if self._active < self._max_concurrency:
                    self._active += 1
                    break
            await asyncio.sleep(0.02)
        try:
            await self._maybe_stagger()
            return await fn()
        finally:
            async with self._gate:
                self._active = max(0, self._active - 1)

    async def map(
        self,
        items: list[T],
        fn: Callable[[T], Awaitable[None]],
    ) -> None:
        await asyncio.gather(*(self.run(lambda i=item: fn(i)) for item in items))
