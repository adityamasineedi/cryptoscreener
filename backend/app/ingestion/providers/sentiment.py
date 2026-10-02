"""Sentiment / social metrics provider abstraction. Never fabricates sentiment."""
from __future__ import annotations
import os
import time
from typing import Any
import httpx
from app.config import Settings
from app.core.cache import TTLCache
from app.core.logging import get_logger
from app.core.rate_limiter import RateLimiter, rate_limiters
from app.core.retry import RetryPolicy
from app.models.schemas import DataStatus, FreshValue
logger = get_logger("providers.sentiment")
FIELDS = (
    "social_dominance",
    "social_volume",
    "mentions",
    "engagement",
    "sentiment",
    "sentiment_change",
)

class SentimentProvider:
    name = "sentiment"
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        cfg = settings.providers_config.get("sentiment") or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.provider_name = str(cfg.get("provider") or "sentiment")
        self.base_url = str(cfg.get("base_url") or "")
        env_key = str(cfg.get("api_key_env") or "SENTIMENT_API_KEY")
        self.api_key = str(os.getenv(env_key) or cfg.get("api_key") or "")
        self._cache = TTLCache()
        self._retry = RetryPolicy(max_attempts=3, backoff_base=1.0, backoff_max=60.0)
        rl = cfg.get("rate_limit") or {}
        rpm = float(rl.get("requests_per_minute", 30))
        self.limiter: RateLimiter = rate_limiters.get_or_create(
            self.name,
            capacity=max(rpm, 1),
            refill_per_second=rpm / 60.0,
            max_concurrency=int(rl.get("max_concurrency", 1)),
        )
        self._default_ttl = float(cfg.get("cache_ttl_seconds", 300))
        self._field_ttl: dict[str, float] = dict(cfg.get("field_ttl_seconds") or {})
        self._cooldown_on_429 = float(rl.get("cooldown_on_429_seconds", 120))
        self._cooldown_until = 0.0
        self._client: httpx.AsyncClient | None = None
        self._last_error: str | None = None
    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.api_key and self.base_url)
    def in_cooldown(self) -> bool:
        return time.monotonic() < self._cooldown_until
    def _waiting(self, field: str) -> FreshValue[Any]:
        if self.in_cooldown():
            return FreshValue(
                value=None,
                timestamp=None,
                source=self.name,
                status=DataStatus.STALE,
                methodology=f"{field}: rate-limited (429 cooldown) — STALE/WAITING",
            )
        if not self.configured:
            return FreshValue.waiting(
                self.name,
                methodology=(
                    f"{field} requires configured sentiment provider "
                    "(Santiment/LunarCrush/etc via providers.yaml) — never fabricated"
                ),
            )
        return FreshValue.waiting(
            self.name,
            methodology=(
                f"{field} — provider '{self.provider_name}' configured but no vendor response"
            ),
        )
    async def start(self) -> None:
        if self.configured:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(25.0, connect=10.0),
                headers={
                    "User-Agent": "CryptoScreener/0.1",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
    async def get_sentiment(self, symbols: list[str]) -> dict[str, dict[str, FreshValue]]:
        return {sym: {f: self._waiting(f) for f in FIELDS} for sym in symbols}
    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider_name,
            "enabled": self.enabled,
            "configured": self.configured,
            "cooldown": self.in_cooldown(),
            "last_error": self._last_error,
            "note": "WAITING until sentiment provider is configured — never fabricated",
            "cache_stats": self._cache.stats(),
        }
