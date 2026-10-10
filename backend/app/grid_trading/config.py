"""Grid inputs, hard limits, and the feature flag.

The flag is read here from the environment. Application settings stay untouched.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MARKET = "binance_usdt_m_futures"
GRID_COUNT_MIN = 1
GRID_COUNT_MAX = 200
BPS_MAX = 500.0


class GridConfigError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def grid_trading_enabled() -> bool:
    raw = os.getenv("GRID_TRADING_ENABLED", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


class GridConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = ""
    timeframe: str = "1h"
    mode: Literal["backtest", "paper", "live"] = "backtest"
    grid_type: Literal["arithmetic", "geometric"] = "arithmetic"
    range_low: float
    range_high: float
    grid_count: int
    capital: float
    max_capital: float = 1_000_000.0
    fee_bps: float = Field(default=4.0, ge=0)
    taker_fee_bps: float | None = None
    slippage_bps: float = Field(default=1.0, ge=0)
    leverage: float = 1.0
    stop_price: float
    max_drawdown: float = 0.5
    max_open_orders: int = 50
    paused: bool = False
    intrabar_path: Literal["worst", "buy_then_sell", "sell_then_buy"] = "worst"
    market: Literal["binance_usdt_m_futures"] = MARKET


def _bps(value: float) -> float:
    return value / 10_000.0


def maker_fee_rate(config: GridConfig) -> float:
    """Limit-grid fills. ``fee_bps`` is the maker rate."""
    return _bps(config.fee_bps)


def taker_fee_rate(config: GridConfig) -> float:
    """Stops, drawdown exits, and the buy-and-hold benchmark."""
    bps = config.fee_bps if config.taker_fee_bps is None else config.taker_fee_bps
    return _bps(bps)


def validate_config(config: GridConfig, levels: list[float]) -> None:
    """Reject inputs that cannot be run. Does not look at candles."""
    if config.mode == "live":
        raise GridConfigError("live_disabled", "live grid execution is disabled")
    if config.mode == "paper":
        raise GridConfigError(
            "paper_not_available",
            "grid paper is a later isolated book and is not available",
        )
    if config.mode != "backtest":
        raise GridConfigError("mode", "default mode is backtest")
    if config.market != MARKET:
        raise GridConfigError("market", "grid backtests use Binance USDT-M futures candles")

    if config.range_low <= 0 or config.range_high <= config.range_low:
        raise GridConfigError("range", "range_low must be > 0 and below range_high")
    if config.grid_count < GRID_COUNT_MIN or config.grid_count > GRID_COUNT_MAX:
        raise GridConfigError(
            "grid_count",
            f"grid_count must be between {GRID_COUNT_MIN} and {GRID_COUNT_MAX}",
        )
    if len(levels) != config.grid_count + 1:
        raise GridConfigError("grid_count", "level count does not match grid_count")
    if config.max_capital <= 0 or config.capital <= 0:
        raise GridConfigError("capital", "capital and max_capital must be > 0")
    if config.capital > config.max_capital:
        raise GridConfigError("max_capital", "capital exceeds max_capital")
    taker_bps = config.fee_bps if config.taker_fee_bps is None else config.taker_fee_bps
    if config.fee_bps > BPS_MAX or taker_bps > BPS_MAX or config.slippage_bps > BPS_MAX:
        raise GridConfigError("fees", f"fee_bps, taker_fee_bps, and slippage_bps must be <= {BPS_MAX}")
    if config.leverage <= 0 or config.leverage > 1:
        raise GridConfigError("leverage", "leverage must be in (0, 1]; this book is unleveraged")
    if not (0 < config.max_drawdown <= 1):
        raise GridConfigError("max_drawdown", "max_drawdown must be in (0, 1]")
    if config.max_open_orders < 1:
        raise GridConfigError("max_open_orders", "max_open_orders must be >= 1")
    if config.grid_count > config.max_open_orders:
        raise GridConfigError(
            "max_open_orders",
            "grid_count exceeds max_open_orders",
        )
    if config.stop_price <= 0 or config.stop_price >= config.range_low:
        raise GridConfigError("stop", "stop must be > 0 and below the range")

    round_trip = 2.0 * maker_fee_rate(config)
    spacing = min(levels[i + 1] / levels[i] - 1.0 for i in range(len(levels) - 1))
    if spacing <= round_trip + 1e-12:
        raise GridConfigError(
            "spacing",
            "grid spacing does not clear the round-trip maker fee",
        )


def slot_quantity(capital: float, grid_count: int, buy_level: float, config: GridConfig) -> float:
    """Size each slot so the slipped, fee-inclusive buy spends an equal quote slice."""
    quote = capital / grid_count
    fee = maker_fee_rate(config)
    denom = buy_level * (1.0 + fee)
    if denom <= 0:
        raise GridConfigError("capital", "cannot size a slot")
    return quote / denom
