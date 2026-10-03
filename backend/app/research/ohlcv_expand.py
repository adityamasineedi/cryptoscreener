"""Expand persisted OHLCV with real Binance Futures REST history.

Research helper — fetches real candles into Postgres/memory. Does not fabricate
data or change live signal thresholds. Used by CLI script and /api/research/ohlcv-expand.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from app.config import get_settings
from app.engines.orchestrator import get_orchestrator
from app.ingestion.binance_rest import BinanceRestClient
from app.ingestion.klines import TIMEFRAME_MS, normalize_rest_kline, normalize_timeframe
from app.research.query_utils import normalize_research_symbol, normalize_research_timeframe
from app.services.database import db_manager
from app.services.ohlcv_store import ohlcv_store

JobStatus = Literal["idle", "running", "done", "error", "cancelled"]

ALLOWED_TFS = frozenset({"1m", "5m", "15m", "1h", "4h", "1d"})


def parse_until_ms(s: str) -> int:
    """UTC ms for the start of the given calendar day (YYYY-MM-DD)."""
    dt = datetime.strptime(s.strip()[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


async def db_earliest_ms(symbol: str, timeframe: str) -> int | None:
    if db_manager.engine is None:
        return None
    from sqlalchemy import text

    async with db_manager.engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT MIN(time) FROM ohlcv
                    WHERE symbol = :symbol AND timeframe = :tf
                    """
                ),
                {"symbol": symbol.upper(), "tf": timeframe},
            )
        ).fetchone()
    if not row or row[0] is None:
        return None
    ts = row[0]
    if hasattr(ts, "timestamp"):
        return int(ts.timestamp() * 1000)
    return None


async def db_latest_ms(symbol: str, timeframe: str) -> int | None:
    if db_manager.engine is None:
        return None
    from sqlalchemy import text

    async with db_manager.engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT MAX(time) FROM ohlcv
                    WHERE symbol = :symbol AND timeframe = :tf
                    """
                ),
                {"symbol": symbol.upper(), "tf": timeframe},
            )
        ).fetchone()
    if not row or row[0] is None:
        return None
    ts = row[0]
    if hasattr(ts, "timestamp"):
        return int(ts.timestamp() * 1000)
    return None


async def _flush_pending() -> None:
    while ohlcv_store._pending_db:  # noqa: SLF001
        await ohlcv_store.flush_db()


@dataclass
class SeriesResult:
    symbol: str
    timeframe: str
    written: int = 0
    direction: str = ""
    error: str | None = None
    db_start: str | None = None
    db_end: str | None = None
    bars: int = 0


@dataclass
class ExpandJob:
    job_id: str
    status: JobStatus = "idle"
    symbols: list[str] = field(default_factory=list)
    timeframes: list[str] = field(default_factory=list)
    until: str | None = None
    refresh_tip: bool = True
    max_pages: int = 200
    total_cells: int = 0
    done_cells: int = 0
    # 0..1 progress within the current cell (REST pages). Lets UI % move while
    # a single symbol/TF fetch runs for a long time.
    cell_fraction: float = 0.0
    current: str = ""
    written_total: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    results: list[SeriesResult] = field(default_factory=list)
    _cancel: bool = field(default=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        frac = max(0.0, min(1.0, float(self.cell_fraction or 0.0)))
        effective = float(self.done_cells) + frac
        pct = (
            round(100.0 * effective / self.total_cells, 1)
            if self.total_cells
            else 0.0
        )
        return {
            "job_id": self.job_id,
            "status": self.status,
            "symbols": self.symbols,
            "timeframes": self.timeframes,
            "until": self.until,
            "refresh_tip": self.refresh_tip,
            "max_pages": self.max_pages,
            "total_cells": self.total_cells,
            "done_cells": self.done_cells,
            "cell_fraction": round(frac, 3),
            "pct": pct,
            "current": self.current,
            "written_total": self.written_total,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "results": [
                {
                    "symbol": r.symbol,
                    "timeframe": r.timeframe,
                    "written": r.written,
                    "direction": r.direction,
                    "error": r.error,
                    "db_start": r.db_start,
                    "db_end": r.db_end,
                    "bars": r.bars,
                }
                for r in self.results
            ],
        }


class OhlcvExpandService:
    """Single-flight expand job manager (one active expand at a time)."""

    def __init__(self) -> None:
        self._job: ExpandJob | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        if self._job is None:
            return {"status": "idle", "job_id": None}
        return self._job.to_dict()

    async def start(
        self,
        *,
        symbols: list[str],
        timeframes: list[str],
        until: str | None,
        refresh_tip: bool = True,
        max_pages: int = 200,
    ) -> dict[str, Any]:
        syms = [normalize_research_symbol(s) for s in symbols if s and s.strip()]
        tfs = [
            normalize_research_timeframe(t)
            for t in timeframes
            if t and str(t).strip()
        ]
        tfs = [t for t in tfs if t in ALLOWED_TFS and t in TIMEFRAME_MS]
        if not syms:
            raise ValueError("At least one symbol is required")
        if not tfs:
            raise ValueError("At least one supported timeframe is required")
        if until:
            parse_until_ms(until)  # validate early

        async with self._lock:
            if self._job is not None and self._job.status == "running":
                raise RuntimeError(
                    f"Expand already running (job_id={self._job.job_id})"
                )
            job = ExpandJob(
                job_id=uuid.uuid4().hex[:12],
                status="running",
                symbols=syms,
                timeframes=tfs,
                until=until,
                refresh_tip=refresh_tip,
                max_pages=max(1, min(int(max_pages), 500)),
                total_cells=len(syms) * len(tfs),
                # Non-zero from the first API response so UI never opens at blank 0%.
                cell_fraction=0.02,
                current="Starting…",
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            self._job = job
            self._task = asyncio.create_task(
                self._run(job), name=f"ohlcv_expand_{job.job_id}"
            )
            return job.to_dict()

    async def cancel(self) -> dict[str, Any]:
        async with self._lock:
            job = self._job
            if job is None or job.status != "running":
                return self.status()
            job._cancel = True
            job.status = "cancelled"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            return job.to_dict()

    async def _run(self, job: ExpandJob) -> None:
        owns_rest = False
        rest: BinanceRestClient | None = None
        try:
            orch = get_orchestrator()
            if orch is not None and getattr(orch, "rest", None) is not None:
                rest = orch.rest
            else:
                settings = get_settings()
                rest = BinanceRestClient(settings)
                await rest.start()
                owns_rest = True

            # Show a visible non-zero % during hydrate (can take minutes on large DBs).
            # Do NOT zero this until a cell overwrites it — otherwise polls can catch
            # "Loading…" with pct=0 right after load_from_db returns.
            job.current = "Loading candles from DB…"
            job.cell_fraction = 0.05
            await ohlcv_store.load_from_db(
                symbols=job.symbols,
                timeframes=job.timeframes,
                limit_per_series=200_000,
            )

            until_ms = parse_until_ms(job.until) if job.until else None

            for sym in job.symbols:
                for tf in job.timeframes:
                    if job._cancel:
                        job.status = "cancelled"
                        job.finished_at = datetime.now(timezone.utc).isoformat()
                        return
                    job.current = f"{sym} {tf} · starting…"
                    # Small non-zero fraction so overall % moves as soon as a cell starts
                    # (6 cells → ~1.7% visible immediately, not stuck at 0%).
                    job.cell_fraction = 0.1

                    def _on_page(
                        pages: int, max_p: int, label: str, written_cell: int
                    ) -> None:
                        # Visible early: treat ~40 pages as a full cell share.
                        denom = max(1, min(int(max_p), 40))
                        job.cell_fraction = min(0.99, max(0.1, pages / denom))
                        job.current = (
                            f"{sym} {tf} · {label} p{pages}/{max_p} · +{written_cell}"
                        )

                    def _on_written(delta: int) -> None:
                        if delta:
                            job.written_total += delta

                    result = SeriesResult(symbol=sym, timeframe=tf)
                    try:
                        written = 0
                        if until_ms is not None:
                            n = await expand_series_backward(
                                rest,
                                sym,
                                tf,
                                until_ms=until_ms,
                                max_pages=job.max_pages,
                                should_cancel=lambda: job._cancel,
                                on_page=lambda p, m, w: _on_page(p, m, "back", w),
                                on_written=_on_written,
                            )
                            written += n
                            result.direction = "backward"
                        if job.refresh_tip and not job._cancel:
                            tip_pages = min(40, job.max_pages)
                            n = await expand_series_forward(
                                rest,
                                sym,
                                tf,
                                max_pages=tip_pages,
                                should_cancel=lambda: job._cancel,
                                on_page=lambda p, m, w: _on_page(p, m, "tip", w),
                                on_written=_on_written,
                            )
                            written += n
                            result.direction = (
                                "backward+tip" if until_ms is not None else "tip"
                            )
                        result.written = written
                        # Coverage snapshot — avoid COUNT(*) on huge tables (blocks UI %).
                        job.current = f"{sym} {tf} · coverage…"
                        t0 = await db_earliest_ms(sym, tf)
                        t1 = await db_latest_ms(sym, tf)
                        store_bars = len(ohlcv_store.get_closed(sym, tf) or [])
                        result.bars = store_bars
                        result.db_start = (
                            datetime.fromtimestamp(t0 / 1000, tz=timezone.utc).date().isoformat()
                            if t0 is not None
                            else None
                        )
                        result.db_end = (
                            datetime.fromtimestamp(t1 / 1000, tz=timezone.utc).date().isoformat()
                            if t1 is not None
                            else None
                        )
                    except Exception as exc:  # noqa: BLE001
                        result.error = str(exc)
                    job.results.append(result)
                    job.done_cells += 1
                    job.cell_fraction = 0.0

            if job._cancel:
                job.status = "cancelled"
            else:
                job.status = "done"
            job.current = ""
            job.finished_at = datetime.now(timezone.utc).isoformat()
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = str(exc)
            job.finished_at = datetime.now(timezone.utc).isoformat()
        finally:
            try:
                await _flush_pending()
            except Exception:  # noqa: BLE001
                pass
            if owns_rest and rest is not None:
                try:
                    await rest.close()
                except Exception:  # noqa: BLE001
                    pass


async def expand_series_backward(
    rest: BinanceRestClient,
    symbol: str,
    timeframe: str,
    *,
    until_ms: int | None,
    max_pages: int,
    should_cancel: Any | None = None,
    on_page: Any | None = None,
    on_written: Any | None = None,
) -> int:
    """Walk older history from DB earliest back to until_ms."""
    tf = normalize_timeframe(timeframe)
    step = TIMEFRAME_MS[tf]
    existing = ohlcv_store.get_closed(symbol, tf)
    known = {c.open_time for c in existing}
    db_earliest = await db_earliest_ms(symbol, tf)
    if db_earliest is not None:
        cursor_end = db_earliest - 1
    elif existing:
        cursor_end = int(min(c.open_time for c in existing).timestamp() * 1000) - 1
    else:
        cursor_end = int(time.time() * 1000)

    written = 0
    pages = 0
    while pages < max_pages:
        if should_cancel and should_cancel():
            break
        if until_ms is not None and cursor_end < until_ms:
            break
        # Announce the page *before* the REST wait so UI is never stuck at 0%.
        if on_page is not None:
            on_page(pages + 1, max_pages, written)
        start_ms = cursor_end - step * 1500
        if until_ms is not None:
            start_ms = max(start_ms, until_ms)
        raw = await rest.futures_klines(
            symbol,
            tf,
            limit=1500,
            start_time=start_ms,
            end_time=cursor_end,
        )
        if not raw:
            break
        candles = []
        for row in raw:
            c = normalize_rest_kline(symbol, tf, row)
            if c and c.is_closed and c.open_time not in known:
                candles.append(c)
                known.add(c.open_time)
        if candles:
            n = await ohlcv_store.ingest_history(candles)
            written += n
            if on_written is not None and n:
                on_written(n)
            await _flush_pending()
        oldest = min(int(r[0]) for r in raw)
        if oldest >= cursor_end:
            break
        cursor_end = oldest - 1
        pages += 1
        if on_page is not None:
            on_page(pages, max_pages, written)
        if until_ms is not None and oldest <= until_ms:
            break
        await asyncio.sleep(0.15)
    return written


async def expand_series_forward(
    rest: BinanceRestClient,
    symbol: str,
    timeframe: str,
    *,
    max_pages: int,
    should_cancel: Any | None = None,
    on_page: Any | None = None,
    on_written: Any | None = None,
) -> int:
    """Fill from DB latest toward now (tip catch-up)."""
    tf = normalize_timeframe(timeframe)
    step = TIMEFRAME_MS[tf]
    existing = ohlcv_store.get_closed(symbol, tf)
    known = {c.open_time for c in existing}
    db_latest = await db_latest_ms(symbol, tf)
    if db_latest is not None:
        cursor_start = db_latest + step
    elif existing:
        cursor_start = int(max(c.open_time for c in existing).timestamp() * 1000) + step
    else:
        # No history — pull latest page once
        if on_page is not None:
            on_page(1, max(1, max_pages), 0)
        raw = await rest.futures_klines(symbol, tf, limit=1500)
        candles = []
        for row in raw or []:
            c = normalize_rest_kline(symbol, tf, row)
            if c and c.is_closed and c.open_time not in known:
                candles.append(c)
                known.add(c.open_time)
        if candles:
            n = await ohlcv_store.ingest_history(candles)
            if on_written is not None and n:
                on_written(n)
            await _flush_pending()
            return n
        return 0

    now_ms = int(time.time() * 1000)
    if cursor_start >= now_ms - step:
        return 0

    written = 0
    pages = 0
    while pages < max_pages and cursor_start < now_ms:
        if should_cancel and should_cancel():
            break
        if on_page is not None:
            on_page(pages + 1, max_pages, written)
        end_ms = min(cursor_start + step * 1500, now_ms)
        raw = await rest.futures_klines(
            symbol,
            tf,
            limit=1500,
            start_time=cursor_start,
            end_time=end_ms,
        )
        if not raw:
            break
        candles = []
        for row in raw:
            c = normalize_rest_kline(symbol, tf, row)
            if c and c.is_closed and c.open_time not in known:
                candles.append(c)
                known.add(c.open_time)
        if candles:
            n = await ohlcv_store.ingest_history(candles)
            written += n
            if on_written is not None and n:
                on_written(n)
            await _flush_pending()
        newest = max(int(r[0]) for r in raw)
        if newest < cursor_start:
            break
        next_start = newest + step
        if next_start <= cursor_start:
            break
        cursor_start = next_start
        pages += 1
        if on_page is not None:
            on_page(pages, max_pages, written)
        await asyncio.sleep(0.15)
    return written


ohlcv_expand_service = OhlcvExpandService()
