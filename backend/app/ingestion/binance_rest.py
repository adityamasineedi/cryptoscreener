from __future__ import annotations

import asyncio

import time

from typing import Any

import httpx

from app.config import Settings

from app.core.logging import get_logger

from app.core.provider_health import provider_health

from app.core.rate_limiter import rate_limiters

from app.core.request_audit import request_audit

logger = get_logger("binance_rest")

PROVIDER = "binance_rest"

class BinanceRestClient:

    """Rate-limited Binance REST client. WebSocket-first — REST for snapshots only."""

    def __init__(self, settings: Settings) -> None:

        self.settings = settings

        self._client: httpx.AsyncClient | None = None

        weight_per_min = settings.binance_rest_weight_per_minute

        self.limiter = rate_limiters.get_or_create(

            "binance_rest",

            capacity=weight_per_min,

            refill_per_second=weight_per_min / 60.0,

            max_concurrency=settings.binance_rest_max_concurrency,

            backoff_base=0.5,

            backoff_max=60.0,

        )

    async def start(self) -> None:

        self._client = httpx.AsyncClient(

            timeout=httpx.Timeout(20.0, connect=10.0),

            headers={"User-Agent": "CryptoScreener/0.1"},

        )

    async def close(self) -> None:

        if self._client is not None:

            await self._client.aclose()

            self._client = None

    async def _request(

        self,

        method: str,

        base: str,

        path: str,

        *,

        params: dict[str, Any] | None = None,

        weight: int = 1,

        max_attempts: int = 5,

    ) -> Any:

        if self._client is None:

            raise RuntimeError("BinanceRestClient not started")

        url = f"{base.rstrip('/')}{path}"

        last_exc: Exception | None = None

        retry_count = 0

        count_429 = 0

        for attempt in range(max_attempts):

            await self.limiter.acquire(weight=weight)

            start = time.monotonic()

            try:

                resp = await self._client.request(method, url, params=params)

                latency_ms = (time.monotonic() - start) * 1000

                if resp.status_code == 429:

                    count_429 += 1

                    retry_count = attempt

                    await provider_health.record_429(PROVIDER)

                    await request_audit.log(

                        provider=PROVIDER,

                        endpoint=path,

                        weight=weight,

                        status=429,

                        latency_ms=latency_ms,

                        retry_count=retry_count,

                        count_429=1,

                    )

                    delay = self.limiter.backoff_delay(attempt)

                    logger.warning(

                        "binance_rate_limited",

                        path=path,

                        attempt=attempt,

                        delay=delay,

                    )

                    await asyncio.sleep(delay)

                    continue

                if resp.status_code >= 500:

                    retry_count = attempt

                    await provider_health.record_error(PROVIDER)

                    await request_audit.log(

                        provider=PROVIDER,

                        endpoint=path,

                        weight=weight,

                        status=resp.status_code,

                        latency_ms=latency_ms,

                        retry_count=retry_count,

                        count_429=count_429,

                    )

                    delay = self.limiter.backoff_delay(attempt)

                    logger.warning(

                        "binance_server_error",

                        path=path,

                        status=resp.status_code,

                        delay=delay,

                    )

                    await asyncio.sleep(delay)

                    continue

                if resp.status_code >= 400:

                    await provider_health.record_error(PROVIDER)

                    await request_audit.log(

                        provider=PROVIDER,

                        endpoint=path,

                        weight=weight,

                        status=resp.status_code,

                        latency_ms=latency_ms,

                        retry_count=retry_count,

                        count_429=count_429,

                    )

                    resp.raise_for_status()

                await provider_health.record_success(PROVIDER, latency_ms)

                await request_audit.log(

                    provider=PROVIDER,

                    endpoint=path,

                    weight=weight,

                    status=resp.status_code,

                    latency_ms=latency_ms,

                    retry_count=retry_count,

                    count_429=count_429,

                )

                return resp.json()

            except (httpx.TimeoutException, httpx.TransportError) as exc:

                last_exc = exc

                retry_count = attempt

                latency_ms = (time.monotonic() - start) * 1000

                await provider_health.record_error(PROVIDER)

                await request_audit.log(

                    provider=PROVIDER,

                    endpoint=path,

                    weight=weight,

                    status=0,

                    latency_ms=latency_ms,

                    retry_count=retry_count,

                    count_429=count_429,

                )

                delay = self.limiter.backoff_delay(attempt)

                logger.warning(

                    "binance_transport_error",

                    path=path,

                    error=str(exc),

                    delay=delay,

                )

                await asyncio.sleep(delay)

        raise RuntimeError(f"Binance REST failed for {path}: {last_exc}")

    async def futures_exchange_info(self) -> dict[str, Any]:

        return await self._request(

            "GET",

            self.settings.binance_futures_rest,

            "/fapi/v1/exchangeInfo",

            weight=1,

        )

    async def spot_exchange_info(self) -> dict[str, Any]:

        return await self._request(

            "GET",

            self.settings.binance_spot_rest,

            "/api/v3/exchangeInfo",

            weight=20,

        )

    async def futures_open_interest(self, symbol: str) -> dict[str, Any]:

        return await self._request(

            "GET",

            self.settings.binance_futures_rest,

            "/fapi/v1/openInterest",

            params={"symbol": symbol},

            weight=1,

        )

    async def futures_open_interest_hist(

        self,

        symbol: str,

        *,

        period: str = "5m",

        limit: int = 30,

    ) -> list[Any]:

        """Open interest statistics history (weight 1)."""

        return await self._request(

            "GET",

            self.settings.binance_futures_rest,

            "/futures/data/openInterestHist",

            params={"symbol": symbol, "period": period, "limit": limit},

            weight=1,

        )

    async def futures_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[Any]:
        params: dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        return await self._request(
            "GET",
            self.settings.binance_futures_rest,
            "/fapi/v1/klines",
            params=params,
            weight=5 if limit < 100 else 10,
        )

    async def futures_premium_index(self) -> list[dict[str, Any]]:

        return await self._request(

            "GET",

            self.settings.binance_futures_rest,

            "/fapi/v1/premiumIndex",

            weight=10,

        )

    async def futures_ticker_24hr_all(self) -> list[dict[str, Any]]:

        """Single batched call for ALL futures 24h tickers (weight 40). Never per-coin."""

        return await self._request(

            "GET",

            self.settings.binance_futures_rest,

            "/fapi/v1/ticker/24hr",

            weight=40,

        )

