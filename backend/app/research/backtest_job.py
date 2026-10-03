"""Background long-strategy backtest job (single-flight).

Mirrors OhlcvExpandService: POST start → asyncio task → GET status → cancel.
The UI Backtest tab polls status so runs survive route changes.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from app.research.query_utils import normalize_research_symbol, normalize_research_timeframe

JobStatus = Literal["idle", "running", "done", "error", "cancelled"]

ALLOWED_TFS = frozenset({"15m", "1h", "4h", "5m", "1d"})


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
    rows: list[dict[str, Any]] = field(default_factory=list)
    playbook: str | None = None
    combination_name: str | None = None
    disclaimer: str | None = None
    label: str | None = None
    _t0: float = field(default=0.0, repr=False)
    _cancel: bool = field(default=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        pct = (
            round(100.0 * self.done_cells / self.total_cells, 1)
            if self.total_cells
            else 0.0
        )
        elapsed = (
            round(time.perf_counter() - self._t0, 3) if self._t0 else None
        )
        return {
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
            "taker_fee_pct": self.taker_fee_pct,
            "maker_fee_pct": self.maker_fee_pct,
            "include_trades": self.include_trades,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "total_cells": self.total_cells,
            "done_cells": self.done_cells,
            "pct": pct,
            "current": self.current,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "rows": self.rows,
            "elapsed_seconds": elapsed,
            "disclaimer": self.disclaimer,
        }


class BacktestJobService:
    """Single-flight backtest job manager (one active matrix at a time)."""

    def __init__(self) -> None:
        self._job: BacktestJob | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        if self._job is None:
            return {"status": "idle", "job_id": None, "rows": []}
        return self._job.to_dict()

    async def start(
        self,
        *,
        symbols: list[str],
        timeframes: list[str],
        direction: str = "LONG",
        combination_id: str = "COMBO_02",
        limit: int = 1200,
        risk_usd: float = 20.0,
        principal_usd: float = 1000.0,
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
            raise ValueError("At least one symbol is required")
        if not tfs:
            raise ValueError("At least one supported timeframe is required")

        direction_u = (direction or "LONG").upper().strip()
        if direction_u not in {"LONG", "SHORT"}:
            direction_u = "LONG"
        combo_id = (combination_id or "COMBO_02").upper().strip()
        lim = max(50, min(int(limit), 20000))
        risk = max(1.0, min(float(risk_usd), 10_000.0))
        principal = max(100.0, min(float(principal_usd), 10_000_000.0))
        taker = max(0.0, min(float(taker_fee_pct), 1.0))
        maker = max(0.0, min(float(maker_fee_pct), 1.0))
        start_s = str(start_date).strip()[:10] if start_date else None
        end_s = str(end_date).strip()[:10] if end_date else None

        async with self._lock:
            if self._job is not None and self._job.status == "running":
                raise RuntimeError(
                    f"Backtest already running (job_id={self._job.job_id})"
                )
            job = BacktestJob(
                job_id=uuid.uuid4().hex[:12],
                status="running",
                symbols=syms,
                timeframes=tfs,
                direction=direction_u,
                combination_id=combo_id,
                limit=lim,
                risk_usd=risk,
                principal_usd=principal,
                taker_fee_pct=taker,
                maker_fee_pct=maker,
                include_trades=bool(include_trades),
                start_date=start_s,
                end_date=end_s,
                total_cells=len(syms) * len(tfs),
                started_at=datetime.now(timezone.utc).isoformat(),
                _t0=time.perf_counter(),
            )
            self._job = job
            self._task = asyncio.create_task(
                self._run(job), name=f"backtest_{job.job_id}"
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

    async def _run(self, job: BacktestJob) -> None:
        from app.research.service import get_bos_research_service

        try:
            svc = get_bos_research_service()
            # Match UI cell order: symbol outer, timeframe inner.
            cells = [
                (sym, tf) for sym in job.symbols for tf in job.timeframes
            ]
            for i, (sym, tf) in enumerate(cells):
                if job._cancel:
                    job.status = "cancelled"
                    job.finished_at = datetime.now(timezone.utc).isoformat()
                    job.current = ""
                    return
                # Publish current cell before the long await so UI polls show
                # which cell is running (pct stays at done/total until finish).
                job.current = f"{sym} {tf}"
                # COMBO_02 v1: size each core/secondary cell from the production profile
                cell_risk = float(job.risk_usd)
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
                payload = await svc.strategy_matrix(
                    combination_id=job.combination_id,
                    symbols=[sym],
                    timeframes=[tf],
                    direction=job.direction,
                    limit=job.limit,
                    risk_usd=cell_risk,
                    start_date=job.start_date,
                    end_date=job.end_date,
                    taker_fee=job.taker_fee_pct / 100.0,
                    maker_fee=job.maker_fee_pct / 100.0,
                    include_trades=job.include_trades,
                    should_cancel=lambda: job._cancel,
                )
                if payload.get("status") == "CANCELLED":
                    job.status = "cancelled"
                    job.finished_at = datetime.now(timezone.utc).isoformat()
                    job.current = ""
                    return
                if payload.get("status") == "NOT_FOUND":
                    job.status = "error"
                    job.error = (
                        payload.get("reason")
                        or f"Unknown combination {job.combination_id}"
                    )
                    job.finished_at = datetime.now(timezone.utc).isoformat()
                    job.current = ""
                    return
                if payload.get("status") == "ERROR":
                    job.status = "error"
                    job.error = payload.get("reason") or "Backtest cell failed"
                    job.finished_at = datetime.now(timezone.utc).isoformat()
                    job.current = ""
                    return

                if job.playbook is None:
                    job.playbook = payload.get("playbook")
                    job.combination_name = payload.get("combination_name")
                    job.disclaimer = payload.get("disclaimer")
                    job.label = payload.get("label")

                for row in payload.get("rows") or []:
                    job.rows.append(row)
                job.done_cells = i + 1

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
            job.current = ""


backtest_job_service = BacktestJobService()
