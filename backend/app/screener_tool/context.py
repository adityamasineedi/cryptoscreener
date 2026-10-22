"""Per-row screener context: cost as R, HTF arrows, research badge, plus the BTC context. Read-only.

Bars come from the live in-memory store first. When a series has fewer closed bars than the slow EMA
needs, a read-only SELECT on the ``ohlcv`` table fills it (cached for a few minutes). Nothing is written
to the store or the database, and nothing is fetched from the exchange.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from app.screener_tool import badges, btc_context, costs, htf

BarsReader = Callable[[Sequence[str], str, datetime], Awaitable[dict[str, list[Any]]]]

DB_CACHE_SECONDS = 300.0
_db_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}


def memory_bars(symbol: str, timeframe: str) -> list[Any]:
    from app.services.ohlcv_store import ohlcv_store

    return list(ohlcv_store.get_closed(symbol, timeframe))


async def db_bars(symbols: Sequence[str], timeframe: str, now: datetime) -> dict[str, list[Any]]:
    """Read-only SELECT of recent closed-looking bars; the closed-bar filter still runs on the result."""
    out: dict[str, list[Any]] = {}
    want: list[str] = []
    clock = time.monotonic()
    for sym in symbols:
        hit = _db_cache.get((sym, timeframe))
        if hit and clock - hit[0] < DB_CACHE_SECONDS:
            out[sym] = hit[1]
        else:
            want.append(sym)
    if not want:
        return out
    from sqlalchemy import text

    from app.services.database import db_manager

    if not db_manager.enabled or db_manager.engine is None:
        return out
    since = now - htf.timeframe_delta(timeframe) * (htf.MAX_BARS + 2)
    try:
        async with db_manager.engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT symbol, time, close FROM ohlcv "
                    "WHERE timeframe = :tf AND symbol = ANY(:syms) AND time >= :since "
                    "ORDER BY symbol, time"
                ),
                {"tf": timeframe, "syms": list(want), "since": since},
            )
            rows = result.fetchall()
    except Exception:  # noqa: BLE001 - display only; the row shows NO_DATA
        return out
    got: dict[str, list[dict[str, Any]]] = {sym: [] for sym in want}
    for sym, opened, close in rows:
        got.setdefault(str(sym).upper(), []).append({"open_time": opened, "close": close})
    for sym, bars in got.items():
        _db_cache[(sym, timeframe)] = (clock, bars)
        out[sym] = bars
    return out


def _clean_symbol(value: Any) -> str | None:
    s = str(value or "").strip().upper()
    if not s or not s.isascii() or not s.isalnum() or len(s) > 30:
        return None
    return s


async def build_context(
    rows: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    memory: Callable[[str, str], list[Any]] = memory_bars,
    fallback: BarsReader | None = db_bars,
    regime: Callable[[datetime], dict[str, Any]] | None = None,
    funding: Callable[[datetime], Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    items: list[dict[str, Any]] = []
    for raw in rows:
        sym = _clean_symbol(raw.get("symbol"))
        if sym is None:
            continue
        items.append({"raw": raw, "symbol": sym})
    symbols = sorted({it["symbol"] for it in items})

    states: dict[str, dict[str, dict[str, Any]]] = {sym: {} for sym in symbols}
    for tf in htf.HTF_TIMEFRAMES:
        short: list[str] = []
        for sym in symbols:
            try:
                bars = memory(sym, tf)
            except Exception:  # noqa: BLE001
                bars = []
            st = htf.htf_state(bars, tf, now)
            st["source"] = "memory"
            states[sym][tf] = st
            if st["status"] != "OK":
                short.append(sym)
        if short and fallback is not None:
            extra = await fallback(short, tf, now)
            for sym in short:
                if extra.get(sym):
                    st = htf.htf_state(extra[sym], tf, now)
                    if st["status"] == "OK" or st["bars"] > states[sym][tf]["bars"]:
                        st["source"] = "db"
                        states[sym][tf] = st

    out_rows = []
    for it in items:
        raw, sym = it["raw"], it["symbol"]
        direction = str(raw.get("direction") or "").upper() or None
        cost = costs.cost_as_r(raw.get("entry_price"), raw.get("stop_price"), sym, side=direction)
        st = states[sym]
        out_rows.append(
            {
                "symbol": sym,
                "direction": direction,
                "tier": raw.get("tier"),
                "status": raw.get("status"),
                "cost": cost,
                "htf": {tf: st[tf] for tf in htf.HTF_TIMEFRAMES},
                "htf_agrees": htf.htf_agrees(direction, st),
                "badge": badges.badge_for(raw),
            }
        )

    reg = (regime or btc_context.btc_regime)(now)
    fund = await (funding or btc_context.btc_funding)(now)
    return {
        "read_only": True,
        "as_of": now.isoformat(),
        "rows": out_rows,
        "btc": {"regime": reg, "funding": fund},
        "rules": {
            "cost": costs.COST_AS_R_FORMULA,
            "cost_r_max": costs.COST_R_MAX,
            "thin_coins": sorted(costs.THIN_COINS),
            "htf": "closed bars only (open + timeframe <= now); close vs EMA50 and EMA20 vs EMA50 on 4h and 1d; "
                   "HTF agrees = LONG with both UP, SHORT with both DOWN",
            "badge": badges.BADGE_TEXT,
            "not_a_signal": badges.NOT_A_SIGNAL,
        },
    }


def clear_cache() -> None:
    _db_cache.clear()

