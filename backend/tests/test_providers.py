from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import Settings
from app.ingestion.providers.coingecko import CoinGeckoProvider
from app.ingestion.providers.defillama import DefiLlamaProvider
from app.models.schemas import DataStatus


@pytest.fixture
def settings() -> Settings:
    return Settings(USE_REAL_DATA=True)


@pytest.mark.asyncio
async def test_coingecko_maps_markets_without_zero_fill(settings: Settings):
    provider = CoinGeckoProvider(settings)
    provider._client = MagicMock()
    markets = [
        {
            "symbol": "btc",
            "market_cap": 1_000_000,
            "fully_diluted_valuation": None,
            "circulating_supply": 19_000_000,
        }
    ]

    async def fake_get_json(path, **kwargs):
        return markets, datetime.now(timezone.utc), False

    provider._get_json = AsyncMock(side_effect=fake_get_json)
    data = await provider.get_market_data(["BTCUSDT"])
    assert data["BTCUSDT"]["market_cap"].value == 1_000_000
    assert data["BTCUSDT"]["fdv"].status == DataStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_defillama_tvl_unavailable_when_no_match(settings: Settings):
    provider = DefiLlamaProvider(settings)

    async def fake_get_json(path, **kwargs):
        return [{"name": "Unrelated", "symbol": "ZZZ", "tvl": 123}], datetime.now(timezone.utc), False

    provider._get_json = AsyncMock(side_effect=fake_get_json)
    provider._protocol_index = None
    tvl = await provider.get_tvl(["BTCUSDT"])
    assert tvl["BTCUSDT"].status == DataStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_defillama_matches_by_symbol(settings: Settings):
    provider = DefiLlamaProvider(settings)

    async def fake_get_json(path, **kwargs):
        ts = datetime.now(timezone.utc)
        return [
            {"name": "Aave", "symbol": "AAVE", "tvl": 5000000000, "category": "Lending"},
        ], ts, False

    provider._get_json = AsyncMock(side_effect=fake_get_json)
    provider._protocol_index = None
    out = await provider.get_tvl(["AAVEUSDT"])
    assert out["AAVEUSDT"].value == 5000000000.0
    assert out["AAVEUSDT"].status == DataStatus.LIVE
