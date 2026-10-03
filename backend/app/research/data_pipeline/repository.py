"""Research-pipeline persistence.

- Additive OHLCV inserts: ON CONFLICT DO NOTHING (never overwrite valid candles)
- Research-only tables for manifest / features / events / dataset versions
- Does not truncate, drop, or delete production historical data
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import text

from app.core.logging import get_logger
from app.research.data_pipeline.checkpoint import SeriesCheckpoint
from app.services.database import db_manager

logger = get_logger("research_data_pipeline.repository")

RESEARCH_PIPELINE_DDL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS research_data_manifest (
        symbol              TEXT NOT NULL,
        timeframe           TEXT NOT NULL,
        requested_start     TEXT NOT NULL,
        requested_end       TEXT NOT NULL,
        actual_first        TEXT,
        actual_last         TEXT,
        candles_downloaded  INT NOT NULL DEFAULT 0,
        chunks_completed    INT NOT NULL DEFAULT 0,
        last_successful_chunk INT,
        last_successful_chunk_end_ms BIGINT,
        status              TEXT NOT NULL DEFAULT 'PENDING',
        last_error          TEXT,
        quality             TEXT,
        gap_count           INT NOT NULL DEFAULT 0,
        duplicate_count     INT NOT NULL DEFAULT 0,
        invalid_rows        INT NOT NULL DEFAULT 0,
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (symbol, timeframe)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_dataset_versions (
        dataset_version     TEXT PRIMARY KEY,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        pipeline_version    TEXT NOT NULL,
        feature_version     TEXT,
        data_fingerprint    TEXT,
        git_commit          TEXT,
        symbols             JSONB NOT NULL DEFAULT '[]',
        timeframes          JSONB NOT NULL DEFAULT '[]',
        period_start        TEXT,
        period_end          TEXT,
        candle_counts       JSONB NOT NULL DEFAULT '{}',
        quality_report      JSONB NOT NULL DEFAULT '{}',
        payload             JSONB NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_pipeline_runs (
        run_id              TEXT PRIMARY KEY,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        git_commit          TEXT,
        pipeline_version    TEXT NOT NULL,
        feature_version     TEXT NOT NULL,
        data_version        TEXT,
        configuration_hash  TEXT NOT NULL,
        configuration       JSONB NOT NULL DEFAULT '{}',
        symbols             JSONB NOT NULL DEFAULT '[]',
        timeframes          JSONB NOT NULL DEFAULT '[]',
        period_start        TEXT,
        period_end          TEXT,
        status              TEXT NOT NULL DEFAULT 'RUNNING',
        metrics             JSONB NOT NULL DEFAULT '{}',
        quality_report      JSONB NOT NULL DEFAULT '{}',
        failed_symbols      JSONB NOT NULL DEFAULT '[]',
        finished_at         TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_feature_cache (
        symbol              TEXT NOT NULL,
        timeframe           TEXT NOT NULL,
        feature_version     TEXT NOT NULL,
        data_fingerprint    TEXT NOT NULL,
        first_time          TIMESTAMPTZ,
        last_time           TIMESTAMPTZ,
        bar_count           INT NOT NULL DEFAULT 0,
        status              TEXT NOT NULL DEFAULT 'COMPLETE',
        payload             JSONB NOT NULL DEFAULT '{}',
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (symbol, timeframe, feature_version, data_fingerprint)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_bos_events (
        id                  BIGSERIAL PRIMARY KEY,
        dataset_version     TEXT NOT NULL,
        feature_version     TEXT NOT NULL,
        symbol              TEXT NOT NULL,
        timestamp           TIMESTAMPTZ NOT NULL,
        timeframe           TEXT NOT NULL,
        direction           TEXT NOT NULL,
        swing_price         DOUBLE PRECISION,
        bos_price           DOUBLE PRECISION,
        bos_distance        DOUBLE PRECISION,
        atr                 DOUBLE PRECISION,
        trend               TEXT,
        htf_alignment       TEXT,
        mtf_state           TEXT,
        impulse             BOOLEAN,
        pullback            BOOLEAN,
        retest              BOOLEAN,
        sd_state            TEXT,
        rvol                DOUBLE PRECISION,
        bar_index           INT,
        payload             JSONB NOT NULL DEFAULT '{}',
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (dataset_version, feature_version, symbol, timeframe, timestamp, direction)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_gap_reports (
        id                  BIGSERIAL PRIMARY KEY,
        symbol              TEXT NOT NULL,
        timeframe           TEXT NOT NULL,
        kind                TEXT NOT NULL DEFAULT 'GAP_DETECTED',
        start_time          TIMESTAMPTZ NOT NULL,
        end_time            TIMESTAMPTZ NOT NULL,
        duration_ms         BIGINT,
        expected_candles    INT,
        missing_candles     INT,
        dataset_version     TEXT,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_research_bos_events_lookup "
    "ON research_bos_events (symbol, timeframe, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_research_bos_events_dataset "
    "ON research_bos_events (dataset_version, feature_version, symbol)",
    "CREATE INDEX IF NOT EXISTS idx_research_gap_reports_sym "
    "ON research_gap_reports (symbol, timeframe, start_time)",
    "CREATE INDEX IF NOT EXISTS idx_research_manifest_status "
    "ON research_data_manifest (status, updated_at DESC)",
    # Research query helper — does not drop or replace existing PK
    "CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_tf_time "
    "ON ohlcv (symbol, timeframe, time)",
    # Multi-agent / multi-process sync coordination (additive, idempotent)
    """
    CREATE TABLE IF NOT EXISTS research_sync_locks (
        lock_key            TEXT PRIMARY KEY,
        operation           TEXT NOT NULL,
        symbol              TEXT NOT NULL,
        timeframe           TEXT NOT NULL,
        range_start_ms      BIGINT NOT NULL,
        range_end_ms        BIGINT NOT NULL,
        run_id              TEXT NOT NULL,
        pid                 INT,
        hostname            TEXT,
        status              TEXT NOT NULL DEFAULT 'ACTIVE',
        acquired_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_heartbeat      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_research_sync_locks_active "
    "ON research_sync_locks (operation, symbol, timeframe, status)",
    """
    CREATE TABLE IF NOT EXISTS research_data_conflicts (
        id                  BIGSERIAL PRIMARY KEY,
        run_id              TEXT,
        symbol              TEXT NOT NULL,
        timeframe           TEXT NOT NULL,
        candle_time         TIMESTAMPTZ NOT NULL,
        existing_open       DOUBLE PRECISION,
        existing_high       DOUBLE PRECISION,
        existing_low        DOUBLE PRECISION,
        existing_close      DOUBLE PRECISION,
        existing_volume     DOUBLE PRECISION,
        incoming_open       DOUBLE PRECISION,
        incoming_high       DOUBLE PRECISION,
        incoming_low        DOUBLE PRECISION,
        incoming_close      DOUBLE PRECISION,
        incoming_volume     DOUBLE PRECISION,
        source              TEXT,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_research_data_conflicts_sym "
    "ON research_data_conflicts (symbol, timeframe, candle_time)",
    """
    CREATE TABLE IF NOT EXISTS research_sync_tasks (
        task_id             TEXT PRIMARY KEY,
        run_id              TEXT,
        symbol              TEXT NOT NULL,
        timeframe           TEXT NOT NULL,
        range_start_ms      BIGINT NOT NULL,
        range_end_ms        BIGINT NOT NULL,
        status              TEXT NOT NULL DEFAULT 'PENDING',
        claimed_by          TEXT,
        attempt             INT NOT NULL DEFAULT 0,
        rows_received       INT NOT NULL DEFAULT 0,
        rows_inserted       INT NOT NULL DEFAULT 0,
        last_error          TEXT,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_heartbeat      TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_research_sync_tasks_claim "
    "ON research_sync_tasks (status, timeframe, symbol)",
]


async def ensure_research_pipeline_schema() -> None:
    if db_manager.engine is None:
        raise RuntimeError("DATABASE unavailable")
    async with db_manager.engine.begin() as conn:
        for stmt in RESEARCH_PIPELINE_DDL:
            await conn.execute(text(stmt))
    logger.info("research_pipeline_schema_ready", statements=len(RESEARCH_PIPELINE_DDL))


async def insert_ohlcv_do_nothing(
    rows: Sequence[Mapping[str, Any]],
    *,
    batch_size: int = 1000,
) -> int:
    """Idempotent insert of real Binance candles. Never overwrites existing rows."""
    if not rows or db_manager.engine is None:
        return 0
    sql = text(
        """
        INSERT INTO ohlcv (
            time, symbol, timeframe, open, high, low, close, volume,
            quote_volume, trade_count, taker_buy_base, taker_buy_quote, source
        ) VALUES (
            :time, :symbol, :timeframe, :open, :high, :low, :close, :volume,
            :quote_volume, :trade_count, :taker_buy_base, :taker_buy_quote, :source
        )
        ON CONFLICT (time, symbol, timeframe) DO NOTHING
        """
    )
    written = 0
    async with db_manager.engine.begin() as conn:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            for row in batch:
                result = await conn.execute(
                    sql,
                    {
                        "time": row["time"],
                        "symbol": row["symbol"],
                        "timeframe": row["timeframe"],
                        "open": row["open"],
                        "high": row["high"],
                        "low": row["low"],
                        "close": row["close"],
                        "volume": row["volume"],
                        "quote_volume": row.get("quote_volume"),
                        "trade_count": row.get("trade_count"),
                        "taker_buy_base": row.get("taker_buy_base"),
                        "taker_buy_quote": row.get("taker_buy_quote"),
                        "source": row.get("source") or "binance_research_pipeline",
                    },
                )
                written += int(result.rowcount or 0)
    return written


async def series_bounds(symbol: str, timeframe: str) -> tuple[datetime | None, datetime | None, int]:
    if db_manager.engine is None:
        return None, None, 0
    async with db_manager.engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT MIN(time), MAX(time), COUNT(*)
                    FROM ohlcv
                    WHERE symbol = :s AND timeframe = :tf
                    """
                ),
                {"s": symbol.upper(), "tf": timeframe},
            )
        ).fetchone()
    if not row:
        return None, None, 0
    return row[0], row[1], int(row[2] or 0)


async def series_coverage_stats(
    symbol: str,
    timeframe: str,
) -> dict[str, Any]:
    """Efficient aggregation for coverage (MIN/MAX/COUNT/DISTINCT). No SELECT *."""
    empty = {
        "min_time": None,
        "max_time": None,
        "row_count": 0,
        "distinct_count": 0,
        "duplicate_count": 0,
        "invalid_ohlc_count": 0,
    }
    if db_manager.engine is None:
        return empty
    async with db_manager.engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT
                        MIN(time),
                        MAX(time),
                        COUNT(*),
                        COUNT(DISTINCT time),
                        COUNT(*) FILTER (
                            WHERE high < GREATEST(open, close)
                               OR low > LEAST(open, close)
                               OR high < low
                               OR volume < 0
                        )
                    FROM ohlcv
                    WHERE symbol = :s AND timeframe = :tf
                    """
                ),
                {"s": symbol.upper(), "tf": timeframe},
            )
        ).fetchone()
    if not row:
        return empty
    n = int(row[2] or 0)
    d = int(row[3] or 0)
    return {
        "min_time": row[0],
        "max_time": row[1],
        "row_count": n,
        "distinct_count": d,
        "duplicate_count": max(0, n - d),
        "invalid_ohlc_count": int(row[4] or 0),
    }


async def detect_ohlcv_conflicts(
    rows: Sequence[Mapping[str, Any]],
    *,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Detect same (symbol,tf,time) with differing OHLCV. Never overwrite."""
    if not rows or db_manager.engine is None:
        return []
    # Group by series for fewer round-trips
    by_key: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for r in rows:
        by_key.setdefault((str(r["symbol"]).upper(), str(r["timeframe"])), []).append(r)

    conflicts: list[dict[str, Any]] = []
    async with db_manager.engine.begin() as conn:
        for (sym, tf), group in by_key.items():
            times = [r["time"] for r in group if r.get("time") is not None]
            if not times:
                continue
            existing = (
                await conn.execute(
                    text(
                        """
                        SELECT time, open, high, low, close, volume
                        FROM ohlcv
                        WHERE symbol = :s AND timeframe = :tf
                          AND time = ANY(:times)
                        """
                    ),
                    {"s": sym, "tf": tf, "times": times},
                )
            ).mappings().fetchall()
            existing_map = {row["time"]: row for row in existing}
            for incoming in group:
                t = incoming.get("time")
                ex = existing_map.get(t)
                if ex is None:
                    continue
                differs = any(
                    abs(float(ex[k]) - float(incoming[k])) > 1e-12
                    for k in ("open", "high", "low", "close", "volume")
                )
                if not differs:
                    continue
                conflict = {
                    "kind": "DATA_CONFLICT",
                    "run_id": run_id,
                    "symbol": sym,
                    "timeframe": tf,
                    "time": t,
                    "existing": {
                        "open": float(ex["open"]),
                        "high": float(ex["high"]),
                        "low": float(ex["low"]),
                        "close": float(ex["close"]),
                        "volume": float(ex["volume"]),
                    },
                    "incoming": {
                        "open": float(incoming["open"]),
                        "high": float(incoming["high"]),
                        "low": float(incoming["low"]),
                        "close": float(incoming["close"]),
                        "volume": float(incoming["volume"]),
                    },
                    "source": incoming.get("source"),
                }
                conflicts.append(conflict)
                await conn.execute(
                    text(
                        """
                        INSERT INTO research_data_conflicts (
                            run_id, symbol, timeframe, candle_time,
                            existing_open, existing_high, existing_low,
                            existing_close, existing_volume,
                            incoming_open, incoming_high, incoming_low,
                            incoming_close, incoming_volume, source
                        ) VALUES (
                            :run_id, :symbol, :timeframe, :candle_time,
                            :eo, :eh, :el, :ec, :ev,
                            :io, :ih, :il, :ic, :iv, :source
                        )
                        """
                    ),
                    {
                        "run_id": run_id,
                        "symbol": sym,
                        "timeframe": tf,
                        "candle_time": t,
                        "eo": conflict["existing"]["open"],
                        "eh": conflict["existing"]["high"],
                        "el": conflict["existing"]["low"],
                        "ec": conflict["existing"]["close"],
                        "ev": conflict["existing"]["volume"],
                        "io": conflict["incoming"]["open"],
                        "ih": conflict["incoming"]["high"],
                        "il": conflict["incoming"]["low"],
                        "ic": conflict["incoming"]["close"],
                        "iv": conflict["incoming"]["volume"],
                        "source": conflict.get("source"),
                    },
                )
                logger.warning(
                    "DATA_CONFLICT",
                    symbol=sym,
                    timeframe=tf,
                    time=str(t),
                    run_id=run_id,
                )
    return conflicts


async def claim_sync_task(
    *,
    run_id: str,
    worker_id: str,
) -> dict[str, Any] | None:
    """Atomically claim one PENDING task (SKIP LOCKED)."""
    if db_manager.engine is None:
        return None
    async with db_manager.engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    UPDATE research_sync_tasks t
                    SET status = 'CLAIMED',
                        claimed_by = :worker,
                        run_id = :run_id,
                        attempt = t.attempt + 1,
                        updated_at = NOW(),
                        last_heartbeat = NOW()
                    WHERE t.task_id = (
                        SELECT task_id FROM research_sync_tasks
                        WHERE status = 'PENDING'
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING *
                    """
                ),
                {"worker": worker_id, "run_id": run_id},
            )
        ).mappings().fetchone()
    return dict(row) if row else None


async def upsert_manifest(cp: SeriesCheckpoint) -> None:
    if db_manager.engine is None:
        raise RuntimeError("DATABASE unavailable")
    sql = text(
        """
        INSERT INTO research_data_manifest (
            symbol, timeframe, requested_start, requested_end,
            actual_first, actual_last, candles_downloaded, chunks_completed,
            last_successful_chunk, last_successful_chunk_end_ms,
            status, last_error, quality, gap_count, duplicate_count, invalid_rows, updated_at
        ) VALUES (
            :symbol, :timeframe, :requested_start, :requested_end,
            :actual_first, :actual_last, :candles_downloaded, :chunks_completed,
            :last_successful_chunk, :last_successful_chunk_end_ms,
            :status, :last_error, :quality, :gap_count, :duplicate_count, :invalid_rows, NOW()
        )
        ON CONFLICT (symbol, timeframe) DO UPDATE SET
            requested_start = EXCLUDED.requested_start,
            requested_end = EXCLUDED.requested_end,
            actual_first = EXCLUDED.actual_first,
            actual_last = EXCLUDED.actual_last,
            candles_downloaded = EXCLUDED.candles_downloaded,
            chunks_completed = EXCLUDED.chunks_completed,
            last_successful_chunk = EXCLUDED.last_successful_chunk,
            last_successful_chunk_end_ms = EXCLUDED.last_successful_chunk_end_ms,
            status = EXCLUDED.status,
            last_error = EXCLUDED.last_error,
            quality = EXCLUDED.quality,
            gap_count = EXCLUDED.gap_count,
            duplicate_count = EXCLUDED.duplicate_count,
            invalid_rows = EXCLUDED.invalid_rows,
            updated_at = NOW()
        """
    )
    d = cp.to_dict()
    async with db_manager.engine.begin() as conn:
        await conn.execute(sql, d)


async def load_manifest(symbol: str, timeframe: str) -> SeriesCheckpoint | None:
    if db_manager.engine is None:
        return None
    async with db_manager.engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT * FROM research_data_manifest
                    WHERE symbol = :s AND timeframe = :tf
                    """
                ),
                {"s": symbol.upper(), "tf": timeframe},
            )
        ).mappings().fetchone()
    if not row:
        return None
    return SeriesCheckpoint.from_row(dict(row))


async def load_all_manifests(
    symbols: Sequence[str] | None = None,
    timeframes: Sequence[str] | None = None,
) -> list[SeriesCheckpoint]:
    if db_manager.engine is None:
        return []
    clauses = ["1=1"]
    params: dict[str, Any] = {}
    if symbols:
        clauses.append("symbol = ANY(:syms)")
        params["syms"] = [s.upper() for s in symbols]
    if timeframes:
        clauses.append("timeframe = ANY(:tfs)")
        params["tfs"] = list(timeframes)
    sql = text(
        f"SELECT * FROM research_data_manifest WHERE {' AND '.join(clauses)} ORDER BY symbol, timeframe"
    )
    async with db_manager.engine.begin() as conn:
        rows = (await conn.execute(sql, params)).mappings().fetchall()
    return [SeriesCheckpoint.from_row(dict(r)) for r in rows]


async def save_gap_reports(
    symbol: str,
    timeframe: str,
    gaps: Sequence[Mapping[str, Any]],
    *,
    dataset_version: str | None = None,
) -> None:
    if not gaps or db_manager.engine is None:
        return
    sql = text(
        """
        INSERT INTO research_gap_reports (
            symbol, timeframe, kind, start_time, end_time,
            duration_ms, expected_candles, missing_candles, dataset_version
        ) VALUES (
            :symbol, :timeframe, :kind, :start_time, :end_time,
            :duration_ms, :expected_candles, :missing_candles, :dataset_version
        )
        """
    )
    async with db_manager.engine.begin() as conn:
        for g in gaps:
            await conn.execute(
                sql,
                {
                    "symbol": symbol.upper(),
                    "timeframe": timeframe,
                    "kind": g.get("kind") or "GAP_DETECTED",
                    "start_time": g["start"],
                    "end_time": g["end"],
                    "duration_ms": g.get("duration_ms"),
                    "expected_candles": g.get("expected_candles"),
                    "missing_candles": g.get("missing_candles"),
                    "dataset_version": dataset_version,
                },
            )


async def insert_pipeline_run(payload: Mapping[str, Any]) -> None:
    if db_manager.engine is None:
        raise RuntimeError("DATABASE unavailable")
    sql = text(
        """
        INSERT INTO research_pipeline_runs (
            run_id, git_commit, pipeline_version, feature_version, data_version,
            configuration_hash, configuration, symbols, timeframes,
            period_start, period_end, status, metrics, quality_report, failed_symbols
        ) VALUES (
            :run_id, :git_commit, :pipeline_version, :feature_version, :data_version,
            :configuration_hash, CAST(:configuration AS JSONB), CAST(:symbols AS JSONB),
            CAST(:timeframes AS JSONB), :period_start, :period_end, :status,
            CAST(:metrics AS JSONB), CAST(:quality_report AS JSONB),
            CAST(:failed_symbols AS JSONB)
        )
        ON CONFLICT (run_id) DO UPDATE SET
            status = EXCLUDED.status,
            metrics = EXCLUDED.metrics,
            quality_report = EXCLUDED.quality_report,
            failed_symbols = EXCLUDED.failed_symbols,
            data_version = EXCLUDED.data_version,
            finished_at = CASE WHEN EXCLUDED.status IN ('DONE', 'ERROR', 'PARTIAL')
                THEN NOW() ELSE research_pipeline_runs.finished_at END
        """
    )
    import json

    async with db_manager.engine.begin() as conn:
        await conn.execute(
            sql,
            {
                "run_id": payload["run_id"],
                "git_commit": payload.get("git_commit"),
                "pipeline_version": payload["pipeline_version"],
                "feature_version": payload["feature_version"],
                "data_version": payload.get("data_version"),
                "configuration_hash": payload["configuration_hash"],
                "configuration": json.dumps(payload.get("configuration") or {}),
                "symbols": json.dumps(payload.get("symbols") or []),
                "timeframes": json.dumps(payload.get("timeframes") or []),
                "period_start": payload.get("period_start"),
                "period_end": payload.get("period_end"),
                "status": payload.get("status") or "RUNNING",
                "metrics": json.dumps(payload.get("metrics") or {}),
                "quality_report": json.dumps(payload.get("quality_report") or {}),
                "failed_symbols": json.dumps(payload.get("failed_symbols") or []),
            },
        )


async def save_dataset_version(payload: Mapping[str, Any]) -> None:
    if db_manager.engine is None:
        raise RuntimeError("DATABASE unavailable")
    import json

    sql = text(
        """
        INSERT INTO research_dataset_versions (
            dataset_version, pipeline_version, feature_version, data_fingerprint,
            git_commit, symbols, timeframes, period_start, period_end,
            candle_counts, quality_report, payload
        ) VALUES (
            :dataset_version, :pipeline_version, :feature_version, :data_fingerprint,
            :git_commit, CAST(:symbols AS JSONB), CAST(:timeframes AS JSONB),
            :period_start, :period_end, CAST(:candle_counts AS JSONB),
            CAST(:quality_report AS JSONB), CAST(:payload AS JSONB)
        )
        ON CONFLICT (dataset_version) DO NOTHING
        """
    )
    async with db_manager.engine.begin() as conn:
        await conn.execute(
            sql,
            {
                "dataset_version": payload["dataset_version"],
                "pipeline_version": payload["pipeline_version"],
                "feature_version": payload.get("feature_version"),
                "data_fingerprint": payload.get("data_fingerprint"),
                "git_commit": payload.get("git_commit"),
                "symbols": json.dumps(payload.get("symbols") or []),
                "timeframes": json.dumps(payload.get("timeframes") or []),
                "period_start": payload.get("period_start"),
                "period_end": payload.get("period_end"),
                "candle_counts": json.dumps(payload.get("candle_counts") or {}),
                "quality_report": json.dumps(payload.get("quality_report") or {}),
                "payload": json.dumps(payload.get("payload") or {}),
            },
        )


async def upsert_feature_cache(row: Mapping[str, Any]) -> None:
    if db_manager.engine is None:
        return
    import json

    sql = text(
        """
        INSERT INTO research_feature_cache (
            symbol, timeframe, feature_version, data_fingerprint,
            first_time, last_time, bar_count, status, payload, updated_at
        ) VALUES (
            :symbol, :timeframe, :feature_version, :data_fingerprint,
            :first_time, :last_time, :bar_count, :status, CAST(:payload AS JSONB), NOW()
        )
        ON CONFLICT (symbol, timeframe, feature_version, data_fingerprint) DO UPDATE SET
            first_time = EXCLUDED.first_time,
            last_time = EXCLUDED.last_time,
            bar_count = EXCLUDED.bar_count,
            status = EXCLUDED.status,
            payload = EXCLUDED.payload,
            updated_at = NOW()
        """
    )
    async with db_manager.engine.begin() as conn:
        await conn.execute(
            sql,
            {
                "symbol": row["symbol"],
                "timeframe": row["timeframe"],
                "feature_version": row["feature_version"],
                "data_fingerprint": row["data_fingerprint"],
                "first_time": row.get("first_time"),
                "last_time": row.get("last_time"),
                "bar_count": int(row.get("bar_count") or 0),
                "status": row.get("status") or "COMPLETE",
                "payload": json.dumps(row.get("payload") or {}),
            },
        )


async def feature_cache_hit(
    symbol: str,
    timeframe: str,
    feature_version: str,
    data_fingerprint: str,
) -> bool:
    if db_manager.engine is None:
        return False
    async with db_manager.engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT 1 FROM research_feature_cache
                    WHERE symbol = :s AND timeframe = :tf
                      AND feature_version = :fv AND data_fingerprint = :fp
                      AND status = 'COMPLETE'
                    """
                ),
                {
                    "s": symbol.upper(),
                    "tf": timeframe,
                    "fv": feature_version,
                    "fp": data_fingerprint,
                },
            )
        ).fetchone()
    return row is not None


async def insert_bos_events(
    events: Sequence[Mapping[str, Any]],
    *,
    batch_size: int = 500,
) -> int:
    if not events or db_manager.engine is None:
        return 0
    import json

    sql = text(
        """
        INSERT INTO research_bos_events (
            dataset_version, feature_version, symbol, timestamp, timeframe,
            direction, swing_price, bos_price, bos_distance, atr, trend,
            htf_alignment, mtf_state, impulse, pullback, retest, sd_state,
            rvol, bar_index, payload
        ) VALUES (
            :dataset_version, :feature_version, :symbol, :timestamp, :timeframe,
            :direction, :swing_price, :bos_price, :bos_distance, :atr, :trend,
            :htf_alignment, :mtf_state, :impulse, :pullback, :retest, :sd_state,
            :rvol, :bar_index, CAST(:payload AS JSONB)
        )
        ON CONFLICT (dataset_version, feature_version, symbol, timeframe, timestamp, direction)
        DO NOTHING
        """
    )
    n = 0
    async with db_manager.engine.begin() as conn:
        for i in range(0, len(events), batch_size):
            for ev in events[i : i + batch_size]:
                result = await conn.execute(
                    sql,
                    {
                        "dataset_version": ev["dataset_version"],
                        "feature_version": ev["feature_version"],
                        "symbol": ev["symbol"],
                        "timestamp": ev["timestamp"],
                        "timeframe": ev["timeframe"],
                        "direction": ev["direction"],
                        "swing_price": ev.get("swing_price"),
                        "bos_price": ev.get("bos_price"),
                        "bos_distance": ev.get("bos_distance"),
                        "atr": ev.get("atr"),
                        "trend": ev.get("trend"),
                        "htf_alignment": ev.get("htf_alignment"),
                        "mtf_state": ev.get("mtf_state"),
                        "impulse": ev.get("impulse"),
                        "pullback": ev.get("pullback"),
                        "retest": ev.get("retest"),
                        "sd_state": ev.get("sd_state"),
                        "rvol": ev.get("rvol"),
                        "bar_index": ev.get("bar_index"),
                        "payload": json.dumps(ev.get("payload") or {}),
                    },
                )
                n += int(result.rowcount or 0)
    return n


async def count_bos_events(dataset_version: str, feature_version: str | None = None) -> int:
    if db_manager.engine is None:
        return 0
    if feature_version:
        sql = text(
            """
            SELECT COUNT(*) FROM research_bos_events
            WHERE dataset_version = :dv AND feature_version = :fv
            """
        )
        params = {"dv": dataset_version, "fv": feature_version}
    else:
        sql = text("SELECT COUNT(*) FROM research_bos_events WHERE dataset_version = :dv")
        params = {"dv": dataset_version}
    async with db_manager.engine.begin() as conn:
        row = (await conn.execute(sql, params)).fetchone()
    return int(row[0] or 0) if row else 0
