"""Bounded in-memory diagnostics log ring — never loads whole log files."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.diagnostics.redact import redact_value

MAX_LOG_ENTRIES = 5000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class LogEntry:
    id: str
    timestamp: datetime
    severity: str
    service: str | None = None
    component: str | None = None
    provider: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    diagnostic_id: str | None = None
    request_id: str | None = None
    job_id: str | None = None
    run_id: str | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


class DiagnosticLogBuffer:
    def __init__(self, maxlen: int = MAX_LOG_ENTRIES) -> None:
        self._entries: deque[LogEntry] = deque(maxlen=maxlen)
        self._lock = asyncio.Lock()
        self._seq = 0

    async def emit(
        self,
        *,
        severity: str,
        message: str,
        service: str | None = None,
        component: str | None = None,
        provider: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        diagnostic_id: str | None = None,
        request_id: str | None = None,
        job_id: str | None = None,
        run_id: str | None = None,
        details: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
        entry_id: str | None = None,
    ) -> LogEntry:
        from app.diagnostics.ids import new_diagnostic_id

        safe_details = redact_value(details or {})
        if not isinstance(safe_details, dict):
            safe_details = {}
        entry = LogEntry(
            id=entry_id or new_diagnostic_id(),
            timestamp=timestamp or _utcnow(),
            severity=(severity or "INFO").upper(),
            service=service,
            component=component,
            provider=provider,
            symbol=symbol,
            timeframe=timeframe,
            diagnostic_id=diagnostic_id,
            request_id=request_id,
            job_id=job_id,
            run_id=run_id,
            message=str(redact_value(message) or ""),
            details=safe_details,
        )
        async with self._lock:
            self._seq += 1
            self._entries.append(entry)
        return entry

    async def query(
        self,
        *,
        severity: str | None = None,
        service: str | None = None,
        component: str | None = None,
        provider: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        diagnostic_id: str | None = None,
        request_id: str | None = None,
        job_id: str | None = None,
        run_id: str | None = None,
        text: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        async with self._lock:
            items = list(self._entries)

        def _match(e: LogEntry) -> bool:
            if severity and e.severity != severity.upper():
                return False
            if service and (e.service or "").lower() != service.lower():
                return False
            if component and (e.component or "").lower() != component.lower():
                return False
            if provider and (e.provider or "").lower() != provider.lower():
                return False
            if symbol and (e.symbol or "").upper() != symbol.upper():
                return False
            if timeframe and (e.timeframe or "").lower() != timeframe.lower():
                return False
            if diagnostic_id and e.diagnostic_id != diagnostic_id:
                return False
            if request_id and e.request_id != request_id:
                return False
            if job_id and e.job_id != job_id:
                return False
            if run_id and e.run_id != run_id:
                return False
            if since and e.timestamp < since:
                return False
            if until and e.timestamp > until:
                return False
            if text:
                hay = f"{e.message} {e.component or ''} {e.service or ''}".lower()
                if text.lower() not in hay:
                    return False
            return True

        matched = [e for e in reversed(items) if _match(e)]
        total = len(matched)
        page = matched[offset : offset + max(1, min(limit, 500))]
        return {
            "count": len(page),
            "total_matched": total,
            "offset": offset,
            "limit": limit,
            "buffer_size": len(items),
            "buffer_capacity": self._entries.maxlen,
            "entries": [e.to_dict() for e in page],
            "status": "HEALTHY" if items or True else "UNKNOWN",
            "reason": "Bounded in-memory diagnostics log (not full application stdout)",
        }

    async def correlate(
        self,
        *,
        diagnostic_id: str | None = None,
        request_id: str | None = None,
        job_id: str | None = None,
        run_id: str | None = None,
        component: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        # Prefer ID correlation over fuzzy matching
        if diagnostic_id or request_id or job_id or run_id:
            res = await self.query(
                diagnostic_id=diagnostic_id,
                request_id=request_id,
                job_id=job_id,
                run_id=run_id,
                since=since,
                until=until,
                limit=limit,
            )
            return list(res.get("entries") or [])
        if component:
            res = await self.query(
                component=component, since=since, until=until, limit=limit
            )
            return list(res.get("entries") or [])
        return []


diagnostic_log_buffer = DiagnosticLogBuffer()
