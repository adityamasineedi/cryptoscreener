"""Adaptive concurrency controller — never blindly raises request rates."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdaptiveRateController:
    """
    Adjusts effective concurrency and pause from observed exchange health.

    Healthy  → gradually increase concurrency (up to max)
    429      → reduce concurrency, lengthen pause
    5xx/timeout → backoff
    Stable again → slowly recover
    """

    min_concurrency: int = 1
    max_concurrency: int = 4
    base_pause_seconds: float = 0.35
    max_pause_seconds: float = 8.0
    window: int = 40

    concurrency: int = field(init=False)
    pause_seconds: float = field(init=False)
    _latencies_ms: deque[float] = field(default_factory=deque, init=False)
    _outcomes: deque[str] = field(default_factory=deque, init=False)
    _success_times: deque[float] = field(default_factory=deque, init=False)
    _last_adjust: float = field(default=0.0, init=False)
    _backoff_until: float = field(default=0.0, init=False)

    count_429: int = 0
    count_5xx: int = 0
    count_timeout: int = 0
    count_success: int = 0

    def __post_init__(self) -> None:
        self.concurrency = max(1, min(self.min_concurrency, self.max_concurrency))
        self.pause_seconds = self.base_pause_seconds
        self._latencies_ms = deque(maxlen=self.window)
        self._outcomes = deque(maxlen=self.window)
        self._success_times = deque(maxlen=200)

    def record(
        self,
        *,
        latency_ms: float | None = None,
        outcome: str = "success",
        weight_used: float | None = None,
    ) -> None:
        """outcome: success | 429 | 5xx | timeout | error"""
        del weight_used  # reserved for exchange weight telemetry
        if latency_ms is not None:
            self._latencies_ms.append(float(latency_ms))
        self._outcomes.append(outcome)
        now = time.monotonic()
        if outcome == "success":
            self.count_success += 1
            self._success_times.append(now)
        elif outcome == "429":
            self.count_429 += 1
            self._on_rate_limit()
        elif outcome == "5xx":
            self.count_5xx += 1
            self._on_server_pressure()
        elif outcome == "timeout":
            self.count_timeout += 1
            self._on_server_pressure()
        else:
            self._on_server_pressure()
        self.maybe_adjust()

    def _on_rate_limit(self) -> None:
        self.concurrency = max(self.min_concurrency, self.concurrency - 1)
        self.pause_seconds = min(self.max_pause_seconds, self.pause_seconds * 1.8 + 0.2)
        self._backoff_until = time.monotonic() + min(30.0, self.pause_seconds * 3)

    def _on_server_pressure(self) -> None:
        self.concurrency = max(self.min_concurrency, self.concurrency - 1)
        self.pause_seconds = min(self.max_pause_seconds, self.pause_seconds * 1.5 + 0.15)
        self._backoff_until = time.monotonic() + min(45.0, self.pause_seconds * 4)

    def maybe_adjust(self) -> None:
        now = time.monotonic()
        if now < self._backoff_until:
            return
        if now - self._last_adjust < 5.0:
            return
        if len(self._outcomes) < 8:
            return
        recent = list(self._outcomes)[-20:]
        n429 = sum(1 for o in recent if o == "429")
        n5xx = sum(1 for o in recent if o in ("5xx", "timeout"))
        n_ok = sum(1 for o in recent if o == "success")
        avg_lat = (
            sum(self._latencies_ms) / len(self._latencies_ms) if self._latencies_ms else 0.0
        )
        self._last_adjust = now
        if n429 > 0:
            self._on_rate_limit()
            return
        if n5xx >= 2:
            self._on_server_pressure()
            return
        # Healthy: high success ratio, moderate latency → slowly recover
        if n_ok >= 8 and avg_lat < 1500:
            if self.concurrency < self.max_concurrency:
                self.concurrency += 1
            self.pause_seconds = max(
                self.base_pause_seconds, self.pause_seconds * 0.85
            )

    def in_backoff(self) -> bool:
        return time.monotonic() < self._backoff_until

    def successes_per_minute(self) -> float:
        now = time.monotonic()
        cutoff = now - 60.0
        while self._success_times and self._success_times[0] < cutoff:
            self._success_times.popleft()
        return float(len(self._success_times))

    def snapshot(self) -> dict[str, Any]:
        avg_lat = (
            round(sum(self._latencies_ms) / len(self._latencies_ms), 1)
            if self._latencies_ms
            else None
        )
        return {
            "concurrency": self.concurrency,
            "pause_seconds": round(self.pause_seconds, 3),
            "avg_latency_ms": avg_lat,
            "successes_per_min": self.successes_per_minute(),
            "count_429": self.count_429,
            "count_5xx": self.count_5xx,
            "count_timeout": self.count_timeout,
            "count_success": self.count_success,
            "in_backoff": self.in_backoff(),
            "min_concurrency": self.min_concurrency,
            "max_concurrency": self.max_concurrency,
        }
