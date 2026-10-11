"""Settlement backfill uses recorded pages. No live network."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.grid_trading.settlements import (
    FUNDING_PAGE_LIMIT,
    FundingHttpError,
    MemorySettlementStore,
    backfill_symbol,
    parse_funding_row,
    snap_funding_time_ms,
    snap_to_candle_open_ms,
)

FIXTURE = Path(__file__).parent / "fixtures" / "grid_funding_rate_history.json"
START = datetime(2023, 11, 14, 22, 0, tzinfo=timezone.utc)
END = datetime(2023, 11, 15, 8, 0, tzinfo=timezone.utc)


def _rows() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_snap_funding_time_to_the_nearest_second_and_to_a_candle_open():
    assert snap_funding_time_ms(1_700_000_000_400) == 1_700_000_000_000
    assert snap_funding_time_ms(1_700_000_000_600) == 1_700_000_001_000
    assert snap_to_candle_open_ms(1_700_000_000_400, 1_700_000_000_000) == 1_700_000_000_000
    assert snap_to_candle_open_ms(1_700_000_003_600, 1_700_000_000_000) == 1_700_000_004_000


def test_recorded_page_parses_mark_price_and_snaps_jitter():
    first, second = [parse_funding_row(row) for row in _rows()]
    assert first["funding_time"] == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
    assert first["funding_rate"] == pytest.approx(0.0001)
    assert first["mark_price"] == pytest.approx(42000.5)
    assert second["mark_price"] is None
    assert second["funding_rate"] == pytest.approx(-0.00005)


class _Pages:
    def __init__(self, pages: list[list[dict]], failures: list[int] | None = None) -> None:
        self.pages = pages
        self.failures = list(failures or [])
        self.calls = 0

    def funding_rate_history(self, symbol, *, start_ms, end_ms, limit):
        del symbol, start_ms, end_ms
        assert limit == FUNDING_PAGE_LIMIT or limit == 2
        if self.failures:
            status = self.failures.pop(0)
            raise FundingHttpError(status)
        self.calls += 1
        return self.pages.pop(0)


def test_backfill_is_resumable_and_an_empty_fetch_is_not_never_fetched():
    store = MemorySettlementStore()
    client = _Pages([_rows()])
    first = backfill_symbol("btcusdt", START, END, client=client, store=store, limit=1000)
    assert first["rows"] == 2
    assert first["skipped"] is False
    again = backfill_symbol("BTCUSDT", START, END, client=_Pages([]), store=store, limit=1000)
    assert again["skipped"] is True
    assert again["requests"] == 0
    assert len(store.rows_between("BTCUSDT", START, END)) == 2

    empty_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    empty_end = datetime(2024, 1, 2, tzinfo=timezone.utc)
    empty = MemorySettlementStore()
    backfill_symbol("BTCUSDT", empty_start, empty_end, client=_Pages([[]]), store=empty, limit=1000)
    assert empty.fetch_covers("BTCUSDT", empty_start, empty_end) is True
    assert empty.rows_between("BTCUSDT", empty_start, empty_end) == []
    assert empty.fetch_covers("ETHUSDT", empty_start, empty_end) is False


def test_backoff_on_429_and_418_then_writes_the_recorded_page():
    slept: list[float] = []
    client = _Pages([_rows()], failures=[429, 418])
    store = MemorySettlementStore()
    result = backfill_symbol(
        "BTCUSDT",
        START,
        END,
        client=client,
        store=store,
        sleep=slept.append,
        limit=1000,
    )
    assert slept == [1.0, 2.0]
    assert result["rows"] == 2
    assert store.rows_between("BTCUSDT", START, END)[0]["funding_rate"] == pytest.approx(0.0001)


def test_pagination_continues_after_a_full_page():
    page = [
        {"symbol": "BTCUSDT", "fundingTime": 1_700_000_000_000, "fundingRate": "0.0001", "markPrice": "1"},
        {"symbol": "BTCUSDT", "fundingTime": 1_700_028_800_000, "fundingRate": "0.0002", "markPrice": "1"},
    ]
    tail = [
        {"symbol": "BTCUSDT", "fundingTime": 1_700_057_600_000, "fundingRate": "0.0003", "markPrice": "1"},
    ]
    store = MemorySettlementStore()
    client = _Pages([page, tail])
    result = backfill_symbol(
        "BTCUSDT",
        datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc),
        datetime(2023, 11, 15, 14, 13, 20, tzinfo=timezone.utc),
        client=client,
        store=store,
        limit=2,
    )
    assert result["requests"] == 2
    assert result["rows"] == 3
