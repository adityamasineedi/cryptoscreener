"""Issue fingerprint for deduplication — never create unbounded duplicate rows."""

from __future__ import annotations

import hashlib
import re


_WS_RE = re.compile(r"\s+")
_HEX_RE = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_NUM_RE = re.compile(r"\b\d+\b")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def normalize_message(message: str | None) -> str:
    if not message:
        return ""
    text = message.strip().lower()
    text = _UUID_RE.sub("<id>", text)
    text = _HEX_RE.sub("<hex>", text)
    text = _NUM_RE.sub("<n>", text)
    text = _WS_RE.sub(" ", text)
    return text[:500]


def issue_fingerprint(
    *,
    service: str | None,
    component: str | None,
    error_code: str | None,
    exception_type: str | None,
    message: str | None,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> str:
    parts = [
        (service or "").strip().lower(),
        (component or "").strip().lower(),
        (error_code or "").strip().upper(),
        (exception_type or "").strip(),
        normalize_message(message),
        (symbol or "").strip().upper(),
        (timeframe or "").strip().lower(),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
