from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.core.logging import get_logger
from app.ingestion.providers.base import BaseFundamentalsProvider
from app.ingestion.providers.coingecko import CoinGeckoProvider
from app.ingestion.providers.defillama import DefiLlamaProvider
from app.models.schemas import DataStatus, FreshValue

logger = get_logger("fundamentals_router")


class FundamentalProviderRouter:
    """
    Aggregates fundamentals from multiple providers with field-level provenance.

    Does not hammer CoinGecko on every screener refresh — caller controls interval.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.coingecko = CoinGeckoProvider(settings)
        self.defillama = DefiLlamaProvider(settings)
        cfg = settings.providers_config.get("fundamentals") or {}
        self.refresh_seconds = float(cfg.get("refresh_seconds", 600))
        self._last_refresh: datetime | None = None
        self._cache: dict[str, dict[str, FreshValue]] = {}
        # field → symbol → reason: covered | asset_not_covered | rate_limited | provider_unavailable | waiting
        self._field_reasons: dict[str, dict[str, str]] = {}

    @property
    def providers(self) -> list[BaseFundamentalsProvider]:
        return [self.coingecko, self.defillama]

    async def start(self) -> None:
        await self.coingecko.start()
        await self.defillama.start()

    async def close(self) -> None:
        await self.coingecko.close()
        await self.defillama.close()

    def due_for_refresh(self) -> bool:
        if self._last_refresh is None:
            return True
        age = (datetime.now(timezone.utc) - self._last_refresh).total_seconds()
        return age >= self.refresh_seconds

    async def refresh(self, symbols: list[str], *, force: bool = False) -> dict[str, dict[str, FreshValue]]:
        if not force and not self.due_for_refresh() and self._cache:
            return self._cache

        merged: dict[str, dict[str, FreshValue]] = {
            s: dict(self._cache.get(s) or {}) for s in symbols
        }

        reasons: dict[str, dict[str, str]] = {
            f: dict(self._field_reasons.get(f) or {})
            for f in ("market_cap", "fdv", "tvl", "market_rank", "category")
        }

        # CoinGecko: market + supply (skip if cooling down — keep SWR cache)
        cg_rate_limited = self.coingecko.in_cooldown()
        if not cg_rate_limited:
            try:
                md = await self.coingecko.get_market_data(symbols)
                sd = await self.coingecko.get_supply_data(symbols)
                cat = await self.coingecko.get_category(symbols)
                for sym in symbols:
                    for k, v in {**(md.get(sym) or {}), **(sd.get(sym) or {})}.items():
                        if isinstance(v, FreshValue) and v.value is not None:
                            merged.setdefault(sym, {})[k] = v
                            if k in reasons:
                                reasons[k][sym] = "covered"
                        elif isinstance(v, FreshValue) and v.status == DataStatus.UNAVAILABLE:
                            if k in reasons:
                                reasons[k][sym] = "asset_not_covered"
                    c = cat.get(sym)
                    if isinstance(c, FreshValue) and c.value is not None:
                        merged.setdefault(sym, {})["category"] = c
                        reasons["category"][sym] = "covered"
            except Exception as exc:  # noqa: BLE001
                logger.warning("coingecko_refresh_failed", error=str(exc))
                for sym in symbols:
                    for f in ("market_cap", "fdv", "market_rank"):
                        if reasons[f].get(sym) != "covered":
                            reasons[f][sym] = "provider_unavailable"
        else:
            logger.info(
                "coingecko_skipped_cooldown",
                remaining=round(self.coingecko.cooldown_remaining(), 1),
            )
            for sym in symbols:
                for f in ("market_cap", "fdv", "market_rank"):
                    if reasons[f].get(sym) != "covered":
                        reasons[f][sym] = "rate_limited"

        if not self.defillama.in_cooldown():
            try:
                tvl = await self.defillama.get_tvl(symbols)
                metrics = await self.defillama.get_protocol_metrics(symbols)
                dcat = await self.defillama.get_category(symbols)
                for sym in symbols:
                    t = tvl.get(sym)
                    if isinstance(t, FreshValue):
                        # Keep UNAVAILABLE if no match — never invent
                        if t.value is not None:
                            merged.setdefault(sym, {})["tvl"] = t
                            reasons["tvl"][sym] = "covered"
                        elif t.status == DataStatus.UNAVAILABLE:
                            merged.setdefault(sym, {})["tvl"] = FreshValue.unavailable(
                                "defillama",
                                methodology="asset_not_covered — not listed on DefiLlama",
                            )
                            reasons["tvl"][sym] = "asset_not_covered"
                        elif t.status != DataStatus.WAITING:
                            merged.setdefault(sym, {})["tvl"] = t
                    for k, v in (metrics.get(sym) or {}).items():
                        if isinstance(v, FreshValue) and v.value is not None:
                            merged.setdefault(sym, {})[k] = v
                    # category fallback only if missing
                    if "category" not in merged.get(sym, {}):
                        c = dcat.get(sym)
                        if isinstance(c, FreshValue) and c.value is not None:
                            merged.setdefault(sym, {})["category"] = c
                            reasons["category"][sym] = "covered"
            except Exception as exc:  # noqa: BLE001
                logger.warning("defillama_refresh_failed", error=str(exc))
                for sym in symbols:
                    if reasons["tvl"].get(sym) != "covered":
                        reasons["tvl"][sym] = "provider_unavailable"
        else:
            for sym in symbols:
                if reasons["tvl"].get(sym) != "covered":
                    reasons["tvl"][sym] = "rate_limited"

        # Ensure absent fields are WAITING not fake zeros
        for sym in symbols:
            row = merged.setdefault(sym, {})
            for field in (
                "market_cap",
                "fdv",
                "circulating_supply",
                "max_supply",
                "tvl",
                "category",
                "market_rank",
            ):
                if field not in row:
                    src = "coingecko" if field != "tvl" else "defillama"
                    reason = reasons.get(field, {}).get(sym, "waiting")
                    if reason == "rate_limited":
                        row[field] = FreshValue.waiting(
                            src, methodology="rate_limited — provider cooldown active"
                        )
                    elif reason == "asset_not_covered":
                        row[field] = FreshValue.unavailable(
                            src, methodology="asset_not_covered — provider has no mapping"
                        )
                    elif reason == "provider_unavailable":
                        row[field] = FreshValue.unavailable(
                            src, methodology="provider_unavailable"
                        )
                    else:
                        row[field] = FreshValue.waiting(src)
                        if field in reasons and sym not in reasons[field]:
                            reasons[field][sym] = "waiting"

        self._field_reasons = reasons
        self._cache = merged
        self._last_refresh = datetime.now(timezone.utc)
        return merged

    def get_cached(self, symbol: str) -> dict[str, FreshValue]:
        return dict(self._cache.get(symbol.upper()) or self._cache.get(symbol) or {})

    def coverage_reasons(self) -> dict[str, Any]:
        """Distinguish covered / asset_not_covered / rate_limited / provider_unavailable."""
        out: dict[str, Any] = {}
        for field, by_sym in self._field_reasons.items():
            counts: dict[str, int] = {}
            for reason in by_sym.values():
                counts[reason] = counts.get(reason, 0) + 1
            out[field] = {
                "counts": counts,
                "covered": counts.get("covered", 0),
                "asset_not_covered": counts.get("asset_not_covered", 0),
                "rate_limited": counts.get("rate_limited", 0),
                "provider_unavailable": counts.get("provider_unavailable", 0),
                "waiting": counts.get("waiting", 0),
            }
        return out

    def status(self) -> dict[str, Any]:
        return {
            "refresh_seconds": self.refresh_seconds,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "cached_symbols": len(self._cache),
            "coingecko_cooldown": self.coingecko.in_cooldown(),
            "coingecko_cooldown_remaining": round(self.coingecko.cooldown_remaining(), 1),
            "defillama_cooldown": self.defillama.in_cooldown(),
            "coverage_reasons": self.coverage_reasons(),
        }
