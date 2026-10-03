"""Persisted strategy_candidate_registry + audit log.

Durable approval / state storage. In-memory fallback is for tests and when
DATABASE_ENABLED=false — production must use Postgres via db_manager.
"""

from __future__ import annotations

import copy
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.research.candidate_state_machine import (
    OPERATOR_ONLY_STATES,
    assert_transition_allowed,
)
from app.research.dynamic_candidate_constants import (
    COMBO_VERSION,
    DEFAULT_RISK_PERCENT,
    SOURCE_PIPELINE,
    STRATEGY_ID,
    TELEGRAM_ELIGIBLE_DEFAULT,
)
from app.services.database import db_manager

SCHEMA_DDL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS strategy_candidate_registry (
        id                          TEXT PRIMARY KEY,
        symbol                      TEXT NOT NULL,
        market_type                 TEXT NOT NULL DEFAULT 'futures_perp',
        quote_asset                 TEXT NOT NULL DEFAULT 'USDT',
        discovered_at_utc           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_screened_at_utc        TIMESTAMPTZ,

        state                       TEXT NOT NULL DEFAULT 'DISCOVERED',
        state_reason                TEXT,
        state_updated_at_utc        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

        selector_version            TEXT,
        manifest_id                 TEXT,
        discovery_rank              INT,
        discovery_volume_usd        DOUBLE PRECISION,
        discovery_reason            TEXT,

        ohlcv_1h_start_utc          TIMESTAMPTZ,
        ohlcv_1h_end_utc            TIMESTAMPTZ,
        ohlcv_1h_completeness       DOUBLE PRECISION,
        ohlcv_4h_start_utc          TIMESTAMPTZ,
        ohlcv_4h_end_utc            TIMESTAMPTZ,
        ohlcv_4h_completeness       DOUBLE PRECISION,
        history_days                DOUBLE PRECISION,
        data_health_checked_at_utc  TIMESTAMPTZ,
        data_health_block_reason    TEXT,

        backtest_window_start_utc   TEXT,
        backtest_window_end_utc     TEXT,
        backtest_engine_fingerprint JSONB NOT NULL DEFAULT '{}',
        backtest_status             TEXT,
        backtest_tier               TEXT,
        backtest_trade_count        INT,
        backtest_win_rate           DOUBLE PRECISION,
        backtest_net_avg_r          DOUBLE PRECISION,
        backtest_profit_factor      DOUBLE PRECISION,
        backtest_net_pnl            DOUBLE PRECISION,
        backtest_fees               DOUBLE PRECISION,
        backtest_max_dd_r           DOUBLE PRECISION,
        backtest_max_losing_streak  INT,

        oos_status                  TEXT,
        oos_window_start_utc        TEXT,
        oos_window_end_utc          TEXT,
        oos_trade_count             INT,
        oos_net_avg_r               DOUBLE PRECISION,
        oos_profit_factor           DOUBLE PRECISION,
        oos_net_pnl                 DOUBLE PRECISION,
        oos_max_dd_r                DOUBLE PRECISION,
        oos_max_losing_streak       INT,

        portfolio_overlap_btc       DOUBLE PRECISION,
        portfolio_overlap_eth       DOUBLE PRECISION,
        portfolio_overlap_sol       DOUBLE PRECISION,
        peak_concurrent_positions   INT,
        portfolio_incremental_dd_r  DOUBLE PRECISION,
        portfolio_report            JSONB NOT NULL DEFAULT '{}',

        risk_percent                DOUBLE PRECISION NOT NULL DEFAULT 0,
        operator_approved           BOOLEAN NOT NULL DEFAULT FALSE,
        operator_approved_by        TEXT,
        operator_approved_at_utc    TIMESTAMPTZ,
        approval_note               TEXT,

        strategy_id                 TEXT NOT NULL DEFAULT 'COMBO_02_V2_RESEARCH',
        combo_version               TEXT NOT NULL DEFAULT 'v2-research',
        source                      TEXT NOT NULL DEFAULT 'DYNAMIC_CANDIDATE_PIPELINE',
        telegram_eligible           BOOLEAN NOT NULL DEFAULT FALSE,

        created_at_utc              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at_utc              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

        UNIQUE (symbol, strategy_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy_candidate_audit_log (
        id                  BIGSERIAL PRIMARY KEY,
        candidate_id        TEXT NOT NULL,
        symbol              TEXT NOT NULL,
        action              TEXT NOT NULL,
        actor               TEXT,
        previous_state      TEXT,
        new_state           TEXT,
        previous_risk       DOUBLE PRECISION,
        new_risk            DOUBLE PRECISION,
        note                TEXT,
        payload             JSONB NOT NULL DEFAULT '{}',
        created_at_utc      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_scr_state ON strategy_candidate_registry (state, updated_at_utc DESC)",
    "CREATE INDEX IF NOT EXISTS idx_scr_symbol ON strategy_candidate_registry (symbol)",
    "CREATE INDEX IF NOT EXISTS idx_scr_audit_sym ON strategy_candidate_audit_log (symbol, created_at_utc DESC)",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def default_candidate_row(symbol: str, **overrides: Any) -> dict[str, Any]:
    now = _utc_now()
    row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "symbol": str(symbol).upper().strip(),
        "market_type": "futures_perp",
        "quote_asset": "USDT",
        "discovered_at_utc": now,
        "last_screened_at_utc": now,
        "state": "DISCOVERED",
        "state_reason": "selected_by_dynamic_discovery",
        "state_updated_at_utc": now,
        "selector_version": None,
        "manifest_id": None,
        "discovery_rank": None,
        "discovery_volume_usd": None,
        "discovery_reason": None,
        "ohlcv_1h_start_utc": None,
        "ohlcv_1h_end_utc": None,
        "ohlcv_1h_completeness": None,
        "ohlcv_4h_start_utc": None,
        "ohlcv_4h_end_utc": None,
        "ohlcv_4h_completeness": None,
        "history_days": None,
        "data_health_checked_at_utc": None,
        "data_health_block_reason": None,
        "backtest_window_start_utc": None,
        "backtest_window_end_utc": None,
        "backtest_engine_fingerprint": {},
        "backtest_status": None,
        "backtest_tier": None,
        "backtest_trade_count": None,
        "backtest_win_rate": None,
        "backtest_net_avg_r": None,
        "backtest_profit_factor": None,
        "backtest_net_pnl": None,
        "backtest_fees": None,
        "backtest_max_dd_r": None,
        "backtest_max_losing_streak": None,
        "oos_status": None,
        "oos_window_start_utc": None,
        "oos_window_end_utc": None,
        "oos_trade_count": None,
        "oos_net_avg_r": None,
        "oos_profit_factor": None,
        "oos_net_pnl": None,
        "oos_max_dd_r": None,
        "oos_max_losing_streak": None,
        "portfolio_overlap_btc": None,
        "portfolio_overlap_eth": None,
        "portfolio_overlap_sol": None,
        "peak_concurrent_positions": None,
        "portfolio_incremental_dd_r": None,
        "portfolio_report": {},
        "risk_percent": float(DEFAULT_RISK_PERCENT),
        "operator_approved": False,
        "operator_approved_by": None,
        "operator_approved_at_utc": None,
        "approval_note": None,
        "strategy_id": STRATEGY_ID,
        "combo_version": COMBO_VERSION,
        "source": SOURCE_PIPELINE,
        "telegram_eligible": bool(TELEGRAM_ELIGIBLE_DEFAULT),
        "created_at_utc": now,
        "updated_at_utc": now,
    }
    row.update(overrides)
    row["symbol"] = str(row["symbol"]).upper().strip()
    row["telegram_eligible"] = False  # never auto-enable
    return row


def serialize_candidate(row: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe view for API/UI."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = _iso(v)
        elif isinstance(v, dict):
            out[k] = copy.deepcopy(v)
        else:
            out[k] = v
    out["telegram_eligible"] = False if out.get("telegram_eligible") is None else bool(
        out["telegram_eligible"]
    )
    # Hard invariant for API consumers
    if str(out.get("strategy_id") or "") == STRATEGY_ID:
        out["telegram_eligible"] = False
    return out


class StrategyCandidateRegistry:
    """Repository with Postgres persistence + in-memory mirror for tests."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._memory: dict[str, dict[str, Any]] = {}  # id -> row
        self._by_symbol: dict[str, str] = {}  # symbol -> id
        self._audit: list[dict[str, Any]] = []
        self.force_memory = False

    def reset_memory(self) -> None:
        with self._lock:
            self._memory.clear()
            self._by_symbol.clear()
            self._audit.clear()

    @property
    def use_db(self) -> bool:
        if self.force_memory:
            return False
        return bool(db_manager.enabled and db_manager.engine is not None)

    async def ensure_schema(self) -> None:
        if not self.use_db:
            return
        assert db_manager.engine is not None
        for stmt in SCHEMA_DDL:
            async with db_manager.engine.begin() as conn:
                await conn.execute(text(stmt))

    def _store_memory(self, row: dict[str, Any]) -> dict[str, Any]:
        rid = str(row["id"])
        sym = str(row["symbol"]).upper()
        self._memory[rid] = copy.deepcopy(row)
        self._by_symbol[sym] = rid
        return copy.deepcopy(row)

    def _audit_memory(
        self,
        *,
        candidate_id: str,
        symbol: str,
        action: str,
        actor: str | None,
        previous_state: str | None,
        new_state: str | None,
        previous_risk: float | None,
        new_risk: float | None,
        note: str | None,
        payload: dict[str, Any] | None,
    ) -> None:
        self._audit.append(
            {
                "id": len(self._audit) + 1,
                "candidate_id": candidate_id,
                "symbol": symbol,
                "action": action,
                "actor": actor,
                "previous_state": previous_state,
                "new_state": new_state,
                "previous_risk": previous_risk,
                "new_risk": new_risk,
                "note": note,
                "payload": payload or {},
                "created_at_utc": _utc_now(),
            }
        )

    async def get_by_symbol(self, symbol: str) -> dict[str, Any] | None:
        sym = str(symbol).upper().strip()
        if not self.use_db:
            with self._lock:
                rid = self._by_symbol.get(sym)
                return copy.deepcopy(self._memory[rid]) if rid else None
        assert db_manager.engine is not None
        sql = text(
            """
            SELECT * FROM strategy_candidate_registry
            WHERE symbol = :sym AND strategy_id = :sid
            LIMIT 1
            """
        )
        async with db_manager.engine.connect() as conn:
            row = (
                await conn.execute(sql, {"sym": sym, "sid": STRATEGY_ID})
            ).mappings().first()
        return dict(row) if row else None

    async def get_by_id(self, candidate_id: str) -> dict[str, Any] | None:
        if not self.use_db:
            with self._lock:
                row = self._memory.get(str(candidate_id))
                return copy.deepcopy(row) if row else None
        assert db_manager.engine is not None
        sql = text("SELECT * FROM strategy_candidate_registry WHERE id = :id LIMIT 1")
        async with db_manager.engine.connect() as conn:
            row = (await conn.execute(sql, {"id": candidate_id})).mappings().first()
        return dict(row) if row else None

    async def list_candidates(
        self,
        *,
        states: list[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if not self.use_db:
            with self._lock:
                rows = list(self._memory.values())
            if states:
                want = {s.upper() for s in states}
                rows = [r for r in rows if str(r.get("state") or "").upper() in want]
            rows.sort(
                key=lambda r: r.get("updated_at_utc") or r.get("created_at_utc") or "",
                reverse=True,
            )
            return [copy.deepcopy(r) for r in rows[:limit]]

        assert db_manager.engine is not None
        if states:
            placeholders = ", ".join(f":s{i}" for i in range(len(states)))
            params: dict[str, Any] = {f"s{i}": s.upper() for i, s in enumerate(states)}
            params["lim"] = int(limit)
            params["sid"] = STRATEGY_ID
            sql = text(
                f"""
                SELECT * FROM strategy_candidate_registry
                WHERE strategy_id = :sid AND state IN ({placeholders})
                ORDER BY updated_at_utc DESC
                LIMIT :lim
                """
            )
        else:
            params = {"lim": int(limit), "sid": STRATEGY_ID}
            sql = text(
                """
                SELECT * FROM strategy_candidate_registry
                WHERE strategy_id = :sid
                ORDER BY updated_at_utc DESC
                LIMIT :lim
                """
            )
        async with db_manager.engine.connect() as conn:
            result = await conn.execute(sql, params)
            return [dict(r) for r in result.mappings().all()]

    async def list_paper_validating(self) -> list[dict[str, Any]]:
        """Rows the v2 watcher is allowed to consider (still gated further)."""
        rows = await self.list_candidates(states=["PAPER_VALIDATING"], limit=500)
        out: list[dict[str, Any]] = []
        for r in rows:
            if not bool(r.get("operator_approved")):
                continue
            if str(r.get("strategy_id") or "") != STRATEGY_ID:
                continue
            if bool(r.get("telegram_eligible")):
                # Fail closed: never trade a row that somehow became telegram-eligible.
                continue
            risk = float(r.get("risk_percent") or 0)
            if risk <= 0 or risk > 0.005:
                continue
            out.append(r)
        return out

    async def upsert_discovered(
        self,
        *,
        symbol: str,
        selector_version: str,
        manifest_id: str | None,
        rank: int | None,
        volume_usd: float | None,
        discovery_reason: str | None,
        market_type: str = "futures_perp",
        quote_asset: str = "USDT",
    ) -> tuple[dict[str, Any], bool]:
        """Create DISCOVERED or refresh screening metadata. Returns (row, is_new)."""
        sym = str(symbol).upper().strip()
        existing = await self.get_by_symbol(sym)
        now = _utc_now()
        if existing is None:
            row = default_candidate_row(
                sym,
                market_type=market_type,
                quote_asset=quote_asset,
                selector_version=selector_version,
                manifest_id=manifest_id,
                discovery_rank=rank,
                discovery_volume_usd=volume_usd,
                discovery_reason=discovery_reason,
                last_screened_at_utc=now,
            )
            await self._persist(row)
            await self.append_audit(
                candidate_id=row["id"],
                symbol=sym,
                action="DISCOVERED",
                actor="dynamic_candidate_discovery",
                previous_state=None,
                new_state="DISCOVERED",
                previous_risk=None,
                new_risk=0.0,
                note=discovery_reason,
                payload={"rank": rank, "volume_usd": volume_usd},
            )
            return row, True

        # Refresh screening fields; do not reset advanced states automatically.
        existing["last_screened_at_utc"] = now
        existing["selector_version"] = selector_version
        existing["manifest_id"] = manifest_id
        existing["discovery_rank"] = rank
        existing["discovery_volume_usd"] = volume_usd
        existing["discovery_reason"] = discovery_reason
        existing["updated_at_utc"] = now
        # Safety invariants
        existing["telegram_eligible"] = False
        existing["strategy_id"] = STRATEGY_ID
        existing["combo_version"] = COMBO_VERSION
        existing["source"] = SOURCE_PIPELINE
        await self._persist(existing)
        return existing, False

    async def transition(
        self,
        symbol: str,
        new_state: str,
        *,
        reason: str | None = None,
        actor: str = "system",
        operator_approved_action: bool = False,
        extra_fields: dict[str, Any] | None = None,
        allow_background: bool = True,
    ) -> dict[str, Any]:
        """Apply a validated state transition and persist."""
        row = await self.get_by_symbol(symbol)
        if row is None:
            raise KeyError(f"candidate not found: {symbol}")
        cur = str(row.get("state") or "")
        nxt = str(new_state).upper().strip()

        if not allow_background and nxt in OPERATOR_ONLY_STATES:
            # Explicit double-check for background callers that pass the flag wrong.
            from app.research.candidate_state_machine import OperatorOnlyTransition

            raise OperatorOnlyTransition(
                f"background jobs cannot transition to {nxt}"
            )

        assert_transition_allowed(
            cur, nxt, operator_approved_action=operator_approved_action
        )
        now = _utc_now()
        prev_risk = float(row.get("risk_percent") or 0)
        row["state"] = nxt
        row["state_reason"] = reason
        row["state_updated_at_utc"] = now
        row["updated_at_utc"] = now
        # Hard safety: never auto-enable telegram or nonzero risk on job transitions.
        if not operator_approved_action:
            row["telegram_eligible"] = False
            if nxt not in ("PAPER_VALIDATING", "APPROVED"):
                # Keep risk at 0 until operator approval path sets it.
                if nxt != "PAPER_VALIDATING":
                    pass
        row["telegram_eligible"] = False
        if extra_fields:
            for k, v in extra_fields.items():
                if k in ("telegram_eligible",):
                    continue  # ignore attempts to flip via extra_fields
                if k == "risk_percent" and not operator_approved_action:
                    continue
                row[k] = v
        await self._persist(row)
        await self.append_audit(
            candidate_id=str(row["id"]),
            symbol=str(row["symbol"]),
            action=f"TRANSITION:{cur}->{nxt}",
            actor=actor,
            previous_state=cur,
            new_state=nxt,
            previous_risk=prev_risk,
            new_risk=float(row.get("risk_percent") or 0),
            note=reason,
            payload={"operator_approved_action": operator_approved_action},
        )
        return row

    async def update_fields(
        self,
        symbol: str,
        fields: dict[str, Any],
        *,
        actor: str = "system",
        note: str | None = None,
    ) -> dict[str, Any]:
        row = await self.get_by_symbol(symbol)
        if row is None:
            raise KeyError(f"candidate not found: {symbol}")
        blocked = {
            "state",
            "telegram_eligible",
            "operator_approved",
            "strategy_id",
            "combo_version",
            "source",
            "id",
        }
        for k, v in fields.items():
            if k in blocked:
                continue
            row[k] = v
        row["telegram_eligible"] = False
        row["updated_at_utc"] = _utc_now()
        await self._persist(row)
        await self.append_audit(
            candidate_id=str(row["id"]),
            symbol=str(row["symbol"]),
            action="UPDATE_FIELDS",
            actor=actor,
            previous_state=str(row.get("state")),
            new_state=str(row.get("state")),
            previous_risk=float(row.get("risk_percent") or 0),
            new_risk=float(row.get("risk_percent") or 0),
            note=note,
            payload={"keys": sorted(fields.keys())},
        )
        return row

    async def approve_paper(
        self,
        symbol: str,
        *,
        confirm: bool,
        approval_note: str | None,
        requested_risk_percent: float,
        actor: str,
        risk_override_above_half_pct: bool = False,
    ) -> dict[str, Any]:
        """Explicit operator gate: V2_PAPER_CANDIDATE → PAPER_VALIDATING."""
        if not confirm:
            raise PermissionError("confirm must be true")
        row = await self.get_by_symbol(symbol)
        if row is None:
            raise KeyError(f"candidate not found: {symbol}")
        if str(row.get("state") or "") != "V2_PAPER_CANDIDATE":
            raise PermissionError(
                f"reject: state must be V2_PAPER_CANDIDATE, got {row.get('state')}"
            )
        # Require research artifacts
        missing: list[str] = []
        if not row.get("backtest_status"):
            missing.append("base_backtest")
        if not row.get("oos_status"):
            missing.append("oos_report")
        if not row.get("portfolio_report"):
            missing.append("portfolio_report")
        if row.get("ohlcv_1h_completeness") is None or row.get("ohlcv_4h_completeness") is None:
            missing.append("data_health")
        if missing:
            raise PermissionError(f"reject: missing reports: {', '.join(missing)}")

        risk = float(requested_risk_percent)
        if risk <= 0:
            raise PermissionError("reject: requested_risk_percent must be > 0")
        if risk > 0.005 and not risk_override_above_half_pct:
            raise PermissionError(
                "reject: risk > 0.5% requires risk_override_above_half_pct=true"
            )
        if risk > 0.02:
            raise PermissionError("reject: risk_percent hard cap 2%")

        now = _utc_now()
        prev = str(row.get("state"))
        prev_risk = float(row.get("risk_percent") or 0)
        row = await self.transition(
            symbol,
            "PAPER_VALIDATING",
            reason=approval_note or "operator_approved_paper",
            actor=actor,
            operator_approved_action=True,
            extra_fields={
                "operator_approved": True,
                "operator_approved_by": actor,
                "operator_approved_at_utc": now,
                "approval_note": approval_note,
                "risk_percent": risk,
                "telegram_eligible": False,
            },
        )
        # transition() strips telegram via invariant; reaffirm risk/approval
        row["operator_approved"] = True
        row["operator_approved_by"] = actor
        row["operator_approved_at_utc"] = now
        row["approval_note"] = approval_note
        row["risk_percent"] = risk
        row["telegram_eligible"] = False
        await self._persist(row)
        await self.append_audit(
            candidate_id=str(row["id"]),
            symbol=str(row["symbol"]),
            action="APPROVE_PAPER",
            actor=actor,
            previous_state=prev,
            new_state="PAPER_VALIDATING",
            previous_risk=prev_risk,
            new_risk=risk,
            note=approval_note,
            payload={
                "risk_override_above_half_pct": risk_override_above_half_pct,
                "telegram_eligible": False,
            },
        )
        return row

    async def append_audit(
        self,
        *,
        candidate_id: str,
        symbol: str,
        action: str,
        actor: str | None,
        previous_state: str | None,
        new_state: str | None,
        previous_risk: float | None,
        new_risk: float | None,
        note: str | None,
        payload: dict[str, Any] | None,
    ) -> None:
        if not self.use_db:
            with self._lock:
                self._audit_memory(
                    candidate_id=candidate_id,
                    symbol=symbol,
                    action=action,
                    actor=actor,
                    previous_state=previous_state,
                    new_state=new_state,
                    previous_risk=previous_risk,
                    new_risk=new_risk,
                    note=note,
                    payload=payload,
                )
            return
        assert db_manager.engine is not None
        sql = text(
            """
            INSERT INTO strategy_candidate_audit_log (
                candidate_id, symbol, action, actor,
                previous_state, new_state, previous_risk, new_risk,
                note, payload
            ) VALUES (
                :candidate_id, :symbol, :action, :actor,
                :previous_state, :new_state, :previous_risk, :new_risk,
                :note, CAST(:payload AS jsonb)
            )
            """
        )
        async with db_manager.engine.begin() as conn:
            await conn.execute(
                sql,
                {
                    "candidate_id": candidate_id,
                    "symbol": symbol,
                    "action": action,
                    "actor": actor,
                    "previous_state": previous_state,
                    "new_state": new_state,
                    "previous_risk": previous_risk,
                    "new_risk": new_risk,
                    "note": note,
                    "payload": json.dumps(payload or {}),
                },
            )

    async def list_audit(self, symbol: str, *, limit: int = 50) -> list[dict[str, Any]]:
        sym = str(symbol).upper().strip()
        if not self.use_db:
            with self._lock:
                rows = [a for a in self._audit if a["symbol"] == sym]
            return [serialize_candidate(r) for r in reversed(rows[-limit:])]
        assert db_manager.engine is not None
        sql = text(
            """
            SELECT * FROM strategy_candidate_audit_log
            WHERE symbol = :sym
            ORDER BY created_at_utc DESC
            LIMIT :lim
            """
        )
        async with db_manager.engine.connect() as conn:
            result = await conn.execute(sql, {"sym": sym, "lim": int(limit)})
            return [serialize_candidate(dict(r)) for r in result.mappings().all()]

    async def _persist(self, row: dict[str, Any]) -> None:
        row["telegram_eligible"] = False
        row["strategy_id"] = STRATEGY_ID
        row["combo_version"] = COMBO_VERSION
        row["source"] = SOURCE_PIPELINE
        row["updated_at_utc"] = _utc_now()
        if not self.use_db:
            with self._lock:
                self._store_memory(row)
            return
        assert db_manager.engine is not None
        cols = [
            "id",
            "symbol",
            "market_type",
            "quote_asset",
            "discovered_at_utc",
            "last_screened_at_utc",
            "state",
            "state_reason",
            "state_updated_at_utc",
            "selector_version",
            "manifest_id",
            "discovery_rank",
            "discovery_volume_usd",
            "discovery_reason",
            "ohlcv_1h_start_utc",
            "ohlcv_1h_end_utc",
            "ohlcv_1h_completeness",
            "ohlcv_4h_start_utc",
            "ohlcv_4h_end_utc",
            "ohlcv_4h_completeness",
            "history_days",
            "data_health_checked_at_utc",
            "data_health_block_reason",
            "backtest_window_start_utc",
            "backtest_window_end_utc",
            "backtest_engine_fingerprint",
            "backtest_status",
            "backtest_tier",
            "backtest_trade_count",
            "backtest_win_rate",
            "backtest_net_avg_r",
            "backtest_profit_factor",
            "backtest_net_pnl",
            "backtest_fees",
            "backtest_max_dd_r",
            "backtest_max_losing_streak",
            "oos_status",
            "oos_window_start_utc",
            "oos_window_end_utc",
            "oos_trade_count",
            "oos_net_avg_r",
            "oos_profit_factor",
            "oos_net_pnl",
            "oos_max_dd_r",
            "oos_max_losing_streak",
            "portfolio_overlap_btc",
            "portfolio_overlap_eth",
            "portfolio_overlap_sol",
            "peak_concurrent_positions",
            "portfolio_incremental_dd_r",
            "portfolio_report",
            "risk_percent",
            "operator_approved",
            "operator_approved_by",
            "operator_approved_at_utc",
            "approval_note",
            "strategy_id",
            "combo_version",
            "source",
            "telegram_eligible",
            "created_at_utc",
            "updated_at_utc",
        ]
        params: dict[str, Any] = {}
        for c in cols:
            v = row.get(c)
            if c in ("backtest_engine_fingerprint", "portfolio_report") and not isinstance(
                v, str
            ):
                params[c] = json.dumps(v or {})
            elif isinstance(v, datetime):
                params[c] = v
            else:
                params[c] = v
        col_list = ", ".join(cols)
        placeholders = ", ".join(
            f"CAST(:{c} AS jsonb)"
            if c in ("backtest_engine_fingerprint", "portfolio_report")
            else f":{c}"
            for c in cols
        )
        updates = ", ".join(
            f"{c} = EXCLUDED.{c}" for c in cols if c not in ("id", "created_at_utc")
        )
        sql = text(
            f"""
            INSERT INTO strategy_candidate_registry ({col_list})
            VALUES ({placeholders})
            ON CONFLICT (symbol, strategy_id) DO UPDATE SET {updates}
            """
        )
        async with db_manager.engine.begin() as conn:
            await conn.execute(sql, params)


# Process singleton (tests may call reset_memory / force_memory).
strategy_candidate_registry = StrategyCandidateRegistry()
