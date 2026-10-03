"""Canonical diagnostic statuses — frontend must not invent health rules."""

from __future__ import annotations

from enum import Enum


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    WARNING = "WARNING"
    ERROR = "ERROR"
    STALE = "STALE"
    WAITING = "WAITING"
    DISABLED = "DISABLED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    MISSING = "MISSING"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class IssueStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    SUPPRESSED = "SUPPRESSED"


class ErrorCategory(str, Enum):
    APPLICATION = "APPLICATION"
    DATABASE = "DATABASE"
    REDIS = "REDIS"
    REST = "REST"
    WEBSOCKET = "WEBSOCKET"
    PARSER = "PARSER"
    DATA_QUALITY = "DATA_QUALITY"
    DATA_STALE = "DATA_STALE"
    DATA_MISSING = "DATA_MISSING"
    RESEARCH = "RESEARCH"
    BACKTEST = "BACKTEST"
    STRATEGY = "STRATEGY"
    SCHEDULER = "SCHEDULER"
    JOB = "JOB"
    LOCK = "LOCK"
    CACHE = "CACHE"
    RESOURCE = "RESOURCE"
    DISK = "DISK"
    MEMORY = "MEMORY"
    CPU = "CPU"
    BACKUP = "BACKUP"
    CONFIGURATION = "CONFIGURATION"
    AUTHENTICATION = "AUTHENTICATION"
    NETWORK = "NETWORK"
    FRONTEND = "FRONTEND"


# Rank for rolling up multiple component statuses → overall system status.
_STATUS_RANK: dict[str, int] = {
    HealthStatus.HEALTHY.value: 0,
    HealthStatus.WAITING.value: 1,
    HealthStatus.DISABLED.value: 1,
    HealthStatus.UNKNOWN.value: 2,
    HealthStatus.STALE.value: 3,
    HealthStatus.DEGRADED.value: 4,
    HealthStatus.WARNING.value: 5,
    HealthStatus.MISSING.value: 6,
    HealthStatus.UNAVAILABLE.value: 7,
    HealthStatus.ERROR.value: 8,
}


def worst_status(*statuses: str | HealthStatus | None) -> str:
    best = HealthStatus.HEALTHY.value
    best_rank = -1
    for s in statuses:
        if s is None:
            continue
        val = s.value if isinstance(s, HealthStatus) else str(s)
        rank = _STATUS_RANK.get(val, 2)
        if rank > best_rank:
            best_rank = rank
            best = val
    return best


# Disk usage % thresholds (configurable via settings).
DEFAULT_DISK_WARNING_PCT = 70.0
DEFAULT_DISK_ERROR_PCT = 85.0
DEFAULT_DISK_CRITICAL_PCT = 95.0

DEFAULT_MEMORY_WARNING_PCT = 80.0
DEFAULT_MEMORY_ERROR_PCT = 90.0
DEFAULT_MEMORY_CRITICAL_PCT = 95.0

DEFAULT_CPU_WARNING_PCT = 85.0
DEFAULT_CPU_ERROR_PCT = 95.0

# Retention defaults (days)
DEFAULT_EVENT_RETENTION_DAYS = 30
DEFAULT_RESOLVED_ISSUE_RETENTION_DAYS = 90
DEFAULT_SNAPSHOT_RETENTION_DAYS = 90
DEFAULT_RESOURCE_SAMPLE_RETENTION_DAYS = 7

# Bounded event history per fingerprint
MAX_EVENTS_PER_FINGERPRINT = 50
MAX_IN_MEMORY_ISSUES = 2000
MAX_IN_MEMORY_EVENTS = 5000
