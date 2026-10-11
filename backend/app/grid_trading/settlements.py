"""Binance USD-M settlement history for the grid book.

Charge mode reads these tables. It does not read funding_rates.
Nothing here runs at application startup.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

GRID_FUNDING_DDL = (
    """
    CREATE TABLE IF NOT EXISTS grid_funding_settlements (
        symbol TEXT NOT NULL,
        funding_time TIMESTAMPTZ NOT NULL,
        funding_rate DOUBLE PRECISION NOT NULL,
        mark_price DOUBLE PRECISION,
        PRIMARY KEY (symbol, funding_time)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS grid_funding_fetch_ranges (
        symbol TEXT NOT NULL,
        "from" TIMESTAMPTZ NOT NULL,
        "to" TIMESTAMPTZ NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (symbol, "from", "to")
    )
    """,
)

# GET /fapi/v1/fundingRate — published max page size. Weight is 1 on the
# MARKET_DATA listing; the OpenAPI mirror does not repeat the weight.
FUNDING_PAGE_LIMIT = 1000
_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0)


async def load_grid_settlements(symbol: str, start: Any, end: Any) -> dict[str, Any]:
    """Read grid_funding_settlements. Does not touch funding_rates.

    ``status`` is ``rows``, ``fetched_no_rows``, ``never_fetched``, or ``unavailable``
    when the database is not connected.
    """
    from sqlalchemy import text

    from app.services.database import db_manager

    if db_manager.engine is None:
        return {"status": "unavailable", "rows": []}
    sym = (symbol or "").strip().upper()
    async with db_manager.engine.begin() as conn:
        covered = (
            await conn.execute(
                text(
                    """
                    SELECT 1 FROM grid_funding_fetch_ranges
                    WHERE symbol = :symbol AND "from" <= :start AND "to" >= :end
                    LIMIT 1
                    """
                ),
                {"symbol": sym, "start": start, "end": end},
            )
        ).first()
        found = (
            await conn.execute(
                text(
                    """
                    SELECT funding_time, funding_rate, mark_price
                    FROM grid_funding_settlements
                    WHERE symbol = :symbol AND funding_time >= :start AND funding_time <= :end
                    ORDER BY funding_time
                    """
                ),
                {"symbol": sym, "start": start, "end": end},
            )
        ).fetchall()
    rows = [
        {
            "time": row[0],
            "rate": float(row[1]),
            "mark_price": None if row[2] is None else float(row[2]),
        }
        for row in found
    ]
    if rows:
        return {"status": "rows", "rows": rows}
    if covered:
        return {"status": "fetched_no_rows", "rows": []}
    return {"status": "never_fetched", "rows": []}


class FundingHttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"funding history HTTP {status}")
        self.status = status


class FundingClient(Protocol):
    def funding_rate_history(
        self,
        symbol: str,
        *,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return one page. Raise FundingHttpError(429 or 418) when limited."""


class SettlementStore(Protocol):
    def upsert(self, rows: list[dict[str, Any]]) -> None: ...

    def record_fetch(self, symbol: str, start: datetime, end: datetime, fetched_at: datetime) -> None: ...

    def fetch_covers(self, symbol: str, start: datetime, end: datetime) -> bool: ...

    def rows_between(self, symbol: str, start: datetime, end: datetime) -> list[dict[str, Any]]: ...


def snap_funding_time_ms(funding_time_ms: int) -> int:
    """Nearest whole second, as epoch milliseconds."""
    return int(round(int(funding_time_ms) / 1000.0) * 1000)


def snap_to_candle_open_ms(funding_time_ms: int, candle_open_ms: int, *, tolerance_ms: int = 2000) -> int:
    """Use the candle open when the snapped settlement is within tolerance of it."""
    snapped = snap_funding_time_ms(funding_time_ms)
    if abs(snapped - int(candle_open_ms)) <= tolerance_ms:
        return int(candle_open_ms)
    return snapped


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def parse_funding_row(raw: dict[str, Any]) -> dict[str, Any]:
    mark = raw.get("markPrice")
    mark_price = None if mark is None or mark == "" else float(mark)
    when = snap_funding_time_ms(int(raw["fundingTime"]))
    return {
        "symbol": str(raw["symbol"]).upper(),
        "funding_time": _dt(when),
        "funding_rate": float(raw["fundingRate"]),
        "mark_price": mark_price,
    }


class MemorySettlementStore:
    """Test double. A recorded fetch with no rows is not the same as never fetched."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, datetime], dict[str, Any]] = {}
        self.fetches: list[tuple[str, datetime, datetime, datetime]] = []

    def upsert(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self.rows[(row["symbol"], row["funding_time"])] = dict(row)

    def record_fetch(self, symbol: str, start: datetime, end: datetime, fetched_at: datetime) -> None:
        self.fetches.append((symbol, start, end, fetched_at))

    def fetch_covers(self, symbol: str, start: datetime, end: datetime) -> bool:
        for fetched_symbol, fetched_from, fetched_to, _at in self.fetches:
            if fetched_symbol == symbol and fetched_from <= start and fetched_to >= end:
                return True
        return False

    def rows_between(self, symbol: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        chosen = [
            row
            for (row_symbol, when), row in self.rows.items()
            if row_symbol == symbol and start <= when <= end
        ]
        chosen.sort(key=lambda row: row["funding_time"])
        return chosen


def backfill_symbol(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    client: FundingClient,
    store: SettlementStore,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] | None = None,
    limit: int = FUNDING_PAGE_LIMIT,
) -> dict[str, Any]:
    """Page /fapi/v1/fundingRate. Idempotent, and a finished empty window stays recorded."""
    symbol = symbol.upper()
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    if store.fetch_covers(symbol, start, end):
        return {"symbol": symbol, "requests": 0, "rows": 0, "skipped": True}
    clock = now or (lambda: datetime.now(timezone.utc))
    cursor = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    requests = 0
    written = 0
    while cursor <= end_ms:
        page = _page_with_backoff(
            client,
            symbol,
            start_ms=cursor,
            end_ms=end_ms,
            limit=limit,
            sleep=sleep,
        )
        requests += 1
        parsed = [parse_funding_row(row) for row in page if str(row.get("symbol", symbol)).upper() == symbol]
        store.upsert(parsed)
        written += len(parsed)
        if len(page) < limit:
            break
        last_ms = max(int(row["fundingTime"]) for row in page)
        nxt = snap_funding_time_ms(last_ms) + 1000
        if nxt <= cursor:
            break
        cursor = nxt
    store.record_fetch(symbol, start, end, clock())
    return {"symbol": symbol, "requests": requests, "rows": written, "skipped": False}


def _page_with_backoff(
    client: FundingClient,
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
    limit: int,
    sleep: Callable[[float], None],
) -> list[dict[str, Any]]:
    attempt = 0
    while True:
        try:
            return client.funding_rate_history(symbol, start_ms=start_ms, end_ms=end_ms, limit=limit)
        except FundingHttpError as exc:
            if exc.status not in (429, 418) or attempt >= len(_BACKOFF_SECONDS):
                raise
            sleep(_BACKOFF_SECONDS[attempt])
            attempt += 1
