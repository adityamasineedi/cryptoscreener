"""Candle and funding feeds for the V3 paper adapter. Read-only market data.

Only closed bars leave this module: a bar is closed when open_time + interval <= now.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

import httpx

UTC = timezone.utc
PUBLIC_DATA_BASE = "https://fapi.binance.com"  # market data only; no order endpoint is ever called here
INTERVAL_DELTA = {"5m": timedelta(minutes=5), "15m": timedelta(minutes=15), "1h": timedelta(hours=1)}


def closed_only(bars: Sequence[Mapping[str, Any]], now: datetime, interval: timedelta) -> list[Mapping[str, Any]]:
    """Drop the forming bar (and anything later)."""
    return [b for b in bars if b["time"] + interval <= now]


def continuity_gaps(bars: Sequence[Mapping[str, Any]], interval: timedelta) -> list[tuple[datetime, datetime]]:
    return [(a["time"], b["time"]) for a, b in zip(bars, bars[1:]) if b["time"] - a["time"] != interval]


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


class BinanceKlineSource:
    def __init__(self, symbol: str, timeframe: str, *, base_url: str = PUBLIC_DATA_BASE,
                 client: httpx.Client | None = None) -> None:
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.interval = INTERVAL_DELTA[timeframe]
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=10.0)

    def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        """GET with backoff. Honors Retry-After on HTTP 429."""
        delay = 0.5
        resp: httpx.Response | None = None
        for _attempt in range(5):
            resp = self.client.get(f"{self.base_url}{path}", params=params)
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp
            retry_after = resp.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after is not None else delay
            except ValueError:
                wait = delay
            time.sleep(max(wait, delay))
            delay = min(delay * 2.0, 60.0)
        assert resp is not None
        resp.raise_for_status()
        return resp

    def klines(self, *, start: datetime | None = None, limit: int = 1500) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": self.symbol, "interval": self.timeframe, "limit": limit}
        if start is not None:
            params["startTime"] = _ms(start)
        resp = self._get("/fapi/v1/klines", params)
        return [
            {"time": datetime.fromtimestamp(row[0] / 1000, tz=UTC), "open": float(row[1]), "high": float(row[2]),
             "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])}
            for row in resp.json()
        ]

    def history(self, start: datetime, now: datetime) -> list[dict[str, Any]]:
        """Closed bars from ``start`` up to now, paged."""
        out: list[dict[str, Any]] = []
        cursor = start
        while cursor + self.interval <= now:
            page = self.klines(start=cursor, limit=1500)
            if not page:
                break
            out += [b for b in page if not out or b["time"] > out[-1]["time"]]
            cursor = page[-1]["time"] + self.interval
            if len(page) < 1500:
                break
        return closed_only(out, now, self.interval)

    def forming_bar(self, now: datetime) -> dict[str, Any] | None:
        page = self.klines(limit=2)
        live = [b for b in page if b["time"] <= now < b["time"] + self.interval]
        return live[-1] if live else None

    def last_price(self) -> float:
        resp = self._get("/fapi/v1/ticker/price", {"symbol": self.symbol})
        return float(resp.json()["price"])


class BinanceFundingSource:
    def __init__(self, symbol: str, *, base_url: str = PUBLIC_DATA_BASE, client: httpx.Client | None = None) -> None:
        self.symbol = symbol.upper()
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=10.0)

    def settlements(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor = start
        while cursor < end:
            resp = self.client.get(f"{self.base_url}/fapi/v1/fundingRate", params={
                "symbol": self.symbol, "startTime": _ms(cursor), "endTime": _ms(end), "limit": 1000})
            resp.raise_for_status()
            page = resp.json()
            if not page:
                break
            for row in page:
                out.append({"time": datetime.fromtimestamp(int(row["fundingTime"]) / 1000, tz=UTC),
                            "rate": float(row["fundingRate"]),
                            "mark_price": float(row["markPrice"]) if row.get("markPrice") not in (None, "") else None})
            cursor = out[-1]["time"] + timedelta(seconds=1)
            if len(page) < 1000:
                break
        return out
