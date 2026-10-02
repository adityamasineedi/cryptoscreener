from __future__ import annotations

from typing import Any

from app.config import Settings
from app.ingestion.providers.base import BaseFundamentalsProvider
from app.models.schemas import FreshValue


def _opt_float(raw: dict[str, Any], key: str) -> float | None:
    if key not in raw:
        return None
    val = raw[key]
    if val is None:
        return None
    return float(val)


class CoinGeckoProvider(BaseFundamentalsProvider):
    name = "coingecko"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        cfg = settings.providers_config.get("coingecko", {})
        self.base_url = str(cfg.get("base_url", "https://api.coingecko.com/api/v3"))

    async def _markets_by_symbol(self, vs: str = "usd") -> dict[str, dict[str, Any]]:
        cache_key = f"markets:{vs}"
        data, ts, from_cache = await self._get_json(
            "/coins/markets",
            params={
                "vs_currency": vs,
                "order": "market_cap_desc",
                "per_page": 250,
                "page": 1,
                "sparkline": "false",
            },
            weight=1.0,
            cache_key=cache_key,
        )
        if not isinstance(data, list):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for row in data:
            sym = str(row.get("symbol", "")).upper()
            if not sym:
                continue
            out[sym] = {**row, "_ts": ts, "_from_cache": from_cache}
        page = 2
        # Free tier: keep pages low; cache + SWR covers the rest
        while len(data) >= 250 and page <= 4:
            data, ts, from_cache = await self._get_json(
                "/coins/markets",
                params={
                    "vs_currency": vs,
                    "order": "market_cap_desc",
                    "per_page": 250,
                    "page": page,
                    "sparkline": "false",
                },
                weight=1.0,
                cache_key=f"{cache_key}:p{page}",
            )
            if not isinstance(data, list) or not data:
                break
            for row in data:
                sym = str(row.get("symbol", "")).upper()
                if sym:
                    out[sym] = {**row, "_ts": ts, "_from_cache": from_cache}
            page += 1
        return out

    def _row_fields(self, row: dict[str, Any] | None, source: str) -> dict[str, FreshValue[Any]]:
        if row is None:
            return {
                "market_cap": FreshValue.unavailable(source),
                "fdv": FreshValue.unavailable(source),
                "circulating_supply": FreshValue.unavailable(source),
                "total_supply": FreshValue.unavailable(source),
                "max_supply": FreshValue.unavailable(source),
                "market_rank": FreshValue.unavailable(
                    source,
                    methodology="CoinGecko market_cap_rank — not screener table position",
                ),
            }
        ts = row.get("_ts")
        from_cache = bool(row.get("_from_cache"))
        mc = _opt_float(row, "market_cap")
        fdv = _opt_float(row, "fully_diluted_valuation")
        circ = _opt_float(row, "circulating_supply")
        total = _opt_float(row, "total_supply")
        mx = _opt_float(row, "max_supply")

        def fv(val: float | None) -> FreshValue[float]:
            if val is None:
                return FreshValue.unavailable(source)
            return self._fresh(val, source=source, ts=ts, from_cache=from_cache)

        rank_raw = row.get("market_cap_rank")
        rank_fv: FreshValue[Any]
        if rank_raw is None:
            rank_fv = FreshValue.unavailable(
                source,
                methodology="CoinGecko market_cap_rank — not screener table position",
            )
        else:
            rank_fv = self._fresh(
                int(rank_raw), source=source, ts=ts, from_cache=from_cache
            )
            rank_fv.methodology = (
                "CoinGecko market_cap_rank (provider methodology) — not screener table position"
            )

        return {
            "market_cap": fv(mc),
            "fdv": fv(fdv),
            "circulating_supply": fv(circ),
            "total_supply": fv(total),
            "max_supply": fv(mx),
            "market_rank": rank_fv,
        }

    async def get_market_data(self, symbols: list[str]) -> dict[str, dict[str, FreshValue[Any]]]:
        markets = await self._markets_by_symbol()
        source = self.name
        out: dict[str, dict[str, FreshValue[Any]]] = {}
        for sym in symbols:
            base = sym.replace("USDT", "").replace("USDC", "").upper()
            row = markets.get(base)
            fields = self._row_fields(row, source)
            out[sym] = {
                "market_cap": fields["market_cap"],
                "fdv": fields["fdv"],
                "market_rank": fields["market_rank"],
            }
        return out

    async def get_supply_data(self, symbols: list[str]) -> dict[str, dict[str, FreshValue[Any]]]:
        markets = await self._markets_by_symbol()
        source = self.name
        out: dict[str, dict[str, FreshValue[Any]]] = {}
        for sym in symbols:
            base = sym.replace("USDT", "").replace("USDC", "").upper()
            fields = self._row_fields(markets.get(base), source)
            out[sym] = {
                "circulating_supply": fields["circulating_supply"],
                "total_supply": fields["total_supply"],
                "max_supply": fields["max_supply"],
            }
        return out

    async def get_category(self, symbols: list[str]) -> dict[str, FreshValue[str]]:
        markets = await self._markets_by_symbol()
        source = self.name
        out: dict[str, FreshValue[str]] = {}
        for sym in symbols:
            base = sym.replace("USDT", "").replace("USDC", "").upper()
            row = markets.get(base)
            if row is None:
                out[sym] = FreshValue.unavailable(source)
                continue
            # /coins/markets does not include category; UNAVAILABLE unless extended
            out[sym] = FreshValue.unavailable(source)
        return out

    async def get_tvl(self, symbols: list[str]) -> dict[str, FreshValue[float]]:
        return {sym: FreshValue.unavailable(self.name) for sym in symbols}

    async def get_protocol_metrics(
        self, symbols: list[str]
    ) -> dict[str, dict[str, FreshValue[Any]]]:
        empty = FreshValue.unavailable(self.name)
        return {sym: {"fees_24h": empty, "revenue_24h": empty} for sym in symbols}
