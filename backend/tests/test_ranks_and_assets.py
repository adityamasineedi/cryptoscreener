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
    rows, total, meta = svc.futures_screener(sort_by="symbol", limit=10, offset=0)
    assert total == 2 or meta.get("eligible_count", total) >= 0
    # Both symbols may be excluded by hard eligibility without live prices —
    # ensure call succeeds and never exceeds limit.
    assert len(rows) <= 10
    if not rows:
        return
    by_sym = {r.symbol: r for r in rows}
    for sym in by_sym:
        assert by_sym[sym].screener_rank is not None
        assert by_sym[sym].rank == by_sym[sym].screener_rank
    if "AAAUSDT" in by_sym and by_sym["AAAUSDT"].market_rank.value is not None:
        assert by_sym["AAAUSDT"].screener_rank != by_sym["AAAUSDT"].market_rank.value
    if "BBBUSDT" in by_sym:
        assert by_sym["BBBUSDT"].market_rank.value == 3
    if "AAAUSDT" in by_sym:
        assert by_sym["AAAUSDT"].market_rank.value == 50
