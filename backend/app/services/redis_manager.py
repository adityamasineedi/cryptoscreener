from __future__ import annotations

from typing import Any

import redis.asyncio as redis

from app.config import Settings
from app.core.logging import get_logger

logger = get_logger("redis")


class RedisManager:
    def __init__(self) -> None:
        self.client: redis.Redis | None = None
        self.enabled = False
        self.status = "disabled"

    async def connect(self, settings: Settings) -> None:
        if not settings.redis_enabled:
            self.status = "disabled"
            self.enabled = False
            logger.info("redis_disabled_using_memory_store")
            return
        try:
            self.client = redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
            )
            await self.client.ping()
            self.enabled = True
            self.status = "ok"
            logger.info("redis_connected", url=settings.redis_url)
        except Exception as exc:  # noqa: BLE001
            self.client = None
            self.enabled = False
            self.status = f"unavailable:{exc.__class__.__name__}"
            logger.warning(
                "redis_unavailable_falling_back_to_memory",
                error=str(exc),
            )

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def health(self) -> dict[str, Any]:
        if not self.enabled or self.client is None:
            return {"status": self.status, "enabled": False}
        try:
            await self.client.ping()
            return {"status": "ok", "enabled": True}
        except Exception as exc:  # noqa: BLE001
            return {"status": f"error:{exc}", "enabled": True}


redis_manager = RedisManager()
