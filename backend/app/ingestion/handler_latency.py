"""Measure WS-path handler durations (diagnostic only — does not move handlers)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _HandlerStats:
    count: int = 0
    errors: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=2000))

    def observe(self, ms: float, *, error: bool = False) -> None:
        self.count += 1
        if error:
            self.errors += 1
        self.total_ms += ms
        if ms > self.max_ms:
            self.max_ms = ms
        self.samples.append(ms)

    def snapshot(self) -> dict[str, Any]:
        ordered = sorted(self.samples)
        def pct(p: float) -> float | None:
            if not ordered:
                return None
            if len(ordered) == 1:
                return round(ordered[0], 3)
            idx = min(
                len(ordered) - 1,
                max(0, int(round((p / 100.0) * (len(ordered) - 1)))),
            )
            return round(ordered[idx], 3)

        return {
            "count": self.count,
            "errors": self.errors,
            "avg_ms": round(self.total_ms / self.count, 3) if self.count else None,
            "p95_ms": pct(95),
            "p99_ms": pct(99),
            "max_ms": round(self.max_ms, 3) if self.count else None,
        }


class HandlerLatencyTracker:
    def __init__(self) -> None:
        self._stats: dict[str, _HandlerStats] = defaultdict(_HandlerStats)

    def time(self, name: str):
        tracker = self

        class _Ctx:
            def __init__(self) -> None:
                self.t0 = 0.0
                self.error = False

            def __enter__(self):
                self.t0 = time.perf_counter()
                return self

            def __exit__(self, exc_type, exc, tb):
                ms = (time.perf_counter() - self.t0) * 1000.0
                tracker._stats[name].observe(ms, error=exc_type is not None)
                return False

        return _Ctx()

    def snapshot(self) -> dict[str, Any]:
        return {name: st.snapshot() for name, st in sorted(self._stats.items())}


handler_latency = HandlerLatencyTracker()
