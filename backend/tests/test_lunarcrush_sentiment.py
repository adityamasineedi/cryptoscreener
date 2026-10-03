"""Unit tests for LunarCrush sentiment provider — mocked HTTP, never fabricates."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.ingestion.providers.lunarcrush import (
    LunarCrushClient,
    candidate_bases,
    reset_lunarcrush_client_for_tests,
)
from app.ingestion.providers.sentiment import (
    SentimentProvider,
    reset_sentiment_provider_for_tests,
)
from app.models.schemas import DataStatus


def _settings(**overrides: Any) -> Settings:
    s = Settings()
    # Force providers_config via monkeypatch on instance property is hard;
    # we patch LunarCrushClient config by constructing with patched load.
    return s


SAMPLE_BTC = {
    "id": 1,
    "symbol": "BTC",
    "name": "Bitcoin",
    "social_dominance": 12.5,
    "social_volume_24h": 12345,
    "interactions_24h": 456789,
    "sentiment": 67,
}
SAMPLE_ETH = {
    "id": 2,
    "symbol": "ETH",
    "name": "Ethereum",
    "social_dominance": 5.0,
    "social_volume_24h": 5000,
    "interactions_24h": 100000,
    "sentiment": 55,
}
SAMPLE_ZERO = {
    "id": 99,
    "symbol": "ZEROCOIN",
    "name": "Zero",
    "social_dominance": 0,
    "social_volume_24h": 0,
    "interactions_24h": 0,
    "sentiment": 0,
}


def _bulk_response(assets: list[dict[str, Any]], generated: int | None = None) -> dict[str, Any]:
    return {
        "config": {
            "sort": "market_cap_rank",
            "limit": 1000,
            "page": 0,
            "total_rows": len(assets),
            "generated": generated or int(datetime.now(timezone.utc).timestamp()),
        },
        "data": assets,
    }


@pytest.fixture(autouse=True)
def _reset_singletons():
    reset_lunarcrush_client_for_tests()
    reset_sentiment_provider_for_tests()
    yield
    reset_lunarcrush_client_for_tests()
    reset_sentiment_provider_for_tests()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> LunarCrushClient:
    monkeypatch.setenv("LUNARCRUSH_API_KEY", "test-key-not-real")
    settings = Settings()
    # Inject sentiment config
    cfg = dict(settings.providers_config)
    cfg["sentiment"] = {
        "enabled": True,
        "provider": "lunarcrush",
        "base_url": "https://lunarcrush.com/api4",
        "api_key_env": "LUNARCRUSH_API_KEY",
        "cache_ttl_seconds": 300,
        "stale_after_seconds": 900,
        "timeout_seconds": 5,
        "rate_limit": {
            "requests_per_minute": 60,
            "max_concurrency": 1,
            "min_interval_seconds": 0,
            "cooldown_on_429_seconds": 30,
        },
        "lunarcrush": {
            "enabled": True,
            "page_limit": 1000,
            "max_pages": 1,
            "cache_ttl_seconds": 300,
            "stale_after_seconds": 900,
        },
    }
    monkeypatch.setattr(
        type(settings),
        "providers_config",
        property(lambda self: cfg),
    )
    reset_lunarcrush_client_for_tests()
    c = LunarCrushClient(settings)
    return c


def test_candidate_bases_btcusdt():
    assert candidate_bases("BTCUSDT") == ["BTCUSDT", "BTC"]
    assert candidate_bases("ETHUSDT") == ["ETHUSDT", "ETH"]


def test_missing_api_key_waiting(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LUNARCRUSH_API_KEY", raising=False)
    monkeypatch.delenv("SENTIMENT_API_KEY", raising=False)
    settings = Settings()
    cfg = {
        "sentiment": {
            "enabled": True,
            "provider": "lunarcrush",
            "base_url": "https://lunarcrush.com/api4",
            "api_key_env": "LUNARCRUSH_API_KEY",
            "lunarcrush": {"enabled": True},
        }
    }
    monkeypatch.setattr(type(settings), "providers_config", property(lambda self: cfg))
    reset_lunarcrush_client_for_tests()
    p = SentimentProvider(settings)
    sent = asyncio.run(p.get_sentiment(["BTCUSDT"]))
    for fv in sent["BTCUSDT"].values():
        assert fv.value is None
        assert fv.status == DataStatus.WAITING


def test_valid_response_mapping(client: LunarCrushClient):
    client._apply_bulk(
        [SAMPLE_BTC, SAMPLE_ETH],
        int(datetime.now(timezone.utc).timestamp()),
        from_cache=False,
    )
    m = client.map_symbol("BTCUSDT")
    assert m.mapping_status == "MAPPED"
    assert m.provider_symbol == "BTC"
    assert m.provider_asset_id == 1

    fields = client.metrics_for_symbol("BTCUSDT")
    assert fields["social_dominance"].value == 12.5
    assert fields["social_dominance"].status == DataStatus.LIVE
    assert fields["social_volume"].value == 12345
    assert fields["mentions"].value == 12345
    assert "social_volume_24h" in (fields["mentions"].methodology or "")
    assert fields["engagement"].value == 456789
    assert fields["sentiment"].value == 67
    assert fields["social_dominance"].source == "lunarcrush"


def test_unknown_asset_unavailable(client: LunarCrushClient):
    client._apply_bulk([SAMPLE_BTC], int(datetime.now(timezone.utc).timestamp()), from_cache=False)
    fields = client.metrics_for_symbol("UNKNOWNTOKENUSDT")
    for fv in fields.values():
        assert fv.value is None
        assert fv.status == DataStatus.UNAVAILABLE
        assert "not covered" in (fv.methodology or "").lower()


def test_no_cross_asset_contamination(client: LunarCrushClient):
    client._apply_bulk(
        [SAMPLE_BTC, SAMPLE_ETH],
        int(datetime.now(timezone.utc).timestamp()),
        from_cache=False,
    )
    btc = client.metrics_for_symbol("BTCUSDT")
    eth = client.metrics_for_symbol("ETHUSDT")
    assert btc["sentiment"].value == 67
    assert eth["sentiment"].value == 55
    assert btc["social_volume"].value != eth["social_volume"].value


def test_legitimate_zero_vs_missing(client: LunarCrushClient):
    client._apply_bulk(
        [SAMPLE_ZERO, {"id": 3, "symbol": "MISS", "name": "Miss"}],
        int(datetime.now(timezone.utc).timestamp()),
        from_cache=False,
    )
    zero = client.metrics_for_symbol("ZEROCOINUSDT")
    assert zero["social_volume"].value == 0.0
    assert zero["social_volume"].status == DataStatus.LIVE
    assert zero["sentiment"].value == 0.0

    miss = client.metrics_for_symbol("MISSUSDT")
    assert miss["sentiment"].value is None
    assert miss["sentiment"].status == DataStatus.UNAVAILABLE


def test_sentiment_change_with_history(client: LunarCrushClient):
    now = datetime.now(timezone.utc)
    client._apply_bulk([SAMPLE_BTC], int(now.timestamp()), from_cache=False)
    # Seed prior observation ~24h ago
    client._sentiment_history["BTCUSDT"] = [
        (now - timedelta(hours=24), 60.0),
    ]
    # Re-apply so current is recorded after prior
    client._provider_generated_at = now
    fields = client.metrics_for_symbol("BTCUSDT")
    assert fields["sentiment_change"].value == pytest.approx(7.0)
    assert fields["sentiment_change"].status == DataStatus.LIVE


def test_sentiment_change_without_history(client: LunarCrushClient):
    client._apply_bulk(
        [SAMPLE_BTC], int(datetime.now(timezone.utc).timestamp()), from_cache=False
    )
    fields = client.metrics_for_symbol("BTCUSDT")
    assert fields["sentiment_change"].value is None
    assert fields["sentiment_change"].status == DataStatus.UNAVAILABLE


def test_stale_detection(client: LunarCrushClient):
    old = datetime.now(timezone.utc) - timedelta(seconds=2000)
    client._apply_bulk([SAMPLE_BTC], int(old.timestamp()), from_cache=False)
    client._provider_generated_at = old
    client._observed_at = old
    fields = client.metrics_for_symbol("BTCUSDT")
    assert fields["sentiment"].status == DataStatus.STALE


async def _mock_response(
    status: int, payload: Any = None, headers: dict[str, str] | None = None
) -> httpx.Response:
    req = httpx.Request("GET", "https://lunarcrush.com/api4/public/coins/list/v2")
    content = b""
    if payload is not None:
        import json

        content = json.dumps(payload).encode()
    return httpx.Response(status, content=content, headers=headers or {}, request=req)


@pytest.mark.parametrize(
    "status,plan",
    [
        (401, "AUTH_FAILED"),
        (403, "PLAN_FORBIDDEN"),
        (429, "RATE_LIMITED"),
        (500, "SERVER_ERROR"),
    ],
)
def test_http_error_codes(client: LunarCrushClient, status: int, plan: str):
    async def run():
        await client.start()
        assert client._client is not None
        headers = {"Retry-After": "1"} if status == 429 else {}
        resp = await _mock_response(status, {"error": "x"}, headers=headers)

        async def fake_get(url, params=None):  # noqa: ANN001
            return resp

        client._client.get = fake_get  # type: ignore[method-assign]
        data, code, err = await client._do_request("/public/coins/list/v2", params={"limit": 10})
        assert data is None
        if status == 500:
            assert err == plan
        else:
            assert code == status
            assert err == plan
        # Ensure failure does not fabricate zeros
        fields = client.metrics_for_symbol("BTCUSDT")
        for fv in fields.values():
            assert fv.value is None

    asyncio.run(run())


def test_timeout_does_not_fabricate(client: LunarCrushClient):
    async def run():
        await client.start()
        assert client._client is not None

        async def boom(url, params=None):  # noqa: ANN001
            raise httpx.TimeoutException("timeout")

        client._client.get = boom  # type: ignore[method-assign]
        from app.core.retry import RetryPolicy

        client._retry = RetryPolicy(max_attempts=1, backoff_base=0.01, backoff_max=0.01)
        data, _code, err = await client._do_request("/public/coins/list/v2")
        assert data is None
        assert err == "NETWORK_ERROR"
        fields = client.metrics_for_symbol("BTCUSDT")
        for fv in fields.values():
            assert fv.value is None

    asyncio.run(run())


def test_malformed_json(client: LunarCrushClient):
    async def run():
        await client.start()
        assert client._client is not None
        req = httpx.Request("GET", "https://lunarcrush.com/api4/public/coins/list/v2")
        resp = httpx.Response(200, content=b"not-json{", request=req)

        async def fake_get(url, params=None):  # noqa: ANN001
            return resp

        client._client.get = fake_get  # type: ignore[method-assign]
        data, _code, err = await client._do_request("/public/coins/list/v2")
        assert data is None
        assert err == "MALFORMED_JSON"

    asyncio.run(run())


def test_bulk_response_parsing(client: LunarCrushClient):
    async def run():
        await client.start()
        assert client._client is not None
        payload = _bulk_response([SAMPLE_BTC, SAMPLE_ETH])
        resp = await _mock_response(200, payload)

        async def fake_get(url, params=None):  # noqa: ANN001
            return resp

        client._client.get = fake_get  # type: ignore[method-assign]
        ok = await client.refresh_universe(force=True)
        assert ok
        assert client.stats.assets_received == 2
        assert client.map_symbol("ETHUSDT").mapping_status == "MAPPED"

    asyncio.run(run())


def test_diagnostic_schema(client: LunarCrushClient):
    client._apply_bulk([SAMPLE_BTC], int(datetime.now(timezone.utc).timestamp()), from_cache=False)
    client.coverage_for_universe(["BTCUSDT", "ETHUSDT", "FAKEUSDT"])
    d = client.diagnostic(universe_size=3)
    assert d["provider"] == "lunarcrush"
    assert d["configured"] is True
    assert d["api_key_present"] is True
    assert "api_key" not in d or d.get("api_key") in (None, True, False)
    assert d["assets_received"] == 1
    assert d["assets_mapped"] == 1
    assert d["assets_unmapped"] == 2
    assert d["status"] in ("LIVE", "CACHED", "HEALTHY")


def test_sentiment_provider_api_shape(client: LunarCrushClient, monkeypatch: pytest.MonkeyPatch):
    settings = client.settings
    p = SentimentProvider(settings)
    p._lc = client
    client._apply_bulk([SAMPLE_BTC], int(datetime.now(timezone.utc).timestamp()), from_cache=False)

    async def no_refresh():
        return True

    monkeypatch.setattr(client, "refresh_universe", AsyncMock(return_value=True))
    sent = asyncio.run(p.get_sentiment(["BTCUSDT"]))
    assert "social_dominance" in sent["BTCUSDT"]
    dump = {k: v.model_dump(mode="json") for k, v in sent["BTCUSDT"].items()}
    assert dump["social_dominance"]["value"] == 12.5
    assert dump["social_dominance"]["status"] == "LIVE"
    assert dump["social_dominance"]["source"] == "lunarcrush"


def test_no_response_must_not_become_zero(client: LunarCrushClient):
    # Fresh client with no bulk data
    fields = client.metrics_for_symbol("BTCUSDT")
    for fv in fields.values():
        assert fv.value is not None or fv.status in (
            DataStatus.WAITING,
            DataStatus.UNAVAILABLE,
            DataStatus.STALE,
        )
        assert fv.value != 0 or fv.status == DataStatus.LIVE  # only real zero allowed
    # Explicitly: without data, values must be None
    for fv in fields.values():
        assert fv.value is None
