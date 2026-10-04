#!/usr/bin/env python3
"""Diagnose LONG BTCUSDT 15m COMBO_02 UI-path backtest latency.

Runs the same loader + combination walk as the UI job with phase timing.
Does NOT start the in-memory UI job service (avoids duplicate long runs).

Usage (from backend/):
  python -m scripts.diagnose_backtest_15m --range small
  python -m scripts.diagnose_backtest_15m --range medium
  python -m scripts.diagnose_backtest_15m --range large --explain-sql
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure backend root on path when invoked as a script.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RANGES = {
    "small": ("2024-01-01", "2024-03-31"),
    "medium": ("2023-01-01", "2023-12-31"),
    "large": ("2022-10-01", "2024-12-31"),
}


async def _explain_sql(symbol: str, timeframe: str, start: str, end: str) -> dict[str, Any]:
    from sqlalchemy import text

    from app.research.query_utils import resolve_date_bounds
    from app.services.database import db_manager

    if db_manager.engine is None:
        return {"status": "DATABASE_UNAVAILABLE"}
    bounds = resolve_date_bounds(start, end)
    params = {
        "symbol": symbol.upper(),
        "tf": timeframe,
        "start_ts": bounds["start"],
        "end_ts": bounds["end_exclusive"],
    }
    sql = """
        SELECT time, open, high, low, close, volume
        FROM ohlcv
        WHERE symbol = :symbol AND timeframe = :tf
          AND time >= :start_ts AND time < :end_ts
        ORDER BY time ASC
    """
    explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"
    t0 = time.perf_counter()
    async with db_manager.engine.begin() as conn:
        result = await conn.execute(text(explain_sql), params)
        plan_row = result.fetchone()
        count_r = await conn.execute(
            text(
                """
                SELECT COUNT(*), MIN(time), MAX(time)
                FROM ohlcv
                WHERE symbol = :symbol AND timeframe = :tf
                  AND time >= :start_ts AND time < :end_ts
                """
            ),
            params,
        )
        crow = count_r.fetchone()
    return {
        "status": "OK",
        "query_duration_seconds": round(time.perf_counter() - t0, 6),
        "rows": int(crow[0]) if crow else 0,
        "first_timestamp": crow[1].isoformat() if crow and crow[1] else None,
        "last_timestamp": crow[2].isoformat() if crow and crow[2] else None,
        "plan": plan_row[0] if plan_row else None,
    }


async def run_range(
    *,
    label: str,
    start: str,
    end: str,
    symbol: str,
    timeframe: str,
    risk: float,
    leverage: float,
    explain_sql: bool,
) -> dict[str, Any]:
    from app.research.backtest_timing import PhaseTimer, emit_phase
    from app.research.bos_combinations import get_combination
    from app.research.combination_backtest import run_combination_backtest
    from app.research.config import ResearchConfig
    from app.research.service import _load_htf_candles_for_combo, _load_research_candles
    from app.signals.config import SignalConfig

    job_id = f"diag_{label}_{int(time.time())}"
    timer = PhaseTimer()
    phases: list[dict[str, Any]] = []
    heartbeats: list[dict[str, Any]] = []

    def on_progress(p: dict[str, Any]) -> None:
        heartbeats.append(dict(p))
        phases.append(
            emit_phase(
                str(p.get("phase") or "HEARTBEAT"),
                job_id=job_id,
                symbol=symbol,
                timeframe=timeframe,
                elapsed_seconds=float(p.get("elapsed_seconds") or timer.elapsed()),
                bars_processed=int(p.get("bars_processed") or 0),
                total_bars=int(p.get("total_bars") or 0),
                trades=int(p.get("trades") or 0),
            )
        )

    tracemalloc.start()
    combo = get_combination("COMBO_02")
    assert combo is not None
    rcfg = ResearchConfig()
    warmup = max(int(rcfg.min_bars), 100)

    t_db = time.perf_counter()
    candles, eval_start, load_meta = await _load_research_candles(
        symbol,
        timeframe,
        limit=1200,
        start_date=start,
        end_date=end,
        warmup_bars=warmup,
    )
    db_s = time.perf_counter() - t_db

    t_htf = time.perf_counter()
    c1h, c4h = await _load_htf_candles_for_combo(
        symbol,
        require_htf=True,
        setup_timeframe=timeframe,
        limit=1200,
        start_date=start,
        end_date=end,
        warmup_bars=warmup,
    )
    htf_s = time.perf_counter() - t_htf

    explain = None
    if explain_sql:
        explain = await _explain_sql(symbol, timeframe, start, end)

    t_bt = time.perf_counter()
    out = await asyncio.to_thread(
        run_combination_backtest,
        symbol,
        timeframe,
        candles,
        combo,
        signal_config=SignalConfig(),
        research_config=rcfg,
        direction_filter="LONG",
        index_start=eval_start if eval_start > 0 else None,
        candles_1h=c1h,
        candles_4h=c4h,
        progress_callback=on_progress,
        job_id=job_id,
    )
    bt_s = time.perf_counter() - t_bt
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    first = candles[0]["time"] if candles else None
    last = candles[-1]["time"] if candles else None
    report = {
        "job_id": job_id,
        "label": label,
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": "LONG",
        "strategy_id": "COMBO_02_V1",
        "start": start,
        "end": end,
        "risk": risk,
        "leverage": leverage,
        "phase_timings": {
            "database_query_seconds": round(db_s, 6),
            "htf_loading_seconds": round(htf_s, 6),
            "htf_precompute_seconds": out.get("htf_precompute_seconds"),
            "structure_scan_backtest_seconds": round(bt_s, 6),
            "total_elapsed_seconds": round(timer.elapsed(), 6),
        },
        "rows_loaded": len(candles),
        "bars_used": out.get("candles_processed"),
        "trades_generated": len(out.get("trades") or []),
        "sample_size": out.get("sample_size"),
        "status": out.get("status"),
        "actual_start": first.isoformat() if hasattr(first, "isoformat") else str(first),
        "actual_end": last.isoformat() if hasattr(last, "isoformat") else str(last),
        "load_meta": load_meta,
        "htf_1h_rows": len(c1h or []),
        "htf_4h_rows": len(c4h or []),
        "htf_cache_entries": out.get("htf_cache_entries"),
        "memory_current_mb": round(current / (1024 * 1024), 3),
        "memory_peak_mb": round(peak / (1024 * 1024), 3),
        "heartbeat_count": len(heartbeats),
        "first_heartbeat_bars": heartbeats[0].get("bars_processed") if heartbeats else None,
        "last_heartbeat_bars": heartbeats[-1].get("bars_processed") if heartbeats else None,
        "explain": explain,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    return report


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--range",
        choices=list(RANGES) + ["all"],
        default="small",
        help="Date window preset (default: small control range)",
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--risk", type=float, default=0.015)
    parser.add_argument("--leverage", type=float, default=2.0)
    parser.add_argument("--explain-sql", action="store_true")
    parser.add_argument(
        "--out",
        default="",
        help="Optional JSON output path",
    )
    args = parser.parse_args()

    from app.config import get_settings
    from app.services.database import db_manager

    settings = get_settings()
    await db_manager.connect(settings, ensure_schema=False)
    try:
        labels = list(RANGES) if args.range == "all" else [args.range]
        reports = []
        for label in labels:
            start, end = RANGES[label]
            print(f"\n=== RANGE {label}: {start} -> {end} ===", flush=True)
            rep = await run_range(
                label=label,
                start=start,
                end=end,
                symbol=args.symbol,
                timeframe=args.timeframe,
                risk=args.risk,
                leverage=args.leverage,
                explain_sql=bool(args.explain_sql),
            )
            reports.append(rep)
            print(json.dumps(rep, indent=2, default=str), flush=True)
        if args.out:
            Path(args.out).write_text(
                json.dumps(reports if len(reports) > 1 else reports[0], indent=2, default=str),
                encoding="utf-8",
            )
    finally:
        await db_manager.close()
    return 0


if __name__ == "__main__":
    # Avoid launching if DATABASE_ENABLED is false without notice.
    if os.getenv("DATABASE_ENABLED", "").lower() in ("0", "false", "no"):
        print("DATABASE_ENABLED is false — enable Postgres for this diagnostic.", file=sys.stderr)
    raise SystemExit(asyncio.run(main()))
