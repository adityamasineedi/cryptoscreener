from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.core.logging import get_logger

logger = get_logger("database")

# Schema DDL must not hang forever behind abandoned idle-in-transaction locks.
_SCHEMA_LOCK_TIMEOUT = "15s"
_SCHEMA_STATEMENT_TIMEOUT = "60s"
# Plain-Postgres DDL (no Timescale hypertable calls) — used when extension missing.
_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS symbols (
        symbol          TEXT PRIMARY KEY,
        base_asset      TEXT NOT NULL,
        quote_asset     TEXT NOT NULL,
        market_type     TEXT NOT NULL,
        exchange        TEXT NOT NULL DEFAULT 'binance',
        status          TEXT NOT NULL DEFAULT 'TRADING',
        contract_type   TEXT,
        price_precision INT,
        qty_precision   INT,
        listed_at       TIMESTAMPTZ,
        delisted_at     TIMESTAMPTZ,
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_metadata (
        asset               TEXT PRIMARY KEY,
        chain               TEXT,
        contract_address    TEXT,
        decimals            INT,
        provider_id         TEXT,
        symbols             JSONB NOT NULL DEFAULT '[]',
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ohlcv (
        time            TIMESTAMPTZ NOT NULL,
        symbol          TEXT NOT NULL,
        timeframe       TEXT NOT NULL,
        open            DOUBLE PRECISION NOT NULL,
        high            DOUBLE PRECISION NOT NULL,
        low             DOUBLE PRECISION NOT NULL,
        close           DOUBLE PRECISION NOT NULL,
        volume          DOUBLE PRECISION NOT NULL,
        quote_volume    DOUBLE PRECISION,
        trade_count     INT,
        taker_buy_base  DOUBLE PRECISION,
        taker_buy_quote DOUBLE PRECISION,
        source          TEXT NOT NULL DEFAULT 'binance',
        PRIMARY KEY (time, symbol, timeframe)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS funding_rates (
        time            TIMESTAMPTZ NOT NULL,
        symbol          TEXT NOT NULL,
        funding_rate    DOUBLE PRECISION NOT NULL,
        mark_price      DOUBLE PRECISION,
        source          TEXT NOT NULL DEFAULT 'binance',
        PRIMARY KEY (time, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS open_interest (
        time            TIMESTAMPTZ NOT NULL,
        symbol          TEXT NOT NULL,
        open_interest   DOUBLE PRECISION NOT NULL,
        source          TEXT NOT NULL DEFAULT 'binance',
        PRIMARY KEY (time, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS liquidations (
        time            TIMESTAMPTZ NOT NULL,
        symbol          TEXT NOT NULL,
        side            TEXT NOT NULL,
        price           DOUBLE PRECISION NOT NULL,
        quantity        DOUBLE PRECISION NOT NULL,
        quote_qty       DOUBLE PRECISION,
        source          TEXT NOT NULL DEFAULT 'binance',
        PRIMARY KEY (time, symbol, side, price, quantity)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS volume_history (
        time            TIMESTAMPTZ NOT NULL,
        symbol          TEXT NOT NULL,
        timeframe       TEXT NOT NULL,
        volume          DOUBLE PRECISION NOT NULL,
        quote_volume    DOUBLE PRECISION,
        source          TEXT NOT NULL DEFAULT 'binance',
        PRIMARY KEY (time, symbol, timeframe)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS price_snapshots (
        time            TIMESTAMPTZ NOT NULL,
        symbol          TEXT NOT NULL,
        price           DOUBLE PRECISION NOT NULL,
        source          TEXT NOT NULL DEFAULT 'binance',
        PRIMARY KEY (time, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signals (
        id              BIGSERIAL PRIMARY KEY,
        time            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        symbol          TEXT NOT NULL,
        timeframe       TEXT NOT NULL,
        state           TEXT NOT NULL,
        reasons         JSONB NOT NULL DEFAULT '[]',
        scores          JSONB NOT NULL DEFAULT '{}',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS structure_events (
        time            TIMESTAMPTZ NOT NULL,
        symbol          TEXT NOT NULL,
        timeframe       TEXT NOT NULL,
        event_type      TEXT NOT NULL,
        price           DOUBLE PRECISION,
        strength        DOUBLE PRECISION,
        evidence        JSONB NOT NULL DEFAULT '{}',
        source          TEXT NOT NULL DEFAULT 'market_structure',
        PRIMARY KEY (time, symbol, timeframe, event_type, price)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS supply_demand_zones (
        id              BIGSERIAL PRIMARY KEY,
        symbol          TEXT NOT NULL,
        timeframe       TEXT NOT NULL,
        zone_type       TEXT NOT NULL,
        high            DOUBLE PRECISION NOT NULL,
        low             DOUBLE PRECISION NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL,
        strength        DOUBLE PRECISION,
        freshness       DOUBLE PRECISION,
        touch_count     INT NOT NULL DEFAULT 0,
        reaction_strength DOUBLE PRECISION,
        status          TEXT NOT NULL,
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS setup_analyses (
        id              BIGSERIAL PRIMARY KEY,
        time            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        symbol          TEXT NOT NULL,
        timeframe       TEXT NOT NULL,
        status          TEXT NOT NULL,
        direction       TEXT,
        payload         JSONB NOT NULL DEFAULT '{}',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS setup_events (
        time            TIMESTAMPTZ NOT NULL,
        symbol          TEXT NOT NULL,
        timeframe       TEXT NOT NULL,
        event_type      TEXT NOT NULL,
        price           DOUBLE PRECISION,
        state           TEXT,
        evidence        JSONB NOT NULL DEFAULT '{}',
        source          TEXT NOT NULL DEFAULT 'setup_signal_engine',
        PRIMARY KEY (time, symbol, timeframe, event_type, price)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_trades (
        id              TEXT PRIMARY KEY,
        symbol          TEXT NOT NULL,
        side            TEXT NOT NULL DEFAULT 'LONG',
        status          TEXT NOT NULL,
        entry_price     DOUBLE PRECISION NOT NULL,
        stop_price      DOUBLE PRECISION NOT NULL,
        tp1_price       DOUBLE PRECISION,
        quantity        DOUBLE PRECISION NOT NULL,
        risk_usd        DOUBLE PRECISION,
        opened_at       TIMESTAMPTZ NOT NULL,
        closed_at       TIMESTAMPTZ,
        exit_price      DOUBLE PRECISION,
        exit_reason     TEXT,
        pnl_usd         DOUBLE PRECISION,
        r_multiple      DOUBLE PRECISION,
        source_candle_ts TEXT,
        timeframe       TEXT,
        signal_snippet  JSONB NOT NULL DEFAULT '{}',
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_signals_symbol_time ON signals (symbol, time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_setup_analyses_sym ON setup_analyses (symbol, time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_setup_events_sym ON setup_events (symbol, timeframe, time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_paper_trades_sym ON paper_trades (symbol, opened_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades (status, opened_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_symbols_market ON symbols (market_type, status)",
    "CREATE INDEX IF NOT EXISTS idx_sd_zones_symbol ON supply_demand_zones (symbol, timeframe, status)",
    "CREATE INDEX IF NOT EXISTS idx_volume_history_sym ON volume_history (symbol, timeframe, time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_price_snapshots_sym ON price_snapshots (symbol, time DESC)",
    # Research persistence — isolated from live setup_analyses / signals
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
    # Research historical data pipeline (additive; never truncates production OHLCV)
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
    "CREATE INDEX IF NOT EXISTS idx_research_bos_events_lookup ON research_bos_events (symbol, timeframe, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_research_bos_events_dataset ON research_bos_events (dataset_version, feature_version, symbol)",
    "CREATE INDEX IF NOT EXISTS idx_research_gap_reports_sym ON research_gap_reports (symbol, timeframe, start_time)",
    "CREATE INDEX IF NOT EXISTS idx_research_manifest_status ON research_data_manifest (status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_tf_time ON ohlcv (symbol, timeframe, time)",
    """
    CREATE TABLE IF NOT EXISTS sentiment_snapshots (
        id                      BIGSERIAL PRIMARY KEY,
        symbol                  TEXT NOT NULL,
        provider                TEXT NOT NULL,
        provider_symbol         TEXT,
        observed_at             TIMESTAMPTZ NOT NULL,
        provider_generated_at   TIMESTAMPTZ,
        social_dominance        DOUBLE PRECISION,
        social_volume           DOUBLE PRECISION,
        mentions                DOUBLE PRECISION,
        engagement              DOUBLE PRECISION,
        sentiment               DOUBLE PRECISION,
        sentiment_change        DOUBLE PRECISION,
        raw_payload_hash        TEXT,
        created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sentiment_symbol_obs ON sentiment_snapshots (symbol, observed_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sentiment_provider_obs ON sentiment_snapshots (provider, observed_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS alerts (
        id              TEXT PRIMARY KEY,
        seq             BIGINT NOT NULL,
        time            TIMESTAMPTZ NOT NULL,
        type            TEXT NOT NULL,
        symbol          TEXT NOT NULL,
        timeframe       TEXT,
        severity        TEXT NOT NULL,
        title           TEXT NOT NULL,
        detail          TEXT NOT NULL DEFAULT '',
        payload         JSONB NOT NULL DEFAULT '{}',
        dedupe_key      TEXT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_alerts_seq ON alerts (seq DESC)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_symbol_time ON alerts (symbol, time DESC)",
    # System diagnostics / Issue Center (additive; no market data mutation)
    """
    CREATE TABLE IF NOT EXISTS diagnostic_issues (
        id                  TEXT PRIMARY KEY,
        diagnostic_id       TEXT NOT NULL,
        fingerprint         TEXT NOT NULL UNIQUE,
        severity            TEXT NOT NULL,
        status              TEXT NOT NULL,
        category            TEXT NOT NULL DEFAULT 'APPLICATION',
        service             TEXT,
        subsystem           TEXT,
        component           TEXT,
        module              TEXT,
        file                TEXT,
        function            TEXT,
        line                INT,
        error_code          TEXT,
        message             TEXT NOT NULL DEFAULT '',
        exception_type      TEXT,
        stack_trace         TEXT,
        symbol              TEXT,
        timeframe           TEXT,
        provider            TEXT,
        endpoint            TEXT,
        stream              TEXT,
        request_id          TEXT,
        job_id              TEXT,
        run_id              TEXT,
        expected            TEXT,
        actual              TEXT,
        first_seen          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_seen           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        occurrence_count    INT NOT NULL DEFAULT 1,
        resolved_at         TIMESTAMPTZ,
        resolution_message  TEXT,
        owner               TEXT,
        details             JSONB NOT NULL DEFAULT '{}',
        metadata            JSONB NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS diagnostic_events (
        id                  TEXT PRIMARY KEY,
        diagnostic_id       TEXT NOT NULL,
        timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        severity            TEXT NOT NULL,
        status              TEXT NOT NULL DEFAULT 'OPEN',
        service             TEXT,
        subsystem           TEXT,
        component           TEXT,
        module              TEXT,
        file                TEXT,
        function            TEXT,
        line                INT,
        event_type          TEXT,
        error_code          TEXT,
        category            TEXT NOT NULL DEFAULT 'APPLICATION',
        message             TEXT NOT NULL DEFAULT '',
        details             JSONB NOT NULL DEFAULT '{}',
        exception_type      TEXT,
        stack_trace         TEXT,
        symbol              TEXT,
        timeframe           TEXT,
        provider            TEXT,
        endpoint            TEXT,
        stream              TEXT,
        request_id          TEXT,
        job_id              TEXT,
        run_id              TEXT,
        expected            TEXT,
        actual              TEXT,
        fingerprint         TEXT,
        metadata            JSONB NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS diagnostic_snapshots (
        id                  TEXT PRIMARY KEY,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        label               TEXT,
        git_commit          TEXT,
        payload             JSONB NOT NULL DEFAULT '{}',
        immutable           BOOLEAN NOT NULL DEFAULT TRUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS diagnostic_service_checks (
        id                  BIGSERIAL PRIMARY KEY,
        checked_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        service_key         TEXT NOT NULL,
        status              TEXT NOT NULL,
        reason              TEXT,
        latency_ms          DOUBLE PRECISION,
        metrics             JSONB NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS diagnostic_resource_snapshots (
        id                  BIGSERIAL PRIMARY KEY,
        sampled_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        disk                JSONB NOT NULL DEFAULT '{}',
        memory              JSONB NOT NULL DEFAULT '{}',
        cpu                 JSONB NOT NULL DEFAULT '{}',
        process             JSONB NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS diagnostic_backup_runs (
        id                  TEXT PRIMARY KEY,
        started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at         TIMESTAMPTZ,
        backup_type         TEXT NOT NULL,
        status              TEXT NOT NULL,
        location            TEXT,
        size_bytes          BIGINT,
        checksum            TEXT,
        verified            BOOLEAN NOT NULL DEFAULT FALSE,
        verification_status TEXT,
        error               TEXT,
        metadata            JSONB NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_diag_issues_status ON diagnostic_issues (status, last_seen DESC)",
    "CREATE INDEX IF NOT EXISTS idx_diag_issues_severity ON diagnostic_issues (severity, last_seen DESC)",
    "CREATE INDEX IF NOT EXISTS idx_diag_issues_component ON diagnostic_issues (component, last_seen DESC)",
    "CREATE INDEX IF NOT EXISTS idx_diag_issues_diagnostic_id ON diagnostic_issues (diagnostic_id)",
    "CREATE INDEX IF NOT EXISTS idx_diag_issues_fingerprint ON diagnostic_issues (fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_diag_issues_job ON diagnostic_issues (job_id)",
    "CREATE INDEX IF NOT EXISTS idx_diag_events_ts ON diagnostic_events (timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_diag_events_fingerprint ON diagnostic_events (fingerprint, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_diag_events_diagnostic_id ON diagnostic_events (diagnostic_id)",
    "CREATE INDEX IF NOT EXISTS idx_diag_events_severity ON diagnostic_events (severity, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_diag_snapshots_created ON diagnostic_snapshots (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_diag_service_checks_key ON diagnostic_service_checks (service_key, checked_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_diag_resource_sampled ON diagnostic_resource_snapshots (sampled_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_diag_backup_started ON diagnostic_backup_runs (started_at DESC)",
    # Dynamic COMBO_02 v2 research candidate pipeline (never joins v1 universe)
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
        production_approved         BOOLEAN NOT NULL DEFAULT FALSE,
        created_at_utc              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at_utc              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (symbol, strategy_id)
    )
    """,
    """
    ALTER TABLE strategy_candidate_registry
        ADD COLUMN IF NOT EXISTS production_approved BOOLEAN NOT NULL DEFAULT FALSE
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
_HYPERTABLES = [
    "ohlcv",
    "funding_rates",
    "open_interest",
    "liquidations",
    "volume_history",
    "price_snapshots",
    "structure_events",
    "setup_events",
]

class DatabaseManager:
    def __init__(self) -> None:
        self.engine: AsyncEngine | None = None
        self.session_factory: async_sessionmaker[AsyncSession] | None = None
        self.enabled = False
        self.status = "disabled"
        self.timescale = False
        self.schema_ready = False
        self.startup_stages: list[dict[str, Any]] = []
        self.last_schema_error: str | None = None

    def _stage_begin(self, name: str) -> float:
        logger.info("startup_stage", startup_stage=name, status="STARTED")
        return time.perf_counter()

    def _stage_end(
        self,
        name: str,
        started: float,
        *,
        status: str = "OK",
        error: str | None = None,
        **extra: Any,
    ) -> None:
        row = {
            "startup_stage": name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "status": status,
            "error": error,
            **extra,
        }
        self.startup_stages.append(row)
        logger.info("startup_stage", **row)

    async def _log_blocking_activity(self) -> list[dict[str, Any]]:
        """Best-effort lock forensics when DDL times out (read-only)."""
        if self.engine is None:
            return []
        try:
            async with self.engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT blocked.pid AS blocked_pid,
                               blocking.pid AS blocking_pid,
                               blocked.wait_event_type,
                               blocked.wait_event,
                               EXTRACT(EPOCH FROM (now() - blocked.query_start))
                                   AS blocked_duration_s,
                               left(blocked.query, 180) AS blocked_query,
                               left(blocking.query, 180) AS blocking_query,
                               blocking.state AS blocking_state
                        FROM pg_stat_activity blocked
                        JOIN pg_locks bl ON bl.pid = blocked.pid AND NOT bl.granted
                        JOIN pg_locks kl ON kl.locktype = bl.locktype
                          AND kl.DATABASE IS NOT DISTINCT FROM bl.DATABASE
                          AND kl.relation IS NOT DISTINCT FROM bl.relation
                          AND kl.page IS NOT DISTINCT FROM bl.page
                          AND kl.tuple IS NOT DISTINCT FROM bl.tuple
                          AND kl.virtualxid IS NOT DISTINCT FROM bl.virtualxid
                          AND kl.transactionid IS NOT DISTINCT FROM bl.transactionid
                          AND kl.classid IS NOT DISTINCT FROM bl.classid
                          AND kl.objid IS NOT DISTINCT FROM bl.objid
                          AND kl.objsubid IS NOT DISTINCT FROM bl.objsubid
                          AND kl.pid <> bl.pid
                          AND kl.granted
                        JOIN pg_stat_activity blocking ON blocking.pid = kl.pid
                        WHERE blocked.datname = current_database()
                        LIMIT 20
                        """
                    )
                )
                rows = [dict(r) for r in result.mappings().all()]
                if rows:
                    logger.warning(
                        "schema_ddl_blocked",
                        blockers=len(rows),
                        sample=rows[:5],
                    )
                return rows
        except Exception as exc:  # noqa: BLE001
            logger.warning("schema_blocker_probe_failed", error=str(exc))
            return []

    async def _exec_ddl(self, stmt: str, *, stage: str) -> None:
        """Run one DDL statement in its own transaction with lock timeouts.

        One giant schema transaction previously held locks across all statements
        and hung indefinitely on CREATE INDEX behind idle-in-transaction writers.
        """
        assert self.engine is not None
        preview = " ".join(stmt.split())[:120]
        t0 = self._stage_begin(stage)
        try:
            async with self.engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL lock_timeout = '{_SCHEMA_LOCK_TIMEOUT}'"))
                await conn.execute(
                    text(f"SET LOCAL statement_timeout = '{_SCHEMA_STATEMENT_TIMEOUT}'")
                )
                await conn.execute(text(stmt))
            self._stage_end(stage, t0, status="OK", preview=preview)
        except Exception as exc:  # noqa: BLE001
            blockers = await self._log_blocking_activity()
            self._stage_end(
                stage,
                t0,
                status="ERROR",
                error=f"{type(exc).__name__}: {exc}",
                preview=preview,
                blockers=len(blockers),
            )
            raise

    async def connect(self, settings: Settings, *, ensure_schema: bool = True) -> None:
        if not settings.database_enabled:
            self.status = "disabled"
            self.enabled = False
            logger.info("database_disabled")
            return
        self.startup_stages = []
        self.last_schema_error = None
        t0 = self._stage_begin("DB_CONNECT")
        try:
            self.engine = create_async_engine(
                settings.database_url,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=10,
            )
            async with self.engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            self.session_factory = async_sessionmaker(
                self.engine, expire_on_commit=False
            )
            self.enabled = True
            self._stage_end("DB_CONNECT", t0, status="OK")
            if ensure_schema:
                await self.ensure_schema()
                self.status = (
                    "ok"
                    if self.schema_ready
                    else f"schema_incomplete:{self.last_schema_error or 'unknown'}"
                )
            else:
                # Research CLIs: skip DDL to avoid lock timeouts against a live API.
                self.schema_ready = True
                self.status = "ok"
            logger.info(
                "database_connected",
                timescale=self.timescale,
                schema_ready=self.schema_ready,
                status=self.status,
                ensure_schema=ensure_schema,
            )
        except Exception as exc:  # noqa: BLE001
            self._stage_end(
                "DB_CONNECT",
                t0,
                status="ERROR",
                error=f"{type(exc).__name__}: {exc}",
            )
            self.engine = None
            self.session_factory = None
            self.enabled = False
            self.status = f"unavailable:{exc.__class__.__name__}"
            logger.warning("database_unavailable", error=str(exc))

    async def ensure_schema(self) -> None:
        """Create tables when DATABASE_ENABLED. Timescale hypertables optional."""
        if not self.enabled or self.engine is None:
            return
        try:
            # Probe Timescale in its own transaction — a failed CREATE EXTENSION
            # must not abort table DDL (Postgres aborts the whole txn on error).
            self.timescale = False
            t_ts = self._stage_begin("TIMESCALE_CHECK")
            try:
                async with self.engine.begin() as conn:
                    await conn.execute(
                        text(f"SET LOCAL lock_timeout = '{_SCHEMA_LOCK_TIMEOUT}'")
                    )
                    await conn.execute(
                        text(
                            f"SET LOCAL statement_timeout = '{_SCHEMA_STATEMENT_TIMEOUT}'"
                        )
                    )
                    await conn.execute(
                        text("CREATE EXTENSION IF NOT EXISTS timescaledb")
                    )
                self.timescale = True
                self._stage_end("TIMESCALE_CHECK", t_ts, status="OK", timescale=True)
            except Exception as exc:  # noqa: BLE001
                self.timescale = False
                self._stage_end(
                    "TIMESCALE_CHECK",
                    t_ts,
                    status="FALLBACK",
                    timescale=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
                logger.info("timescaledb_extension_unavailable_using_plain_postgres")

            # Per-statement transactions + lock timeouts (see _exec_ddl).
            t_schema = self._stage_begin("SCHEMA_INIT")
            failed: list[str] = []
            for i, stmt in enumerate(_SCHEMA_STATEMENTS):
                kind = "INDEX_CREATION" if "CREATE INDEX" in stmt.upper() else (
                    "TABLE_CREATION" if "CREATE TABLE" in stmt.upper() else "SCHEMA_DDL"
                )
                if "diagnostic_" in stmt:
                    stage = f"DIAGNOSTIC_SCHEMA_{i}"
                elif "research_" in stmt:
                    stage = f"RESEARCH_SCHEMA_{i}"
                else:
                    stage = f"{kind}_{i}"
                try:
                    await self._exec_ddl(stmt, stage=stage)
                except Exception as exc:  # noqa: BLE001
                    # CREATE INDEX IF NOT EXISTS can block forever behind idle
                    # writers; after lock_timeout, continue other DDL and surface error.
                    msg = f"{stage}:{type(exc).__name__}:{exc}"
                    failed.append(msg)
                    logger.warning("schema_statement_failed", stage=stage, error=str(exc))

            self._stage_end(
                "SCHEMA_INIT",
                t_schema,
                status="OK" if not failed else "PARTIAL",
                failed_count=len(failed),
            )

            if self.timescale:
                t_ht = self._stage_begin("HYPERTABLE_SETUP")
                async with self.engine.begin() as conn:
                    await conn.execute(
                        text(f"SET LOCAL lock_timeout = '{_SCHEMA_LOCK_TIMEOUT}'")
                    )
                    for table in _HYPERTABLES:
                        try:
                            await conn.execute(
                                text(
                                    "SELECT create_hypertable(:t, by_range('time'), "
                                    "if_not_exists => TRUE)"
                                ),
                                {"t": table},
                            )
                        except Exception:  # noqa: BLE001
                            try:
                                await conn.execute(
                                    text(
                                        f"SELECT create_hypertable('{table}', 'time', "
                                        "if_not_exists => TRUE)"
                                    )
                                )
                            except Exception:  # noqa: BLE001
                                pass
                    for table, interval in (
                        ("liquidations", "INTERVAL '48 hours'"),
                        ("price_snapshots", "INTERVAL '7 days'"),
                        ("volume_history", "INTERVAL '3 years'"),
                        ("funding_rates", "INTERVAL '90 days'"),
                        ("open_interest", "INTERVAL '90 days'"),
                        ("structure_events", "INTERVAL '365 days'"),
                        ("ohlcv", "INTERVAL '10 years'"),
                    ):
                        try:
                            await conn.execute(
                                text(
                                    f"SELECT add_retention_policy('{table}', "
                                    f"{interval}, if_not_exists => TRUE)"
                                )
                            )
                        except Exception:  # noqa: BLE001
                            pass
                self._stage_end("HYPERTABLE_SETUP", t_ht, status="OK")

            if failed:
                self.schema_ready = False
                self.last_schema_error = "; ".join(failed[:5])
                logger.warning(
                    "schema_ensure_partial",
                    failed=len(failed),
                    error=self.last_schema_error,
                )
            else:
                self.schema_ready = True
                self.last_schema_error = None
                logger.info(
                    "schema_ready",
                    timescale=self.timescale,
                    tables=len(_SCHEMA_STATEMENTS),
                )
        except Exception as exc:  # noqa: BLE001
            self.schema_ready = False
            self.last_schema_error = f"{type(exc).__name__}: {exc}"
            await self._log_blocking_activity()
            logger.warning("schema_ensure_failed", error=str(exc))

    async def retention_policies(self) -> dict[str, Any]:
        """Inspect Timescale retention jobs when available."""
        if not self.enabled or self.engine is None:
            return {"enabled": False, "policies": []}
        if not self.timescale:
            return {
                "enabled": True,
                "timescale": False,
                "policies": [],
                "note": "Using application-level DELETE retention (retention.py)",
            }
        try:
            async with self.engine.begin() as conn:
                result = await conn.execute(
                    text(
                        "SELECT hypertable_name, schedule_interval, config "
                        "FROM timescaledb_information.jobs "
                        "WHERE proc_name = 'policy_retention' "
                        "ORDER BY hypertable_name"
                    )
                )
                rows = [
                    {
                        "hypertable": r[0],
                        "schedule_interval": str(r[1]),
                        "config": r[2],
                    }
                    for r in result.fetchall()
                ]
            return {"enabled": True, "timescale": True, "policies": rows}
        except Exception as exc:  # noqa: BLE001
            return {
                "enabled": True,
                "timescale": True,
                "policies": [],
                "error": str(exc),
                "note": "Application-level DELETE retention still active",
            }
    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None
    async def health(self) -> dict[str, Any]:
        base = {
            "status": self.status,
            "enabled": self.enabled,
            "timescale": self.timescale,
            "schema_ready": self.schema_ready,
            "last_schema_error": self.last_schema_error,
            "startup_stages": list(self.startup_stages[-40:]),
        }
        if not self.enabled or self.engine is None:
            return base
        try:
            async with self.engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            base["status"] = self.status if self.status.startswith("schema_") else "ok"
            return base
        except Exception as exc:  # noqa: BLE001
            base["status"] = f"error:{exc}"
            return base
db_manager = DatabaseManager()
