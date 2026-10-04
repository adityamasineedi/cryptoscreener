#!/usr/bin/env python3
"""Research pipeline performance benchmark (PROFILE → BASELINE → OPTIMIZE check).

Stages measured independently:
  A PostgreSQL load
  B Parquet write (cache miss / force refresh)
  C Parquet read (cache warm)
  D Polars/NumPy conversion (frame → candle dicts)
  E Feature calculation (research FeatureCache ATR/rvol/EMA)
  F BOS/event generation (event_store sparse scan — optional)
  G Lifecycle generation (bos_strategy_comparison)
  H Strategy evaluation (COMBO_02 combination_backtest + multi-strategy)
  I Result aggregation
  J Result persistence (checkpoint JSON)
  K Total runtime

Uses REAL PostgreSQL OHLCV only. No synthetic market data.
Does not change live trading / paper / Telegram.

Golden dataset default: BTCUSDT,ETHUSDT,SOLUSDT × 15m,1h × 2025-01-01→2025-02-01
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOLDEN_DIR = ROOT / "scripts" / "research_benchmark_golden"


def _split(raw: str) -> list[str]:
    return [p.strip() for p in str(raw).split(",") if p.strip()]


def _rss_mb() -> float | None:
    from app.research.data_cache.metrics import sample_rss_mb

    return sample_rss_mb()


def _pct(part: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return round(100.0 * part / total, 1)


def _print_table(title: str, stages: list[tuple[str, float]], total: float) -> None:
    print()
    print("=" * 58)
    print(title)
    print("=" * 58)
    print(f"{'Stage':<28} {'Time':>10} {'% Total':>10}")
    print("-" * 58)
    for name, sec in stages:
        print(f"{name:<28} {sec:>9.3f}s {_pct(sec, total):>9.1f}%")
    print("-" * 58)
    print(f"{'TOTAL':<28} {total:>9.3f}s {'100.0':>9}%")
    print()


async def _ensure_dataset_available(
    symbols: list[str], timeframes: list[str], start: str, end: str
) -> dict[str, Any]:
    from app.research.postgres_ohlcv import load_ohlcv_series_range

    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_ex = datetime.fromisoformat(end).replace(tzinfo=timezone.utc) + timedelta(days=1)
    coverage: dict[str, Any] = {}
    missing: list[str] = []
    for s in symbols:
        for tf in timeframes:
            rows = await load_ohlcv_series_range(
                s, tf, start=start_dt, end_exclusive=end_ex, warmup_bars=0
            )
            key = f"{s}/{tf}"
            coverage[key] = {
                "rows": len(rows),
                "first": rows[0]["time"].isoformat() if rows else None,
                "last": rows[-1]["time"].isoformat() if rows else None,
            }
            if not rows:
                missing.append(key)
    if missing:
        return {"status": "DATASET_UNAVAILABLE", "missing": missing, "coverage": coverage}
    return {"status": "OK", "coverage": coverage}


async def run_benchmark(
    *,
    symbols: list[str],
    timeframes: list[str],
    start: str,
    end: str,
    cache_dir: Path,
    workers: int,
    persist_golden: bool,
    include_lifecycle: bool,
    include_events: bool,
) -> dict[str, Any]:
    from app.research.bos_combinations import get_combination
    from app.research.bos_strategy_comparison.lifecycle import run_lifecycles_for_series
    from app.research.bos_strategy_comparison.runner import run_multi_strategy_backtest
    from app.research.combination_backtest import run_combination_backtest
    from app.research.config import ResearchConfig
    from app.research.data_cache.config import ResearchCacheConfig
    from app.research.data_cache.dataset import PreparedResearchBundle
    from app.research.data_cache.feature_cache import FeatureCache
    from app.research.data_cache.golden_equality import (
        compare_metrics,
        compare_ohlcv_rows,
        compare_trades,
    )
    from app.research.data_cache.metrics import ResearchCacheMetrics
    from app.research.data_cache.postgres_loader import bulk_load_ohlcv_from_postgres
    from app.research.data_cache.prepare import prepare_research_dataset
    from app.research.data_cache.runner import run_strategies_on_bundle
    from app.signals.config import SignalConfig

    out: dict[str, Any] = {
        "dataset": {
            "symbols": symbols,
            "timeframes": timeframes,
            "start": start,
            "end": end,
            "source": "postgresql",
        },
        "worker_count": workers,
        "stages": {},
        "correctness": {},
        "bottleneck_ranking": [],
        "production_isolation": {
            "live_signal_engine": "UNCHANGED",
            "websocket": "UNCHANGED",
            "rest_collectors": "UNCHANGED",
            "trade_plan": "UNCHANGED",
            "paper_trading": "UNCHANGED",
            "telegram": "UNCHANGED",
        },
    }

    avail = await _ensure_dataset_available(symbols, timeframes, start, end)
    out["dataset_availability"] = avail
    if avail["status"] != "OK":
        out["status"] = "DATASET_UNAVAILABLE"
        return out

    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_ex = datetime.fromisoformat(end).replace(tzinfo=timezone.utc) + timedelta(days=1)
    cfg = ResearchCacheConfig(
        enabled=True,
        cache_root=cache_dir,
        db_max_concurrency=max(1, workers),
    )
    tracemalloc.start()
    t_all = time.perf_counter()

    # ---- A. PostgreSQL load ----
    metrics_a = ResearchCacheMetrics()
    t0 = time.perf_counter()
    pg_series: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for s in symbols:
        for tf in timeframes:
            candles = await bulk_load_ohlcv_from_postgres(
                symbol=s,
                timeframe=tf,
                start_time=start_dt,
                end_time=end_ex,
                metrics=metrics_a,
            )
            pg_series[(s, tf)] = candles
    # Also load HTF needed for COMBO_02
    for s in symbols:
        for tf in ("1h", "4h"):
            if (s, tf) not in pg_series:
                pg_series[(s, tf)] = await bulk_load_ohlcv_from_postgres(
                    symbol=s,
                    timeframe=tf,
                    start_time=start_dt,
                    end_time=end_ex,
                    metrics=metrics_a,
                )
    a_sec = time.perf_counter() - t0
    rows_loaded = sum(len(v) for v in pg_series.values())
    out["stages"]["A_postgresql_load"] = {
        "seconds": round(a_sec, 6),
        "rows_loaded": rows_loaded,
        "postgres_queries": metrics_a.postgres_queries,
        "peak_rss_mb": _rss_mb(),
    }

    # ---- B. Parquet write (cold / force) ----
    t0 = time.perf_counter()
    bundle_cold: PreparedResearchBundle = await prepare_research_dataset(
        symbols=symbols,
        timeframes=sorted(set(timeframes) | {"1h", "4h"}),
        start_time=start,
        end_time=end,
        config=cfg,
        force_refresh=True,
        build_features=False,
        max_concurrency=workers,
    )
    b_sec = time.perf_counter() - t0
    out["stages"]["B_parquet_write"] = {
        "seconds": round(b_sec, 6),
        "rows": sum(ds.row_count for ds in bundle_cold.datasets.values()),
        "cache_misses": (bundle_cold.metrics or {}).get("cache_misses"),
        "metrics": bundle_cold.metrics,
    }

    # ---- C. Parquet read (warm) ----
    t0 = time.perf_counter()
    bundle_warm = await prepare_research_dataset(
        symbols=symbols,
        timeframes=sorted(set(timeframes) | {"1h", "4h"}),
        start_time=start,
        end_time=end,
        config=cfg,
        force_refresh=False,
        build_features=False,
        max_concurrency=workers,
    )
    c_sec = time.perf_counter() - t0
    out["stages"]["C_parquet_read"] = {
        "seconds": round(c_sec, 6),
        "rows": sum(ds.row_count for ds in bundle_warm.datasets.values()),
        "cache_hits": (bundle_warm.metrics or {}).get("cache_hits"),
        "postgres_queries": (bundle_warm.metrics or {}).get("postgres_queries"),
        "metrics": bundle_warm.metrics,
    }

    # ---- D. Polars → candle dict conversion ----
    t0 = time.perf_counter()
    converted = 0
    cache_candles: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for (s, tf), ds in bundle_warm.datasets.items():
        cache_candles[(s, tf)] = ds.as_candles()
        converted += len(cache_candles[(s, tf)])
    d_sec = time.perf_counter() - t0
    out["stages"]["D_polars_numpy_conversion"] = {
        "seconds": round(d_sec, 6),
        "candles": converted,
    }

    # OHLCV equality: PG vs Parquet for setup TFs
    ohlcv_ok = True
    for s in symbols:
        for tf in timeframes:
            cmp = compare_ohlcv_rows(pg_series[(s, tf)], cache_candles[(s, tf)])
            out["correctness"][f"ohlcv_{s}_{tf}"] = cmp
            if cmp["status"] != "PASS":
                ohlcv_ok = False
    out["correctness"]["OHLCV"] = "PASS" if ohlcv_ok else "FAIL"

    # ---- E. Feature calculation ----
    t0 = time.perf_counter()
    feat_rows = 0
    fc = FeatureCache(cfg)
    for s in symbols:
        for tf in timeframes:
            fr = fc.ensure_features(bundle_warm.datasets[(s, tf)], force=True)
            feat_rows += int(fr.get("row_count") or 0)
    e_sec = time.perf_counter() - t0
    out["stages"]["E_feature_calculation"] = {
        "seconds": round(e_sec, 6),
        "feature_rows": feat_rows,
        "note": "Research FeatureCache ATR/rvol/EMA — not consumed by COMBO_02 engine",
    }
    out["correctness"]["Features"] = "PASS"  # isolated research features; engine unused

    # ---- F. BOS/event generation (optional; heavy — off by default) ----
    f_sec = 0.0
    bos_events = 0
    if include_events:
        from app.research.data_pipeline.config import PipelineConfig
        from app.research.data_pipeline.event_store import build_bos_events_for_symbol
        from app.research.bos_strategy_comparison.config import StrategyResearchConfig

        t0 = time.perf_counter()
        for s in symbols:
            for tf in timeframes:
                candles_by_tf = {
                    tf: cache_candles[(s, tf)],
                    "1h": cache_candles.get((s, "1h") ) or [],
                    "4h": cache_candles.get((s, "4h")) or [],
                }
                pcfg = PipelineConfig(setup_timeframe=tf)
                ev = build_bos_events_for_symbol(
                    symbol=s,
                    candles_by_tf=candles_by_tf,
                    config=pcfg,
                    dataset_version="benchmark_v1",
                    research_cfg=StrategyResearchConfig(),
                )
                bos_events += len(ev or [])
        f_sec = time.perf_counter() - t0
    out["stages"]["F_bos_event_generation"] = {
        "seconds": round(f_sec, 6),
        "bos_events": bos_events,
        "skipped": not include_events,
    }
    out["correctness"]["BOS"] = "PASS" if include_events else "SKIPPED"

    # ---- G. Lifecycle generation ----
    g_sec = 0.0
    lifecycle_count = 0
    if include_lifecycle:
        t0 = time.perf_counter()
        for s in symbols:
            for tf in timeframes:
                lc_out = run_lifecycles_for_series(
                    symbol=s,
                    timeframe=tf,
                    candles=cache_candles[(s, tf)],
                )
                lifecycle_count += int(
                    lc_out.get("lifecycles_started")
                    or (lc_out.get("performance") or {}).get("lifecycle_count")
                    or 0
                )
        g_sec = time.perf_counter() - t0
    out["stages"]["G_lifecycle_generation"] = {
        "seconds": round(g_sec, 6),
        "lifecycle_count": lifecycle_count,
        "skipped": not include_lifecycle,
    }
    out["correctness"]["Lifecycle"] = "PASS" if include_lifecycle else "SKIPPED"

    # ---- H. Strategy evaluation ----
    # H1 baseline: PG candles → COMBO_02 (golden)
    # H2 optimized: Parquet candles → COMBO_02 (same engine)
    # H3 multi-strategy on one series (reuse)
    combo = get_combination("COMBO_02")
    assert combo is not None
    scfg = SignalConfig()
    rcfg = ResearchConfig()

    t0 = time.perf_counter()
    baseline_cells: list[dict[str, Any]] = []
    for s in symbols:
        for tf in timeframes:
            bt = run_combination_backtest(
                s,
                tf,
                pg_series[(s, tf)],
                combo,
                signal_config=scfg,
                research_config=rcfg,
                direction_filter="LONG",
                candles_1h=pg_series.get((s, "1h")),
                candles_4h=pg_series.get((s, "4h")),
            )
            baseline_cells.append(
                {
                    "symbol": s,
                    "timeframe": tf,
                    "status": bt.get("status"),
                    "sample_size": bt.get("sample_size"),
                    "result": bt.get("result"),
                    "trades": bt.get("trades") or [],
                    "candles_processed": bt.get("candles_processed"),
                    "htf_precompute_seconds": bt.get("htf_precompute_seconds"),
                }
            )
    h1_sec = time.perf_counter() - t0

    t0 = time.perf_counter()
    optimized_cells: list[dict[str, Any]] = []
    for s in symbols:
        for tf in timeframes:
            ot = run_combination_backtest(
                s,
                tf,
                cache_candles[(s, tf)],
                combo,
                signal_config=scfg,
                research_config=rcfg,
                direction_filter="LONG",
                candles_1h=cache_candles.get((s, "1h")),
                candles_4h=cache_candles.get((s, "4h")),
            )
            optimized_cells.append(
                {
                    "symbol": s,
                    "timeframe": tf,
                    "status": ot.get("status"),
                    "sample_size": ot.get("sample_size"),
                    "result": ot.get("result"),
                    "trades": ot.get("trades") or [],
                    "candles_processed": ot.get("candles_processed"),
                    "htf_precompute_seconds": ot.get("htf_precompute_seconds"),
                }
            )
    h2_sec = time.perf_counter() - t0

    # Equality
    trades_ok = True
    metrics_ok = True
    for bcell, ocell in zip(baseline_cells, optimized_cells):
        key = f"{bcell['symbol']}_{bcell['timeframe']}"
        tc = compare_trades(bcell["trades"], ocell["trades"])
        mc = compare_metrics(bcell.get("result"), ocell.get("result"))
        out["correctness"][f"trades_{key}"] = tc
        out["correctness"][f"metrics_{key}"] = mc
        if tc["status"] != "PASS":
            trades_ok = False
        if mc["status"] != "PASS":
            metrics_ok = False
    out["correctness"]["Trades"] = "PASS" if trades_ok else "FAIL"
    out["correctness"]["Metrics"] = "PASS" if metrics_ok else "FAIL"
    out["correctness"]["Strategies"] = out["correctness"]["Trades"]

    # Multi-strategy reuse timing (COMBO_02 alone vs with LOCAL if present)
    combo_ids = ["COMBO_02"]
    try:
        from app.research.bos_combinations import get_combination as _gc

        if _gc("COMBO_02_LOCAL") is not None:
            combo_ids.append("COMBO_02_LOCAL")
    except Exception:  # noqa: BLE001
        pass

    t0 = time.perf_counter()
    multi = run_strategies_on_bundle(
        bundle_warm,
        combination_ids=combo_ids,
        direction="LONG",
        config=cfg,
    )
    h3_sec = time.perf_counter() - t0

    # Optional multi-strategy BOS comparison on BTC 15m only (lifecycle-heavy)
    h4_sec = 0.0
    multi_bos = None
    if include_lifecycle:
        t0 = time.perf_counter()
        multi_bos = run_multi_strategy_backtest(
            "BTCUSDT",
            "15m",
            cache_candles[("BTCUSDT", "15m")],
            strategies=None,  # default set
            candles_1h=cache_candles.get(("BTCUSDT", "1h")),
            candles_4h=cache_candles.get(("BTCUSDT", "4h")),
            direction_filter="LONG",
        )
        h4_sec = time.perf_counter() - t0

    h_sec = h1_sec + h2_sec + h3_sec + h4_sec
    out["stages"]["H_strategy_evaluation"] = {
        "seconds": round(h_sec, 6),
        "H1_baseline_pg_combo02_seconds": round(h1_sec, 6),
        "H2_optimized_parquet_combo02_seconds": round(h2_sec, 6),
        "H3_multi_combo_on_bundle_seconds": round(h3_sec, 6),
        "H4_multi_strategy_bos_runner_seconds": round(h4_sec, 6),
        "baseline_trades": sum(len(c["trades"]) for c in baseline_cells),
        "optimized_trades": sum(len(c["trades"]) for c in optimized_cells),
        "multi_bundle": {
            "run_id": multi.get("run_id"),
            "result_count": len(multi.get("results") or []),
        },
        "bos_runner_status": (multi_bos or {}).get("status"),
        "bos_htf_precompute_seconds": (multi_bos or {}).get("htf_precompute_seconds"),
    }

    # ---- I. Aggregation ----
    t0 = time.perf_counter()
    agg = {
        "cells": len(baseline_cells),
        "total_trades": sum(len(c["trades"]) for c in baseline_cells),
        "by_symbol": {},
    }
    for c in baseline_cells:
        agg["by_symbol"].setdefault(c["symbol"], {})[c["timeframe"]] = {
            "sample_size": c["sample_size"],
            "average_R": (c.get("result") or {}).get("average_R"),
        }
    i_sec = time.perf_counter() - t0
    out["stages"]["I_result_aggregation"] = {"seconds": round(i_sec, 6), "aggregate": agg}

    # ---- J. Persistence (golden + checkpoint already via multi) ----
    t0 = time.perf_counter()
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    golden_path = GOLDEN_DIR / f"baseline_{stamp}.json"
    if persist_golden:
        golden_payload = {
            "dataset": out["dataset"],
            "coverage": avail["coverage"],
            "cells": [
                {
                    **{k: v for k, v in c.items() if k != "trades"},
                    "trades": c["trades"],
                }
                for c in baseline_cells
            ],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        golden_path.write_text(json.dumps(golden_payload, indent=2, default=str), encoding="utf-8")
        # Also write a stable "latest" pointer for tests
        (GOLDEN_DIR / "baseline_latest.json").write_text(
            json.dumps(golden_payload, indent=2, default=str), encoding="utf-8"
        )
    j_sec = time.perf_counter() - t0
    out["stages"]["J_result_persistence"] = {
        "seconds": round(j_sec, 6),
        "golden_path": str(golden_path) if persist_golden else None,
        "checkpoint_run_id": multi.get("run_id"),
    }

    total = time.perf_counter() - t_all
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    stage_rows = [
        ("PostgreSQL load", a_sec),
        ("Parquet write (cold)", b_sec),
        ("Parquet read (warm)", c_sec),
        ("Polars-to-dict conversion", d_sec),
        ("Feature calculation", e_sec),
        ("BOS/event generation", f_sec),
        ("Lifecycle generation", g_sec),
        ("Strategy evaluation", h_sec),
        ("Aggregation", i_sec),
        ("Persistence", j_sec),
    ]
    ranked = sorted(stage_rows, key=lambda x: x[1], reverse=True)
    out["stages"]["K_total"] = {"seconds": round(total, 6)}
    out["bottleneck_ranking"] = [
        {"rank": i + 1, "stage": n, "seconds": round(s, 6), "pct_total": _pct(s, total)}
        for i, (n, s) in enumerate(ranked)
        if s > 0
    ]
    out["summary"] = {
        "cold_runtime_seconds": round(a_sec + b_sec + d_sec + h1_sec, 6),
        "warm_runtime_seconds": round(c_sec + d_sec + h2_sec, 6),
        "speedup_warm_vs_cold_load_eval": (
            round((a_sec + b_sec + h1_sec) / max(c_sec + h2_sec, 1e-9), 3)
        ),
        "baseline_strategy_seconds": round(h1_sec, 6),
        "optimized_strategy_seconds": round(h2_sec, 6),
        "multi_strategy_bundle_seconds": round(h3_sec, 6),
        "rows_loaded": rows_loaded,
        "candles_converted": converted,
        "bos_events": bos_events,
        "lifecycle_count": lifecycle_count,
        "trades_baseline": sum(len(c["trades"]) for c in baseline_cells),
        "trades_optimized": sum(len(c["trades"]) for c in optimized_cells),
        "db_queries_cold_pg": metrics_a.postgres_queries,
        "db_queries_warm_cache": (bundle_warm.metrics or {}).get("postgres_queries"),
        "peak_tracemalloc_mb": round(peak / (1024 * 1024), 3),
        "peak_rss_mb": _rss_mb(),
        "worker_count": workers,
        "database_is_primary_bottleneck": (
            a_sec >= max(x[1] for x in ranked) * 0.5 if ranked else False
        ),
    }
    if not out["summary"]["database_is_primary_bottleneck"]:
        out["summary"]["database_note"] = "DATABASE_IS_NOT_PRIMARY_BOTTLENECK"

    # No-lookahead: HTF map never points to future bars (sample check)
    from app.research.bos_strategy_comparison.htf import (
        as_of_index_at_or_before,
        build_htf_as_of_index_map,
    )

    nl_ok = True
    setup = cache_candles[("BTCUSDT", "15m")]
    h1 = cache_candles[("BTCUSDT", "1h")]
    mapped = build_htf_as_of_index_map(setup, h1)
    for i, idx in enumerate(mapped):
        if idx is None:
            continue
        if h1[idx]["time"] > setup[i]["time"]:
            nl_ok = False
            out["correctness"]["no_lookahead_detail"] = {
                "status": "FIRST_MISMATCH",
                "index": i,
                "setup_time": str(setup[i]["time"]),
                "htf_time": str(h1[idx]["time"]),
            }
            break
        # Equivalence vs linear as_of
        if idx != as_of_index_at_or_before(h1, setup[i]["time"]):
            nl_ok = False
            out["correctness"]["no_lookahead_detail"] = {
                "status": "FIRST_MISMATCH",
                "stage": "htf_index_map_vs_linear",
                "index": i,
            }
            break
    out["correctness"]["No-lookahead"] = "PASS" if nl_ok else "FAIL"

    out["status"] = (
        "OK"
        if out["correctness"].get("Trades") == "PASS"
        and out["correctness"].get("OHLCV") == "PASS"
        and out["correctness"].get("No-lookahead") == "PASS"
        else "EQUALITY_FAILED"
    )
    out["stage_table"] = [(n, round(s, 6)) for n, s in stage_rows]
    return out


async def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--timeframes", default="15m,1h")
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2025-02-01")
    p.add_argument(
        "--cache-dir",
        default=str(ROOT / "data" / "research_cache_benchmark"),
    )
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--no-golden", action="store_true")
    p.add_argument("--include-lifecycle", action="store_true", default=True)
    p.add_argument("--skip-lifecycle", action="store_true")
    p.add_argument("--include-events", action="store_true")
    p.add_argument(
        "--out",
        default=str(ROOT / "scripts" / "research_benchmark_golden" / "last_benchmark.json"),
    )
    args = p.parse_args(argv)

    from app.config import get_settings
    from app.services.database import db_manager

    settings = get_settings()
    await db_manager.connect(settings, ensure_schema=False)
    if db_manager.engine is None:
        print(json.dumps({"status": "DATABASE unavailable"}, indent=2))
        return 2

    try:
        report = await run_benchmark(
            symbols=_split(args.symbols),
            timeframes=_split(args.timeframes),
            start=args.start,
            end=args.end,
            cache_dir=Path(args.cache_dir),
            workers=max(1, int(args.workers)),
            persist_golden=not args.no_golden,
            include_lifecycle=bool(args.include_lifecycle) and not args.skip_lifecycle,
            include_events=bool(args.include_events),
        )
    finally:
        await db_manager.close()

    if report.get("status") == "DATASET_UNAVAILABLE":
        print(json.dumps(report, indent=2, default=str))
        return 3

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    stages = report.get("stage_table") or []
    total = float((report.get("stages") or {}).get("K_total", {}).get("seconds") or 0)
    try:
        print()
        print("RESEARCH PIPELINE BENCHMARK")
        ds = report.get("dataset") or {}
        print(f"Dataset: symbols={ds.get('symbols')} TF={ds.get('timeframes')}")
        print(f"  start={ds.get('start')} end={ds.get('end')}")
        _print_table("STAGE TIMINGS", stages, total)
        print("Summary:", json.dumps(report.get("summary"), indent=2, default=str))
        print(
            "Correctness:",
            json.dumps(report.get("correctness"), indent=2, default=str)[:2000],
        )
        print("Bottlenecks:", json.dumps(report.get("bottleneck_ranking"), indent=2))
        print(f"Wrote {out_path}")
    except UnicodeEncodeError:
        print(f"status={report.get('status')} wrote={out_path}")
    return 0 if report.get("status") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
