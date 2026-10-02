"""Persistence for research runs — separate from live setup signal tables."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.core.logging import get_logger
from app.services.database import db_manager

logger = get_logger("research_repository")

# In-memory fallback when DATABASE_ENABLED=false
_MEMORY_RUNS: dict[str, dict[str, Any]] = {}
_MEMORY_RESULTS: list[dict[str, Any]] = []
_MEMORY_TRADES: list[dict[str, Any]] = []


RESEARCH_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS research_runs (
        run_id              TEXT PRIMARY KEY,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        configuration_hash  TEXT NOT NULL,
        signal_engine_version TEXT NOT NULL,
        research_engine_version TEXT NOT NULL,
        data_period         JSONB NOT NULL DEFAULT '{}',
        configuration       JSONB NOT NULL DEFAULT '{}',
        symbols             JSONB NOT NULL DEFAULT '[]',
        timeframes          JSONB NOT NULL DEFAULT '[]',
        combinations_tested INT NOT NULL DEFAULT 0,
        parameters_tested   INT NOT NULL DEFAULT 1,
        multiple_testing_risk BOOLEAN NOT NULL DEFAULT FALSE,
        elapsed_seconds     DOUBLE PRECISION,
        candles_processed   INT NOT NULL DEFAULT 0,
        setups_processed    INT NOT NULL DEFAULT 0,
        data_coverage       JSONB NOT NULL DEFAULT '{}',
        payload             JSONB NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_results (
        id                  BIGSERIAL PRIMARY KEY,
        run_id              TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
        combination_id      TEXT NOT NULL,
        symbol              TEXT,
        timeframe           TEXT,
        direction           TEXT,
        period_label        TEXT,
        sample_size         INT NOT NULL DEFAULT 0,
        payload             JSONB NOT NULL DEFAULT '{}',
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_trades (
        id                  BIGSERIAL PRIMARY KEY,
        run_id              TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
        combination_id      TEXT NOT NULL,
        symbol              TEXT NOT NULL,
        timeframe           TEXT NOT NULL,
        direction           TEXT NOT NULL,
        entry_index         INT,
        signal_time         TIMESTAMPTZ,
        entry_price         DOUBLE PRECISION,
        stop_price          DOUBLE PRECISION,
        tp1                 DOUBLE PRECISION,
        tp2                 DOUBLE PRECISION,
        tp3                 DOUBLE PRECISION,
        outcome             TEXT,
        r_multiple          DOUBLE PRECISION,
        mae_r               DOUBLE PRECISION,
        mfe_r               DOUBLE PRECISION,
        ambiguous           BOOLEAN NOT NULL DEFAULT FALSE,
        payload             JSONB NOT NULL DEFAULT '{}',
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_research_results_run ON research_results (run_id, combination_id)",
    "CREATE INDEX IF NOT EXISTS idx_research_trades_run ON research_trades (run_id, combination_id, symbol)",
]


async def ensure_research_schema() -> bool:
    if not db_manager.enabled or db_manager.engine is None:
        return False
    try:
        async with db_manager.engine.begin() as conn:
            for stmt in RESEARCH_SCHEMA:
                await conn.execute(text(stmt))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("research_schema_failed", error=str(exc))
        return False


def _new_run_id() -> str:
    return str(uuid.uuid4())


async def save_research_run(summary: dict[str, Any]) -> str:
    """Persist a research run. Never writes to setup_analyses / live signal tables."""
    run_id = summary.get("run_id") or _new_run_id()
    summary = {**summary, "run_id": run_id}
    created = summary.get("created_at") or datetime.now(timezone.utc).isoformat()

    if not db_manager.enabled or db_manager.engine is None:
        _MEMORY_RUNS[run_id] = summary
        for row in summary.get("results") or []:
            _MEMORY_RESULTS.append({**row, "run_id": run_id})
        for t in summary.get("trades") or []:
            _MEMORY_TRADES.append({**t, "run_id": run_id})
        return run_id

    await ensure_research_schema()
    async with db_manager.engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO research_runs (
                    run_id, created_at, configuration_hash,
                    signal_engine_version, research_engine_version,
                    data_period, configuration, symbols, timeframes,
                    combinations_tested, parameters_tested, multiple_testing_risk,
                    elapsed_seconds, candles_processed, setups_processed,
                    data_coverage, payload
                ) VALUES (
                    :run_id, CAST(:created_at AS TIMESTAMPTZ), :configuration_hash,
                    :signal_engine_version, :research_engine_version,
                    CAST(:data_period AS JSONB), CAST(:configuration AS JSONB),
                    CAST(:symbols AS JSONB), CAST(:timeframes AS JSONB),
                    :combinations_tested, :parameters_tested, :multiple_testing_risk,
                    :elapsed_seconds, :candles_processed, :setups_processed,
                    CAST(:data_coverage AS JSONB), CAST(:payload AS JSONB)
                )
                ON CONFLICT (run_id) DO UPDATE SET payload = EXCLUDED.payload
                """
            ),
            {
                "run_id": run_id,
                "created_at": created,
                "configuration_hash": summary.get("configuration_hash") or "",
                "signal_engine_version": summary.get("signal_engine_version") or "",
                "research_engine_version": summary.get("research_engine_version") or "",
                "data_period": json.dumps(summary.get("data_period") or {}),
                "configuration": json.dumps(summary.get("configuration") or {}),
                "symbols": json.dumps(summary.get("symbols") or []),
                "timeframes": json.dumps(summary.get("timeframes") or []),
                "combinations_tested": int(summary.get("combinations_tested") or 0),
                "parameters_tested": int(summary.get("parameters_tested") or 1),
                "multiple_testing_risk": bool(summary.get("multiple_testing_risk")),
                "elapsed_seconds": summary.get("elapsed_seconds"),
                "candles_processed": int(summary.get("candles_processed") or 0),
                "setups_processed": int(summary.get("setups_processed") or 0),
                "data_coverage": json.dumps(summary.get("data_coverage") or {}),
                "payload": json.dumps(summary),
            },
        )
        for row in summary.get("results") or []:
            await conn.execute(
                text(
                    """
                    INSERT INTO research_results (
                        run_id, combination_id, symbol, timeframe, direction,
                        period_label, sample_size, payload
                    ) VALUES (
                        :run_id, :combination_id, :symbol, :timeframe, :direction,
                        :period_label, :sample_size, CAST(:payload AS JSONB)
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "combination_id": row.get("combination_id"),
                    "symbol": row.get("symbol"),
                    "timeframe": row.get("timeframe"),
                    "direction": row.get("direction"),
                    "period_label": row.get("period_label"),
                    "sample_size": int(row.get("sample_size") or 0),
                    "payload": json.dumps(row),
                },
            )
        for t in summary.get("trades") or []:
            await conn.execute(
                text(
                    """
                    INSERT INTO research_trades (
                        run_id, combination_id, symbol, timeframe, direction,
                        entry_index, signal_time, entry_price, stop_price,
                        tp1, tp2, tp3, outcome, r_multiple, mae_r, mfe_r,
                        ambiguous, payload
                    ) VALUES (
                        :run_id, :combination_id, :symbol, :timeframe, :direction,
                        :entry_index, CAST(:signal_time AS TIMESTAMPTZ),
                        :entry_price, :stop_price, :tp1, :tp2, :tp3,
                        :outcome, :r_multiple, :mae_r, :mfe_r, :ambiguous,
                        CAST(:payload AS JSONB)
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "combination_id": t.get("combination_id"),
                    "symbol": t.get("symbol"),
                    "timeframe": t.get("timeframe"),
                    "direction": t.get("direction"),
                    "entry_index": t.get("entry_index"),
                    "signal_time": t.get("signal_time"),
                    "entry_price": t.get("entry_price"),
                    "stop_price": t.get("stop_price"),
                    "tp1": t.get("tp1"),
                    "tp2": t.get("tp2"),
                    "tp3": t.get("tp3"),
                    "outcome": t.get("outcome"),
                    "r_multiple": t.get("r_multiple"),
                    "mae_r": t.get("mae_r"),
                    "mfe_r": t.get("mfe_r"),
                    "ambiguous": bool(t.get("ambiguous")),
                    "payload": json.dumps(t),
                },
            )
    _MEMORY_RUNS[run_id] = summary
    return run_id


async def get_research_run(run_id: str) -> dict[str, Any] | None:
    if run_id in _MEMORY_RUNS:
        return _MEMORY_RUNS[run_id]
    if not db_manager.enabled or db_manager.engine is None:
        return None
    await ensure_research_schema()
    async with db_manager.engine.begin() as conn:
        res = await conn.execute(
            text("SELECT payload FROM research_runs WHERE run_id = :run_id"),
            {"run_id": run_id},
        )
        row = res.fetchone()
        if not row:
            return None
        payload = row[0]
        return payload if isinstance(payload, dict) else json.loads(payload)


async def list_research_results(
    *,
    combination_id: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    direction: str | None = None,
    minimum_sample_size: int = 0,
    limit: int = 200,
) -> list[dict[str, Any]]:
    rows = list(_MEMORY_RESULTS)
    if db_manager.enabled and db_manager.engine is not None:
        await ensure_research_schema()
        async with db_manager.engine.begin() as conn:
            res = await conn.execute(
                text(
                    """
                    SELECT payload FROM research_results
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
            for r in res.fetchall():
                payload = r[0]
                rows.append(payload if isinstance(payload, dict) else json.loads(payload))

    out = []
    for row in rows:
        if combination_id and row.get("combination_id") != combination_id:
            continue
        if symbol and str(row.get("symbol") or "").upper() != symbol.upper():
            continue
        if timeframe and row.get("timeframe") != timeframe:
            continue
        if direction and direction.upper() not in ("ALL", "") and row.get("direction") not in (
            direction.upper(),
            "ALL",
            None,
        ):
            if row.get("direction") != direction.upper():
                continue
        if int(row.get("sample_size") or 0) < minimum_sample_size:
            continue
        out.append(row)
    return out[:limit]


def memory_clear() -> None:
    """Test helper — does not touch production live tables."""
    _MEMORY_RUNS.clear()
    _MEMORY_RESULTS.clear()
    _MEMORY_TRADES.clear()
