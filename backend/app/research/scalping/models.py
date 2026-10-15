from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ScalpCandidate:
    strategy_id: str
    variant: str
    symbol: str
    direction: str
    signal_index: int
    signal_time: datetime
    entry_index: int
    entry_time: datetime | None
    entry_price: float | None
    stop_price: float
    target_price: float | None
    max_holding_minutes: int
    frozen_atr: float
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def risk_per_unit(self) -> float | None:
        if self.entry_price is None:
            return None
        return abs(self.entry_price - self.stop_price)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["signal_time"] = self.signal_time.isoformat()
        out["entry_time"] = self.entry_time.isoformat() if self.entry_time else None
        out["risk_per_unit"] = self.risk_per_unit
        return out


@dataclass
class ScalpTrade:
    candidate: ScalpCandidate
    exit_index: int | None = None
    exit_time: datetime | None = None
    exit_price: float | None = None
    outcome: str = "OPEN"
    r_multiple: float | None = None
    ambiguous_intrabar: bool = False

    def to_dict(self) -> dict[str, Any]:
        out = self.candidate.to_dict()
        out.update(
            {
                "exit_index": self.exit_index,
                "exit_time": self.exit_time.isoformat() if self.exit_time else None,
                "exit_price": self.exit_price,
                "outcome": self.outcome,
                "r_multiple": self.r_multiple,
                "ambiguous_intrabar": self.ambiguous_intrabar,
            }
        )
        return out
