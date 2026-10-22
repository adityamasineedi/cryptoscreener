"""HTTP routes for the screener context and My Trades (owner D188). Paper only: no order, no exchange key.

- POST /api/screener-tool/context   cost as R, HTF arrows, research badge per Trade-chances row, BTC context
- GET  /api/my-trades               latest version per trade, stats, honesty panel
- POST /api/my-trades               log a trade (version 1)
- POST /api/my-trades/{id}/edit     new version with the changes
- POST /api/my-trades/{id}/close    new version with the exit (D144 costs)
- POST /api/my-trades/{id}/void     new version flagged void (no delete)
- GET  /api/my-trades/{id}/history  every version of one trade
- GET  /api/my-trades/export.csv    current versions; ?history=1 for every version
- GET  /api/my-trades/price         the live last price, to pre-fill the entry
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import Response

from app.screener_tool import costs, journal_stats
from app.screener_tool.journal_store import (
    JournalError,
    ManualTradeJournal,
    NotFound,
    csv_columns,
    get_journal,
    schema_description,
)

router = APIRouter(tags=["screener-tool"])

MAX_CONTEXT_ROWS = 300
DISCLAIMER = "Paper journal and display only: no orders, no exchange keys. Not a buy or sell signal."


@router.post("/screener-tool/context")
async def screener_context(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    from app.screener_tool.context import build_context

    rows = body.get("rows") or []
    if not isinstance(rows, list):
        raise HTTPException(status_code=422, detail="rows must be a list")
    rows = [r for r in rows if isinstance(r, dict)][:MAX_CONTEXT_ROWS]
    return await build_context(rows)


def _journal() -> ManualTradeJournal:
    j = get_journal()
    if j is None:
        raise HTTPException(status_code=503, detail="My Trades needs the app database, which is not connected")
    return j


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _closed(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in trades if t["status"] == "CLOSED" and not t["voided"] and t.get("r_multiple") is not None]


@router.get("/my-trades")
async def my_trades(include_void: bool = Query(default=True)) -> dict[str, Any]:
    j = _journal()
    trades = await j.latest(include_void=True)
    closed = _closed(trades)
    return {
        "trades": trades if include_void else [t for t in trades if not t["voided"]],
        "counts": {
            "open": sum(1 for t in trades if t["status"] == "OPEN"),
            "closed": len(closed),
            "void": sum(1 for t in trades if t["voided"]),
        },
        "stats": journal_stats.full_stats(closed),
        "cost_model": {
            "name": costs.COST_MODEL,
            "taker_fee_rate": costs.TAKER_FEE_RATE,
            "slippage_rate": costs.SLIPPAGE_RATE,
            "thin_coin_slippage_rate": costs.THIN_COIN_SLIPPAGE_RATE,
            "thin_coins": sorted(costs.THIN_COINS),
            "formula": costs.JOURNAL_FORMULA,
        },
        "storage": {**schema_description(), "append_only_guard": j.append_only_guard, "guard_error": j.guard_error},
        "disclaimer": DISCLAIMER,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/my-trades")
async def my_trades_create(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    j = _journal()
    try:
        return {"trade": await j.create(body)}
    except JournalError as exc:
        raise _fail(exc) from exc


@router.post("/my-trades/{trade_id}/edit")
async def my_trades_edit(trade_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    j = _journal()
    changes = dict(body.get("changes") or {})
    try:
        return {"trade": await j.edit(trade_id, changes, edit_note=body.get("edit_note"))}
    except JournalError as exc:
        raise _fail(exc) from exc


@router.post("/my-trades/{trade_id}/close")
async def my_trades_close(trade_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    j = _journal()
    try:
        trade = await j.close(
            trade_id,
            exit_price=body.get("exit_price"),
            exit_at=body.get("exit_at"),
            exit_reason=body.get("exit_reason"),
            edit_note=body.get("edit_note"),
        )
    except JournalError as exc:
        raise _fail(exc) from exc
    return {"trade": trade}


@router.post("/my-trades/{trade_id}/void")
async def my_trades_void(trade_id: str, body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    j = _journal()
    try:
        return {"trade": await j.void(trade_id, reason=body.get("reason"))}
    except JournalError as exc:
        raise _fail(exc) from exc


@router.get("/my-trades/price")
async def my_trades_price(symbol: str = Query(..., min_length=2, max_length=30)) -> dict[str, Any]:
    from app.services.market_store import market_store

    sym = symbol.strip().upper()
    t = market_store.get_ticker(sym)
    return {
        "symbol": sym,
        "price": float(t.price) if t is not None and t.price else None,
        "as_of": t.timestamp.isoformat() if t is not None and getattr(t, "timestamp", None) else None,
    }


def _csv_value(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat()
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return json.dumps(v)
    return "" if v is None else v


def trades_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    cols = list(csv_columns())
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([_csv_value(r.get(c)) for c in cols])
    return buf.getvalue()


@router.get("/my-trades/export.csv")
async def my_trades_export(history: bool = Query(default=False)) -> Response:
    j = _journal()
    rows = await j.all_versions() if history else await j.latest(include_void=True)
    name = "manual_trades_history.csv" if history else "manual_trades_current.csv"
    return Response(
        content=trades_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/my-trades/{trade_id}/history")
async def my_trades_history(trade_id: str) -> dict[str, Any]:
    j = _journal()
    rows = await j.history(trade_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"no trade {trade_id}")
    return {"trade_id": trade_id, "versions": rows}
