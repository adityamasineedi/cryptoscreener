from __future__ import annotations

import pytest

from app.ingestion.symbol_discovery import SymbolDiscovery
from app.models.schemas import SymbolInfo
from app.services.market_store import MarketDataStore


class FakeRest:
    async def futures_exchange_info(self):
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "pair": "BTCUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "pricePrecision": 2,
                    "quantityPrecision": 3,
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                    ],
                },
                {
                    "symbol": "ETHUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "baseAsset": "ETH",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "pricePrecision": 2,
                    "quantityPrecision": 3,
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                    ],
                },
                {
                    "symbol": "BTCUSD_PERP",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "baseAsset": "BTC",
                    "quoteAsset": "USD",
                    "marginAsset": "BTC",
                    "filters": [],
                },
                {
                    "symbol": "OLDUSDT",
                    "contractType": "PERPETUAL",
                    "status": "SETTLING",
                    "baseAsset": "OLD",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "filters": [],
                },
            ]
        }


@pytest.mark.asyncio
async def test_symbol_discovery_filters_usdt_perps():
    store = MarketDataStore()
    discovery = SymbolDiscovery(FakeRest(), store, quote="USDT")  # type: ignore[arg-type]
    symbols = await discovery.refresh()
    names = {s.symbol for s in symbols}
    assert names == {"BTCUSDT", "ETHUSDT"}
    assert "BTCUSD_PERP" not in names
    assert "OLDUSDT" not in names
    assert len(store.symbols) == 2
    btc = store.symbols["BTCUSDT"]
    assert btc.tick_size == pytest.approx(0.10)
    assert btc.step_size == pytest.approx(0.001)


@pytest.mark.asyncio
async def test_detect_listed_delisted():
    store = MarketDataStore()
    discovery = SymbolDiscovery(FakeRest(), store, quote="USDT")  # type: ignore[arg-type]
    prev = {
        "BTCUSDT": SymbolInfo(
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            market_type="futures_perp",
        ),
        "XRPUSDT": SymbolInfo(
            symbol="XRPUSDT",
            base_asset="XRP",
            quote_asset="USDT",
            market_type="futures_perp",
        ),
    }
    current = await discovery.refresh()
    listed, delisted = await discovery.detect_changes(prev, current)
    assert "ETHUSDT" in {s.symbol for s in listed}
    assert "XRPUSDT" in delisted
