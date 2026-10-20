"""Append-only decision journal for live paper (owner ruling 2026-10-20, D19).

One JSON line per decision event: every evaluated bar, signal, skip and refusal with its reason code,
and every open, for the passed book, the COMBO_02 v1 watcher and the CANDLE12_V2 forward paper.

Write-only. No decision path reads it, and a failed write is swallowed, so the journal can never change
what live paper does. It is off until ``configure`` is called; only the orchestrator does that, in the
process that holds the paper writer lock. Tests, replays and research imports never write.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_path: Path | None = None


def configure(path: str | Path | None) -> None:
    global _path
    _path = Path(path) if path else None


def enabled() -> bool:
    return _path is not None


def write(book: str, event: str, **fields: Any) -> None:
    """Append one row. Never raises."""
    if _path is None:
        return
    try:
        row = {"ts": datetime.now(timezone.utc).isoformat(), "book": book, "event": event, **fields}
        line = json.dumps(row, default=str, sort_keys=True)
        with _lock:
            _path.parent.mkdir(parents=True, exist_ok=True)
            with _path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:  # noqa: BLE001 - the journal must never affect trading
        return
