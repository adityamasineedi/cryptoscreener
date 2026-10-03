"""Unit tests for free socialtickers + XOOMAR sentiment — mocked HTTP."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.ingestion.providers.free_social import (
    FreeSocialClient,
    candidate_bases,
    reset_free_social_client_for_tests,
)
from app.ingestion.providers.sentiment import (
    SentimentProvider,
    reset_sentiment_provider_for_tests,
)
from app.models.schemas import DataStatus


ST_BTC = {
    "ticker": "BTC",
    "name": "Bitcoin",
    "share": 28.5,
    "mentions": 115,
    "upvotes": 698,
}
ST_ETH = {
    "ticker": "ETH",
    "name": "Ethereum",
    "share": 10.0,
    "mentions": 50,
    "upvotes": 200,
}
XO_BTC = {
    "slug": "btc",
    "name": "Bitcoin",
    "kind": "crypto",
    "compositeScore": 0.0,  # → 50.0
}
XO_ETH = {
    "slug": "eth",
    "name": "Ethereum",
    "kind": "crypto",
    "compositeScore": -0.5,  # → 25.0
}


@pytest.fixture(autouse=True)
def _reset():
    reset_free_social_client_for_tests()
    reset_sentiment_provider_for_tests()
    yield
    reset_free_social_client_for_tests()
    reset_sentiment_provider_for_tests()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> FreeSocialClient:
    settings = Settings()
    cfg = {
        "sentiment": {
            "enabled": True,
            "provider": "free_social",
            "cache_ttl_seconds": 300,
            "stale_after_seconds": 900,
            "timeout_seconds": 5,
            "rate_limit": {
                "requests_per_minute": 60,
                "max_concurrency": 1,
                "min_interval_seconds": 0,
                "cooldown_on_429_seconds": 30,
            },
            "socialtickers": {"enabled": True, "base_url": "https://socialtickers.com"},
            "xoomar": {"enabled": True, "base_url": "https://xoomar.com"},
        }
    }
    monkeypatch.setattr(type(settings), "providers_config", property(lambda self: cfg))
    reset_free_social_client_for_tests()
    return FreeSocialClient(settings)


def test_candidate_bases():
    assert candidate_bases("BTCUSDT")[-1] == "BTC"


def test_configured_without_api_key(client: FreeSocialClient):
    assert client.configured is True
    assert client.api_key_present is False


def test_mapping_and_metrics(client: FreeSocialClient):
    client._st_by_symbol = {"BTC": ST_BTC, "ETH": ST_ETH}
    client._xo_by_symbol = {"BTC": XO_BTC, "ETH": XO_ETH}
    client._observed_at = datetime.now(timezone.utc)
    client.stats.connected = True
    client.stats.assets_received = 2

    m = client.map_symbol("BTCUSDT")
    assert m.mapping_status == "MAPPED"
    assert m.provider_symbol == "BTC"

    fields = client.metrics_for_symbol("BTCUSDT")
    assert fields["social_dominance"].value == 28.5
    assert fields["social_dominance"].source == "socialtickers"
    assert fields["mentions"].value == 115
    assert fields["social_volume"].value == 115
    assert fields["engagement"].value == 698
    assert fields["sentiment"].value == pytest.approx(50.0)
    assert fields["sentiment"].source == "xoomar"
    assert fields["sentiment_change"].value is None
    assert fields["sentiment_change"].status == DataStatus.UNAVAILABLE


def test_unknown_asset(client: FreeSocialClient):
    client._st_by_symbol = {"BTC": ST_BTC}
    client._xo_by_symbol = {"BTC": XO_BTC}
    client._observed_at = datetime.now(timezone.utc)
    fields = client.metrics_for_symbol("UNKNOWNTOKENUSDT")
    for fv in fields.values():
        assert fv.value is None
        assert fv.status == DataStatus.UNAVAILABLE


def test_social_without_xoomar_sentiment(client: FreeSocialClient):
    """Coin on socialtickers but not XOOMAR → social LIVE, sentiment UNAVAILABLE."""
    client._st_by_symbol = {"ADA": {"ticker": "ADA", "share": 1.0, "mentions": 9, "upvotes": 3}}
    client._xo_by_symbol = {"BTC": XO_BTC}
    client._observed_at = datetime.now(timezone.utc)
    fields = client.metrics_for_symbol("ADAUSDT")
    assert fields["mentions"].value == 9
    assert fields["mentions"].status == DataStatus.LIVE
    assert fields["sentiment"].value is None
    assert fields["sentiment"].status == DataStatus.UNAVAILABLE
    assert "XOOMAR" in (fields["sentiment"].methodology or "")


def test_no_cross_contamination(client: FreeSocialClient):
    client._st_by_symbol = {"BTC": ST_BTC, "ETH": ST_ETH}
    client._xo_by_symbol = {"BTC": XO_BTC, "ETH": XO_ETH}
    client._observed_at = datetime.now(timezone.utc)
    btc = client.metrics_for_symbol("BTCUSDT")
    eth = client.metrics_for_symbol("ETHUSDT")
    assert btc["mentions"].value != eth["mentions"].value
    assert btc["sentiment"].value == pytest.approx(50.0)
    assert eth["sentiment"].value == pytest.approx(25.0)


def test_legitimate_zero(client: FreeSocialClient):
    client._st_by_symbol = {
        "ZERO": {"ticker": "ZERO", "share": 0, "mentions": 0, "upvotes": 0}
    }
    client._observed_at = datetime.now(timezone.utc)
    fields = client.metrics_for_symbol("ZEROUSDT")
    assert fields["mentions"].value == 0.0
    assert fields["mentions"].status == DataStatus.LIVE


def test_missing_must_not_become_zero(client: FreeSocialClient):
    client._st_by_symbol = {"MISS": {"ticker": "MISS", "share": 1.0}}  # no mentions
    client._observed_at = datetime.now(timezone.utc)
    fields = client.metrics_for_symbol("MISSUSDT")
    assert fields["mentions"].value is None
    assert fields["mentions"].status == DataStatus.UNAVAILABLE


def test_sentiment_change_with_history(client: FreeSocialClient):
    now = datetime.now(timezone.utc)
    client._st_by_symbol = {"BTC": ST_BTC}
    client._xo_by_symbol = {"BTC": {"slug": "btc", "kind": "crypto", "compositeScore": 0.2}}
    client._observed_at = now
    client._sentiment_history["BTCUSDT"] = [(now - timedelta(hours=24), 40.0)]
    fields = client.metrics_for_symbol("BTCUSDT")
    # 0.2 → 60.0; change = 60 - 40 = 20
    assert fields["sentiment"].value == pytest.approx(60.0)
    assert fields["sentiment_change"].value == pytest.approx(20.0)


def test_stale(client: FreeSocialClient):
    old = datetime.now(timezone.utc) - timedelta(seconds=2000)
    client._st_by_symbol = {"BTC": ST_BTC}
    client._xo_by_symbol = {"BTC": XO_BTC}
    client._observed_at = old
    fields = client.metrics_for_symbol("BTCUSDT")
    assert fields["social_dominance"].status == DataStatus.STALE


def test_bulk_parse(client: FreeSocialClient):
    async def run():
        await client.start()
        assert client._client is not None

        st_body = {"class": "crypto", "count": 2, "results": [ST_BTC, ST_ETH]}
        xo_body = {
            "data": {"data": [XO_BTC, XO_ETH, {"slug": "aapl", "kind": "equity", "compositeScore": 0.1}]}
        }

        async def fake_get(url, params=None):  # noqa: ANN001
            req = httpx.Request("GET", str(url))
            if "socialtickers" in str(url):
                import json

                return httpx.Response(200, content=json.dumps(st_body).encode(), request=req)
            import json

            return httpx.Response(200, content=json.dumps(xo_body).encode(), request=req)

        client._client.get = fake_get  # type: ignore[method-assign]
        ok = await client.refresh_universe(force=True)
        assert ok
        assert client.stats.socialtickers_received == 2
        assert client.stats.xoomar_received == 2  # equity filtered out
        assert client.map_symbol("ETHUSDT").mapping_status == "MAPPED"

    asyncio.run(run())


def test_http_403(client: FreeSocialClient):
    async def run():
        await client.start()
        assert client._client is not None

        async def fake_get(url, params=None):  # noqa: ANN001
            req = httpx.Request("GET", str(url))
            return httpx.Response(403, content=b'{"error":"forbidden"}', request=req)

        client._client.get = fake_get  # type: ignore[method-assign]
        data, code, err = await client._do_request(
            client.st_base, "/api/v1/leaderboard", provider_tag="socialtickers"
        )
        assert data is None
        assert code == 403
        assert err == "PLAN_FORBIDDEN"

    asyncio.run(run())


def test_sentiment_provider_facade(client: FreeSocialClient, monkeypatch: pytest.MonkeyPatch):
    from unittest.mock import AsyncMock

    p = SentimentProvider(client.settings)
    p._free = client
    p._lc = None
    client._st_by_symbol = {"BTC": ST_BTC}
    client._xo_by_symbol = {"BTC": XO_BTC}
    client._observed_at = datetime.now(timezone.utc)
    monkeypatch.setattr(client, "refresh_universe", AsyncMock(return_value=True))
    sent = asyncio.run(p.get_sentiment(["BTCUSDT"]))
    assert sent["BTCUSDT"]["social_dominance"].value == 28.5
    st = p.status()
    assert st["provider"] == "free_social"
    assert st["api_key_required"] is False
