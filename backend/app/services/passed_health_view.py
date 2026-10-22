"""Display-only additions to the passed-book health panel (owner ruling D181). Read-only.

- ``missed_signals_since``: the late-tip missed signals journalled since the passed book's forward start
  (2026-10-21 00:00 UTC), next to the watcher's all-time ``missed_signals`` counter (shown as a tooltip).
- ``not_tradable``: coins whose due bars are never evaluated because their history is too short
  (``history_short``), shown once instead of one "missed" row per hour.

Nothing here feeds the watcher, the paper engine or any decision; it only reads decision_journal.jsonl.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import ROOT

FORWARD_START = datetime(2026, 10, 21, tzinfo=timezone.utc)
NOT_TRADABLE_REASONS = {"history_short": "no history / no market cap"}

_cache: dict[str, Any] = {"key": None, "value": None}


def _ts(value: Any) -> datetime | None:
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def summarize(lines: Any, *, since: datetime = FORWARD_START) -> dict[str, Any]:
    """Count PASSED_BOOK missed signals at or after ``since`` and collect the not-tradable coins."""
    missed = 0
    not_tradable: dict[str, dict[str, Any]] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict) or row.get("book") != "PASSED_BOOK" or row.get("event") != "signal":
            continue
        at = _ts(row.get("ts") or row.get("recorded_at"))
        outcome, reason = row.get("outcome"), row.get("reason")
        if outcome == "missed" and at is not None and at >= since:
            missed += 1
        if outcome == "not_evaluated" and reason in NOT_TRADABLE_REASONS and row.get("symbol"):
            sym = str(row["symbol"])
            item = not_tradable.setdefault(sym, {"symbol": sym, "reason": reason,
                                                 "label": NOT_TRADABLE_REASONS[reason], "bars": 0, "last": None})
            item["bars"] += int(row.get("bars") or 1)
            if at is not None and (item["last"] is None or at.isoformat() > item["last"]):
                item["last"] = at.isoformat()
    return {
        "missed_signals_since": {"since": since.isoformat(), "count": missed},
        "not_tradable": sorted(not_tradable.values(), key=lambda r: r["symbol"]),
    }


def passed_health_extras(path: Path | None = None) -> dict[str, Any]:
    """``summarize`` over the live journal, cached on the file's size and mtime. Missing file: zero, empty."""
    file = path or ROOT / "data" / "paper" / "decision_journal.jsonl"
    try:
        st = file.stat()
    except OSError:
        return summarize([])
    key = (str(file), st.st_size, st.st_mtime_ns)
    if _cache["key"] != key:
        with file.open("r", encoding="utf-8", errors="replace") as fh:
            _cache["value"] = summarize(fh)
        _cache["key"] = key
    return _cache["value"]
