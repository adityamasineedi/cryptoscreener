from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings
from app.core.cache import TTLCache
from app.core.logging import get_logger
from app.core.provider_health import provider_health
from app.core.rate_limiter import RateLimiter, rate_limiters
from app.core.request_audit import request_audit
from app.core.retry import RetryPolicy
from app.models.schemas import DataStatus, FreshValue

logger = get_logger("providers.base")


class BaseFundamentalsProvider(ABC):
    """Rate limit, min interval, cache TTL, dedupe, SWR, Retry-After, cooldown."""

    name: str = "unknown"
    base_url: str = ""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None
        self._cache = TTLCache()
        self._retry = RetryPolicy(max_attempts=3, backoff_base=1.0, backoff_max=60.0)
        cfg = settings.providers_config.get(self.name, {})
        rl = cfg.get("rate_limit", {})
        rpm = float(rl.get("requests_per_minute", 12))
        self.limiter: RateLimiter = rate_limiters.get_or_create(
            self.name,
            capacity=max(rpm, 1),
            refill_per_second=rpm / 60.0,
            max_concurrency=int(rl.get("max_concurrency", 1)),
        )
        self._default_cache_ttl = float(cfg.get("cache_ttl_seconds", 600))
        self._field_ttl: dict[str, float] = dict(cfg.get("field_ttl_seconds") or {})
        self._stale_seconds = float(settings.stale_fundamental_seconds)
        self._min_interval = float(rl.get("min_interval_seconds", 0))
        self._cooldown_on_429 = float(rl.get("cooldown_on_429_seconds", 120))
        self._cooldown_until = 0.0
        self._last_request_at = 0.0
        self._inflight: dict[str, asyncio.Future] = {}
        self._swr = bool(
            (settings.providers_config.get("fundamentals") or {}).get(
                "stale_while_revalidate", True
            )
        )

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(25.0, connect=10.0),
            headers={"User-Agent": "CryptoScreener/0.1"},
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def field_ttl(self, field: str) -> float:
        return float(self._field_ttl.get(field, self._default_cache_ttl))

    def in_cooldown(self) -> bool:
        return time.monotonic() < self._cooldown_until

    def cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - time.monotonic())

    def _fresh(
        self,
        value: Any | None,
        *,
        source: str,
        ts: datetime | None,
        from_cache: bool,
    ) -> FreshValue[Any]:
        if value is None:
            return FreshValue.unavailable(source)
        if ts is None:
            return FreshValue(
                value=value, timestamp=None, source=source, status=DataStatus.LIVE
            )
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > self._stale_seconds:
            status = DataStatus.STALE
        elif from_cache:
            status = DataStatus.CACHED
        else:
            status = DataStatus.LIVE
        return FreshValue(value=value, timestamp=ts, source=source, status=status)

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        weight: float = 1.0,
        cache_key: str | None = None,
        cache_ttl: float | None = None,
    ) -> tuple[Any | None, datetime | None, bool]:
        ttl = cache_ttl or self._default_cache_ttl
        if cache_key:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                await provider_health.record_cache(self.name, hit=True)
                val, ts_iso = cached
                ts = datetime.fromisoformat(ts_iso) if ts_iso else None
                return val, ts, True
            await provider_health.record_cache(self.name, hit=False)

        if self.in_cooldown():
            logger.info(
                "provider_cooldown_skip",
                provider=self.name,
                remaining=round(self.cooldown_remaining(), 1),
            )
            return None, None, False

        # Deduplicate concurrent identical requests
        dedupe_key = cache_key or f"{path}:{params}"
        if dedupe_key in self._inflight:
            return await self._inflight[dedupe_key]

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[dedupe_key] = fut
        try:
            result = await self._do_request(path, params=params, weight=weight, cache_key=cache_key, ttl=ttl)
            fut.set_result(result)
            return result
        except Exception as exc:  # noqa: BLE001
            fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(dedupe_key, None)

    async def _do_request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None,
        weight: float,
        cache_key: str | None,
        ttl: float,
    ) -> tuple[Any | None, datetime | None, bool]:
        if self._client is None:
            raise RuntimeError(f"{self.name} provider not started")

        # Minimum interval between requests
        if self._min_interval > 0:
            wait = self._min_interval - (time.monotonic() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)

        url = f"{self.base_url.rstrip('/')}{path}"
        retry_count = 0
        count_429 = 0
        last_exc: Exception | None = None

        for attempt in range(self._retry.max_attempts):
            if self.in_cooldown():
                break
            await self.limiter.acquire(weight=weight)
            self._last_request_at = time.monotonic()
            start = time.monotonic()
            try:
                resp = await self._client.get(url, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                retry_count += 1
                await provider_health.record_error(self.name)
                await asyncio.sleep(self._retry.delay(attempt))
                continue

            latency_ms = (time.monotonic() - start) * 1000
            if resp.status_code == 429:
                count_429 += 1
                retry_count += 1
                await provider_health.record_429(self.name)
                await request_audit.log(
                    provider=self.name,
                    endpoint=path,
                    weight=int(weight),
                    status=429,
                    latency_ms=latency_ms,
                    retry_count=retry_count,
                    count_429=1,
                )
                ra = resp.headers.get("Retry-After")
                try:
                    cooldown = float(ra) if ra is not None else self._cooldown_on_429
                except ValueError:
                    cooldown = self._cooldown_on_429
                self._cooldown_until = time.monotonic() + max(cooldown, self._cooldown_on_429)
                logger.warning(
                    "provider_rate_limited",
                    provider=self.name,
                    cooldown=cooldown,
                )
                break
            if resp.status_code >= 500:
                retry_count += 1
                await provider_health.record_error(self.name)
                await request_audit.log(
                    provider=self.name,
                    endpoint=path,
                    weight=int(weight),
                    status=resp.status_code,
                    latency_ms=latency_ms,
                    retry_count=retry_count,
                )
                await asyncio.sleep(self._retry.delay(attempt))
                continue

            try:
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                await provider_health.record_error(self.name)
                break

            await provider_health.record_success(self.name, latency_ms)
            await request_audit.log(
                provider=self.name,
                endpoint=path,
                weight=int(weight),
                status=resp.status_code,
                latency_ms=latency_ms,
                retry_count=retry_count,
                count_429=count_429,
            )
            ts = datetime.now(timezone.utc)
            if cache_key:
                await self._cache.set(cache_key, (data, ts.isoformat()), ttl)
            return data, ts, False

        if last_exc:
            logger.warning(
                "provider_request_failed",
                provider=self.name,
                path=path,
                error=str(last_exc),
            )
        return None, None, False

    @abstractmethod
    async def get_market_data(self, symbols: list[str]) -> dict[str, dict[str, FreshValue[Any]]]:
        ...

    @abstractmethod
    async def get_supply_data(self, symbols: list[str]) -> dict[str, dict[str, FreshValue[Any]]]:
        ...

    @abstractmethod
    async def get_category(self, symbols: list[str]) -> dict[str, FreshValue[str]]:
        ...

    @abstractmethod
    async def get_tvl(self, symbols: list[str]) -> dict[str, FreshValue[float]]:
        ...

    @abstractmethod
    async def get_protocol_metrics(
        self, symbols: list[str]
    ) -> dict[str, dict[str, FreshValue[Any]]]:
        ...
