from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.schemas import DataStatus


class Candle(BaseModel):
    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float | None = None
    trade_count: int | None = None
    taker_buy_volume: float | None = None
    taker_buy_quote_volume: float | None = None
    is_closed: bool = False
    timestamp: datetime
    source: str = "binance_ws"
    status: DataStatus = DataStatus.LIVE

    def as_mapping(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "open_time": self.open_time,
            "close_time": self.close_time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "quote_volume": self.quote_volume,
            "trade_count": self.trade_count,
            "taker_buy_volume": self.taker_buy_volume,
            "taker_buy_quote_volume": self.taker_buy_quote_volume,
            "is_closed": self.is_closed,
            "closed": self.is_closed,
            "timestamp": self.timestamp,
            "time": self.open_time,
            "source": self.source,
            "status": self.status.value,
        }


class GapInfo(BaseModel):
    symbol: str
    timeframe: str
    expected_open_time: datetime
    previous_open_time: datetime | None = None
    next_open_time: datetime | None = None


class StreamShard(BaseModel):
    connection_index: int
    streams: list[str] = Field(default_factory=list)

    @property
    def stream_count(self) -> int:
        return len(self.streams)
