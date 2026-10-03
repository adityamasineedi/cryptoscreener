"""In-chunk and cross-chunk deduplication by (symbol, timeframe, open_time)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def _open_ms(row: Mapping[str, Any]) -> int | None:
    if row.get("open_time_ms") is not None:
        return int(row["open_time_ms"])
    t = row.get("time") or row.get("open_time")
    if t is None:
        return None
    if hasattr(t, "timestamp"):
        return int(t.timestamp() * 1000)
    if isinstance(t, (int, float)):
        v = int(t)
        return v if v > 1e12 else v * 1000
    return None


def dedupe_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep first occurrence of each open_time; preserve order."""
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        ms = _open_ms(row)
        if ms is None or ms in seen:
            continue
        seen.add(ms)
        out.append(dict(row))
    return out


def merge_unique(
    existing_ms: set[int],
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Drop rows whose open_time is already known (DB or prior chunk)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        ms = _open_ms(row)
        if ms is None or ms in existing_ms:
            continue
        existing_ms.add(ms)
        out.append(dict(row))
    return out
