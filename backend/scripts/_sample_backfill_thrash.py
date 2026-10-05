#!/usr/bin/env python3
"""Sample backfill thrash metrics for ~N minutes (warm boot)."""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path


def get(path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=30) as r:
        return json.loads(r.read().decode())


def main() -> None:
    duration_s = 600  # 10 minutes
    interval_s = 30
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
            health = get("/api/health")
            row["health_ms"] = round((time.perf_counter() - t_h0) * 1000, 1)
            row["ingestion"] = health.get("ingestion")
        except Exception as exc:  # noqa: BLE001
            row["health_err"] = str(exc)
        try:
            perf = get("/api/system/performance")
            pipe = perf.get("pipeline") or {}
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
                }
            )
        except Exception as exc:  # noqa: BLE001
            row["perf_err"] = str(exc)
        try:
            bf = get("/api/data/backfill")
            row["bf_queue"] = bf.get("queue_size")
            row["bf_pending"] = bf.get("pending")
            row["bf_running"] = bf.get("running")
            row["bf_failed"] = bf.get("failed")
            row["bf_retry"] = bf.get("retry_wait")
            row["bf_complete"] = bf.get("completed_series") or bf.get("complete")
            row["bf_requests"] = bf.get("requests")
            row["active_universe"] = bf.get("active_universe_count")
            row["enqueue_reasons"] = bf.get("enqueue_reasons")
            row["blocked_queued"] = bf.get("enqueue_blocked_queued")
            row["blocked_cooldown"] = bf.get("enqueue_blocked_cooldown")
            row["blocked_window"] = bf.get("enqueue_blocked_window")
            row["blocked_attempts"] = bf.get("enqueue_blocked_attempts")
            row["stale_cooldowns"] = bf.get("stale_cooldowns_active")
            row["q_depth_max"] = bf.get("queue_depth_max_recent")
            row["q_depth_avg"] = bf.get("queue_depth_avg_recent")
        except Exception as exc:  # noqa: BLE001
            row["bf_err"] = str(exc)
        try:
            t_s0 = time.perf_counter()
            scr = get("/api/screener/futures?limit=50")
            row["screener_ms"] = round((time.perf_counter() - t_s0) * 1000, 1)
            row["screener_rows"] = scr.get("returned_count")
        except Exception as exc:  # noqa: BLE001
            row["screener_err"] = str(exc)
        samples.append(row)
        print(json.dumps(row), flush=True)
        if elapsed > duration_s:
            break
        time.sleep(interval_s)

    out = Path("reports/_backfill_thrash_sample.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(samples, indent=2), encoding="utf-8")
    print("wrote", out, "n=", len(samples), flush=True)


if __name__ == "__main__":
    main()
