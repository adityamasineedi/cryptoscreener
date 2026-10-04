"""Structured timing / progress events for UI backtest jobs.

Research-only observability. Does not alter strategy / risk / fee calculations.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger("app.research.backtest_timing")

ProgressCallback = Callable[[dict[str, Any]], None]

# Canonical phase names (spec §2).
JOB_CREATED = "JOB_CREATED"
DB_QUERY_START = "DB_QUERY_START"
DB_QUERY_END = "DB_QUERY_END"
RAW_ROWS_LOADED = "RAW_ROWS_LOADED"
DATE_FILTER_START = "DATE_FILTER_START"
DATE_FILTER_END = "DATE_FILTER_END"
TIMEFRAME_NORMALIZATION_START = "TIMEFRAME_NORMALIZATION_START"
TIMEFRAME_NORMALIZATION_END = "TIMEFRAME_NORMALIZATION_END"
HTF_DATA_LOAD_START = "HTF_DATA_LOAD_START"
HTF_DATA_LOAD_END = "HTF_DATA_LOAD_END"
HTF_PRECOMPUTE_START = "HTF_PRECOMPUTE_START"
HTF_PRECOMPUTE_END = "HTF_PRECOMPUTE_END"
STRUCTURE_SCAN_START = "STRUCTURE_SCAN_START"
STRUCTURE_SCAN_HEARTBEAT = "STRUCTURE_SCAN_HEARTBEAT"
SIGNAL_GENERATION_END = "SIGNAL_GENERATION_END"
BACKTEST_START = "BACKTEST_START"
BACKTEST_HEARTBEAT = "BACKTEST_HEARTBEAT"
BACKTEST_END = "BACKTEST_END"
METRICS_START = "METRICS_START"
METRICS_END = "METRICS_END"
JOB_COMPLETED = "JOB_COMPLETED"
JOB_FAILED = "JOB_FAILED"
JOB_STALLED = "JOB_STALLED"
JOB_CANCELLED = "JOB_CANCELLED"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit_phase(
    phase: str,
    *,
    job_id: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    elapsed_seconds: float = 0.0,
    rows_loaded: int = 0,
    bars_processed: int = 0,
    total_bars: int = 0,
    trades: int = 0,
    **extra: Any,
) -> dict[str, Any]:
    """Emit one structured timing event (JSON log line)."""
    event: dict[str, Any] = {
        "job_id": job_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "phase": phase,
        "elapsed_seconds": round(float(elapsed_seconds), 6),
        "rows_loaded": int(rows_loaded),
        "bars_processed": int(bars_processed),
        "total_bars": int(total_bars),
        "trades": int(trades),
        "ts": utc_now_iso(),
    }
    if extra:
        event.update(extra)
    logger.info("backtest_phase %s", json.dumps(event, default=str))
    return event


class PhaseTimer:
    """Simple wall-clock helper for diagnostic scripts / job phases."""

    def __init__(self) -> None:
        self.t0 = time.perf_counter()
        self.marks: dict[str, float] = {}

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    def mark(self, name: str) -> float:
        now = self.elapsed()
        self.marks[name] = now
        return now

    def since(self, name: str) -> float:
        return self.elapsed() - float(self.marks.get(name, 0.0))
