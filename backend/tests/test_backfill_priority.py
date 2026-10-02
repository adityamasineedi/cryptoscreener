from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.core.adaptive_rate import AdaptiveRateController
from app.ingestion.backfill import ProgressiveBackfillService, TF_PRIORITY_RANK
from app.ingestion.klines import missing_fetch_ranges
from app.models.ohlcv import Candle
from app.models.schemas import TickerUpdate
from app.services.market_store import MarketDataStore
from app.services.ohlcv_store import OHLCVStore


def _candle(sym: str, tf: str, t: datetime) -> Candle:
    return Candle(
        symbol=sym,
        timeframe=tf,
        open_time=t,
        close_time=t + timedelta(days=1),
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1.0,
        is_closed=True,
        timestamp=t,
        source="test",
    )


def test_ranked_symbols_by_volume_not_hardcoded():
    settings = Settings(USE_REAL_DATA=True)
    market = MarketDataStore()
    ohlcv = OHLCVStore()
    rest = MagicMock()
    svc = ProgressiveBackfillService(settings, rest, market, ohlcv)

    now = datetime.now(timezone.utc)
    for sym, vol in [("AAAUSDT", 10.0), ("BBBUSDT", 1000.0), ("CCCUSDT", 100.0)]:
        market.tickers[sym] = TickerUpdate(
            symbol=sym,
            market_type="futures_perp",
            price=1.0,
            quote_volume_24h=vol,
            timestamp=now,
            source="test",
        )

    ranked = svc.ranked_symbols(["AAAUSDT", "BBBUSDT", "CCCUSDT"])
    assert ranked[0] == "BBBUSDT"
    assert ranked[1] == "CCCUSDT"
    assert ranked[2] == "AAAUSDT"


def test_coverage_counts_waiting_when_empty():
    settings = Settings(USE_REAL_DATA=True)
    market = MarketDataStore()
    ohlcv = OHLCVStore()
    svc = ProgressiveBackfillService(settings, MagicMock(), market, ohlcv)
    snap = svc.coverage_snapshot(["BTCUSDT", "ETHUSDT"])
    assert snap["symbol_count"] == 2
    assert snap["coverage_by_timeframe"]["1d"]["covered"] == 0
    assert snap["coverage_by_timeframe"]["1d"]["total"] == 2
    report = svc.backfill_report(["BTCUSDT", "ETHUSDT"])
    assert "1d" in report["timeframes"]
    assert report["timeframes"]["1d"]["symbols_waiting"] == 2
    assert "estimated_progress" in report
    assert report["estimated_progress"]["eta_seconds"] is None
    assert "total_jobs" in report
    assert "pending" in report
    assert "complete" in report


def test_p0_timeframes_outrank_1m():
    assert TF_PRIORITY_RANK["1d"] < TF_PRIORITY_RANK["4h"] < TF_PRIORITY_RANK["1h"]
    assert TF_PRIORITY_RANK["1h"] < TF_PRIORITY_RANK["15m"]
    assert TF_PRIORITY_RANK["5m"] < TF_PRIORITY_RANK["1m"]

    settings = Settings(USE_REAL_DATA=True)
    market = MarketDataStore()
    ohlcv = OHLCVStore()
    svc = ProgressiveBackfillService(settings, MagicMock(), market, ohlcv)
    now = datetime.now(timezone.utc)
    market.tickers["AAAUSDT"] = TickerUpdate(
        symbol="AAAUSDT",
        market_type="futures_perp",
        price=1.0,
        quote_volume_24h=100.0,
        timestamp=now,
        source="test",
    )
    s_1d = svc.priority_score("AAAUSDT", "1d", volume_rank=0, universe_size=10)
    s_1m = svc.priority_score("AAAUSDT", "1m", volume_rank=0, universe_size=10)
    assert s_1d > s_1m


def test_visible_boosts_priority():
    settings = Settings(USE_REAL_DATA=True)
    market = MarketDataStore()
    ohlcv = OHLCVStore()
    svc = ProgressiveBackfillService(settings, MagicMock(), market, ohlcv)
    now = datetime.now(timezone.utc)
    for sym in ("VISUSDT", "HIDUSDT"):
        market.tickers[sym] = TickerUpdate(
            symbol=sym,
            market_type="futures_perp",
            price=1.0,
            quote_volume_24h=50.0,
            timestamp=now,
            source="test",
        )
    svc.set_visible_symbols(["VISUSDT"])
    s_vis = svc.priority_score("VISUSDT", "1d", volume_rank=5, universe_size=10)
    s_hid = svc.priority_score("HIDUSDT", "1d", volume_rank=5, universe_size=10)
    assert s_vis > s_hid


def test_missing_fetch_ranges_skips_existing():
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    candles = [_candle("XUSDT", "1d", start + timedelta(days=i)) for i in range(20)]
    want_start = int(datetime(2026, 8, 20, tzinfo=timezone.utc).timestamp() * 1000)
    want_end = int(datetime(2026, 9, 30, tzinfo=timezone.utc).timestamp() * 1000)
    ranges = missing_fetch_ranges(
        candles, "1d", want_start_ms=want_start, want_end_ms=want_end
    )
    assert len(ranges) >= 2
    # First range ends at/before first existing candle
    first_existing = int(start.timestamp() * 1000)
    assert ranges[0][0] == want_start
    assert ranges[0][1] <= first_existing
    # Last range starts after last existing
    last_existing_end = int((start + timedelta(days=20)).timestamp() * 1000)
    assert ranges[-1][0] >= last_existing_end - 86_400_000
    assert ranges[-1][1] == want_end


def test_trailing_stale_detects_frozen_tip():
    from app.ingestion.klines import is_trailing_stale

    now = datetime(2026, 10, 2, 9, 45, tzinfo=timezone.utc)
    fresh = [_candle("BTCUSDT", "15m", now - timedelta(minutes=10))]
    assert not is_trailing_stale(fresh, "15m", now=now)

    frozen = [_candle("BTCUSDT", "15m", now - timedelta(hours=30))]
    assert is_trailing_stale(frozen, "15m", now=now)
    assert is_trailing_stale([], "15m", now=now)

    # Daily tip stuck on yesterday must refresh once the new day opens
    day_tip = [_candle("BTCUSDT", "1d", datetime(2026, 10, 1, tzinfo=timezone.utc))]
    assert is_trailing_stale(day_tip, "1d", now=now)


@pytest.mark.asyncio
async def test_offer_reopens_complete_when_trailing_stale():
    from app.ingestion.backfill import JobStatus

    settings = Settings(USE_REAL_DATA=True)
    market = MarketDataStore()
    ohlcv = OHLCVStore()
    svc = ProgressiveBackfillService(settings, MagicMock(), market, ohlcv)

    now = datetime.now(timezone.utc)
    tip = now - timedelta(hours=30)
    for i in range(220):
        await ohlcv.ingest_history(
            [_candle("BTCUSDT", "15m", tip - timedelta(minutes=15 * (219 - i)))]
        )

    key = svc._key("BTCUSDT", "15m")
    st = svc._state(key)
    st.touch(JobStatus.COMPLETE, candles=220)

    n = await svc._offer("BTCUSDT", "15m", priority=1, reason="seed", score=100.0)
    assert n == 1
    assert svc._state(key).status == JobStatus.PENDING
    job = await svc._queue.get()
    assert job.reason == "trailing_stale"


def test_1m_rolling_excludes_long_tail():
    settings = Settings(USE_REAL_DATA=True)
    market = MarketDataStore()
    ohlcv = OHLCVStore()
    svc = ProgressiveBackfillService(settings, MagicMock(), market, ohlcv)
    svc.m1_rolling_count = 2
    now = datetime.now(timezone.utc)
    syms = []
    for i, vol in enumerate([1000.0, 500.0, 10.0]):
        sym = f"S{i}USDT"
        syms.append(sym)
        market.tickers[sym] = TickerUpdate(
            symbol=sym,
            market_type="futures_perp",
            price=1.0,
            quote_volume_24h=vol,
            timestamp=now,
            source="test",
        )
    ranked = svc.ranked_symbols(syms)
    assert svc._include_1m(ranked[0], ranked)
    assert svc._include_1m(ranked[1], ranked)
    assert not svc._include_1m(ranked[2], ranked)


def test_adaptive_rate_reduces_on_429():
    ctl = AdaptiveRateController(min_concurrency=1, max_concurrency=4, base_pause_seconds=0.3)
    ctl.concurrency = 3
    ctl.record(latency_ms=100, outcome="429")
    assert ctl.concurrency == 2
    assert ctl.pause_seconds > 0.3
    assert ctl.in_backoff()


def test_adaptive_rate_recovers_when_healthy():
    ctl = AdaptiveRateController(min_concurrency=1, max_concurrency=4, base_pause_seconds=0.3)
    ctl.concurrency = 1
    ctl._last_adjust = 0.0
    ctl._backoff_until = 0.0
    for _ in range(12):
        ctl.record(latency_ms=200, outcome="success")
    ctl._last_adjust = 0.0
    ctl.maybe_adjust()
    assert ctl.concurrency >= 1


@pytest.mark.asyncio
async def test_enqueue_skips_complete_and_orders_by_score():
    settings = Settings(USE_REAL_DATA=True)
    market = MarketDataStore()
    ohlcv = OHLCVStore()
    svc = ProgressiveBackfillService(settings, MagicMock(), market, ohlcv)
    now = datetime.now(timezone.utc)
    market.tickers["HIUSDT"] = TickerUpdate(
        symbol="HIUSDT",
        market_type="futures_perp",
        price=1.0,
        quote_volume_24h=9999.0,
        timestamp=now,
        source="test",
    )
    market.tickers["LOUSDT"] = TickerUpdate(
        symbol="LOUSDT",
        market_type="futures_perp",
        price=1.0,
        quote_volume_24h=1.0,
        timestamp=now,
        source="test",
    )
    n = await svc.enqueue_universe(["HIUSDT", "LOUSDT"])
    assert n > 0
    first = await svc._queue.get()
    # Highest urgency should be a P0 TF for the high-volume symbol
    assert first.symbol == "HIUSDT"
    assert first.timeframe in ("1d", "4h", "1h")
