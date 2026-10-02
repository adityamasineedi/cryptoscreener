from __future__ import annotations

import asyncio

from app.config import Settings
from app.ingestion.providers.onchain import OnChainProvider
from app.models.schemas import DataStatus, FreshValue, SymbolInfo
from app.services.asset_metadata import asset_registry
from app.services.engine_store import engine_store
from app.services.market_store import MarketDataStore
from app.services.screener import ScreenerService


def test_asset_registry_no_assumed_mapping():
    # Futures symbols must not invent Ethereum/EVM mappings
    assert asset_registry.get_for_symbol("BTCUSDT") is None
    assert asset_registry.get_for_symbol("ETHUSDT") is None
    assert asset_registry.get_for_symbol("SOLUSDT") is None


def test_onchain_methods_waiting_without_mapping():
    settings = Settings()
    p = OnChainProvider(settings)
    fv = asyncio.run(p.get_holder_count("BTCUSDT"))
    assert fv.status == DataStatus.WAITING
    assert "mapping" in (fv.methodology or "").lower() or "assets.yaml" in (
        fv.methodology or ""
    )
    nvt = asyncio.run(p.get_nvt("ETHUSDT"))
    assert nvt.status == DataStatus.WAITING
    assert nvt.value is None


def test_screener_rank_vs_market_rank_separated():
    store = MarketDataStore()
    settings = Settings()
    a = SymbolInfo(
        symbol="AAAUSDT",
        base_asset="AAA",
        quote_asset="USDT",
        market_type="futures_perp",
    )
    b = SymbolInfo(
        symbol="BBBUSDT",
        base_asset="BBB",
        quote_asset="USDT",
        market_type="futures_perp",
    )
    store.symbols = {a.symbol: a, b.symbol: b}
    engine_store.set_fundamentals(
        "AAAUSDT",
        {
            "market_rank": FreshValue.live(
                50,
                "coingecko",
                methodology="CoinGecko market_cap_rank",
            )
        },
    )
    engine_store.set_fundamentals(
        "BBBUSDT",
        {
            "market_rank": FreshValue.live(
                3,
                "coingecko",
                methodology="CoinGecko market_cap_rank",
            )
        },
    )
    svc = ScreenerService(settings, store)
    rows, total = svc.futures_screener(sort_by="symbol", limit=10, offset=0)
    assert total == 2
    by_sym = {r.symbol: r for r in rows}
    assert by_sym["AAAUSDT"].screener_rank is not None
    assert by_sym["BBBUSDT"].screener_rank is not None
    assert by_sym["AAAUSDT"].screener_rank != by_sym["AAAUSDT"].market_rank.value
    assert by_sym["BBBUSDT"].market_rank.value == 3
    assert by_sym["AAAUSDT"].market_rank.value == 50
    # Table position equals screener_rank alias
    assert by_sym["AAAUSDT"].rank == by_sym["AAAUSDT"].screener_rank
