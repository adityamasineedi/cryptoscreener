#!/usr/bin/env python3
"""Snapshot backfill backlog metrics + event-loop into followup artifacts."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

BASE = os.environ.get("FOLLOWUP_API_BASE", "http://127.0.0.1:8001")
OUT = Path("reports/full_system_performance_followup")


def get(path: str, timeout: float = 60.0) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode())


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    bf = get("/api/data/backfill", timeout=90)
    try:
        perf = get("/api/system/performance", timeout=30)
    except Exception as exc:  # noqa: BLE001
        perf = {"error": str(exc)}
    keys = [
        "backlog_state",
        "gap_backlog_count",
        "oldest_gap_age_seconds",
        "newest_gap_age_seconds",
        "enqueue_rate_per_minute",
        "completion_rate_per_minute",
        "failed_gap_count",
        "retry_count",
        "estimated_completion_seconds",
        "active_backfill_jobs",
        "cooldown_suppressed_count",
        "duplicate_suppressed_count",
        "queue_size",
        "priority_policy",
        "running",
        "retry_wait",
        "failed",
    ]
    snap = {k: bf.get(k) for k in keys}
    snap["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    snap["event_loop"] = (perf or {}).get("event_loop")
    snap["process"] = (perf or {}).get("process")
    (OUT / "backfill_backlog_metrics.json").write_text(
        json.dumps(snap, indent=2), encoding="utf-8"
    )
    print(json.dumps(snap, indent=2)[:2000])


if __name__ == "__main__":
    main()
