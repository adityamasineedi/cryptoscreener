"""``manual_trades``: the owner's manual trade journal, append-only with edit history (owner D188 part 2e).

Every change inserts a new version row. Nothing is ever updated or deleted:

- log a trade -> version 1, action OPEN, status OPEN;
- edit        -> version n+1, action EDIT (same status, figures recomputed);
- close       -> version n+1, action CLOSE, status CLOSED, exit figures with D144 costs;
- void        -> version n+1, action VOID, status VOID, ``voided`` true (the flag, not a delete).

Readers take the latest version per ``trade_id``. In PostgreSQL a trigger refuses UPDATE, DELETE and
TRUNCATE on the table, so the history cannot be rewritten through the app or by mistake.

The table is created on first use with CREATE TABLE IF NOT EXISTS from this module; database.py is not
changed. Production uses the app's SQLAlchemy async engine; the tests use the stdlib sqlite3 backend
below with the same DML.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from app.screener_tool import costs

TABLE = "manual_trades"
KEY = ("trade_id", "version")
ACTIONS = ("OPEN", "EDIT", "CLOSE", "VOID")
STATUSES = ("OPEN", "CLOSED", "VOID")

# (column, PostgreSQL type, SQLite type). row_id and the constraints are added per dialect.
COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("trade_id", "TEXT NOT NULL", "TEXT NOT NULL"),
    ("version", "INTEGER NOT NULL", "INTEGER NOT NULL"),
    ("valid_from", "TIMESTAMPTZ NOT NULL", "TEXT NOT NULL"),
    ("action", "TEXT NOT NULL", "TEXT NOT NULL"),
    ("voided", "BOOLEAN NOT NULL DEFAULT FALSE", "INTEGER NOT NULL DEFAULT 0"),
    ("status", "TEXT NOT NULL", "TEXT NOT NULL"),
    ("symbol", "TEXT NOT NULL", "TEXT NOT NULL"),
    ("side", "TEXT NOT NULL", "TEXT NOT NULL"),
    ("timeframe", "TEXT", "TEXT"),
    ("setup_tag", "TEXT", "TEXT"),
    ("entry_price", "DOUBLE PRECISION NOT NULL", "REAL NOT NULL"),
    ("stop_price", "DOUBLE PRECISION NOT NULL", "REAL NOT NULL"),
    ("targets_json", "TEXT NOT NULL DEFAULT '[]'", "TEXT NOT NULL DEFAULT '[]'"),
    ("quantity", "DOUBLE PRECISION NOT NULL", "REAL NOT NULL"),
    ("risk_usd", "DOUBLE PRECISION NOT NULL", "REAL NOT NULL"),
    ("size_input", "TEXT NOT NULL", "TEXT NOT NULL"),
    ("opened_at", "TIMESTAMPTZ NOT NULL", "TEXT NOT NULL"),
    ("notes", "TEXT", "TEXT"),
    ("screenshot_path", "TEXT", "TEXT"),
    ("exit_price", "DOUBLE PRECISION", "REAL"),
    ("exit_at", "TIMESTAMPTZ", "TEXT"),
    ("exit_reason", "TEXT", "TEXT"),
    ("gross_pnl_usd", "DOUBLE PRECISION", "REAL"),
    ("fees_usd", "DOUBLE PRECISION", "REAL"),
    ("slippage_usd", "DOUBLE PRECISION", "REAL"),
    ("net_pnl_usd", "DOUBLE PRECISION", "REAL"),
    ("gross_r", "DOUBLE PRECISION", "REAL"),
    ("r_multiple", "DOUBLE PRECISION", "REAL"),
    ("cost_model", "TEXT NOT NULL", "TEXT NOT NULL"),
    ("taker_fee_rate", "DOUBLE PRECISION NOT NULL", "REAL NOT NULL"),
    ("slippage_rate", "DOUBLE PRECISION NOT NULL", "REAL NOT NULL"),
    ("edit_note", "TEXT", "TEXT"),
)
COLUMN_NAMES = tuple(c[0] for c in COLUMNS)
TIME_COLUMNS = ("valid_from", "opened_at", "exit_at")
EDITABLE = ("symbol", "side", "timeframe", "setup_tag", "entry_price", "stop_price", "targets", "quantity",
            "risk_usd", "opened_at", "notes", "screenshot_path", "exit_price", "exit_at", "exit_reason")

_CHECKS = (
    "CONSTRAINT manual_trades_trade_version UNIQUE (trade_id, version)",
    "CONSTRAINT manual_trades_side_chk CHECK (side IN ('LONG', 'SHORT'))",
    "CONSTRAINT manual_trades_action_chk CHECK (action IN ('OPEN', 'EDIT', 'CLOSE', 'VOID'))",
    "CONSTRAINT manual_trades_status_chk CHECK (status IN ('OPEN', 'CLOSED', 'VOID'))",
    "CONSTRAINT manual_trades_size_chk CHECK (size_input IN ('quantity', 'risk_usd'))",
)

POSTGRES_DDL: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS manual_trades (\n    row_id BIGSERIAL PRIMARY KEY,\n    "
    + ",\n    ".join(f"{name} {pg}" for name, pg, _ in COLUMNS)
    + ",\n    "
    + ",\n    ".join(_CHECKS)
    + "\n)",
    "CREATE INDEX IF NOT EXISTS manual_trades_trade_version_idx ON manual_trades (trade_id, version DESC)",
    "CREATE OR REPLACE FUNCTION manual_trades_append_only() RETURNS trigger LANGUAGE plpgsql AS $fn$\n"
    "BEGIN\n"
    "    RAISE EXCEPTION 'manual_trades is append-only: % is not allowed (insert a new version instead)', TG_OP;\n"
    "END;\n"
    "$fn$",
    "DO $do$\nBEGIN\n"
    "    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'manual_trades_no_update_delete') THEN\n"
    "        CREATE TRIGGER manual_trades_no_update_delete BEFORE UPDATE OR DELETE ON manual_trades\n"
    "            FOR EACH ROW EXECUTE FUNCTION manual_trades_append_only();\n"
    "    END IF;\n"
    "    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'manual_trades_no_truncate') THEN\n"
    "        CREATE TRIGGER manual_trades_no_truncate BEFORE TRUNCATE ON manual_trades\n"
    "            FOR EACH STATEMENT EXECUTE FUNCTION manual_trades_append_only();\n"
    "    END IF;\n"
    "END\n$do$",
)

SQLITE_DDL: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS manual_trades (\n    row_id INTEGER PRIMARY KEY AUTOINCREMENT,\n    "
    + ",\n    ".join(f"{name} {lite}" for name, _, lite in COLUMNS)
    + ",\n    "
    + ",\n    ".join(_CHECKS)
    + "\n)",
    "CREATE INDEX IF NOT EXISTS manual_trades_trade_version_idx ON manual_trades (trade_id, version DESC)",
    "CREATE TRIGGER IF NOT EXISTS manual_trades_no_update BEFORE UPDATE ON manual_trades "
    "BEGIN SELECT RAISE(ABORT, 'manual_trades is append-only: UPDATE is not allowed'); END",
    "CREATE TRIGGER IF NOT EXISTS manual_trades_no_delete BEFORE DELETE ON manual_trades "
    "BEGIN SELECT RAISE(ABORT, 'manual_trades is append-only: DELETE is not allowed'); END",
)

INSERT_SQL = (
    f"INSERT INTO {TABLE} ({', '.join(COLUMN_NAMES)}) VALUES ({', '.join(':' + c for c in COLUMN_NAMES)})"
)
LATEST_SQL = (
    f"SELECT m.* FROM {TABLE} m JOIN (SELECT trade_id, MAX(version) AS v FROM {TABLE} GROUP BY trade_id) l "
    "ON m.trade_id = l.trade_id AND m.version = l.v ORDER BY m.opened_at, m.trade_id"
)
HISTORY_SQL = f"SELECT * FROM {TABLE} WHERE trade_id = :trade_id ORDER BY version"
ALL_SQL = f"SELECT * FROM {TABLE} ORDER BY trade_id, version"


class JournalError(ValueError):
    """A request the journal refuses (bad levels, unknown trade, wrong state)."""


class NotFound(JournalError):
    pass


class Backend(Protocol):
    dialect: str

    async def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> None: ...

    async def fetch_all(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]: ...


class SqlAlchemyBackend:
    """The app's async engine (PostgreSQL)."""

    dialect = "postgresql"

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    async def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> None:
        from sqlalchemy import text

        async with self.engine.begin() as conn:
            await conn.execute(text(sql), dict(params or {}))

    async def fetch_all(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        from sqlalchemy import text

        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql), dict(params or {}))
            return [dict(row) for row in result.mappings().all()]


class SqliteBackend:
    """stdlib sqlite3 (tests and offline checks). Times are stored as ISO-8601 UTC text."""

    dialect = "sqlite"

    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    @staticmethod
    def _param(v: Any) -> Any:
        if isinstance(v, datetime):
            return v.astimezone(timezone.utc).isoformat()
        if isinstance(v, bool):
            return int(v)
        return v

    async def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> None:
        with self.conn:
            self.conn.execute(sql, {k: self._param(v) for k, v in (params or {}).items()})

    async def fetch_all(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        cur = self.conn.execute(sql, {k: self._param(v) for k, v in (params or {}).items()})
        out = []
        for row in cur.fetchall():
            d = dict(row)
            for col in TIME_COLUMNS:
                if isinstance(d.get(col), str):
                    d[col] = datetime.fromisoformat(d[col])
            d["voided"] = bool(d.get("voided"))
            out.append(d)
        return out


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(v: Any, field: str) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        ts = v
    else:
        raw = str(v).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            ts = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise JournalError(f"{field}: not an ISO-8601 time") from exc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _symbol(v: Any) -> str:
    s = str(v or "").strip().upper()
    if not s or not s.isascii() or not s.isalnum() or len(s) > 30:
        raise JournalError("symbol must be an ASCII ticker such as BTCUSDT")
    return s


def _text(v: Any, field: str, limit: int) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if len(s) > limit:
        raise JournalError(f"{field} is longer than {limit} characters")
    return s or None


def _pos(v: Any, field: str) -> float | None:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError) as exc:
        raise JournalError(f"{field} must be a number") from exc
    if not x > 0 or x != x or x == float("inf"):
        raise JournalError(f"{field} must be a positive number")
    return x


def _targets(v: Any) -> list[float]:
    if v is None or v == "":
        return []
    if isinstance(v, str):
        parts = [p for p in v.replace(";", ",").split(",") if p.strip()]
    elif isinstance(v, (list, tuple)):
        parts = list(v)
    else:
        raise JournalError("targets must be a list of prices")
    out = []
    for p in parts:
        x = _pos(p, "target")
        if x is not None:
            out.append(x)
    if len(out) > 10:
        raise JournalError("at most 10 targets")
    return out


def _compute(state: dict[str, Any]) -> dict[str, Any]:
    """Size, risk and (when closed) D144 exit figures from the stored inputs."""
    sym = state["symbol"]
    try:
        levels = costs.position_levels(
            state["side"], state["entry_price"], state["stop_price"],
            quantity=state["quantity"] if state["size_input"] == "quantity" else None,
            risk_usd=state["risk_usd"] if state["size_input"] == "risk_usd" else None,
            targets=state["targets"], symbol=sym,
        )
    except ValueError as exc:
        raise JournalError(str(exc)) from exc
    state["quantity"] = levels["quantity"]
    state["risk_usd"] = levels["risk_usd"]
    exit_fields = ("gross_pnl_usd", "fees_usd", "slippage_usd", "net_pnl_usd", "gross_r", "r_multiple")
    if state.get("exit_price") is not None:
        if state.get("exit_at") is None:
            raise JournalError("exit_at is required with an exit price")
        if state["exit_at"] < state["opened_at"]:
            raise JournalError("exit time is before the entry time")
        res = costs.close_math(state["side"], state["entry_price"], state["stop_price"], state["quantity"],
                               state["exit_price"], sym)
        for f in exit_fields:
            state[f] = res[f]
    else:
        for f in exit_fields:
            state[f] = None
    state["cost_model"] = costs.COST_MODEL
    state["taker_fee_rate"] = costs.TAKER_FEE_RATE
    state["slippage_rate"] = costs.slippage_rate(sym)
    return state


def _row_to_trade(row: Mapping[str, Any]) -> dict[str, Any]:
    d = dict(row)
    try:
        d["targets"] = [float(x) for x in json.loads(d.get("targets_json") or "[]")]
    except (TypeError, ValueError):
        d["targets"] = []
    d["voided"] = bool(d.get("voided"))
    per_unit = abs(float(d["entry_price"]) - float(d["stop_price"]))
    sgn = 1 if d["side"] == "LONG" else -1
    d["targets_r"] = [(t - float(d["entry_price"])) * sgn / per_unit for t in d["targets"]] if per_unit else []
    d["stop_distance_pct"] = per_unit / float(d["entry_price"]) * 100.0 if d["entry_price"] else None
    d["costs_usd"] = (
        (d.get("fees_usd") or 0.0) + (d.get("slippage_usd") or 0.0) if d.get("fees_usd") is not None else None
    )
    return d


class ManualTradeJournal:
    def __init__(self, backend: Backend, *, clock: Any = _now) -> None:
        self.backend = backend
        self.clock = clock
        self._ready = False
        self._lock = asyncio.Lock()
        self.append_only_guard: bool | None = None
        self.guard_error: str | None = None

    async def ensure_table(self) -> None:
        """CREATE TABLE/INDEX IF NOT EXISTS (required), then the append-only triggers.

        A trigger statement that fails leaves the journal usable (the app itself only ever inserts) and is
        reported as ``append_only_guard`` False with the error; the next process start tries again.
        """
        if self._ready:
            return
        ddl = POSTGRES_DDL if self.backend.dialect == "postgresql" else SQLITE_DDL
        for stmt in ddl[:2]:
            await self.backend.execute(stmt)
        try:
            for stmt in ddl[2:]:
                await self.backend.execute(stmt)
            self.append_only_guard, self.guard_error = True, None
        except Exception as exc:  # noqa: BLE001
            self.append_only_guard, self.guard_error = False, f"{type(exc).__name__}: {str(exc)[:200]}"
        self._ready = True

    # --- reads -----------------------------------------------------------------------------------
    async def latest(self, *, include_void: bool = True) -> list[dict[str, Any]]:
        await self.ensure_table()
        rows = [_row_to_trade(r) for r in await self.backend.fetch_all(LATEST_SQL)]
        return rows if include_void else [r for r in rows if not r["voided"]]

    async def history(self, trade_id: str) -> list[dict[str, Any]]:
        await self.ensure_table()
        return [_row_to_trade(r) for r in await self.backend.fetch_all(HISTORY_SQL, {"trade_id": str(trade_id)})]

    async def all_versions(self) -> list[dict[str, Any]]:
        await self.ensure_table()
        return [_row_to_trade(r) for r in await self.backend.fetch_all(ALL_SQL)]

    async def get(self, trade_id: str) -> dict[str, Any]:
        rows = await self.history(trade_id)
        if not rows:
            raise NotFound(f"no trade {trade_id}")
        return rows[-1]

    # --- writes (inserts only) -------------------------------------------------------------------
    async def _insert(self, state: dict[str, Any]) -> dict[str, Any]:
        state = dict(state)
        state["targets_json"] = json.dumps([float(t) for t in state.get("targets") or []])
        params = {c: state.get(c) for c in COLUMN_NAMES}
        await self.backend.execute(INSERT_SQL, params)
        return (await self.history(state["trade_id"]))[-1]

    async def create(self, data: Mapping[str, Any]) -> dict[str, Any]:
        await self.ensure_table()
        qty, risk = _pos(data.get("quantity"), "quantity"), _pos(data.get("risk_usd"), "risk_usd")
        if (qty is None) == (risk is None):
            raise JournalError("give exactly one of quantity (coins) or risk_usd")
        side = str(data.get("side") or "").strip().upper()
        if side not in ("LONG", "SHORT"):
            raise JournalError("side must be LONG or SHORT")
        entry, stop = _pos(data.get("entry_price"), "entry_price"), _pos(data.get("stop_price"), "stop_price")
        if entry is None or stop is None:
            raise JournalError("entry_price and stop_price are required")
        now = self.clock()
        state: dict[str, Any] = {
            "trade_id": uuid.uuid4().hex,
            "version": 1,
            "valid_from": now,
            "action": "OPEN",
            "voided": False,
            "status": "OPEN",
            "symbol": _symbol(data.get("symbol")),
            "side": side,
            "timeframe": _text(data.get("timeframe"), "timeframe", 20),
            "setup_tag": _text(data.get("setup_tag"), "setup_tag", 80),
            "entry_price": entry,
            "stop_price": stop,
            "targets": _targets(data.get("targets")),
            "quantity": qty,
            "risk_usd": risk,
            "size_input": "quantity" if qty is not None else "risk_usd",
            "opened_at": _utc(data.get("opened_at"), "opened_at") or now,
            "notes": _text(data.get("notes"), "notes", 4000),
            "screenshot_path": _text(data.get("screenshot_path"), "screenshot_path", 500),
            "exit_price": None,
            "exit_at": None,
            "exit_reason": None,
            "edit_note": None,
        }
        async with self._lock:
            return await self._insert(_compute(state))

    async def _next(self, trade_id: str, action: str, mutate: Any, edit_note: Any) -> dict[str, Any]:
        await self.ensure_table()
        async with self._lock:
            prev = await self.get(trade_id)
            if prev["voided"]:
                raise JournalError("this trade is void; void is final")
            state = {c: prev.get(c) for c in COLUMN_NAMES}
            state["targets"] = list(prev["targets"])
            mutate(state, prev)
            state.update(
                version=int(prev["version"]) + 1,
                valid_from=self.clock(),
                action=action,
                edit_note=_text(edit_note, "edit_note", 500),
            )
            if action != "VOID":
                _compute(state)
            return await self._insert(state)

    async def edit(self, trade_id: str, changes: Mapping[str, Any], *, edit_note: Any = None) -> dict[str, Any]:
        unknown = sorted(set(changes) - set(EDITABLE))
        if unknown:
            raise JournalError(f"not editable: {', '.join(unknown)}")
        if not changes:
            raise JournalError("nothing to change")

        def mutate(state: dict[str, Any], prev: Mapping[str, Any]) -> None:
            if "symbol" in changes:
                state["symbol"] = _symbol(changes["symbol"])
            if "side" in changes:
                side = str(changes["side"] or "").strip().upper()
                if side not in ("LONG", "SHORT"):
                    raise JournalError("side must be LONG or SHORT")
                state["side"] = side
            for f, limit in (("timeframe", 20), ("setup_tag", 80), ("notes", 4000), ("screenshot_path", 500),
                             ("exit_reason", 200)):
                if f in changes:
                    state[f] = _text(changes[f], f, limit)
            for f in ("entry_price", "stop_price"):
                if f in changes:
                    x = _pos(changes[f], f)
                    if x is None:
                        raise JournalError(f"{f} is required")
                    state[f] = x
            if "targets" in changes:
                state["targets"] = _targets(changes["targets"])
            if "quantity" in changes and "risk_usd" in changes and changes["quantity"] not in (None, "") \
                    and changes["risk_usd"] not in (None, ""):
                raise JournalError("give quantity or risk_usd, not both")
            if changes.get("quantity") not in (None, ""):
                state["quantity"], state["size_input"] = _pos(changes["quantity"], "quantity"), "quantity"
            elif changes.get("risk_usd") not in (None, ""):
                state["risk_usd"], state["size_input"] = _pos(changes["risk_usd"], "risk_usd"), "risk_usd"
            elif {"entry_price", "stop_price"} & set(changes):
                state["size_input"] = "quantity"  # the position held is the size; its risk follows the stop
            if "opened_at" in changes:
                state["opened_at"] = _utc(changes["opened_at"], "opened_at") or prev["opened_at"]
            if {"exit_price", "exit_at"} & set(changes):
                if prev["status"] != "CLOSED":
                    raise JournalError("log the exit with close; edit changes an exit only after it is logged")
                if "exit_price" in changes:
                    x = _pos(changes["exit_price"], "exit_price")
                    if x is None:
                        raise JournalError("exit_price is required on a closed trade")
                    state["exit_price"] = x
                if "exit_at" in changes:
                    state["exit_at"] = _utc(changes["exit_at"], "exit_at") or prev["exit_at"]
            if "exit_reason" in changes and prev["status"] != "CLOSED":
                raise JournalError("exit_reason belongs to a closed trade")

        return await self._next(trade_id, "EDIT", mutate, edit_note)

    async def close(self, trade_id: str, *, exit_price: Any, exit_at: Any = None, exit_reason: Any = None,
                    edit_note: Any = None) -> dict[str, Any]:
        x = _pos(exit_price, "exit_price")
        if x is None:
            raise JournalError("exit_price is required")

        def mutate(state: dict[str, Any], prev: Mapping[str, Any]) -> None:
            if prev["status"] != "OPEN":
                raise JournalError(f"trade is {prev['status']}; edit a closed trade's exit instead")
            state["exit_price"] = x
            state["exit_at"] = _utc(exit_at, "exit_at") or self.clock()
            state["exit_reason"] = _text(exit_reason, "exit_reason", 200)
            state["status"] = "CLOSED"

        return await self._next(trade_id, "CLOSE", mutate, edit_note)

    async def void(self, trade_id: str, *, reason: Any = None) -> dict[str, Any]:
        def mutate(state: dict[str, Any], prev: Mapping[str, Any]) -> None:
            state["voided"] = True
            state["status"] = "VOID"

        return await self._next(trade_id, "VOID", mutate, reason)


# --- the app's journal ----------------------------------------------------------------------------
_journal: ManualTradeJournal | None = None
_journal_engine: Any = None
_test_journal: ManualTradeJournal | None = None


def get_journal() -> ManualTradeJournal | None:
    """The journal on the app's database, or None when the database is not connected."""
    global _journal, _journal_engine
    if _test_journal is not None:
        return _test_journal
    from app.services.database import db_manager

    engine = db_manager.engine if db_manager.enabled else None
    if engine is None:
        return None
    if _journal is None or _journal_engine is not engine:
        _journal = ManualTradeJournal(SqlAlchemyBackend(engine))
        _journal_engine = engine
    return _journal


def set_journal_for_tests(journal: ManualTradeJournal | None) -> None:
    global _test_journal
    _test_journal = journal


def schema_description() -> dict[str, Any]:
    """For the research session's backup and restore checks."""
    return {
        "table": TABLE,
        "key": list(KEY),
        "primary_key": "row_id",
        "columns": ["row_id", *COLUMN_NAMES],
        "append_only": "UPDATE/DELETE/TRUNCATE refused by trigger; latest version per trade_id is current",
    }


def csv_columns() -> Sequence[str]:
    return ("row_id", *COLUMN_NAMES, "costs_usd", "stop_distance_pct")
