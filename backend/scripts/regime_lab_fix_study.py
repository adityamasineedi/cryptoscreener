"""Regime Lab fix study: each finding's fix as a flag, compared with the baseline.

Windows (UTC):
  tune      2025-06-25 -> 2026-02-25   in-sample, ~8 months
  wf        2023-06-01 -> 2026-02-25   rolling walk-forward, fit 6 months / test 2 months
  test      2026-02-25 -> 2026-07-15   run once, baseline and the final config only
Nothing reads bars from 2026-07-15 on (the reserved Regime Lab holdout).

Stage 1 (default) runs every variant on tune and wf, then picks the final config
with a rule fixed before any result was seen:
  a change is kept only if it beats the baseline on BOTH tune and wf net P&L;
  within a group of alternatives only the best qualifying one is kept;
  the stop multiple is the one whose wf result and both neighbours' are best on average.
Stage 2 (--run-test) runs the baseline and that final config once on test.

Usage:
  python scripts/regime_lab_fix_study.py            # stage 1
  python scripts/regime_lab_fix_study.py --run-test # stage 2, reads stage 1 output
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.research.regime_lab.config import (  # noqa: E402
    BOLLINGER,
    DONCHIAN,
    RECOMMENDED_SAFEGUARDS,
    RSI2,
    RunConfig,
)
from app.research.regime_lab.api import load_data  # noqa: E402
from app.research.regime_lab.engine import run_backtest  # noqa: E402
from app.research.regime_lab.walkforward import run_walk_forward  # noqa: E402
from app.services.database import db_manager  # noqa: E402

OUT = ROOT / "reports" / "regime_lab_fix"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "DOGEUSDT", "ARBUSDT"]
SLIPPAGE = {"BTCUSDT": 0.0005, "ETHUSDT": 0.0005, "XRPUSDT": 0.0015, "DOGEUSDT": 0.0015, "ARBUSDT": 0.0015}


def utc(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


TUNE = (utc(2025, 6, 25), utc(2026, 2, 25))
WF = (utc(2023, 6, 1), utc(2026, 2, 25))
TEST = (utc(2026, 2, 25), utc(2026, 7, 15))
WF_TRAIN_MONTHS = 6
WF_TEST_MONTHS = 2
STOPS = [1.5, 2.0, 2.5, 3.0]

# The run the findings came from: Recommended safeguards, all three strategies,
# tier-default stop (1.75 x ATR) and 0.5% risk, with the per-symbol costs above.
BASELINE = RunConfig(
    tier="large",
    timeframe="4h",
    strategies=(DONCHIAN, RSI2, BOLLINGER),
    initial_equity=10_000.0,
    risk_pct=0.005,
    fee_rate=0.0004,
    max_leverage=2.0,
    btc_gate=True,
    safeguards=RECOMMENDED_SAFEGUARDS,
    symbol_slippage=SLIPPAGE,
)

# name -> (group, description, overrides). Overrides keys: run, params, safeguards, symbols, timeframes.
VARIANTS: dict[str, tuple[str, str, dict[str, Any]]] = {
    "baseline": ("", "Recommended safeguards, all strategies", {}),
    "no_rsi2": ("rsi", "RSI(2) off", {"run": {"strategies": (DONCHIAN, BOLLINGER)}}),
    "rsi2_exit_rsi70": ("rsi", "RSI(2) exits at RSI(2) > 70 / < 30", {"params": {"rsi_exit_mode": "rsi"}}),
    "rsi2_exit_rsi70_hold20": (
        "rsi", "RSI(2) exits at RSI 70/30, time stop 20 bars",
        {"params": {"rsi_exit_mode": "rsi", "rsi_max_bars": 20}},
    ),
    "no_transition": ("transition", "Nothing traded in TRANSITION", {"safeguards": {"transition_size": 0.0}}),
    "donchian_long_only": ("shorts", "Donchian shorts off", {"params": {"donchian_shorts": False}}),
    "donchian_short_filtered": (
        "shorts", "Donchian shorts need 55-bar low, ADX > 25, below 200 SMA",
        {"params": {"donchian_short_filter": True}},
    ),
    **{
        f"stop_{s}": ("stop", f"Stop {s} x ATR", {"run": {"stop_atr_mult": s}})
        for s in STOPS
    },
    "retest_level": ("entry", "Donchian waits for a retest of the breakout level", {"params": {"donchian_entry_mode": "retest_level"}}),
    "retest_mid": ("entry", "Donchian waits for a retest of the breakout bar midpoint", {"params": {"donchian_entry_mode": "retest_mid"}}),
    "exclude_btc": ("btc", "BTC not traded (still used for the gate)", {"symbols": [s for s in SYMBOLS if s != "BTCUSDT"]}),
    "btc_1d": ("btc", "BTC traded on 1d bars", {"timeframes": {"BTCUSDT": "1d"}}),
    "daily_loss_realized": (
        "daily_loss", "Daily loss limit counts realized + open losses only",
        {"safeguards": {"daily_loss_basis": "realized"}},
    ),
}


def build(over: dict[str, Any]) -> tuple[RunConfig, list[str]]:
    cfg = replace(BASELINE, **over.get("run", {}))
    if over.get("params"):
        cfg = replace(cfg, params=replace(cfg.params, **over["params"]))
    if over.get("safeguards"):
        cfg = replace(cfg, safeguards=replace(cfg.safeguards, **over["safeguards"]))
    if over.get("timeframes"):
        cfg = replace(cfg, symbol_timeframes=dict(over["timeframes"]))
    return cfg, list(over.get("symbols", SYMBOLS))


def merge(*overs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for o in overs:
        for k, v in o.items():
            if isinstance(v, dict):
                out[k] = {**out.get(k, {}), **v}
            else:
                out[k] = v
    return out


def row(name: str, period: str, res: dict[str, Any]) -> dict[str, Any]:
    s = res["summary"]
    out = {
        "variant": name,
        "period": period,
        "trades": s["trades"],
        "net_pnl": round(s["net_pnl"], 1),
        "win_rate": round((s["win_rate"] or 0) * 100, 1),
        "profit_factor": None if s["profit_factor"] is None else round(s["profit_factor"], 2),
        "avg_r": None if s["avg_r"] is None else round(s["avg_r"], 3),
        "max_dd_usd": round(s["max_drawdown_usd"], 1),
        "max_dd_pct": round(s["max_drawdown_pct"], 1),
        "max_losing_streak": s["max_losing_streak"],
        "quick_stops": s["quick_stops"],
        "pnl_ex_top5": round(s["pnl_ex_top5"], 1),
    }
    if "folds" in res:
        out["positive_folds"] = f"{res['positive_folds']}/{len(res['folds'])}"
    return out


def symbol_diagnostics(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by = defaultdict(list)
    for t in trades:
        by[t["symbol"]].append(t)
    out = []
    for sym, ts in sorted(by.items()):
        nets = [t["net_pnl"] for t in ts]
        gains = sum(x for x in nets if x > 0)
        pains = -sum(x for x in nets if x <= 0)
        out.append(
            {
                "symbol": sym,
                "trades": len(ts),
                "net_pnl": round(sum(nets), 1),
                "win_rate": round(sum(1 for x in nets if x > 0) / len(ts) * 100, 1),
                "avg_r": round(sum(t["r_multiple"] for t in ts) / len(ts), 3),
                "profit_factor": round(gains / pains, 2) if pains else None,
                "quick_stops": sum(1 for t in ts if t["exit_reason"] == "stop" and t["bars_held"] <= 2),
                "entries": dict(Counter(f"{t['strategy']} {t['side'].lower()}" for t in ts)),
                "exits": dict(Counter(t["exit_reason"] for t in ts)),
            }
        )
    return out


def export_trades(path: Path, trades: list[dict[str, Any]]) -> None:
    """Same columns as the Regime Lab trade table, newest first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "strategy", "side", "regime", "entry time", "exit time",
                    "entry price", "exit price", "P&L", "R", "bars held", "exit reason"])
        for t in reversed(trades):
            w.writerow([
                t["symbol"], t["strategy"], t["side"], t["regime"],
                t["entry_time"][:16].replace("T", " "), t["exit_time"][:16].replace("T", " "),
                f"{t['entry_price']:.4f}", f"{t['exit_price']:.4f}", f"{t['net_pnl']:.2f}",
                f"{t['r_multiple']:.2f}", t["bars_held"], t["exit_reason"],
            ])


async def run_variant(name: str, over: dict[str, Any], *, window: tuple[datetime, datetime], wf: bool) -> dict[str, Any]:
    cfg, symbols = build(over)
    load_start = WF[0] if wf else window[0]
    datas, btc, _ = await load_data(symbols, cfg, load_start, window[1])
    out: dict[str, Any] = {}
    res = await asyncio.to_thread(run_backtest, datas, cfg, window[0], window[1], btc=btc, with_monte_carlo=False)
    out["window"] = res
    if wf:
        out["wf"] = await asyncio.to_thread(
            run_walk_forward, datas, cfg, WF[0], WF[1], btc=btc,
            train_months=WF_TRAIN_MONTHS, test_months=WF_TEST_MONTHS,
        )
    return out


def choose_final(results: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    base_tune = results["baseline"]["tune"]["net_pnl"]
    base_wf = results["baseline"]["wf"]["net_pnl"]
    notes: list[str] = []
    chosen: list[str] = []
    groups: dict[str, list[str]] = defaultdict(list)
    for name, (group, _, _) in VARIANTS.items():
        if group and group != "stop":
            groups[group].append(name)
    for group, names in groups.items():
        ok = [
            n for n in names
            if results[n]["tune"]["net_pnl"] > base_tune and results[n]["wf"]["net_pnl"] > base_wf
        ]
        if group == "rsi" and "no_rsi2" in ok:
            ok = ["no_rsi2"]  # exit tweaks are moot once RSI(2) is off
        if ok:
            best = max(ok, key=lambda n: results[n]["wf"]["net_pnl"])
            chosen.append(best)
            notes.append(f"{group}: kept {best} (beat baseline on tune and walk-forward)")
        else:
            notes.append(f"{group}: kept baseline behaviour (no variant beat it on both)")
    stop_wf = [results[f"stop_{s}"]["wf"]["net_pnl"] for s in STOPS]
    plateau = []
    for k in range(len(STOPS)):
        vals = stop_wf[max(0, k - 1): k + 2]
        plateau.append(sum(vals) / len(vals))
    k_best = max(range(len(STOPS)), key=lambda k: plateau[k])
    stop_name = f"stop_{STOPS[k_best]}"
    zone = ", ".join(f"{s}={v:.0f}" for s, v in zip(STOPS, plateau))
    if results[stop_name]["tune"]["net_pnl"] > base_tune and results[stop_name]["wf"]["net_pnl"] > base_wf:
        chosen.append(stop_name)
        notes.append(f"stop: {STOPS[k_best]} x ATR (walk-forward net, neighbours averaged: {zone})")
    else:
        notes.append(f"stop: kept 1.75 x ATR; best zone {STOPS[k_best]} did not beat baseline on both ({zone})")
    over = merge(*(VARIANTS[n][2] for n in chosen))
    return {"variants": chosen, "overrides": over}, notes


def fmt_table(rows: list[dict[str, Any]]) -> str:
    cols = ["variant", "period", "trades", "net_pnl", "win_rate", "profit_factor", "avg_r",
            "max_dd_usd", "max_losing_streak", "quick_stops", "pnl_ex_top5", "positive_folds"]
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    return head + "\n".join("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows) + "\n"


def jsonable(obj: Any) -> Any:
    if isinstance(obj, tuple):
        return list(obj)
    return obj


async def stage1() -> None:
    results: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    t0 = time.time()
    for name, (_, desc, over) in VARIANTS.items():
        r = await run_variant(name, over, window=TUNE, wf=True)
        results[name] = {"tune": row(name, "tune", r["window"]), "wf": row(name, "wf_oos", r["wf"])}
        rows += [results[name]["tune"], results[name]["wf"]]
        if name == "baseline":
            results[name]["tune_breakdowns"] = r["window"]["breakdowns"]
            results[name]["tune_symbols"] = symbol_diagnostics(r["window"]["trades"])
        print(f"[{time.time() - t0:6.0f}s] {name}: tune {results[name]['tune']['net_pnl']}, wf {results[name]['wf']['net_pnl']}", flush=True)

    final, notes = choose_final(results)
    r = await run_variant("final", final["overrides"], window=TUNE, wf=True)
    results["final"] = {"tune": row("final", "tune", r["window"]), "wf": row("final", "wf_oos", r["wf"])}
    rows += [results["final"]["tune"], results["final"]["wf"]]
    results["final"]["tune_breakdowns"] = r["window"]["breakdowns"]
    results["final"]["tune_symbols"] = symbol_diagnostics(r["window"]["trades"])
    export_trades(OUT / "final_tune_trades.csv", r["window"]["trades"])
    export_trades(OUT / "final_wf_oos_trades.csv", r["wf"]["trades"])

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stage1.json").write_text(
        json.dumps({"results": results, "final": final, "notes": notes, "rows": rows}, indent=2, default=jsonable),
        encoding="utf-8",
    )
    (OUT / "stage1_table.md").write_text(fmt_table(rows), encoding="utf-8")
    print("\n".join(notes))
    print(f"final: {final['variants']}")


async def stage2() -> None:
    data = json.loads((OUT / "stage1.json").read_text(encoding="utf-8"))
    if (OUT / "stage2.json").exists():
        raise SystemExit("test window already used; stage2.json exists")
    rows = []
    out: dict[str, Any] = {}
    for name, over in (("baseline", {}), ("final", data["final"]["overrides"])):
        r = await run_variant(name, over, window=TEST, wf=False)
        res = r["window"]
        rows.append(row(name, "test", res))
        out[name] = {
            "row": rows[-1],
            "breakdowns": res["breakdowns"],
            "symbols": symbol_diagnostics(res["trades"]),
        }
        export_trades(OUT / f"{name}_test_trades.csv", res["trades"])
    out["run_at"] = datetime.now(timezone.utc).isoformat()
    (OUT / "stage2.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    (OUT / "stage2_table.md").write_text(fmt_table(rows), encoding="utf-8")
    print(fmt_table(rows))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-test", action="store_true")
    args = ap.parse_args()
    await db_manager.connect(get_settings(), ensure_schema=False)
    try:
        await (stage2() if args.run_test else stage1())
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
