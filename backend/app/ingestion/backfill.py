"""Persistent, resumable OHLCV backfill — priority-scored, gap-only, adaptive rate."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.config import Settings
from app.core.adaptive_rate import AdaptiveRateController
from app.core.logging import get_logger
from app.ingestion.binance_rest import BinanceRestClient
from app.ingestion.klines import (
    BINANCE_INTERVAL,
    TIMEFRAME_MS,
    missing_fetch_ranges,
    normalize_rest_kline,
    normalize_timeframe,
)
from app.models.ohlcv import Candle
from app.services.market_store import MarketDataStore
from app.services.ohlcv_store import OHLCVStore
from app.services.redis_manager import redis_manager

logger = get_logger("backfill")


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    RETRY_WAIT = "RETRY_WAIT"


# Closed candles needed before a series is COMPLETE
TARGET_CANDLES: dict[str, int] = {
    "1d": 200,
    "4h": 180,
    "1h": 200,
    "15m": 200,
    "5m": 200,
    "1m": 200,
}

# Lower = higher priority (P0 daily/4h/1h before P1 then P2 1m)
TF_PRIORITY_RANK: dict[str, int] = {
    "1d": 0,
    "4h": 1,
    "1h": 2,
    "15m": 10,
    "5m": 11,
    "1m": 20,
}

COVERAGE_GOALS: dict[str, float | str] = {
    "1d": 95.0,
    "4h": 95.0,
    "1h": 95.0,
    "15m": 95.0,
    "5m": 95.0,
    "1m": "rolling",
    "oi": 95.0,
}


@dataclass(order=True)
class BackfillJob:
    priority: int
    symbol: str = field(compare=False)
    timeframe: str = field(compare=False)
    reason: str = field(default="seed", compare=False)
    score: float = field(default=0.0, compare=False)


@dataclass
class JobState:
    symbol: str
    timeframe: str
    status: JobStatus = JobStatus.PENDING
    candles: int = 0
    attempts: int = 0
    last_error: str | None = None
    updated_at: str | None = None
    priority_score: float = 0.0

    def touch(self, status: JobStatus, **kwargs: Any) -> None:
        self.status = status
        self.updated_at = datetime.now(timezone.utc).isoformat()
        for k, v in kwargs.items():
            setattr(self, k, v)


class ProgressiveBackfillService:
    """
    Gap-aware REST backfill with priority scoring + adaptive concurrency.

    Checkpoints persist to Redis when enabled so restarts resume without
    re-downloading completed series or deleting existing candles.
    """

    PROGRESS_KEY = "backfill:progress:v2"

    def __init__(
        self,
        settings: Settings,
        rest: BinanceRestClient,
        market: MarketDataStore,
        ohlcv: OHLCVStore,
        *,
        on_series_ready: Any | None = None,
    ) -> None:
        self.settings = settings
        self.rest = rest
        self.market = market
        self.ohlcv = ohlcv
        self.on_series_ready = on_series_ready
        cfg = settings.market_config.get("backfill") or {}
        self.tier1_count = int(cfg.get("tier1_count", 40))
        self.tier2_count = int(cfg.get("tier2_count", 150))
        self.candles_per_request = int(cfg.get("candles_per_request", 200))
        self.min_headroom = float(cfg.get("min_token_headroom", 100))
        self.gap_fill = bool(cfg.get("gap_fill_enabled", True))
        self.workers_n = int(cfg.get("workers", 2))
        # 1m rolling universe — never unlimited history for all 527
        self.m1_rolling_count = int(cfg.get("m1_rolling_universe", 60))
        self.m1_retention_candles = int(cfg.get("m1_retention_candles", 200))
        default_tfs = ["1d", "4h", "1h", "15m", "5m", "1m"]
        self.tf_priority = [
            normalize_timeframe(t)
            for t in (cfg.get("timeframes_priority") or default_tfs)
        ]
        max_c = int(cfg.get("max_concurrency", max(self.workers_n, 3)))
        min_c = int(cfg.get("min_concurrency", 1))
        self.adaptive = AdaptiveRateController(
            min_concurrency=min_c,
            max_concurrency=max_c,
            base_pause_seconds=float(cfg.get("request_pause_seconds", 0.35)),
        )
        self._sem = asyncio.Semaphore(self.adaptive.concurrency)
        self._queue: asyncio.PriorityQueue[BackfillJob] = asyncio.PriorityQueue()
        self._queued: set[tuple[str, str]] = set()
        self._jobs: dict[tuple[str, str], JobState] = {}
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._requests = 0
        self._candles_written = 0
        self._hydrate_count = 0
        self._progress_dirty = False
        self._visible: set[str] = set()
        self._watchlist: set[str] = set()
        self._completions: deque[float] = deque(maxlen=500)
        self._started_at = datetime.now(timezone.utc)

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def set_visible_symbols(self, symbols: list[str]) -> None:
        self._visible = {s.upper() for s in symbols}
        # Preferential setup recompute for visible rows (not full universe)
        try:
            from app.engines.orchestrator import get_orchestrator

            orch = get_orchestrator()
            if orch is not None:
                orch.request_setup_for_visible(list(self._visible))
        except Exception:  # noqa: BLE001
            pass

    def set_watchlist(self, symbols: list[str]) -> None:
        self._watchlist = {s.upper() for s in symbols}

    def _target(self, timeframe: str) -> int:
        tf = normalize_timeframe(timeframe)
        if tf == "1m":
            return self.m1_retention_candles
        return int(TARGET_CANDLES.get(tf, 200))

    def _key(self, symbol: str, timeframe: str) -> tuple[str, str]:
        return (symbol.upper(), normalize_timeframe(timeframe))

    def _state(self, key: tuple[str, str]) -> JobState:
        if key not in self._jobs:
            self._jobs[key] = JobState(symbol=key[0], timeframe=key[1])
        return self._jobs[key]

    async def start(self) -> None:
        if self._tasks:
            return
        self._running = True
        self._started_at = datetime.now(timezone.utc)
        await self._load_progress()
        # Reset RUNNING → PENDING after restart (candles kept)
        for st in self._jobs.values():
            if st.status == JobStatus.RUNNING:
                st.touch(JobStatus.PENDING)
        n_workers = max(self.workers_n, self.adaptive.max_concurrency)
        self._tasks = [
            asyncio.create_task(self._worker(i), name=f"ohlcv_backfill_{i}")
            for i in range(n_workers)
        ]
        self._tasks.append(
            asyncio.create_task(self._progress_loop(), name="backfill_progress")
        )

    async def stop(self) -> None:
        self._running = False
        await self._save_progress()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    def _volume(self, symbol: str) -> float:
        t = self.market.tickers.get(symbol)
        if t is None or t.quote_volume_24h is None:
            return 0.0
        return float(t.quote_volume_24h)

    def ranked_symbols(self, symbols: list[str]) -> list[str]:
        return sorted(symbols, key=self._volume, reverse=True)

    def priority_score(
        self,
        symbol: str,
        timeframe: str,
        *,
        volume_rank: int = 0,
        universe_size: int = 1,
    ) -> float:
        """
        Higher score = more urgent. Converted to ascending PriorityQueue key.

        Factors: timeframe P0/P1/P2, 24h volume, visibility, watchlist,
        missing-history severity, last successful update age.
        """
        tf = normalize_timeframe(timeframe)
        tf_rank = TF_PRIORITY_RANK.get(tf, 50)
        # Invert rank so P0 dominates: 1d/4h/1h >> 15m/5m >> 1m
        tf_component = (50 - tf_rank) * 1000.0

        n = max(universe_size, 1)
        vol_component = max(0.0, (n - volume_rank) / n) * 400.0

        vis = 250.0 if symbol.upper() in self._visible else 0.0
        watch = 120.0 if symbol.upper() in self._watchlist else 0.0

        existing = self.ohlcv.get_closed(symbol, tf)
        target = self._target(tf)
        missing_ratio = 1.0 - (min(len(existing), target) / max(target, 1))
        missing_severity = missing_ratio * 300.0
        # Prefer never-fetched series so uncovered TFs converge (not endless refreshes)
        if len(existing) == 0:
            missing_severity += 450.0
        if existing and self.gap_fill:
            gaps = self.ohlcv.find_gaps(symbol, tf)
            missing_severity += min(150.0, len(gaps) * 2.0)

        # Screener engines are 15m-keyed — pull visible 15m ahead once higher TFs have started
        if tf == "15m" and symbol.upper() in self._visible:
            vis += 180.0
        if tf == "5m" and symbol.upper() in self._visible and len(existing) == 0:
            vis += 80.0

        # Prefer colder series (no recent success)
        st = self._jobs.get(self._key(symbol, tf))
        freshness = 80.0
        if st and st.updated_at and st.status == JobStatus.COMPLETE:
            freshness = 0.0
        elif st and st.updated_at:
            try:
                age = (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(st.updated_at)
                ).total_seconds()
                freshness = min(80.0, age / 60.0)
            except ValueError:
                pass

        return (
            tf_component
            + vol_component
            + vis
            + watch
            + missing_severity
            + freshness
        )

    def _priority_key(self, score: float) -> int:
        # asyncio PriorityQueue: lower int first
        return int(10_000_000 - score * 100)

    def _include_1m(self, symbol: str, ranked: list[str]) -> bool:
        if symbol.upper() in self._visible:
            return True
        try:
            idx = ranked.index(symbol.upper())
        except ValueError:
            try:
                idx = ranked.index(symbol)
            except ValueError:
                return False
        return idx < self.m1_rolling_count

    async def hydrate_from_db(self, symbols: list[str] | None = None) -> int:
        """Load persisted OHLCV into memory and mark COMPLETE series."""
        n = await self.ohlcv.load_from_db(
            symbols=symbols, timeframes=self.tf_priority, limit_per_series=500
        )
        self._hydrate_count = n
        for (sym, tf), candles in list(self.ohlcv._closed.items()):  # noqa: SLF001
            key = self._key(sym, tf)
            st = self._state(key)
            st.candles = len(candles)
            if len(candles) >= self._target(tf) and not (
                self.gap_fill and self.ohlcv.find_gaps(sym, tf)
            ):
                st.touch(JobStatus.COMPLETE, candles=len(candles))
        self._progress_dirty = True
        logger.info(
            "backfill_hydrated",
            candles=n,
            complete=self._count_status(JobStatus.COMPLETE),
        )
        return n

    async def enqueue_universe(self, symbols: list[str]) -> int:
        ranked = self.ranked_symbols(symbols)
        rank_map = {s: i for i, s in enumerate(ranked)}
        n = 0
        for sym in ranked:
            for tf in self.tf_priority:
                if tf == "1m" and not self._include_1m(sym, ranked):
                    # Skip unlimited 1m for the long tail — do not create job state
                    continue
                score = self.priority_score(
                    sym,
                    tf,
                    volume_rank=rank_map.get(sym, len(ranked)),
                    universe_size=len(ranked),
                )
                n += await self._offer(
                    sym,
                    tf,
                    self._priority_key(score),
                    reason="seed",
                    score=score,
                )
        logger.info(
            "backfill_enqueued",
            jobs=n,
            queue=self.queue_size,
            m1_rolling=self.m1_rolling_count,
            visible=len(self._visible),
        )
        self._progress_dirty = True
        return n

    async def _offer(
        self,
        symbol: str,
        timeframe: str,
        priority: int,
        reason: str,
        *,
        score: float = 0.0,
    ) -> int:
        key = self._key(symbol, timeframe)
        st = self._state(key)
        if st.status == JobStatus.COMPLETE:
            return 0
        if key in self._queued or st.status == JobStatus.RUNNING:
            return 0
        existing = self.ohlcv.get_closed(key[0], key[1])
        st.candles = len(existing)
        st.priority_score = score
        target = self._target(key[1])
        if len(existing) >= target:
            gaps = self.ohlcv.find_gaps(key[0], key[1]) if self.gap_fill else []
            if not gaps:
                st.touch(JobStatus.COMPLETE, candles=len(existing))
                self._progress_dirty = True
                return 0
            reason = "gap"
        st.touch(JobStatus.PENDING, priority_score=score)
        self._queued.add(key)
        await self._queue.put(
            BackfillJob(
                priority=priority,
                symbol=key[0],
                timeframe=key[1],
                reason=reason,
                score=score,
            )
        )
        return 1

    def _sync_semaphore(self) -> None:
        """Resize permit count toward adaptive concurrency (best-effort)."""
        target = max(1, self.adaptive.concurrency)
        # Semaphore doesn't support resize; workers check adaptive.concurrency
        # before starting work. Keep _sem as a soft gate at max capacity.
        del target

    async def _worker(self, worker_id: int) -> None:
        while self._running:
            # Adaptive gate: only N workers process at once
            if worker_id >= self.adaptive.concurrency:
                await asyncio.sleep(0.4)
                continue
            if self.adaptive.in_backoff():
                await asyncio.sleep(0.5)
                continue
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            key = self._key(job.symbol, job.timeframe)
            self._queued.discard(key)
            st = self._state(key)

            tokens = getattr(self.rest.limiter, "tokens", None)
            if isinstance(tokens, (int, float)) and tokens < self.min_headroom:
                st.touch(JobStatus.RETRY_WAIT)
                await self._queue.put(job)
                self._queued.add(key)
                await asyncio.sleep(1.5 + worker_id * 0.2)
                continue

            st.touch(JobStatus.RUNNING)
            st.attempts += 1
            t0 = time.monotonic()
            try:
                written = await self._run_job(job)
                latency = (time.monotonic() - t0) * 1000
                self.adaptive.record(latency_ms=latency, outcome="success")
                self._candles_written += written
                count = len(self.ohlcv.get_closed(job.symbol, job.timeframe))
                st.candles = count
                gaps = (
                    self.ohlcv.find_gaps(job.symbol, job.timeframe)
                    if self.gap_fill
                    else []
                )
                if count >= self._target(job.timeframe) and not gaps:
                    st.touch(JobStatus.COMPLETE, last_error=None)
                    self._completions.append(time.monotonic())
                elif gaps and self.gap_fill:
                    st.touch(JobStatus.PENDING)
                    await self._offer(
                        job.symbol,
                        job.timeframe,
                        job.priority + 1,
                        "gap",
                        score=job.score - 10,
                    )
                elif count > 0 and written == 0:
                    # No new candles available from exchange for missing ranges
                    st.touch(JobStatus.COMPLETE, last_error=None)
                    self._completions.append(time.monotonic())
                elif count > 0:
                    st.touch(JobStatus.COMPLETE, last_error=None)
                    self._completions.append(time.monotonic())
                else:
                    st.touch(JobStatus.FAILED, last_error="empty_response")
                if self.on_series_ready and written:
                    await self.on_series_ready(job.symbol, job.timeframe)
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                latency = (time.monotonic() - t0) * 1000
                outcome = "error"
                if "429" in err:
                    outcome = "429"
                elif any(x in err for x in ("500", "502", "503", "504")):
                    outcome = "5xx"
                elif "timeout" in err.lower() or "Timeout" in err:
                    outcome = "timeout"
                self.adaptive.record(latency_ms=latency, outcome=outcome)
                st.touch(JobStatus.FAILED, last_error=err)
                logger.warning(
                    "backfill_job_failed",
                    symbol=job.symbol,
                    timeframe=job.timeframe,
                    error=err,
                    attempts=st.attempts,
                    worker=worker_id,
                )
                if st.attempts < 5:
                    st.touch(JobStatus.RETRY_WAIT)
                    delay = min(60.0, 2.0 ** min(st.attempts, 5))
                    await asyncio.sleep(delay)
                    await self._offer(
                        job.symbol,
                        job.timeframe,
                        job.priority + 5,
                        "retry",
                        score=job.score - 50,
                    )
            self._progress_dirty = True
            await asyncio.sleep(self.adaptive.pause_seconds)

    async def _run_job(self, job: BackfillJob) -> int:
        """Fetch only missing ranges; never re-download existing open_times."""
        self._requests += 1
        interval = BINANCE_INTERVAL.get(job.timeframe, job.timeframe)
        step = TIMEFRAME_MS.get(job.timeframe, 60_000)
        existing = self.ohlcv.get_closed(job.symbol, job.timeframe)
        known = {c.open_time for c in existing}
        target = self._target(job.timeframe)
        want_end = int(datetime.now(timezone.utc).timestamp() * 1000)
        want_start = want_end - step * max(target + 20, self.candles_per_request)

        ranges = missing_fetch_ranges(
            existing,
            job.timeframe,
            want_start_ms=want_start,
            want_end_ms=want_end,
            max_ranges=4,
        )
        if not ranges and not existing:
            ranges = [(want_start, want_end)]
        if not ranges and existing and self.gap_fill:
            gaps = self.ohlcv.find_gaps(job.symbol, job.timeframe)
            if gaps:
                first = gaps[0]
                start_ms = int(first.expected_open_time.timestamp() * 1000)
                ranges = [
                    (start_ms, start_ms + step * min(len(gaps), self.candles_per_request))
                ]

        written_total = 0
        for start_ms, end_ms in ranges[:3]:
            # Cap each request to exchange page size
            span = end_ms - start_ms
            max_span = step * self.candles_per_request
            if span > max_span:
                # Prefer older missing history first for performance horizons
                end_ms = start_ms + max_span

            kwargs: dict[str, Any] = {"limit": self.candles_per_request}
            if existing:
                kwargs["start_time"] = start_ms
                kwargs["end_time"] = end_ms
            # Fresh seed (no history): omit bounds so Binance returns latest N
            raw = await self.rest.futures_klines(
                job.symbol,
                interval,
                **kwargs,
            )
            candles: list[Candle] = []
            for row in raw:
                c = normalize_rest_kline(job.symbol, job.timeframe, row)
                if c and c.open_time not in known:
                    candles.append(c)
                    known.add(c.open_time)
            if candles:
                before = len(self.ohlcv.get_closed(job.symbol, job.timeframe))
                await self.ohlcv.ingest_history(candles)
                after = len(self.ohlcv.get_closed(job.symbol, job.timeframe))
                written_total += max(0, after - before)
            # One page per job cycle keeps rate polite; gaps re-queue
            break
        return written_total

    def _count_status(self, status: JobStatus) -> int:
        return sum(1 for s in self._jobs.values() if s.status == status)

    def _throughput_per_min(self) -> float:
        now = time.monotonic()
        cutoff = now - 60.0
        while self._completions and self._completions[0] < cutoff:
            self._completions.popleft()
        return float(len(self._completions))

    def backfill_report(self, symbols: list[str]) -> dict[str, Any]:
        """Queue stats + per-TF coverage + estimated_progress from throughput."""
        out: dict[str, Any] = {}
        for tf in self.tf_priority:
            complete = partial = waiting = candles = 0
            for sym in symbols:
                if tf == "1m" and not self._include_1m(sym, self.ranked_symbols(symbols)):
                    continue
                key = self._key(sym, tf)
                n = len(self.ohlcv.get_closed(sym, tf))
                candles += n
                st = self._jobs.get(key)
                if st and st.status == JobStatus.COMPLETE:
                    complete += 1
                elif n > 0:
                    partial += 1
                else:
                    waiting += 1
            total_tf = complete + partial + waiting
            out[tf] = {
                "symbols_complete": complete,
                "symbols_partial": partial,
                "symbols_waiting": waiting,
                "candles": candles,
                "queue_size": sum(1 for k in self._queued if k[1] == tf),
                "target_candles": self._target(tf),
                "universe": total_tf,
                "pct": round(100.0 * complete / total_tf, 2) if total_tf else 0.0,
            }

        total_jobs = len(self._jobs)
        pending = self._count_status(JobStatus.PENDING)
        running = self._count_status(JobStatus.RUNNING)
        complete = self._count_status(JobStatus.COMPLETE)
        failed = self._count_status(JobStatus.FAILED)
        retry_wait = self._count_status(JobStatus.RETRY_WAIT)
        # Include queued-but-not-yet-stated
        pending = max(pending, self.queue_size)

        throughput = self._throughput_per_min()
        remaining = pending + running + retry_wait
        eta_seconds: float | None = None
        if throughput > 0 and remaining > 0:
            eta_seconds = round(remaining / (throughput / 60.0), 1)

        goals = self.coverage_goals_status(symbols)
        return {
            "total_jobs": total_jobs,
            "pending": pending,
            "running": running,
            "complete": complete,
            "failed": failed,
            "retry_wait": retry_wait,
            "estimated_progress": {
                "pct_complete": round(100.0 * complete / total_jobs, 2)
                if total_jobs
                else 0.0,
                "throughput_jobs_per_min": throughput,
                "eta_seconds": eta_seconds,
                "eta_note": (
                    "derived from actual completion throughput"
                    if eta_seconds is not None
                    else "ETA withheld until throughput is measurable"
                ),
            },
            "timeframes": out,
            "queue_size": self.queue_size,
            "requests": self._requests,
            "candles_written": self._candles_written,
            "hydrated_candles": self._hydrate_count,
            "adaptive": self.adaptive.snapshot(),
            "coverage_goals": goals,
            "m1_rolling_universe": self.m1_rolling_count,
            "status_counts": {s.value: self._count_status(s) for s in JobStatus},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def coverage_goals_status(self, symbols: list[str]) -> dict[str, Any]:
        snap = self.coverage_snapshot(symbols)
        by_tf = snap.get("coverage_by_timeframe") or {}
        goals_out: dict[str, Any] = {}
        all_met = True
        for tf, goal in COVERAGE_GOALS.items():
            if tf == "oi":
                continue
            if goal == "rolling":
                ranked = self.ranked_symbols(symbols)
                rolling = [
                    s for s in ranked if self._include_1m(s, ranked)
                ]
                covered = sum(
                    1 for s in rolling if self.ohlcv.get_closed(s, "1m")
                )
                total = len(rolling) or 1
                pct = round(100.0 * covered / total, 2)
                goals_out["1m"] = {
                    "goal": "rolling",
                    "universe": total,
                    "covered": covered,
                    "pct": pct,
                    "met": covered > 0,
                }
                continue
            block = by_tf.get(tf) or {}
            pct = float(block.get("pct") or 0.0)
            met = pct >= float(goal)
            if not met:
                all_met = False
            goals_out[tf] = {
                "goal_pct": float(goal),
                "pct": pct,
                "covered": block.get("covered", 0),
                "total": block.get("total", len(symbols)),
                "met": met,
            }
        return {
            "targets": goals_out,
            "all_ohlcv_targets_met": all_met
            and all(
                goals_out.get(tf, {}).get("met")
                for tf in ("1d", "4h", "1h", "15m", "5m")
            ),
            "note": "Do not mark COMPLETE until measured targets are met",
        }

    def coverage_snapshot(self, symbols: list[str]) -> dict[str, Any]:
        tfs = self.tf_priority or ["1m", "5m", "15m", "1h", "4h", "1d"]
        by_tf: dict[str, dict[str, Any]] = {}
        oldest: datetime | None = None
        newest: datetime | None = None
        missing_ranges = 0
        for tf in tfs:
            covered = 0
            universe = symbols
            if tf == "1m":
                ranked = self.ranked_symbols(symbols)
                universe = [s for s in ranked if self._include_1m(s, ranked)]
            for sym in universe:
                candles = self.ohlcv.get_closed(sym, tf)
                if candles:
                    covered += 1
                    if oldest is None or candles[0].open_time < oldest:
                        oldest = candles[0].open_time
                    if newest is None or candles[-1].open_time > newest:
                        newest = candles[-1].open_time
                    missing_ranges += len(self.ohlcv.find_gaps(sym, tf)[:20])
            total = len(universe)
            by_tf[tf] = {
                "covered": covered,
                "total": total,
                "pct": round(100.0 * covered / total, 2) if total else 0.0,
                "rolling": tf == "1m",
            }
        return {
            "symbol_count": len(symbols),
            "coverage_by_timeframe": by_tf,
            "oldest_candle": oldest.isoformat() if oldest else None,
            "newest_candle": newest.isoformat() if newest else None,
            "missing_ranges": missing_ranges,
            "backfill_queue_size": self.queue_size,
            "completed_series": self._count_status(JobStatus.COMPLETE),
            "requests": self._requests,
            "candles_written": self._candles_written,
            "adaptive": self.adaptive.snapshot(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def status(self) -> dict[str, Any]:
        return {
            "queue_size": self.queue_size,
            "completed_series": self._count_status(JobStatus.COMPLETE),
            "pending": self._count_status(JobStatus.PENDING),
            "running": self._count_status(JobStatus.RUNNING),
            "failed": self._count_status(JobStatus.FAILED),
            "retry_wait": self._count_status(JobStatus.RETRY_WAIT),
            "requests": self._requests,
            "candles_written": self._candles_written,
            "hydrated_candles": self._hydrate_count,
            "workers": self.workers_n,
            "adaptive": self.adaptive.snapshot(),
            "tier1_count": self.tier1_count,
            "tier2_count": self.tier2_count,
            "m1_rolling_universe": self.m1_rolling_count,
        }

    async def _progress_loop(self) -> None:
        while self._running:
            await asyncio.sleep(10.0)
            if self._progress_dirty:
                await self._save_progress()
                self._progress_dirty = False

    async def _save_progress(self) -> None:
        if not redis_manager.enabled or redis_manager.client is None:
            return
        payload = {
            f"{k[0]}:{k[1]}": {
                "status": (
                    JobStatus.PENDING.value
                    if v.status == JobStatus.RUNNING
                    else v.status.value
                ),
                "candles": v.candles,
                "attempts": v.attempts,
                "last_error": v.last_error,
                "updated_at": v.updated_at,
                "priority_score": v.priority_score,
            }
            for k, v in self._jobs.items()
        }
        try:
            await redis_manager.client.set(
                self.PROGRESS_KEY, json.dumps(payload), ex=7 * 86400
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("backfill_progress_save_failed", error=str(exc))

    async def _load_progress(self) -> None:
        if not redis_manager.enabled or redis_manager.client is None:
            return
        try:
            raw = await redis_manager.client.get(self.PROGRESS_KEY)
            if not raw:
                # Legacy key (COMPLETE-only)
                raw = await redis_manager.client.get("backfill:progress")
            if not raw:
                return
            data = json.loads(raw)
            for key_s, meta in data.items():
                parts = key_s.split(":", 1)
                if len(parts) != 2:
                    continue
                key = self._key(parts[0], parts[1])
                st = self._state(key)
                status_s = meta.get("status") or JobStatus.PENDING.value
                if status_s == JobStatus.RUNNING.value:
                    status_s = JobStatus.PENDING.value
                existing = self.ohlcv.get_closed(key[0], key[1])
                if status_s == JobStatus.COMPLETE.value:
                    if len(existing) >= self._target(key[1]):
                        st.touch(
                            JobStatus.COMPLETE,
                            candles=len(existing),
                            attempts=int(meta.get("attempts") or 0),
                        )
                    else:
                        st.touch(
                            JobStatus.PENDING,
                            candles=len(existing),
                            attempts=int(meta.get("attempts") or 0),
                        )
                elif status_s == JobStatus.FAILED.value:
                    st.touch(
                        JobStatus.FAILED,
                        candles=len(existing),
                        attempts=int(meta.get("attempts") or 0),
                        last_error=meta.get("last_error"),
                    )
                else:
                    st.touch(
                        JobStatus.PENDING,
                        candles=len(existing),
                        attempts=int(meta.get("attempts") or 0),
                    )
            logger.info(
                "backfill_progress_loaded",
                complete=self._count_status(JobStatus.COMPLETE),
                pending=self._count_status(JobStatus.PENDING),
                failed=self._count_status(JobStatus.FAILED),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("backfill_progress_load_failed", error=str(exc))
