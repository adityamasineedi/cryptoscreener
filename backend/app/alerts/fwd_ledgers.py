"""Trade results as recorded in the forward tests' own ledgers (owner ruling D150). READ ONLY.

- FWD-1 (BOOK16_4H): ``forward/book16_fwd1/ledger.jsonl``, a hash-chained append-only ledger. A trade's result is
  its ``TRADE_CLOSED`` record (payload ``trade_id`` = ``"<symbol>|<entry_time>"``, the alert's position id).
  Until then the last ``RUN`` row shows it as ``open_position`` or ``pending_closed`` (closed, waiting for the
  funding settlement after its exit before the collector records it): no result yet.
- FWD-3 (LCSC_V1): ``forward_out/runs.jsonl``. The last run row's ``trades`` hold each trade by ``signal_time``
  (the alert's position id); ``outcome`` TP or SL is the result, OPEN is no result yet.

Both files belong to their running collectors. This module only ever opens them with ``open(path, "rb")``: no
write mode, no lock, no rename. On Windows that handle shares read and write, so an appending writer is never
blocked, and it is closed as soon as the bytes are read. A torn last line (a write in progress) is ignored and
read again on the next poll; any other unparseable line is skipped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from app.alerts.plan import utc

FWD_LEDGER_NAMES = {"BOOK16_FWD1": "FWD-1", "LCSC_FWD3": "FWD-3"}
PAPER_LEDGER_NAME = "paper"
CLOSED = "CLOSED"
# The only mode this module opens a ledger with.
READ_MODE = "rb"


def read_jsonl_lines(path: str | Path) -> list[dict[str, Any] | None]:
    """One entry per complete line of a JSONL file another process appends to (None: not a JSON object).

    Complete lines end in a newline. A last line without one counts only when it already parses (the writer's
    newline is all that is missing); otherwise it is a torn write and is left for the next read.
    """
    p = Path(path)
    try:
        with open(p, READ_MODE) as f:
            blob = f.read()
    except FileNotFoundError:
        return []
    parts = blob.split(b"\n")
    tail = parts.pop()
    lines = [_parse(ln) for ln in parts]
    last = _parse(tail) if tail.strip() else None
    if last is not None:
        lines.append(last)
    return lines


def read_jsonl_readonly(path: str | Path) -> tuple[list[dict[str, Any]], int]:
    """(parsed rows, complete line count); see ``read_jsonl_lines``."""
    lines = read_jsonl_lines(path)
    return [r for r in lines if r is not None], len(lines)


def _parse(raw: bytes) -> dict[str, Any] | None:
    try:
        row = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return row if isinstance(row, dict) else None


def _num(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _same_time(a: Any, b: Any) -> bool:
    try:
        return utc(a) == utc(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


class ForwardLedgers:
    """The FWD-1 and FWD-3 ledgers, re-read only when a file's size or mtime changed."""

    def __init__(self, fwd1: str | Path | None = None, fwd3: str | Path | None = None) -> None:
        self.paths = {"FWD-1": Path(fwd1) if fwd1 else None, "FWD-3": Path(fwd3) if fwd3 else None}
        self._cache: dict[str, tuple[tuple[int, int], list[dict[str, Any]]]] = {}

    def _rows(self, name: str) -> list[dict[str, Any]]:
        p = self.paths.get(name)
        if p is None:
            return []
        try:
            st = p.stat()
        except FileNotFoundError:
            return []
        key = (st.st_size, st.st_mtime_ns)
        hit = self._cache.get(name)
        if hit and hit[0] == key:
            return hit[1]
        rows, _ = read_jsonl_readonly(p)
        self._cache[name] = (key, rows)
        return rows

    def result(self, source: str, position_id: Any) -> dict[str, Any] | None:
        """The trade as its forward ledger records it (``status`` CLOSED = a result), or None if not there."""
        name = FWD_LEDGER_NAMES.get(str(source))
        if name is None or not position_id:
            return None
        rows = self._rows(name)
        return fwd1_result(rows, str(position_id)) if name == "FWD-1" else fwd3_result(rows, str(position_id))


def fwd1_result(rows: list[Mapping[str, Any]], trade_id: str) -> dict[str, Any] | None:
    for r in rows:
        p = r.get("payload") or {}
        if r.get("kind") == "TRADE_CLOSED" and str(p.get("trade_id")) == trade_id:
            return {"ledger": "FWD-1", "status": CLOSED, "exit_reason": p.get("exit_reason"),
                    "exit_time": p.get("exit_time"), "exit_price": _num(p.get("exit_price")),
                    # FWD-1 records no R multiple; its result is the net pnl (fees and funding included).
                    "r_multiple": None, "pnl_usd": _num(p.get("net_pnl")),
                    "ref": {"seq": r.get("seq"), "hash": str(r.get("hash"))[:16]}}
    for r in reversed(rows):
        p = r.get("payload") or {}
        if r.get("kind") != "RUN" or "symbols" not in p:
            continue
        for book in (p.get("symbols") or {}).values():
            op = book.get("open_position") if isinstance(book, Mapping) else None
            if isinstance(op, Mapping) and str(op.get("trade_id")) == trade_id:
                return {"ledger": "FWD-1", "status": "OPEN", "ref": {"seq": r.get("seq")}}
            pend = book.get("pending_closed") if isinstance(book, Mapping) else None
            for t in pend if isinstance(pend, list) else []:
                if isinstance(t, Mapping) and str(t.get("trade_id")) == trade_id:
                    return {"ledger": "FWD-1", "status": "CLOSED_NOT_RECORDED", "ref": {"seq": r.get("seq")}}
        return None  # only the latest run's view counts
    return None


def fwd3_result(rows: list[Mapping[str, Any]], signal_time: str) -> dict[str, Any] | None:
    for r in reversed(rows):
        trades = r.get("trades")
        if not isinstance(trades, list):
            continue
        for t in trades:
            if not isinstance(t, Mapping) or not _same_time(t.get("signal_time"), signal_time):
                continue
            outcome = str(t.get("outcome") or "")
            ref = {"run_at": r.get("run_at"), "row_hash": str(r.get("row_hash"))[:16]}
            if outcome == "OPEN" or not outcome:
                return {"ledger": "FWD-3", "status": "OPEN", "ref": ref}
            return {"ledger": "FWD-3", "status": CLOSED, "exit_reason": outcome, "exit_time": t.get("exit_time"),
                    "exit_price": _num(t.get("exit_fill")), "r_multiple": _num(t.get("R")),
                    "pnl_usd": _num(t.get("net_usd")), "ref": ref}
        return None  # only the latest run's view counts
    return None


def paper_result(trade: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A ``paper_trades`` row in the same shape (D149: the paper ledger's own result)."""
    if not trade:
        return None
    status = str(trade.get("status") or "")
    out: dict[str, Any] = {"ledger": PAPER_LEDGER_NAME, "status": status, "ref": {"id": trade.get("id")}}
    if status == CLOSED:
        out.update(exit_reason=trade.get("exit_reason"), exit_time=_iso(trade.get("closed_at")),
                   exit_price=_num(trade.get("exit_price")), r_multiple=_num(trade.get("r_multiple")),
                   pnl_usd=_num(trade.get("pnl_usd")))
    return out


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v
