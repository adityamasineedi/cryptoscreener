#!/usr/bin/env python3
"""COMBO_03_TRANSITION multi-market validation — research only.

Compares COMBO_03_TRANSITION (+ variants B/C) on the COMBO_02_V2 universe/window/
ResearchConfig/SignalConfig/risk path. Does not modify COMBO_02 files.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import gc
import importlib.util
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "LINKUSDT",
    "DOGEUSDT",
    "SUIUSDT",
]

DATA_START = "2022-01-01"
DATA_END = "2026-10-06"

REGIMES = (
    "BULL_TREND",
    "BEAR_TREND",
    "HIGH_VOLATILITY_TREND",
    "CHOPPY",
    "RANGE",
    "HIGH_VOLATILITY_RANGE",
    "LOW_VOLATILITY_COMPRESSION",
    "TRANSITION",
    "UNKNOWN",
)

OUT_ROOT = Path("reports/combo03_transition")


def _load_v2_validation_module() -> Any:
    path = Path(__file__).resolve().parent / "run_combo02_v2_multi_market_validation.py"
    spec = importlib.util.spec_from_file_location("combo02_v2_validation", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _year(ts: str | None) -> str:
    if not ts:
        return "UNKNOWN"
    s = str(ts).replace("Z", "+00:00")
    try:
        return f"{datetime.fromisoformat(s).year:04d}"
    except ValueError:
        return str(ts)[:4] if len(str(ts)) >= 4 else "UNKNOWN"


def _streaks(rs: list[float]) -> tuple[int, int]:
    max_lose = max_win = cur_lose = cur_win = 0
    for x in rs:
        if x < 0:
            cur_lose += 1
            cur_win = 0
            max_lose = max(max_lose, cur_lose)
        elif x > 0:
            cur_win += 1
            cur_lose = 0
            max_win = max(max_win, cur_win)
        else:
            cur_lose = cur_win = 0
    return max_lose, max_win


def _bucket_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rs = [r["r_multiple"] for r in rows if r.get("r_multiple") is not None]
    wins = sum(1 for x in rs if x > 0)
    losses = sum(1 for x in rs if x < 0)
    gross = sum(x for x in rs if x > 0)
    loss_abs = abs(sum(x for x in rs if x < 0))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in rs:
        equity += x
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    holds = [_f(r.get("holding_bars")) for r in rows]
    holds_ok = [h for h in holds if h is not None]
    fees = [_f(r.get("fees")) for r in rows]
    fees_ok = [x for x in fees if x is not None]
    long_n = sum(1 for r in rows if r.get("direction") == "LONG")
    short_n = sum(1 for r in rows if r.get("direction") == "SHORT")
    max_lose, max_win = _streaks([float(x) for x in rs])
    best = max(rs) if rs else None
    worst = min(rs) if rs else None
    return {
        "trades": len(rows),
        "long": long_n,
        "short": short_n,
        "win_rate": (wins / len(rs)) if rs else None,
        "total_R": sum(rs) if rs else 0.0,
        "average_R": (sum(rs) / len(rs)) if rs else None,
        "expectancy": (sum(rs) / len(rs)) if rs else None,
        "profit_factor": (gross / loss_abs) if loss_abs > 0 else None,
        "maximum_drawdown_R": abs(max_dd) if rs else None,
        "average_holding_bars": (sum(holds_ok) / len(holds_ok)) if holds_ok else None,
        "total_fees": sum(fees_ok) if fees_ok else None,
        "best_trade": best,
        "worst_trade": worst,
        "maximum_losing_streak": max_lose,
        "maximum_winning_streak": max_win,
        "wins": wins,
        "losses": losses,
        "exit_reasons": _count_key(rows, "outcome"),
    }


def _count_key(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for r in rows:
        out[str(r.get(key) or "UNKNOWN")] += 1
    return dict(out)


def _trade_row(symbol: str, t: dict[str, Any]) -> dict[str, Any]:
    snap = t.get("condition_snapshot") or {}
    c03 = snap.get("combo03_diagnostics") if isinstance(snap.get("combo03_diagnostics"), dict) else {}
    v2 = snap.get("v2_diagnostics") if isinstance(snap.get("v2_diagnostics"), dict) else {}
    diag = c03 or v2
    return {
        "symbol": symbol,
        "setup_timestamp": diag.get("sweep_timestamp") or t.get("signal_time"),
        "entry_timestamp": t.get("signal_time") or t.get("entry_time"),
        "exit_time": t.get("exit_time"),
        "direction": t.get("direction"),
        "regime": snap.get("regime") or diag.get("regime"),
        "playbook": snap.get("playbook") or diag.get("playbook"),
        "sweep_direction": diag.get("sweep_direction"),
        "sweep_level": diag.get("sweep_level"),
        "sweep_timestamp": diag.get("sweep_timestamp"),
        "rejection_timestamp": diag.get("rejection_timestamp"),
        "structure_shift": diag.get("structure_shift"),
        "BOS_or_CHoCH": diag.get("BOS_or_CHoCH"),
        "structure_shift_timestamp": diag.get("structure_shift_timestamp"),
        "confirmation_15m": diag.get("confirmation_15m"),
        "confirmation_type": diag.get("confirmation_type") or snap.get("confirmation"),
        "confirmation_timestamp": diag.get("confirmation_timestamp"),
        "displacement": diag.get("displacement"),
        "event": snap.get("event") or diag.get("sweep_event"),
        "confirmation": snap.get("confirmation") or diag.get("confirmation_type"),
        "entry": t.get("entry_price"),
        "stop": t.get("stop_price"),
        "target": t.get("tp1"),
        "tp1": t.get("tp1"),
        "tp2": t.get("tp2"),
        "tp3": t.get("tp3"),
        "r_multiple": _f(t.get("r_multiple")),
        "holding_bars": t.get("holding_bars"),
        "outcome": t.get("outcome"),
        "fees": _f(t.get("fees") or t.get("total_fee")),
        "year": _year(t.get("signal_time") or t.get("entry_time")),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            flat = {
                k: (json.dumps(v, default=str) if isinstance(v, (dict, list)) else v)
                for k, v in r.items()
            }
            w.writerow(flat)


def _bind_combo03_to_v2_15m_patch() -> None:
    import app.research.combo02_v2.evaluate as v2_evaluate
    import app.research.combo03_transition.evaluate as c03_evaluate

    c03_evaluate.detect_swings = v2_evaluate.detect_swings  # type: ignore[attr-defined]


async def _run_symbol(symbol: str, combination_id: str, v2mod: Any) -> dict[str, Any]:
    from app.research.bos_combinations import get_combination
    from app.research.bos_strategy_comparison.htf import (
        build_htf_as_of_index_map_fully_closed,
    )
    from app.research.combination_backtest import run_combination_backtest
    from app.research.combo03_transition.context import Combo03Context
    from app.research.config import ResearchConfig
    from app.research.service import _load_research_candles
    from app.signals.config import SignalConfig

    t0 = time.perf_counter()
    print(f"  loading 1h {symbol} ({combination_id})...", flush=True)
    c1h, eval_start, meta1 = await _load_research_candles(
        symbol,
        "1h",
        start_date=DATA_START,
        end_date=DATA_END,
        use_research_cache=True,
        warmup_bars=100,
    )

    combo = get_combination(combination_id)
    assert combo is not None
    idx0 = eval_start if eval_start and eval_start > 0 else None

    reset_15m_v2 = getattr(v2mod._install_validation_15m_speed_patch, "reset_15m_cache", None)
    if callable(reset_15m_v2):
        reset_15m_v2()
    reset_ohlcv = getattr(
        v2mod._install_validation_ohlcv_cache_patch, "reset_ohlcv_registry", None
    )
    if callable(reset_ohlcv):
        reset_ohlcv()
    register = getattr(v2mod._install_validation_ohlcv_cache_patch, "register_series", None)
    if callable(register):
        register(c1h)

    print(f"  precomputing regimes (fast_structure) for {symbol}...", flush=True)
    t_reg = time.perf_counter()
    prebuilt = Combo03Context.build(
        symbol=symbol,
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_15m=None,
    )
    print(f"  regimes ready in {time.perf_counter() - t_reg:.1f}s", flush=True)

    needs_15m = combination_id != "COMBO_03_TRANSITION_B"
    c15m: list[Any] = []
    meta15: dict[str, Any] = {"source": "skipped_variant_b"}
    if needs_15m:
        print(f"  loading 15m for {symbol}...", flush=True)
        c15m, _, meta15 = await _load_research_candles(
            symbol,
            "15m",
            start_date=DATA_START,
            end_date=DATA_END,
            use_research_cache=True,
            warmup_bars=200,
        )
        if callable(register):
            register(c15m)
        prebuilt.candles_15m = list(c15m)
        prebuilt.idx_15m_map = build_htf_as_of_index_map_fully_closed(
            c1h,
            c15m,
            setup_timeframe="1h",
            htf_timeframe="15m",
        )
    gc.collect()

    print(
        f"  backtesting {symbol}/{combination_id}: 1h={len(c1h)} 15m={len(c15m)}...",
        flush=True,
    )
    last_prog = {"t": time.perf_counter(), "bars": 0}

    def _progress(payload: dict[str, Any]) -> None:
        bars = int(payload.get("bars_processed") or payload.get("bars") or 0)
        total = int(payload.get("total_bars") or payload.get("total") or 0)
        trades_n = payload.get("trades")
        if trades_n is None:
            trades_n = payload.get("trades_n")
        phase = str(payload.get("phase") or "")
        now = time.perf_counter()
        if bars - last_prog["bars"] >= 200 or now - last_prog["t"] >= 10.0:
            elapsed = max(now - last_prog["t"], 1e-6)
            delta = max(bars - last_prog["bars"], 0)
            rate = delta / elapsed
            print(
                f"  [{symbol}/{combination_id}] {phase} bars={bars}/{total} "
                f"trades={trades_n} ({rate:.1f} bars/s)",
                flush=True,
            )
            last_prog["t"] = now
            last_prog["bars"] = bars

    out = run_combination_backtest(
        symbol,
        "1h",
        c1h,
        combo,
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_1h=c1h,
        candles_4h=None,
        candles_15m=c15m if c15m else None,
        direction_filter=None,
        index_start=idx0,
        progress_callback=_progress,
        prebuilt_context=prebuilt,
    )
    closed = [
        t
        for t in (out.get("trades") or [])
        if t.get("outcome") not in (None, "OPEN")
    ]
    rows = [_trade_row(symbol, t) for t in closed]
    elapsed = time.perf_counter() - t0
    return {
        "symbol": symbol,
        "combination_id": combination_id,
        "bars_1h": len(c1h),
        "bars_15m": len(c15m),
        "eval_start": eval_start,
        "source_1h": meta1.get("source"),
        "source_15m": meta15.get("source"),
        "configuration_hash": out.get("configuration_hash"),
        "elapsed_seconds": round(elapsed, 3),
        "sample_size": out.get("sample_size"),
        "metrics": _bucket_metrics(rows),
        "trades": rows,
    }


def _build_report(combination_id: str, per_symbol: list[dict[str, Any]]) -> dict[str, Any]:
    all_trades: list[dict[str, Any]] = []
    for s in per_symbol:
        all_trades.extend(s.get("trades") or [])
    overall = _bucket_metrics(all_trades)

    by_regime = {}
    for reg in REGIMES:
        rows = [t for t in all_trades if str(t.get("regime") or "UNKNOWN") == reg]
        by_regime[reg] = _bucket_metrics(rows)

    by_year: dict[str, dict[str, Any]] = {}
    year_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in all_trades:
        year_groups[str(t.get("year") or "UNKNOWN")].append(t)
    for y in sorted(year_groups):
        by_year[y] = _bucket_metrics(year_groups[y])

    by_symbol = {s["symbol"]: s["metrics"] for s in per_symbol}
    long_rows = [t for t in all_trades if t.get("direction") == "LONG"]
    short_rows = [t for t in all_trades if t.get("direction") == "SHORT"]
    ranked = sorted(
        [t for t in all_trades if t.get("r_multiple") is not None],
        key=lambda t: float(t["r_multiple"]),
    )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "combination_id": combination_id,
        "data_start": DATA_START,
        "data_end": DATA_END,
        "symbols": [s["symbol"] for s in per_symbol],
        "disclaimer": (
            "Research validation only. No parameter optimization. "
            "Not a profitability claim. Not production."
        ),
        "overall": overall,
        "by_regime": by_regime,
        "by_symbol": by_symbol,
        "by_year": by_year,
        "long_vs_short": {
            "LONG": _bucket_metrics(long_rows),
            "SHORT": _bucket_metrics(short_rows),
        },
        "best_trades": list(reversed(ranked[-10:])) if ranked else [],
        "worst_trades": ranked[:10],
        "exit_reasons": overall.get("exit_reasons") or {},
    }


def _format_block(label: str, report: dict[str, Any]) -> str:
    o = report["overall"]
    lines = [
        f"{label}:",
        f"Trades: {o.get('trades')}",
        f"Win rate: {o.get('win_rate')}",
        f"Total R: {o.get('total_R')}",
        f"Avg R: {o.get('average_R')}",
        f"Profit factor: {o.get('profit_factor')}",
        f"Max DD: {o.get('maximum_drawdown_R')}",
        f"Fees: {o.get('total_fees')}",
        f"Expectancy: {o.get('expectancy')}",
        f"Average holding time: {o.get('average_holding_bars')}",
        f"Best trade: {o.get('best_trade')}",
        f"Worst trade: {o.get('worst_trade')}",
        f"Maximum losing streak: {o.get('maximum_losing_streak')}",
        f"Maximum winning streak: {o.get('maximum_winning_streak')}",
        "",
        "Regime breakdown:",
    ]
    for reg in ("CHOPPY", "RANGE", "HIGH_VOLATILITY_RANGE", "TRANSITION"):
        m = report["by_regime"].get(reg) or {}
        lines.append(
            f"{reg}: trades={m.get('trades')} total_R={m.get('total_R')} "
            f"avg_R={m.get('average_R')} win_rate={m.get('win_rate')}"
        )
    lines.extend(["", "Direction:"])
    for d in ("LONG", "SHORT"):
        m = report["long_vs_short"][d]
        lines.append(
            f"{d}: trades={m.get('trades')} total_R={m.get('total_R')} "
            f"avg_R={m.get('average_R')} win_rate={m.get('win_rate')}"
        )
    lines.extend(["", "Symbol breakdown:"])
    for sym, m in report["by_symbol"].items():
        lines.append(
            f"{sym}: trades={m.get('trades')} total_R={m.get('total_R')} "
            f"avg_R={m.get('average_R')} win_rate={m.get('win_rate')}"
        )
    lines.extend(["", "Year breakdown:"])
    for y, m in report["by_year"].items():
        lines.append(
            f"{y}: trades={m.get('trades')} total_R={m.get('total_R')} "
            f"avg_R={m.get('average_R')} win_rate={m.get('win_rate')}"
        )
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--combos",
        default="COMBO_03_TRANSITION,COMBO_03_TRANSITION_B,COMBO_03_TRANSITION_C",
        help="Comma-separated combination ids",
    )
    parser.add_argument(
        "--symbols",
        default=",".join(SYMBOLS),
        help="Comma-separated symbols",
    )
    args = parser.parse_args()
    combos = [c.strip() for c in args.combos.split(",") if c.strip()]
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    from app.config import get_settings
    from app.services.database import db_manager

    v2mod = _load_v2_validation_module()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    await db_manager.connect(get_settings())

    v2mod._install_validation_15m_speed_patch()
    v2mod._install_validation_ohlcv_cache_patch()
    _bind_combo03_to_v2_15m_patch()
    print("Installed validation-only speed patches (strategy files untouched).", flush=True)

    comparison: dict[str, Any] = {}
    summary_blocks: list[str] = []

    for cid in combos:
        out_dir = OUT_ROOT / cid.lower()
        out_dir.mkdir(parents=True, exist_ok=True)
        per_symbol: list[dict[str, Any]] = []
        for sym in symbols:
            print(f"=== {cid} / {sym} ===", flush=True)
            result = await _run_symbol(sym, cid, v2mod)
            (out_dir / f"{sym}_result.json").write_text(
                json.dumps(
                    {k: v for k, v in result.items() if k != "trades"}
                    | {"trade_count": len(result.get("trades") or [])},
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            _write_csv(out_dir / f"{sym}_trades.csv", result.get("trades") or [])
            print(
                f"{sym}: trades={result['metrics']['trades']} "
                f"total_R={result['metrics']['total_R']:.3f} "
                f"elapsed={result['elapsed_seconds']}s",
                flush=True,
            )
            per_symbol.append(result)
            gc.collect()

        report = _build_report(cid, per_symbol)
        all_trades: list[dict[str, Any]] = []
        for s in per_symbol:
            all_trades.extend(s.get("trades") or [])
        (out_dir / "validation_report.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        block = _format_block(cid, report)
        (out_dir / "VALIDATION_REPORT.md").write_text(
            f"# {cid} Validation\n\n{report['disclaimer']}\n\n```text\n{block}\n```\n",
            encoding="utf-8",
        )
        _write_csv(out_dir / "all_trades.csv", all_trades)
        comparison[cid] = report["overall"]
        summary_blocks.append(block)

    # Attach frozen COMBO_02_V2 overall from prior multi-market report when present.
    v2_report_path = Path("reports/combo02_v2_multi_market_validation/validation_report.json")
    if v2_report_path.exists():
        v2rep = json.loads(v2_report_path.read_text(encoding="utf-8"))
        comparison["COMBO_02_V2"] = v2rep.get("overall") or {}
        summary_blocks.insert(
            0,
            "COMBO_02_V2 (prior multi-market validation report):\n"
            + json.dumps(comparison["COMBO_02_V2"], indent=2, default=str),
        )

    compare_rows = [
        {"combination_id": cid, **(metrics if isinstance(metrics, dict) else {})}
        for cid, metrics in comparison.items()
    ]
    _write_csv(OUT_ROOT / "comparison.csv", compare_rows)
    (OUT_ROOT / "COMPARISON_REPORT.md").write_text(
        "# COMBO_03_TRANSITION vs COMBO_02_V2\n\n"
        + "\n\n".join(f"```text\n{b}\n```" for b in summary_blocks)
        + "\n",
        encoding="utf-8",
    )
    print(f"\nWrote artifacts under {OUT_ROOT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
