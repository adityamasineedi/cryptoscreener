from __future__ import annotations

import re
from typing import Any

from app.config import Settings
from app.ingestion.providers.base import BaseFundamentalsProvider
from app.models.schemas import FreshValue


def _normalize_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _base_from_symbol(symbol: str) -> str:
    for q in ("USDT", "USDC", "BUSD", "USD"):
        if symbol.upper().endswith(q):
            return symbol[: -len(q)].upper()
    return symbol.upper()


class DefiLlamaProvider(BaseFundamentalsProvider):
    name = "defillama"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        cfg = settings.providers_config.get("defillama", {})
        self.base_url = str(cfg.get("base_url", "https://api.llama.fi"))
        self._protocol_index: dict[str, dict[str, Any]] | None = None

    async def _load_protocols(self) -> dict[str, dict[str, Any]]:
        if self._protocol_index is not None:
            return self._protocol_index
        data, ts, from_cache = await self._get_json(
            "/protocols",
            weight=1.0,
            cache_key="protocols",
            cache_ttl=600.0,
        )
        index: dict[str, dict[str, Any]] = {}
        if isinstance(data, list):
            for p in data:
                sym = p.get("symbol")
                name = p.get("name") or p.get("slug") or ""
                entry = {**p, "_ts": ts, "_from_cache": from_cache}
                if sym:
                    index[str(sym).upper()] = entry
                if name:
                    index[_normalize_name(str(name))] = entry
                slug = p.get("slug")
                if slug:
                    index[_normalize_name(str(slug))] = entry
        self._protocol_index = index
        return index

    def _match_protocol(self, symbol: str, index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        base = _base_from_symbol(symbol)
        if base in index:
            return index[base]
        norm = _normalize_name(base)
        if norm in index:
            return index[norm]
        for key, proto in index.items():
            if key.isupper() and len(key) <= 6:
                continue
            pname = _normalize_name(str(proto.get("name", "")))
            if norm and (norm in pname or pname.startswith(norm)):
                return proto
        return None

    async def get_market_data(self, symbols: list[str]) -> dict[str, dict[str, FreshValue[Any]]]:
        return {sym: {} for sym in symbols}

    async def get_supply_data(self, symbols: list[str]) -> dict[str, dict[str, FreshValue[Any]]]:
        return {sym: {} for sym in symbols}

    async def get_category(self, symbols: list[str]) -> dict[str, FreshValue[str]]:
        index = await self._load_protocols()
        source = self.name
        out: dict[str, FreshValue[str]] = {}
        for sym in symbols:
            proto = self._match_protocol(sym, index)
            if proto is None:
                out[sym] = FreshValue.unavailable(source)
                continue
            cat = proto.get("category")
            if cat is None:
                out[sym] = FreshValue.unavailable(source)
            else:
                out[sym] = self._fresh(
                    str(cat),
                    source=source,
                    ts=proto.get("_ts"),
                    from_cache=bool(proto.get("_from_cache")),
                )
        return out

    async def get_tvl(self, symbols: list[str]) -> dict[str, FreshValue[float]]:
        index = await self._load_protocols()
        source = self.name
        out: dict[str, FreshValue[float]] = {}
        for sym in symbols:
            proto = self._match_protocol(sym, index)
            if proto is None:
                out[sym] = FreshValue.unavailable(source)
                continue
            tvl_raw = proto.get("tvl")
            if tvl_raw is None:
                out[sym] = FreshValue.unavailable(source)
            else:
                out[sym] = self._fresh(
                    float(tvl_raw),
                    source=source,
                    ts=proto.get("_ts"),
                    from_cache=bool(proto.get("_from_cache")),
                )
        return out

    async def get_protocol_metrics(
        self, symbols: list[str]
    ) -> dict[str, dict[str, FreshValue[Any]]]:
        index = await self._load_protocols()
        source = self.name
        out: dict[str, dict[str, FreshValue[Any]]] = {}
        for sym in symbols:
            proto = self._match_protocol(sym, index)
            fees_f = FreshValue.unavailable(source)
            rev_f = FreshValue.unavailable(source)
            if proto is not None:
                metrics = proto.get("metrics") or {}
                fees = metrics.get("fees") if isinstance(metrics, dict) else None
                revenue = metrics.get("revenue") if isinstance(metrics, dict) else None
                if fees is not None and isinstance(fees, dict) and "24h" in fees:
                    fees_f = self._fresh(
                        float(fees["24h"]),
                        source=source,
                        ts=proto.get("_ts"),
                        from_cache=bool(proto.get("_from_cache")),
                    )
                elif proto.get("change_1d") is not None and proto.get("mcap") is not None:
                    pass
                if revenue is not None and isinstance(revenue, dict) and "24h" in revenue:
                    rev_f = self._fresh(
                        float(revenue["24h"]),
                        source=source,
                        ts=proto.get("_ts"),
                        from_cache=bool(proto.get("_from_cache")),
                    )
            out[sym] = {"fees_24h": fees_f, "revenue_24h": rev_f}
        return out
