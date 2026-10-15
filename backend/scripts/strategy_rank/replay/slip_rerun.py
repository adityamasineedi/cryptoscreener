"""Slippage re-run of the rank's LONG 1h books (fill price only).

Source of the optimistic trades: the rank's own stored per-trade lists
(combo/trades, combo/oos/trades, multicap/trades). They are the exact output of
the rank's engine walk + fee/sizing step, so no engine walk or DB access is needed.

Parity step (slippage 0): every stored trade is re-sized and re-fee'd with the same
repo function the rank used (app.research.trade_fees.enrich_trade_execution) and the
same equity rule (combo: running equity as enrich_trades(running_equity=True);
multicap: constant equity 1000). Per-trade gross/fee/net and qty must match the
stored values, and the cell metrics must match ranked.json / rows_oos.json.

Slippage step: SLIP_0002 = CANDLE12_V2 fill rule with slippage_rate 0.0002:
  long entry fill = entry * (1 + 0.0002), long exit fill = exit * (1 - 0.0002),
  every exit type. tick_size: CANDLE12_V2 adds cfg.tick_size, whose default is 0.0
  and which no caller sets, so the effective tick is 0 -> tick not applied.
Quantity, entry/exit bars, stop, targets, outcome are the optimistic trade's.
Gross = qty * (exit_fill - entry_fill); fees on executed notional at the fills with
the same maker/taker type; net = gross + fees.

enrich_trades(slippage_rate=...) is NOT used for the slipped book: its gross uses
the unslipped entry (qty * (exit_fill - entry)), and with running_equity=True the
slipped nets would feed back into later trades' leverage cap (quantity change).

Usage (from backend/):  python -B scripts/strategy_rank/replay/slip_rerun.py
Writes only under scripts/strategy_rank/replay/out/.
"""

from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND = Path(r"D:\cryptoscreener\backend")
SR = BACKEND / "scripts" / "strategy_rank"
OUT = SR / "replay" / "out"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.research.trade_fees import (  # noqa: E402
    FEE_TYPE_TAKER,
    _parse_utc,
    calculate_execution_fee,
    enrich_trade_execution,
    fee_type_for_entry,
)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
           "TRXUSDT", "AVAXUSDT", "LINKUSDT"]
COMBO_BOOKS = ["COMBO_02", "COMBO_01", "COMBO_02_LOCAL"]
MC_BOOK = "LARGE_CAP_SWEEP_CHOCH"
PRINCIPAL, RISK, LEV = 1000.0, 20.0, 2.0
TAKER, MAKER = 0.0004, 0.0002
SLIP = 0.0002
TICK = 0.0  # CANDLE12_V2 Candle12V2Config.tick_size default; never set by any caller
TOL = 1e-6


def num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def judge(m: dict[str, Any]) -> tuple[bool, list[str]]:
    """merge_rank.judge for a non-grid row."""
    net, gross, fees = num(m["net_pnl_usd"]), num(m["gross_pnl_usd"]), num(m["fees_usd"])
    pf, n = num(m["profit_factor"]), int(m["trades_closed"] or 0)
    failed = []
    if net is None or net <= 0:
        failed.append("net <= 0")
    if n < 15:
        failed.append("trades < 15")
    if pf is None:
        if int(m.get("losses") or 0) > 0:
            failed.append("profit factor missing")
    elif pf <= 1.2:
        failed.append("PF <= 1.2")
    if gross is None or fees is None:
        failed.append("gross or fees missing")
    elif abs(fees) >= gross:
        failed.append("fees >= gross")
    return (not failed), failed


def dd(vals: list[float]) -> float:
    cum = peak = worst = 0.0
    for v in vals:
        cum += v
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst


def metrics(trades: list[dict[str, Any]], family: str) -> dict[str, Any]:
    """Same formulas as the rank: combo = run_combo_rank.metrics (list order, fees=-|sum|);
    multicap = run_multicap_rank.finish_row (sorted by exit_time, entry_time; fees=sum)."""
    rows = list(trades)
    if family == "multicap":
        rows.sort(key=lambda t: (str(t.get("exit_time") or ""), str(t.get("entry_time") or "")))
    net = [float(t["net_pnl_usd"]) for t in rows]
    gross = [float(t["gross_pnl_usd"]) for t in rows]
    fees = [float(t["fee_total_usd"]) for t in rows]
    n = len(rows)
    wins = sum(1 for v in net if v > 0)
    losses = sum(1 for v in net if v < 0)
    gains = sum(v for v in net if v > 0)
    lost = -sum(v for v in net if v < 0)
    fee_sum = sum(fees)
    return {
        "trades_closed": n, "wins": wins, "losses": losses,
        "win_rate": (wins / n) if n else None,
        "gross_pnl_usd": sum(gross) if n else 0.0,
        "fees_usd": (-abs(fee_sum) if family == "combo" else fee_sum) if n else 0.0,
        "net_pnl_usd": sum(net) if n else 0.0,
        "profit_factor": (gains / lost) if lost > 0 else None,
        "max_drawdown_usd": dd(net) if n else (0.0 if family == "combo" else None),
    }


def slip_fill(entry: float, exit_px: float) -> tuple[float, float]:
    return entry * (1.0 + SLIP) + TICK, exit_px * (1.0 - SLIP)


def leg_fees(qty: float, entry_px: float, exit_px: float, entry_type: str) -> tuple[float, float]:
    fe = calculate_execution_fee(price=entry_px, quantity=qty, fee_type=fee_type_for_entry(entry_type),
                                 maker_rate=MAKER, taker_rate=TAKER)
    fx = calculate_execution_fee(price=exit_px, quantity=qty, fee_type=FEE_TYPE_TAKER,
                                 maker_rate=MAKER, taker_rate=TAKER)
    return fe, fx


def close(a: Any, b: Any, tol: float = TOL) -> bool:
    a, b = num(a), num(b)
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol * max(1.0, abs(b))


# --------------------------------------------------------------------------
def parity_combo(stored: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Re-derive qty / entry type for stored combo trades (running equity, as
    build_matrix_row_postprocess -> enrich_trades(running_equity=True))."""
    out, issues = [], []
    closed: list[tuple[datetime | None, float]] = []
    for i, t in enumerate(stored):
        entry_ts = _parse_utc(t.get("signal_time"))
        realized = sum(p for ts, p in closed if entry_ts is None or ts is None or ts <= entry_ts)
        equity = max(1e-6, PRINCIPAL + realized)
        tg = t.get("targets") or [None, None, None]
        base = {"direction": t.get("direction") or "LONG", "signal_time": t.get("signal_time"),
                "entry_time": t.get("entry_time"), "entry_price": t["entry"], "stop_price": t["stop"],
                "tp1": tg[0], "tp2": tg[1], "tp3": tg[2], "exit_time": t.get("exit_time"),
                "exit_price": t.get("exit_price"), "outcome": t.get("outcome"), "r_multiple": t.get("r_gross")}
        hit = None
        for et in ("MARKET", "LIMIT_RETEST"):
            e = enrich_trade_execution({**base, "entry_type": et}, risk_usd=RISK, taker_fee=TAKER,
                                       maker_fee=MAKER, leverage=LEV, account_equity=equity)
            if close(e["fee_total_usd"], t["fee"]) and close(e["gross_pnl_usd"], t["gross"]) \
                    and close(e["net_pnl_usd"], t["net"]):
                hit = e
                break
        if hit is None:
            issues.append(f"trade {i} {t.get('signal_time')}: no entry type reproduces stored gross/fee/net")
            hit = e
        out.append(hit)
        closed.append((_parse_utc(t.get("exit_time")), float(hit["net_pnl_usd"])))
    return out, issues


def parity_mc(stored: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    out, issues = [], []
    for i, t in enumerate(stored):
        e = enrich_trade_execution(t, risk_usd=RISK, taker_fee=TAKER, maker_fee=MAKER,
                                   leverage=LEV, account_equity=PRINCIPAL)
        for k in ("qty", "gross_pnl_usd", "fee_total_usd", "net_pnl_usd"):
            if not close(e[k], t.get(k)):
                issues.append(f"trade {i} {t.get('signal_time')}: {k} {e[k]} vs stored {t.get(k)}")
        out.append(e)
    return out, issues


def build_trades(stored: list[dict[str, Any]], enr: list[dict[str, Any]], family: str,
                 slip: bool) -> list[dict[str, Any]]:
    res = []
    for t, e in zip(stored, enr):
        qty = float(e["qty"])
        entry = float(e["entry_price"])
        exit_px = float(e["exit_price"])
        etype = str(e["entry_type"])
        opt_net = float(t["net"] if family == "combo" else t["net_pnl_usd"])
        d = dict(t)
        if family == "combo":
            # uniform aliases + recovered sizing (stored combo lists carry no qty)
            d.update(entry_price=entry, stop_price=float(e["stop_price"]), tp1=e.get("tp1"),
                     tp2=e.get("tp2"), tp3=e.get("tp3"), qty=qty, entry_type=etype)
        if slip:
            ef, xf = slip_fill(entry, exit_px)
            fe, fx = leg_fees(qty, ef, xf, etype)
            gross = qty * (xf - ef)
        else:
            ef, xf = entry, exit_px
            fe, fx = float(e["fee_entry_usd"]), float(e["fee_exit_usd"])
            gross = float(e["gross_pnl_usd"])
        fee = fe + fx
        net = gross + fee
        d.update(entry_fill=ef, exit_fill=xf, gross_pnl_usd=gross, fee_total_usd=fee,
                 fee_entry_fill_usd=fe, fee_exit_fill_usd=fx, net_pnl_usd=net,
                 optimistic_net_pnl_usd=opt_net,
                 r_net_fill=(net / float(e["risk_usd"])) if float(e["risk_usd"] or 0) > 0 else None)
        res.append(d)
    return res


def write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    t0 = time.perf_counter()
    ranked = json.loads((SR / "ranked.json").read_text(encoding="utf-8"))["cells_ranked"]
    ref: dict[tuple, dict[str, Any]] = {}
    for style_rows in ranked.values():
        for r in style_rows:
            if r.get("direction") == "LONG" and r.get("timeframe") == "1h":
                ref[(r["strategy_id"], r["symbol"], "IS")] = r
    for r in json.loads((SR / "combo" / "oos" / "rows_oos.json").read_text(encoding="utf-8")):
        if r.get("strategy_id") == "COMBO_02" and r.get("direction") == "LONG" and r.get("timeframe") == "1h":
            ref[(r["strategy_id"], r["symbol"], r["window_id"])] = r

    cells = []
    for s in COMBO_BOOKS:
        for sym in SYMBOLS:
            cells.append((s, sym, "IS", "combo", SR / "combo" / "trades" / f"{s}__LONG__{sym}__1h.json"))
    for sym in SYMBOLS:
        cells.append((MC_BOOK, sym, "IS", "multicap", SR / "multicap" / "trades" / f"{MC_BOOK}__LONG__{sym}__1h.json"))
    for w in ("OOS1", "OOS2"):
        for sym in SYMBOLS:
            cells.append(("COMBO_02", sym, w, "combo",
                          SR / "combo" / "oos" / "trades" / f"COMBO_02__LONG__{sym}__1h__{w}.json"))

    summary = {"generated_utc": datetime.now(timezone.utc).isoformat(), "slippage_rate": SLIP,
               "tick_applied": False, "tick_note": "CANDLE12_V2 tick_size default 0.0, never set by callers",
               "principal_usd": PRINCIPAL, "risk_usd": RISK, "leverage": LEV,
               "taker_fee": TAKER, "maker_fee": MAKER, "cells": []}
    for s, sym, w, fam, path in cells:
        stored = json.loads(path.read_text(encoding="utf-8"))
        enr, issues = (parity_combo if fam == "combo" else parity_mc)(stored)
        opt = build_trades(stored, enr, fam, slip=False)
        slp = build_trades(stored, enr, fam, slip=True)
        mo, ms = metrics(opt, fam), metrics(slp, fam)
        r = ref.get((s, sym, w))
        cell_issues = list(issues)
        if r is None:
            cell_issues.append("no reference row")
        else:
            for k in ("trades_closed", "net_pnl_usd", "fees_usd", "gross_pnl_usd", "profit_factor",
                      "max_drawdown_usd", "wins", "losses"):
                if not close(mo[k], r.get(k)):
                    cell_issues.append(f"{k}: replay {mo[k]} vs rank {r.get(k)}")
        po, fo = judge(mo)
        ps, fs = judge(ms)
        ref_pass = None
        if r is not None:
            ref_pass = r.get("passes") if "passes" in r else judge(r)[0]
        model_opt = (r or {}).get("execution_model") or ("LEGACY_SIGNAL_CLOSE" if fam == "combo" else "MULTICAP_SIGNAL_CLOSE")
        if fam == "multicap":
            model_opt = "MULTICAP_SIGNAL_CLOSE"
        name = f"{s}__LONG__{sym}__1h__{w}.json"
        head = {"strategy": s, "symbol": sym, "direction": "LONG", "timeframe": "1h", "window": w,
                "source_trades_file": str(path.relative_to(SR)).replace("\\", "/")}
        note = ("multicap: every stored field is kept as stored (optimistic); only entry_fill, exit_fill, "
                "gross_pnl_usd, fee_total_usd, fee_entry_fill_usd, fee_exit_fill_usd, net_pnl_usd, r_net_fill "
                "are under this file's fill model" if fam == "multicap" else
                "combo: stored fields kept (entry/stop/targets/gross/fee/net are the optimistic values); "
                "entry_price/stop_price/tp1-3/qty/entry_type added (qty re-derived, parity-checked); "
                "entry_fill..net_pnl_usd are under this file's fill model")
        write(OUT / "opt" / name, {**head, "fill_model": model_opt, "tick_applied": False, "note": note,
                                   "trades": opt})
        write(OUT / "slip" / name, {**head, "fill_model": "SLIP_0002", "tick_applied": False, "note": note,
                                    "trades": slp})

        def best_removed(tr: list[dict[str, Any]]) -> float:
            if not tr:
                return 0.0
            nets = [float(x["net_pnl_usd"]) for x in tr]
            return sum(nets) - max(nets)

        summary["cells"].append({
            "strategy": s, "symbol": sym, "window": w, "family": fam,
            "parity_ok": not cell_issues, "parity_issues": cell_issues[:20],
            "rank_ref": None if r is None else {k: r.get(k) for k in (
                "trades_closed", "net_pnl_usd", "fees_usd", "gross_pnl_usd", "profit_factor",
                "max_drawdown_usd")} | {"passes": ref_pass},
            "opt": {**mo, "passes": po, "fail_reasons": fo, "net_best_trade_removed": best_removed(opt)},
            "slip": {**ms, "passes": ps, "fail_reasons": fs, "net_best_trade_removed": best_removed(slp)},
        })
    summary["runtime_s"] = round(time.perf_counter() - t0, 2)
    write(OUT / "slip" / "_summary.json", summary)
    bad = [c for c in summary["cells"] if not c["parity_ok"]]
    print(f"cells={len(summary['cells'])} parity_ok={len(summary['cells']) - len(bad)} "
          f"runtime={summary['runtime_s']}s")
    for c in bad:
        print("MISMATCH", c["strategy"], c["symbol"], c["window"], c["parity_issues"][:5])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
