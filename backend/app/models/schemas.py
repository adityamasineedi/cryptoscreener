from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class DataStatus(str, Enum):
    LIVE = "LIVE"
    HISTORICAL = "HISTORICAL"
    CACHED = "CACHED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    WAITING = "WAITING"


class FreshValue(BaseModel, Generic[T]):
    value: T | None = None
    timestamp: datetime | None = None
    source: str = "unknown"
    status: DataStatus = DataStatus.UNAVAILABLE
    methodology: str | None = None

    @classmethod
    def unavailable(cls, source: str = "unknown", methodology: str | None = None) -> FreshValue[T]:
        return cls(
            value=None,
            timestamp=None,
            source=source,
            status=DataStatus.UNAVAILABLE,
            methodology=methodology,
        )

    @classmethod
    def waiting(cls, source: str = "unknown", methodology: str | None = None) -> FreshValue[T]:
        return cls(
            value=None,
            timestamp=None,
            source=source,
            status=DataStatus.WAITING,
            methodology=methodology,
        )

    @classmethod
    def live(cls, value: T, source: str, methodology: str | None = None) -> FreshValue[T]:
        return cls(
            value=value,
            timestamp=datetime.now(timezone.utc),
            source=source,
            status=DataStatus.LIVE,
            methodology=methodology,
        )


# Alias used in docs / API — same shape as FreshValue with methodology required in spirit
MetricValue = FreshValue


class SymbolInfo(BaseModel):
    symbol: str
    base_asset: str
    quote_asset: str
    market_type: str  # spot | futures_perp
    exchange: str = "binance"
    status: str = "TRADING"
    contract_type: str | None = None
    price_precision: int | None = None
    qty_precision: int | None = None
    # Exchange filter values when discovery provides them (preferred over precision).
    tick_size: float | None = None
    step_size: float | None = None


class TickerUpdate(BaseModel):
    symbol: str
    market_type: str
    price: float
    price_change_pct_24h: float | None = None
    high_24h: float | None = None
    low_24h: float | None = None
    volume_24h: float | None = None
    quote_volume_24h: float | None = None
    trade_count: int | None = None
    timestamp: datetime
    source: str = "binance_ws"
    status: DataStatus = DataStatus.LIVE


class MarkPriceUpdate(BaseModel):
    symbol: str
    mark_price: float
    index_price: float | None = None
    funding_rate: float | None = None
    next_funding_time: datetime | None = None
    timestamp: datetime
    source: str = "binance_ws"
    status: DataStatus = DataStatus.LIVE


class ScreenerRow(BaseModel):
    # screener_rank = position in filtered/sorted table; market_rank = provider methodology
    rank: int | None = None  # alias of screener_rank for backward compatibility
    screener_rank: int | None = None
    market_rank: FreshValue[int] = Field(
        default_factory=lambda: FreshValue.waiting(
            "coingecko",
            methodology="CoinGecko market_cap_rank (provider methodology) — not screener table position",
        )
    )
    symbol: str
    base_asset: str
    quote_asset: str
    exchange: str = "binance"
    market_type: str
    price: FreshValue[float] = Field(default_factory=FreshValue.unavailable)
    change_24h_pct: FreshValue[float] = Field(default_factory=FreshValue.unavailable)
    volume_24h: FreshValue[float] = Field(default_factory=FreshValue.unavailable)
    quote_volume_24h: FreshValue[float] = Field(default_factory=FreshValue.unavailable)
    volume_change_pct: FreshValue[float] = Field(
        default_factory=lambda: FreshValue.waiting(
            "ohlcv_store",
            methodology=(
                "(sum(volume_current_window) / sum(volume_previous_window) - 1) * 100 "
                "from consecutive non-overlapping OHLCV windows"
            ),
        )
    )
    volume_change_1h: FreshValue[float] = Field(
        default_factory=lambda: FreshValue.waiting("ohlcv_store")
    )
    volume_change_4h: FreshValue[float] = Field(
        default_factory=lambda: FreshValue.waiting("ohlcv_store")
    )
    volume_change_24h: FreshValue[float] = Field(
        default_factory=lambda: FreshValue.waiting("ohlcv_store")
    )
    volume_change_7d: FreshValue[float] = Field(
        default_factory=lambda: FreshValue.waiting("ohlcv_store")
    )
    performance_1d: FreshValue[float] = Field(
        default_factory=lambda: FreshValue.waiting("ohlcv_store")
    )
    performance_7d: FreshValue[float] = Field(
        default_factory=lambda: FreshValue.waiting("ohlcv_store")
    )
    performance_30d: FreshValue[float] = Field(
        default_factory=lambda: FreshValue.waiting("ohlcv_store")
    )
    performance_90d: FreshValue[float] = Field(
        default_factory=lambda: FreshValue.waiting("ohlcv_store")
    )
    performance_180d: FreshValue[float] = Field(
        default_factory=lambda: FreshValue.waiting("ohlcv_store")
    )
    performance_1y: FreshValue[float] = Field(
        default_factory=lambda: FreshValue.waiting("ohlcv_store")
    )
    performance_ytd: FreshValue[float] = Field(
        default_factory=lambda: FreshValue.waiting("ohlcv_store")
    )
    high_24h: FreshValue[float] = Field(default_factory=FreshValue.unavailable)
    low_24h: FreshValue[float] = Field(default_factory=FreshValue.unavailable)
    funding_rate: FreshValue[float] = Field(default_factory=FreshValue.unavailable)
    open_interest: FreshValue[float] = Field(default_factory=FreshValue.unavailable)
    oi_change_pct: FreshValue[float] = Field(default_factory=FreshValue.unavailable)
    oi_change_24h: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    market_cap: FreshValue[float] = Field(default_factory=FreshValue.unavailable)
    fdv: FreshValue[float] = Field(default_factory=FreshValue.unavailable)
    circulating_supply: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    total_supply: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    max_supply: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    category: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    tvl: FreshValue[float] = Field(default_factory=FreshValue.unavailable)
    volume_mcap: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    mcap_fdv: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    nvt: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    velocity: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    williams_r: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    relative_volume: FreshValue[float] = Field(default_factory=FreshValue.unavailable)
    volatility: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    structure: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    market_structure: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    bos: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    choch: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    nearest_supply_zone: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    nearest_demand_zone: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    zone: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    liquidation: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    long_liquidations: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    short_liquidations: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    liquidation_status: DataStatus = DataStatus.WAITING
    technical_state: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    entry_exit_state: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    tech_rating: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    # Structure setup signal engine columns (explainable setups — not predictions)
    setup_trend: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    setup_bos: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    setup_impulse: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    setup_pullback: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    setup_entry: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    setup_sl: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    setup_tp1: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    setup_rr: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    setup_signal: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    market_signal: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    confirmation_strength: FreshValue[str] = Field(default_factory=FreshValue.waiting)
    social_dominance: FreshValue[float] = Field(default_factory=FreshValue.waiting)
    data_status: DataStatus = DataStatus.WAITING
    updated_at: datetime | None = None
    # Presentation-only screening rank (never a strategy/profit score)
    screen_priority_score: float | None = None
    screen_priority_reason: str | None = None


class HealthResponse(BaseModel):
    status: str
    use_real_data: bool
    redis: str
    database: str
    ingestion: str
    symbols_loaded: int
    tickers_live: int
    rate_limiters: list[dict[str, Any]] = Field(default_factory=list)
    timestamp: datetime
