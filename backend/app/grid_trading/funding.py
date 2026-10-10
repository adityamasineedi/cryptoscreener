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

FUNDING_EXCLUDED = "Funding is excluded. USDT-M funding payments are not charged."

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
    if missing and gap_from is not None and gap_to is not None:
        warning = (
            f"Funding coverage is {coverage:.2%}. "
            f"No settlement data from {gap_from} to {gap_to}."
        )
        return {
            "funding_coverage_pct": coverage,
            "funding_included": True,
            "warnings": [warning],
            "uncovered_from": gap_from,
            "uncovered_to": gap_to,
        }
    return {
        "funding_coverage_pct": coverage,
        "funding_included": True,
        "warnings": [],
        "uncovered_from": None,
        "uncovered_to": None,
    }


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
                "context": bool(raw.get("context")),
            }
        )
    out.sort(key=lambda row: float(row["stamp"]))
    return out


async def load_funding_window(symbol: str, start: Any, end: Any) -> list[dict[str, Any]]:
    """Rows inside the window, plus one neighbor on each side for the cadence."""
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
