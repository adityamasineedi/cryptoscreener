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
    "CREATE INDEX IF NOT EXISTS idx_signals_symbol_time ON signals (symbol, time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_setup_analyses_sym ON setup_analyses (symbol, time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_setup_events_sym ON setup_events (symbol, timeframe, time DESC)",
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
