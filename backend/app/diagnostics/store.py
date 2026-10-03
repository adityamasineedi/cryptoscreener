"""In-memory diagnostic store with optional PostgreSQL persistence.

Never invents issues. Deduplicates by fingerprint. Bounded history.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.diagnostics.constants import (
    MAX_EVENTS_PER_FINGERPRINT,
    MAX_IN_MEMORY_EVENTS,
    MAX_IN_MEMORY_ISSUES,
    IssueStatus,
)
from app.diagnostics.fingerprint import issue_fingerprint
from app.diagnostics.ids import new_diagnostic_id
from app.diagnostics.models import DiagnosticEvent, DiagnosticIssue
from app.diagnostics.redact import redact_value
from app.core.logging import get_logger

logger = get_logger("diagnostics.store")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DiagnosticStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._issues: dict[str, DiagnosticIssue] = {}  # by fingerprint
        self._issues_by_id: dict[str, DiagnosticIssue] = {}
        self._events: list[DiagnosticEvent] = []
        self._events_by_fp: dict[str, list[str]] = {}

    async def record_event(
        self,
        *,
        severity: str,
        message: str,
        category: str = "APPLICATION",
        service: str | None = None,
        subsystem: str | None = None,
        component: str | None = None,
        module: str | None = None,
        file: str | None = None,
        function: str | None = None,
        line: int | None = None,
        event_type: str | None = None,
        error_code: str | None = None,
        exception_type: str | None = None,
        stack_trace: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        provider: str | None = None,
        endpoint: str | None = None,
        stream: str | None = None,
        request_id: str | None = None,
        job_id: str | None = None,
        run_id: str | None = None,
        expected: str | None = None,
        actual: str | None = None,
        details: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        reopen_if_resolved: bool = True,
    ) -> tuple[DiagnosticEvent, DiagnosticIssue]:
        """Create/append an event and upsert the deduplicated issue."""
        now = _utcnow()
        safe_details = redact_value(details or {})
        safe_meta = redact_value(metadata or {})
        safe_message = str(redact_value(message) or "")
        safe_stack = redact_value(stack_trace)
        fp = issue_fingerprint(
            service=service,
            component=component,
            error_code=error_code,
            exception_type=exception_type,
            message=safe_message,
            symbol=symbol,
            timeframe=timeframe,
        )
        diagnostic_id = new_diagnostic_id(now)
        event = DiagnosticEvent(
            id=new_diagnostic_id(now),
            diagnostic_id=diagnostic_id,
            timestamp=now,
            severity=severity,
            status=IssueStatus.OPEN.value,
            service=service,
            subsystem=subsystem,
            component=component,
            module=module,
            file=file,
            function=function,
            line=line,
            event_type=event_type,
            error_code=error_code,
            category=category,
            message=safe_message,
            details=safe_details if isinstance(safe_details, dict) else {},
            exception_type=exception_type,
            stack_trace=safe_stack if isinstance(safe_stack, str) else None,
            symbol=symbol,
            timeframe=timeframe,
            provider=provider,
            endpoint=endpoint,
            stream=stream,
            request_id=request_id,
            job_id=job_id,
            run_id=run_id,
            expected=expected,
            actual=actual,
            fingerprint=fp,
            metadata=safe_meta if isinstance(safe_meta, dict) else {},
        )

        async with self._lock:
            issue = self._issues.get(fp)
            if issue is None:
                issue = DiagnosticIssue(
                    id=diagnostic_id,
                    diagnostic_id=diagnostic_id,
                    fingerprint=fp,
                    severity=severity,
                    status=IssueStatus.OPEN.value,
                    category=category,
                    service=service,
                    subsystem=subsystem,
                    component=component,
                    module=module,
                    file=file,
                    function=function,
                    line=line,
                    error_code=error_code,
                    message=safe_message,
                    exception_type=exception_type,
                    stack_trace=event.stack_trace,
                    symbol=symbol,
                    timeframe=timeframe,
                    provider=provider,
                    endpoint=endpoint,
                    stream=stream,
                    request_id=request_id,
                    job_id=job_id,
                    run_id=run_id,
                    expected=expected,
                    actual=actual,
                    first_seen=now,
                    last_seen=now,
                    occurrence_count=1,
                    details=event.details,
                    metadata=event.metadata,
                    recent_event_ids=[event.id],
                )
                self._issues[fp] = issue
                self._issues_by_id[issue.id] = issue
            else:
                issue.last_seen = now
                issue.occurrence_count += 1
                issue.severity = severity
                issue.message = safe_message
                if exception_type:
                    issue.exception_type = exception_type
                if event.stack_trace:
                    issue.stack_trace = event.stack_trace
                if file:
                    issue.file = file
                if function:
                    issue.function = function
                if line is not None:
                    issue.line = line
                if expected is not None:
                    issue.expected = expected
                if actual is not None:
                    issue.actual = actual
                issue.details = event.details
                issue.recent_event_ids.append(event.id)
                if len(issue.recent_event_ids) > MAX_EVENTS_PER_FINGERPRINT:
                    issue.recent_event_ids = issue.recent_event_ids[
                        -MAX_EVENTS_PER_FINGERPRINT:
                    ]
                # Reopen resolved issues when the same fingerprint recurs.
                if (
                    reopen_if_resolved
                    and issue.status == IssueStatus.RESOLVED.value
                ):
                    issue.status = IssueStatus.OPEN.value
                    issue.resolved_at = None
                    issue.resolution_message = None
                elif issue.status == IssueStatus.SUPPRESSED.value:
                    pass  # stay suppressed
                elif issue.status != IssueStatus.ACKNOWLEDGED.value:
                    issue.status = IssueStatus.OPEN.value
                # Keep diagnostic_id stable for the issue; event has its own.
                event.diagnostic_id = issue.diagnostic_id

            self._events.append(event)
            self._events_by_fp.setdefault(fp, []).append(event.id)
            if len(self._events_by_fp[fp]) > MAX_EVENTS_PER_FINGERPRINT:
                self._events_by_fp[fp] = self._events_by_fp[fp][
                    -MAX_EVENTS_PER_FINGERPRINT:
                ]
            if len(self._events) > MAX_IN_MEMORY_EVENTS:
                self._events = self._events[-MAX_IN_MEMORY_EVENTS:]
            if len(self._issues) > MAX_IN_MEMORY_ISSUES:
                # Drop oldest resolved first
                resolved = sorted(
                    (
                        i
                        for i in self._issues.values()
                        if i.status == IssueStatus.RESOLVED.value
                    ),
                    key=lambda x: x.last_seen,
                )
                for drop in resolved[: max(0, len(self._issues) - MAX_IN_MEMORY_ISSUES)]:
                    self._issues.pop(drop.fingerprint, None)
                    self._issues_by_id.pop(drop.id, None)

            issue_out = issue

        await self._persist_event_and_issue(event, issue_out)
        return event, issue_out

    async def list_issues(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        component: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[DiagnosticIssue]:
        async with self._lock:
            items = list(self._issues.values())
        if status:
            items = [i for i in items if i.status == status]
        if severity:
            items = [i for i in items if i.severity == severity]
        if component:
            items = [
                i
                for i in items
                if (i.component or "").lower() == component.lower()
            ]
        items.sort(key=lambda i: i.last_seen, reverse=True)
        return items[offset : offset + limit]

    async def get_issue(self, issue_id: str) -> DiagnosticIssue | None:
        async with self._lock:
            hit = self._issues_by_id.get(issue_id)
            if hit:
                return hit
            for issue in self._issues.values():
                if (
                    issue.id == issue_id
                    or issue.diagnostic_id == issue_id
                    or issue.fingerprint == issue_id
                ):
                    return issue
        return None

    async def get_events_for_issue(
        self, issue: DiagnosticIssue, *, limit: int = 50
    ) -> list[DiagnosticEvent]:
        async with self._lock:
            ids = set(issue.recent_event_ids[-limit:])
            events = [e for e in self._events if e.id in ids or e.fingerprint == issue.fingerprint]
        events.sort(key=lambda e: e.timestamp, reverse=True)
        return events[:limit]

    async def acknowledge(
        self, issue_id: str, *, owner: str | None = None
    ) -> DiagnosticIssue | None:
        issue = await self.get_issue(issue_id)
        if issue is None:
            return None
        async with self._lock:
            issue.status = IssueStatus.ACKNOWLEDGED.value
            if owner:
                issue.owner = owner
        await self._persist_issue(issue)
        return issue

    async def resolve(
        self, issue_id: str, *, message: str | None = None
    ) -> DiagnosticIssue | None:
        issue = await self.get_issue(issue_id)
        if issue is None:
            return None
        async with self._lock:
            issue.status = IssueStatus.RESOLVED.value
            issue.resolved_at = _utcnow()
            issue.resolution_message = message
        await self._persist_issue(issue)
        return issue

    async def suppress(self, issue_id: str) -> DiagnosticIssue | None:
        issue = await self.get_issue(issue_id)
        if issue is None:
            return None
        async with self._lock:
            issue.status = IssueStatus.SUPPRESSED.value
        await self._persist_issue(issue)
        return issue

    async def open_issue_count(self) -> dict[str, int]:
        async with self._lock:
            counts = {"OPEN": 0, "ACKNOWLEDGED": 0, "RESOLVED": 0, "SUPPRESSED": 0}
            by_sev = {"CRITICAL": 0, "ERROR": 0, "WARNING": 0, "INFO": 0}
            for i in self._issues.values():
                counts[i.status] = counts.get(i.status, 0) + 1
                if i.status in (IssueStatus.OPEN.value, IssueStatus.ACKNOWLEDGED.value):
                    by_sev[i.severity] = by_sev.get(i.severity, 0) + 1
        return {"by_status": counts, "open_by_severity": by_sev}

    async def _persist_event_and_issue(
        self, event: DiagnosticEvent, issue: DiagnosticIssue
    ) -> None:
        from app.services.database import db_manager

        if not db_manager.enabled or db_manager.engine is None:
            return
        try:
            async with db_manager.engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        INSERT INTO diagnostic_issues (
                            id, diagnostic_id, fingerprint, severity, status, category,
                            service, subsystem, component, module, file, function, line,
                            error_code, message, exception_type, stack_trace,
                            symbol, timeframe, provider, endpoint, stream,
                            request_id, job_id, run_id, expected, actual,
                            first_seen, last_seen, occurrence_count,
                            resolved_at, resolution_message, owner, details, metadata
                        ) VALUES (
                            :id, :diagnostic_id, :fingerprint, :severity, :status, :category,
                            :service, :subsystem, :component, :module, :file, :function, :line,
                            :error_code, :message, :exception_type, :stack_trace,
                            :symbol, :timeframe, :provider, :endpoint, :stream,
                            :request_id, :job_id, :run_id, :expected, :actual,
                            :first_seen, :last_seen, :occurrence_count,
                            :resolved_at, :resolution_message, :owner,
                            CAST(:details AS jsonb), CAST(:metadata AS jsonb)
                        )
                        ON CONFLICT (fingerprint) DO UPDATE SET
                            severity = EXCLUDED.severity,
                            status = EXCLUDED.status,
                            message = EXCLUDED.message,
                            exception_type = COALESCE(EXCLUDED.exception_type, diagnostic_issues.exception_type),
                            stack_trace = COALESCE(EXCLUDED.stack_trace, diagnostic_issues.stack_trace),
                            file = COALESCE(EXCLUDED.file, diagnostic_issues.file),
                            function = COALESCE(EXCLUDED.function, diagnostic_issues.function),
                            line = COALESCE(EXCLUDED.line, diagnostic_issues.line),
                            expected = COALESCE(EXCLUDED.expected, diagnostic_issues.expected),
                            actual = COALESCE(EXCLUDED.actual, diagnostic_issues.actual),
                            last_seen = EXCLUDED.last_seen,
                            occurrence_count = EXCLUDED.occurrence_count,
                            resolved_at = EXCLUDED.resolved_at,
                            resolution_message = EXCLUDED.resolution_message,
                            details = EXCLUDED.details,
                            metadata = EXCLUDED.metadata
                        """
                    ),
                    {
                        "id": issue.id,
                        "diagnostic_id": issue.diagnostic_id,
                        "fingerprint": issue.fingerprint,
                        "severity": issue.severity,
                        "status": issue.status,
                        "category": issue.category,
                        "service": issue.service,
                        "subsystem": issue.subsystem,
                        "component": issue.component,
                        "module": issue.module,
                        "file": issue.file,
                        "function": issue.function,
                        "line": issue.line,
                        "error_code": issue.error_code,
                        "message": issue.message,
                        "exception_type": issue.exception_type,
                        "stack_trace": issue.stack_trace,
                        "symbol": issue.symbol,
                        "timeframe": issue.timeframe,
                        "provider": issue.provider,
                        "endpoint": issue.endpoint,
                        "stream": issue.stream,
                        "request_id": issue.request_id,
                        "job_id": issue.job_id,
                        "run_id": issue.run_id,
                        "expected": issue.expected,
                        "actual": issue.actual,
                        "first_seen": issue.first_seen,
                        "last_seen": issue.last_seen,
                        "occurrence_count": issue.occurrence_count,
                        "resolved_at": issue.resolved_at,
                        "resolution_message": issue.resolution_message,
                        "owner": issue.owner,
                        "details": json.dumps(issue.details),
                        "metadata": json.dumps(issue.metadata),
                    },
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO diagnostic_events (
                            id, diagnostic_id, timestamp, severity, status,
                            service, subsystem, component, module, file, function, line,
                            event_type, error_code, category, message, details,
                            exception_type, stack_trace, symbol, timeframe,
                            provider, endpoint, stream, request_id, job_id, run_id,
                            expected, actual, fingerprint, metadata
                        ) VALUES (
                            :id, :diagnostic_id, :timestamp, :severity, :status,
                            :service, :subsystem, :component, :module, :file, :function, :line,
                            :event_type, :error_code, :category, :message, CAST(:details AS jsonb),
                            :exception_type, :stack_trace, :symbol, :timeframe,
                            :provider, :endpoint, :stream, :request_id, :job_id, :run_id,
                            :expected, :actual, :fingerprint, CAST(:metadata AS jsonb)
                        )
                        ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {
                        "id": event.id,
                        "diagnostic_id": event.diagnostic_id,
                        "timestamp": event.timestamp,
                        "severity": event.severity,
                        "status": event.status,
                        "service": event.service,
                        "subsystem": event.subsystem,
                        "component": event.component,
                        "module": event.module,
                        "file": event.file,
                        "function": event.function,
                        "line": event.line,
                        "event_type": event.event_type,
                        "error_code": event.error_code,
                        "category": event.category,
                        "message": event.message,
                        "details": json.dumps(event.details),
                        "exception_type": event.exception_type,
                        "stack_trace": event.stack_trace,
                        "symbol": event.symbol,
                        "timeframe": event.timeframe,
                        "provider": event.provider,
                        "endpoint": event.endpoint,
                        "stream": event.stream,
                        "request_id": event.request_id,
                        "job_id": event.job_id,
                        "run_id": event.run_id,
                        "expected": event.expected,
                        "actual": event.actual,
                        "fingerprint": event.fingerprint,
                        "metadata": json.dumps(event.metadata),
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("diagnostic_persist_failed", error=str(exc))

    async def _persist_issue(self, issue: DiagnosticIssue) -> None:
        from app.services.database import db_manager

        if not db_manager.enabled or db_manager.engine is None:
            return
        try:
            async with db_manager.engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        UPDATE diagnostic_issues SET
                            status = :status,
                            owner = :owner,
                            resolved_at = :resolved_at,
                            resolution_message = :resolution_message,
                            last_seen = :last_seen
                        WHERE fingerprint = :fingerprint
                        """
                    ),
                    {
                        "status": issue.status,
                        "owner": issue.owner,
                        "resolved_at": issue.resolved_at,
                        "resolution_message": issue.resolution_message,
                        "last_seen": issue.last_seen,
                        "fingerprint": issue.fingerprint,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("diagnostic_issue_update_failed", error=str(exc))


diagnostic_store = DiagnosticStore()
