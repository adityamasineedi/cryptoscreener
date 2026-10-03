"""CoinGecko historical market-cap fetch (research only).

Uses the already-configured CoinGecko base URL / rate-limit settings.
Endpoint: GET /coins/{id}/market_chart (market_caps series).

Does not fabricate. Provider failure / missing id → empty list
(caller maps to CAP_GROUP_UNAVAILABLE).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.research.config import ResearchConfig
from app.research.historical_market_cap.classifier import (
    CLASSIFICATION_RULE_VERSION,
    taxonomy_label,
)

logger = get_logger("historical_market_cap_provider")

SOURCE = "coingecko_market_chart"


class CoinGeckoHistoricalMarketCapClient:
    """Thin research client — does not mutate live fundamentals router."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        cfg = self.settings.providers_config.get("coingecko", {})
        self.base_url = str(cfg.get("base_url", "https://api.coingecko.com/api/v3")).rstrip(
            "/"
        )
        rl = cfg.get("rate_limit") or {}
        self.min_interval = float(rl.get("min_interval_seconds", 6.0))
        self._last_request = 0.0
        self._client: httpx.AsyncClient | None = None
        self.research_config = ResearchConfig()

    async def __aenter__(self) -> CoinGeckoHistoricalMarketCapClient:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers={"User-Agent": "CryptoScreener-Research/0.1"},
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)

    async def _get(self, path: str, params: dict[str, Any]) -> Any | None:
        if self._client is None:
            raise RuntimeError("Client not started")
        await self._throttle()
        url = f"{self.base_url}{path}"
        try:
            resp = await self._client.get(url, params=params)
            self._last_request = time.monotonic()
            if resp.status_code == 429:
                retry = float(resp.headers.get("Retry-After") or 60)
                logger.warning("coingecko_hist_rate_limited", retry_after=retry)
                await asyncio.sleep(retry)
                resp = await self._client.get(url, params=params)
                self._last_request = time.monotonic()
            if resp.status_code >= 400:
                logger.warning(
                    "coingecko_hist_http_error",
                    path=path,
                    status=resp.status_code,
                    body=resp.text[:200],
                )
                return None
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("coingecko_hist_request_failed", path=path, error=str(exc))
            return None

    async def fetch_markets_pages(self, pages: int = 4) -> list[dict[str, Any]]:
        """Current markets rows (for symbol→coin_id map + CURRENT_CAP only)."""
        out: list[dict[str, Any]] = []
        for page in range(1, pages + 1):
            data = await self._get(
                "/coins/markets",
                {
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": 250,
                    "page": page,
                    "sparkline": "false",
                },
            )
            if not isinstance(data, list) or not data:
                break
            out.extend(data)
            if len(data) < 250:
                break
        return out

    async def fetch_market_chart(
        self,
        coin_id: str,
        *,
        days: str = "365",
        interval: str = "daily",
    ) -> list[tuple[datetime, float]]:
        """Return (effective_time_utc, market_cap) ascending. Empty on failure."""
        data = await self._get(
            f"/coins/{coin_id}/market_chart",
            {
                "vs_currency": "usd",
                "days": days,
                "interval": interval,
            },
        )
        if not isinstance(data, dict):
            return []
        caps = data.get("market_caps") or []
        out: list[tuple[datetime, float]] = []
        for pair in caps:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            ts_ms, val = pair[0], pair[1]
            if val is None:
                continue
            try:
                mcap = float(val)
            except (TypeError, ValueError):
                continue
            if mcap <= 0:
                continue
            try:
                ts = datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                continue
            out.append((ts, mcap))
        out.sort(key=lambda x: x[0])
        return out

    def observation_rows(
        self,
        symbol: str,
        coin_id: str,
        series: list[tuple[datetime, float]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for ts, mcap in series:
            tax = taxonomy_label(symbol, mcap, config=self.research_config)
            rows.append(
                {
                    "symbol": symbol.upper(),
                    "effective_time": ts,
                    "market_cap": mcap,
                    "source": SOURCE,
                    "currency": "usd",
                    "cap_group": tax,
                    "classification_rule_version": CLASSIFICATION_RULE_VERSION,
                    "provider_coin_id": coin_id,
                }
            )
        return rows
