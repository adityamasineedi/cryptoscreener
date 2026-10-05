#!/usr/bin/env python3
"""Warm API latency baseline (n=10 per endpoint). Transport-only measurement."""

from __future__ import annotations

import json
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
OUT = Path("reports/full_system_performance_followup/baseline_api_benchmarks.json")

ENDPOINTS = [
    ("screener", "/api/screener/futures?limit=50"),
    ("health", "/api/health"),
    ("chart_15m", "/api/charts/BTCUSDT/ohlcv?timeframe=15m&limit=200"),
    ("chart_1h", "/api/charts/BTCUSDT/ohlcv?timeframe=1h&limit=200"),
    ("coin", "/api/coin/BTCUSDT"),
    ("ohlcv_range", "/api/research/ohlcv-range?symbols=BTCUSDT,ETHUSDT,SOLUSDT&timeframes=1h,4h"),
]


def pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def one(path: str, timeout: float = 60.0) -> tuple[float, int, int, str | None]:
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
            body = r.read()
            ms = (time.perf_counter() - t0) * 1000.0
            return ms, r.status, len(body), None
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000.0
        return ms, 0, 0, str(exc)


def summarize(samples: list[dict]) -> dict:
    oks = [s for s in samples if s["error"] is None]
    times = sorted(s["ms"] for s in oks)
    sizes = [s["bytes"] for s in oks]
    return {
        "n": len(samples),
        "ok": len(oks),
        "error_count": len(samples) - len(oks),
        "average_ms": round(statistics.mean(times), 2) if times else None,
        "p50_ms": round(pct(times, 50), 2) if times else None,
        "p95_ms": round(pct(times, 95), 2) if times else None,
        "p99_ms": round(pct(times, 99), 2) if times else None,
        "maximum_ms": round(max(times), 2) if times else None,
        "payload_bytes_avg": int(statistics.mean(sizes)) if sizes else None,
        "errors": [s["error"] for s in samples if s["error"]],
    }


def main() -> None:
    # Warm discard
    for _, path in ENDPOINTS:
        one(path, timeout=90.0)

    results: dict = {"recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "n_per_endpoint": 10}
    for name, path in ENDPOINTS:
        samples = []
        print(f"benchmarking {name} ...", flush=True)
        for i in range(10):
            ms, status, nbytes, err = one(path)
            samples.append({"i": i, "ms": round(ms, 2), "status": status, "bytes": nbytes, "error": err})
            print(f"  {i}: {ms:.1f}ms status={status} bytes={nbytes} err={err}", flush=True)
        results[name] = {"path": path, "summary": summarize(samples), "samples": samples}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
