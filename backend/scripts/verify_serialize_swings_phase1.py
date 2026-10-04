#!/usr/bin/env python3
"""Phase-1 equality + perf check: skip research SwingRecord serialization.

Compares COMBO_02 golden trades/metrics vs baseline_latest.json, samples
analyze_timeframe serialize True vs False event equality, and profiles CPU/RSS.

Usage (from backend/):
  python -m scripts.verify_serialize_swings_phase1
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
BASELINE = OUT_DIR / "baseline_latest.json"


def _split(raw: str) -> list[str]:
    return [p.strip() for p in str(raw).split(",") if p.strip()]


def _rss_mb() -> float | None:
    from app.research.data_cache.metrics import sample_rss_mb

    return sample_rss_mb()


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


def _structure_keys(analysis: dict[str, Any]) -> dict[str, Any]:
    swings = analysis.get("_swings_objs") or []
    return {
        "trend": analysis.get("trend"),
        "bos": analysis.get("bos"),
        "choch": analysis.get("choch"),
        "impulse": analysis.get("impulse"),
        "pullback": analysis.get("pullback"),
        "retest": analysis.get("retest"),
        "swing_count": len(swings),
        "swings": [
            {
                "bar_index": s.bar_index,
                "swing_type": s.swing_type,
                "price": float(s.price),
                "label": s.label,
                "timestamp": s.timestamp.isoformat() if s.timestamp else None,
            }
            for s in swings
        ],
    }


def _check_serialize_parity(series: dict[tuple[str, str], list[dict[str, Any]]]) -> dict[str, Any]:
    from app.signals.config import SignalConfig
    from app.signals.signal_engine import SignalEngine

    eng = SignalEngine(SignalConfig())
    mismatches: list[dict[str, Any]] = []
    checked = 0
    for (sym, tf), candles in series.items():
        if tf not in ("15m", "1h") or len(candles) < 80:
            continue
        # Sample several as_of indices across the series
        indices = [
            len(candles) // 4,
            len(candles) // 2,
            (3 * len(candles)) // 4,
            len(candles) - 1,
        ]
        for i in indices:
            prod = eng.analyze_timeframe(
                sym, tf, candles, as_of_index=i, serialize_swings=True
            )
            research = eng.analyze_timeframe(
                sym, tf, candles, as_of_index=i, serialize_swings=False
            )
            checked += 1
            if _structure_keys(prod) != _structure_keys(research):
                mismatches.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "as_of_index": i,
                        "stage": "structure",
                    }
                )
                continue
            rebuilt = [s.to_dict() for s in research["_swings_objs"]]
            if rebuilt != prod["swings"]:
                mismatches.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "as_of_index": i,
                        "stage": "api_serialization",
                    }
                )
            if research["swings"] != []:
                mismatches.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "as_of_index": i,
                        "stage": "research_swings_not_empty",
                    }
                )
    return {
        "status": "PASS" if not mismatches else "FIRST_MISMATCH",
        "checked": checked,
        "mismatches": mismatches[:5],
    }


def _run_combo(
    series: dict[tuple[str, str], list[dict[str, Any]]],
    symbols: list[str],
    timeframes: list[str],
) -> dict[str, Any]:
    from app.research.bos_combinations import get_combination
    from app.research.combination_backtest import run_combination_backtest
    from app.research.config import ResearchConfig
    from app.signals.config import SignalConfig

    combo = get_combination("COMBO_02")
    assert combo is not None
    scfg = SignalConfig()
    rcfg = ResearchConfig()
    cells: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for s in symbols:
        for tf in timeframes:
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
                    "result": out.get("result") or {},
                    "trades": out.get("trades") or [],
                    "elapsed_seconds": out.get("elapsed_seconds"),
                }
            )
    return {
        "wall_seconds": time.perf_counter() - t0,
        "cells": cells,
        "trade_count": sum(len(c["trades"]) for c in cells),
    }


def _compare_to_baseline(combo_out: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    from app.research.data_cache.golden_equality import compare_metrics, compare_trades

    base_cells = {(c["symbol"], c["timeframe"]): c for c in baseline.get("cells") or []}
    details: dict[str, Any] = {}
    overall = "PASS"
    for cell in combo_out["cells"]:
        key = (cell["symbol"], cell["timeframe"])
        label = f"{key[0]}_{key[1]}"
        b = base_cells.get(key)
        if b is None:
            details[label] = {"status": "FIRST_MISMATCH", "stage": "missing_baseline_cell"}
            overall = "FIRST_MISMATCH"
            continue
        tcmp = compare_trades(b.get("trades") or [], cell.get("trades") or [])
        mcmp = compare_metrics(b.get("result") or {}, cell.get("result") or {})
        details[f"trades_{label}"] = tcmp
        details[f"metrics_{label}"] = mcmp
        if tcmp.get("status") != "PASS" or mcmp.get("status") != "PASS":
            overall = "FIRST_MISMATCH"
    return {
        "status": overall,
        "trade_count_optimized": combo_out["trade_count"],
        "trade_count_baseline": sum(
            len(c.get("trades") or []) for c in baseline.get("cells") or []
        ),
        "details": details,
    }


def _extract_cum(ps: pstats.Stats, names: set[str]) -> dict[str, dict[str, float | int]]:
    out: dict[str, dict[str, float | int]] = {}
    stats = ps.stats  # type: ignore[attr-defined]
    for func, (_cc, nc, tt, ct, _callers) in stats.items():
        _filename, _line, name = func
        if name in names:
            prev = out.get(name)
            if prev is None or float(ct) > float(prev["cumulative_seconds"]):
                out[name] = {
                    "call_count": int(nc),
                    "cumulative_seconds": round(float(ct), 6),
                    "self_seconds": round(float(tt), 6),
                }
    return out


async def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--timeframes", default="15m,1h")
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2025-02-01")
    args = p.parse_args(argv)

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
        if not BASELINE.exists():
            print(json.dumps({"status": "BASELINE_MISSING", "path": str(BASELINE)}))
            return 3
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

        rss_before = _rss_mb()
        series = await _load_series(symbols, timeframes, args.start, args.end)
        empty = [f"{s}/{tf}" for s in symbols for tf in timeframes if not series.get((s, tf))]
        if empty:
            print(json.dumps({"status": "DATASET_UNAVAILABLE", "missing": empty}, indent=2))
            return 3

        parity = _check_serialize_parity(series)

        pr = cProfile.Profile()
        pr.enable()
        t0 = time.perf_counter()
        combo_out = _run_combo(series, symbols, timeframes)
        wall = time.perf_counter() - t0
        pr.disable()
        rss_after = _rss_mb()

        stream = io.StringIO()
        ps = pstats.Stats(pr, stream=stream).sort_stats("cumulative")
        ps.print_stats(40)
        hot = _extract_cum(
            ps,
            {
                "analyze_timeframe",
                "to_dict",
                "asdict",
                "_asdict_inner",
                "deepcopy",
                "evaluate_combination_at_bar",
                "_run_combo",
                "run_combination_backtest",
            },
        )

        equality = _compare_to_baseline(combo_out, baseline)

        # Before numbers from the prior profile artifact (pre-optimization)
        before = {
            "strategy_evaluation_wall_seconds": 18.965,
            "analyze_timeframe_cum_seconds": 14.402,
            "to_dict_cum_seconds": 11.682,
            "asdict_cum_seconds": 10.955,
            "deepcopy_cum_seconds": 8.547,
            "evaluate_combination_at_bar_cum_seconds": 15.026,
            "trade_count": 17,
            "source": "profile_strategy.txt (pre Phase-1)",
        }
        after = {
            "strategy_evaluation_wall_seconds": round(wall, 6),
            "analyze_timeframe_cum_seconds": (hot.get("analyze_timeframe") or {}).get(
                "cumulative_seconds"
            ),
            "to_dict_cum_seconds": (hot.get("to_dict") or {}).get("cumulative_seconds"),
            "asdict_cum_seconds": (hot.get("asdict") or {}).get("cumulative_seconds"),
            "deepcopy_cum_seconds": (hot.get("deepcopy") or {}).get("cumulative_seconds"),
            "evaluate_combination_at_bar_cum_seconds": (
                hot.get("evaluate_combination_at_bar") or {}
            ).get("cumulative_seconds"),
            "trade_count": combo_out["trade_count"],
            "hot_functions": hot,
        }

        def _speedup(b: float | None, a: float | None) -> dict[str, Any] | None:
            if b is None or a is None or b <= 0:
                return None
            return {
                "before": b,
                "after": a,
                "absolute_improvement_seconds": round(b - a, 6),
                "pct_improvement": round(100.0 * (b - a) / b, 2),
                "speedup_x": round(b / a, 3) if a > 0 else None,
            }

        report = {
            "status": "OK"
            if equality["status"] == "PASS" and parity["status"] == "PASS"
            else "FAIL",
            "optimization": "serialize_swings=False on research hot path",
            "serialize_parity": parity,
            "golden_equality": equality,
            "performance": {
                "before": before,
                "after": after,
                "wall": _speedup(
                    before["strategy_evaluation_wall_seconds"],
                    after["strategy_evaluation_wall_seconds"],
                ),
                "analyze_timeframe": _speedup(
                    before["analyze_timeframe_cum_seconds"],
                    after["analyze_timeframe_cum_seconds"],
                ),
                "to_dict": _speedup(
                    before["to_dict_cum_seconds"], after["to_dict_cum_seconds"] or 0.0
                ),
                "asdict": _speedup(
                    before["asdict_cum_seconds"], after["asdict_cum_seconds"] or 0.0
                ),
                "deepcopy": _speedup(
                    before["deepcopy_cum_seconds"], after["deepcopy_cum_seconds"] or 0.0
                ),
            },
            "memory": {
                "rss_mb_before_combo": rss_before,
                "rss_mb_after_combo": rss_after,
            },
            "cprofile_dump_head": stream.getvalue()[:8000],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUT_DIR / "phase1_serialize_swings.json"
        out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        (OUT_DIR / "phase1_serialize_swings_profile.txt").write_text(
            stream.getvalue(), encoding="utf-8"
        )
        print(json.dumps({k: report[k] for k in ("status", "performance", "memory", "golden_equality", "serialize_parity")}, indent=2, default=str))
        print(f"Wrote {out_path}")
        return 0 if report["status"] == "OK" else 1
    finally:
        await db_manager.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
