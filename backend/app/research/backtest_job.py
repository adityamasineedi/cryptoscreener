"""Background long-strategy backtest job (single-flight).

Mirrors OhlcvExpandService: POST start → asyncio task → GET status → cancel.
The UI Backtest tab polls status so runs survive route changes.

Configuration safety (risk profile, SHORT pause, identity metadata) is resolved
via ``backtest_ui_config`` without changing trading / fee calculations.

Progress: cell fraction + in-cell bar heartbeats so single-cell 15m runs no
longer appear stuck at 0% while structure scanning.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from app.research.backtest_timing import (
    DB_QUERY_END,
    DB_QUERY_START,
    HTF_DATA_LOAD_END,
    HTF_DATA_LOAD_START,
    JOB_CANCELLED,
    JOB_COMPLETED,
    JOB_CREATED,
    JOB_FAILED,
    JOB_STALLED,
    RAW_ROWS_LOADED,
    emit_phase,
    utc_now_iso,
)
from app.research.backtest_ui_config import (
    BacktestConfigError,
    ResolvedBacktestConfig,
    enrich_row_with_config,
    job_identity_payload,
    validate_backtest_request,
)
from app.research.query_utils import normalize_research_symbol, normalize_research_timeframe

JobStatus = Literal["idle", "running", "done", "error", "cancelled", "stalled"]

ALLOWED_TFS = frozenset({"15m", "1h", "4h", "5m", "1d"})


def _settings_timeouts() -> tuple[float, float, float]:
    """Return (job_timeout_s, heartbeat_timeout_s, db_timeout_s)."""
    try:
        from app.config import get_settings

        s = get_settings()
        return (
            float(getattr(s, "backtest_job_timeout_seconds", 3600.0) or 3600.0),
            float(getattr(s, "backtest_heartbeat_timeout_seconds", 120.0) or 120.0),
            float(getattr(s, "backtest_db_timeout_seconds", 60.0) or 60.0),
        )
    except Exception:  # noqa: BLE001
        return 3600.0, 120.0, 60.0


@dataclass
class BacktestJob:
    job_id: str
    status: JobStatus = "idle"
    symbols: list[str] = field(default_factory=list)
    timeframes: list[str] = field(default_factory=list)
    direction: str = "LONG"
    combination_id: str = "COMBO_02"
    limit: int = 1200
    risk_usd: float = 20.0
    principal_usd: float = 1000.0
    leverage: float = 2.0
    taker_fee_pct: float = 0.04
    maker_fee_pct: float = 0.02
    include_trades: bool = True
    start_date: str | None = None
    end_date: str | None = None
    total_cells: int = 0
    done_cells: int = 0
    current: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    error_code: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    playbook: str | None = None
    combination_name: str | None = None
    disclaimer: str | None = None
    label: str | None = None
    identity: dict[str, Any] = field(default_factory=dict)
    resolved_cells: list[dict[str, Any]] = field(default_factory=list)
    dataset_fingerprint: str | None = None
    # In-cell progress (updated from worker thread via progress_callback).
    phase: str = ""
    bars_processed: int = 0
    total_bars: int = 0
    trades_generated: int = 0
    last_heartbeat: str | None = None
    rows_loaded: int = 0
    load_meta: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict)
    elapsed_seconds: float | None = None
    _resolved: ResolvedBacktestConfig | None = field(default=None, repr=False)
    _t0: float = field(default=0.0, repr=False)
    _cancel: bool = field(default=False, repr=False)
    _hb_mono: float = field(default=0.0, repr=False)
    _cancel_event: Any = field(default=None, repr=False)

    def snapshot_elapsed(self) -> float | None:
        """Freeze wall-clock elapsed at terminal status (status polls stay stable)."""
        if self._t0:
            self.elapsed_seconds = round(time.perf_counter() - self._t0, 3)
        return self.elapsed_seconds

    def heartbeat(
        self,
        *,
        phase: str | None = None,
        bars_processed: int | None = None,
        total_bars: int | None = None,
        trades: int | None = None,
        rows_loaded: int | None = None,
    ) -> None:
        if phase is not None:
            self.phase = phase
        if bars_processed is not None:
            self.bars_processed = int(bars_processed)
        if total_bars is not None:
            self.total_bars = int(total_bars)
        if trades is not None:
            self.trades_generated = int(trades)
        if rows_loaded is not None:
            self.rows_loaded = int(rows_loaded)
        self.last_heartbeat = utc_now_iso()
        self._hb_mono = time.perf_counter()

    def progress_percent(self) -> float:
        if self.status == "done":
            return 100.0
        if not self.total_cells:
            return 0.0
        cell_base = float(self.done_cells) / float(self.total_cells)
        intra = 0.0
        if self.status == "running" and self.total_bars > 0:
            intra = min(1.0, float(self.bars_processed) / float(self.total_bars))
            intra = intra / float(self.total_cells)
        elif self.status == "running" and self.phase and self.done_cells < self.total_cells:
            # Data-load / HTF phases before bar walk — show a small non-zero signal.
            if self.phase in (
                DB_QUERY_START,
                DB_QUERY_END,
                RAW_ROWS_LOADED,
                HTF_DATA_LOAD_START,
                HTF_DATA_LOAD_END,
            ):
                intra = 0.02 / float(self.total_cells)
        return round(100.0 * min(1.0, cell_base + intra), 1)

    def to_dict(self) -> dict[str, Any]:
        if self.status in ("done", "error", "cancelled", "stalled"):
            elapsed = self.elapsed_seconds
            if elapsed is None and self._t0:
                elapsed = round(time.perf_counter() - self._t0, 3)
                self.elapsed_seconds = elapsed
        else:
            elapsed = (
                round(time.perf_counter() - self._t0, 3) if self._t0 else None
            )
        identity = dict(self.identity or {})
        out: dict[str, Any] = {
            "job_id": self.job_id,
            "status": self.status,
            "symbols": self.symbols,
            "timeframes": self.timeframes,
            "direction": self.direction,
            "combination_id": self.combination_id,
            "combination_name": self.combination_name,
            "playbook": self.playbook,
            "label": self.label,
            "limit": self.limit,
            "risk_usd": self.risk_usd,
            "principal_usd": self.principal_usd,
            "leverage": self.leverage,
            "taker_fee_pct": self.taker_fee_pct,
            "maker_fee_pct": self.maker_fee_pct,
            "include_trades": self.include_trades,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "total_cells": self.total_cells,
            "done_cells": self.done_cells,
            "pct": self.progress_percent(),
            "progress_percent": self.progress_percent(),
            "current": self.current,
            "phase": self.phase,
            "bars_processed": self.bars_processed,
            "total_bars": self.total_bars,
            "trades": self.trades_generated,
            "trades_generated": self.trades_generated,
            "last_heartbeat": self.last_heartbeat,
            "rows_loaded": self.rows_loaded,
            "load_meta": self.load_meta,
            "timing": self.timing,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "error_code": self.error_code,
            "rows": self.rows,
            "elapsed_seconds": elapsed,
            "disclaimer": self.disclaimer,
            "resolved_cells": self.resolved_cells,
            "dataset_fingerprint": self.dataset_fingerprint,
            "execution_isolation": "process_pool",
            "requested_range": {
                "mode": identity.get("period_mode"),
                "start_date": self.start_date,
                "end_date": self.end_date,
                "limit": self.limit,
            },
            **identity,
        }
        return out


class BacktestJobService:
    """Single-flight backtest job manager (one active matrix at a time)."""

    def __init__(self) -> None:
        self._job: BacktestJob | None = None
        self._task: asyncio.Task | None = None
        self._watchdog: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        from app.research.backtest_cpu_pool import pool_stats

        if self._job is None:
            return {
                "status": "idle",
                "job_id": None,
                "rows": [],
                "execution_isolation": "process_pool",
                "cpu_pool": pool_stats(),
            }
        out = self._job.to_dict()
        out["cpu_pool"] = pool_stats()
        return out

    def is_running(self) -> bool:
        """True while a UI backtest job is actively executing (not terminal)."""
        return self._job is not None and self._job.status == "running"

    async def start(
        self,
        *,
        symbols: list[str],
        timeframes: list[str],
        direction: str = "LONG",
        combination_id: str = "COMBO_02",
        strategy_id: str | None = None,
        combo_version: str | None = None,
        setup_timeframe: str | None = None,
        risk_mode: str | None = None,
        research_risk_override: bool = False,
        limit: int = 1200,
        risk_usd: float = 20.0,
        risk_percent: float | None = None,
        principal_usd: float = 1000.0,
        leverage: float = 2.0,
        taker_fee_pct: float = 0.04,
        maker_fee_pct: float = 0.02,
        include_trades: bool = True,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        syms = [normalize_research_symbol(s) for s in symbols if s and str(s).strip()]
        tfs = [
            normalize_research_timeframe(t)
            for t in timeframes
            if t and str(t).strip()
        ]
        tfs = [t for t in tfs if t in ALLOWED_TFS]
        if not syms:
            raise BacktestConfigError("symbols_required", "At least one symbol is required")
        if not tfs:
            raise BacktestConfigError(
                "timeframes_required",
                "At least one supported timeframe is required",
            )

        lim = max(50, min(int(limit), 20000))
        risk = max(1.0, min(float(risk_usd), 10_000.0))
        principal = max(100.0, min(float(principal_usd), 10_000_000.0))
        lev = max(1.0, min(float(leverage or 2.0), 125.0))
        taker = max(0.0, min(float(taker_fee_pct), 1.0))
        maker = max(0.0, min(float(maker_fee_pct), 1.0))
        start_s = str(start_date).strip()[:10] if start_date else None
        end_s = str(end_date).strip()[:10] if end_date else None

        resolved = validate_backtest_request(
            symbols=syms,
            timeframes=tfs,
            direction=direction,
            combination_id=combination_id,
            strategy_id=strategy_id,
            combo_version=combo_version,
            setup_timeframe=setup_timeframe,
            risk_mode=risk_mode,
            research_risk_override=bool(research_risk_override),
            risk_usd=risk,
            risk_percent=risk_percent,
            principal_usd=principal,
            leverage=lev,
            taker_fee_pct=taker,
            maker_fee_pct=maker,
            start_date=start_s,
            end_date=end_s,
            allow_short=False,
        )
        identity = job_identity_payload(resolved)

        async with self._lock:
            if self._job is not None and self._job.status == "running":
                raise RuntimeError(
                    f"Backtest already running (job_id={self._job.job_id})"
                )
            from app.research.backtest_cpu_pool import new_cancel_event

            job = BacktestJob(
                job_id=uuid.uuid4().hex[:12],
                status="running",
                symbols=syms,
                timeframes=tfs,
                direction=resolved.direction,
                combination_id=(combination_id or "COMBO_02").upper().strip(),
                limit=lim,
                risk_usd=risk,
                principal_usd=principal,
                leverage=lev,
                taker_fee_pct=taker,
                maker_fee_pct=maker,
                include_trades=bool(include_trades),
                start_date=start_s,
                end_date=end_s,
                total_cells=len(syms) * len(tfs),
                started_at=datetime.now(timezone.utc).isoformat(),
                identity=identity,
                phase=JOB_CREATED,
                resolved_cells=[
                    {
                        "symbol": c.symbol,
                        "timeframe": c.timeframe,
                        "configured_risk_percent": c.configured_risk_percent,
                        "effective_risk_percent": c.effective_risk_percent,
                        "effective_risk_amount": c.effective_risk_amount,
                        "risk_source": c.risk_source,
                        "production_comparable": c.production_comparable,
                        "symbol_role": c.symbol_role,
                    }
                    for c in resolved.cells
                ],
                _resolved=resolved,
                _t0=time.perf_counter(),
                _cancel_event=new_cancel_event(),
            )
            job.heartbeat(phase=JOB_CREATED)
            emit_phase(
                JOB_CREATED,
                job_id=job.job_id,
                symbol=syms[0] if syms else None,
                timeframe=tfs[0] if tfs else None,
            )
            self._job = job
            self._task = asyncio.create_task(
                self._run(job), name=f"backtest_{job.job_id}"
            )
            self._watchdog = asyncio.create_task(
                self._watchdog_loop(job), name=f"backtest_wd_{job.job_id}"
            )
            return job.to_dict()

    async def cancel(self) -> dict[str, Any]:
        async with self._lock:
            job = self._job
            if job is None or job.status != "running":
                return self.status()
            job._cancel = True
            if job._cancel_event is not None:
                try:
                    job._cancel_event.set()
                except Exception:  # noqa: BLE001
                    pass
            job.status = "cancelled"
            job.phase = JOB_CANCELLED
            job.finished_at = datetime.now(timezone.utc).isoformat()
            job.snapshot_elapsed()
            job.heartbeat(phase=JOB_CANCELLED)
            emit_phase(JOB_CANCELLED, job_id=job.job_id)
            return job.to_dict()

    async def _watchdog_loop(self, job: BacktestJob) -> None:
        job_timeout, hb_timeout, _ = _settings_timeouts()
        try:
            while job.status == "running":
                await asyncio.sleep(2.0)
                if job.status != "running":
                    return
                now = time.perf_counter()
                if job._t0 and job_timeout > 0 and (now - job._t0) > job_timeout:
                    job._cancel = True
                    if job._cancel_event is not None:
                        try:
                            job._cancel_event.set()
                        except Exception:  # noqa: BLE001
                            pass
                    job.status = "error"
                    job.error = f"Job exceeded timeout ({job_timeout:.0f}s)"
                    job.error_code = "job_timeout"
                    job.phase = JOB_FAILED
                    job.finished_at = datetime.now(timezone.utc).isoformat()
                    job.snapshot_elapsed()
                    emit_phase(
                        JOB_FAILED,
                        job_id=job.job_id,
                        elapsed_seconds=now - job._t0,
                        reason="job_timeout",
                    )
                    return
                if (
                    job._hb_mono
                    and hb_timeout > 0
                    and (now - job._hb_mono) > hb_timeout
                ):
                    job._cancel = True
                    if job._cancel_event is not None:
                        try:
                            job._cancel_event.set()
                        except Exception:  # noqa: BLE001
                            pass
                    job.status = "stalled"
                    job.error = f"No heartbeat for {hb_timeout:.0f}s"
                    job.error_code = "NO_HEARTBEAT"
                    job.phase = JOB_STALLED
                    job.finished_at = datetime.now(timezone.utc).isoformat()
                    job.snapshot_elapsed()
                    emit_phase(
                        JOB_STALLED,
                        job_id=job.job_id,
                        elapsed_seconds=now - job._t0,
                        reason="NO_HEARTBEAT",
                    )
                    return
        except asyncio.CancelledError:
            return

    async def _run(self, job: BacktestJob) -> None:
        from app.research.service import get_bos_research_service

        try:
            svc = get_bos_research_service()
            resolved = job._resolved
            # Match UI cell order: symbol outer, timeframe inner.
            cells = [
                (sym, tf) for sym in job.symbols for tf in job.timeframes
            ]
            for i, (sym, tf) in enumerate(cells):
                if job._cancel:
                    job.status = "cancelled"
                    job.phase = JOB_CANCELLED
                    job.finished_at = datetime.now(timezone.utc).isoformat()
                    job.snapshot_elapsed()
                    job.current = ""
                    return
                # Publish current cell before the long await so UI polls show
                # which cell is running (pct stays at done/total until finish).
                job.current = f"{sym} {tf}"
                job.bars_processed = 0
                job.total_bars = 0
                job.trades_generated = 0
                job.heartbeat(phase=DB_QUERY_START)
                emit_phase(
                    DB_QUERY_START,
                    job_id=job.job_id,
                    symbol=sym,
                    timeframe=tf,
                    elapsed_seconds=time.perf_counter() - job._t0,
                )
                # Effective cell risk from resolved config (v1 profile or override).
                cell_risk = float(job.risk_usd)
                if resolved is not None:
                    from app.research.backtest_ui_config import cell_risk_lookup

                    cell = cell_risk_lookup(resolved, sym, tf)
                    if cell is not None:
                        cell_risk = float(cell.effective_risk_amount)
                else:
                    # Fallback — preserve prior COMBO_02 v1 sizing behavior.
                    if str(job.combination_id).upper() == "COMBO_02":
                        try:
                            from app.research.v1_production import (
                                classify_tier,
                                recommended_risk_usd,
                            )

                            tier = classify_tier(sym, tf)
                            if tier in ("core", "secondary"):
                                cell_risk = recommended_risk_usd(
                                    sym,
                                    tf,
                                    principal_usd=float(job.principal_usd),
                                    fallback_risk_usd=float(job.risk_usd),
                                )
                        except Exception:  # noqa: BLE001
                            cell_risk = float(job.risk_usd)

                def _on_progress(payload: dict[str, Any]) -> None:
                    # Called from worker thread — keep assignments simple.
                    job.heartbeat(
                        phase=str(payload.get("phase") or job.phase),
                        bars_processed=int(payload.get("bars_processed") or 0),
                        total_bars=int(payload.get("total_bars") or 0),
                        trades=int(payload.get("trades") or 0),
                    )
                    if payload.get("rows_loaded") is not None:
                        job.rows_loaded = int(payload["rows_loaded"])
                    if isinstance(payload.get("load_meta"), dict):
                        job.load_meta = payload["load_meta"]
                    if isinstance(payload.get("timing"), dict):
                        job.timing.update(payload["timing"])

                payload = await svc.strategy_matrix(
                    combination_id=job.combination_id,
                    symbols=[sym],
                    timeframes=[tf],
                    direction=job.direction,
                    limit=job.limit,
                    risk_usd=cell_risk,
                    principal_usd=float(job.principal_usd),
                    start_date=job.start_date,
                    end_date=job.end_date,
                    taker_fee=job.taker_fee_pct / 100.0,
                    maker_fee=job.maker_fee_pct / 100.0,
                    leverage=float(job.leverage),
                    include_trades=job.include_trades,
                    should_cancel=lambda: job._cancel,
                    progress_callback=_on_progress,
                    job_id=job.job_id,
                    use_research_cache=False,
                    cancel_event=job._cancel_event,
                )
                if payload.get("status") == "CANCELLED":
                    job.status = "cancelled"
                    job.phase = JOB_CANCELLED
                    job.finished_at = datetime.now(timezone.utc).isoformat()
                    job.snapshot_elapsed()
                    job.current = ""
                    return
                if payload.get("status") == "NOT_FOUND":
                    job.status = "error"
                    job.error = (
                        payload.get("reason")
                        or f"Unknown combination {job.combination_id}"
                    )
                    job.error_code = "not_found"
                    job.phase = JOB_FAILED
                    job.finished_at = datetime.now(timezone.utc).isoformat()
                    job.snapshot_elapsed()
                    job.current = ""
                    emit_phase(JOB_FAILED, job_id=job.job_id, reason=job.error)
                    return
                if payload.get("status") == "ERROR":
                    job.status = "error"
                    job.error = payload.get("reason") or "Backtest cell failed"
                    job.error_code = "cell_error"
                    job.phase = JOB_FAILED
                    job.finished_at = datetime.now(timezone.utc).isoformat()
                    job.snapshot_elapsed()
                    job.current = ""
                    emit_phase(JOB_FAILED, job_id=job.job_id, reason=job.error)
                    return

                if job.playbook is None:
                    job.playbook = payload.get("playbook")
                    job.combination_name = payload.get("combination_name")
                    job.disclaimer = payload.get("disclaimer")
                    job.label = payload.get("label")
                    ds = payload.get("dataset_id") or payload.get("dataset")
                    if ds:
                        job.dataset_fingerprint = str(ds)

                timing = payload.get("timing") or {}
                if isinstance(timing, dict):
                    job.timing.update(timing)

                for row in payload.get("rows") or []:
                    if resolved is not None:
                        enriched = enrich_row_with_config(
                            row,
                            resolved,
                            dataset_fingerprint=job.dataset_fingerprint,
                        )
                        enriched["requested_range"] = {
                            "mode": resolved.period_mode,
                            "start_date": job.start_date,
                            "end_date": job.end_date,
                            "limit": job.limit,
                            "requested_range_available": bool(
                                row.get("period_start") and row.get("period_end")
                            ),
                        }
                        enriched["risk_usd"] = cell_risk
                        job.rows.append(enriched)
                    else:
                        job.rows.append(row)
                job.done_cells = i + 1
                job.bars_processed = int(
                    (payload.get("rows") or [{}])[0].get("bars_loaded")
                    or job.bars_processed
                    or 0
                )
                job.heartbeat(phase="CELL_DONE")

            if job._cancel or job.status in ("cancelled", "stalled"):
                if job.status == "running":
                    job.status = "cancelled"
                    job.phase = JOB_CANCELLED
            else:
                job.status = "done"
                job.phase = JOB_COMPLETED
                emit_phase(
                    JOB_COMPLETED,
                    job_id=job.job_id,
                    elapsed_seconds=time.perf_counter() - job._t0,
                    trades=job.trades_generated,
                )
            job.current = ""
            job.finished_at = datetime.now(timezone.utc).isoformat()
            job.snapshot_elapsed()
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = str(exc)
            job.error_code = "runtime_error"
            job.phase = JOB_FAILED
            job.finished_at = datetime.now(timezone.utc).isoformat()
            job.snapshot_elapsed()
            job.current = ""
            emit_phase(JOB_FAILED, job_id=job.job_id, reason=str(exc))
        finally:
            if self._watchdog is not None and not self._watchdog.done():
                self._watchdog.cancel()


backtest_job_service = BacktestJobService()
