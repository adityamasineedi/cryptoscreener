"""The paper exit loop does not price a position off a stale or cached mark.

Pure function under test. No loop, no store, no network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.engines.orchestrator import PAPER_MARK_MAX_AGE_SECONDS, paper_tick_price
from app.models.schemas import DataStatus, MarkPriceUpdate

NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)
TICKER = SimpleNamespace(price=101.0)


def _mark(age_seconds: float, *, status: DataStatus = DataStatus.LIVE, price: float = 100.0):
    return MarkPriceUpdate(
        symbol="BTCUSDT",
        mark_price=price,
        timestamp=NOW - timedelta(seconds=age_seconds),
        status=status,
    )


def _never() -> float:
    raise AssertionError("candle fallback must not be read")


def test_bound_is_30_seconds():
    assert PAPER_MARK_MAX_AGE_SECONDS == 30.0


def test_fresh_live_mark_is_used():
    assert paper_tick_price(_mark(5), TICKER, _never, NOW) == 100.0
    assert paper_tick_price(_mark(30), TICKER, _never, NOW) == 100.0
    # No ticker at all: the fresh mark is still enough.
    assert paper_tick_price(_mark(5), None, _never, NOW) == 100.0


def test_old_live_mark_falls_through_to_the_ticker():
    assert paper_tick_price(_mark(120), TICKER, _never, NOW) == 101.0
    assert paper_tick_price(_mark(30.5), TICKER, _never, NOW) == 101.0


def test_cached_mark_is_never_used_even_when_recent():
    for status in (DataStatus.CACHED, DataStatus.STALE, DataStatus.HISTORICAL):
        assert paper_tick_price(_mark(1, status=status), TICKER, _never, NOW) == 101.0


def test_naive_mark_timestamp_is_read_as_utc():
    fresh = SimpleNamespace(
        mark_price=100.0,
        timestamp=(NOW - timedelta(seconds=5)).replace(tzinfo=None),
        status=DataStatus.LIVE,
    )
    old = SimpleNamespace(
        mark_price=100.0,
        timestamp=(NOW - timedelta(seconds=120)).replace(tzinfo=None),
        status=DataStatus.LIVE,
    )
    assert paper_tick_price(fresh, TICKER, _never, NOW) == 100.0
    assert paper_tick_price(old, TICKER, _never, NOW) == 101.0


def test_zero_or_missing_mark_price_falls_through_as_before():
    assert paper_tick_price(_mark(5, price=0.0), TICKER, _never, NOW) == 101.0
    assert paper_tick_price(None, TICKER, _never, NOW) == 101.0
    no_ts = SimpleNamespace(mark_price=100.0, timestamp=None, status=DataStatus.LIVE)
    assert paper_tick_price(no_ts, TICKER, _never, NOW) == 101.0


def test_candle_close_is_the_last_fallback():
    assert paper_tick_price(_mark(120), None, lambda: 99.5, NOW) == 99.5
    assert paper_tick_price(_mark(1, status=DataStatus.CACHED), SimpleNamespace(price=0), lambda: 99.5, NOW) == 99.5
    assert paper_tick_price(None, None, lambda: "99.5", NOW) == 99.5


def test_nothing_usable_skips_the_symbol():
    assert paper_tick_price(_mark(120), None, lambda: None, NOW) is None
    assert paper_tick_price(_mark(120), None, lambda: 0, NOW) is None
    assert paper_tick_price(None, None, None, NOW) is None


def test_default_clock_is_the_wall_clock():
    live_now = MarkPriceUpdate(
        symbol="BTCUSDT", mark_price=100.0, timestamp=datetime.now(timezone.utc)
    )
    assert paper_tick_price(live_now, TICKER, _never) == 100.0
    long_ago = MarkPriceUpdate(
        symbol="BTCUSDT", mark_price=100.0, timestamp=datetime(2020, 1, 1, tzinfo=timezone.utc)
    )
    assert paper_tick_price(long_ago, TICKER, _never) == 101.0
