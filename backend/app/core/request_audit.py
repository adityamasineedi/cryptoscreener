from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger("request_audit")


@dataclass
class AuditRecord:
    provider: str
    endpoint: str
    weight: int
    status: int
    latency_ms: float
    retry_count: int = 0
    count_429: int = 0
    at: float = field(default_factory=time.monotonic)


class RequestAudit:
    """Log REST calls and expose rolling stats for the last minute."""

    def __init__(self, window_seconds: float = 60.0) -> None:
        self._window = window_seconds
        self._records: deque[AuditRecord] = deque()
        self._lock = asyncio.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        while self._records and self._records[0].at < cutoff:
            self._records.popleft()

    async def log(
        self,
        *,
        provider: str,
        endpoint: str,
        weight: int,
        status: int,
        latency_ms: float,
        retry_count: int = 0,
        count_429: int = 0,
    ) -> None:
        rec = AuditRecord(
            provider=provider,
            endpoint=endpoint,
            weight=weight,
            status=status,
            latency_ms=latency_ms,
            retry_count=retry_count,
            count_429=count_429,
        )
        async with self._lock:
            self._records.append(rec)
            self._prune(rec.at)
        logger.debug(
            "rest_request",
            provider=provider,
            endpoint=endpoint,
            weight=weight,
            status=status,
            latency_ms=round(latency_ms, 2),
            retry_count=retry_count,
            count_429=count_429,
        )

    async def stats_last_minute(self) -> dict[str, Any]:
        async with self._lock:
            now = time.monotonic()
            self._prune(now)
            recs = list(self._records)
        if not recs:
            return {
                "count": 0,
                "errors": 0,
                "count_429": 0,
                "total_weight": 0,
                "avg_latency_ms": 0.0,
                "by_provider": {},
            }
        errors = sum(1 for r in recs if r.status >= 400)
        count_429 = sum(r.count_429 for r in recs)
        total_weight = sum(r.weight for r in recs)
        avg_lat = sum(r.latency_ms for r in recs) / len(recs)
        by_provider: dict[str, dict[str, Any]] = {}
        for r in recs:
            p = by_provider.setdefault(
                r.provider,
                {"count": 0, "errors": 0, "count_429": 0, "total_weight": 0},
            )
            p["count"] += 1
            if r.status >= 400:
                p["errors"] += 1
            p["count_429"] += r.count_429
            p["total_weight"] += r.weight
        return {
            "count": len(recs),
            "errors": errors,
            "count_429": count_429,
            "total_weight": total_weight,
            "avg_latency_ms": round(avg_lat, 2),
            "by_provider": by_provider,
        }


request_audit = RequestAudit()
