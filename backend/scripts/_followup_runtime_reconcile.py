#!/usr/bin/env python3
"""COMBO_02 runtime reconciliation without cProfile overhead."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import time
from pathlib import Path

OUT = Path("reports/full_system_performance_followup")
START, END = "2025-07-01", "2026-09-30"
SYMBOL = "BTCUSDT"


def _digest_trades(trades: list[dict]) -> str:
    h = hashlib.sha256()
    for t in trades:
        h.update(str(t.get("signal_time") or t.get("entry_time")).encode())
        h.update(str(t.get("exit_time")).encode())
        h.update(str(t.get("entry_price")).encode())
        h.update(str(t.get("exit_price")).encode())
        h.update(str(t.get("stop_price")).encode())
    return h.hexdigest()[:24]


async def _one_run(run_id: str, *, cache_state: str) -> dict:
    from app.config import get_settings
    from app.research.bos_combinations import get_combination
    from app.research.combination_backtest import run_combination_backtest
    from app.research.config import ResearchConfig
    from app.research.service import _load_research_candles
    from app.services.database import db_manager

    settings = get_settings()
    if not db_manager.enabled or db_manager.engine is None:
        await db_manager.connect(settings)

    combo = get_combination("COMBO_02")
    assert combo is not None

    t_load0 = time.perf_counter()
    c1h, _, meta1 = await _load_research_candles(
        SYMBOL, "1h", start_date=START, end_date=END, use_research_cache=True
    )
    c4h, _, meta4 = await _load_research_candles(
        SYMBOL, "4h", start_date=START, end_date=END, use_research_cache=True
    )
    load_s = time.perf_counter() - t_load0

    h = hashlib.sha256()
    for c in c1h:
        h.update(str(c.get("open_time") or c.get("time")).encode())
        h.update(str(c.get("close")).encode())
    dataset_fp = h.hexdigest()[:32]

    rss_mb = None
    try:
        import psutil

        rss_mb = round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001
        pass

    t0 = time.perf_counter()
    out = run_combination_backtest(
        symbol=SYMBOL,
        timeframe="1h",
        candles=c1h,
        combination=combo,
        research_config=ResearchConfig(),
        candles_4h=c4h,
    )
    strategy_s = time.perf_counter() - t0
    trades = list(out.get("trades") or [])
    result = out.get("result") or {}
    return {
        "run_id": run_id,
        "dataset_fingerprint": dataset_fp,
        "configuration_fingerprint": (
            result.get("configuration_fingerprint")
            or out.get("configuration_fingerprint")
            or "COMBO_02"
        ),
        "requested_range": f"{START}..{END}",
        "actual_range": {
            "1h_first": (c1h[0].get("open_time") if c1h else None),
            "1h_last": (c1h[-1].get("open_time") if c1h else None),
            "src_1h": meta1.get("source") or meta1.get("candle_source"),
            "src_4h": meta4.get("source") or meta4.get("candle_source"),
        },
        "bar_count": len(c1h),
        "bar_count_4h": len(c4h),
        "trade_count": len(trades),
        "trade_digest": _digest_trades(trades),
        "analytics_enabled": False,
        "profiler_enabled": False,
        "cache_state": cache_state,
        "load_seconds": round(load_s, 3),
        "strategy_runtime_seconds": round(strategy_s, 3),
        "total_runtime_seconds": round(load_s + strategy_s, 3),
        "rss_mb": rss_mb,
    }


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {}
    s = sorted(vals)
    n = len(s)
    return {
        "average": round(sum(s) / n, 3),
        "p50": round(s[int(0.50 * (n - 1))], 3),
        "p95": round(s[int(0.95 * (n - 1))], 3),
        "minimum": round(s[0], 3),
        "maximum": round(s[-1], 3),
        "n": n,
    }


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    runs: list[dict] = []

    # Cold-ish: first process connect + first loads (still parquet warm on disk)
    for i in range(5):
        print(f"cold-ish run {i+1}/5", flush=True)
        runs.append(await _one_run(f"cold_{i+1}", cache_state="process_cold_disk_warm"))

    # Warm process iterations
    for i in range(5):
        print(f"warm run {i+1}/5", flush=True)
        runs.append(await _one_run(f"warm_{i+1}", cache_state="process_warm"))

    cold = [r for r in runs if r["run_id"].startswith("cold_")]
    warm = [r for r in runs if r["run_id"].startswith("warm_")]
    summary = {
        "label": "COMBO_02_BTCUSDT_10968_runtime_reconciliation",
        "prior_audit_seconds": 32.8,
        "prior_profile_seconds": 47.0,
        "bar_count_expected": 10968,
        "analytics_enabled": False,
        "profiler_enabled": False,
        "cold": _stats([r["strategy_runtime_seconds"] for r in cold]),
        "warm": _stats([r["strategy_runtime_seconds"] for r in warm]),
        "warm_total": _stats([r["total_runtime_seconds"] for r in warm]),
        "fingerprints_stable": len({r["dataset_fingerprint"] for r in runs}) == 1
        and len({r["configuration_fingerprint"] for r in runs}) == 1,
        "trade_counts": sorted({r["trade_count"] for r in runs}),
        "trade_digests": sorted({r["trade_digest"] for r in runs}),
        "bar_counts": sorted({r["bar_count"] for r in runs}),
        "reconciliation_notes": [
            "32.8s audit was combination_backtest wall without cProfile on medium case",
            "47s figure included cProfile instrumentation overhead (~same bar count)",
            "This harness disables profiler; warm strategy_runtime is the comparable metric",
            "Disk parquet cache is warm across cold-ish process runs in this harness",
        ],
        "runs": runs,
    }
    (OUT / "backtest_runtime_reconciliation.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    csv_path = OUT / "backtest_runtime_runs.csv"
    fields = [
        "run_id",
        "dataset_fingerprint",
        "configuration_fingerprint",
        "bar_count",
        "trade_count",
        "analytics_enabled",
        "profiler_enabled",
        "cache_state",
        "strategy_runtime_seconds",
        "total_runtime_seconds",
        "rss_mb",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in runs:
            w.writerow({k: r.get(k) for k in fields})
    print(json.dumps({k: summary[k] for k in ("cold", "warm", "trade_counts", "bar_counts")}, indent=2))
    print("wrote", OUT / "backtest_runtime_reconciliation.json")

    from app.services.database import db_manager

    await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
