"""USDT-M funding settlements for the long grid.

A settlement is charged from the inventory held after the last candle at or
before that timestamp. The price is the row's mark price when that value is
finite and positive, otherwise that candle's close. Positive rate: the long
pays. The cadence used to spot missing rows is the median gap between stored
timestamps, not a fixed 8 hour clock.
"""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

from sqlalchemy import text

from app.grid_trading.config import GridConfigError

FUNDING_EXCLUDED = "Funding is not charged."
FUNDING_UNAVAILABLE = (
    "Funding is unavailable. No settlement history covers this symbol and window."
)
FUNDING_QUOTES = (
    "Funding is unavailable. These rows are live funding quotes, not settlements."
)
_QUOTE_SOURCES = frozenset({"binance_ws", "binance_rest"})

_GAP = 1.5


def stamp(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    return float(value)


def settlement_price(row: dict[str, Any], close: float) -> tuple[float, str]:
    mark = row.get("mark_price")
    if mark is not None:
        try:
            number = float(mark)
        except (TypeError, ValueError):
            number = float("nan")
        if number == number and number > 0 and number not in (float("inf"), float("-inf")):
            return number, "mark_price"
    return float(close), "close"


def due_settlements(
    rows: list[dict[str, Any]],
    *,
    bar_time: float,
    next_time: float | None,
    window_end: float,
) -> list[dict[str, Any]]:
    """Settlements whose last candle is this one: [bar_time, next_time).

    The final candle also takes a settlement exactly at the window end.
    """
    chosen = []
    for row in rows:
        if row.get("context"):
            continue
        when = float(row["stamp"])
        if when < bar_time:
            continue
        if next_time is None:
            if when <= window_end:
                chosen.append(row)
        elif bar_time <= when < next_time:
            chosen.append(row)
    chosen.sort(key=lambda row: float(row["stamp"]))
    return chosen


def assess_coverage(
    rows: list[dict[str, Any]],
    *,
    window_start: float,
    window_end: float,
) -> dict[str, Any]:
    """Share of settlements in the window that have a stored rate.

    Gaps wider than 1.5x the median stored interval count as missing
    settlements. No interval is hard-coded.
    """
    inside = [
        row
        for row in rows
        if not row.get("context") and window_start <= float(row["stamp"]) <= window_end
    ]
    if not inside:
        return {
            "funding_coverage_pct": 0.0,
            "funding_included": False,
            "warnings": [FUNDING_EXCLUDED],
            "uncovered_from": None,
            "uncovered_to": None,
        }
    times = sorted(float(row["stamp"]) for row in rows)
    diffs = [right - left for left, right in zip(times, times[1:]) if right > left]
    missing = 0
    gap_from = None
    gap_to = None
    if diffs:
        step = float(median(diffs))
        points = [window_start, *[float(row["stamp"]) for row in inside], window_end]
        for left, right in zip(points, points[1:]):
            if right - left <= step * _GAP:
                continue
            count = max(1, int(round((right - left) / step)) - 1)
            missing += count
            gap_from = left if gap_from is None else min(gap_from, left)
            gap_to = right if gap_to is None else max(gap_to, right)
    total = len(inside) + missing
    coverage = len(inside) / total if total else 0.0
    warnings: list[str] = []
    if missing and gap_from is not None and gap_to is not None:
        warnings.append(
            f"Funding coverage is {coverage:.2%}. "
            f"No settlement data from {gap_from} to {gap_to}."
        )
    if len(diffs) >= 2:
        split = len(diffs) // 2
        early = float(median(diffs[:split]))
        late = float(median(diffs[split:]))
        if early > 0 and late > 0 and max(early, late) > min(early, late) * _GAP:
            warnings.append(
                "Funding settlement interval changes inside the window "
                f"({early} then {late}). "
                "Coverage uses the median interval, so a missing row can be missed or over-counted."
            )
    return {
        "funding_coverage_pct": coverage,
        "funding_included": True,
        "warnings": warnings,
        "uncovered_from": gap_from,
        "uncovered_to": gap_to,
    }


_TF_SECONDS = {
    "1m": 60.0,
    "5m": 300.0,
    "15m": 900.0,
    "30m": 1800.0,
    "1h": 3600.0,
    "4h": 14400.0,
    "1d": 86400.0,
}
_TF_ALIASES = {
    "1min": "1m",
    "5min": "5m",
    "15min": "15m",
    "30min": "30m",
    "60m": "1h",
    "1hour": "1h",
    "1-hour": "1h",
    "4hour": "4h",
    "4-hour": "4h",
    "1day": "1d",
    "1-day": "1d",
}


def timeframe_seconds(timeframe: str) -> float | None:
    key = (timeframe or "").strip().lower()
    key = _TF_ALIASES.get(key, key)
    return _TF_SECONDS.get(key)


def coarse_inventory_warning(
    timeframe: str,
    bars: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> str | None:
    """Warn when one candle covers more than one settlement.

    The check runs only when the candle gaps match the stated timeframe, so a
    synthetic clock that is not that timeframe is left alone.
    """
    duration = timeframe_seconds(timeframe)
    if duration is None or len(bars) < 2:
        return None
    bar_times = sorted(stamp(bar["time"]) for bar in bars)
    bar_diffs = [right - left for left, right in zip(bar_times, bar_times[1:]) if right > left]
    if not bar_diffs:
        return None
    bar_step = float(median(bar_diffs))
    if abs(bar_step - duration) / duration > 0.25:
        return None
    settle_times = sorted(float(row["stamp"]) for row in rows if not row.get("context"))
    settle = [right - left for left, right in zip(settle_times, settle_times[1:]) if right > left]
    if not settle:
        return None
    if duration <= float(median(settle)):
        return None
    return (
        f"Candle timeframe {timeframe} is longer than the funding settlement spacing. "
        "Inventory at each settlement is the inventory after that candle, so it is approximated."
    )


def distinct_settlements(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per snapped funding time. Context rows stay, for the cadence only."""
    seen: set[float] = set()
    chosen = []
    for row in rows:
        when = float(row["stamp"])
        if when >= 1_000_000_000:
            when = snap_funding_time_seconds(when)
            row = {**row, "stamp": when}
        if when in seen:
            continue
        seen.add(when)
        chosen.append(row)
    chosen.sort(key=lambda item: float(item["stamp"]))
    return chosen


def snap_funding_time_seconds(when: float) -> float:
    return float(round(when))


def funding_result_guards(
    rows: list[dict[str, Any]],
    coverage: dict[str, Any],
    *,
    payments: list[tuple[float, float]],
) -> dict[str, Any]:
    """Median spacing under 1 hour is not a settlement series.

    Coverage under 95% hides funded net PnL. Annualized funding above 50% of
    average notional is a warning only.
    """
    status = "charged"
    warnings = list(coverage.get("warnings") or [])
    inside = [row for row in rows if not row.get("context")]
    stamps = sorted(float(row["stamp"]) for row in inside)
    diffs = [right - left for left, right in zip(stamps, stamps[1:]) if right > left]
    if stamps and max(stamps) >= 1_000_000_000 and diffs and float(median(diffs)) < 3600.0:
        return {
            "funding_status": "unavailable",
            "funding_included": False,
            "funding_coverage_pct": None,
            "warnings": [FUNDING_QUOTES],
            "hide_funded_net": True,
        }
    pct = coverage.get("funding_coverage_pct")
    if inside and pct is not None and float(pct) < 0.95:
        status = "incomplete"
        warnings.append(
            f"Funding is incomplete. Coverage is {float(pct):.2%}, so funded net PnL is not shown."
        )
    if diffs and payments:
        step = float(median(diffs))
        rates = [rate for _notional, rate in payments if rate == rate]
        notionals = [notional for notional, _rate in payments]
        if step > 0 and rates and notionals and (sum(notionals) / len(notionals)) > 0:
            annualized = abs(sum(rates) / len(rates)) * (365.25 * 86400.0 / step)
            if annualized > 0.50:
                warnings.append(
                    "Annualized funding on average notional is "
                    f"{annualized:.2%}, above 50%."
                )
    return {
        "funding_status": status,
        "funding_included": status == "charged",
        "funding_coverage_pct": pct,
        "warnings": warnings,
        "hide_funded_net": status != "charged",
    }


def quotes_not_settlements(rows: list[dict[str, Any]]) -> bool:
    """True when the rows are the live quote table, which charge mode must not use."""
    if any(str(row.get("source") or "") in _QUOTE_SOURCES for row in rows):
        return True
    stamps = sorted(float(row["stamp"]) for row in rows)
    if not stamps or max(stamps) < 1_000_000_000:
        return False
    diffs = [right - left for left, right in zip(stamps, stamps[1:]) if right > left]
    return len(diffs) >= 2 and float(median(diffs)) < 3600.0


def normalize_funding(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not rows:
        return []
    out = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise GridConfigError("funding", "each funding row must be an object")
        try:
            when = stamp(raw.get("time"))
            rate = float(raw.get("rate", raw.get("funding_rate")))
        except (TypeError, ValueError) as exc:
            raise GridConfigError("funding", "funding time and rate must be numbers") from exc
        if rate != rate or rate in (float("inf"), float("-inf")):
            raise GridConfigError("funding", "funding rate is not finite")
        out.append(
            {
                "time": raw.get("time"),
                "stamp": when,
                "rate": rate,
                "mark_price": raw.get("mark_price"),
                "source": raw.get("source"),
                "context": bool(raw.get("context")),
            }
        )
    out.sort(key=lambda row: float(row["stamp"]))
    return out


async def load_funding_window(symbol: str, start: Any, end: Any) -> list[dict[str, Any]]:
    """Read funding_rates for diagnostics. Charge mode must not call this.

    The table is a live quote of the current rate, not a settlement history.
    """
    from app.services.database import db_manager

    if db_manager.engine is None:
        raise GridConfigError("funding", "funding rates are unavailable because the database is not connected")
    sym = (symbol or "").strip().upper()
    if not sym:
        raise GridConfigError("funding", "symbol is required to load funding rates")
    query = text(
        """
        SELECT time, funding_rate, mark_price
        FROM funding_rates
        WHERE symbol = :symbol AND time >= :start AND time <= :end
        ORDER BY time ASC
        """
    )
    before = text(
        """
        SELECT time, funding_rate, mark_price
        FROM funding_rates
        WHERE symbol = :symbol AND time < :start
        ORDER BY time DESC
        LIMIT 1
        """
    )
    after = text(
        """
        SELECT time, funding_rate, mark_price
        FROM funding_rates
        WHERE symbol = :symbol AND time > :end
        ORDER BY time ASC
        LIMIT 1
        """
    )
    params = {"symbol": sym, "start": start, "end": end}
    async with db_manager.engine.begin() as conn:
        inside = (await conn.execute(query, params)).fetchall()
        prior = (await conn.execute(before, params)).fetchone()
        nxt = (await conn.execute(after, params)).fetchone()

    def pack(row: Any, *, context: bool) -> dict[str, Any]:
        mark = row[2]
        return {
            "time": row[0],
            "rate": float(row[1]),
            "mark_price": None if mark is None else float(mark),
            "context": context,
        }

    rows: list[dict[str, Any]] = []
    if prior is not None:
        rows.append(pack(prior, context=True))
    rows.extend(pack(row, context=False) for row in inside)
    if nxt is not None:
        rows.append(pack(nxt, context=True))
    return rows
