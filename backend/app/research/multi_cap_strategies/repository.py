"""Persist multi-cap research runs (memory fallback when DB unavailable).

Does not touch research_strategy_runs used by S1/S2/S3 — separate tables.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.core.logging import get_logger
from app.services.database import db_manager

logger = get_logger("multi_cap_strategies_repository")

_MEMORY_RUNS: dict[str, dict[str, Any]] = {}

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS research_multi_cap_runs (
        run_id              TEXT PRIMARY KEY,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        configuration_hash  TEXT NOT NULL,
        research_engine_version TEXT NOT NULL,
        data_period         JSONB NOT NULL DEFAULT '{}',
        configuration       JSONB NOT NULL DEFAULT '{}',
        symbols             JSONB NOT NULL DEFAULT '[]',
        timeframes          JSONB NOT NULL DEFAULT '[]',
        strategies_tested   INT NOT NULL DEFAULT 0,
        elapsed_seconds     DOUBLE PRECISION,
        payload             JSONB NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_multi_cap_events (
        id                  BIGSERIAL PRIMARY KEY,
        run_id              TEXT NOT NULL REFERENCES research_multi_cap_runs(run_id) ON DELETE CASCADE,
        strategy_id         TEXT NOT NULL,
        symbol              TEXT NOT NULL,
        timeframe           TEXT NOT NULL,
        event_time          TIMESTAMPTZ,
        event_type          TEXT NOT NULL,
        direction           TEXT,
        price               DOUBLE PRECISION,
        reference_level     DOUBLE PRECISION,
        atr                 DOUBLE PRECISION,
        volume_ratio        DOUBLE PRECISION,
        payload             JSONB NOT NULL DEFAULT '{}',
        data_version        TEXT,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_multi_cap_events_run ON research_multi_cap_events (run_id, strategy_id)",
]


async def ensure_schema() -> bool:
    if not db_manager.enabled or db_manager.engine is None:
        return False
    try:
        async with db_manager.engine.begin() as conn:
            for stmt in SCHEMA:
                await conn.execute(text(stmt))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("multi_cap_schema_failed", error=str(exc))
        return False


def _new_run_id() -> str:
    return str(uuid.uuid4())


async def save_run(summary: dict[str, Any]) -> str:
    run_id = summary.get("run_id") or _new_run_id()
    summary = {**summary, "run_id": run_id}
    created = summary.get("created_at") or datetime.now(timezone.utc).isoformat()
    summary["created_at"] = created

    if not db_manager.enabled or db_manager.engine is None:
        _MEMORY_RUNS[run_id] = summary
        return run_id

    await ensure_schema()
    try:
        async with db_manager.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO research_multi_cap_runs (
                        run_id, created_at, configuration_hash, research_engine_version,
                        data_period, configuration, symbols, timeframes,
                        strategies_tested, elapsed_seconds, payload
                    ) VALUES (
                        :run_id, :created_at, :configuration_hash, :research_engine_version,
                        CAST(:data_period AS jsonb), CAST(:configuration AS jsonb),
                        CAST(:symbols AS jsonb), CAST(:timeframes AS jsonb),
                        :strategies_tested, :elapsed_seconds, CAST(:payload AS jsonb)
                    )
                    ON CONFLICT (run_id) DO UPDATE SET payload = EXCLUDED.payload
                    """
                ),
                {
                    "run_id": run_id,
                    "created_at": created,
                    "configuration_hash": summary.get("configuration_hash") or "",
                    "research_engine_version": summary.get("research_engine_version") or "",
                    "data_period": json.dumps(summary.get("data_period") or {}),
                    "configuration": json.dumps(summary.get("configuration") or {}),
                    "symbols": json.dumps(summary.get("symbols") or []),
                    "timeframes": json.dumps(summary.get("timeframes") or []),
                    "strategies_tested": int(summary.get("strategies_tested") or 0),
                    "elapsed_seconds": summary.get("elapsed_seconds"),
                    "payload": json.dumps(summary),
                },
            )
            for ev in summary.get("events") or []:
                await conn.execute(
                    text(
                        """
                        INSERT INTO research_multi_cap_events (
                            run_id, strategy_id, symbol, timeframe, event_time,
                            event_type, direction, price, reference_level, atr,
                            volume_ratio, payload, data_version
                        ) VALUES (
                            :run_id, :strategy_id, :symbol, :timeframe, :event_time,
                            :event_type, :direction, :price, :reference_level, :atr,
                            :volume_ratio, CAST(:payload AS jsonb), :data_version
                        )
                        """
                    ),
                    {
                        "run_id": run_id,
                        "strategy_id": ev.get("strategy_id"),
                        "symbol": ev.get("symbol"),
                        "timeframe": ev.get("timeframe"),
                        "event_time": ev.get("event_time"),
                        "event_type": ev.get("event_type"),
                        "direction": ev.get("direction"),
                        "price": ev.get("price"),
                        "reference_level": ev.get("reference_level"),
                        "atr": ev.get("atr"),
                        "volume_ratio": ev.get("volume_ratio"),
                        "payload": json.dumps(ev.get("payload") or {}),
                        "data_version": ev.get("data_version"),
                    },
                )
        _MEMORY_RUNS[run_id] = summary
        return run_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("multi_cap_save_failed", error=str(exc))
        _MEMORY_RUNS[run_id] = summary
        return run_id


async def load_run(run_id: str) -> dict[str, Any] | None:
    if run_id in _MEMORY_RUNS:
        return _MEMORY_RUNS[run_id]
    if not db_manager.enabled or db_manager.engine is None:
        return None
    try:
        await ensure_schema()
        async with db_manager.engine.begin() as conn:
            result = await conn.execute(
                text("SELECT payload FROM research_multi_cap_runs WHERE run_id = :run_id"),
                {"run_id": run_id},
            )
            row = result.fetchone()
            if not row:
                return None
            payload = row[0]
            if isinstance(payload, str):
                return json.loads(payload)
            return dict(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("multi_cap_load_failed", error=str(exc))
        return None
