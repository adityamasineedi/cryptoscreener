"""Public Binance market data for the runner. No API key and no order route."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.research.liq_flush.costs import HOUR_MS, REST_BASE
from app.research.liq_flush.execute import Bar, floor_hour

UTC = timezone.utc
Get = Callable[[str], Any]


def http_get_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "cryptoscreener-liq-flush/e34"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_closed_bars(symbol: str, origin: datetime, now: datetime, get: Get = http_get_json) -> list[Bar]:
    """Closed 1h bars with open time >= origin and close time <= now."""
    origin = floor_hour(origin)
    now = now.astimezone(UTC)
    cursor = int(origin.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    found: dict[datetime, Bar] = {}
    while cursor < end_ms:
        url = (
            f"{REST_BASE}/fapi/v1/klines?symbol={symbol}&interval=1h"
            f"&startTime={cursor}&limit=1500"
        )
        rows = get(url)
        if not rows:
            break
        for row in rows:
            bar = _kline(row)
            if bar.open_time < origin:
                continue
            if bar.open_time + timedelta(hours=1) > now:
                continue
            found.setdefault(bar.open_time, bar)
        nxt = int(rows[-1][0]) + HOUR_MS
        if nxt <= cursor:
            break
        cursor = nxt
        if len(rows) < 1500:
            break
    return [found[key] for key in sorted(found)]


def fetch_funding(
    symbol: str,
    start: datetime,
    end: datetime,
    get: Get = http_get_json,
) -> list[tuple[int, float]]:
    """Settlements with fundingTime in [start, end). Times are floored to the second."""
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    cursor = start_ms
    by: dict[int, float] = {}
    while cursor < end_ms:
        url = (
            f"{REST_BASE}/fapi/v1/fundingRate?symbol={symbol}"
            f"&startTime={cursor}&endTime={end_ms - 1}&limit=1000"
        )
        rows = get(url)
        if not rows:
            break
        for row in rows:
            ts = int(row["fundingTime"]) // 1000 * 1000
            if start_ms <= ts < end_ms:
                by[ts] = float(row["fundingRate"])
        nxt = int(rows[-1]["fundingTime"]) + 1
        if nxt <= cursor:
            break
        cursor = nxt
        if len(rows) < 1000:
            break
    return sorted(by.items())


def _kline(row: list[Any]) -> Bar:
    open_time = datetime.fromtimestamp(int(row[0]) / 1000.0, tz=UTC)
    return Bar(
        open_time=floor_hour(open_time),
        o=float(row[1]),
        h=float(row[2]),
        l=float(row[3]),
        c=float(row[4]),
    )
