#!/usr/bin/env python3
"""Combined-load stress: health/screener/charts/coin + optional backtest, >=10 minutes."""

from __future__ import annotations

import csv
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("FOLLOWUP_API_BASE", "http://127.0.0.1:8000")
OUT = Path("reports/full_system_performance_followup")
DURATION_S = int(os.environ.get("FOLLOWUP_STRESS_SECONDS", "600"))
OUT_TAG = os.environ.get("FOLLOWUP_STRESS_TAG", f"{DURATION_S}s")


def get(path: str, timeout: float = 30.0) -> tuple[dict | None, float, str | None]:
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
            body = json.loads(r.read().decode())
        return body, (time.perf_counter() - t0) * 1000.0, None
    except Exception as exc:  # noqa: BLE001
        return None, (time.perf_counter() - t0) * 1000.0, str(exc)


def post(path: str, payload: dict, timeout: float = 30.0) -> tuple[dict | None, float, str | None]:
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(
            f"{BASE}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode())
        return body, (time.perf_counter() - t0) * 1000.0, None
    except Exception as exc:  # noqa: BLE001
        return None, (time.perf_counter() - t0) * 1000.0, str(exc)


class LoadGen:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.lat: dict[str, list[float]] = {
            "health": [],
            "screener": [],
            "chart_15m": [],
            "chart_1h": [],
            "coin": [],
        }
        self.errors: dict[str, int] = {k: 0 for k in self.lat}
        self.timeouts: dict[str, int] = {k: 0 for k in self.lat}
        self.chart_empty = 0
        self.backtest: dict = {}

    def _rec(self, name: str, ms: float, err: str | None, body: dict | None = None) -> None:
        with self.lock:
            if err:
                self.errors[name] += 1
                if "timed out" in err.lower() or "timeout" in err.lower():
                    self.timeouts[name] += 1
            else:
                self.lat[name].append(ms)
            if name.startswith("chart") and body is not None:
                n = body.get("returned_count") or body.get("count") or 0
                if n == 0 and not body.get("error"):
                    self.chart_empty += 1

    def loop_health(self) -> None:
        while not self.stop.wait(2.0):
            body, ms, err = get("/api/health", timeout=10)
            self._rec("health", ms, err, body)

    def loop_screener(self) -> None:
        while not self.stop.wait(5.0):
            body, ms, err = get("/api/screener/futures?limit=50", timeout=45)
            self._rec("screener", ms, err, body)

    def loop_charts(self) -> None:
        while not self.stop.wait(5.0):
            b15, ms15, e15 = get(
                "/api/charts/BTCUSDT/ohlcv?timeframe=15m&limit=200", timeout=45
            )
            self._rec("chart_15m", ms15, e15, b15)
            b1h, ms1h, e1h = get(
                "/api/charts/BTCUSDT/ohlcv?timeframe=1h&limit=200", timeout=45
            )
            self._rec("chart_1h", ms1h, e1h, b1h)

    def loop_coin(self) -> None:
        while not self.stop.wait(15.0):
            body, ms, err = get("/api/coin/BTCUSDT", timeout=45)
            self._rec("coin", ms, err, body)

    def start_backtest(self) -> None:
        # Research job only — does not create paper/live trades or Telegram.
        # Delay so backfill enqueue / hydrate settle before CPU isolation starts.
        time.sleep(45.0)
        self.backtest = {"start_ms": None, "error": "not_started", "body": None, "final_job": None}
        body, ms, err = post(
            "/api/research/long-strategy/backtest/start",
            {
                "symbols": ["BTCUSDT"],
                "timeframes": ["1h"],
                "setup_timeframe": "1h",
                "combination_id": "COMBO_02",
                "direction": "LONG",
                "start_date": "2025-07-01",
                "end_date": "2026-09-30",
            },
            timeout=20,
        )
        job = body.get("job") if isinstance(body, dict) else body
        job_err = err
        self.backtest = {
            "start_ms": ms,
            "error": err,
            "body": body,
            "final_job": job,
        }
        if body and not err:
            for _ in range(120):
                if self.stop.is_set():
                    break
                time.sleep(2)
                st, _, serr = get(
                    "/api/research/long-strategy/backtest/job", timeout=15
                )
                if serr:
                    job_err = serr
                    continue
                job = st or job
                status = str((st or {}).get("status") or "").lower()
                self.backtest = {
                    "start_ms": ms,
                    "error": job_err,
                    "body": body,
                    "final_job": job,
                }
                if status in {
                    "done",
                    "completed",
                    "failed",
                    "cancelled",
                    "error",
                    "stalled",
                }:
                    break
        self.backtest = {
            "start_ms": ms,
            "error": job_err,
            "body": body,
            "final_job": job,
        }


def pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return round(s[int((len(s) - 1) * p / 100.0)], 2)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    gen = LoadGen()
    threads = [
        threading.Thread(target=gen.loop_health, daemon=True),
        threading.Thread(target=gen.loop_screener, daemon=True),
        threading.Thread(target=gen.loop_charts, daemon=True),
        threading.Thread(target=gen.loop_coin, daemon=True),
    ]
    for t in threads:
        t.start()
    threading.Thread(target=gen.start_backtest, daemon=True).start()

    series: list[dict] = []
    t0 = time.time()
    print(f"combined load for {DURATION_S}s...", flush=True)
    sample_i = 0
    while True:
        elapsed = time.time() - t0
        if elapsed >= DURATION_S:
            break
        perf, _, perr = get("/api/system/performance", timeout=30)
        # Full backfill report gap-scans the active universe — poll sparsely so the
        # stress harness itself does not starve health.
        bf, berr = None, None
        if sample_i % 3 == 0:
            bf, _, berr = get("/api/data/backfill", timeout=45)
        sample_i += 1
        pipe = (perf or {}).get("pipeline") or {}
        loop = (perf or {}).get("event_loop") or {}
        proc = (perf or {}).get("process") or {}
        row = {
            "t": round(elapsed, 1),
            "health_last_ms": (gen.lat["health"][-1] if gen.lat["health"] else None),
            "screener_last_ms": (gen.lat["screener"][-1] if gen.lat["screener"] else None),
            "chart_15m_last_ms": (gen.lat["chart_15m"][-1] if gen.lat["chart_15m"] else None),
            "chart_1h_last_ms": (gen.lat["chart_1h"][-1] if gen.lat["chart_1h"] else None),
            "coin_last_ms": (gen.lat["coin"][-1] if gen.lat["coin"] else None),
            "event_loop_lag_ms": loop.get("event_loop_lag_ms"),
            "event_loop_lag_p95_ms": loop.get("event_loop_lag_p95_ms"),
            "backfill_queue": (bf or {}).get("queue_size") or pipe.get("backfill_queue_size"),
            "backfill_running": (bf or {}).get("running"),
            "backfill_retry": (bf or {}).get("retry_wait"),
            "backlog_state": (bf or {}).get("backlog_state"),
            "gap_backlog_count": (bf or {}).get("gap_backlog_count"),
            "rest_rpm": pipe.get("rest_requests_last_minute"),
            "cpu_percent": proc.get("cpu_percent"),
            "rss_mb": proc.get("rss_mb"),
            "hydrate_active": pipe.get("hydrate_active"),
            "perf_err": perr,
            "bf_err": berr,
            "errors": dict(gen.errors),
            "timeouts": dict(gen.timeouts),
        }
        series.append(row)
        print(
            json.dumps(
                {
                    k: row[k]
                    for k in (
                        "t",
                        "health_last_ms",
                        "screener_last_ms",
                        "backfill_queue",
                        "backlog_state",
                        "event_loop_lag_ms",
                    )
                }
            ),
            flush=True,
        )
        time.sleep(10)

    gen.stop.set()
    time.sleep(1)

    def summarize(name: str) -> dict:
        vals = gen.lat[name]
        return {
            "n": len(vals),
            "p50_ms": pct(vals, 50),
            "p95_ms": pct(vals, 95),
            "max_ms": round(max(vals), 2) if vals else None,
            "avg_ms": round(sum(vals) / len(vals), 2) if vals else None,
            "errors": gen.errors[name],
            "timeouts": gen.timeouts[name],
        }

    queues = [r["backfill_queue"] for r in series if r.get("backfill_queue") is not None]
    bt_body = gen.backtest.get("body") or {}
    bt_final = gen.backtest.get("final_job") or {}
    bt_job = bt_final if isinstance(bt_final, dict) else {}
    if not bt_job and isinstance(bt_body, dict):
        bt_job = bt_body.get("job") if isinstance(bt_body.get("job"), dict) else bt_body
    summary = {
        "duration_s": DURATION_S,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "endpoints": {k: summarize(k) for k in gen.lat},
        "health_p95_ok": (summarize("health").get("p95_ms") or 9999) < 500,
        "health_timeouts": gen.timeouts["health"],
        "chart_silent_empty": gen.chart_empty,
        "queue_start": queues[0] if queues else None,
        "queue_end": queues[-1] if queues else None,
        "queue_max": max(queues) if queues else None,
        "queue_min": min(queues) if queues else None,
        "backtest": {
            "error": gen.backtest.get("error"),
            "job_id": (bt_job or {}).get("job_id"),
            "status": (bt_job or {}).get("status"),
            "start_ms": gen.backtest.get("start_ms"),
            "execution_isolation": (bt_job or {}).get("execution_isolation"),
            "final_status": (bt_job or {}).get("status"),
            "final_error": (bt_job or {}).get("error"),
            "done_cells": (bt_job or {}).get("done_cells"),
            "total_cells": (bt_job or {}).get("total_cells"),
        },
        "acceptance": {
            "health_p95_lt_500": (summarize("health").get("p95_ms") or 9999) < 500,
            "no_health_timeout": gen.timeouts["health"] == 0,
            "no_silent_empty_charts": gen.chart_empty == 0,
            "backtest_completed": str((bt_job or {}).get("status") or "").lower()
            in {"done", "completed"},
        },
    }
    # Prefer explicit tag so re-runs do not clobber prior baselines.
    if OUT_TAG and OUT_TAG != f"{DURATION_S}s":
        stress_name = f"combined_load_stress_{OUT_TAG}.json"
        ts_name = f"combined_load_timeseries_{OUT_TAG}.csv"
    elif DURATION_S >= 600:
        stress_name = "combined_load_stress.json"
        ts_name = "combined_load_timeseries.csv"
    else:
        stress_name = f"combined_load_stress_{OUT_TAG}.json"
        ts_name = f"combined_load_timeseries_{OUT_TAG}.csv"
    (OUT / stress_name).write_text(
        json.dumps({"summary": summary, "timeseries": series}, indent=2), encoding="utf-8"
    )
    with (OUT / ts_name).open("w", newline="", encoding="utf-8") as f:
        fields = [
            "t",
            "health_last_ms",
            "screener_last_ms",
            "chart_15m_last_ms",
            "chart_1h_last_ms",
            "coin_last_ms",
            "event_loop_lag_ms",
            "backfill_queue",
            "backfill_running",
            "backlog_state",
            "rest_rpm",
            "cpu_percent",
            "rss_mb",
        ]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in series:
            w.writerow(r)
    print(json.dumps(summary, indent=2))
    print(f"wrote {stress_name}")


if __name__ == "__main__":
    main()
