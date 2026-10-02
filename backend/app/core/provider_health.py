from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

class ProviderStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED = "DISABLED"
@dataclass

class ProviderMetrics:
    name: str
    status: ProviderStatus = ProviderStatus.HEALTHY
    total_requests: int = 0
    total_errors: int = 0
    total_429: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    latency_ms_sum: float = 0.0
    last_latency_ms: float | None = None
    last_error: str | None = None
    last_error_at: float | None = None
    last_success_at: float | None = None
    last_success_iso: str | None = None
    last_error_iso: str | None = None
    @property
    def avg_latency_ms(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.latency_ms_sum / self.total_requests
    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        if total == 0:
            return 0.0
        return self.cache_hits / total
    def snapshot(self) -> dict[str, Any]:
        successful = max(0, self.total_requests - self.total_errors - self.total_429)
        enabled = self.status != ProviderStatus.DISABLED
        return {
            "provider": self.name,
            "name": self.name,
            "enabled": enabled,
            "healthy": self.status == ProviderStatus.HEALTHY,
            "status": self.status.value,
            "requests": self.total_requests,
            "successful": successful,
            "failed": self.total_errors,
            "429_count": self.total_429,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
            "last_success": self.last_success_iso,
            "last_error": self.last_error,
            "last_error_at": self.last_error_iso,
            "cooldown_until": None,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "last_latency_ms": self.last_latency_ms,
        }

class ProviderHealth:
    """Registry tracking latency, 429s, errors, cache, and status per provider."""
    def __init__(self) -> None:
        self._providers: dict[str, ProviderMetrics] = {}
        self._lock = asyncio.Lock()
    def _get(self, name: str) -> ProviderMetrics:
        if name not in self._providers:
            self._providers[name] = ProviderMetrics(name=name)
        return self._providers[name]
    async def record_success(self, name: str, latency_ms: float) -> None:
        async with self._lock:
            m = self._get(name)
            m.total_requests += 1
            m.latency_ms_sum += latency_ms
            m.last_latency_ms = latency_ms
            m.last_success_at = time.monotonic()
            m.last_success_iso = datetime.now(timezone.utc).isoformat()
            m.status = ProviderStatus.HEALTHY
    async def record_429(self, name: str) -> None:
        async with self._lock:
            m = self._get(name)
            m.total_requests += 1
            m.total_429 += 1
            m.last_error = "HTTP 429"
            m.last_error_at = time.monotonic()
            m.last_error_iso = datetime.now(timezone.utc).isoformat()
            m.status = ProviderStatus.DEGRADED
    async def record_error(self, name: str, error: str | None = None) -> None:
        async with self._lock:
            m = self._get(name)
            m.total_requests += 1
            m.total_errors += 1
            m.last_error = error or "error"
            m.last_error_at = time.monotonic()
            m.last_error_iso = datetime.now(timezone.utc).isoformat()
            if m.total_errors >= 5 and (
                m.last_success_at is None
                or (time.monotonic() - m.last_success_at) > 120
            ):
                m.status = ProviderStatus.UNAVAILABLE
            else:
                m.status = ProviderStatus.DEGRADED
    async def record_cache(self, name: str, *, hit: bool) -> None:
        async with self._lock:
            m = self._get(name)
            if hit:
                m.cache_hits += 1
            else:
                m.cache_misses += 1
    async def mark_disabled(self, name: str) -> None:
        async with self._lock:
            m = self._get(name)
            m.status = ProviderStatus.DISABLED
    async def snapshot_all(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [m.snapshot() for m in self._providers.values()]
provider_health = ProviderHealth()
