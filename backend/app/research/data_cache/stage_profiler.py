"""Optional research-only CPU stage timers (default OFF).

Enable with RESEARCH_PROFILE_ENABLED=true.

Does not alter strategy results — only aggregates wall times / call counts.
Never logs per-candle; callers must aggregate.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


def profile_enabled() -> bool:
    raw = (os.getenv("RESEARCH_PROFILE_ENABLED") or "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass
class _StageStats:
    calls: int = 0
    elapsed_ms: float = 0.0


@dataclass
class StageProfiler:
    """Thread-safe aggregated stage timings for one research run."""

    enabled: bool = False
    meta: dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _stages: dict[str, _StageStats] = field(default_factory=dict, repr=False)

    def reset(self, **meta: Any) -> None:
        with self._lock:
            self._stages.clear()
            self.meta = dict(meta)
            self.enabled = profile_enabled()

    def add(self, stage: str, elapsed_ms: float, *, calls: int = 1) -> None:
        if not self.enabled:
            return
        with self._lock:
            st = self._stages.setdefault(stage, _StageStats())
            st.calls += int(calls)
            st.elapsed_ms += float(elapsed_ms)

    @contextmanager
    def time(self, stage: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.add(stage, (time.perf_counter() - t0) * 1000.0)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            stages = {
                k: {
                    "calls": v.calls,
                    "elapsed_ms": round(v.elapsed_ms, 3),
                    "elapsed_seconds": round(v.elapsed_ms / 1000.0, 6),
                }
                for k, v in sorted(self._stages.items())
            }
            total_ms = sum(v.elapsed_ms for v in self._stages.values())
            ranked = sorted(
                (
                    {
                        "stage": k,
                        "calls": v["calls"],
                        "elapsed_ms": v["elapsed_ms"],
                        "pct_of_staged": round(
                            100.0 * v["elapsed_ms"] / total_ms, 1
                        )
                        if total_ms > 0
                        else 0.0,
                    }
                    for k, v in stages.items()
                ),
                key=lambda r: r["elapsed_ms"],
                reverse=True,
            )
            return {
                "enabled": self.enabled,
                "meta": dict(self.meta),
                "stages": stages,
                "ranked": ranked,
                "total_staged_ms": round(total_ms, 3),
            }


# Process-wide research profiler (safe default: disabled until reset/enabled).
research_stage_profiler = StageProfiler(enabled=False)


def ensure_profiler(**meta: Any) -> StageProfiler:
    """Enable/reset profiler from env for a new measurement run."""
    research_stage_profiler.reset(**meta)
    return research_stage_profiler
