"""Sentiment / social metrics provider abstraction. Never fabricates sentiment."""
from __future__ import annotations

import os
from typing import Any

from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.ingestion.providers.free_social import (
    FIELDS,
    PROVIDER_NAME as FREE_PROVIDER,
    FreeSocialClient,
    get_free_social_client,
)
from app.ingestion.providers.lunarcrush import (
    PROVIDER_NAME as LC_PROVIDER,
    LunarCrushClient,
    get_lunarcrush_client,
)
from app.models.schemas import DataStatus, FreshValue

logger = get_logger("providers.sentiment")

__all__ = ["FIELDS", "SentimentProvider", "get_sentiment_provider"]

FREE_ALIASES = {FREE_PROVIDER, "socialtickers", "xoomar"}


class SentimentProvider:
    """Facade over configured social sentiment vendor (free_social or lunarcrush)."""

    name = "sentiment"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        cfg = settings.providers_config.get("sentiment") or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.provider_name = str(cfg.get("provider") or "").lower() or FREE_PROVIDER
        self.base_url = str(cfg.get("base_url") or "")
        env_key = str(cfg.get("api_key_env") or "")
        self.api_key = str(
            (os.getenv(env_key) if env_key else "")
            or os.getenv("LUNARCRUSH_API_KEY")
            or os.getenv("SENTIMENT_API_KEY")
            or cfg.get("api_key")
            or ""
        )
        self._free: FreeSocialClient | None = None
        self._lc: LunarCrushClient | None = None
        if self.provider_name in FREE_ALIASES:
            self._free = get_free_social_client(settings)
        elif self.provider_name == LC_PROVIDER:
            self._lc = get_lunarcrush_client(settings)

    @property
    def configured(self) -> bool:
        if self._free is not None:
            return self._free.configured
        if self._lc is not None:
            return self._lc.configured
        return bool(self.enabled and self.api_key and self.base_url)

    @property
    def client(self) -> FreeSocialClient | LunarCrushClient | None:
        return self._free or self._lc

    def in_cooldown(self) -> bool:
        if self._free is not None:
            return self._free.in_cooldown()
        if self._lc is not None:
            return self._lc.in_cooldown()
        return False

    def _waiting(self, field: str) -> FreshValue[Any]:
        if self.in_cooldown():
            return FreshValue(
                value=None,
                timestamp=None,
                source=self.provider_name if self.provider_name != "sentiment" else self.name,
                status=DataStatus.STALE,
                methodology=f"{field}: rate-limited (429 cooldown) — STALE/WAITING",
            )
        if not self.configured:
            return FreshValue.waiting(
                self.name,
                methodology=(
                    f"{field} requires configured sentiment provider "
                    "(free_social / LunarCrush via providers.yaml) — never fabricated"
                ),
            )
        return FreshValue.waiting(
            self.name,
            methodology=(
                f"{field} — provider '{self.provider_name}' configured but no vendor response"
            ),
        )

    async def start(self) -> None:
        if self._free is not None and self._free.configured:
            await self._free.start()
        if self._lc is not None and self._lc.configured:
            await self._lc.start()

    async def close(self) -> None:
        if self._free is not None:
            await self._free.close()
        if self._lc is not None:
            await self._lc.close()

    async def get_sentiment(self, symbols: list[str]) -> dict[str, dict[str, FreshValue]]:
        if not symbols:
            return {}

        active = self._free or self._lc
        if active is None or not active.configured:
            return {sym: {f: self._waiting(f) for f in FIELDS} for sym in symbols}

        try:
            await active.refresh_universe()
        except Exception as exc:  # noqa: BLE001
            logger.warning("sentiment_refresh_failed", provider=self.provider_name, error=str(exc))

        out: dict[str, dict[str, FreshValue]] = {}
        for sym in symbols:
            out[sym.upper()] = active.metrics_for_symbol(sym)
        return out

    def cached_social_dominance(self, symbol: str) -> FreshValue[float]:
        """Non-blocking read for screener rows — never fabricates."""
        active = self._free or self._lc
        if active is None or not active.configured:
            return FreshValue.waiting(
                self.name,
                methodology="Requires configured sentiment provider",
            )
        has_data = bool(
            getattr(active, "_st_by_symbol", None)
            or getattr(active, "_by_symbol", None)
            or getattr(active, "_xo_by_symbol", None)
        )
        if not has_data:
            return FreshValue.waiting(
                self.provider_name,
                methodology="social_dominance: waiting for provider bulk response",
            )
        return active.metrics_for_symbol(symbol).get(
            "social_dominance",
            FreshValue.waiting(self.provider_name),
        )

    def status(self) -> dict[str, Any]:
        active = self._free or self._lc
        if active is not None:
            d = active.diagnostic()
            vendor = FREE_PROVIDER if self._free is not None else LC_PROVIDER
            return {
                "name": self.name,
                "provider": vendor,
                "enabled": self.enabled,
                "configured": self.configured,
                "api_key_present": bool(getattr(active, "api_key_present", False)),
                "api_key_required": vendor == LC_PROVIDER,
                "cooldown": active.in_cooldown(),
                "last_error": d.get("last_error"),
                "connected": d.get("connected"),
                "status": d.get("status"),
                "assets_received": d.get("assets_received"),
                "assets_mapped": d.get("assets_mapped"),
                "assets_unmapped": d.get("assets_unmapped"),
                "assets_requested": d.get("assets_requested"),
                "socialtickers_received": d.get("socialtickers_received"),
                "xoomar_received": d.get("xoomar_received"),
                "last_success_at": d.get("last_success_at"),
                "last_failure_at": d.get("last_failure_at"),
                "last_response_at": d.get("last_response_at"),
                "requests_total": d.get("requests_total"),
                "requests_success": d.get("requests_success"),
                "requests_failed": d.get("requests_failed"),
                "rate_limit_hits": d.get("rate_limit_hits"),
                "plan_status": d.get("plan_status"),
                "cache_ttl_seconds": d.get("cache_ttl_seconds"),
                "stale_after_seconds": d.get("stale_after_seconds"),
                "vendors": d.get("vendors"),
                "note": d.get("note")
                or (
                    "Free socialtickers + XOOMAR metrics — never fabricated"
                    if self._free is not None
                    else "LunarCrush real social metrics — never fabricated"
                ),
                "cache_stats": d.get("cache_stats"),
            }
        return {
            "name": self.name,
            "provider": self.provider_name,
            "enabled": self.enabled,
            "configured": self.configured,
            "cooldown": False,
            "last_error": None,
            "note": "WAITING until sentiment provider is configured — never fabricated",
            "cache_stats": {},
        }

    def diagnostic(self, *, universe_size: int | None = None) -> dict[str, Any]:
        active = self._free or self._lc
        if active is not None:
            return active.diagnostic(universe_size=universe_size)
        return {
            "provider": self.provider_name,
            "configured": self.configured,
            "enabled": self.enabled,
            "api_key_present": bool(self.api_key),
            "status": "WAITING",
            "note": "No sentiment client for configured provider",
        }


_shared: SentimentProvider | None = None


def get_sentiment_provider(settings: Settings | None = None) -> SentimentProvider:
    global _shared
    if _shared is None:
        _shared = SentimentProvider(settings or get_settings())
    return _shared


def reset_sentiment_provider_for_tests() -> None:
    global _shared
    from app.ingestion.providers.free_social import reset_free_social_client_for_tests
    from app.ingestion.providers.lunarcrush import reset_lunarcrush_client_for_tests

    _shared = None
    reset_free_social_client_for_tests()
    reset_lunarcrush_client_for_tests()
