from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class _CacheEntry(Generic[T]):
    value: T
    expires_at: float


class TTLCache:
    """Simple TTL in-memory cache with async-safe get/set/delete + hit stats."""

    def __init__(self) -> None:
        self._data: dict[str, _CacheEntry[Any]] = {}
        self._lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None
            if time.monotonic() >= entry.expires_at:
                del self._data[key]
                self.misses += 1
                return None
            self.hits += 1
            return entry.value

    async def set(self, key: str, value: Any, ttl: float) -> None:
        async with self._lock:
            self._data[key] = _CacheEntry(value=value, expires_at=time.monotonic() + ttl)

    async def delete(self, key: str) -> bool:
        async with self._lock:
            return self._data.pop(key, None) is not None

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        hit_rate = (self.hits / total) if total else 0.0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "entries": len(self._data),
            "cache_hit_rate": round(hit_rate, 4),
        }
