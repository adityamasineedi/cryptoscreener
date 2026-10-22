"""Paper ledger VIEW (owner ruling D187): read-time evidence tags and per-book stats.

Display only. Nothing here writes to the ledger, the engine or any file: rows come from one
READ ONLY ``SELECT ... FROM paper_trades ORDER BY id`` (``read_ledger_rows``), and the tags,
default filter and stats are computed from them in memory on every read. No tag is stored.
Paper trading decisions do not import this module.

Evidence tags
-------------
Every CLOSED or CANCELLED row gets exactly one tag. The first rule that matches wins, in this
order (owner D187 precedence):

1. ``LEGACY`` - the row's stream is not one the current code can open:
   - its book is LEGACY: ``signal_snippet.source`` is neither ``PASSED_CAP_BOOK`` nor
     ``V1_PAPER_WATCHER`` (and ``strategy_id`` neither starts with ``PASSED|`` nor is
     ``COMBO_02_V1``): the 15m ``LEGACY_SETUP_SIGNAL`` / ``RESEARCH_15M`` stream, the V2 / V2.1-A
     watchers, anything else;
   - a passed-book row whose side ``(strategy_id, timeframe, side)`` is not a row of
     ``PASSED_SIDES`` (e.g. ``COMBO_02_V2_1B`` / ``COMBO_02_V2_1_A`` SHORT, disarmed 2026-10-20,
     D3/P5), whose coin is not on the cap-book list, or whose coin+side the per-coin allowlist
     blocks;
   - a V1 row whose ``(symbol, timeframe)`` is not an enabled-by-default 1h ``V1_BOOKS`` book.
   This reads the book definitions in code, not runtime switches, so a watcher toggled off for a
   while does not re-tag its history.
2. ``PRE_REWRITE`` - ``opened_at`` < ``REWRITE_UTC`` (2026-10-17 12:16:47 UTC): before the
   2026-10-17 allowlist/fill rewrite reached the live branch. See ``REWRITE_CITE``.
3. ``PRE_COUNTERS`` - ``opened_at`` < ``COUNTERS_UTC`` (2026-10-21 00:00 UTC, the owner's date).
   ``data/paper/stop_counters_start.json`` says first_run_utc 2026-10-20T17:57:21Z; the owner's
   2026-10-21 00:00 UTC is used (it is also the effective counter start under D34/D77: the later of
   2026-10-21 00:00 UTC and the first live run).
4. ``CATCHUP_CLOSE`` - status CLOSED and ``opened_at`` and ``closed_at`` fall in the same UTC second
   (both truncated to the second): the restart catch-up opened and closed it in one pass.
5. ``DUPLICATE_FILL`` - the ledger itself marked it (``exit_reason = 'duplicate_fill'``), or an
   earlier row (ordered by ``opened_at``, then ``id``, any status) has the same book, symbol, side
   and source candle. The source candle is ``source_candle_ts`` parsed to a UTC instant; a row
   without one falls back to ``signal_snippet.fill_bar_time``, then to the entry fill
   (``entry_price`` and ``opened_at`` truncated to the second).
6. ``VALID`` - everything else.

OPEN rows get no tag (``evidence_tag`` is null); they are still used as "earlier rows" by rule 5.
Each row also lists ``evidence_matches``: every rule it meets, ignoring precedence, so a shadowed
rule (a PRE_COUNTERS row that also closed in its opening second) is still visible.

Default view
------------
VALID rows opened at or after their book's forward start (``FORWARD_STARTS``). The LEGACY book has
no forward start, so it is never in the default view. "Show all history" shows every row with its
tag.

Per-book stats (default view, CLOSED rows only)
-----------------------------------------------
- ``trades``, ``pnl_usd``; ``equity`` = ``STARTING_EQUITY`` + ``pnl_usd``.
- ``r_actual`` ("R on actual risk") = sum of ``pnl_usd / risk_usd``; ``risk_usd`` is the risk the
  position actually carried after tick/lot rounding and the 2x leverage cap. The ledger's own
  ``r_multiple`` is this same ratio (``net_paper_pnl``), reported as ``r_ledger`` for a cross-check.
- ``r_nominal`` = sum of ``pnl_usd / nominal_risk_usd``; ``nominal_risk_usd`` = equity at open x
  ``signal_snippet.risk_percent``, where equity at open is ``STARTING_EQUITY`` plus the PnL of every
  CLOSED ledger row with ``closed_at`` before this row's ``opened_at`` (the pooled equity the engine
  sizes new risk from). A row without ``risk_percent`` uses ``risk_usd`` (``nominal_fallback``).
- ``capped`` = rows whose ``signal_snippet.paper_execution.capped_by_leverage`` is true (the 2x
  notional cap shrank the quantity below the risk-based size).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

UTC = timezone.utc

TAGS: tuple[str, ...] = ("LEGACY", "PRE_REWRITE", "PRE_COUNTERS", "CATCHUP_CLOSE", "DUPLICATE_FILL", "VALID")
BOOKS: tuple[str, ...] = ("PASSED", "V1", "LEGACY")
BOOK_LABELS = {"PASSED": "PARITY TEST", "V1": "PAPER", "LEGACY": "LEGACY (disabled)"}

STARTING_EQUITY = 1000.0

REWRITE_UTC = datetime(2026, 10, 17, 12, 16, 47, tzinfo=UTC)
REWRITE_CITE = (
    "2026-10-17 allowlist/fill rewrite: live branch feature/grid-trading fast-forwarded to e7089a3 "
    "(merge integration/2026-10-17b) at 2026-10-17T17:46:47+05:30 = 12:16:47 UTC (reflog). That merge "
    "carried c83b45d 'feat(passed-book): per-coin allowlist of passed sides' (merged as be591ed) on top "
    "of 88e5ebb 'fix(paper): passed book fills, exits and fees match the backtest's next-open model' "
    "(live since f876a63, 10:53:05 UTC). In the ledger, every passed-book row before it lacks "
    "signal_snippet.execution_model; every one after has CAUSAL_NEXT_OPEN."
)
COUNTERS_UTC = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)
COUNTERS_CITE = (
    "Owner D187: 2026-10-21 00:00 UTC. data/paper/stop_counters_start.json says first_run_utc "
    "2026-10-20T17:57:21Z (622aaf3 went live at 17:56:28 UTC); the owner's date is used. It equals "
    "the D34/D77 effective counter start (the later of 2026-10-21 00:00 UTC and the first run)."
)

FORWARD_STARTS: dict[str, datetime | None] = {"PASSED": COUNTERS_UTC, "V1": COUNTERS_UTC, "LEGACY": None}
FORWARD_START_CITES = {
    "PASSED": "passed-book parity test forward start 2026-10-21 00:00 UTC (owner D187)",
    "V1": "COMBO_02 v1 forward start 2026-10-21 00:00 UTC (owner D187)",
    "LEGACY": "disabled stream: no forward start, never in the default view",
}
# Books whose trades live in their own JSON stores, not in paper_trades. Listed for the record;
# the ledger view neither reads nor filters them.
OTHER_BOOKS: tuple[dict[str, str], ...] = (
    {
        "id": "CANDLE12_V2",
        "forward_start": "2026-10-19T17:00:00+00:00",
        "source": "backend/data/candle12_v2_paper/btcusdt_long.json (version 2)",
        "cite": "First live 2026-10-17 (v1 watermark 2026-10-17T18:00Z); the version-2 migration at "
        "2026-10-19T18:57:30Z marked every v1 record INVALID and restarted the book at watermark "
        "2026-10-19T17:00Z (candle12_v2_paper.migrate). Its store holds only trades after that.",
    },
    {
        "id": "FWD-1",
        "forward_start": "2026-10-19T00:00:00+00:00",
        "source": "backend/data/book16_paper/state.json",
        "cite": "book16_paper.START = 2026-10-19 00:00 UTC (first forward 4h bar; entries from START + 1 bar).",
    },
)


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: Any) -> str | None:
    t = _utc(value)
    return t.isoformat() if t is not None else (None if value is None else str(value))


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def _snippet(row: dict[str, Any]) -> dict[str, Any]:
    snip = row.get("signal_snippet")
    if isinstance(snip, str):
        try:
            snip = json.loads(snip)
        except ValueError:
            snip = {}
    return snip if isinstance(snip, dict) else {}


def book_of(row: dict[str, Any]) -> str:
    """PASSED, V1 or LEGACY: the same split as the dashboard's per-book rollup (bookStats.paperBook)."""
    snip = _snippet(row)
    source = str(snip.get("source") or row.get("source") or "")
    strategy = str(snip.get("strategy_id") or row.get("strategy_id") or "")
    if source == "PASSED_CAP_BOOK" or strategy.startswith("PASSED|"):
        return "PASSED"
    if source == "V1_PAPER_WATCHER" or strategy == "COMBO_02_V1":
        return "V1"
    return "LEGACY"


def _passed_side(row: dict[str, Any]) -> tuple[str, str, str]:
    snip = _snippet(row)
    stream = str(snip.get("stream") or "")
    parts = stream.split("|")
    if len(parts) == 3 and parts[0].upper() == "PASSED":
        strategy, direction = parts[1], parts[2]
    else:
        strategy = str(snip.get("strategy_id") or "")
        direction = str(snip.get("direction") or row.get("side") or "")
    timeframe = str(row.get("timeframe") or snip.get("timeframe") or "").lower()
    return (strategy.upper(), timeframe, direction.upper())


def legacy_reason(row: dict[str, Any]) -> str | None:
    """Why the row's stream is disabled in the current code, or None when it can still open."""
    book = book_of(row)
    symbol = str(row.get("symbol") or "").upper()
    if book == "LEGACY":
        snip = _snippet(row)
        return f"stream off: source={snip.get('source') or '-'} strategy={snip.get('strategy_id') or '-'}"
    if book == "PASSED":
        from app.research import passed_symbol_allowlist
        from app.research.passed_cap_book import PASSED_SIDES, cap_symbols

        side = _passed_side(row)
        if side not in PASSED_SIDES:
            return f"passed side {'|'.join(side)} is not in PASSED_SIDES"
        if symbol not in cap_symbols():
            return f"{symbol} is not on the cap-book list"
        if not passed_symbol_allowlist.get_allowlist().is_allowed(symbol, side):
            return f"per-coin allowlist blocks {symbol} {'|'.join(side)}"
        return None
    from app.research.v1_production import V1_BOOKS

    timeframe = str(row.get("timeframe") or "").lower()
    live = {(b.symbol, b.timeframe) for b in V1_BOOKS if b.timeframe == "1h" and b.enabled_by_default}
    if (symbol, timeframe) not in live:
        return f"V1 book {symbol} {timeframe or '-'} is not an enabled 1h book"
    return None


def _second(value: Any) -> datetime | None:
    t = _utc(value)
    return t.replace(microsecond=0) if t is not None else None


def _candle_key(row: dict[str, Any]) -> tuple[Any, ...]:
    candle = _utc(row.get("source_candle_ts"))
    if candle is not None:
        return ("candle", candle)
    fill_bar = _utc(_snippet(row).get("fill_bar_time"))
    if fill_bar is not None:
        return ("fill_bar", fill_bar)
    return ("entry_fill", _num(row.get("entry_price")), _second(row.get("opened_at")))


def _order_key(row: dict[str, Any]) -> tuple[datetime, str]:
    return (_utc(row.get("opened_at")) or datetime.max.replace(tzinfo=UTC), str(row.get("id") or ""))


def rule_matches(row: dict[str, Any], earlier: str | None) -> list[tuple[str, str]]:
    """Every rule the row matches, in precedence order, ignoring precedence. ``earlier`` = the id of an
    earlier row with the same book, symbol, side and source candle (or None)."""
    opened = _utc(row.get("opened_at"))
    status = str(row.get("status") or "").upper()
    hits: list[tuple[str, str]] = []
    reason = legacy_reason(row)
    if reason is not None:
        hits.append(("LEGACY", reason))
    if opened is None or opened < REWRITE_UTC:
        hits.append(("PRE_REWRITE", f"opened before {REWRITE_UTC.isoformat()}"))
    if opened is None or opened < COUNTERS_UTC:
        hits.append(("PRE_COUNTERS", f"opened before {COUNTERS_UTC.isoformat()}"))
    closed = _second(row.get("closed_at"))
    if status == "CLOSED" and closed is not None and opened is not None and closed == _second(opened):
        hits.append(("CATCHUP_CLOSE", "opened and closed in the same second"))
    if str(row.get("exit_reason") or "") == "duplicate_fill":
        hits.append(("DUPLICATE_FILL", "ledger exit_reason duplicate_fill"))
    elif earlier is not None:
        hits.append(("DUPLICATE_FILL", f"same book, symbol, side and source candle as {earlier}"))
    return hits


def evidence_tags(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``{id: {"tag", "reason", "matches"}}`` for every row. OPEN rows get tag None. Reads only.

    ``tag`` is the first match in ``TAGS`` order (VALID when none); ``matches`` lists every rule
    the row meets, so a shadowed rule (e.g. a PRE_COUNTERS row that also closed in its opening
    second) stays visible.
    """
    seen: dict[tuple[Any, ...], str] = {}
    out: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=_order_key):
        rid = str(row.get("id") or "")
        key = (book_of(row), str(row.get("symbol") or "").upper(), str(row.get("side") or "").upper(), _candle_key(row))
        earlier = seen.get(key)
        if earlier is None:
            seen[key] = rid
        hits = rule_matches(row, earlier)
        if str(row.get("status") or "").upper() not in ("CLOSED", "CANCELLED"):
            out[rid] = {"tag": None, "reason": "open row: no tag", "matches": [name for name, _ in hits]}
            continue
        tag, reason = hits[0] if hits else ("VALID", "")
        out[rid] = {"tag": tag, "reason": reason, "matches": [name for name, _ in hits]}
    return out


def _equity_before(closed: list[tuple[datetime, float]], opened: datetime | None, starting: float) -> float:
    if opened is None:
        return starting
    return starting + sum(pnl for at, pnl in closed if at < opened)


def build_ledger_view(
    rows: list[dict[str, Any]],
    *,
    starting_equity: float = STARTING_EQUITY,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Tag every row, mark the default view and compute per-book stats. ``rows`` is not modified."""
    tags = evidence_tags(rows)
    closed_pnl: list[tuple[datetime, float]] = []
    for row in rows:
        if str(row.get("status") or "").upper() == "CLOSED":
            at = _utc(row.get("closed_at"))
            if at is not None:
                closed_pnl.append((at, float(_num(row.get("pnl_usd")) or 0.0)))

    out_rows: list[dict[str, Any]] = []
    overall: Counter[str] = Counter()
    matched: Counter[str] = Counter()
    by_book: dict[str, Counter[str]] = {book: Counter() for book in BOOKS}
    stats = {
        book: {"trades": 0, "pnl_usd": 0.0, "r_actual": 0.0, "r_nominal": 0.0, "r_ledger": 0.0, "capped": 0, "nominal_fallback": 0}
        for book in BOOKS
    }
    for row in sorted(rows, key=_order_key):
        rid = str(row.get("id") or "")
        tag, reason, matches = tags[rid]["tag"], tags[rid]["reason"], tags[rid]["matches"]
        book = book_of(row)
        status = str(row.get("status") or "").upper()
        snip = _snippet(row)
        execution = snip.get("paper_execution") if isinstance(snip.get("paper_execution"), dict) else {}
        capped = bool(execution.get("capped_by_leverage"))
        opened = _utc(row.get("opened_at"))
        pnl = _num(row.get("pnl_usd"))
        risk = _num(row.get("risk_usd"))
        risk_pct = _num(snip.get("risk_percent"))
        equity_open = _equity_before(closed_pnl, opened, starting_equity)
        nominal_risk = equity_open * risk_pct if risk_pct and risk_pct > 0 else risk
        start = FORWARD_STARTS.get(book)
        in_default = tag == "VALID" and start is not None and opened is not None and opened >= start
        r_actual = pnl / risk if pnl is not None and risk else None
        r_nominal = pnl / nominal_risk if pnl is not None and nominal_risk else None
        if tag is not None:
            overall[tag] += 1
            by_book[book][tag] += 1
            matched.update(matches or ["VALID"])
        if in_default and status == "CLOSED":
            s = stats[book]
            s["trades"] += 1
            s["pnl_usd"] += pnl or 0.0
            s["r_actual"] += r_actual or 0.0
            s["r_nominal"] += r_nominal or 0.0
            s["r_ledger"] += _num(row.get("r_multiple")) or 0.0
            s["capped"] += int(capped)
            s["nominal_fallback"] += int(not (risk_pct and risk_pct > 0))
        out_rows.append(
            {
                "id": rid,
                "symbol": row.get("symbol"),
                "side": row.get("side"),
                "status": row.get("status"),
                "entry_price": _num(row.get("entry_price")),
                "stop_price": _num(row.get("stop_price")),
                "tp1_price": _num(row.get("tp1_price")),
                "quantity": _num(row.get("quantity")),
                "risk_usd": risk,
                "opened_at": _iso(row.get("opened_at")),
                "closed_at": _iso(row.get("closed_at")),
                "exit_price": _num(row.get("exit_price")),
                "exit_reason": row.get("exit_reason"),
                "pnl_usd": pnl,
                "r_multiple": _num(row.get("r_multiple")),
                "source_candle_ts": row.get("source_candle_ts"),
                "timeframe": row.get("timeframe"),
                "signal_snippet": snip,
                "mark_price": None,
                "unrealized_pnl_usd": None,
                "unrealized_r": None,
                "book": book,
                "evidence_tag": tag,
                "evidence_reason": reason,
                "evidence_matches": matches,
                "in_default_view": in_default,
                "capped_by_leverage": capped,
                "equity_at_open": equity_open,
                "nominal_risk_usd": nominal_risk,
                "r_nominal": r_nominal,
                "r_actual": r_actual,
            }
        )
    books = []
    for book in BOOKS:
        s = stats[book]
        start = FORWARD_STARTS[book]
        books.append(
            {
                "id": book,
                "label": BOOK_LABELS[book],
                "forward_start": start.isoformat() if start else None,
                "forward_start_cite": FORWARD_START_CITES[book],
                "equity": starting_equity + s["pnl_usd"],
                **s,
            }
        )
    return {
        "read_only": True,
        "source": "paper_trades (one READ ONLY SELECT ... ORDER BY id); tags computed at read time, never stored",
        "as_of": (now or datetime.now(UTC)).isoformat(),
        "starting_equity": starting_equity,
        "precedence": list(TAGS),
        "cutoffs": {
            "rewrite_utc": REWRITE_UTC.isoformat(),
            "rewrite_cite": REWRITE_CITE,
            "counters_utc": COUNTERS_UTC.isoformat(),
            "counters_cite": COUNTERS_CITE,
        },
        "other_books": [dict(b) for b in OTHER_BOOKS],
        "tag_counts": {
            "overall": {tag: overall.get(tag, 0) for tag in TAGS},
            "by_book": {book: {tag: by_book[book].get(tag, 0) for tag in TAGS} for book in BOOKS},
            # Closed/cancelled rows meeting each rule on its own, ignoring precedence.
            "rule_matches": {tag: matched.get(tag, 0) for tag in TAGS},
        },
        "books": books,
        "rows": out_rows,
        "total_rows": len(out_rows),
        "default_rows": sum(1 for r in out_rows if r["in_default_view"]),
    }


LEDGER_COLUMNS = (
    "id, symbol, side, status, entry_price, stop_price, tp1_price, quantity, risk_usd, opened_at, closed_at, "
    "exit_price, exit_reason, pnl_usd, r_multiple, source_candle_ts, timeframe, signal_snippet"
)
READ_ONLY_SQL = "SET TRANSACTION READ ONLY"
LEDGER_SQL = f"SELECT {LEDGER_COLUMNS} FROM paper_trades ORDER BY id"


async def read_ledger_rows(engine: Any | None = None) -> list[dict[str, Any]]:
    """Every paper_trades row, in one READ ONLY transaction that is rolled back. Raises when no DB."""
    from sqlalchemy import text

    if engine is None:
        from app.services.database import db_manager

        if not db_manager.enabled or db_manager.engine is None:
            raise RuntimeError("database not connected")
        engine = db_manager.engine
    async with engine.connect() as conn:
        await conn.execute(text(READ_ONLY_SQL))
        result = await conn.execute(text(LEDGER_SQL))
        rows = [dict(r) for r in result.mappings().all()]
        await conn.rollback()
    return rows
