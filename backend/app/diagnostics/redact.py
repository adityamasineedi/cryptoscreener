"""Secret redaction for AI packages and exports — never leak credentials."""

from __future__ import annotations

import re
from typing import Any

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|password|passwd|secret|authorization|cookie|"
    r"jwt|bearer|private[_-]?key|access[_-]?key|credential|conn(ection)?[_-]?string|"
    r"database_url|redis_url)",
    re.IGNORECASE,
)

_INLINE_SECRET_RE = re.compile(
    r"(?i)(authorization|api[_-]?key|token|password|bearer|secret)\s*[:=]\s*['\"]?([^\s'\"]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_URL_CREDS_RE = re.compile(r"(://)([^:/@]+):([^@]+)(@)")


def redact_string(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = _URL_CREDS_RE.sub(r"\1***:***\4", text)
    text = _BEARER_RE.sub("Bearer ***REDACTED***", text)
    text = _JWT_RE.sub("***JWT_REDACTED***", text)
    text = _INLINE_SECRET_RE.sub(r"\1=***REDACTED***", text)
    return text


def redact_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _SECRET_KEY_RE.search(key):
        return "***REDACTED***"
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, dict):
        return {str(k): redact_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_value(v) for v in value)
    return value


def contains_secret(text: str | None) -> bool:
    if not text:
        return False
    if _BEARER_RE.search(text) or _JWT_RE.search(text):
        return True
    if _INLINE_SECRET_RE.search(text):
        return True
    if _URL_CREDS_RE.search(text):
        return True
    return False
