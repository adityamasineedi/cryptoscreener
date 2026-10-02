"""Redis state helpers. No-op when REDIS_ENABLED=false or Redis unavailable."""
from __future__ import annotations
import json
from typing import Any
from app.config import get_settings
from app.core.logging import get_logger
from app.services.redis_manager import redis_manager
logger = get_logger("redis_state")

def _ttl(key: str, default: int) -> int:
    cfg = get_settings().providers_config.get("cache_ttl_defaults") or {}
    return int(cfg.get(key, default))

class RedisStateStore:
    """Mirrors selected in-memory state for multi-process / restart recovery."""
    async def set_json(self, key: str, value: Any, *, ttl: int) -> bool:
        if not redis_manager.enabled or redis_manager.client is None:
            return False
        try:
            await redis_manager.client.set(
                key, json.dumps(value, default=str), ex=max(ttl, 1)
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("redis_set_json_failed", key=key, error=str(exc))
            return False
    async def get_json(self, key: str) -> Any | None:
        if not redis_manager.enabled or redis_manager.client is None:
            return None
        try:
            raw = await redis_manager.client.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("redis_get_json_failed", key=key, error=str(exc))
            return None
    async def store_market_state(self, payload: dict[str, Any]) -> None:
        await self.set_json(
            "state:market:latest",
            payload,
            ttl=_ttl("price_seconds", 5) * 24,
        )
    async def store_screener_state(self, payload: dict[str, Any]) -> None:
        await self.set_json(
            "state:screener:latest",
            payload,
            ttl=_ttl("screener_state_seconds", 30),
        )
    async def store_provider_cache(self, provider: str, cache_key: str, value: Any, ttl: int) -> None:
        await self.set_json(f"provider:{provider}:{cache_key}", value, ttl=ttl)
    async def store_oi_state(self, symbol: str, payload: dict[str, Any]) -> None:
        await self.set_json(
            f"oi:{symbol.upper()}",
            payload,
            ttl=_ttl("oi_state_seconds", 90),
        )
    async def store_fundamentals(self, symbol: str, payload: dict[str, Any]) -> None:
        await self.set_json(
            f"fund:{symbol.upper()}",
            payload,
            ttl=_ttl("market_cap_seconds", 600),
        )
    async def store_symbol_metadata(self, symbols: list[dict[str, Any]]) -> None:
        await self.set_json(
            "meta:symbols",
            symbols,
            ttl=_ttl("symbol_metadata_seconds", 300),
        )
    async def health_detail(self) -> dict[str, Any]:
        base = await redis_manager.health()
        return {
            **base,
            "keys": {
                "market_state": "state:market:latest",
                "screener_state": "state:screener:latest",
                "provider_cache_prefix": "provider:",
                "oi_prefix": "oi:",
                "fundamental_prefix": "fund:",
                "symbol_metadata": "meta:symbols",
            },
        }
redis_state = RedisStateStore()
