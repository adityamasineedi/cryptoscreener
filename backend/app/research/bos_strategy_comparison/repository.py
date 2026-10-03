"""Persistence for BOS strategy comparison runs (separate from combo research)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.core.logging import get_logger
from app.services.database import db_manager

logger = get_logger("bos_strategy_comparison_repository")

_MEMORY_RUNS: dict[str, dict[str, Any]] = {}
_MEMORY_RESULTS: list[dict[str, Any]] = []
_MEMORY_TRADES: list[dict[str, Any]] = []

STRATEGY_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS research_strategy_runs (
        run_id              TEXT PRIMARY KEY,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        configuration_hash  TEXT NOT NULL,
        signal_engine_version TEXT NOT NULL,
        research_engine_version TEXT NOT NULL,
        data_period         JSONB NOT NULL DEFAULT '{}',
        configuration       JSONB NOT NULL DEFAULT '{}',
        symbols             JSONB NOT NULL DEFAULT '[]',
        timeframes          JSONB NOT NULL DEFAULT '[]',
        strategies_tested   INT NOT NULL DEFAULT 0,
        elapsed_seconds     DOUBLE PRECISION,
        candles_processed   INT NOT NULL DEFAULT 0,
        setups_processed    INT NOT NULL DEFAULT 0,
        data_coverage       JSONB NOT NULL DEFAULT '{}',
        payload             JSONB NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_strategy_results (
        id                  BIGSERIAL PRIMARY KEY,
        run_id              TEXT NOT NULL REFERENCES research_strategy_runs(run_id) ON DELETE CASCADE,
        strategy_id         TEXT NOT NULL,
        direction           TEXT,
        sample_size         INT NOT NULL DEFAULT 0,
        payload             JSONB NOT NULL DEFAULT '{}',
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_strategy_trades (
        id                  BIGSERIAL PRIMARY KEY,
        run_id              TEXT NOT NULL REFERENCES research_strategy_runs(run_id) ON DELETE CASCADE,
        strategy_id         TEXT NOT NULL,
        symbol              TEXT NOT NULL,
        timeframe           TEXT NOT NULL,
        direction           TEXT NOT NULL,
        entry_time          TIMESTAMPTZ,
        entry_price         DOUBLE PRECISION,
        sl                  DOUBLE PRECISION,
        tp1                 DOUBLE PRECISION,
        tp2                 DOUBLE PRECISION,
        tp3                 DOUBLE PRECISION,
        exit_time           TIMESTAMPTZ,
        exit_price          DOUBLE PRECISION,
        exit_reason         TEXT,
        gross_r             DOUBLE PRECISION,
        net_r               DOUBLE PRECISION,
        htf_alignment       TEXT,
        payload             JSONB NOT NULL DEFAULT '{}',
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_research_strategy_results_run ON research_strategy_results (run_id, strategy_id)",
    "CREATE INDEX IF NOT EXISTS idx_research_strategy_trades_run ON research_strategy_trades (run_id, strategy_id, symbol)",
]


async def ensure_strategy_research_schema() -> bool:
    if not db_manager.enabled or db_manager.engine is None:
        return False
    try:
        async with db_manager.engine.begin() as conn:
            for stmt in STRATEGY_SCHEMA:
                await conn.execute(text(stmt))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("strategy_research_schema_failed", error=str(exc))
        return False


def _new_run_id() -> str:
    return str(uuid.uuid4())


def _as_dt(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str) and raw.strip():
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


async def save_strategy_run(summary: dict[str, Any]) -> str:
    run_id = summary.get("run_id") or _new_run_id()
    summary = {**summary, "run_id": run_id}
    created = _as_dt(summary.get("created_at")) or datetime.now(timezone.utc)

    if not db_manager.enabled or db_manager.engine is None:
        _MEMORY_RUNS[run_id] = summary
        for row in summary.get("results") or []:
            _MEMORY_RESULTS.append({**row, "run_id": run_id})
        for t in summary.get("trades") or []:
            _MEMORY_TRADES.append({**t, "run_id": run_id})
        return run_id

    await ensure_strategy_research_schema()
    async with db_manager.engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO research_strategy_runs (
                    run_id, created_at, configuration_hash,
                    signal_engine_version, research_engine_version,
                    data_period, configuration, symbols, timeframes,
                    strategies_tested, elapsed_seconds, candles_processed,
                    setups_processed, data_coverage, payload
                ) VALUES (
                    :run_id, :created_at, :configuration_hash,
                    :signal_engine_version, :research_engine_version,
                    CAST(:data_period AS JSONB), CAST(:configuration AS JSONB),
                    CAST(:symbols AS JSONB), CAST(:timeframes AS JSONB),
                    :strategies_tested, :elapsed_seconds, :candles_processed,
                    :setups_processed, CAST(:data_coverage AS JSONB),
                    CAST(:payload AS JSONB)
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
                "strategies_tested": int(summary.get("strategies_tested") or 0),
                "elapsed_seconds": summary.get("elapsed_seconds"),
                "candles_processed": int(summary.get("candles_processed") or 0),
                "setups_processed": int(summary.get("setups_processed") or 0),
                "data_coverage": json.dumps(summary.get("data_coverage") or {}),
                "payload": json.dumps(
                    {
                        k: summary.get(k)
                        for k in (
                            "disclaimer",
                            "condition_contribution",
                            "universe",
                            "fee_assumptions",
                            "label",
                        )
                    },
                    default=str,
                ),
            },
        )
        for row in summary.get("results") or []:
            await conn.execute(
                text(
                    """
                    INSERT INTO research_strategy_results (
                        run_id, strategy_id, direction, sample_size, payload
                    ) VALUES (
                        :run_id, :strategy_id, :direction, :sample_size,
                        CAST(:payload AS JSONB)
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "strategy_id": row.get("strategy_id") or "",
                    "direction": row.get("direction") or "ALL",
                    "sample_size": int(row.get("sample_size") or 0),
                    "payload": json.dumps(row, default=str),
                },
            )
        for t in summary.get("trades") or []:
            await conn.execute(
                text(
                    """
                    INSERT INTO research_strategy_trades (
                        run_id, strategy_id, symbol, timeframe, direction,
                        entry_time, entry_price, sl, tp1, tp2, tp3,
                        exit_time, exit_price, exit_reason, gross_r, net_r,
                        htf_alignment, payload
                    ) VALUES (
                        :run_id, :strategy_id, :symbol, :timeframe, :direction,
                        :entry_time, :entry_price, :sl,
                        :tp1, :tp2, :tp3,
                        :exit_time, :exit_price, :exit_reason,
                        :gross_r, :net_r, :htf_alignment, CAST(:payload AS JSONB)
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "strategy_id": t.get("strategy_id") or "",
                    "symbol": t.get("symbol") or "",
                    "timeframe": t.get("timeframe") or "",
                    "direction": t.get("direction") or "",
                    "entry_time": _as_dt(t.get("entry_time")),
                    "entry_price": t.get("entry_price"),
                    "sl": t.get("sl"),
                    "tp1": t.get("tp1"),
                    "tp2": t.get("tp2"),
                    "tp3": t.get("tp3"),
                    "exit_time": _as_dt(t.get("exit_time")),
                    "exit_price": t.get("exit_price"),
                    "exit_reason": t.get("exit_reason"),
                    "gross_r": t.get("gross_R"),
                    "net_r": t.get("net_R"),
                    "htf_alignment": t.get("htf_alignment"),
                    "payload": json.dumps(t, default=str),
                },
            )
    return run_id


async def load_latest_strategy_run() -> dict[str, Any] | None:
    if not db_manager.enabled or db_manager.engine is None:
        if not _MEMORY_RUNS:
            return None
        latest = max(_MEMORY_RUNS.values(), key=lambda r: r.get("created_at") or "")
        return latest

    await ensure_strategy_research_schema()
    async with db_manager.engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT run_id, created_at, configuration_hash,
                       signal_engine_version, research_engine_version,
                       data_period, configuration, symbols, timeframes,
                       strategies_tested, elapsed_seconds, candles_processed,
                       setups_processed, data_coverage, payload
                FROM research_strategy_runs
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
        )
        row = result.fetchone()
        if row is None:
            return None
        run_id = row[0]
        res = await conn.execute(
            text(
                "SELECT payload FROM research_strategy_results WHERE run_id = :run_id"
            ),
            {"run_id": run_id},
        )
        results = [r[0] for r in res.fetchall()]
        return {
            "run_id": run_id,
            "created_at": row[1].isoformat() if row[1] else None,
            "configuration_hash": row[2],
            "signal_engine_version": row[3],
            "research_engine_version": row[4],
            "data_period": row[5] or {},
            "configuration": row[6] or {},
            "symbols": row[7] or [],
            "timeframes": row[8] or [],
            "strategies_tested": row[9],
            "elapsed_seconds": row[10],
            "candles_processed": row[11],
            "setups_processed": row[12],
            "data_coverage": row[13] or {},
            "payload": row[14] or {},
            "results": results,
        }
