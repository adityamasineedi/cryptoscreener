"""Append-only decision journal for live paper (owner ruling 2026-10-20, D19).

One JSON line per decision event: every evaluated bar, signal, skip and refusal with its reason code,
and every open, for the passed book, the COMBO_02 v1 watcher and the CANDLE12_V2 forward paper.

Write-only. No decision path reads it, and a failed write is swallowed, so the journal can never change
what live paper does. It is off until ``configure`` is called; only the orchestrator does that, in the
process that holds the paper writer lock. Tests, replays and research imports never write.

Signal detail (owner ruling D51c, P14; flag ``DECISION_JOURNAL_SIGNAL_DETAIL``, default off): every signal,
from the paper books and the screener, carries its engine stop, tested target (TP1) and source, including
signals that do not open. Callers hand the detail over as a zero-argument function; this module calls it only
when detail is on, adds its fields without replacing any field the row already has, and swallows any error.
So with the flag off nothing extra is computed or written, and with it on rows only gain fields or
detail-only rows. The flag lives only here: decision code never reads it.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

_lock = threading.Lock()
_path: Path | None = None
_signal_detail = False
# Keys already written by ``write_signal_once`` (bounded): the screener recomputes the same setup often.
_ONCE_MAX = 20_000
_once: OrderedDict[str, None] = OrderedDict()

Detail = Callable[[], "Mapping[str, Any] | None"]


def configure(path: str | Path | None, *, signal_detail: bool = False) -> None:
    global _path, _signal_detail
    _path = Path(path) if path else None
    _signal_detail = bool(signal_detail) and _path is not None
    with _lock:
        _once.clear()


def enabled() -> bool:
    return _path is not None


def signal_detail_enabled() -> bool:
    return _signal_detail


def _append(row: dict[str, Any]) -> None:
    line = json.dumps(row, default=str, sort_keys=True)
    with _lock:
        assert _path is not None
        _path.parent.mkdir(parents=True, exist_ok=True)
        with _path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _detail_fields(detail: Detail | None) -> dict[str, Any]:
    if detail is None or not _signal_detail:
        return {}
    try:
        extra = detail()
    except Exception:  # noqa: BLE001 - detail can never affect the row or the caller
        return {}
    return dict(extra) if isinstance(extra, Mapping) else {}


def write(book: str, event: str, *, _detail: Detail | None = None, **fields: Any) -> None:
    """Append one row. Never raises. ``_detail`` adds fields only when signal detail is on."""
    if _path is None:
        return
    try:
        row = {"ts": datetime.now(timezone.utc).isoformat(), "book": book, "event": event, **fields}
        for k, v in _detail_fields(_detail).items():
            row.setdefault(k, v)
        _append(row)
    except Exception:  # noqa: BLE001 - the journal must never affect trading
        return


def write_signal(book: str, event: str, detail: Detail) -> None:
    """A detail-only row (signal detail on): written only if ``detail()`` returns fields. Never raises."""
    if _path is None or not _signal_detail:
        return
    try:
        fields = _detail_fields(detail)
        if not fields:
            return
        row = {"ts": datetime.now(timezone.utc).isoformat(), "book": book, "event": event}
        for k, v in fields.items():
            row.setdefault(k, v)
        _append(row)
    except Exception:  # noqa: BLE001
        return


def write_signal_once(book: str, event: str, detail: Callable[[], "tuple[str, Mapping[str, Any]] | None"]) -> None:
    """Like ``write_signal``; ``detail()`` returns (key, fields) and a key already written is skipped."""
    if _path is None or not _signal_detail:
        return
    try:
        got = detail()
        if not got:
            return
        key, fields = got
        with _lock:
            if key in _once:
                return
            _once[key] = None
            while len(_once) > _ONCE_MAX:
                _once.popitem(last=False)
        write_signal(book, event, lambda: fields)
    except Exception:  # noqa: BLE001
        return
