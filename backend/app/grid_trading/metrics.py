"""Summary figures for a finished grid run."""

from __future__ import annotations

from typing import Any


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    hold = result.get("buy_and_hold") or {}
    grid_return = float(result.get("return") or 0.0)
    hold_return = float(hold.get("return") or 0.0)
    return {
        "market": result.get("market"),
        "accepted": result.get("accepted"),
        "error": result.get("error"),
        "halted": result.get("halted"),
        "fill_count": result.get("fill_count"),
        "touch_count": result.get("touch_count"),
        "fill_rate": result.get("fill_rate"),
        "round_trips": result.get("round_trips"),
        "max_drawdown": result.get("max_drawdown"),
        "fees_paid": result.get("fees_paid"),
        "realized_pnl": result.get("realized_pnl"),
        "unrealized_pnl": result.get("unrealized_pnl"),
        "net_pnl": result.get("net_pnl"),
        "pct_time_in_range": result.get("pct_time_in_range"),
        "avg_profit_per_grid_cycle": result.get("avg_profit_per_grid_cycle"),
        "cycle_count": result.get("cycle_count"),
        "events": result.get("events") or [],
        "warnings": result.get("warnings") or [],
        "funding_included": result.get("funding_included", False),
        "leverage": result.get("leverage"),
        "liquidated_equity": result.get("liquidated_equity"),
        "return": grid_return,
        "buy_and_hold_return": hold_return,
        "excess_return": grid_return - hold_return,
        "bar_count": result.get("bar_count"),
    }


def export_fills(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for fill in result.get("fills") or []:
        rows.append(
            {
                "time": fill.get("time"),
                "side": fill.get("side"),
                "slot": fill.get("slot"),
                "level": fill.get("level"),
                "price": fill.get("price"),
                "qty": fill.get("qty"),
                "fee": fill.get("fee"),
                "reason": fill.get("reason"),
            }
        )
    return rows
