#!/usr/bin/env python3
"""Sample backfill thrash metrics every 10s for >=10 minutes after warm boot."""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
OUT = Path("reports/full_system_performance_followup/backfill_metrics.json")


def get(path: str, timeout: float = 45.0) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode())


def main() -> None:
    duration_s = 600
    interval_s = 10
    samples: list[dict] = []
    t0 = time.time()
    print(f"sampling for {duration_s}s every {interval_s}s...", flush=True)
    while True:
        elapsed = time.time() - t0
        if elapsed > duration_s and samples:
            break
        row: dict = {"t": round(elapsed, 1)}
        try:
            t_h0 = time.perf_counter()
            health = get("/api/health", timeout=20)
            row["health_ms"] = round((time.perf_counter() - t_h0) * 1000, 1)
            row["ingestion"] = health.get("ingestion")
        except Exception as exc:  # noqa: BLE001
            row["health_err"] = str(exc)
        try:
            perf = get("/api/system/performance", timeout=30)
            pipe = perf.get("pipeline") or {}
            loop = perf.get("event_loop") or perf.get("loop") or {}
            row.update(
                {
                    "queue": pipe.get("backfill_queue_size"),
                    "complete": pipe.get("backfill_completed_series"),
                    "candles_written": pipe.get("backfill_candles_written"),
                    "backfill_requests": pipe.get("backfill_requests"),
                    "rest_rpm": pipe.get("rest_requests_last_minute"),
                    "rest_weight": pipe.get("rest_weight_last_minute"),
                    "rate_429": pipe.get("rate_limit_errors_last_minute"),
                    "db_writes": pipe.get("db_writes_total"),
                    "rss_mb": (perf.get("process") or {}).get("rss_mb"),
                    "event_loop_lag_ms": loop.get("lag_ms") or loop.get("event_loop_lag_ms"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            row["perf_err"] = str(exc)
        try:
            bf = get("/api/data/backfill", timeout=30)
            row.update(
                {
                    "bf_queue": bf.get("queue_size"),
                    "bf_pending": bf.get("pending"),
                    "bf_running": bf.get("running"),
                    "bf_failed": bf.get("failed"),
                    "bf_retry": bf.get("retry_wait"),
                    "bf_complete": bf.get("completed_series") or bf.get("complete"),
                    "bf_requests": bf.get("requests"),
                    "active_universe": bf.get("active_universe_count") or bf.get("report_universe"),
                    "enqueue_reasons": bf.get("enqueue_reasons"),
                    "blocked_queued": bf.get("enqueue_blocked_queued"),
                    "blocked_cooldown": bf.get("enqueue_blocked_cooldown"),
                    "blocked_window": bf.get("enqueue_blocked_window"),
                    "blocked_attempts": bf.get("enqueue_blocked_attempts"),
                    "stale_cooldowns": bf.get("stale_cooldowns_active"),
                    "q_depth_max": bf.get("queue_depth_max_recent"),
                    "q_depth_avg": bf.get("queue_depth_avg_recent"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            row["bf_err"] = str(exc)
        try:
            t_s0 = time.perf_counter()
            scr = get("/api/screener/futures?limit=50", timeout=60)
            row["screener_ms"] = round((time.perf_counter() - t_s0) * 1000, 1)
            row["screener_rows"] = scr.get("returned_count")
        except Exception as exc:  # noqa: BLE001
            row["screener_err"] = str(exc)
        samples.append(row)
        print(json.dumps({k: row.get(k) for k in (
            "t", "health_ms", "bf_queue", "bf_complete", "bf_requests", "rest_rpm",
            "blocked_queued", "blocked_cooldown", "enqueue_reasons", "screener_ms",
            "event_loop_lag_ms", "health_err", "bf_err"
        ) if k in row or row.get(k) is not None}), flush=True)
        if elapsed > duration_s:
            break
        time.sleep(interval_s)

    # Derived rates
    derived = {}
    if len(samples) >= 2:
        first, last = samples[0], samples[-1]
        dt_min = max((last["t"] - first["t"]) / 60.0, 1e-6)
        reasons0 = first.get("enqueue_reasons") or {}
        reasons1 = last.get("enqueue_reasons") or {}
        derived = {
            "duration_s": last["t"] - first["t"],
            "queue_start": first.get("bf_queue"),
            "queue_end": last.get("bf_queue"),
            "queue_max": max((s.get("bf_queue") or 0) for s in samples),
            "queue_min": min((s.get("bf_queue") or 0) for s in samples if s.get("bf_queue") is not None) if any(s.get("bf_queue") is not None for s in samples) else None,
            "requests_delta": (last.get("bf_requests") or 0) - (first.get("bf_requests") or 0),
            "requests_per_min": round(((last.get("bf_requests") or 0) - (first.get("bf_requests") or 0)) / dt_min, 2),
            "trailing_stale_delta": (reasons1.get("trailing_stale") or 0) - (reasons0.get("trailing_stale") or 0),
            "trailing_stale_per_min": round(
                ((reasons1.get("trailing_stale") or 0) - (reasons0.get("trailing_stale") or 0)) / dt_min, 2
            ),
            "blocked_queued_delta": (last.get("blocked_queued") or 0) - (first.get("blocked_queued") or 0),
            "blocked_cooldown_delta": (last.get("blocked_cooldown") or 0) - (first.get("blocked_cooldown") or 0),
            "health_ms_avg": round(
                sum(s["health_ms"] for s in samples if "health_ms" in s)
                / max(1, sum(1 for s in samples if "health_ms" in s)),
                1,
            ),
            "health_errors": sum(1 for s in samples if s.get("health_err")),
            "screener_ms_avg": round(
                sum(s["screener_ms"] for s in samples if "screener_ms" in s)
                / max(1, sum(1 for s in samples if "screener_ms" in s)),
                1,
            ),
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"derived": derived, "samples": samples}, indent=2), encoding="utf-8")
    print("wrote", OUT, "n=", len(samples), "derived=", json.dumps(derived), flush=True)


if __name__ == "__main__":
    main()
