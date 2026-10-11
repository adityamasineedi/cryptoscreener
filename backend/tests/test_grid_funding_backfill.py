"""Settlement backfill uses recorded pages. No live network."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.grid_trading.config import GridConfig
from app.grid_trading.engine import run_grid
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


def test_recorded_btcusdt_settlements_charge_the_held_inventory_only():
    path = Path(__file__).parent / "fixtures" / "btcusdt_funding_settlements_2026-08-02_2026-10-11.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    parsed = [parse_funding_row(row) for row in raw]
    assert len(parsed) == 211
    gaps = [
        (parsed[index]["funding_time"] - parsed[index - 1]["funding_time"]).total_seconds()
        for index in range(1, len(parsed))
    ]
    assert gaps
    assert min(gaps) == max(gaps) == 8 * 3600
    candles = []
    funding = []
    for index, row in enumerate(parsed):
        when = int(row["funding_time"].timestamp())
        candles.append(
            {
                "time": when,
                "open": 70_000.0,
                "high": 71_000.0,
                "low": 59_000.0 if index == 0 else 65_000.0,
                "close": 70_000.0,
            }
        )
        funding.append(
            {
                "time": when,
                "rate": row["funding_rate"],
                "mark_price": row["mark_price"],
            }
        )
    result = run_grid(
        candles,
        GridConfig(
            range_low=60_000.0,
            range_high=90_000.0,
            grid_count=1,
            capital=10_000.0,
            fee_bps=0.0,
            slippage_bps=0.0,
            stop_price=50_000.0,
            max_drawdown=0.99,
            max_open_orders=1,
        ),
        funding,
    )
    qty = 10_000.0 / 60_000.0
    expected = sum(qty * row["mark_price"] * row["funding_rate"] for row in parsed[1:])
    assert result["funding_status"] == "charged"
    assert result["funding_coverage_pct"] == pytest.approx(1.0)
    assert result["funding_paid"] == pytest.approx(expected)


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
