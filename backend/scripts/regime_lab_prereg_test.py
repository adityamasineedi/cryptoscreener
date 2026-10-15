"""Pre-registered test: plain Donchian long-only on data this strategy has not been run on.

The spec and the pass/fail rules below were fixed on 2026-10-15, before any run on these
datasets. Do not edit them after the first run; the script refuses to run twice and records
its own SHA-256 so any later edit is visible.

Datasets
  A  11 coins never used in Regime Lab runs, 2023-06-01 -> 2026-07-15 (holdout excluded)
  B  BTC, ETH, SOL in the 2022 bear market, 2022-01-01 -> 2023-06-01

Primary config (judged): Donchian 20-bar breakout long, 10-bar exit (the original plan).
Secondary (reported, not judged): 20-bar exit, which was found on in-sample data.

Usage: python scripts/regime_lab_prereg_test.py
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import sys
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.research.regime_lab.api import load_data  # noqa: E402
from app.research.regime_lab.config import DONCHIAN, RunConfig, Safeguards  # noqa: E402
from app.research.regime_lab.engine import run_backtest  # noqa: E402
from app.services.database import db_manager  # noqa: E402

OUT = ROOT / "reports" / "regime_lab_prereg"


def utc(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


DATASETS = {
    "A_new_coins": {
        "symbols": ["SOLUSDT", "BNBUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT",
                    "LTCUSDT", "NEARUSDT", "OPUSDT", "APTUSDT", "SUIUSDT"],
        "start": utc(2023, 6, 1),
        "end": utc(2026, 7, 15),
    },
    "B_bear_2022": {
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "start": utc(2022, 1, 1),
        "end": utc(2023, 6, 1),
    },
}

MAJORS = {"BTCUSDT", "ETHUSDT"}


def slippage_for(symbols: list[str]) -> dict[str, float]:
    return {s: 0.0005 if s in MAJORS else 0.0015 for s in symbols}


def primary_config(symbols: list[str]) -> RunConfig:
    base = RunConfig(
        tier="large",
        timeframe="4h",
        strategies=(DONCHIAN,),
        initial_equity=10_000.0,
        risk_pct=0.005,
        stop_atr_mult=1.75,
        fee_rate=0.0004,
        max_leverage=2.0,
        btc_gate=True,
        allow_short=False,
        safeguards=Safeguards(heat_cap=0.04),  # regime: threshold method, nothing else on
        symbol_slippage=slippage_for(symbols),
    )
    return replace(base, params=replace(base.params, donchian_entry=20, donchian_exit=10, donchian_shorts=False))


def secondary_config(symbols: list[str]) -> RunConfig:
    cfg = primary_config(symbols)
    return replace(cfg, params=replace(cfg.params, donchian_exit=20))


def judge_a(res: dict) -> list[tuple[str, bool, str]]:
    s = res["summary"]
    trades = sorted((t["net_pnl"] for t in res["trades"]), reverse=True)
    ex_top10 = sum(trades) - sum(x for x in trades[:10] if x > 0)
    years = {g["key"]: g["net_pnl"] for g in res["breakdowns"]["year"]}
    coins = res["breakdowns"]["symbol"]
    pos_coins = sum(1 for g in coins if g["net_pnl"] > 0)
    return [
        ("at least 150 trades", s["trades"] >= 150, str(s["trades"])),
        ("profit factor at least 1.3", (s["profit_factor"] or 0) >= 1.3, f"{s['profit_factor'] or 0:.2f}"),
        ("net P&L still positive without the 10 best trades", ex_top10 > 0, f"{ex_top10:.0f}"),
        ("2024 and 2025 both positive", years.get("2024", 0) > 0 and years.get("2025", 0) > 0,
         f"2024 {years.get('2024', 0):.0f}, 2025 {years.get('2025', 0):.0f}"),
        ("max drawdown at most 25%", s["max_drawdown_pct"] <= 25, f"{s['max_drawdown_pct']:.1f}%"),
        ("at least 60% of coins net positive", pos_coins >= 0.6 * len(coins), f"{pos_coins}/{len(coins)}"),
    ]


def judge_b(res: dict) -> list[tuple[str, bool, str]]:
    s = res["summary"]
    return [
        ("bear market: loss no worse than 5%", s["return_pct"] >= -5, f"{s['return_pct']:.1f}%"),
        ("bear market: max drawdown at most 20%", s["max_drawdown_pct"] <= 20, f"{s['max_drawdown_pct']:.1f}%"),
    ]


def summary_row(res: dict) -> dict:
    s = res["summary"]
    keys = ("trades", "net_pnl", "return_pct", "win_rate", "profit_factor", "avg_r", "max_drawdown_pct",
            "max_drawdown_usd", "max_losing_streak", "quick_stops", "pnl_ex_top5")
    return {k: (round(s[k], 3) if isinstance(s[k], float) else s[k]) for k in keys}


def export_trades(path: Path, trades: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "strategy", "side", "regime", "entry time", "exit time",
                    "entry price", "exit price", "P&L", "R", "bars held", "exit reason"])
        for t in reversed(trades):
            w.writerow([t["symbol"], t["strategy"], t["side"], t["regime"],
                        t["entry_time"][:16].replace("T", " "), t["exit_time"][:16].replace("T", " "),
                        f"{t['entry_price']:.4f}", f"{t['exit_price']:.4f}", f"{t['net_pnl']:.2f}",
                        f"{t['r_multiple']:.2f}", t["bars_held"], t["exit_reason"]])


async def main() -> None:
    result_file = OUT / "results.json"
    if result_file.exists():
        raise SystemExit(f"already run: {result_file} exists. The pre-registered test runs once.")
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    await db_manager.connect(get_settings(), ensure_schema=False)
    out: dict = {"script_sha256": script_hash, "run_at": datetime.now(timezone.utc).isoformat(), "datasets": {}}
    try:
        for name, ds in DATASETS.items():
            entry: dict = {"symbols": ds["symbols"], "start": ds["start"].isoformat(), "end": ds["end"].isoformat()}
            for label, make in (("primary", primary_config), ("secondary_exit20", secondary_config)):
                cfg = make(ds["symbols"])
                datas, btc, coverage = await load_data(ds["symbols"], cfg, ds["start"], ds["end"])
                res = await asyncio.to_thread(run_backtest, datas, cfg, ds["start"], ds["end"], btc=btc)
                checks = judge_a(res) if name.startswith("A") else judge_b(res)
                entry[label] = {
                    "summary": summary_row(res),
                    "checks": [{"rule": r, "passed": ok, "value": v} for r, ok, v in checks],
                    "passed": all(ok for _, ok, _ in checks),
                    "by_year": res["breakdowns"]["year"],
                    "by_symbol": res["breakdowns"]["symbol"],
                    "by_exit": res["breakdowns"]["exit_reason"],
                    "monte_carlo": res["monte_carlo"],
                    "coverage": coverage,
                    "config": {"params": asdict(cfg.params), "safeguards": asdict(cfg.safeguards),
                               "slippage": cfg.symbol_slippage, "stop_atr_mult": cfg.stop_atr_mult,
                               "risk_pct": cfg.risk_pct, "fee_rate": cfg.fee_rate},
                }
                export_trades(OUT / f"{name}_{label}_trades.csv", res["trades"])
            out["datasets"][name] = entry
    finally:
        await db_manager.close()
    out["verdict"] = {
        "primary_passed_all": all(out["datasets"][n]["primary"]["passed"] for n in DATASETS),
    }
    result_file.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    for name, entry in out["datasets"].items():
        for label in ("primary", "secondary_exit20"):
            e = entry[label]
            print(f"\n{name} / {label}: {'PASS' if e['passed'] else 'FAIL'}  {e['summary']}")
            for c in e["checks"]:
                print(f"   [{'x' if c['passed'] else ' '}] {c['rule']}: {c['value']}")
    print(f"\nPrimary passed everything: {out['verdict']['primary_passed_all']}")


if __name__ == "__main__":
    asyncio.run(main())
