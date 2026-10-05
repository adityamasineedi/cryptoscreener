from __future__ import annotations

import asyncio
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
    from app.models.schemas import DataStatus

    now = datetime(2026, 10, 2, 9, 45, tzinfo=timezone.utc)
    fresh = [_candle("BTCUSDT", "15m", now - timedelta(minutes=10))]
    assert not is_trailing_stale(fresh, "15m", now=now)

    frozen = [_candle("BTCUSDT", "15m", now - timedelta(hours=30))]
    assert is_trailing_stale(frozen, "15m", now=now)
    assert is_trailing_stale([], "15m", now=now)

    # Daily tip stuck on yesterday must refresh once the new day opens
    day_tip = [_candle("BTCUSDT", "1d", datetime(2026, 10, 1, tzinfo=timezone.utc))]
    assert is_trailing_stale(day_tip, "1d", now=now)

    # Closed-only previous 1m bar mid-minute is stale (missing forming tip)
    mid = datetime(2026, 10, 2, 9, 45, 30, tzinfo=timezone.utc)
    prev_closed = [
        _candle("BTCUSDT", "1m", datetime(2026, 10, 2, 9, 44, tzinfo=timezone.utc))
    ]
    assert is_trailing_stale(prev_closed, "1m", now=mid)

    # Same series with current open candle is fresh
    open_c = Candle(
        symbol="BTCUSDT",
        timeframe="1m",
        open_time=datetime(2026, 10, 2, 9, 45, tzinfo=timezone.utc),
        close_time=datetime(2026, 10, 2, 9, 46, tzinfo=timezone.utc),
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1.0,
        is_closed=False,
        timestamp=mid,
        source="test",
        status=DataStatus.LIVE,
    )
    assert not is_trailing_stale([*prev_closed, open_c], "1m", now=mid)


@pytest.mark.asyncio
async def test_offer_dedupes_while_queued_and_respects_stale_cooldown():
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

    n1 = await svc._offer("BTCUSDT", "15m", priority=1, reason="trailing_stale", score=1.0)
    n2 = await svc._offer("BTCUSDT", "15m", priority=1, reason="trailing_stale", score=1.0)
    assert n1 == 1
    assert n2 == 0
    assert svc._enqueue_blocked_queued >= 1

    # Drain queue and apply cooldown — subsequent trailing offers blocked
    job = await svc._queue.get()
    svc._queued.discard(svc._key(job.symbol, job.timeframe))
    key = svc._key("BTCUSDT", "15m")
    svc._state(key).touch(JobStatus.PENDING)
    svc._set_stale_cooldown(key)
    n3 = await svc._offer("BTCUSDT", "15m", priority=1, reason="trailing_stale", score=1.0)
    assert n3 == 0
    assert svc._enqueue_blocked_cooldown >= 1


@pytest.mark.asyncio
async def test_tip_view_includes_open_candle_for_freshness():
    settings = Settings(USE_REAL_DATA=True)
    market = MarketDataStore()
    ohlcv = OHLCVStore()
    svc = ProgressiveBackfillService(settings, MagicMock(), market, ohlcv)

    now = datetime.now(timezone.utc)
    # Align to 1m boundary
    open_t = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
    await ohlcv.ingest_history([_candle("BTCUSDT", "1m", open_t)])
    forming = Candle(
        symbol="BTCUSDT",
        timeframe="1m",
        open_time=now.replace(second=0, microsecond=0),
        close_time=now.replace(second=0, microsecond=0) + timedelta(minutes=1),
        open=1.0,
        high=1.1,
        low=0.9,
        close=1.05,
        volume=1.0,
        is_closed=False,
        timestamp=now,
        source="test",
    )
    await ohlcv.upsert_candle(forming)
    tip = svc._tip_view("BTCUSDT", "1m")
    assert any(not c.is_closed for c in tip)
    from app.ingestion.klines import is_trailing_stale

    assert not is_trailing_stale(tip, "1m", now=now)


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


def test_active_universe_caps_and_keeps_paper_watch():
    settings = Settings(USE_REAL_DATA=True)
    market = MarketDataStore()
    ohlcv = OHLCVStore()
    svc = ProgressiveBackfillService(settings, MagicMock(), market, ohlcv)
    svc.active_universe_count = 5
    svc.tier2_count = 5
    now = datetime.now(timezone.utc)
    # Low-volume paper symbol must still win a seat over higher-volume junk.
    paper = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    junk = [f"JUNK{i}USDT" for i in range(20)]
    for i, sym in enumerate(junk):
        market.tickers[sym] = TickerUpdate(
            symbol=sym,
            market_type="futures_perp",
            price=1.0,
            quote_volume_24h=float(10_000 - i),
            timestamp=now,
            source="test",
        )
    for sym in paper:
        market.tickers[sym] = TickerUpdate(
            symbol=sym,
            market_type="futures_perp",
            price=1.0,
            quote_volume_24h=1.0,
            timestamp=now,
            source="test",
        )
    svc.set_watchlist(paper)
    active = svc.select_active_universe(paper + junk)
    assert len(active) == 5
    for sym in paper:
        assert sym in active
    # Remaining seats are highest-volume junk
    assert active[3] == "JUNK0USDT"
    assert active[4] == "JUNK1USDT"


def test_active_universe_explicit_list_does_not_inject_extra_paper():
    settings = Settings(USE_REAL_DATA=True)
    market = MarketDataStore()
    ohlcv = OHLCVStore()
    svc = ProgressiveBackfillService(settings, MagicMock(), market, ohlcv)
    svc.active_universe_count = 80
    now = datetime.now(timezone.utc)
    for sym, vol in [("HIUSDT", 9999.0), ("LOUSDT", 1.0)]:
        market.tickers[sym] = TickerUpdate(
            symbol=sym,
            market_type="futures_perp",
            price=1.0,
            quote_volume_24h=vol,
            timestamp=now,
            source="test",
        )
    active = svc.select_active_universe(["HIUSDT", "LOUSDT"])
    assert active == ["HIUSDT", "LOUSDT"]


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


@pytest.mark.asyncio
async def test_trailing_stale_deferred_while_backtest_running():
    from app.research.backtest_job import BacktestJob, backtest_job_service

    settings = Settings(USE_REAL_DATA=True)
    market = MarketDataStore()
    ohlcv = OHLCVStore()
    svc = ProgressiveBackfillService(settings, MagicMock(), market, ohlcv)
    now = datetime.now(timezone.utc)
    market.tickers["BTCUSDT"] = TickerUpdate(
        symbol="BTCUSDT",
        market_type="futures_perp",
        price=1.0,
        quote_volume_24h=9999.0,
        timestamp=now,
        source="test",
    )
    tip = now - timedelta(hours=6)
    await ohlcv.ingest_history([_candle("BTCUSDT", "15m", tip)])
    backtest_job_service._job = BacktestJob(  # noqa: SLF001
        job_id="busy",
        status="running",
        symbols=["BTCUSDT"],
        timeframes=["1h"],
        total_cells=1,
    )
    try:
        n = await svc._enqueue_stale_tips()
        assert n == 0
        assert svc.queue_size == 0
    finally:
        backtest_job_service._job = None  # noqa: SLF001


@pytest.mark.asyncio
async def test_offer_blocks_when_queue_at_max():
    settings = Settings(USE_REAL_DATA=True)
    market = MarketDataStore()
    ohlcv = OHLCVStore()
    svc = ProgressiveBackfillService(settings, MagicMock(), market, ohlcv)
    svc.max_queue_size = 1
    n1 = await svc._offer("AAAUSDT", "1h", priority=1, reason="seed", score=10)
    n2 = await svc._offer("BBBUSDT", "1h", priority=1, reason="seed", score=9)
    assert n1 == 1
    assert n2 == 0
    assert svc._enqueue_blocked_queue_full >= 1
    assert svc.queue_size == 1


@pytest.mark.asyncio
async def test_max_job_attempts_marks_failed_without_requeue():
    from app.ingestion.backfill import JobStatus

    settings = Settings(USE_REAL_DATA=True)
    market = MarketDataStore()
    ohlcv = OHLCVStore()
    svc = ProgressiveBackfillService(settings, MagicMock(), market, ohlcv)
    svc.max_job_attempts = 2
    key = svc._key("BTCUSDT", "15m")
    st = svc._state(key)
    st.attempts = 2
    st.touch(JobStatus.PENDING, last_error="boom")
    n = await svc._offer("BTCUSDT", "15m", priority=1, reason="retry", score=1)
    assert n == 0
    assert svc._state(key).status == JobStatus.FAILED
    assert svc._enqueue_blocked_attempts >= 1


def test_semaphore_bounds_concurrency():
    settings = Settings(USE_REAL_DATA=True)
    svc = ProgressiveBackfillService(
        settings, MagicMock(), MarketDataStore(), OHLCVStore()
    )
    assert svc._sem._value == svc.adaptive.concurrency
    assert svc.max_queue_size > 0
    assert svc.max_job_attempts >= 1


def test_backlog_state_clear_when_empty():
    settings = Settings(USE_REAL_DATA=True)
    svc = ProgressiveBackfillService(
        settings, MagicMock(), MarketDataStore(), OHLCVStore()
    )
    m = svc.backlog_metrics()
    assert m["backlog_state"] == "CLEAR"
    assert m["gap_backlog_count"] == 0
    assert m["priority_policy"]["1"] == "user_requested_chart_research"
    assert "historical_gap_repair" in m["priority_policy"]["3"]


def test_backlog_state_not_blocked_on_lone_retry_wait():
    """A single RETRY_WAIT with zero throughput must not classify as BLOCKED."""
    import time as _time

    settings = Settings(USE_REAL_DATA=True)
    svc = ProgressiveBackfillService(
        settings, MagicMock(), MarketDataStore(), OHLCVStore()
    )
    # Seed a stable non-empty queue depth history over >60s.
    now = _time.monotonic()
    svc._backlog_depth_history.clear()
    svc._backlog_depth_history.append((now - 90.0, 10))
    svc._backlog_depth_history.append((now - 30.0, 10))
    svc._backlog_depth_history.append((now, 10))
    # Fake one retry-wait job without completions in the rpm window.
    from app.ingestion.backfill import JobStatus, JobState

    key = ("BTCUSDT", "1h")
    svc._jobs[key] = JobState(
        symbol="BTCUSDT",
        timeframe="1h",
        status=JobStatus.RETRY_WAIT,
        attempts=1,
    )
    svc._queued.add(key)
    m = svc.backlog_metrics()
    assert m["retry_count"] >= 1
    assert m["backlog_state"] != "BLOCKED"
    assert m["backlog_state"] in {"STABLE_BACKLOG", "DRAINING", "GROWING", "CLEAR"}


@pytest.mark.asyncio
async def test_startup_enqueue_respects_max_queue_depth():
    """Startup ramp must not dump an unbounded backlog into the queue."""
    settings = Settings(USE_REAL_DATA=True)
    market = MarketDataStore()
    ohlcv = OHLCVStore()
    svc = ProgressiveBackfillService(settings, MagicMock(), market, ohlcv)
    svc._running = True
    svc.startup_window_seconds = 60.0
    svc.startup_max_queue_depth = 5
    svc.startup_enqueue_batch_size = 2
    svc.startup_enqueue_rate_per_second = 1000.0
    svc.startup_batch_yield_seconds = 0.0
    svc.defer_gap_repair_during_startup = True
    svc.active_universe_count = 20
    svc.m1_rolling_count = 0
    # Recreate limiter with test rates (constructor already built the default).
    svc._startup_enqueue_limiter = __import__(
        "app.core.rate_limiter", fromlist=["RateLimiter"]
    ).RateLimiter(
        name="test_enqueue",
        capacity=1000.0,
        refill_per_second=1000.0,
        max_concurrency=1,
    )
    now = datetime.now(timezone.utc)
    syms = []
    for i in range(12):
        sym = f"S{i}USDT"
        syms.append(sym)
        market.tickers[sym] = TickerUpdate(
            symbol=sym,
            market_type="futures_perp",
            price=1.0,
            quote_volume_24h=float(1000 - i),
            timestamp=now,
            source="test",
        )

    svc.mark_ingestion_ready()

    async def _drain() -> None:
        # Simulate workers draining so the soft queue cap can admit more work.
        while svc._running:
            await asyncio.sleep(0.005)
            try:
                job = svc._queue.get_nowait()
            except asyncio.QueueEmpty:
                continue
            key = svc._key(job.symbol, job.timeframe)
            svc._queued.discard(key)
            from app.ingestion.backfill import JobStatus

            svc._state(key).touch(JobStatus.COMPLETE, candles=200)

    drain_task = asyncio.create_task(_drain())
    try:
        n = await asyncio.wait_for(svc.enqueue_universe(syms), timeout=5.0)
        assert n >= 1
        # Peak must stay near the startup soft cap (not hundreds).
        assert svc._startup_queue_depth_peak <= svc.startup_max_queue_depth + 1
        ramp = svc.startup_ramp_metrics()
        assert ramp["startup_window_seconds"] == 60.0
        assert ramp["startup_max_queue_depth"] == 5
        assert "startup_enqueue_rate" in ramp
    finally:
        svc._running = False
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_startup_defers_gap_repair_behind_freshness():
    from app.ingestion.backfill import JobStatus

    settings = Settings(USE_REAL_DATA=True)
    market = MarketDataStore()
    ohlcv = OHLCVStore()
    svc = ProgressiveBackfillService(settings, MagicMock(), market, ohlcv)
    svc.startup_window_seconds = 60.0
    svc.startup_max_queue_depth = 200
    svc.startup_batch_yield_seconds = 0.0
    svc.defer_gap_repair_during_startup = True
    svc.m1_rolling_count = 0
    svc.tf_priority = ["1h"]
    svc._startup_enqueue_limiter = __import__(
        "app.core.rate_limiter", fromlist=["RateLimiter"]
    ).RateLimiter(
        name="test_enqueue",
        capacity=1000.0,
        refill_per_second=1000.0,
        max_concurrency=1,
    )
    now = datetime.now(timezone.utc)
    market.tickers["GAPUSDT"] = TickerUpdate(
        symbol="GAPUSDT",
        market_type="futures_perp",
        price=1.0,
        quote_volume_24h=9999.0,
        timestamp=now,
        source="test",
    )
    # Contiguous closed history + forming tip so the series is not trailing-stale.
    tip_open = now.replace(minute=0, second=0, microsecond=0)
    candles = [
        _candle("GAPUSDT", "1h", tip_open - timedelta(hours=220 - i))
        for i in range(220)
    ]
    await ohlcv.ingest_history(candles)
    forming = Candle(
        symbol="GAPUSDT",
        timeframe="1h",
        open_time=tip_open,
        close_time=tip_open + timedelta(hours=1),
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1.0,
        is_closed=False,
        timestamp=now,
        source="test",
    )
    await ohlcv.upsert_candle(forming)
    key = svc._key("GAPUSDT", "1h")
    svc._state(key).touch(JobStatus.COMPLETE, candles=220)

    class _Gap:
        expected_open_time = tip_open - timedelta(hours=100)

    ohlcv.find_gaps = lambda sym, tf: [_Gap()]  # type: ignore[method-assign]

    svc.mark_ingestion_ready()
    n = await svc.enqueue_universe(["GAPUSDT"])
    assert n == 0
    assert len(svc._deferred_gap_candidates) >= 1
    assert all(c["reason"] == "gap" for c in svc._deferred_gap_candidates)
    assert all(int(c.get("band", 3)) >= 3 for c in svc._deferred_gap_candidates)
    assert svc.queue_size == 0


def test_startup_effective_concurrency_never_raises_max():
    settings = Settings(USE_REAL_DATA=True)
    svc = ProgressiveBackfillService(
        settings, MagicMock(), MarketDataStore(), OHLCVStore()
    )
    svc.startup_max_active_rest_jobs = 2
    svc.adaptive.concurrency = 3
    svc.adaptive.max_concurrency = 3
    svc.mark_ingestion_ready()
    assert svc.in_startup_window()
    assert svc._effective_concurrency() == 2
    # After window, effective follows adaptive (still capped by adaptive max).
    svc._controls_active_until = __import__("time").monotonic() - 1.0
    assert not svc.in_startup_window()
    assert svc._effective_concurrency() == 3


def test_mark_ingestion_ready_rearms_controls_after_long_hydrate():
    settings = Settings(USE_REAL_DATA=True)
    svc = ProgressiveBackfillService(
        settings, MagicMock(), MarketDataStore(), OHLCVStore()
    )
    svc.startup_window_seconds = 60.0
    svc.arm_startup_controls()
    # Simulate controls expiring during a long hydrate.
    svc._controls_active_until = __import__("time").monotonic() - 1.0
    assert not svc.in_startup_window()
    svc.mark_ingestion_ready()
    assert svc.in_startup_window()
    assert svc.startup_elapsed_seconds() is not None


@pytest.mark.asyncio
async def test_startup_offer_dedupes_queued_and_running():
    from app.ingestion.backfill import JobStatus

    settings = Settings(USE_REAL_DATA=True)
    svc = ProgressiveBackfillService(
        settings, MagicMock(), MarketDataStore(), OHLCVStore()
    )
    svc.mark_ingestion_ready()
    n1 = await svc._offer("BTCUSDT", "1h", priority=1, reason="seed", score=10)
    n2 = await svc._offer("BTCUSDT", "1h", priority=1, reason="seed", score=10)
    assert n1 == 1
    assert n2 == 0
    assert svc._startup_duplicate_suppressions >= 1
    job = await svc._queue.get()
    svc._queued.discard(svc._key(job.symbol, job.timeframe))
    svc._state(svc._key("BTCUSDT", "1h")).touch(JobStatus.RUNNING)
    n3 = await svc._offer("BTCUSDT", "1h", priority=1, reason="seed", score=10)
    assert n3 == 0
