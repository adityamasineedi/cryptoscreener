from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
if not CONFIG_DIR.exists():
    CONFIG_DIR = Path("/app/config")


def load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    use_real_data: bool = Field(default=True, alias="USE_REAL_DATA")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_cors_origins: str = Field(
        default=(
            "http://localhost:5173,http://127.0.0.1:5173,"
            "http://localhost:4173,http://127.0.0.1:4173,"
            "http://localhost:3000"
        ),
        alias="API_CORS_ORIGINS",
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    redis_enabled: bool = Field(default=True, alias="REDIS_ENABLED")

    database_url: str = Field(
        default="postgresql+asyncpg://screener:screener@localhost:5432/cryptoscreener",
        alias="DATABASE_URL",
    )
    database_enabled: bool = Field(default=True, alias="DATABASE_ENABLED")

    binance_spot_rest: str = Field(
        default="https://api.binance.com", alias="BINANCE_SPOT_REST"
    )
    binance_futures_rest: str = Field(
        default="https://fapi.binance.com", alias="BINANCE_FUTURES_REST"
    )
    binance_spot_ws: str = Field(
        default="wss://stream.binance.com:9443", alias="BINANCE_SPOT_WS"
    )
    binance_futures_ws: str = Field(
        default="wss://fstream.binance.com", alias="BINANCE_FUTURES_WS"
    )

    binance_rest_weight_per_minute: int = Field(
        default=1100, alias="BINANCE_REST_WEIGHT_PER_MINUTE"
    )
    binance_rest_max_concurrency: int = Field(
        default=5, alias="BINANCE_REST_MAX_CONCURRENCY"
    )

    stale_ticker_seconds: int = Field(default=15, alias="STALE_TICKER_SECONDS")
    stale_fundamental_seconds: int = Field(
        default=900, alias="STALE_FUNDAMENTAL_SECONDS"
    )
    unavailable_after_seconds: int = Field(
        default=60, alias="UNAVAILABLE_AFTER_SECONDS"
    )
    symbol_refresh_seconds: int = Field(default=300, alias="SYMBOL_REFRESH_SECONDS")
    subscribe_quote: str = Field(default="USDT", alias="SUBSCRIBE_QUOTE")
    max_ws_streams_per_connection: int = Field(
        default=900, alias="MAX_WS_STREAMS_PER_CONNECTION"
    )

    # Open interest scheduler (env overrides market.yaml)
    oi_refresh_seconds: int = Field(default=120, alias="OI_REFRESH_SECONDS")
    oi_concurrency: int = Field(default=3, alias="OI_CONCURRENCY")
    oi_batch_size: int = Field(default=40, alias="OI_BATCH_SIZE")
    oi_priority_mode: str = Field(default="TOP_MARKET_CAP", alias="OI_PRIORITY_MODE")
    oi_top_n: int = Field(default=40, alias="OI_TOP_N")

    paper_trade_enabled: bool = Field(default=True, alias="PAPER_TRADE_ENABLED")
    paper_starting_equity: float = Field(default=1000.0, alias="PAPER_STARTING_EQUITY")
    paper_risk_percent: float = Field(default=0.02, alias="PAPER_RISK_PERCENT")
    # path_a = Trend+BOS (research COMBO_02); path_b = full LONG_ENTRY_CANDIDATE
    paper_entry_mode: str = Field(default="path_a", alias="PAPER_ENTRY_MODE")
    # Legacy 15m setup → PaperTradeEngine.on_setup_signal auto-entry.
    # Default OFF so screener/BOS/liq alerts continue without RESEARCH_15M paper fills.
    paper_legacy_auto_entry_enabled: bool = Field(
        default=False, alias="PAPER_LEGACY_AUTO_ENTRY_ENABLED"
    )
    # Cap / liquidity / liq-spike gates (fail-closed for unknown mcap)
    paper_risk_gates_enabled: bool = Field(default=True, alias="PAPER_RISK_GATES_ENABLED")
    paper_allowed_groups: str = Field(
        default="BTC,ETH,large-cap,mid-cap", alias="PAPER_ALLOWED_GROUPS"
    )
    paper_min_quote_volume_24h: float = Field(
        default=5_000_000.0, alias="PAPER_MIN_QUOTE_VOLUME_24H"
    )
    paper_min_quote_volume_no_mcap: float = Field(
        default=20_000_000.0, alias="PAPER_MIN_QUOTE_VOLUME_NO_MCAP"
    )
    paper_risk_pct_btc_eth: float = Field(default=0.02, alias="PAPER_RISK_PCT_BTC_ETH")
    paper_risk_pct_large: float = Field(default=0.02, alias="PAPER_RISK_PCT_LARGE")
    paper_risk_pct_mid: float = Field(default=0.01, alias="PAPER_RISK_PCT_MID")
    paper_risk_pct_small: float = Field(default=0.005, alias="PAPER_RISK_PCT_SMALL")
    paper_risk_pct_unknown: float = Field(default=0.005, alias="PAPER_RISK_PCT_UNKNOWN")
    paper_max_open_positions: int = Field(default=5, alias="PAPER_MAX_OPEN_POSITIONS")
    paper_max_open_risk_pct: float = Field(default=0.10, alias="PAPER_MAX_OPEN_RISK_PCT")
    paper_liq_gate_enabled: bool = Field(default=True, alias="PAPER_LIQ_GATE_ENABLED")
    paper_liq_min_long_notional_5m: float = Field(
        default=25_000.0, alias="PAPER_LIQ_MIN_LONG_NOTIONAL_5M"
    )
    # COMBO_02 v1 production profile (see app.research.v1_production / docs/v1_production.md)
    paper_v1_profile_enabled: bool = Field(
        default=True, alias="PAPER_V1_PROFILE_ENABLED"
    )
    paper_v1_universe_only: bool = Field(
        default=True, alias="PAPER_V1_UNIVERSE_ONLY"
    )
    paper_v1_secondary_enabled: bool = Field(
        default=True, alias="PAPER_V1_SECONDARY_ENABLED"
    )
    # Dedicated 1h COMBO_02 watcher (evaluate_combination_at_bar)
    paper_v1_watcher_enabled: bool = Field(
        default=True, alias="PAPER_V1_WATCHER_ENABLED"
    )
    # When True, blocks legacy 15m Path A opens so only the watcher fills COMBO_02 v1.
    # Keep False to run v1 watcher + RESEARCH_15M as separate labeled paper streams.
    paper_v1_watcher_owns_entries: bool = Field(
        default=False, alias="PAPER_V1_WATCHER_OWNS_ENTRIES"
    )
    paper_v1_timeframe: str = Field(default="1h", alias="PAPER_V1_TIMEFRAME")
    paper_v1_telegram_enabled: bool = Field(
        default=False, alias="PAPER_V1_TELEGRAM_ENABLED"
    )
    paper_v1_replay_mode: bool = Field(default=False, alias="PAPER_V1_REPLAY_MODE")

    # Dynamic COMBO_02 v2 research candidate pipeline (never joins v1)
    dynamic_candidate_discovery_enabled: bool = Field(
        default=False, alias="DYNAMIC_CANDIDATE_DISCOVERY_ENABLED"
    )
    dynamic_candidate_discovery_top_n: int = Field(
        default=30, alias="DYNAMIC_CANDIDATE_DISCOVERY_TOP_N"
    )
    dynamic_v2_paper_watcher_enabled: bool = Field(
        default=False, alias="DYNAMIC_V2_PAPER_WATCHER_ENABLED"
    )
    dynamic_v2_max_open_positions: int = Field(
        default=1, alias="DYNAMIC_V2_MAX_OPEN_POSITIONS"
    )
    dynamic_v2_max_total_risk_percent: float = Field(
        default=0.005, alias="DYNAMIC_V2_MAX_TOTAL_RISK_PERCENT"
    )
    dynamic_v2_max_v1_book_risk_percent: float = Field(
        default=0.05, alias="DYNAMIC_V2_MAX_V1_BOOK_RISK_PERCENT"
    )

    # Telegram alerts for v1 paper PAPER_ENTRY / PAPER_EXIT (optional)
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, alias="TELEGRAM_CHAT_ID")

    # Research gates for live setup candidates — default OFF
    research_gate_enabled: bool = Field(default=False, alias="RESEARCH_GATE_ENABLED")
    research_gate_block_shorts: bool = Field(
        default=False, alias="RESEARCH_GATE_BLOCK_SHORTS"
    )
    research_gate_block_htf_conflict: bool = Field(
        default=False, alias="RESEARCH_GATE_BLOCK_HTF_CONFLICT"
    )
    # Research-only ENTRY_PRICE_CHECK threshold (percent). Descriptive; not a trade rule.
    research_only_entry_deviation_threshold: float = Field(
        default=0.10, alias="RESEARCH_ONLY_ENTRY_DEVIATION_THRESHOLD"
    )

    # System diagnostics (Issue Center) — thresholds & polling
    diag_ui_refresh_seconds: int = Field(default=5, alias="DIAG_UI_REFRESH_SECONDS")
    diag_expensive_metrics_seconds: int = Field(
        default=30, alias="DIAG_EXPENSIVE_METRICS_SECONDS"
    )
    diag_disk_warning_pct: float = Field(default=70.0, alias="DIAG_DISK_WARNING_PCT")
    diag_disk_error_pct: float = Field(default=85.0, alias="DIAG_DISK_ERROR_PCT")
    diag_disk_critical_pct: float = Field(default=95.0, alias="DIAG_DISK_CRITICAL_PCT")
    diag_memory_warning_pct: float = Field(default=80.0, alias="DIAG_MEMORY_WARNING_PCT")
    diag_memory_error_pct: float = Field(default=90.0, alias="DIAG_MEMORY_ERROR_PCT")
    diag_memory_critical_pct: float = Field(
        default=95.0, alias="DIAG_MEMORY_CRITICAL_PCT"
    )
    diag_cpu_warning_pct: float = Field(default=85.0, alias="DIAG_CPU_WARNING_PCT")
    diag_cpu_error_pct: float = Field(default=95.0, alias="DIAG_CPU_ERROR_PCT")
    diag_event_retention_days: int = Field(default=30, alias="DIAG_EVENT_RETENTION_DAYS")
    diag_resolved_issue_retention_days: int = Field(
        default=90, alias="DIAG_RESOLVED_ISSUE_RETENTION_DAYS"
    )
    diag_snapshot_retention_days: int = Field(
        default=90, alias="DIAG_SNAPSHOT_RETENTION_DAYS"
    )
    diag_resource_sample_retention_days: int = Field(
        default=7, alias="DIAG_RESOURCE_SAMPLE_RETENTION_DAYS"
    )
    # Phase 2 forensic thresholds
    diag_ws_stale_seconds: float = Field(default=120.0, alias="DIAG_WS_STALE_SECONDS")
    diag_db_long_query_seconds: float = Field(
        default=30.0, alias="DIAG_DB_LONG_QUERY_SECONDS"
    )
    diag_db_pool_warning_pct: float = Field(
        default=80.0, alias="DIAG_DB_POOL_WARNING_PCT"
    )
    diag_db_pool_error_pct: float = Field(default=95.0, alias="DIAG_DB_POOL_ERROR_PCT")
    diagnostics_db_detail_cache_seconds: float = Field(
        default=60.0, alias="DIAGNOSTICS_DB_DETAIL_CACHE_SECONDS"
    )
    diag_rest_429_warning: int = Field(default=5, alias="DIAG_REST_429_WARNING")
    diag_job_stale_heartbeat_seconds: float = Field(
        default=300.0, alias="DIAG_JOB_STALE_HEARTBEAT_SECONDS"
    )
    # UI research backtest job limits (observability / stall detection only)
    backtest_job_timeout_seconds: float = Field(
        default=3600.0, alias="BACKTEST_JOB_TIMEOUT_SECONDS"
    )
    backtest_heartbeat_timeout_seconds: float = Field(
        default=120.0, alias="BACKTEST_HEARTBEAT_TIMEOUT_SECONDS"
    )
    backtest_db_timeout_seconds: float = Field(
        default=60.0, alias="BACKTEST_DB_TIMEOUT_SECONDS"
    )
    # Research-only CPU stage timers (default off — no production impact)
    research_profile_enabled: bool = Field(
        default=False, alias="RESEARCH_PROFILE_ENABLED"
    )
    # Phase 3 — backups / snapshots (safe defaults; no auto-delete)
    backup_destination: str = Field(
        default="data/diagnostics/backups", alias="BACKUP_DESTINATION"
    )
    backup_min_free_space_gb: float = Field(
        default=5.0, alias="BACKUP_MIN_FREE_SPACE_GB"
    )
    backup_retention_days: int = Field(default=90, alias="BACKUP_RETENTION_DAYS")
    backup_pg_dump_path: str = Field(default="", alias="BACKUP_PG_DUMP_PATH")
    backup_pg_restore_path: str = Field(default="", alias="BACKUP_PG_RESTORE_PATH")
    snapshot_destination: str = Field(
        default="data/diagnostics/snapshots", alias="SNAPSHOT_DESTINATION"
    )

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def market_config(self) -> dict[str, Any]:
        return load_yaml("market.yaml")

    @property
    def indicators_config(self) -> dict[str, Any]:
        return load_yaml("indicators.yaml")

    @property
    def screener_config(self) -> dict[str, Any]:
        return load_yaml("screener.yaml")

    @property
    def providers_config(self) -> dict[str, Any]:
        return load_yaml("providers.yaml")


@lru_cache
def get_settings() -> Settings:
    # Prefer project-root .env when running from backend/
    root_env = ROOT / ".env"
    if root_env.exists() and not os.getenv("USE_REAL_DATA"):
        from dotenv import load_dotenv

        load_dotenv(root_env)
    return Settings()
