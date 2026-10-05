"""Persistent, resumable OHLCV backfill — priority-scored, gap-only, adaptive rate."""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.config import Settings
from app.core.adaptive_rate import AdaptiveRateController
from app.core.logging import get_logger
from app.core.rate_limiter import RateLimiter
from app.ingestion.binance_rest import BinanceRestClient
from app.ingestion.klines import (
    BINANCE_INTERVAL,
    TIMEFRAME_MS,
    is_trailing_stale,
    missing_fetch_ranges,
    normalize_rest_kline,
    normalize_timeframe,
)
from app.models.ohlcv import Candle
from app.services.market_store import MarketDataStore
from app.services.ohlcv_store import OHLCVStore
from app.services.redis_manager import redis_manager

logger = get_logger("backfill")

# Hard cap on failed-job retries (exponential backoff applied in _worker).
MAX_JOB_ATTEMPTS = 5
# After a trailing_stale catch-up, suppress re-enqueue for this many seconds
# (also floored at half a bar for slow TFs).
STALE_COOLDOWN_SECONDS = 45.0


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
        self.tier1_count = int(cfg.get("tier1_count", 30))
        # Hard cap for backfill/engine compute (not screener discovery).
        self.tier2_count = int(cfg.get("tier2_count", 80))
        self.active_universe_count = int(
            cfg.get("active_universe_count", self.tier2_count)
        )
        self.candles_per_request = int(cfg.get("candles_per_request", 200))
        self.min_headroom = float(cfg.get("min_token_headroom", 100))
        self.gap_fill = bool(cfg.get("gap_fill_enabled", True))
        self.workers_n = int(cfg.get("workers", 2))
        # 1m rolling universe — never unlimited history for full discovery set
        self.m1_rolling_count = int(cfg.get("m1_rolling_universe", 40))
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
        # Dedup key while queued/running: (symbol, timeframe)
        self._queued: set[tuple[str, str]] = set()
        # Window dedup: (symbol, timeframe, requested_until_ms) recently served
        self._recent_windows: dict[tuple[str, str, int], float] = {}
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
        # (symbol, tf) -> monotonic time of last force_tip REST pull
        self._last_tip_fetch: dict[tuple[str, str], float] = {}
        self.tip_refresh_min_seconds = 5.0
        # Trailing-stale cooldown + metrics (presentation/scheduling only)
        self._stale_cooldown_until: dict[tuple[str, str], float] = {}
        self.stale_cooldown_seconds = STALE_COOLDOWN_SECONDS
        self.max_job_attempts = MAX_JOB_ATTEMPTS
        self._enqueue_reasons: dict[str, int] = defaultdict(int)
        self._enqueue_blocked_queued = 0
        self._enqueue_blocked_cooldown = 0
        self._enqueue_blocked_window = 0
        self._enqueue_blocked_attempts = 0
        self._enqueue_blocked_queue_full = 0
        self.max_queue_size = int(cfg.get("max_queue_size", 800))
        self._queue_depth_samples: deque[tuple[float, int]] = deque(maxlen=240)
        self._enqueue_times: deque[float] = deque(maxlen=2000)
        self._backlog_depth_history: deque[tuple[float, int]] = deque(maxlen=120)
        # Startup ramp (scheduling only — no strategy / BOS / HTF / risk impact).
        self.startup_window_seconds = float(cfg.get("startup_window_seconds", 60.0))
        self.startup_enqueue_batch_size = max(
            1, int(cfg.get("startup_enqueue_batch_size", 10))
        )
        self.startup_enqueue_rate_per_second = float(
            cfg.get("startup_enqueue_rate_per_second", 8.0)
        )
        self.startup_max_queue_depth = max(
            1, int(cfg.get("startup_max_queue_depth", 40))
        )
        self.startup_max_active_rest_jobs = max(
            1, int(cfg.get("startup_max_active_rest_jobs", 2))
        )
        # Never raise above configured adaptive max (no auto concurrency increase).
        self.startup_max_active_rest_jobs = min(
            self.startup_max_active_rest_jobs, self.adaptive.max_concurrency
        )
        self.startup_rest_refill_per_second = float(
            cfg.get("startup_rest_refill_per_second", 1.5)
        )
        self.startup_batch_yield_seconds = float(
            cfg.get("startup_batch_yield_seconds", 0.05)
        )
        self.startup_request_pause_seconds = float(
            cfg.get("startup_request_pause_seconds", 0.5)
        )
        self.defer_gap_repair_during_startup = bool(
            cfg.get("defer_gap_repair_during_startup", True)
        )
        self._ready_at_monotonic: float | None = None
        # Absolute monotonic deadline for burst controls (may be re-armed).
        self._controls_active_until: float | None = None
        self._deferred_gap_candidates: list[dict[str, Any]] = []
        self._startup_enqueue_times: deque[float] = deque(maxlen=4000)
        self._steady_enqueue_times: deque[float] = deque(maxlen=4000)
        self._startup_rest_times: deque[float] = deque(maxlen=4000)
        self._steady_rest_times: deque[float] = deque(maxlen=4000)
        self._startup_rate_limit_wait_s = 0.0
        self._steady_rate_limit_wait_s = 0.0
        self._startup_duplicate_suppressions = 0
        self._startup_cooldown_suppressions = 0
        self._steady_duplicate_suppressions = 0
        self._steady_cooldown_suppressions = 0
        self._startup_queue_depth_peak = 0
        self._startup_active_jobs_peak = 0
        enqueue_cap = max(1.0, self.startup_enqueue_rate_per_second)
        self._startup_enqueue_limiter = RateLimiter(
            name="backfill_startup_enqueue",
            capacity=max(enqueue_cap, float(self.startup_enqueue_batch_size)),
            refill_per_second=enqueue_cap,
            max_concurrency=1,
        )
        rest_cap = max(1.0, self.startup_rest_refill_per_second)
        self._startup_rest_limiter = RateLimiter(
            name="backfill_startup_rest",
            capacity=max(rest_cap, float(self.startup_max_active_rest_jobs)),
            refill_per_second=rest_cap,
            max_concurrency=self.startup_max_active_rest_jobs,
        )
        # Priority policy (documentation + metrics only — does not change strategy).
        self.backfill_priority_policy = {
            "1": "user_requested_chart_research",
            "2": "active_universe_freshness",
            "3": "historical_gap_repair",
            "4": "inactive_symbol_repair",
            "note": (
                "Historical gap repair must not starve health/screener/charts; "
                "concurrency is adaptive but never auto-raised beyond max_concurrency; "
                "startup ramp batches enqueue and bounds REST without raising workers."
            ),
        }

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def arm_startup_controls(self) -> None:
        """Ensure burst controls remain active for a full startup window from now."""
        until = time.monotonic() + self.startup_window_seconds
        if self._controls_active_until is None or until > self._controls_active_until:
            self._controls_active_until = until

    def mark_ingestion_ready(self) -> None:
        """Start the startup-ramp clock (called after hydrate, before seed enqueue)."""
        now = time.monotonic()
        if self._ready_at_monotonic is None:
            self._ready_at_monotonic = now
            logger.info(
                "backfill_ingestion_ready",
                startup_window_seconds=self.startup_window_seconds,
                startup_max_queue_depth=self.startup_max_queue_depth,
                startup_max_active_rest_jobs=self.startup_max_active_rest_jobs,
            )
        # Re-arm so a long hydrate cannot expire controls before seed enqueue.
        self.arm_startup_controls()

    def in_startup_window(self) -> bool:
        if self._controls_active_until is None:
            return False
        return time.monotonic() < self._controls_active_until

    def startup_elapsed_seconds(self) -> float | None:
        if self._ready_at_monotonic is None:
            return None
        return round(time.monotonic() - self._ready_at_monotonic, 3)

    def set_visible_symbols(self, symbols: list[str]) -> None:
        self._visible = {s.upper() for s in symbols}
        # Preferential setup recompute for visible rows (not full universe)
        try:
            from app.engines.orchestrator import get_orchestrator

            orch = get_orchestrator()
            if orch is not None:
                orch.request_setup_for_visible(list(self._visible))
                orch.set_kline_focus(list(self._visible))
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

    def _tip_view(self, symbol: str, timeframe: str) -> list[Candle]:
        """Closed bars + forming/open candle — required for freshness checks."""
        sym = symbol.upper()
        tf = normalize_timeframe(timeframe)
        tip = list(self.ohlcv.get_closed(sym, tf))
        open_c = self.ohlcv.get_open(sym, tf)
        if open_c is not None:
            tip.append(open_c)
        return tip

    def _request_until_ms(self, timeframe: str, *, now: datetime | None = None) -> int:
        """Exclusive end of the current TF interval (dedupe window key)."""
        tf = normalize_timeframe(timeframe)
        step = int(TIMEFRAME_MS.get(tf) or 60_000)
        wall = now or datetime.now(timezone.utc)
        now_ms = int(wall.timestamp() * 1000)
        current_open = (now_ms // step) * step
        return current_open + step

    def _cooldown_seconds(self, timeframe: str) -> float:
        tf = normalize_timeframe(timeframe)
        step_s = float(TIMEFRAME_MS.get(tf) or 60_000) / 1000.0
        return max(float(self.stale_cooldown_seconds), step_s * 0.5)

    def _set_stale_cooldown(self, key: tuple[str, str]) -> None:
        self._stale_cooldown_until[key] = time.monotonic() + self._cooldown_seconds(
            key[1]
        )

    def _in_stale_cooldown(self, key: tuple[str, str]) -> bool:
        until = self._stale_cooldown_until.get(key)
        if until is None:
            return False
        if time.monotonic() >= until:
            self._stale_cooldown_until.pop(key, None)
            return False
        return True

    def _record_queue_depth(self) -> None:
        self._queue_depth_samples.append((time.monotonic(), self.queue_size))

    def _state(self, key: tuple[str, str]) -> JobState:
        if key not in self._jobs:
            self._jobs[key] = JobState(symbol=key[0], timeframe=key[1])
        return self._jobs[key]

    async def start(self) -> None:
        if self._tasks:
            return
        self._running = True
        self._started_at = datetime.now(timezone.utc)
        # Arm controls immediately so pre-hydrate freshness cannot burst REST,
        # but do not start the readiness clock until hydrate completes.
        self.arm_startup_controls()
        await self._load_progress()
        # Reset RUNNING → PENDING after restart (candles kept)
        for st in self._jobs.values():
            if st.status == JobStatus.RUNNING:
                st.touch(JobStatus.PENDING)
        # Older progress files tracked full discovery (~500×TFs). Drop entries
        # outside the active/paper/visible set so status/queue math stays sane.
        pruned = self._prune_job_registry()
        if pruned:
            logger.info("backfill_progress_pruned", dropped=pruned, kept=len(self._jobs))
            self._progress_dirty = True
        n_workers = max(self.workers_n, self.adaptive.max_concurrency)
        self._tasks = [
            asyncio.create_task(self._worker(i), name=f"ohlcv_backfill_{i}")
            for i in range(n_workers)
        ]
        self._tasks.append(
            asyncio.create_task(self._progress_loop(), name="backfill_progress")
        )
        self._tasks.append(
            asyncio.create_task(self._freshness_loop(), name="backfill_freshness")
        )
        self._tasks.append(
            asyncio.create_task(
                self._deferred_gap_loop(), name="backfill_deferred_gap"
            )
        )

    def _prune_job_registry(self, keep_symbols: list[str] | None = None) -> int:
        """Keep job state only for active-universe (+ paper/visible/watch) series."""
        if keep_symbols is not None:
            keep_syms = {s.upper() for s in keep_symbols}
        else:
            keep_syms = set(self.select_active_universe())
        must = set(self._paper_must_symbols()) | set(self._visible) | set(self._watchlist)
        keep_syms |= must
        keep_tfs = set(self.tf_priority)
        drop = [
            key
            for key in list(self._jobs.keys())
            if key[0] not in keep_syms or key[1] not in keep_tfs
        ]
        for key in drop:
            self._jobs.pop(key, None)
            self._queued.discard(key)
            self._stale_cooldown_until.pop(key, None)
        return len(drop)

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

    def _paper_must_symbols(self) -> set[str]:
        """COMBO_02 v1 paper watch — always backfilled/computed."""
        try:
            from app.research.v1_production import V1_SYMBOLS

            return {s.upper() for s in V1_SYMBOLS}
        except Exception:  # noqa: BLE001
            return {"BTCUSDT", "ETHUSDT", "SOLUSDT"}

    def select_active_universe(self, symbols: list[str] | None = None) -> list[str]:
        """V1 paper + visible/watchlist + top volume, capped at active_universe_count.

        Screener discovery can stay full (~500+); this set drives REST backfill,
        engine warm, and trailing tip catch-up only. Does not change strategy logic.
        """
        explicit = symbols is not None
        if symbols is None:
            raw = list(self.market.tickers.keys()) or list(self.market.symbols.keys())
        else:
            raw = list(symbols)
        candidates = [s.upper() for s in raw if s]
        cand_set = set(candidates)

        must = set(self._paper_must_symbols())
        must |= {s.upper() for s in self._visible}
        must |= {s.upper() for s in self._watchlist}

        # Explicit caller lists stay scoped to that pool (tests / research jobs).
        # Live market selection (symbols=None) may seed paper symbols early.
        if explicit:
            must_ordered = sorted(s for s in must if s in cand_set)
        else:
            must_ordered = sorted(
                s for s in must if s in cand_set or s in self._paper_must_symbols()
            )
        if not must_ordered and not candidates:
            return []

        ranked_fill = [
            s
            for s in self.ranked_symbols(candidates)
            if s not in set(must_ordered)
        ]
        cap = max(1, int(self.active_universe_count))
        # Never drop must symbols even if must > cap.
        if len(must_ordered) >= cap:
            return list(dict.fromkeys(must_ordered))
        combined = list(dict.fromkeys([*must_ordered, *ranked_fill]))
        return combined[:cap]

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
        # Tier1 band (paper + highest volume) gets a modest urgency boost.
        if volume_rank < max(0, int(self.tier1_count)):
            vol_component += 200.0
        elif volume_rank < max(0, int(self.tier2_count)):
            vol_component += 50.0

        vis = 250.0 if symbol.upper() in self._visible else 0.0
        watch = 120.0 if symbol.upper() in self._watchlist else 0.0
        if symbol.upper() in self._paper_must_symbols():
            watch += 80.0

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
            gaps = self.gap_fill and self.ohlcv.find_gaps(sym, tf)
            tip = self._tip_view(sym, tf)
            if (
                len(candles) >= self._target(tf)
                and not gaps
                and not is_trailing_stale(tip, tf)
            ):
                st.touch(JobStatus.COMPLETE, candles=len(candles))
            else:
                # Keep PENDING so trailing catch-up / gaps still enqueue
                if st.status == JobStatus.COMPLETE:
                    st.touch(JobStatus.PENDING, candles=len(candles))
        self._progress_dirty = True
        logger.info(
            "backfill_hydrated",
            candles=n,
            complete=self._count_status(JobStatus.COMPLETE),
        )
        return n

    def _work_priority_band(self, symbol: str, reason: str) -> int:
        """Map work to priority policy bands (1=highest). Scheduling only."""
        sym = symbol.upper()
        if sym in self._visible or reason in {"user", "chart", "research"}:
            return 1
        if reason == "gap":
            # Inactive symbols are outside active universe; gap on active = band 3.
            return 3
        if sym in self._watchlist or sym in self._paper_must_symbols():
            return 2
        return 2

    def _classify_seed_work(
        self, symbol: str, timeframe: str
    ) -> tuple[str, int] | None:
        """Return (reason, band) if series needs work; None if complete/fresh."""
        key = self._key(symbol, timeframe)
        st = self._state(key)
        tip = self._tip_view(key[0], key[1])
        existing = self.ohlcv.get_closed(key[0], key[1])
        target = self._target(key[1])
        trailing = is_trailing_stale(tip, key[1])
        gaps = self.ohlcv.find_gaps(key[0], key[1]) if self.gap_fill else []

        if st.status == JobStatus.COMPLETE and not trailing:
            if gaps:
                return ("gap", 3)
            return None
        if trailing:
            return ("trailing_stale", self._work_priority_band(symbol, "trailing_stale"))
        if len(existing) < target:
            return ("seed", self._work_priority_band(symbol, "seed"))
        if gaps:
            return ("gap", 3)
        return None

    async def _await_startup_enqueue_budget(self) -> None:
        """Bound enqueue rate and yield the event loop during the startup window."""
        if not self.in_startup_window():
            await asyncio.sleep(0)
            return
        t0 = time.monotonic()
        while self.in_startup_window() and self.queue_size >= self.startup_max_queue_depth:
            if not self._running:
                break
            await asyncio.sleep(0.05)
        try:
            await self._startup_enqueue_limiter.acquire(1.0, timeout=30.0)
        except TimeoutError:
            await asyncio.sleep(self.startup_batch_yield_seconds)
        waited = time.monotonic() - t0
        if waited > 0:
            self._startup_rate_limit_wait_s += waited

    async def _await_startup_rest_budget(self) -> None:
        """Token-bucket + active-job cap for REST during startup ramp."""
        if not self.in_startup_window():
            return
        t0 = time.monotonic()
        while (
            self._running
            and self.in_startup_window()
            and self._count_status(JobStatus.RUNNING) >= self.startup_max_active_rest_jobs
        ):
            await asyncio.sleep(0.05)
        try:
            await self._startup_rest_limiter.acquire(1.0, timeout=30.0)
        except TimeoutError:
            await asyncio.sleep(self.startup_request_pause_seconds)
        waited = time.monotonic() - t0
        if waited > 0:
            self._startup_rate_limit_wait_s += waited

    async def _deferred_gap_loop(self) -> None:
        """Enqueue historical gap repair only after startup priority work."""
        while self._running:
            await asyncio.sleep(1.0)
            if not self._running:
                break
            if self.in_startup_window():
                continue
            if not self._deferred_gap_candidates:
                continue
            # Prefer draining freshness/user work before gap repair.
            if self.queue_size > max(8, self.startup_max_queue_depth // 2):
                continue
            batch = self._deferred_gap_candidates[: self.startup_enqueue_batch_size]
            self._deferred_gap_candidates = self._deferred_gap_candidates[
                self.startup_enqueue_batch_size :
            ]
            offered = 0
            for cand in batch:
                offered += await self._offer(
                    cand["symbol"],
                    cand["timeframe"],
                    cand["priority"],
                    reason="gap",
                    score=float(cand.get("score") or 0.0),
                )
                await asyncio.sleep(0)
            if offered:
                logger.info(
                    "backfill_deferred_gap_enqueued",
                    offered=offered,
                    remaining=len(self._deferred_gap_candidates),
                    queue=self.queue_size,
                )
                self._progress_dirty = True

    async def enqueue_universe(self, symbols: list[str]) -> int:
        """Batched, priority-ordered seed enqueue with startup burst controls.

        Priority: user/chart → active-universe freshness → gap repair → inactive.
        Gap repair is deferred during the startup window so health is not starved.
        """
        if self._ready_at_monotonic is None:
            self.mark_ingestion_ready()
        # Cap REST backfill to paper + top volume — not full Binance discovery.
        active = self.select_active_universe(symbols)
        active_set = set(active)
        pruned = self._prune_job_registry(keep_symbols=active)
        if pruned:
            logger.info(
                "backfill_progress_pruned",
                dropped=pruned,
                kept=len(self._jobs),
                active_universe=len(active),
            )
        ranked = self.ranked_symbols(active)
        rank_map = {s: i for i, s in enumerate(ranked)}
        candidates: list[dict[str, Any]] = []
        for sym in ranked:
            for tf in self.tf_priority:
                if tf == "1m" and not self._include_1m(sym, ranked):
                    # Skip unlimited 1m for the long tail — do not create job state
                    continue
                classified = self._classify_seed_work(sym, tf)
                if classified is None:
                    # Preserve prior COMPLETE short-circuit for full+fresh series.
                    key = self._key(sym, tf)
                    st = self._state(key)
                    existing = self.ohlcv.get_closed(key[0], key[1])
                    tip = self._tip_view(key[0], key[1])
                    if (
                        len(existing) >= self._target(tf)
                        and not is_trailing_stale(tip, tf)
                        and st.status != JobStatus.COMPLETE
                    ):
                        gaps = (
                            self.ohlcv.find_gaps(key[0], key[1])
                            if self.gap_fill
                            else []
                        )
                        if not gaps:
                            st.touch(JobStatus.COMPLETE, candles=len(existing))
                    continue
                reason, band = classified
                if sym not in active_set:
                    band = 4
                score = self.priority_score(
                    sym,
                    tf,
                    volume_rank=rank_map.get(sym, len(ranked)),
                    universe_size=len(ranked),
                )
                # Band ordering dominates score within asyncio PriorityQueue key.
                band_boost = (5 - band) * 10_000.0
                adj_score = score + band_boost
                candidates.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "reason": reason,
                        "band": band,
                        "score": adj_score,
                        "priority": self._priority_key(adj_score),
                    }
                )
        candidates.sort(key=lambda c: (int(c["band"]), -float(c["score"])))

        n = 0
        deferred = 0
        batch_i = 0
        for cand in candidates:
            if (
                self.defer_gap_repair_during_startup
                and self.in_startup_window()
                and int(cand["band"]) >= 3
            ):
                self._deferred_gap_candidates.append(cand)
                deferred += 1
                continue
            await self._await_startup_enqueue_budget()
            offered = await self._offer(
                cand["symbol"],
                cand["timeframe"],
                int(cand["priority"]),
                reason=str(cand["reason"]),
                score=float(cand["score"]),
            )
            n += offered
            batch_i += 1
            if batch_i >= self.startup_enqueue_batch_size:
                batch_i = 0
                # Cooperative yield between batches (startup and steady-state).
                await asyncio.sleep(
                    self.startup_batch_yield_seconds
                    if self.in_startup_window()
                    else 0
                )
        logger.info(
            "backfill_enqueued",
            jobs=n,
            deferred_gap=deferred,
            queue=self.queue_size,
            discovered=len(symbols),
            active_universe=len(active),
            active_cap=self.active_universe_count,
            m1_rolling=self.m1_rolling_count,
            visible=len(self._visible),
            startup_window=self.in_startup_window(),
            startup_elapsed_s=self.startup_elapsed_seconds(),
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
        tip = self._tip_view(key[0], key[1])
        existing = self.ohlcv.get_closed(key[0], key[1])
        # COMPLETE but tip frozen (WS silent) must re-enter the queue
        if st.status == JobStatus.COMPLETE and is_trailing_stale(tip, key[1]):
            st.touch(JobStatus.PENDING, candles=len(existing))
            reason = "trailing_stale"
        if st.status == JobStatus.COMPLETE:
            return 0
        if key in self._queued or st.status == JobStatus.RUNNING:
            self._enqueue_blocked_queued += 1
            if self.in_startup_window():
                self._startup_duplicate_suppressions += 1
            else:
                self._steady_duplicate_suppressions += 1
            return 0
        if st.attempts >= self.max_job_attempts and reason in (
            "retry",
            "trailing_stale",
        ):
            self._enqueue_blocked_attempts += 1
            st.touch(JobStatus.FAILED, last_error=st.last_error or "max_attempts")
            return 0
        if reason == "trailing_stale" and self._in_stale_cooldown(key):
            self._enqueue_blocked_cooldown += 1
            if self.in_startup_window():
                self._startup_cooldown_suppressions += 1
            else:
                self._steady_cooldown_suppressions += 1
            return 0

        # Window dedupe: same (symbol, tf, current-interval-until) recently worked
        until_ms = self._request_until_ms(key[1])
        window_key = (key[0], key[1], until_ms)
        win_exp = self._recent_windows.get(window_key)
        if (
            reason == "trailing_stale"
            and win_exp is not None
            and time.monotonic() < win_exp
        ):
            self._enqueue_blocked_window += 1
            if self.in_startup_window():
                self._startup_duplicate_suppressions += 1
            else:
                self._steady_duplicate_suppressions += 1
            return 0

        # Hard cap — never grow an unbounded backlog of duplicate freshness work.
        if self.queue_size >= self.max_queue_size:
            self._enqueue_blocked_queue_full += 1
            return 0
        # Soft cap during startup ramp — keeps queue from jumping to hundreds.
        if (
            self.in_startup_window()
            and self.queue_size >= self.startup_max_queue_depth
            and reason not in {"user", "chart", "research", "retry"}
        ):
            self._enqueue_blocked_queue_full += 1
            return 0

        st.candles = len(existing)
        st.priority_score = score
        target = self._target(key[1])
        if len(existing) >= target and not is_trailing_stale(tip, key[1]):
            gaps = self.ohlcv.find_gaps(key[0], key[1]) if self.gap_fill else []
            if not gaps:
                st.touch(JobStatus.COMPLETE, candles=len(existing))
                self._progress_dirty = True
                return 0
            reason = "gap"
        st.touch(JobStatus.PENDING, priority_score=score)
        self._queued.add(key)
        self._enqueue_reasons[reason] += 1
        now_m = time.monotonic()
        self._enqueue_times.append(now_m)
        if self.in_startup_window():
            self._startup_enqueue_times.append(now_m)
        else:
            self._steady_enqueue_times.append(now_m)
        await self._queue.put(
            BackfillJob(
                priority=priority,
                symbol=key[0],
                timeframe=key[1],
                reason=reason,
                score=score,
            )
        )
        self._record_queue_depth()
        if self.in_startup_window():
            self._startup_queue_depth_peak = max(
                self._startup_queue_depth_peak, self.queue_size
            )
        return 1

    async def ensure_series_fresh(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 250,
        force_tip: bool = False,
    ) -> int:
        """On-demand REST tail catch-up for chart / visible symbols.

        Used when the kline websocket is silent so charts do not freeze on a
        hydrated tip from hours ago.

        force_tip=True (chart / setup polls): refresh the latest few bars /
        forming candle so OHLC stays live even when the tip is not "stale".
        Throttled to tip_refresh_min_seconds to protect REST weight budget.
        """
        sym = symbol.upper()
        tf = normalize_timeframe(timeframe)
        existing = self.ohlcv.get_closed(sym, tf)
        open_c = self.ohlcv.get_open(sym, tf)
        tip_view = list(existing)
        if open_c is not None:
            tip_view.append(open_c)
        stale = is_trailing_stale(tip_view, tf)
        if tip_view and not stale and not force_tip:
            return 0

        tip_key = (sym, tf)
        now_m = time.monotonic()
        if force_tip and not stale:
            last = self._last_tip_fetch.get(tip_key, 0.0)
            if now_m - last < self.tip_refresh_min_seconds:
                return 0

        # Chart/setup polls: cheap tip-only pull when history is already caught up
        fetch_limit = 5 if force_tip and tip_view and not stale else limit
        written = await self._fetch_rest_tail(sym, tf, limit=fetch_limit)
        self._last_tip_fetch[tip_key] = time.monotonic()
        if written and self.on_series_ready:
            try:
                await self.on_series_ready(sym, tf)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ensure_fresh_ready_failed",
                    symbol=sym,
                    timeframe=tf,
                    error=str(exc),
                )
        candles = self.ohlcv.get_closed(sym, tf)
        open_now = self.ohlcv.get_open(sym, tf)
        tip_now = list(candles)
        if open_now is not None:
            tip_now.append(open_now)
        st = self._state(self._key(sym, tf))
        st.candles = len(candles)
        if tip_now and not is_trailing_stale(tip_now, tf):
            gaps = self.ohlcv.find_gaps(sym, tf) if self.gap_fill else []
            if len(candles) >= self._target(tf) and not gaps:
                st.touch(JobStatus.COMPLETE, last_error=None)
            else:
                st.touch(JobStatus.PENDING)
        else:
            st.touch(JobStatus.PENDING)
        self._progress_dirty = True
        return written

    async def ensure_setup_mtf_fresh(
        self,
        symbol: str,
        timeframes: list[str],
        *,
        force_setup_tip: bool = True,
        setup_tf: str | None = None,
    ) -> int:
        """Refresh strategy MTF series before signal compute (WS-silent safe)."""
        sym = symbol.upper()
        setup = normalize_timeframe(setup_tf) if setup_tf else None
        written = 0
        for raw_tf in timeframes:
            tf = normalize_timeframe(raw_tf)
            force = bool(force_setup_tip and setup and tf == setup)
            written += await self.ensure_series_fresh(
                sym, tf, limit=120, force_tip=force
            )
        return written

    async def _fetch_rest_tail(
        self, symbol: str, timeframe: str, *, limit: int = 250
    ) -> int:
        """Fetch missing tip candles via REST and merge into the store."""
        self._requests += 1
        # Chart/user tip path: count for metrics but do not apply startup REST
        # token wait — priority-1 freshness must not be starved by the ramp.
        now_m = time.monotonic()
        if self.in_startup_window():
            self._startup_rest_times.append(now_m)
        else:
            self._steady_rest_times.append(now_m)
        interval = BINANCE_INTERVAL.get(timeframe, timeframe)
        step = TIMEFRAME_MS.get(timeframe, 60_000)
        existing = self.ohlcv.get_closed(symbol, timeframe)
        known = {c.open_time for c in existing}
        want_end = int(datetime.now(timezone.utc).timestamp() * 1000)
        page = max(50, min(int(limit), self.candles_per_request))

        ranges = missing_fetch_ranges(
            existing,
            timeframe,
            want_start_ms=(
                int(existing[-1].open_time.timestamp() * 1000) + step
                if existing
                else want_end - step * page
            ),
            want_end_ms=want_end,
            max_ranges=2,
        )
        kwargs: dict[str, Any] = {"limit": page}
        if ranges:
            start_ms, end_ms = ranges[-1]
            # Prefer the trailing window so the chart tip reaches "now"
            kwargs["start_time"] = start_ms
            kwargs["end_time"] = end_ms
        elif existing:
            # Tip looks contiguous enough for missing_fetch_ranges to drop the
            # tiny residual — still pull latest page as a safety net.
            kwargs["start_time"] = int(existing[-1].open_time.timestamp() * 1000)
        # else: no history → omit bounds so Binance returns latest N

        raw = await self.rest.futures_klines(symbol, interval, **kwargs)
        closed: list[Candle] = []
        open_c: Candle | None = None
        for row in raw:
            c = normalize_rest_kline(symbol, timeframe, row)
            if c is None:
                continue
            if not c.is_closed:
                open_c = c
                continue
            if c.open_time not in known:
                closed.append(c)
                known.add(c.open_time)
            else:
                # Replace matching tip with fresher REST bar
                closed.append(c)

        written = 0
        if closed:
            # Prefer replace-aware ingest so overlapping tip bars update OHLC
            unique_by_time: dict[Any, Candle] = {c.open_time: c for c in closed}
            batch = list(unique_by_time.values())
            before_times = {c.open_time for c in self.ohlcv.get_closed(symbol, timeframe)}
            await self.ohlcv.ingest_history(batch)
            after = self.ohlcv.get_closed(symbol, timeframe)
            written = sum(1 for c in after if c.open_time not in before_times)
            # Count replacements of the tip as progress too
            if written == 0 and batch:
                written = len(batch)
            self._candles_written += written
        if open_c is not None:
            await self.ohlcv.upsert_candle(open_c)
        logger.info(
            "rest_tail_catchup",
            symbol=symbol,
            timeframe=timeframe,
            written=written,
            open=open_c is not None,
            last=(
                self.ohlcv.get_closed(symbol, timeframe)[-1].open_time.isoformat()
                if self.ohlcv.get_closed(symbol, timeframe)
                else None
            ),
        )
        return written

    async def _freshness_loop(self) -> None:
        """Periodically reopen COMPLETE series whose tip has gone stale."""
        while self._running:
            await asyncio.sleep(45.0)
            if not self._running:
                break
            try:
                await self._enqueue_stale_tips()
            except Exception as exc:  # noqa: BLE001
                logger.warning("freshness_loop_failed", error=str(exc))

    async def _enqueue_stale_tips(self) -> int:
        # Defer trailing REST work while a UI backtest owns the event loop.
        try:
            from app.research.backtest_job import backtest_job_service

            if backtest_job_service.is_running():
                logger.info(
                    "trailing_stale_deferred_for_backtest",
                    queue=self.queue_size,
                )
                return 0
        except Exception:  # noqa: BLE001
            pass
        active = self.select_active_universe()
        ranked = self.ranked_symbols(active)
        if not ranked and self._visible:
            ranked = sorted(self._visible)
        # Prefer visible / watchlist / paper, then top volume within active set
        prefer = list(
            dict.fromkeys(
                [*sorted(self._visible), *sorted(self._watchlist), *ranked]
            )
        )
        n = 0
        # Cap work per tick within the active universe
        for sym in prefer[: max(20, min(len(prefer), self.active_universe_count))]:
            for tf in self.tf_priority:
                if tf == "1m" and not self._include_1m(sym, ranked or prefer):
                    continue
                tip = self._tip_view(sym, tf)
                if not tip:
                    continue
                if not is_trailing_stale(tip, tf):
                    continue
                key = self._key(sym, tf)
                if self._in_stale_cooldown(key):
                    self._enqueue_blocked_cooldown += 1
                    continue
                score = self.priority_score(
                    sym,
                    tf,
                    volume_rank=ranked.index(sym) if sym in ranked else len(prefer),
                    universe_size=max(len(ranked), 1),
                )
                # Visible chart symbols jump the queue
                if sym in self._visible:
                    score += 500.0
                n += await self._offer(
                    sym,
                    tf,
                    self._priority_key(score),
                    "trailing_stale",
                    score=score,
                )
        if n:
            logger.info(
                "enqueued_trailing_stale",
                jobs=n,
                queue=self.queue_size,
                blocked_cooldown=self._enqueue_blocked_cooldown,
                blocked_window=self._enqueue_blocked_window,
            )
            self._progress_dirty = True
        self._record_queue_depth()
        return n

    def _sync_semaphore(self) -> None:
        """Resize permit count toward adaptive concurrency (best-effort)."""
        target = max(1, self.adaptive.concurrency)
        # Semaphore doesn't support resize; workers check adaptive.concurrency
        # before starting work. Keep _sem as a soft gate at max capacity.
        del target

    def _effective_concurrency(self) -> int:
        """Startup ramp may lower concurrency but never raises the configured max."""
        base = max(1, int(self.adaptive.concurrency))
        if self.in_startup_window():
            return max(1, min(base, self.startup_max_active_rest_jobs))
        return base

    async def _worker(self, worker_id: int) -> None:
        while self._running:
            # Adaptive gate: only N workers process at once (startup-capped).
            if worker_id >= self._effective_concurrency():
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

            # User/chart work bypasses startup REST token wait; still respects
            # active-job cap via effective concurrency above.
            if job.reason not in {"user", "chart", "research"}:
                await self._await_startup_rest_budget()

            st.touch(JobStatus.RUNNING)
            if self.in_startup_window():
                self._startup_active_jobs_peak = max(
                    self._startup_active_jobs_peak,
                    self._count_status(JobStatus.RUNNING),
                )
            st.attempts += 1
            t0 = time.monotonic()
            try:
                async with self._sem:
                    written = await self._run_job(job)
                latency = (time.monotonic() - t0) * 1000
                # During startup, do not let adaptive controller raise concurrency.
                if self.in_startup_window():
                    prev_max = self.adaptive.max_concurrency
                    self.adaptive.max_concurrency = min(
                        prev_max, self.startup_max_active_rest_jobs
                    )
                    try:
                        self.adaptive.record(latency_ms=latency, outcome="success")
                    finally:
                        self.adaptive.max_concurrency = prev_max
                        self.adaptive.concurrency = min(
                            self.adaptive.concurrency,
                            self.startup_max_active_rest_jobs,
                        )
                else:
                    self.adaptive.record(latency_ms=latency, outcome="success")
                self._candles_written += written
                count = len(self.ohlcv.get_closed(job.symbol, job.timeframe))
                st.candles = count
                gaps = (
                    self.ohlcv.find_gaps(job.symbol, job.timeframe)
                    if self.gap_fill
                    else []
                )
                tip = self._tip_view(job.symbol, job.timeframe)
                tip_stale = is_trailing_stale(tip, job.timeframe)
                until_ms = self._request_until_ms(job.timeframe)
                window_key = (key[0], key[1], until_ms)
                # Mark this interval window as recently served (dedupe)
                self._recent_windows[window_key] = (
                    time.monotonic() + self._cooldown_seconds(job.timeframe)
                )
                if count >= self._target(job.timeframe) and not gaps and not tip_stale:
                    st.touch(JobStatus.COMPLETE, last_error=None)
                    self._completions.append(time.monotonic())
                    if job.reason == "trailing_stale":
                        self._set_stale_cooldown(key)
                elif gaps and self.gap_fill:
                    st.touch(JobStatus.PENDING)
                    await self._offer(
                        job.symbol,
                        job.timeframe,
                        job.priority + 1,
                        "gap",
                        score=job.score - 10,
                    )
                elif tip_stale:
                    # Tip still behind after a successful REST cycle — cool down
                    # so freshness loop cannot thrash the same series. Do not
                    # immediate re-offer (cooldown would no-op anyway).
                    st.touch(JobStatus.PENDING)
                    self._set_stale_cooldown(key)
                elif count > 0 and written == 0:
                    # No new candles available from exchange for missing ranges
                    st.touch(JobStatus.COMPLETE, last_error=None)
                    self._completions.append(time.monotonic())
                    if job.reason == "trailing_stale":
                        self._set_stale_cooldown(key)
                elif count > 0:
                    st.touch(JobStatus.COMPLETE, last_error=None)
                    self._completions.append(time.monotonic())
                    if job.reason == "trailing_stale":
                        self._set_stale_cooldown(key)
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
                if self.in_startup_window():
                    prev_max = self.adaptive.max_concurrency
                    self.adaptive.max_concurrency = min(
                        prev_max, self.startup_max_active_rest_jobs
                    )
                    try:
                        self.adaptive.record(latency_ms=latency, outcome=outcome)
                    finally:
                        self.adaptive.max_concurrency = prev_max
                        self.adaptive.concurrency = min(
                            self.adaptive.concurrency,
                            self.startup_max_active_rest_jobs,
                        )
                else:
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
                if st.attempts < self.max_job_attempts:
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
                else:
                    self._set_stale_cooldown(key)
            self._progress_dirty = True
            self._record_queue_depth()
            pause = float(self.adaptive.pause_seconds)
            if self.in_startup_window():
                pause = max(pause, self.startup_request_pause_seconds)
            await asyncio.sleep(pause)

    async def _run_job(self, job: BackfillJob) -> int:
        """Fetch only missing ranges; never re-download existing open_times."""
        self._requests += 1
        now_m = time.monotonic()
        if self.in_startup_window():
            self._startup_rest_times.append(now_m)
        else:
            self._steady_rest_times.append(now_m)
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
            candles_closed: list[Candle] = []
            for row in raw:
                c = normalize_rest_kline(job.symbol, job.timeframe, row)
                if c is None:
                    continue
                if not c.is_closed:
                    await self.ohlcv.upsert_candle(c)
                    continue
                if c.open_time not in known:
                    candles_closed.append(c)
                    known.add(c.open_time)
            if candles_closed:
                before = len(self.ohlcv.get_closed(job.symbol, job.timeframe))
                await self.ohlcv.ingest_history(candles_closed)
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

    def _enqueue_rate_per_min(self) -> float:
        now = time.monotonic()
        cutoff = now - 60.0
        while self._enqueue_times and self._enqueue_times[0] < cutoff:
            self._enqueue_times.popleft()
        return float(len(self._enqueue_times))

    @staticmethod
    def _rate_in_window(times: deque[float], window_s: float) -> float:
        if not times or window_s <= 0:
            return 0.0
        now = time.monotonic()
        cutoff = now - window_s
        n = sum(1 for t in times if t >= cutoff)
        return round(n / (window_s / 60.0), 2)

    def startup_ramp_metrics(self) -> dict[str, Any]:
        """Startup vs steady-state scheduling metrics (observability only)."""
        elapsed = self.startup_elapsed_seconds()
        in_startup = self.in_startup_window()
        startup_window = float(self.startup_window_seconds)
        # Rates over the configured startup window (or elapsed if shorter).
        startup_span = startup_window
        if self._ready_at_monotonic is not None:
            startup_span = min(
                startup_window,
                max(0.001, time.monotonic() - self._ready_at_monotonic),
            )
        if in_startup:
            startup_enqueue_rate = self._rate_in_window(
                self._startup_enqueue_times, max(startup_span, 1.0)
            )
            startup_rest_rate = self._rate_in_window(
                self._startup_rest_times, max(startup_span, 1.0)
            )
        else:
            # Freeze rates against the completed startup window samples.
            startup_enqueue_rate = round(
                (len(self._startup_enqueue_times) / max(startup_window, 1.0)) * 60.0,
                2,
            )
            startup_rest_rate = round(
                (len(self._startup_rest_times) / max(startup_window, 1.0)) * 60.0,
                2,
            )
        steady_enqueue_rate = self._rate_in_window(self._steady_enqueue_times, 60.0)
        steady_rest_rate = self._rate_in_window(self._steady_rest_times, 60.0)
        loop_lag: dict[str, Any] = {}
        try:
            from app.services.performance import performance_monitor

            loop_lag = performance_monitor.event_loop.snapshot()
        except Exception:  # noqa: BLE001
            loop_lag = {}
        return {
            "startup_window_seconds": startup_window,
            "startup_elapsed_seconds": elapsed,
            "in_startup_window": in_startup,
            "startup_enqueue_rate": startup_enqueue_rate,
            "startup_rest_request_rate": startup_rest_rate,
            "startup_queue_depth": self.queue_size if in_startup else None,
            "startup_queue_depth_peak": self._startup_queue_depth_peak,
            "startup_active_jobs": (
                self._count_status(JobStatus.RUNNING) if in_startup else None
            ),
            "startup_active_jobs_peak": self._startup_active_jobs_peak,
            "startup_duplicate_suppressions": self._startup_duplicate_suppressions,
            "startup_cooldown_suppressions": self._startup_cooldown_suppressions,
            "startup_rate_limit_wait": round(self._startup_rate_limit_wait_s, 3),
            "startup_event_loop_lag": {
                "last_ms": loop_lag.get("event_loop_lag_ms"),
                "p50_ms": loop_lag.get("event_loop_lag_p50_ms"),
                "p95_ms": loop_lag.get("event_loop_lag_p95_ms"),
                "max_ms": loop_lag.get("event_loop_lag_max_ms"),
            },
            "startup_health_latency": None,  # filled by external probe harness
            "steady_state_enqueue_rate": steady_enqueue_rate,
            "steady_state_rest_request_rate": steady_rest_rate,
            "steady_state_health_latency": None,  # filled by external probe harness
            "steady_duplicate_suppressions": self._steady_duplicate_suppressions,
            "steady_cooldown_suppressions": self._steady_cooldown_suppressions,
            "steady_rate_limit_wait": round(self._steady_rate_limit_wait_s, 3),
            "deferred_gap_candidates": len(self._deferred_gap_candidates),
            "startup_enqueue_batch_size": self.startup_enqueue_batch_size,
            "startup_max_queue_depth": self.startup_max_queue_depth,
            "startup_max_active_rest_jobs": self.startup_max_active_rest_jobs,
            "defer_gap_repair_during_startup": self.defer_gap_repair_during_startup,
            "effective_concurrency": self._effective_concurrency(),
        }

    def backlog_metrics(self) -> dict[str, Any]:
        """Gap/queue backlog observability — scheduling only, no strategy impact."""
        active = self.select_active_universe()
        ranked = self.ranked_symbols(active)
        gap_count = 0
        oldest: datetime | None = None
        newest: datetime | None = None
        now = datetime.now(timezone.utc)
        for sym in active:
            for tf in self.tf_priority:
                if tf == "1m" and not self._include_1m(sym, ranked):
                    continue
                gaps = self.ohlcv.find_gaps(sym, tf) if self.gap_fill else []
                gap_count += len(gaps)
                for g in gaps[:20]:
                    ot = getattr(g, "expected_open_time", None)
                    if ot is None:
                        continue
                    if ot.tzinfo is None:
                        ot = ot.replace(tzinfo=timezone.utc)
                    if oldest is None or ot < oldest:
                        oldest = ot
                    if newest is None or ot > newest:
                        newest = ot

        enqueue_rpm = self._enqueue_rate_per_min()
        complete_rpm = self._throughput_per_min()
        depth = self.queue_size
        hist = list(self._backlog_depth_history)
        if not hist or hist[-1][1] != depth:
            # Sample when depth changes or history empty (avoid inflating on every status poll).
            self._backlog_depth_history.append((time.monotonic(), depth))
            hist = list(self._backlog_depth_history)
        depth_delta = 0
        if len(hist) >= 2:
            depth_delta = hist[-1][1] - hist[0][1]
        window_s = (hist[-1][0] - hist[0][0]) if len(hist) >= 2 else 0.0

        # Classify over a sustained window (>= ~60s of samples preferred).
        # BLOCKED requires sustained zero completions with failures or a stuck
        # queue — a lone RETRY_WAIT must not flip state while the queue is stable.
        failed_n = self._count_status(JobStatus.FAILED)
        running_n = self._count_status(JobStatus.RUNNING)
        state = "STABLE_BACKLOG"
        if depth == 0 and gap_count == 0:
            state = "CLEAR"
        elif complete_rpm > enqueue_rpm and depth_delta < 0:
            state = "DRAINING"
        elif enqueue_rpm > complete_rpm + 0.5 and depth_delta > 5:
            state = "GROWING"
        elif (
            depth > 0
            and window_s >= 60
            and complete_rpm == 0
            and running_n == 0
            and (
                failed_n > 0
                or (enqueue_rpm == 0 and depth_delta >= 0 and window_s >= 120)
            )
        ):
            state = "BLOCKED"
        elif abs(depth_delta) <= 5 and depth > 0:
            state = "STABLE_BACKLOG"
        elif depth == 0 and gap_count > 0:
            state = "STABLE_BACKLOG"

        remaining = depth + self._count_status(JobStatus.RETRY_WAIT)
        eta = None
        if complete_rpm > 0 and remaining > 0:
            eta = round(remaining / (complete_rpm / 60.0), 1)

        oldest_age = (
            round((now - oldest).total_seconds(), 1) if oldest is not None else None
        )
        newest_age = (
            round((now - newest).total_seconds(), 1) if newest is not None else None
        )

        return {
            "gap_backlog_count": gap_count,
            "oldest_gap_age_seconds": oldest_age,
            "newest_gap_age_seconds": newest_age,
            "enqueue_rate_per_minute": enqueue_rpm,
            "completion_rate_per_minute": complete_rpm,
            "failed_gap_count": self._count_status(JobStatus.FAILED),
            "retry_count": self._count_status(JobStatus.RETRY_WAIT),
            "estimated_completion_seconds": eta,
            "active_backfill_jobs": self._count_status(JobStatus.RUNNING),
            "cooldown_suppressed_count": self._enqueue_blocked_cooldown,
            "duplicate_suppressed_count": (
                self._enqueue_blocked_queued + self._enqueue_blocked_window
            ),
            "queue_depth": depth,
            "queue_depth_delta_window": depth_delta,
            "backlog_state": state,
            "priority_policy": self.backfill_priority_policy,
            "startup_ramp": self.startup_ramp_metrics(),
        }

    def backfill_report(self, symbols: list[str]) -> dict[str, Any]:
        """Queue stats + per-TF coverage + estimated_progress from throughput."""
        # Report against active universe only — full discovery (~500) makes this
        # endpoint CPU-heavy and competes with health/screener during warm boot.
        report_syms = self.select_active_universe(symbols) or list(symbols)
        out: dict[str, Any] = {}
        ranked = self.ranked_symbols(report_syms)
        for tf in self.tf_priority:
            complete = partial = waiting = candles = 0
            for sym in report_syms:
                if tf == "1m" and not self._include_1m(sym, ranked):
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

        goals = self.coverage_goals_status(report_syms)
        metrics = self.status()
        return {
            "total_jobs": total_jobs,
            "pending": pending,
            "running": running,
            "complete": complete,
            "completed_series": complete,
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
            "active_universe_count": self.active_universe_count,
            "report_universe": len(report_syms),
            "status_counts": {s.value: self._count_status(s) for s in JobStatus},
            "enqueue_reasons": metrics.get("enqueue_reasons"),
            "enqueue_blocked_queued": metrics.get("enqueue_blocked_queued"),
            "enqueue_blocked_cooldown": metrics.get("enqueue_blocked_cooldown"),
            "enqueue_blocked_window": metrics.get("enqueue_blocked_window"),
            "enqueue_blocked_attempts": metrics.get("enqueue_blocked_attempts"),
            "enqueue_blocked_queue_full": metrics.get("enqueue_blocked_queue_full"),
            "max_queue_size": metrics.get("max_queue_size"),
            "stale_cooldowns_active": metrics.get("stale_cooldowns_active"),
            "stale_cooldown_seconds": metrics.get("stale_cooldown_seconds"),
            "max_job_attempts": metrics.get("max_job_attempts"),
            "queue_depth_max_recent": metrics.get("queue_depth_max_recent"),
            "queue_depth_avg_recent": metrics.get("queue_depth_avg_recent"),
            "gap_backlog_count": metrics.get("gap_backlog_count"),
            "oldest_gap_age_seconds": metrics.get("oldest_gap_age_seconds"),
            "newest_gap_age_seconds": metrics.get("newest_gap_age_seconds"),
            "enqueue_rate_per_minute": metrics.get("enqueue_rate_per_minute"),
            "completion_rate_per_minute": metrics.get("completion_rate_per_minute"),
            "failed_gap_count": metrics.get("failed_gap_count"),
            "retry_count": metrics.get("retry_count"),
            "estimated_completion_seconds": metrics.get("estimated_completion_seconds"),
            "active_backfill_jobs": metrics.get("active_backfill_jobs"),
            "cooldown_suppressed_count": metrics.get("cooldown_suppressed_count"),
            "duplicate_suppressed_count": metrics.get("duplicate_suppressed_count"),
            "backlog_state": metrics.get("backlog_state"),
            "priority_policy": metrics.get("priority_policy"),
            "startup_ramp": metrics.get("startup_ramp"),
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
        now_m = time.monotonic()
        active_cooldowns = sum(
            1 for until in self._stale_cooldown_until.values() if until > now_m
        )
        depth_vals = [d for _, d in self._queue_depth_samples]
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
            "active_universe_count": self.active_universe_count,
            "m1_rolling_universe": self.m1_rolling_count,
            "enqueue_reasons": dict(self._enqueue_reasons),
            "enqueue_blocked_queued": self._enqueue_blocked_queued,
            "enqueue_blocked_cooldown": self._enqueue_blocked_cooldown,
            "enqueue_blocked_window": self._enqueue_blocked_window,
            "enqueue_blocked_attempts": self._enqueue_blocked_attempts,
            "enqueue_blocked_queue_full": self._enqueue_blocked_queue_full,
            "max_queue_size": self.max_queue_size,
            "stale_cooldowns_active": active_cooldowns,
            "stale_cooldown_seconds": self.stale_cooldown_seconds,
            "max_job_attempts": self.max_job_attempts,
            "queue_depth_max_recent": max(depth_vals) if depth_vals else self.queue_size,
            "queue_depth_avg_recent": (
                round(sum(depth_vals) / len(depth_vals), 1) if depth_vals else None
            ),
            **self.backlog_metrics(),
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
