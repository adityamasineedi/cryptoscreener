"""Convert collector issue candidates into deduplicated diagnostic issues."""

from __future__ import annotations

from typing import Any

from app.diagnostics.log_buffer import diagnostic_log_buffer
from app.diagnostics.store import diagnostic_store


async def ingest_candidates(
    candidates: list[dict[str, Any]],
    *,
    service: str,
    component: str,
    category: str,
    file: str,
    function: str,
    line: int | None = None,
) -> list[dict[str, Any]]:
    """Record measurable issue candidates. Never invents root cause."""
    created: list[dict[str, Any]] = []
    for c in candidates or []:
        error_code = c.get("error_code") or "DIAGNOSTIC"
        severity = c.get("severity") or "WARNING"
        message = c.get("message") or error_code
        details = c.get("details") if isinstance(c.get("details"), dict) else {}
        if c.get("expected") is not None:
            details.setdefault("threshold_expected", c.get("expected"))
        if c.get("actual") is not None:
            details.setdefault("threshold_actual", c.get("actual"))
        event, issue = await diagnostic_store.record_event(
            severity=str(severity),
            message=str(message),
            category=category,
            service=service,
            subsystem=c.get("subsystem") or component,
            component=c.get("component") or component,
            module="diagnostics.collectors",
            file=file,
            function=function,
            line=line,
            event_type="collector_issue",
            error_code=str(error_code),
            symbol=c.get("symbol"),
            timeframe=c.get("timeframe"),
            provider=c.get("provider"),
            endpoint=c.get("endpoint"),
            job_id=c.get("job_id"),
            run_id=c.get("run_id"),
            expected=c.get("expected"),
            actual=c.get("actual"),
            details=details,
        )
        await diagnostic_log_buffer.emit(
            severity=str(severity),
            message=str(message),
            service=service,
            component=component,
            provider=c.get("provider"),
            symbol=c.get("symbol"),
            timeframe=c.get("timeframe"),
            diagnostic_id=issue.diagnostic_id,
            job_id=c.get("job_id"),
            run_id=c.get("run_id"),
            details={"error_code": error_code, "event_id": event.id},
        )
        created.append(issue.to_dict())
    return created
