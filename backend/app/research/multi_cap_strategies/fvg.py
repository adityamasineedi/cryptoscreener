"""Chronological three-candle FVG detector with stateful mitigation.

Research-only. No existing production FVG engine is present in this codebase
(confirmed by trade_plan_forensics). Mitigation rule is explicit in config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.research.multi_cap_strategies.config import FVG_MITIGATION_RULE


@dataclass
class FVGZone:
    created_at: int  # index of Candle 3 (formation complete)
    direction: str  # BULLISH | BEARISH
    lower: float
    upper: float
    size: float
    mitigated: bool = False
    mitigation_time: int | None = None
    mitigation_price: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "direction": self.direction,
            "lower": self.lower,
            "upper": self.upper,
            "size": self.size,
            "mitigated": self.mitigated,
            "mitigation_time": self.mitigation_time,
            "mitigation_price": self.mitigation_price,
            "mitigation_rule": FVG_MITIGATION_RULE,
        }


@dataclass
class FVGState:
    zones: list[FVGZone] = field(default_factory=list)
    mitigation_rule: str = FVG_MITIGATION_RULE


def _intersects(low: float, high: float, lower: float, upper: float) -> bool:
    return low <= upper and high >= lower


def _fully_mitigated(direction: str, low: float, high: float, lower: float, upper: float) -> bool:
    if direction == "BULLISH":
        return low <= lower
    return high >= upper


def detect_fvg_at(
    highs: np.ndarray,
    lows: np.ndarray,
    index: int,
) -> FVGZone | None:
    """FVG known ONLY after Candle 3 closes (index is Candle 3).

    Bullish: high[i-2] < low[i] → interval [high[i-2], low[i]]
    Bearish: low[i-2] > high[i] → interval [high[i], low[i-2]]
    """
    if index < 2:
        return None
    h1 = float(highs[index - 2])
    l1 = float(lows[index - 2])
    h3 = float(highs[index])
    l3 = float(lows[index])
    if h1 < l3:
        lower, upper = h1, l3
        return FVGZone(
            created_at=index,
            direction="BULLISH",
            lower=lower,
            upper=upper,
            size=upper - lower,
        )
    if l1 > h3:
        lower, upper = h3, l1
        return FVGZone(
            created_at=index,
            direction="BEARISH",
            lower=lower,
            upper=upper,
            size=upper - lower,
        )
    return None


def update_mitigation(
    state: FVGState,
    *,
    index: int,
    low: float,
    high: float,
    close: float,
) -> list[FVGZone]:
    """Apply mitigation on subsequent candles (not formation candle).

    Full mitigation does not require a prior intersection check: a candle
    entirely beyond the gap (e.g. bullish FVG with high < lower) still counts
    as having traded through the zone.
    """
    newly: list[FVGZone] = []
    for z in state.zones:
        if z.mitigated:
            continue
        if index <= z.created_at:
            continue
        if _fully_mitigated(z.direction, low, high, z.lower, z.upper):
            z.mitigated = True
            z.mitigation_time = index
            z.mitigation_price = float(close)
            newly.append(z)
    return newly


def unmitigated_zones(
    state: FVGState,
    *,
    direction: str | None = None,
    as_of_index: int | None = None,
) -> list[FVGZone]:
    out: list[FVGZone] = []
    for z in state.zones:
        if z.mitigated:
            continue
        if as_of_index is not None and z.created_at > as_of_index:
            continue
        if direction and z.direction != direction:
            continue
        out.append(z)
    return out


def build_fvg_timeline(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
) -> FVGState:
    """Walk chronologically: form FVGs, then mitigate on later bars."""
    state = FVGState()
    n = len(closes)
    for i in range(n):
        # Mitigate existing first using this bar, then form new FVG at close
        # Formation bar cannot mitigate its own FVG (created_at == i skipped).
        update_mitigation(
            state,
            index=i,
            low=float(lows[i]),
            high=float(highs[i]),
            close=float(closes[i]),
        )
        zone = detect_fvg_at(highs, lows, i)
        if zone is not None:
            state.zones.append(zone)
    return state
