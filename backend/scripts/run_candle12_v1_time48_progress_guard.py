"""In-sample exit-overlay study for CANDLE12_V1_TIME48_PROGRESS_GUARD.

Reads the same BTCUSDT 1h window as scripts/run_candle12_v3_exec_fix.py.
Does not write orders, paper state, or any frozen CANDLE12 artifact.

Usage: python scripts/run_candle12_v1_time48_progress_guard.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "strategy_rank" / "multicap"))

from _db import load_candles  # noqa: E402
from app.research.candle12_v1_time48_progress_guard import (  # noqa: E402
    DISCLAIMER,
    STRATEGY_ID,
    OverlayConfig,
    apply_overlay,
    baseline_identity,
    classify_variant,
    config_hash,
    summarize,
    validate_fills,
)
from app.research.candle12_v2 import Candle12V2Config, run_candle12_v2  # noqa: E402
from app.research.candle12_v3 import check_oos_guard  # noqa: E402
from app.research.engine_backtest import run_engine_cell  # noqa: E402

UTC = timezone.utc
SYMBOL = "BTCUSDT"
START = datetime(2023, 1, 1, tzinfo=UTC)
END_EXCLUSIVE = datetime(2026, 10, 16, tzinfo=UTC)
WARMUP = 1000
RISK, EQUITY, LEVERAGE, TAKER = 20.0, 1000.0, 2.0, 0.0004
EXPECTED_TRADES = 95
EXPECTED_NET = 594.85
OUT_DIR = ROOT / "reports" / "candle12_v1_time48_progress_guard"

CONFIGS = (
    ("BASELINE", OverlayConfig(time_stop_48=False, progress_guard_1r=False)),
    ("TIME48_ONLY", OverlayConfig(time_stop_48=True, progress_guard_1r=False)),
    ("GUARD_ONLY", OverlayConfig(time_stop_48=False, progress_guard_1r=True)),
    ("TIME48_PLUS_GUARD", OverlayConfig(time_stop_48=True, progress_guard_1r=True)),
)


def _candles() -> tuple[list[dict[str, Any]], int]:
    raw = load_candles(SYMBOL, "1h", START - timedelta(hours=WARMUP + 600), END_EXCLUSIVE)
    check_oos_guard(raw)
    first = next(i for i, b in enumerate(raw) if b["time"] >= START)
    return raw[first - WARMUP :], WARMUP


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT.parent, text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _module_sha() -> str:
    path = ROOT / "app" / "research" / "candle12_v1_time48_progress_guard.py"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _md(report: dict[str, Any]) -> str:
    lines = [
        f"# {STRATEGY_ID}",
        "",
        DISCLAIMER,
        "",
        "The trade list is the frozen CANDLE12_V2 BTCUSDT 1h LONG book (shared lock).",
        "Overlays change only the exit of those trades. They do not add trades.",
        "",
        "## Gates",
        "",
    ]
    for key, value in report["gates"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Comparison", ""])
    headers = [
        "config",
        "trades",
        "net $",
        "net R",
        "win rate",
        "avg win R",
        "avg loss R",
        "PF",
        "max DD $",
        "max DD R",
        "lose streak",
        "winners lost",
        "time exits",
        "guard armed",
        "guard exits",
        "label",
    ]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for name, block in report["configs"].items():
        s = block["summary"]
        lines.append(
            "| "
            + " | ".join(
                [
                    name,
                    str(s["trades"]),
                    f"{s['net_pnl_usd']:.2f}",
                    f"{s['net_r']:.2f}",
                    "—" if s["win_rate"] is None else f"{100 * s['win_rate']:.1f}%",
                    "—" if s["avg_win_r"] is None else f"{s['avg_win_r']:.3f}",
                    "—" if s["avg_loss_r"] is None else f"{s['avg_loss_r']:.3f}",
                    "—" if s["profit_factor"] is None else f"{s['profit_factor']:.3f}",
                    f"{s['max_drawdown_usd']:.2f}",
                    f"{s['max_drawdown_r']:.2f}",
                    str(s["longest_losing_streak"]),
                    "n/a" if s["winners_lost_vs_baseline"] is None else str(s["winners_lost_vs_baseline"]),
                    str(s["time_stop_exits"]),
                    str(s["progress_guard_activated"]),
                    str(s["progress_guard_exits"]),
                    block["classification"]["label"] if block["classification"] else "BASELINE",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Loss anatomy", ""])
    reason_names = [
        "stopped before any real progress",
        "setup failed early, stop hit late",
        "gave back open profit",
        "time-stop exit",
        "progress-guard exit",
    ]
    lines.append("| reason | " + " | ".join(report["configs"]) + " |")
    lines.append("|---|" + "|".join(["---"] * len(report["configs"])) + "|")
    for reason in reason_names:
        cells = [str(report["configs"][name]["summary"]["loss_reasons"].get(reason, 0)) for name in report["configs"]]
        lines.append("| " + reason + " | " + " | ".join(cells) + " |")
    lines.extend(["", "## Per year (fresh $1,000)", ""])
    for name, block in report["configs"].items():
        lines.append(f"### {name}")
        lines.append("")
        lines.append("| year | trades | net $ | end equity | max DD $ | net R | max DD R |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for year, row in block["summary"]["per_year"].items():
            lines.append(
                f"| {year} | {row['trades']} | {row['net_pnl_usd']:.2f} | {row['end_equity_usd']:.2f} | "
                f"{row['max_drawdown_usd']:.2f} | {row['net_r']:.2f} | {row['max_drawdown_r']:.2f} |"
            )
        lines.append("")
    lines.extend(["## Recommendation", "", report["recommendation"], "", "## What this is not", "", DISCLAIMER, ""])
    return "\n".join(lines)


def _recommend(report: dict[str, Any]) -> str:
    labels = {name: block["classification"]["label"] for name, block in report["configs"].items() if block["classification"]}
    promising = [name for name, label in labels.items() if label == "PROMISING"]
    if not promising:
        return (
            "No overlay is PROMISING on this in-sample window. Do not take a variant forward "
            "and do not retune 48 bars or 1R. The predeclared next test, if one is still wanted "
            "for the record, is a single untouched forward window of BTCUSDT 1h starting "
            "2026-10-16, same $20 risk, fees, slippage, and funding-off assumption, with "
            "TIME48_ONLY and GUARD_ONLY scored separately against the frozen entry list. "
            "That window was not used to choose these two rules."
        )
    joined = ", ".join(promising)
    return (
        f"{joined} met the predeclared in-sample rule. The next test is one forward window, "
        "BTCUSDT 1h from 2026-10-16, same sizing and costs, overlays unchanged, scored once. "
        "Do not refit the bar count or the 1R level on that window. This in-sample pass is "
        "not evidence of an edge."
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    candles, first = _candles()
    parent = run_candle12_v2(SYMBOL, "1h", candles, index_start=first, progress_every=0)
    if parent.get("status") != "OK":
        raise SystemExit(f"parent engine status {parent.get('status')}")
    v2_raw = parent["v2_config"]
    v2 = Candle12V2Config(
        trading_fee=v2_raw["trading_fee"],
        entry_slippage=v2_raw["entry_slippage"],
        exit_slippage=v2_raw["exit_slippage"],
    )
    longs = [
        t
        for t in parent["trades"]
        if t.get("direction") == "LONG" and t.get("outcome") not in (None, "OPEN") and t.get("exit_price") is not None
    ]
    cell = run_engine_cell(
        {
            "strategy_id": "CANDLE12_V2",
            "symbol": SYMBOL,
            "timeframe": "1h",
            "candles": candles,
            "index_start": first,
            "market_cap": None,
            "direction": "LONG",
            "risk_usd": RISK,
            "taker_fee": TAKER,
            "maker_fee": 0.0002,
            "leverage": LEVERAGE,
            "principal": EQUITY,
        }
    )
    engine_rows = cell["trades"]
    runs: dict[str, Any] = {}
    baseline_rows: list[dict[str, Any]] | None = None
    for name, cfg in CONFIGS:
        rows = apply_overlay(longs, candles, cfg, v2=v2)
        fill_errors = validate_fills(rows, candles)
        if fill_errors:
            raise SystemExit(f"{name} fill check failed: {fill_errors[:5]}")
        summary = summarize(rows, baseline=baseline_rows)
        runs[name] = {"config": cfg, "rows": rows, "summary": summary, "hash": config_hash(cfg)}
        if name == "BASELINE":
            baseline_rows = rows
    identity = baseline_identity(longs, runs["BASELINE"]["rows"])
    if not identity["ok"]:
        raise SystemExit(f"baseline replay diverged: {identity['mismatches'][:3]}")
    engine_net = round(sum(float(t["net_pnl_usd"]) for t in engine_rows), 2)
    replay_net = runs["BASELINE"]["summary"]["net_pnl_usd"]
    entry_sets = [tuple(int(t["entry_index"]) for t in runs[name]["rows"]) for name, _ in CONFIGS]
    same_universe = len(set(entry_sets)) == 1
    for name, block in runs.items():
        parent_exit = {int(t["entry_index"]): int(t["exit_index"]) for t in runs["BASELINE"]["rows"]}
        extended = [
            int(t["entry_index"])
            for t in block["rows"]
            if int(t["exit_index"]) > parent_exit[int(t["entry_index"])]
        ]
        if extended:
            raise SystemExit(f"{name} walked past the baseline exit on {extended[:5]}")
    base_summary = runs["BASELINE"]["summary"]
    configs_out: dict[str, Any] = {}
    for name, block in runs.items():
        classification = None if name == "BASELINE" else classify_variant(block["summary"], base_summary)
        configs_out[name] = {
            "config_hash": block["hash"],
            "config": block["config"].to_dict(),
            "summary": block["summary"],
            "classification": classification,
        }
    gates = {
        "parent_closed_longs": len(longs),
        "engine_adapter_trades": len(engine_rows),
        "replay_matches_parent_exits": identity["ok"],
        "same_95_entry_universe": same_universe and len(longs) == EXPECTED_TRADES,
        "baseline_net_usd": replay_net,
        "engine_adapter_net_usd": engine_net,
        "baseline_net_matches_published_594_85": abs(replay_net - EXPECTED_NET) <= 0.02,
        "engine_net_matches_replay": abs(engine_net - replay_net) <= 0.02,
        "fees": TAKER,
        "entry_slippage": v2.entry_slippage,
        "exit_slippage": v2.exit_slippage,
        "funding_enabled": False,
        "seed": None,
        "lookahead_extension": False,
    }
    if not gates["baseline_net_matches_published_594_85"] or not gates["same_95_entry_universe"]:
        raise SystemExit(f"baseline universe gate failed: {gates}")
    if not gates["engine_net_matches_replay"]:
        raise SystemExit(f"dollar identity vs engine adapter failed: {gates}")
    report = {
        "strategy_id": STRATEGY_ID,
        "disclaimer": DISCLAIMER,
        "git_commit": _git_commit(),
        "module_sha256": _module_sha(),
        "symbol": SYMBOL,
        "timeframe": "1h",
        "window": {
            "start": START.isoformat(),
            "end_exclusive": END_EXCLUSIVE.isoformat(),
            "first_bar": candles[first]["time"].isoformat(),
            "last_bar": candles[-1]["time"].isoformat(),
            "warmup_bars": WARMUP,
            "index_start": first,
        },
        "sizing": {"risk_usd": RISK, "equity": EQUITY, "leverage": LEVERAGE, "taker_fee": TAKER},
        "gates": gates,
        "configs": configs_out,
        "recommendation": "",
    }
    report["recommendation"] = _recommend(report)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    (out / "report.md").write_text(_md(report), encoding="utf-8")
    print(_md(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
