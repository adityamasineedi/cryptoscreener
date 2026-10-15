"""Merge the per-family rows.json files and rank cells by trading style.

Reads   backend/scripts/strategy_rank/<family>/rows.json   (combo, scalp, grid, multicap)
Writes  backend/scripts/strategy_rank/ranked.json and ranked.md

Style is decided by setup timeframe: scalp = 5m/15m, day = 1h, swing = 4h/1d,
grid = any row whose family is "grid".

A cell is "fee-aware profitable on this window" only if
  net_pnl_usd > 0, profit_factor > 1.2, trades_closed >= 15,
  and |fees_usd| < gross_pnl_usd.
Grid rows have no per-trade profit factor in every engine, so for grid the
profit-factor test is applied only when the engine reports one.

Usage:  python -B merge_rank.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent
FAMILIES = ("combo", "scalp", "grid", "multicap")
STYLE_BY_TF = {"5m": "scalp", "15m": "scalp", "1h": "day", "4h": "swing", "1d": "swing"}
STYLES = ("scalp", "day", "swing", "grid")
MIN_TRADES = 15
MIN_PF = 1.2
TOP_N = 15
# Ids reported as the same strategy under two names. Dropped only if the data confirms it.
ALIASES = {"COMBO_02_CLOSED_HTF": "COMBO_02", "COMBO_02_V2_1A": "COMBO_02_V2_1_A"}


def num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def style_of(row: dict[str, Any]) -> str | None:
    if str(row.get("family") or "").lower() == "grid":
        return "grid"
    return STYLE_BY_TF.get(str(row.get("timeframe") or "").lower())


def judge(row: dict[str, Any]) -> tuple[bool, list[str]]:
    """Apply the pass rule. Returns (passes, reasons it failed)."""
    net, gross, fees = num(row.get("net_pnl_usd")), num(row.get("gross_pnl_usd")), num(row.get("fees_usd"))
    pf, n = num(row.get("profit_factor")), int(num(row.get("trades_closed")) or 0)
    failed: list[str] = []
    if net is None or net <= 0:
        failed.append("net <= 0")
    if n < MIN_TRADES:
        failed.append(f"trades < {MIN_TRADES}")
    is_grid = style_of(row) == "grid"
    if pf is None:
        # No losing trades gives a null profit factor; that is not a failure when net > 0.
        if not is_grid and int(num(row.get("losses")) or 0) > 0:
            failed.append("profit factor missing")
    elif pf <= MIN_PF:
        failed.append(f"PF <= {MIN_PF}")
    if gross is None or fees is None:
        failed.append("gross or fees missing")
    elif abs(fees) >= gross:
        failed.append("fees >= gross")
    return (not failed), failed


def sort_key(row: dict[str, Any]) -> tuple[float, float, float]:
    return (-(num(row.get("net_pnl_usd")) or -1e18), -(num(row.get("profit_factor")) or 0.0),
            -(num(row.get("avg_r_net")) or 0.0))


def fmt(v: Any, nd: int = 2, sign: bool = False) -> str:
    f = num(v)
    if f is None:
        return "-"
    return f"{f:+.{nd}f}" if sign else f"{f:.{nd}f}"


def main() -> int:
    rows: list[dict[str, Any]] = []
    loaded: dict[str, Any] = {}
    for fam in FAMILIES:
        path = ROOT / fam / "rows.json"
        if not path.exists():
            loaded[fam] = "MISSING rows.json"
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        loaded[fam] = len(data)
        for r in data:
            r["_source"] = fam
            rows.append(r)

    # A two-sided walk is reported as BOTH plus LONG and SHORT subsets of the same walk.
    # Keep the subsets and drop BOTH so no trade is counted twice.
    cell_key = lambda r: (r.get("family"), r.get("strategy_id"), r.get("symbol"), r.get("timeframe"))  # noqa: E731
    has_sides = {cell_key(r) for r in rows if str(r.get("direction")).upper() in ("LONG", "SHORT")}
    dropped_both = [r for r in rows if str(r.get("direction")).upper() == "BOTH" and cell_key(r) in has_sides]
    rows = [r for r in rows if not (str(r.get("direction")).upper() == "BOTH" and cell_key(r) in has_sides)]

    # Drop an alias id only when every cell it shares with its twin has the same trades and net.
    idx = {(r.get("strategy_id"), r.get("direction"), r.get("symbol"), r.get("timeframe")): r
           for r in rows if r.get("status") == "OK"}
    aliases_dropped: dict[str, Any] = {}
    for alias, twin in ALIASES.items():
        mine = [(k, r) for k, r in idx.items() if k[0] == alias]
        pairs = [(r, idx.get((twin,) + k[1:])) for k, r in mine]
        same = [a for a, b in pairs if b is not None and a.get("trades_closed") == b.get("trades_closed")
                and abs((num(a.get("net_pnl_usd")) or 0.0) - (num(b.get("net_pnl_usd")) or 0.0)) < 1e-9]
        confirmed = bool(mine) and len(same) == len(mine)
        aliases_dropped[alias] = {"same_as": twin, "cells": len(mine), "cells_identical": len(same), "dropped": confirmed}
        if confirmed:
            rows = [r for r in rows if r.get("strategy_id") != alias]

    ok = [r for r in rows if r.get("status") == "OK"]
    not_ok = [r for r in rows if r.get("status") != "OK"]
    for r in ok:
        r["style"] = style_of(r)
        r["passes"], r["fail_reasons"] = judge(r)
        # USD is only comparable across engines at the study sizing (20 USD risk; grid: 1000 USD capital).
        risk = num(r.get("risk_usd"))
        r["usd_comparable"] = r["style"] == "grid" or risk is None or abs(risk - 20.0) < 1e-9

    by_style: dict[str, list[dict[str, Any]]] = {s: [] for s in STYLES}
    for r in ok:
        if r["style"] in by_style:
            by_style[r["style"]].append(r)
    for s in STYLES:
        by_style[s].sort(key=sort_key)

    # Strategy-level roll-up inside each style: one line per (strategy, direction, timeframe).
    rollup: dict[str, list[dict[str, Any]]] = {}
    for s in STYLES:
        groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
        for r in by_style[s]:
            groups[(r.get("family"), r.get("strategy_id"), r.get("direction"), r.get("timeframe"))].append(r)
        out = []
        for (fam, sid, d, tf), cells in groups.items():
            nets = [num(c.get("net_pnl_usd")) or 0.0 for c in cells]
            out.append({
                "family": fam, "strategy_id": sid, "direction": d, "timeframe": tf,
                "coins": len(cells),
                "coins_net_positive": sum(1 for x in nets if x > 0),
                "coins_passing": sum(1 for c in cells if c["passes"]),
                "passing_symbols": sorted(str(c.get("symbol")).replace("USDT", "") for c in cells if c["passes"]),
                "trades": sum(int(num(c.get("trades_closed")) or 0) for c in cells),
                "net_pnl_usd": sum(nets),
                "fees_usd": sum(num(c.get("fees_usd")) or 0.0 for c in cells),
                "gross_pnl_usd": sum(num(c.get("gross_pnl_usd")) or 0.0 for c in cells),
                "worst_cell_net_usd": min(nets) if nets else None,
                "best_cell_net_usd": max(nets) if nets else None,
                "execution_models": sorted({str(c.get("execution_model")) for c in cells}),
                "usd_comparable": all(c["usd_comparable"] for c in cells),
                "risk_usd": sorted({num(c.get("risk_usd")) for c in cells if num(c.get("risk_usd")) is not None}),
                "avg_r_net_mean": (sum(num(c.get("avg_r_net")) or 0.0 for c in cells) / len(cells)) if cells else None,
            })
        out.sort(key=lambda g: (-g["coins_passing"], -g["net_pnl_usd"]))
        rollup[s] = out

    summary = {
        "rows_loaded": loaded,
        "cells_ok": len(ok),
        "cells_not_ok": len(not_ok),
        "both_rows_dropped_as_duplicates": len(dropped_both),
        "alias_ids": aliases_dropped,
        "cells_not_usd_comparable": sum(1 for r in ok if not r["usd_comparable"]),
        "rule": {"net_pnl_usd": "> 0", "profit_factor": f"> {MIN_PF}", "trades_closed": f">= {MIN_TRADES}",
                 "fees": "|fees| < gross"},
        "style_by_timeframe": STYLE_BY_TF,
        "per_style": {s: {"cells": len(by_style[s]),
                          "cells_net_positive": sum(1 for r in by_style[s] if (num(r.get("net_pnl_usd")) or 0) > 0),
                          "cells_passing": sum(1 for r in by_style[s] if r["passes"])} for s in STYLES},
    }
    (ROOT / "ranked.json").write_text(json.dumps({
        "summary": summary,
        "strategy_rollup": rollup,
        "cells_ranked": {s: [{k: v for k, v in r.items() if k != "_source"} for r in by_style[s]] for s in STYLES},
        "not_run_or_error": [{k: r.get(k) for k in ("family", "strategy_id", "direction", "symbol", "timeframe",
                                                    "status", "reason")} for r in not_ok],
    }, indent=1), encoding="utf-8")

    md = ["# Strategy ranking", "", f"Rows loaded: {loaded}", f"Cells OK: {len(ok)}, not run or error: {len(not_ok)}", ""]
    for s in STYLES:
        ps = summary["per_style"][s]
        md += [f"## {s}", "",
               f"Cells: {ps['cells']}, net positive: {ps['cells_net_positive']}, passing the rule: {ps['cells_passing']}", "",
               "### Strategies (all coins combined)", "",
               "| # | Strategy | Dir | TF | Coins passing | Coins net+ | Trades | Net USD | Fees USD | Worst coin | Passing coins |",
               "|---|---|---|---|---|---|---|---|---|---|---|"]
        for i, g in enumerate(rollup[s][:TOP_N], 1):
            md.append(f"| {i} | {g['strategy_id']} | {g['direction']} | {g['timeframe']} | {g['coins_passing']}/{g['coins']} | "
                      f"{g['coins_net_positive']}/{g['coins']} | {g['trades']} | {fmt(g['net_pnl_usd'], sign=True)} | "
                      f"{fmt(g['fees_usd'])} | {fmt(g['worst_cell_net_usd'], sign=True)} | {' '.join(g['passing_symbols']) or '-'} |")
        md += ["", "### Top cells", "",
               "| # | Strategy | Dir | Symbol | TF | Trades | Win % | Net USD | Fees USD | PF | Max DD USD | Passes |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for i, r in enumerate(by_style[s][:TOP_N], 1):
            wr = num(r.get("win_rate"))
            md.append(f"| {i} | {r.get('strategy_id')} | {r.get('direction')} | {r.get('symbol')} | {r.get('timeframe')} | "
                      f"{r.get('trades_closed')} | {fmt(wr * 100 if wr is not None else None, 1)} | "
                      f"{fmt(r.get('net_pnl_usd'), sign=True)} | {fmt(r.get('fees_usd'))} | {fmt(r.get('profit_factor'))} | "
                      f"{fmt(r.get('max_drawdown_usd'))} | {'yes' if r['passes'] else 'no: ' + ', '.join(r['fail_reasons'])} |")
        md.append("")
    (ROOT / "ranked.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
