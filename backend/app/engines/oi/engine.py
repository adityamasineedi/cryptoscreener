from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence


@dataclass(frozen=True)
class OISample:
    timestamp: datetime
    open_interest: float
    price: float | None = None

    @property
    def open_interest_value(self) -> float | None:
        if self.price is None:
            return None
        return self.open_interest * self.price


def pick_sample_at_or_before(
    history: Sequence[OISample],
    target: datetime,
) -> OISample | None:
    best: OISample | None = None
    for s in history:
        if s.timestamp <= target:
            if best is None or s.timestamp > best.timestamp:
                best = s
    return best


def oi_change_pct(current: float, past: float | None) -> float | None:
    if past is None or past == 0:
        return None
    return ((current - past) / past) * 100.0


_WINDOW_LABELS: tuple[tuple[str, int], ...] = (
    ("5m", 5),
    ("15m", 15),
    ("1h", 60),
    ("4h", 240),
    ("24h", 1440),
)


def oi_changes_from_history(
    history: Sequence[OISample],
    *,
    now: datetime | None = None,
    windows: Sequence[tuple[str, int]] | None = None,
) -> dict[str, float | None]:
    labels = list(windows or _WINDOW_LABELS)
    if not history:
        return {name: None for name, _ in labels}
    now = now or datetime.now(timezone.utc)
    current = history[-1]
    out: dict[str, float | None] = {}
    for name, minutes in labels:
        past = pick_sample_at_or_before(history, now - timedelta(minutes=minutes))
        if past is None or past.timestamp == current.timestamp:
            out[name] = None
        else:
            out[name] = oi_change_pct(current.open_interest, past.open_interest)
    return out


def classify_price_oi(
    price_change_pct: float | None,
    oi_change_pct: float | None,
    *,
    threshold: float = 0.05,
) -> str | None:
    if price_change_pct is None or oi_change_pct is None:
        return None
    pu = price_change_pct > threshold
    pd = price_change_pct < -threshold
    ou = oi_change_pct > threshold
    od = oi_change_pct < -threshold
    if pu and ou:
        return "PRICE_UP_OI_UP"
    if pu and od:
        return "PRICE_UP_OI_DOWN"
    if pd and ou:
        return "PRICE_DOWN_OI_UP"
    if pd and od:
        return "PRICE_DOWN_OI_DOWN"
    return "NEUTRAL"
