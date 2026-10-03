"""Bounded diagnostic endpoint latency tracker."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any


class EndpointLatencyTracker:
    def __init__(self, maxlen: int = 100) -> None:
        self._samples: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=maxlen)
        )

    def record(self, endpoint: str, ms: float) -> None:
        self._samples[endpoint].append(float(ms))

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for ep, samples in self._samples.items():
            vals = list(samples)
            if not vals:
                continue
            ordered = sorted(vals)
            p95 = None
            if len(ordered) >= 5:
                idx = min(
                    len(ordered) - 1,
                    max(0, int(round(0.95 * (len(ordered) - 1)))),
                )
                p95 = round(ordered[idx], 2)
            out[ep] = {
                "count": len(vals),
                "avg_ms": round(sum(vals) / len(vals), 2),
                "p95_ms": p95,
                "last_ms": round(vals[-1], 2),
            }
        return out


latency_tracker = EndpointLatencyTracker()


class timed:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self._t0 = 0.0

    def __enter__(self) -> timed:
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *args: Any) -> None:
        ms = (time.perf_counter() - self._t0) * 1000
        latency_tracker.record(self.endpoint, ms)
