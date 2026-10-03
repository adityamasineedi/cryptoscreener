from __future__ import annotations
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from app.config import Settings
from app.core.logging import get_logger
logger = get_logger("database")
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
    async def connect(self, settings: Settings) -> None:
        if not settings.database_enabled:
            self.status = "disabled"
            self.enabled = False
            logger.info("database_disabled")
            return
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
            self.status = "ok"
            await self.ensure_schema()
            logger.info(
                "database_connected",
                timescale=self.timescale,
                schema_ready=self.schema_ready,
            )
        except Exception as exc:  # noqa: BLE001
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
            try:
                async with self.engine.begin() as conn:
                    await conn.execute(
                        text("CREATE EXTENSION IF NOT EXISTS timescaledb")
                    )
                self.timescale = True
            except Exception:  # noqa: BLE001
                self.timescale = False
                logger.info("timescaledb_extension_unavailable_using_plain_postgres")

            async with self.engine.begin() as conn:
                for stmt in _SCHEMA_STATEMENTS:
                    await conn.execute(text(stmt))

            if self.timescale:
                async with self.engine.begin() as conn:
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
            self.schema_ready = True
            logger.info(
                "schema_ready",
                timescale=self.timescale,
                tables=len(_SCHEMA_STATEMENTS),
            )
        except Exception as exc:  # noqa: BLE001
            self.schema_ready = False
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
        if not self.enabled or self.engine is None:
            return {
                "status": self.status,
                "enabled": False,
                "timescale": self.timescale,
                "schema_ready": self.schema_ready,
            }
        try:
            async with self.engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            return {
                "status": "ok",
                "enabled": True,
                "timescale": self.timescale,
                "schema_ready": self.schema_ready,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": f"error:{exc}",
                "enabled": True,
                "timescale": self.timescale,
                "schema_ready": self.schema_ready,
            }
db_manager = DatabaseManager()
