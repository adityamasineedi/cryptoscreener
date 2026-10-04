"""Resumable research-run checkpoints (symbol × timeframe cells)."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.research.data_cache.config import ResearchCacheConfig, load_research_cache_config

CellStatus = Literal[
    "PENDING",
    "LOADING",
    "FEATURES_READY",
    "EVENTS_READY",
    "BACKTESTING",
    "COMPLETED",
    "FAILED",
]


@dataclass
class CellCheckpoint:
    symbol: str
    timeframe: str
    status: CellStatus = "PENDING"
    dataset_version: str = ""
    strategy_version: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    result_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunCheckpoint:
    run_id: str
    dataset_version: str
    strategy_version: str
    symbols: list[str]
    timeframes: list[str]
    start_time: str | None
    end_time: str | None
    cells: dict[str, CellCheckpoint] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metrics: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def cell_key(symbol: str, timeframe: str) -> str:
        return f"{symbol.upper()}|{timeframe.lower()}"

    def ensure_cells(self) -> None:
        for sym in self.symbols:
            for tf in self.timeframes:
                k = self.cell_key(sym, tf)
                if k not in self.cells:
                    self.cells[k] = CellCheckpoint(
                        symbol=sym.upper(),
                        timeframe=tf.lower(),
                        dataset_version=self.dataset_version,
                        strategy_version=self.strategy_version,
                    )

    def pending_cells(self) -> list[CellCheckpoint]:
        self.ensure_cells()
        return [c for c in self.cells.values() if c.status not in ("COMPLETED",)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "dataset_version": self.dataset_version,
            "strategy_version": self.strategy_version,
            "symbols": self.symbols,
            "timeframes": self.timeframes,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "created_at": self.created_at,
            "metrics": self.metrics,
            "cells": {k: v.to_dict() for k, v in self.cells.items()},
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RunCheckpoint:
        cells_raw = raw.get("cells") or {}
        cells = {
            k: CellCheckpoint(**v) if isinstance(v, dict) else v
            for k, v in cells_raw.items()
        }
        return cls(
            run_id=str(raw["run_id"]),
            dataset_version=str(raw.get("dataset_version") or ""),
            strategy_version=str(raw.get("strategy_version") or ""),
            symbols=list(raw.get("symbols") or []),
            timeframes=list(raw.get("timeframes") or []),
            start_time=raw.get("start_time"),
            end_time=raw.get("end_time"),
            cells=cells,
            created_at=str(raw.get("created_at") or datetime.now(timezone.utc).isoformat()),
            metrics=dict(raw.get("metrics") or {}),
        )


class CheckpointStore:
    def __init__(self, config: ResearchCacheConfig | None = None) -> None:
        self.config = config or load_research_cache_config()

    def path_for(self, run_id: str) -> Path:
        return self.config.runs_dir() / f"{run_id}.checkpoint.json"

    def create(
        self,
        *,
        symbols: list[str],
        timeframes: list[str],
        start_time: str | None,
        end_time: str | None,
        dataset_version: str,
        strategy_version: str,
        run_id: str | None = None,
    ) -> RunCheckpoint:
        rid = run_id or uuid.uuid4().hex[:12]
        cp = RunCheckpoint(
            run_id=rid,
            dataset_version=dataset_version,
            strategy_version=strategy_version,
            symbols=[s.upper() for s in symbols],
            timeframes=[t.lower() for t in timeframes],
            start_time=start_time,
            end_time=end_time,
        )
        cp.ensure_cells()
        self.save(cp)
        return cp

    def save(self, cp: RunCheckpoint) -> None:
        path = self.path_for(cp.run_id)
        path.write_text(json.dumps(cp.to_dict(), indent=2, sort_keys=True), encoding="utf-8")

    def load(self, run_id: str) -> RunCheckpoint | None:
        path = self.path_for(run_id)
        if not path.is_file():
            return None
        try:
            return RunCheckpoint.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError):
            return None

    def mark(
        self,
        cp: RunCheckpoint,
        symbol: str,
        timeframe: str,
        status: CellStatus,
        *,
        error: str | None = None,
        result_summary: dict[str, Any] | None = None,
    ) -> RunCheckpoint:
        k = RunCheckpoint.cell_key(symbol, timeframe)
        cell = cp.cells.get(k) or CellCheckpoint(
            symbol=symbol.upper(), timeframe=timeframe.lower()
        )
        now = datetime.now(timezone.utc).isoformat()
        if cell.started_at is None and status != "PENDING":
            cell.started_at = now
        cell.status = status
        if status == "COMPLETED":
            cell.completed_at = now
            cell.error = None
        if status == "FAILED":
            cell.error = error or "failed"
            cell.completed_at = now
        if result_summary is not None:
            cell.result_summary = result_summary
        cp.cells[k] = cell
        self.save(cp)
        return cp
