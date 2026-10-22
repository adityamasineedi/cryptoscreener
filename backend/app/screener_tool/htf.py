"""Higher-timeframe context from CLOSED bars only (owner D188 part 1b).

Per timeframe (4h, 1d): close vs EMA50 and EMA20 vs EMA50, each an arrow (UP / DOWN / FLAT).
A bar counts only when ``open_time + timeframe <= now`` and it is not flagged open, so the bar still
forming is never used, even when a store hands it over as "closed".

The EMA is this module's own (engines/mtf/indicators.py is a frozen CANDLE12 V3 OOS-2 file and is not
imported here): seeded with the SMA of the first ``period`` closes of the window, then
``ema = close x k + ema x (1 - k)``, ``k = 2 / (period + 1)``, the same recurrence as the app's helper.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

FAST = 20
SLOW = 50
MAX_BARS = 300  # the EMA window: the last 300 closed bars at most
HTF_TIMEFRAMES = ("4h", "1d")

UP, DOWN, FLAT, MIXED = "UP", "DOWN", "FLAT", "MIXED"
_TF_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def timeframe_delta(tf: str) -> timedelta:
    s = str(tf or "").strip().lower()
    if len(s) < 2 or s[-1] not in _TF_SECONDS or not s[:-1].isdigit():
        raise ValueError(f"unknown timeframe {tf!r}")
    return timedelta(seconds=int(s[:-1]) * _TF_SECONDS[s[-1]])


def ema(values: Sequence[float], period: int) -> float | None:
    if period < 1:
        raise ValueError("period must be >= 1")
    series = [float(v) for v in values]
    if len(series) < period:
        return None
    k = 2.0 / (period + 1)
    out = sum(series[:period]) / period
    for price in series[period:]:
        out = price * k + out * (1.0 - k)
    return out


def _utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, (int, float)):
        ts = datetime.fromtimestamp(float(value) / (1000.0 if value > 1e11 else 1.0), tz=timezone.utc)
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            ts = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _field(bar: Any, *names: str) -> Any:
    for name in names:
        if isinstance(bar, Mapping):
            if name in bar and bar[name] is not None:
                return bar[name]
        else:
            val = getattr(bar, name, None)
            if val is not None:
                return val
    return None


def closed_bars(bars: Sequence[Any], timeframe: str, now: datetime) -> list[tuple[datetime, float]]:
    """(open time, close) of bars fully closed at ``now``, oldest first, one per open time."""
    step = timeframe_delta(timeframe)
    now = _utc(now) or datetime.now(timezone.utc)
    seen: dict[datetime, float] = {}
    for bar in bars or []:
        if _field(bar, "is_closed", "closed") is False:
            continue
        opened = _utc(_field(bar, "open_time", "time", "timestamp"))
        close = _field(bar, "close")
        if opened is None or close is None:
            continue
        if opened + step > now:
            continue  # still forming at ``now``
        try:
            seen[opened] = float(close)
        except (TypeError, ValueError):
            continue
    return sorted(seen.items())


def _arrow(a: float, b: float) -> str:
    if a > b:
        return UP
    if a < b:
        return DOWN
    return FLAT


def htf_state(bars: Sequence[Any], timeframe: str, now: datetime) -> dict[str, Any]:
    """Trend arrows for one timeframe from closed bars only."""
    rows = closed_bars(bars, timeframe, now)[-MAX_BARS:]
    out: dict[str, Any] = {
        "timeframe": timeframe,
        "status": "OK",
        "bars": len(rows),
        "last_closed_bar": rows[-1][0].isoformat() if rows else None,
        "close": None,
        "ema20": None,
        "ema50": None,
        "close_vs_ema50": None,
        "ema20_vs_ema50": None,
        "trend": None,
    }
    if len(rows) < SLOW:
        out["status"] = "NO_DATA" if not rows else "SHORT_HISTORY"
        return out
    closes = [c for _, c in rows]
    e20 = ema(closes, FAST)
    e50 = ema(closes, SLOW)
    assert e20 is not None and e50 is not None
    price = closes[-1]
    a, b = _arrow(price, e50), _arrow(e20, e50)
    out.update(
        close=price,
        ema20=e20,
        ema50=e50,
        close_vs_ema50=a,
        ema20_vs_ema50=b,
        trend=a if a == b and a != FLAT else MIXED,
    )
    return out


def htf_agrees(direction: str | None, states: Mapping[str, Mapping[str, Any]]) -> bool | None:
    """LONG needs every HTF trend UP, SHORT every one DOWN. None when a timeframe has no usable data."""
    d = str(direction or "").upper()
    if d not in ("LONG", "SHORT"):
        return None
    want = UP if d == "LONG" else DOWN
    trends = []
    for tf in HTF_TIMEFRAMES:
        st = states.get(tf) or {}
        if st.get("status") != "OK":
            return None
        trends.append(st.get("trend"))
    return all(t == want for t in trends)
