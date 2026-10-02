from __future__ import annotations

from datetime import datetime, timezone

from app.core.logging import get_logger
from app.ingestion.binance_rest import BinanceRestClient
from app.models.schemas import SymbolInfo
from app.services.market_store import MarketDataStore

logger = get_logger("symbol_discovery")


def _precision_from_filter(filters: list[dict], filter_type: str, key: str) -> int | None:
    for f in filters:
        if f.get("filterType") == filter_type:
            raw = f.get(key)
            if raw is None:
                return None
            text = str(raw)
            if "." in text:
                return len(text.rstrip("0").split(".")[-1]) if "." in text.rstrip("0") else 0
            return int(raw) if str(raw).isdigit() else None
    return None


class SymbolDiscovery:
    """Dynamically discover active Binance symbols — never hardcode coin lists."""

    def __init__(
        self,
        rest: BinanceRestClient,
        store: MarketDataStore,
        *,
        quote: str = "USDT",
    ) -> None:
        self.rest = rest
        self.store = store
        self.quote = quote.upper()

    async def refresh(self) -> list[SymbolInfo]:
        futures = await self._discover_futures()
        # Spot optional for Phase 2; keep futures primary for screener
        combined = {s.symbol: s for s in futures}
        symbols = list(combined.values())
        await self.store.set_symbols(symbols)
        logger.info(
            "symbols_discovered",
            futures=len(futures),
            total=len(symbols),
            quote=self.quote,
        )
        return symbols

    async def _discover_futures(self) -> list[SymbolInfo]:
        data = await self.rest.futures_exchange_info()
        out: list[SymbolInfo] = []
        for item in data.get("symbols", []):
            if item.get("contractType") != "PERPETUAL":
                continue
            if item.get("quoteAsset") != self.quote:
                continue
            if item.get("status") != "TRADING":
                continue
            if item.get("marginAsset") and item.get("marginAsset") != self.quote:
                # Keep USDT-margined perps
                if item.get("marginAsset") != "USDT":
                    continue
            filters = item.get("filters", [])
            out.append(
                SymbolInfo(
                    symbol=item["symbol"],
                    base_asset=item["baseAsset"],
                    quote_asset=item["quoteAsset"],
                    market_type="futures_perp",
                    exchange="binance",
                    status=item.get("status", "TRADING"),
                    contract_type=item.get("contractType"),
                    price_precision=item.get("pricePrecision")
                    or _precision_from_filter(filters, "PRICE_FILTER", "tickSize"),
                    qty_precision=item.get("quantityPrecision")
                    or _precision_from_filter(filters, "LOT_SIZE", "stepSize"),
                )
            )
        return out

    async def detect_changes(
        self, previous: dict[str, SymbolInfo], current: list[SymbolInfo]
    ) -> tuple[list[SymbolInfo], list[str]]:
        current_map = {s.symbol: s for s in current}
        listed = [s for sym, s in current_map.items() if sym not in previous]
        delisted = [sym for sym in previous if sym not in current_map]
        return listed, delisted
