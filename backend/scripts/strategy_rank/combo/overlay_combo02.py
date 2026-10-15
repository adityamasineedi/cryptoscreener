"""Risk-controlled overlay (app.research.combo02_v1_1_risk_controlled) applied to the
COMBO_02 LONG 1h trade lists already written by run_combo_rank.py.

Pure replay: reads trade JSON files, calls replay_candidates_with_risk_controls,
writes JSON under overlay/. No database, no engine walk, nothing under backend/app
or backend/reports is written.

Basis (same for baseline and overlay): each symbol's COMBO_02 LONG trade list is the
standalone cell book (1000 USD account per symbol, 20 USD risk, 2x leverage, running
equity, fees as in the cell). The overlay only accepts or rejects entries; it does not
resize, so an accepted trade keeps the USD result it has in the baseline list.
Portfolio rows add the per-symbol trades together and order them by exit time.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
from pathlib import Path

BACKEND = Path(r"D:\cryptoscreener\backend")
OUT = BACKEND / "scripts" / "strategy_rank" / "combo"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(OUT))

import run_combo_rank as rc  # noqa: E402
import app.research.combo02_v1_1_risk_controlled as ov  # noqa: E402

STUDY_RISK_PCT = rc.RISK_USD / rc.PRINCIPAL_USD  # 0.02
WINDOWS = {
    "IS": ("2026-02-14T00:00:00Z", "2026-10-12T00:00:00Z"),
    "OOS1": ("2025-06-19T00:00:00Z", "2026-02-14T00:00:00Z"),
    "OOS2": ("2024-10-23T00:00:00Z", "2025-06-19T00:00:00Z"),
}
UNIVERSES = {"TRIO": list(ov.SYMBOLS), "TEN": list(rc.SYMBOLS)}
KEYS = rc.ROW_KEYS + ["window_id", "basis"]


def cell_paths(sym: str, wid: str) -> tuple[Path, Path]:
    if wid == "IS":
        n = f"COMBO_02__LONG__{sym}__1h.json"
        return OUT / "cells" / n, OUT / "trades" / n
    n = f"COMBO_02__LONG__{sym}__1h__{wid}.json"
    return OUT / "oos" / "cells" / n, OUT / "oos" / "trades" / n


def to_engine_keys(t: dict, sym: str) -> dict:
    """Trade-file record -> the keys metrics() and the overlay read."""
    return {**t, "symbol": sym, "r_multiple": t.get("r_gross"), "net_pnl_usd": t.get("net"),
            "gross_pnl_usd": t.get("gross"), "fee_total_usd": t.get("fee"),
            "risk_percent": STUDY_RISK_PCT}


def out_trade(t: dict) -> dict:
    keep = ("symbol", "direction", "signal_time", "entry", "stop", "targets", "exit_time", "exit_price",
            "outcome", "gross", "fee", "net", "r_gross", "r_net", "holding_bars", "holding_hours",
            "risk_percent")
    return {k: t.get(k) for k in keep}


def make_row(strategy: str, symbol: str, wid: str, basis: str, universe: str, trades: list[dict],
             cell_row: dict | None, notes: list[str], trades_file: str) -> dict:
    row = {k: None for k in KEYS}
    row.update(family="combo", engine=f"combination_engine+risk_overlay:{universe}", strategy_id=strategy,
               direction="LONG", symbol=symbol, timeframe="1h", status="OK", reason="",
               window_start_utc=WINDOWS[wid][0], window_end_utc=WINDOWS[wid][1], window_id=wid, basis=basis,
               principal_usd=rc.PRINCIPAL_USD, risk_usd=rc.RISK_USD, leverage=rc.LEVERAGE,
               taker_fee_pct=rc.TAKER_FEE_PCT, maker_fee_pct=rc.MAKER_FEE_PCT, trades_file=trades_file,
               execution_model="LEGACY_SIGNAL_CLOSE")
    ordered = sorted(trades, key=lambda t: (str(t.get("exit_time") or ""), str(t.get("symbol") or "")))
    row.update(rc.metrics(ordered, "1h"))
    if cell_row:
        for k in ("eval_bars", "warmup_bars", "missing_bars", "execution_model"):
            row[k] = cell_row.get(k)
        row["open_trades_at_end"] = cell_row.get("open_trades_at_end") if basis == "baseline" else None
    row["notes"] = "; ".join(notes)
    return row


def main() -> int:
    rows: list[dict] = []
    summary: dict = {}
    (OUT / "overlay" / "trades").mkdir(parents=True, exist_ok=True)
    for wid in WINDOWS:
        base: dict[str, list[dict]] = {}
        cells: dict[str, dict] = {}
        for sym in rc.SYMBOLS:
            cp, tp = cell_paths(sym, wid)
            c = json.loads(cp.read_text(encoding="utf-8"))
            if c["row"]["status"] != "OK":
                raise SystemExit(f"baseline cell not OK: {cp}")
            cells[sym] = c["row"]
            base[sym] = [to_engine_keys(t, sym) for t in json.loads(tp.read_text(encoding="utf-8"))]
        for uni, syms in UNIVERSES.items():
            cands = [t for s in syms for t in base[s]]
            replay = ov.replay_candidates_with_risk_controls(cands)
            accepted = list(replay["accepted_trades"])
            rejected = list(replay["rejected"])
            # every candidate is either accepted or rejected exactly once
            if len(accepted) + len(rejected) != len(cands):
                raise SystemExit(f"accept+reject != candidates in {wid} {uni}")
            rej_by_sym: dict[str, dict[str, int]] = {}
            for r in rejected:
                d = rej_by_sym.setdefault(r["symbol"], {})
                d[r["reason"]] = d.get(r["reason"], 0) + 1
            common = [
                f"universe {uni} = {','.join(s[:-4] for s in syms)}"
                + (" (the module's own SYMBOLS)" if uni == "TRIO" else
                   " (extension: the module is written for BTC/ETH/SOL; the same caps applied across ten coins)"),
                "account basis: per-symbol standalone COMBO_02 LONG cell books (1000 USD each, 20 USD risk, 2x, "
                "running equity); USD per trade is not re-sized by the overlay",
                f"overlay risk_percent passed per candidate = {STUDY_RISK_PCT} (study 20/1000); module defaults "
                f"are {dict(ov.RISK_PERCENT_BY_SYMBOL)}",
            ]
            rules = (f"overlay rules (entries only, never force-closes): max {ov.MAX_CONCURRENT_CLUSTER} open "
                     f"positions, open risk <= {ov.CLUSTER_HEAT_CAP:.0%} of equity, no entry for the rest of a UTC "
                     f"day once closed R that day <= -{ov.DAILY_LOSS_HALT_R}, {ov.CONSECUTIVE_LOSS_HALT} losses in a "
                     f"row on a symbol pause it {ov.CONSECUTIVE_LOSS_PAUSE.total_seconds() / 3600:.0f}h, symbol "
                     f"halted at {ov.SYMBOL_DD_HALT_R}R drawdown, whole book halted at {ov.STRATEGY_DD_HALT_R}R "
                     "drawdown (R = r_net of accepted closed trades)")
            pname = f"PORTFOLIO_{uni}"
            # portfolio rows
            for basis, trades, strat in (("baseline", cands, "COMBO_02"), ("overlay", accepted, ov.STRATEGY_ID)):
                fn = f"overlay/trades/{strat}__LONG__{pname}__1h__{wid}__{basis}.json"
                ordered = sorted(trades, key=lambda t: (str(t.get("exit_time") or ""), str(t.get("symbol") or "")))
                rc.atomic_write(OUT / fn, [out_trade(t) for t in ordered])
                notes = list(common)
                if basis == "overlay":
                    notes += [rules, f"candidates {len(cands)}, accepted {len(accepted)}, rejected {len(rejected)}: "
                              f"{replay['reject_counts']}"]
                else:
                    notes.append("baseline = all closed COMBO_02 LONG trades of the universe added together, "
                                 "no caps (positions on different symbols overlap freely)")
                notes.append("portfolio row: eval/warmup/missing bars are per symbol (see per-symbol rows); "
                             "return_pct = net / 1000 although the trades come from one 1000 USD book per symbol; "
                             "drawdown from trades ordered by exit time")
                row = make_row(strat, pname, wid, basis, uni, trades, None, notes, fn)
                if basis == "baseline":
                    row["open_trades_at_end"] = sum(int(cells[s].get("open_trades_at_end") or 0) for s in syms)
                rows.append(row)
                summary[f"{wid}|{uni}|{basis}"] = {k: row[k] for k in (
                    "trades_closed", "wins", "losses", "net_pnl_usd", "gross_pnl_usd", "fees_usd",
                    "profit_factor", "max_drawdown_usd", "max_drawdown_r", "avg_r_net")}
                if basis == "overlay":
                    summary[f"{wid}|{uni}|overlay"]["reject_counts"] = replay["reject_counts"]
            rc.atomic_write(OUT / "overlay" / f"rejects__{wid}__{uni}.json", rejected)
            # per-symbol rows (the symbol's part of the combined replay)
            for s in syms:
                solo = ov.replay_candidates_with_risk_controls(base[s])
                for basis, trades, strat in (
                    ("baseline", base[s], "COMBO_02"),
                    ("overlay", [t for t in accepted if t["symbol"] == s], ov.STRATEGY_ID),
                ):
                    fn = f"overlay/trades/{strat}__LONG__{s}__1h__{wid}__{uni}__{basis}.json"
                    rc.atomic_write(OUT / fn, [out_trade(t) for t in trades])
                    notes = list(common)
                    if basis == "overlay":
                        notes += [f"this symbol's accepted trades inside the {uni} portfolio replay; rejected here: "
                                  f"{rej_by_sym.get(s, {})}",
                                  f"solo replay of this symbol alone (as the module's runner also reports): "
                                  f"accepted {len(solo['accepted_trades'])} of {len(base[s])}, "
                                  f"rejects {solo['reject_counts']}"]
                    else:
                        notes.append("identical to the plain COMBO_02 LONG cell for this symbol and window")
                    rows.append(make_row(strat, s, wid, basis, uni, trades, cells[s], notes, fn))
    rc.atomic_write(OUT / "overlay" / "rows_overlay.json", [{k: r.get(k) for k in KEYS} for r in rows])
    rc.atomic_write(OUT / "overlay" / "_summary.json", {
        "module": "app.research.combo02_v1_1_risk_controlled", "function": "replay_candidates_with_risk_controls",
        "module_defaults": ov.profile_summary(), "study_risk_percent_used": STUDY_RISK_PCT,
        "portfolio": summary})
    for k, v in summary.items():
        print(k, {a: (round(b, 2) if isinstance(b, float) else b) for a, b in v.items()})
    print("rows", len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
