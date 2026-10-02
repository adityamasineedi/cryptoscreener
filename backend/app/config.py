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
        default="http://localhost:5173,http://localhost:3000",
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

    # Research gates for live setup candidates — default OFF
    research_gate_enabled: bool = Field(default=False, alias="RESEARCH_GATE_ENABLED")
    research_gate_block_shorts: bool = Field(
        default=False, alias="RESEARCH_GATE_BLOCK_SHORTS"
    )
    research_gate_block_htf_conflict: bool = Field(
        default=False, alias="RESEARCH_GATE_BLOCK_HTF_CONFLICT"
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
