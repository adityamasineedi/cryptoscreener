from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.core.adaptive_rate import AdaptiveRateController
from app.core.cache import TTLCache
from app.core.logging import get_logger
from app.core.request_queue import RequestQueue
from app.engines.oi.engine import (
    OISample,
    classify_price_oi,
    oi_changes_from_history,
)
from app.ingestion.binance_rest import BinanceRestClient
from app.models.schemas import DataStatus, FreshValue

logger = get_logger("oi_scheduler")

PriceLookup = Callable[[str], float | None]
VolumeLookup = Callable[[str], float]
McapLookup = Callable[[str], float]


@dataclass
class OIState:
    symbol: str
    open_interest: FreshValue[float] = field(default_factory=lambda: FreshValue.waiting("binance_rest"))
    oi_change_pct: FreshValue[float] = field(default_factory=lambda: FreshValue.waiting("binance_rest"))
    oi_classification: FreshValue[str] = field(default_factory=lambda: FreshValue.waiting("binance_rest"))
    open_interest_value: FreshValue[float] = field(default_factory=lambda: FreshValue.waiting("binance_rest"))
    changes: dict[str, float | None] = field(default_factory=dict)
    history: deque[OISample] = field(default_factory=lambda: deque(maxlen=500))
    last_success_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_value: float | None = None
    next_refresh_at: datetime | None = None
    fetch_attempts: int = 0
    fetch_errors: int = 0
    failure_count: int = 0


class OIScheduler:
    """
    Staggered OI polling with priority modes.

    Default modes (TOP_VOLUME / TOP_MARKET_CAP) only REST-poll the top
    large-cap / high-volume tier plus visible + watchlist symbols.
    Set priority_mode=FULL_UNIVERSE to eventually cover every listed symbol.
    """

    def __init__(
        self,
        settings: Settings,
        rest: BinanceRestClient,
        *,
        price_lookup: PriceLookup | None = None,
        volume_lookup: VolumeLookup | None = None,
        mcap_lookup: McapLookup | None = None,
        watchlist: list[str] | None = None,
        poll_interval_seconds: float | None = None,
        stagger_seconds: float = 0.2,
        max_concurrency: int | None = None,
        batch_size: int | None = None,
        priority_mode: str | None = None,
        active_count: int | None = None,
    ) -> None:
        self.settings = settings
        self.rest = rest
        self.price_lookup = price_lookup or (lambda _s: None)
        self.volume_lookup = volume_lookup or (lambda _s: 0.0)
        self.mcap_lookup = mcap_lookup or (lambda _s: 0.0)
        self.watchlist = {s.upper() for s in (watchlist or [])}

        oi_cfg = settings.market_config.get("open_interest") or {}
        self.poll_interval = float(
            poll_interval_seconds
            if poll_interval_seconds is not None
            else settings.oi_refresh_seconds
            or oi_cfg.get("refresh_seconds", 120)
        )
        concurrency = int(
            max_concurrency
            if max_concurrency is not None
            else settings.oi_concurrency or oi_cfg.get("concurrency", 3)
        )
        self.batch_size = int(
            batch_size
            if batch_size is not None
            else settings.oi_batch_size or oi_cfg.get("batch_size", 40)
        )
        self.priority_mode = str(
            priority_mode
            or settings.oi_priority_mode
            or oi_cfg.get("priority_mode", "TOP_MARKET_CAP")
        ).upper()
        self.active_count = int(
            active_count
            if active_count is not None
            else settings.oi_top_n
            or oi_cfg.get("active_count")
            or oi_cfg.get("tier1_count", 40)
        )
        self._stale_seconds = float(oi_cfg.get("stale_seconds", 300))
        self._cache_ttl = float(oi_cfg.get("cache_ttl_seconds", 90))
        self._min_headroom = float(oi_cfg.get("min_token_headroom", 80))

        self._queue = RequestQueue(
            max_concurrency=concurrency,
            stagger_seconds=stagger_seconds,
        )
        self.adaptive = AdaptiveRateController(
            min_concurrency=1,
            max_concurrency=max(1, concurrency),
            base_pause_seconds=max(0.15, stagger_seconds),
        )
        self._cache = TTLCache()
        self._states: dict[str, OIState] = {}
        self._symbols: list[str] = []
        self._visible: set[str] = set()
        self._cursor = 0
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._polls_completed = 0
        self._last_cycle_at: datetime | None = None
        self._base_batch = self.batch_size
        self._base_concurrency = concurrency

    def set_symbols(self, symbols: list[str]) -> None:
        self._symbols = list(symbols)
        for sym in symbols:
            self._states.setdefault(sym, OIState(symbol=sym))

    def set_watchlist(self, symbols: list[str]) -> None:
        self.watchlist = {s.upper() for s in symbols}

    def set_visible_symbols(self, symbols: list[str]) -> None:
        """Screener-visible symbols get priority after high-volume names."""
        self._visible = {s.upper() for s in symbols}

    def get_state(self, symbol: str) -> OIState | None:
        return self._states.get(symbol)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name="oi_scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _ranked_universe(self) -> list[str]:
        """Rank discovered symbols for large-cap selection."""
        syms = list(self._symbols)
        if self.priority_mode == "TOP_MARKET_CAP":
            # Prefer market cap; fall back to 24h quote volume when mcap is missing.
            return sorted(
                syms,
                key=lambda s: (self.mcap_lookup(s), self.volume_lookup(s)),
                reverse=True,
            )
        return sorted(syms, key=self.volume_lookup, reverse=True)

    def _prioritized(self) -> list[str]:
        """
        Symbols eligible for OI REST calls.

        Priority:
        1. visible screener symbols
        2. top large-cap / high-volume tier (active_count)
        3. watchlist
        4. remaining universe — only when priority_mode=FULL_UNIVERSE
        """
        ranked = self._ranked_universe()
        if self.priority_mode == "WATCHLIST":
            visible = [s for s in ranked if s in self._visible]
            watched = [
                s for s in ranked if s in self.watchlist and s not in self._visible
            ]
            return visible + watched

        visible = [s for s in ranked if s in self._visible]
        visible_set = set(visible)
        top_n = [s for s in ranked if s not in visible_set][: self.tier_top()]
        top_set = set(top_n)
        watched = [
            s
            for s in ranked
            if s in self.watchlist and s not in visible_set and s not in top_set
        ]
        if self.priority_mode == "FULL_UNIVERSE":
            watched_set = set(watched)
            rest = [
                s
                for s in ranked
                if s not in visible_set and s not in top_set and s not in watched_set
            ]
            return visible + top_n + watched + rest
        # Default: large caps / top volume only — do not REST-call the long tail
        return visible + top_n + watched

    def tier_top(self) -> int:
        return max(1, int(self.active_count))

    def _adapt_batch(self) -> None:
        """Adapt batch/concurrency from limiter headroom + adaptive controller."""
        self._queue.max_concurrency = self.adaptive.concurrency
        if self.adaptive.in_backoff():
            self.batch_size = max(5, self._base_batch // 3)
            return
        tokens = getattr(self.rest.limiter, "tokens", None)
        if not isinstance(tokens, (int, float)):
            self.batch_size = self._base_batch
            return
        capacity = float(getattr(self.rest.limiter, "capacity", 1100) or 1100)
        ratio = tokens / capacity if capacity else 0.0
        if ratio > 0.7 and self.adaptive.count_429 == 0:
            self.batch_size = min(self._base_batch + 10, 60)
            self.adaptive.maybe_adjust()
        elif ratio < 0.25:
            self.batch_size = max(8, self._base_batch // 2)
        else:
            self.batch_size = self._base_batch

    def _is_uncovered(self, st: OIState) -> bool:
        return st.last_success_at is None and st.open_interest.value is None

    def _due_symbols(self, ordered: list[str]) -> list[str]:
        """
        Prefer never-fetched symbols so coverage converges.

        Without this, visible/top-volume refreshes refill every batch and the
        long tail stays WAITING forever (~64/527 stall).
        """
        now = datetime.now(timezone.utc)
        uncover: list[str] = []
        refresh: list[str] = []
        for sym in ordered:
            st = self._states.setdefault(sym, OIState(symbol=sym))
            if self._is_uncovered(st):
                uncover.append(sym)
            elif st.next_refresh_at is None or st.next_refresh_at <= now:
                refresh.append(sym)
        due = uncover[: self.batch_size]
        if len(due) < self.batch_size:
            # Keep most of the batch for uncovering while the long tail remains
            refresh_slots = max(1, self.batch_size // 5) if uncover else self.batch_size
            due.extend(refresh[: min(refresh_slots, self.batch_size - len(due))])
            # If uncover still has capacity, fill remaining with more uncovered
            if len(due) < self.batch_size and len(uncover) > len(due):
                due.extend(
                    [s for s in uncover if s not in set(due)][
                        : self.batch_size - len(due)
                    ]
                )
        # Round-robin fallback so the universe still moves if nothing is due
        if not due and ordered:
            start = self._cursor % len(ordered)
            due = ordered[start : start + self.batch_size]
            if len(due) < self.batch_size:
                due += ordered[: self.batch_size - len(due)]
            self._cursor = (start + len(due)) % max(len(ordered), 1)
        return due

    def _cycle_sleep_seconds(self) -> float:
        """Faster cycles while uncovering; normal interval once covered."""
        active = self._prioritized()
        uncovered = sum(
            1
            for sym in active
            if self._is_uncovered(self._states.setdefault(sym, OIState(symbol=sym)))
        )
        if uncovered <= 0:
            return self.poll_interval
        if self.adaptive.in_backoff() or self.adaptive.count_429 > 0:
            return self.poll_interval
        tokens = getattr(self.rest.limiter, "tokens", None)
        capacity = float(getattr(self.rest.limiter, "capacity", 1100) or 1100)
        if isinstance(tokens, (int, float)) and capacity and tokens / capacity < 0.35:
            return self.poll_interval
        # Converge coverage without exceeding safety limits (still batched)
        if uncovered > 100:
            return min(self.poll_interval, 12.0)
        if uncovered > 20:
            return min(self.poll_interval, 18.0)
        return min(self.poll_interval, 25.0)

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            if self._symbols:
                await self._poll_batch()
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._cycle_sleep_seconds()
                )
            except asyncio.TimeoutError:
                pass

    async def _poll_batch(self) -> None:
        self._adapt_batch()
        ordered = self._prioritized()
        batch = self._due_symbols(ordered)
        if not batch:
            return

        async def _one(sym: str) -> None:
            tokens = getattr(self.rest.limiter, "tokens", None)
            if isinstance(tokens, (int, float)) and tokens < self._min_headroom:
                await asyncio.sleep(2.0)
            await self._poll_symbol(sym)

        await asyncio.gather(*(self._queue.run(lambda s=sym: _one(s)) for sym in batch))
        self._polls_completed += 1
        self._last_cycle_at = datetime.now(timezone.utc)
        cov = self.coverage_counts()
        logger.info(
            "oi_batch_complete",
            batch=len(batch),
            mode=self.priority_mode,
            covered=cov["live"] + cov["cached"] + cov["stale"],
            active=cov["total"],
            discovered=len(self._symbols),
        )

    async def _poll_symbol(self, symbol: str) -> None:
        import time as _time

        st = self._states.setdefault(symbol, OIState(symbol=symbol))
        st.fetch_attempts += 1
        st.last_attempt_at = datetime.now(timezone.utc)
        cache_key = f"oi:{symbol}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            await self._apply_snapshot(symbol, cached, from_cache=True)
            return
        t0 = _time.monotonic()
        try:
            raw = await self.rest.futures_open_interest(symbol)
            self.adaptive.record(
                latency_ms=(_time.monotonic() - t0) * 1000, outcome="success"
            )
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            outcome = "error"
            if "429" in err:
                outcome = "429"
            elif any(x in err for x in ("500", "502", "503", "504")):
                outcome = "5xx"
            elif "timeout" in err.lower():
                outcome = "timeout"
            self.adaptive.record(
                latency_ms=(_time.monotonic() - t0) * 1000, outcome=outcome
            )
            st.fetch_errors += 1
            st.failure_count += 1
            logger.warning("oi_fetch_failed", symbol=symbol, error=err)
            # Keep last good value as STALE/UNAVAILABLE — never invent 0
            if st.open_interest.value is None:
                st.open_interest = FreshValue.unavailable("binance_rest")
            else:
                st.open_interest = FreshValue(
                    value=st.open_interest.value,
                    timestamp=st.open_interest.timestamp,
                    source=st.open_interest.source,
                    status=DataStatus.STALE if st.last_value is not None else DataStatus.UNAVAILABLE,
                )
            # Back off this symbol proportionally to failures
            from datetime import timedelta

            delay = min(600.0, self.poll_interval * (1 + st.failure_count))
            st.next_refresh_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            return
        oi_str = raw.get("openInterest")
        if oi_str is None:
            if st.open_interest.value is None:
                st.open_interest = FreshValue.waiting("binance_rest")
            return
        oi = float(oi_str)
        ts_ms = raw.get("time")
        ts = (
            datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc)
            if ts_ms
            else datetime.now(timezone.utc)
        )
        price = self.price_lookup(symbol)
        snap = {"oi": oi, "ts": ts.isoformat(), "price": price}
        await self._cache.set(cache_key, snap, ttl=self._cache_ttl)
        await self._apply_snapshot(symbol, snap, from_cache=False)

    async def _apply_snapshot(self, symbol: str, snap: dict[str, Any], *, from_cache: bool) -> None:
        st = self._states.setdefault(symbol, OIState(symbol=symbol))
        oi = float(snap["oi"])
        ts = datetime.fromisoformat(snap["ts"])
        price = snap.get("price")
        if price is not None:
            price = float(price)
        sample = OISample(timestamp=ts, open_interest=oi, price=price)
        st.history.append(sample)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > self._stale_seconds:
            status = DataStatus.STALE
        elif from_cache:
            status = DataStatus.CACHED
        else:
            status = DataStatus.LIVE
        st.open_interest = FreshValue(
            value=oi, timestamp=ts, source="binance_rest", status=status
        )
        oiv = sample.open_interest_value
        st.open_interest_value = (
            FreshValue(value=oiv, timestamp=ts, source="binance_rest", status=status)
            if oiv is not None
            else FreshValue.waiting("binance_rest")
        )
        changes = oi_changes_from_history(st.history)
        st.changes = dict(changes)
        ch_5 = changes.get("5m")
        ch_24 = changes.get("24h")
        preferred = ch_5 if ch_5 is not None else ch_24
        st.oi_change_pct = (
            FreshValue(value=preferred, timestamp=ts, source="binance_rest", status=status)
            if preferred is not None
            else FreshValue.waiting("binance_rest")
        )
        price_ch = None
        if len(st.history) >= 2:
            past = st.history[-2]
            if past.price and price:
                price_ch = ((price - past.price) / past.price) * 100.0
        label = classify_price_oi(price_ch, ch_5)
        st.oi_classification = (
            FreshValue(value=label, timestamp=ts, source="binance_rest", status=status)
            if label
            else FreshValue.waiting("binance_rest")
        )
        st.last_success_at = ts
        st.last_value = oi
        st.failure_count = 0
        # Spread refreshes: base interval + small symbol hash offset
        offset = (sum(ord(c) for c in symbol) % 30)
        from datetime import timedelta

        st.next_refresh_at = datetime.now(timezone.utc) + timedelta(
            seconds=self.poll_interval + offset + self.adaptive.pause_seconds
        )
        # Optional persistence / redis (no-op when disabled)
        try:
            from app.services.persistence import persistence
            from app.services.redis_state import redis_state

            await persistence.persist_open_interest(
                symbol, oi, source="binance_rest", ts=ts
            )
            await redis_state.store_oi_state(
                symbol,
                {
                    "open_interest": oi,
                    "timestamp": ts.isoformat(),
                    "changes": st.changes,
                },
            )
        except Exception:  # noqa: BLE001
            pass

    def coverage_counts(self) -> dict[str, int]:
        """Coverage over the active poll set (large-cap tier by default)."""
        active = self._prioritized()
        live = stale = cached = waiting = unavailable = error = 0
        for sym in active:
            st = self._states.get(sym) or OIState(symbol=sym)
            status = st.open_interest.status
            if status == DataStatus.LIVE:
                live += 1
            elif status == DataStatus.CACHED:
                cached += 1
            elif status == DataStatus.STALE:
                stale += 1
            elif status == DataStatus.WAITING:
                waiting += 1
            elif st.fetch_errors > 0 and st.open_interest.value is None:
                error += 1
            else:
                unavailable += 1
        return {
            "live": live,
            "cached": cached,
            "stale": stale,
            "waiting": waiting,
            "unavailable": unavailable,
            "error": error,
            "available": live + cached + stale,
            "total": len(active),
            "discovered": len(self._symbols),
        }

    def oi_coverage_report(self) -> dict[str, Any]:
        cov = self.coverage_counts()
        total = max(cov.get("total") or 0, 1)
        active = self._prioritized()
        rows = []
        for sym in active:
            st = self._states.get(sym) or OIState(symbol=sym)
            status = st.open_interest.status.value
            if st.fetch_errors and st.open_interest.value is None:
                status = "ERROR"
            rows.append(
                {
                    "symbol": sym,
                    "status": status,
                    "last_success": st.last_success_at.isoformat()
                    if st.last_success_at
                    else None,
                    "last_attempt": st.last_attempt_at.isoformat()
                    if st.last_attempt_at
                    else None,
                    "last_value": st.last_value,
                    "next_refresh": st.next_refresh_at.isoformat()
                    if st.next_refresh_at
                    else None,
                    "failure_count": st.failure_count,
                    "value": st.open_interest.value,
                }
            )
        goal = 95.0
        pct = round(100.0 * cov["available"] / total, 2)
        return {
            "coverage": cov,
            "pct_available": pct,
            "goal_pct": goal,
            "goal_met": pct >= goal,
            "priority_mode": self.priority_mode,
            "active_count": self.active_count,
            "batch_size": self.batch_size,
            "visible_symbols": len(self._visible),
            "watchlist": len(self.watchlist),
            "polls_completed": self._polls_completed,
            "adaptive": self.adaptive.snapshot(),
            "symbols": rows,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def status(self) -> dict[str, Any]:
        cov = self.coverage_counts()
        return {
            "priority_mode": self.priority_mode,
            "refresh_seconds": self.poll_interval,
            "concurrency": self._queue.max_concurrency,
            "batch_size": self.batch_size,
            "active_count": self.active_count,
            "active_universe": cov["total"],
            "discovered": len(self._symbols),
            "universe": len(self._symbols),
            "visible": len(self._visible),
            "coverage": cov,
            "polls_completed": self._polls_completed,
            "adaptive": self.adaptive.snapshot(),
            "last_cycle_at": self._last_cycle_at.isoformat() if self._last_cycle_at else None,
        }

    async def recover_from_hist(
        self,
        symbol: str,
        *,
        period: str = "5m",
        limit: int = 30,
    ) -> None:
        try:
            rows = await self.rest.futures_open_interest_hist(
                symbol, period=period, limit=limit
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("oi_hist_failed", symbol=symbol, error=str(exc))
            return
        st = self._states.setdefault(symbol, OIState(symbol=symbol))
        for row in rows:
            if not isinstance(row, dict):
                continue
            oi = row.get("sumOpenInterest")
            ts_ms = row.get("timestamp")
            if oi is None or ts_ms is None:
                continue
            ts = datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc)
            st.history.append(
                OISample(
                    timestamp=ts,
                    open_interest=float(oi),
                    price=self.price_lookup(symbol),
                )
            )
