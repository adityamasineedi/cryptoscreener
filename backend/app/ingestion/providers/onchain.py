"""On-chain / addresses / transactions provider abstraction.
Without a configured indexer API + asset mapping, all methods return WAITING.
Never fabricates holder/tx/NVT/velocity data. Never assumes chain from symbol.
"""
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
from app.services.asset_metadata import AssetMeta, asset_registry
logger = get_logger("providers.onchain")

class OnChainProvider:
    """Holder concentration + transaction metrics interface."""
    name = "onchain"
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        cfg = settings.providers_config.get("onchain") or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.provider_name = str(cfg.get("provider") or "onchain")
        self.base_url = str(cfg.get("base_url") or "")
        env_key = str(cfg.get("api_key_env") or "ONCHAIN_API_KEY")
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
        self._default_ttl = float(cfg.get("cache_ttl_seconds", 3600))
        self._field_ttl: dict[str, float] = dict(cfg.get("field_ttl_seconds") or {})
        self._cooldown_on_429 = float(rl.get("cooldown_on_429_seconds", 120))
        self._cooldown_until = 0.0
        self._client: httpx.AsyncClient | None = None
        self._last_error: str | None = None
    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.api_key)
    def in_cooldown(self) -> bool:
        return time.monotonic() < self._cooldown_until
    def _waiting(self, field: str, reason: str) -> FreshValue[Any]:
        return FreshValue.waiting(
            self.name,
            methodology=f"{field}: {reason}",
        )
    def _meta_or_waiting(self, symbol: str, field: str) -> AssetMeta | FreshValue:
        meta = asset_registry.get_for_symbol(symbol)
        if meta is None or not meta.is_queryable():
            return self._waiting(
                field,
                "no asset/chain/contract mapping in config/assets.yaml — "
                "do not assume chain from futures symbol",
            )
        if not self.configured:
            return self._waiting(
                field,
                "on-chain provider disabled or missing API key / base_url",
            )
        if self.in_cooldown():
            return FreshValue(
                value=None,
                timestamp=None,
                source=self.name,
                status=DataStatus.STALE,
                methodology=f"{field}: provider cooling down after 429",
            )
        return meta
    async def start(self) -> None:
        if self.configured:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                headers={
                    "User-Agent": "CryptoScreener/0.1",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
    # --- Required interface methods ---
    async def get_holder_count(self, symbol: str) -> FreshValue[int]:
        return await self._metric(symbol, "holder_count")
    async def get_holder_growth(self, symbol: str) -> FreshValue[float]:
        return await self._metric(symbol, "holder_growth")
    async def get_holder_distribution(self, symbol: str) -> dict[str, FreshValue]:
        keys = [
            "top_10_holder_pct",
            "top_20_holder_pct",
            "top_50_holder_pct",
            "top_100_holder_pct",
            "largest_holder_pct",
            "exchange_held_supply_pct",
        ]
        return {k: await self._metric(symbol, k) for k in keys}
    async def get_transaction_count(self, symbol: str) -> FreshValue[int]:
        return await self._metric(symbol, "transaction_count")
    async def get_transaction_volume(self, symbol: str) -> FreshValue[float]:
        return await self._metric(symbol, "transaction_volume_usd")
    async def get_large_transactions(self, symbol: str) -> dict[str, FreshValue]:
        return {
            "large_transaction_count": await self._metric(
                symbol, "large_transaction_count"
            ),
            "large_transaction_volume": await self._metric(
                symbol, "large_transaction_volume"
            ),
        }
    async def get_active_addresses(self, symbol: str) -> FreshValue[int]:
        return await self._metric(symbol, "active_addresses")
    async def get_nvt(self, symbol: str) -> FreshValue[float]:
        """True NVT = network value / on-chain transaction volume. Never uses trading volume."""
        return await self._metric(
            symbol,
            "nvt",
            methodology="market_cap / on_chain_transaction_volume_usd (true NVT)",
        )
    async def get_velocity(self, symbol: str) -> FreshValue[float]:
        """Velocity = on-chain transaction volume / network value. Requires on-chain volume."""
        return await self._metric(
            symbol,
            "velocity",
            methodology="on_chain_transaction_volume_usd / market_cap (true velocity)",
        )
    async def _metric(
        self,
        symbol: str,
        field: str,
        *,
        methodology: str | None = None,
    ) -> FreshValue[Any]:
        resolved = self._meta_or_waiting(symbol, field)
        if isinstance(resolved, FreshValue):
            if methodology:
                resolved.methodology = methodology
            return resolved
        # Configured provider path: adapter would call external API here.
        # Until a concrete vendor adapter is plugged in, remain WAITING (honest).
        return FreshValue.waiting(
            self.name,
            methodology=methodology
            or f"{field} — provider '{self.provider_name}' configured but no vendor adapter response",
        )
    async def get_holder_metrics(self, symbols: list[str]) -> dict[str, dict[str, FreshValue]]:
        out: dict[str, dict[str, FreshValue]] = {}
        for sym in symbols:
            dist = await self.get_holder_distribution(sym)
            out[sym] = {
                "holder_count": await self.get_holder_count(sym),
                "holder_growth": await self.get_holder_growth(sym),
                "top_10_holder_pct": dist["top_10_holder_pct"],
                "top_20_holder_pct": dist["top_20_holder_pct"],
                "top_50_holder_pct": dist["top_50_holder_pct"],
                "top_100_holder_pct": dist["top_100_holder_pct"],
                "largest_holder_pct": dist["largest_holder_pct"],
                "exchange_held_supply_pct": dist["exchange_held_supply_pct"],
                # Back-compat aliases used by existing tests/UI
                "top10_pct": dist["top_10_holder_pct"],
                "top20_pct": dist["top_20_holder_pct"],
                "top50_pct": dist["top_50_holder_pct"],
                "top100_pct": dist["top_100_holder_pct"],
                "exchange_held_pct": dist["exchange_held_supply_pct"],
            }
        return out
    async def get_transaction_metrics(
        self, symbols: list[str]
    ) -> dict[str, dict[str, FreshValue]]:
        out: dict[str, dict[str, FreshValue]] = {}
        for sym in symbols:
            large = await self.get_large_transactions(sym)
            tx_vol = await self.get_transaction_volume(sym)
            tx_count = await self.get_transaction_count(sym)
            out[sym] = {
                "transaction_count": tx_count,
                "transaction_volume_usd": tx_vol,
                "transaction_change": await self._metric(sym, "transaction_change"),
                "average_transaction_value": await self._metric(
                    sym, "average_transaction_value"
                ),
                "large_transaction_count": large["large_transaction_count"],
                "large_transaction_volume": large["large_transaction_volume"],
                # Back-compat
                "tx_count": tx_count,
                "tx_volume": tx_vol,
                "tx_volume_change": await self._metric(sym, "transaction_change"),
                "avg_tx_value": await self._metric(sym, "average_transaction_value"),
                "large_tx_activity": large["large_transaction_count"],
            }
        return out
    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider_name,
            "enabled": self.enabled,
            "configured": self.configured,
            "cooldown": self.in_cooldown(),
            "asset_mappings": asset_registry.stats(),
            "last_error": self._last_error,
            "note": "WAITING until on-chain provider credentials + asset mapping are configured",
            "cache_stats": self._cache.stats(),
        }
