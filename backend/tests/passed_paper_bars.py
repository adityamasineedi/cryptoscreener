"""Test helpers: closed bars for the causal passed paper book.

Causal passed-book positions exit only on closed bars
(``PaperTradeEngine.resolve_passed_exits``), as the backtest regime book does.
These helpers build those bars for engine-level tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.paper_trade import PaperPosition, PaperTradeEngine, is_causal_passed

HOUR = timedelta(hours=1)


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        ts = value
    else:
        raw = str(value)
        ts = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def flat_bar(when: datetime, price: float) -> dict[str, Any]:
    return {"time": when, "open": price, "high": price, "low": price, "close": price}


def next_bar(tip_time: Any, open_price: float) -> dict[str, Any]:
    """The bar after a closed tip, as the watcher's fill source."""
    return {"time": _utc(tip_time) + HOUR, "open": float(open_price)}


def exit_passed_on_bar(
    eng: PaperTradeEngine,
    symbol: str,
    price: float,
    when: Any,
    *,
    timeframe: str = "1h",
) -> list[PaperPosition]:
    """Walk each causal passed position on ``symbol`` to the 1h bar containing ``when``.

    Bars from the fill bar up to that bar print flat at the entry; that bar
    opens at the entry and trades to ``price``.
    """
    bar_open = _utc(when).replace(minute=0, second=0, microsecond=0)
    closed: list[PaperPosition] = []
    for pos in list(eng._open.values()):  # noqa: SLF001
        if pos.symbol != symbol.upper() or not is_causal_passed(pos):
            continue
        entry = float(pos.entry_price)
        t = _utc(pos.signal_snippet["fill_bar_time"])
        bars = []
        while t < bar_open:
            bars.append(flat_bar(t, entry))
            t += HOUR
        bars.append(
            {
                "time": bar_open,
                "open": entry,
                "high": max(entry, float(price)),
                "low": min(entry, float(price)),
                "close": float(price),
            }
        )
        closed += eng.resolve_passed_exits({eng.replay_key(symbol, timeframe): bars})
    return closed
