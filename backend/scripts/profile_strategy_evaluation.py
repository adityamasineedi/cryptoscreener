#!/usr/bin/env python3
"""cProfile + stage timers for the golden COMBO_02 strategy-evaluation path.

Research-only. Does not change strategy behavior.

Outputs:
  backend/scripts/research_benchmark_golden/profile_strategy.txt
  backend/scripts/research_benchmark_golden/profile_strategy.json
  backend/scripts/research_benchmark_golden/stage_timings.json

Usage (from backend/):
  set RESEARCH_PROFILE_ENABLED=true
  python -m scripts.profile_strategy_evaluation
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import io
import json
import os
import pstats
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "scripts" / "research_benchmark_golden"


def _split(raw: str) -> list[str]:
    return [p.strip() for p in str(raw).split(",") if p.strip()]


def _rank_stats(ps: pstats.Stats, *, limit: int = 40) -> list[dict[str, Any]]:
    # Internal sort by cumulative time
    stats = ps.stats  # type: ignore[attr-defined]
    rows: list[tuple[float, float, int, tuple]] = []
    for func, (cc, nc, tt, ct, callers) in stats.items():
        rows.append((ct, tt, nc, func))
    rows.sort(key=lambda r: r[0], reverse=True)
    out: list[dict[str, Any]] = []
    for ct, tt, nc, func in rows[:limit]:
        filename, line, name = func
        out.append(
            {
                "function": name,
                "file": filename,
                "line": line,
                "call_count": int(nc),
                "cumulative_seconds": round(float(ct), 6),
                "self_seconds": round(float(tt), 6),
            }
        )
    return out


def _classify(name: str, file: str) -> str:
    n = (name or "").lower()
    f = (file or "").replace("\\", "/").lower()
    if n in {"_run_combo_matrix", "run_combination_backtest", "main"}:
        return "driver"
    if "extend_swings" in n or "detect_swings" in n:
        return "swing_detection"
    if "analyze_timeframe" in n:
        return "structure_calculation"
    if n in {"to_dict", "asdict", "_asdict_inner"} or "dataclasses.py" in f:
        return "schema_serialization"
    if n in {"deepcopy", "_deepcopy_dict", "_deepcopy_list", "_reconstruct"} or (
        "copy.py" in f and "deep" in n
    ):
        return "object_copying"
    if "detect_bos" in n:
        return "bos_detection"
    if "impulse" in n:
        return "impulse_detection"
    if "detect_pullback" in n:
        return "pullback_evaluation"
    if "detect_retest" in n:
        return "retest_evaluation"
    if "htf" in n or "trend_at_as_of" in n or "precompute_htf" in n:
        return "htf_mapping"
    if "evaluate_combination" in n:
        return "entry_evaluation"
    if "resolve_intrabar" in n or "simulate" in n or "_excursions" in n:
        return "trade_simulation"
    if "candles_as_of" in n:
        return "object_copying"
    if "compute_metrics" in n:
        return "result_aggregation"
    if "lifecycle" in f or "discover_impulse" in n or "run_lifecycle" in n:
        return "lifecycle_construction"
    if n == "atr" or n.endswith("atr_series"):
        return "feature_atr"
    return "other"


async def _load_series(
    symbols: list[str], timeframes: list[str], start: str, end: str
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    from app.research.postgres_ohlcv import load_ohlcv_series_range

    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_ex = datetime.fromisoformat(end).replace(tzinfo=timezone.utc) + timedelta(days=1)
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    needed = set(timeframes) | {"1h", "4h"}
    for s in symbols:
        for tf in sorted(needed):
            out[(s, tf)] = await load_ohlcv_series_range(
                s, tf, start=start_dt, end_exclusive=end_ex, warmup_bars=0
            )
    return out


def _run_combo_matrix(
    series: dict[tuple[str, str], list[dict[str, Any]]],
    symbols: list[str],
    timeframes: list[str],
) -> dict[str, Any]:
    from app.research.bos_combinations import get_combination
    from app.research.combination_backtest import run_combination_backtest
    from app.research.config import ResearchConfig
    from app.research.data_cache.golden_equality import compare_trades
    from app.research.data_cache.stage_profiler import ensure_profiler, research_stage_profiler
    from app.signals.config import SignalConfig

    ensure_profiler(
        symbols=symbols,
        timeframes=timeframes,
        combination_id="COMBO_02",
        purpose="strategy_evaluation_profile",
    )
    combo = get_combination("COMBO_02")
    assert combo is not None
    scfg = SignalConfig()
    rcfg = ResearchConfig()
    cells: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for s in symbols:
        for tf in timeframes:
            with research_stage_profiler.time(f"cell_{s}_{tf}"):
                out = run_combination_backtest(
                    s,
                    tf,
                    series[(s, tf)],
                    combo,
                    signal_config=scfg,
                    research_config=rcfg,
                    direction_filter="LONG",
                    candles_1h=series.get((s, "1h")),
                    candles_4h=series.get((s, "4h")),
                )
            cells.append(
                {
                    "symbol": s,
                    "timeframe": tf,
                    "sample_size": out.get("sample_size"),
                    "trades": out.get("trades") or [],
                    "elapsed_seconds": out.get("elapsed_seconds"),
                    "candles_processed": out.get("candles_processed"),
                    "htf_precompute_seconds": out.get("htf_precompute_seconds"),
                }
            )
    wall = time.perf_counter() - t0
    return {
        "wall_seconds": wall,
        "cells": cells,
        "trade_count": sum(len(c["trades"]) for c in cells),
        "stage_timings": research_stage_profiler.snapshot(),
    }


def _run_lifecycle_pass(
    series: dict[tuple[str, str], list[dict[str, Any]]],
    symbols: list[str],
    timeframes: list[str],
) -> dict[str, Any]:
    from app.research.bos_strategy_comparison.lifecycle import run_lifecycles_for_series
    from app.research.data_cache.stage_profiler import ensure_profiler, research_stage_profiler

    ensure_profiler(
        symbols=symbols,
        timeframes=timeframes,
        purpose="lifecycle_profile",
    )
    t0 = time.perf_counter()
    results = []
    for s in symbols:
        for tf in timeframes:
            with research_stage_profiler.time(f"lifecycle_{s}_{tf}"):
                out = run_lifecycles_for_series(
                    symbol=s,
                    timeframe=tf,
                    candles=series[(s, tf)],
                )
            results.append(
                {
                    "symbol": s,
                    "timeframe": tf,
                    "lifecycles_started": out.get("lifecycles_started"),
                    "bos_candidates": out.get("bos_candidates"),
                    "elapsed": (out.get("performance") or {}).get("elapsed_seconds"),
                }
            )
    return {
        "wall_seconds": time.perf_counter() - t0,
        "results": results,
        "stage_timings": research_stage_profiler.snapshot(),
    }


def _dependency_findings(stage: dict[str, Any], ranked: list[dict[str, Any]]) -> dict[str, Any]:
    stages = (stage or {}).get("stages") or {}
    eval_calls = (stages.get("evaluate_combination_at_bar") or {}).get("calls", 0)
    structure_calls = (stages.get("structure_analyze_timeframe") or {}).get("calls", 0)
    swing_calls = (stages.get("swing_extend") or {}).get("calls", 0)
    bos_pref = (stages.get("bos_prefilter") or {}).get("calls", 0)
    htf_gate = (stages.get("htf_alignment_gate") or {}).get("calls", 0)
    return {
        "architecture_note": (
            "COMBO_02 combination_backtest walks bars with incremental swings; "
            "full analyze_timeframe only on BOS-prefilter hits. "
            "Multi-strategy BOS runner / lifecycle discovery still call "
            "analyze_timeframe every bar when used."
        ),
        "repeated_work_signals": {
            "swing_extend_calls_per_bar_walk": swing_calls,
            "bos_prefilter_calls": bos_pref,
            "full_evaluate_combination_calls": eval_calls,
            "structure_analyze_timeframe_calls": structure_calls,
            "htf_alignment_gate_calls": htf_gate,
            "structure_calls_approx_equal_eval_calls": structure_calls == eval_calls,
        },
        "lifecycle_copy_pattern": {
            "candles_as_of_copy_calls": (stages.get("candles_as_of_copy") or {}).get("calls"),
            "candles_as_of_copy_ms": (stages.get("candles_as_of_copy") or {}).get("elapsed_ms"),
            "note": (
                "If lifecycle profiling is enabled, candles_as_of_copy counts list slices "
                "rebuilt per pullback/retest evaluation (O(bars) copy each time)."
            ),
        },
        "top_cprofile_categories": [
            {
                "category": _classify(r["function"], r["file"]),
                "function": r["function"],
                "cumulative_seconds": r["cumulative_seconds"],
                "self_seconds": r["self_seconds"],
                "call_count": r["call_count"],
            }
            for r in ranked[:15]
        ],
    }


async def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--timeframes", default="15m,1h")
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2025-02-01")
    p.add_argument(
        "--lifecycle-symbol",
        default="BTCUSDT",
        help="Symbol for lifecycle CPU profile (default BTCUSDT only — full matrix is very slow)",
    )
    p.add_argument(
        "--lifecycle-timeframe",
        default="15m",
        help="Timeframe for lifecycle CPU profile (default 15m)",
    )
    p.add_argument("--skip-lifecycle", action="store_true")
    args = p.parse_args(argv)

    # Force stage timers on for this script regardless of prior env (script purpose).
    os.environ["RESEARCH_PROFILE_ENABLED"] = "true"

    from app.config import get_settings
    from app.services.database import db_manager

    settings = get_settings()
    await db_manager.connect(settings, ensure_schema=False)
    if db_manager.engine is None:
        print(json.dumps({"status": "DATABASE unavailable"}))
        return 2

    symbols = _split(args.symbols)
    timeframes = _split(args.timeframes)
    try:
        t_load = time.perf_counter()
        series = await _load_series(symbols, timeframes, args.start, args.end)
        load_s = time.perf_counter() - t_load
        empty = [f"{s}/{tf}" for s in symbols for tf in timeframes if not series.get((s, tf))]
        if empty:
            print(json.dumps({"status": "DATASET_UNAVAILABLE", "missing": empty}, indent=2))
            return 3

        # ---- cProfile COMBO_02 ----
        pr = cProfile.Profile()
        pr.enable()
        combo_out = _run_combo_matrix(series, symbols, timeframes)
        pr.disable()

        stream = io.StringIO()
        ps = pstats.Stats(pr, stream=stream).sort_stats("cumulative")
        ps.print_stats(60)
        ranked = _rank_stats(ps, limit=50)

        lifecycle_out = None
        if not args.skip_lifecycle:
            lc_sym = str(args.lifecycle_symbol).upper()
            lc_tf = str(args.lifecycle_timeframe).lower()
            pr2 = cProfile.Profile()
            pr2.enable()
            lifecycle_out = _run_lifecycle_pass(series, [lc_sym], [lc_tf])
            pr2.disable()
            stream2 = io.StringIO()
            ps2 = pstats.Stats(pr2, stream=stream2).sort_stats("cumulative")
            ps2.print_stats(40)
            lifecycle_ranked = _rank_stats(ps2, limit=40)
            lifecycle_txt = stream2.getvalue()
        else:
            lifecycle_ranked = []
            lifecycle_txt = ""

        # Golden trade count check vs known 17
        trade_count = combo_out["trade_count"]

        total_combo = float(combo_out["wall_seconds"])
        findings = _dependency_findings(combo_out["stage_timings"], ranked)
        # Merge lifecycle stage timings if present
        if lifecycle_out:
            findings["lifecycle_stage_timings"] = lifecycle_out["stage_timings"]
            findings["lifecycle_wall_seconds"] = lifecycle_out["wall_seconds"]
            findings["lifecycle_top_cprofile"] = [
                {
                    "category": _classify(r["function"], r["file"]),
                    "function": r["function"],
                    "cumulative_seconds": r["cumulative_seconds"],
                    "self_seconds": r["self_seconds"],
                    "call_count": r["call_count"],
                }
                for r in lifecycle_ranked[:15]
            ]

        # Classify bottlenecks
        bottlenecks = []
        for r in ranked[:10]:
            pct = 100.0 * r["cumulative_seconds"] / total_combo if total_combo > 0 else 0.0
            level = (
                "PRIMARY_BOTTLENECK"
                if pct >= 20
                else "SECONDARY_BOTTLENECK"
                if pct >= 5
                else "LOW_IMPACT"
            )
            bottlenecks.append(
                {
                    **r,
                    "pct_of_strategy_wall": round(pct, 1),
                    "classification": level,
                    "category": _classify(r["function"], r["file"]),
                }
            )

        report = {
            "status": "OK",
            "dataset": {
                "symbols": symbols,
                "timeframes": timeframes,
                "start": args.start,
                "end": args.end,
                "rows_loaded": sum(len(v) for v in series.values()),
            },
            "dataset_prepare_seconds": round(load_s, 6),
            "strategy_evaluation_wall_seconds": round(total_combo, 6),
            "trade_count": trade_count,
            "expected_trade_count_golden": 17,
            "trade_count_matches_golden": trade_count == 17,
            "cells": [
                {
                    "symbol": c["symbol"],
                    "timeframe": c["timeframe"],
                    "sample_size": c["sample_size"],
                    "trades": len(c["trades"]),
                    "elapsed_seconds": c["elapsed_seconds"],
                    "candles_processed": c["candles_processed"],
                }
                for c in combo_out["cells"]
            ],
            "top_functions": bottlenecks,
            "dependency_map_findings": findings,
            "optimization_candidates": [],
            "decision": "READY_FOR_OPTIMIZATION",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Fill optimization candidates from evidence (do not implement)
        primary = [b for b in bottlenecks if b["classification"] == "PRIMARY_BOTTLENECK"]
        secondary = [b for b in bottlenecks if b["classification"] == "SECONDARY_BOTTLENECK"]
        candidates = []
        cats = {b["category"] for b in primary + secondary}
        if "structure_calculation" in cats or "swing_detection" in cats:
            candidates.append(
                {
                    "id": 1,
                    "title": "Reduce full structure/BOS work on BOS-candidate bars",
                    "evidence": "High cumulative time in analyze_timeframe / swing / bos helpers",
                    "risk": "Must preserve as_of_index and BOS semantics; equality-gated",
                }
            )
        if lifecycle_out and (
            (lifecycle_out["stage_timings"].get("stages") or {}).get(
                "lifecycle_discover_analyze_timeframe"
            )
        ):
            candidates.append(
                {
                    "id": 2,
                    "title": "Lifecycle discovery: avoid full analyze_timeframe every bar",
                    "evidence": "lifecycle_discover_analyze_timeframe dominates lifecycle profile",
                    "risk": "Must keep BOS+impulse freeze semantics identical",
                }
            )
        copy_ms = (
            ((lifecycle_out or {}).get("stage_timings") or {}).get("stages") or {}
        ).get("candles_as_of_copy", {}).get("elapsed_ms")
        if copy_ms and copy_ms > 100:
            candidates.append(
                {
                    "id": 3,
                    "title": "Eliminate candles_as_of list copies if engines honor as_of_index",
                    "evidence": f"candles_as_of_copy elapsed_ms={copy_ms}",
                    "risk": "Only if detect_pullback/retest already respect as_of_index on full series",
                }
            )
        if not candidates:
            candidates.append(
                {
                    "id": 1,
                    "title": "Inspect top cProfile functions listed in top_functions",
                    "evidence": "See cumulative ranking",
                    "risk": "Equality-gated only",
                }
            )
        report["optimization_candidates"] = candidates[:3]

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        txt_path = OUT_DIR / "profile_strategy.txt"
        json_path = OUT_DIR / "profile_strategy.json"
        stage_path = OUT_DIR / "stage_timings.json"

        txt_body = []
        txt_body.append("RESEARCH STRATEGY EVALUATION PROFILE")
        txt_body.append("=" * 60)
        txt_body.append(f"Dataset: {symbols} {timeframes} {args.start}->{args.end}")
        txt_body.append(f"COMBO_02 wall seconds: {total_combo:.3f}")
        txt_body.append(f"Trades: {trade_count} (golden expect 17)")
        txt_body.append("")
        txt_body.append("TOP FUNCTIONS (cumulative)")
        txt_body.append("-" * 60)
        for b in bottlenecks:
            txt_body.append(
                f"{b['cumulative_seconds']:8.3f}s  {b['pct_of_strategy_wall']:5.1f}%  "
                f"n={b['call_count']:<8}  [{b['classification']}]  "
                f"{b['category']:24}  {b['function']}"
            )
        txt_body.append("")
        txt_body.append("cProfile dump (COMBO_02):")
        txt_body.append(stream.getvalue())
        if lifecycle_txt:
            txt_body.append("")
            txt_body.append("cProfile dump (lifecycle):")
            txt_body.append(lifecycle_txt)
        txt_path.write_text("\n".join(txt_body), encoding="utf-8")
        json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        stage_path.write_text(
            json.dumps(
                {
                    "combo02": combo_out["stage_timings"],
                    "lifecycle": (lifecycle_out or {}).get("stage_timings"),
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

        print(f"Wrote {txt_path}")
        print(f"Wrote {json_path}")
        print(f"Wrote {stage_path}")
        print(f"COMBO_02 wall={total_combo:.3f}s trades={trade_count}")
        print("TOP:")
        for b in bottlenecks[:8]:
            print(
                f"  {b['cumulative_seconds']:.3f}s {b['pct_of_strategy_wall']:5.1f}% "
                f"{b['classification']:22} {b['category']:22} {b['function']}"
            )
        print("DECISION:", report["decision"])
        return 0
    finally:
        await db_manager.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
