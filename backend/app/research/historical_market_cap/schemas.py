"""Schemas for historical market-cap research observations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class CapObservation:
    symbol: str
    effective_time: datetime
    market_cap: float
    source: str
    currency: str
    cap_group: str
    classification_rule_version: str
    provider_coin_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["effective_time"] = self.effective_time.isoformat()
        return d


@dataclass(frozen=True)
class CapLookupResult:
    """Result of cap_group_at(symbol, timestamp)."""

    symbol: str
    as_of: datetime
    market_cap: float | None
    effective_time: datetime | None
    source: str | None
    currency: str | None
    cap_group: str  # LARGE_CAP|MID_CAP|SMALL_CAP|BTC|ETH|UNAVAILABLE|UNKNOWN
    strategy_cap_group: str  # LARGE_CAP|MID_CAP|SMALL_CAP|UNAVAILABLE
    classification_rule_version: str
    status: str  # OK | CAP_GROUP_UNAVAILABLE
    reason: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        d["effective_time"] = (
            self.effective_time.isoformat() if self.effective_time else None
        )
        return d
