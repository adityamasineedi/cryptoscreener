"""Normalized diagnostic event / issue models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.diagnostics.constants import ErrorCategory, HealthStatus, IssueStatus, Severity
from app.diagnostics.ids import new_diagnostic_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


@dataclass
class LocationInfo:
    service: str | None = None
    subsystem: str | None = None
    component: str | None = None
    module: str | None = None
    file: str | None = None
    function: str | None = None
    line: int | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    stack_trace: str | None = None
    request_id: str | None = None
    job_id: str | None = None
    run_id: str | None = None
    trace_id: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    provider: str | None = None
    database_table: str | None = None
    endpoint: str | None = None
    stream: str | None = None
    worker_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ComponentHealth:
    key: str
    label: str
    status: str
    reason: str | None = None
    last_checked: str | None = None
    last_success: str | None = None
    latency_ms: float | None = None
    error_count: int | None = None
    stale_seconds: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "reason": self.reason,
            "last_checked": self.last_checked,
            "last_success": self.last_success,
            "latency_ms": self.latency_ms,
            "error_count": self.error_count,
            "stale_seconds": self.stale_seconds,
            "metrics": self.metrics,
        }


@dataclass
class DiagnosticEvent:
    id: str
    diagnostic_id: str
    timestamp: datetime
    severity: str
    status: str
    service: str | None = None
    subsystem: str | None = None
    component: str | None = None
    module: str | None = None
    file: str | None = None
    function: str | None = None
    line: int | None = None
    event_type: str | None = None
    error_code: str | None = None
    category: str = ErrorCategory.APPLICATION.value
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    exception_type: str | None = None
    stack_trace: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    provider: str | None = None
    endpoint: str | None = None
    stream: str | None = None
    request_id: str | None = None
    job_id: str | None = None
    run_id: str | None = None
    expected: str | None = None
    actual: str | None = None
    fingerprint: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = _iso(self.timestamp)
        return d


@dataclass
class DiagnosticIssue:
    id: str
    diagnostic_id: str
    fingerprint: str
    severity: str
    status: str
    category: str
    service: str | None = None
    subsystem: str | None = None
    component: str | None = None
    module: str | None = None
    file: str | None = None
    function: str | None = None
    line: int | None = None
    error_code: str | None = None
    message: str = ""
    exception_type: str | None = None
    stack_trace: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    provider: str | None = None
    endpoint: str | None = None
    stream: str | None = None
    request_id: str | None = None
    job_id: str | None = None
    run_id: str | None = None
    expected: str | None = None
    actual: str | None = None
    first_seen: datetime = field(default_factory=_utcnow)
    last_seen: datetime = field(default_factory=_utcnow)
    occurrence_count: int = 1
    resolved_at: datetime | None = None
    resolution_message: str | None = None
    owner: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    recent_event_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "diagnostic_id": self.diagnostic_id,
            "fingerprint": self.fingerprint,
            "severity": self.severity,
            "status": self.status,
            "category": self.category,
            "service": self.service,
            "subsystem": self.subsystem,
            "component": self.component,
            "module": self.module,
            "file": self.file,
            "function": self.function,
            "line": self.line,
            "location": self.location_str(),
            "error_code": self.error_code,
            "message": self.message,
            "exception_type": self.exception_type,
            "stack_trace": self.stack_trace,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "provider": self.provider,
            "endpoint": self.endpoint,
            "stream": self.stream,
            "request_id": self.request_id,
            "job_id": self.job_id,
            "run_id": self.run_id,
            "expected": self.expected,
            "actual": self.actual,
            "first_seen": _iso(self.first_seen),
            "last_seen": _iso(self.last_seen),
            "occurrence_count": self.occurrence_count,
            "resolved_at": _iso(self.resolved_at),
            "resolution_message": self.resolution_message,
            "owner": self.owner,
            "details": self.details,
            "metadata": self.metadata,
            "recent_event_ids": list(self.recent_event_ids),
        }

    def location_str(self) -> str | None:
        if self.file and self.line is not None:
            base = self.file
            if self.function:
                return f"{base}:{self.line} ({self.function})"
            return f"{base}:{self.line}"
        if self.file:
            return self.file
        if self.component:
            return self.component
        return None


def make_event_id() -> str:
    return new_diagnostic_id()


def health_component(
    key: str,
    label: str,
    status: HealthStatus | str,
    *,
    reason: str | None = None,
    last_checked: datetime | str | None = None,
    last_success: datetime | str | None = None,
    latency_ms: float | None = None,
    error_count: int | None = None,
    stale_seconds: float | None = None,
    metrics: dict[str, Any] | None = None,
) -> ComponentHealth:
    lc = last_checked
    if isinstance(lc, datetime):
        lc = _iso(lc)
    ls = last_success
    if isinstance(ls, datetime):
        ls = _iso(ls)
    st = status.value if isinstance(status, HealthStatus) else str(status)
    return ComponentHealth(
        key=key,
        label=label,
        status=st,
        reason=reason,
        last_checked=lc,
        last_success=ls,
        latency_ms=latency_ms,
        error_count=error_count,
        stale_seconds=stale_seconds,
        metrics=metrics or {},
    )


# Re-export enums used by callers
__all__ = [
    "ComponentHealth",
    "DiagnosticEvent",
    "DiagnosticIssue",
    "ErrorCategory",
    "HealthStatus",
    "IssueStatus",
    "LocationInfo",
    "Severity",
    "health_component",
    "make_event_id",
]
