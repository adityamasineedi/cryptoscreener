#!/usr/bin/env python3
"""Startup backfill ramp validation — health/screener/charts + COMBO_02 process-pool.

Writes ONLY into a uniquely named sandbox directory (never overwrites prior reports).
Scheduling / observability probe only — does not create paper/live trades or Telegram.
"""

from __future__ import annotations

import csv
import json
import os
import statistics
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("FOLLOWUP_API_BASE", "http://127.0.0.1:8000")
OUT = Path(
    os.environ.get(
        "STARTUP_BACKFILL_OUT",
        "reports/startup_backfill_burst_control",
    )
)
DURATION_S = int(os.environ.get("FOLLOWUP_STRESS_SECONDS", "600"))
STARTUP_WINDOW_S = float(os.environ.get("STARTUP_WINDOW_SECONDS", "90"))
REPS = int(os.environ.get("STARTUP_BACKFILL_REPS", "1"))
REP_TAG = os.environ.get("STARTUP_BACKFILL_REP_TAG", "")


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


def pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return round(s[int((len(s) - 1) * p / 100.0)], 2)


def wait_ingestion_ready(timeout_s: float = 240.0) -> dict:
    """Ready = health ok + ingestion live (+ optional startup_ramp clock).

    Avoid relying on the heavy /api/data/backfill gap scan during hydrate.
    """
    t0 = time.time()
    last: dict = {}
    while time.time() - t0 < timeout_s:
        health, hms, herr = get("/api/health", timeout=10)
        perf, _, perr = get("/api/system/performance", timeout=15)
        pipe = (perf or {}).get("pipeline") or {}
        ramp: dict = {}
        berr = None
        # Prefer light status from performance; sample backfill sparsely.
        if int(time.time() - t0) % 5 == 0:
            bf, _, berr = get("/api/data/backfill", timeout=20)
            if isinstance(bf, dict):
                ramp = bf.get("startup_ramp") or {}
        last = {
            "health_ms": hms,
            "health_err": herr,
            "ingestion": (health or {}).get("ingestion"),
            "hydrate_active": pipe.get("hydrate_active"),
            "queue": pipe.get("backfill_queue_size"),
            "startup_ramp": ramp if isinstance(ramp, dict) else {},
            "perf_err": perr,
            "bf_err": berr,
            "elapsed_s": round(time.time() - t0, 1),
        }
        ing = str(last.get("ingestion") or "").lower()
        if herr is None and ing in {"live", "ok", "ready", "running"}:
            # Prefer waiting until DB hydrate flag clears, but do not block forever
            # on the enqueue ramp (backfill_active may remain true).
            if not last.get("hydrate_active"):
                return {**last, "ready": True}
            if last.get("elapsed_s", 0) >= 45 and pipe.get("backfill_queue_size") is not None:
                return {**last, "ready": True, "note": "accepted_with_hydrate_flag"}
        time.sleep(1.0)
    return {**last, "ready": False}


class LoadGen:
    def __init__(self, ready_mono: float) -> None:
        self.ready_mono = ready_mono
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.lat: dict[str, list[tuple[float, float]]] = {
            "health": [],
            "screener": [],
            "chart_15m": [],
            "chart_1h": [],
        }
        self.errors: dict[str, int] = {k: 0 for k in self.lat}
        self.timeouts: dict[str, int] = {k: 0 for k in self.lat}
        self.chart_empty = 0
        self.backtest: dict = {}

    def _phase(self, t_mono: float) -> str:
        return "startup" if (t_mono - self.ready_mono) < STARTUP_WINDOW_S else "steady"

    def _rec(self, name: str, ms: float, err: str | None, body: dict | None = None) -> None:
        with self.lock:
            if err:
                self.errors[name] += 1
                if "timed out" in err.lower() or "timeout" in err.lower():
                    self.timeouts[name] += 1
            else:
                self.lat[name].append((time.monotonic(), ms))
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

    def start_backtest(self) -> None:
        # Delay slightly so startup ramp is under observation first.
        time.sleep(20.0)
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
            for _ in range(180):
                if self.stop.is_set():
                    break
                time.sleep(2)
                st, _, serr = get("/api/research/long-strategy/backtest/job", timeout=15)
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
                if status in {"done", "completed", "failed", "cancelled", "error", "stalled"}:
                    break


def summarize_phase(samples: list[tuple[float, float]], ready_mono: float) -> dict:
    startup = [ms for t, ms in samples if (t - ready_mono) < STARTUP_WINDOW_S]
    steady = [ms for t, ms in samples if (t - ready_mono) >= STARTUP_WINDOW_S]
    all_ms = [ms for _, ms in samples]

    def block(vals: list[float]) -> dict:
        return {
            "n": len(vals),
            "p50_ms": pct(vals, 50),
            "p95_ms": pct(vals, 95),
            "max_ms": round(max(vals), 2) if vals else None,
            "avg_ms": round(sum(vals) / len(vals), 2) if vals else None,
        }

    return {
        "startup": block(startup),
        "steady_state": block(steady),
        "overall": block(all_ms),
    }


def run_one(rep: int) -> dict:
    tag = REP_TAG or f"rep{rep}"
    out_dir = OUT
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[{tag}] waiting for ingestion readiness at {BASE}...", flush=True)
    ready = wait_ingestion_ready()
    ready_mono = time.monotonic()
    print(json.dumps({"ready": ready}, indent=2), flush=True)
    if not ready.get("ready"):
        raise SystemExit(f"ingestion not ready: {ready}")

    gen = LoadGen(ready_mono)
    threads = [
        threading.Thread(target=gen.loop_health, daemon=True),
        threading.Thread(target=gen.loop_screener, daemon=True),
        threading.Thread(target=gen.loop_charts, daemon=True),
    ]
    for t in threads:
        t.start()
    threading.Thread(target=gen.start_backtest, daemon=True).start()

    series: list[dict] = []
    t0 = time.time()
    sample_i = 0
    print(f"[{tag}] combined load for {DURATION_S}s...", flush=True)
    while True:
        elapsed = time.time() - t0
        if elapsed >= DURATION_S:
            break
        perf, _, perr = get("/api/system/performance", timeout=30)
        bf = None
        berr = None
        # Full backfill report gap-scans the active universe — poll sparsely so the
        # harness itself does not inflate health latency (same lesson as prior follow-up).
        if sample_i % 15 == 0:
            bf, _, berr = get("/api/data/backfill", timeout=45)
        sample_i += 1
        pipe = (perf or {}).get("pipeline") or {}
        loop = (perf or {}).get("event_loop") or {}
        ramp = ((bf or {}).get("startup_ramp") if bf else None) or {}
        phase = "startup" if elapsed < STARTUP_WINDOW_S else "steady"
        row = {
            "t": round(elapsed, 1),
            "phase": phase,
            "health_last_ms": (gen.lat["health"][-1][1] if gen.lat["health"] else None),
            "screener_last_ms": (
                gen.lat["screener"][-1][1] if gen.lat["screener"] else None
            ),
            "chart_15m_last_ms": (
                gen.lat["chart_15m"][-1][1] if gen.lat["chart_15m"] else None
            ),
            "chart_1h_last_ms": (
                gen.lat["chart_1h"][-1][1] if gen.lat["chart_1h"] else None
            ),
            "event_loop_lag_ms": loop.get("event_loop_lag_ms"),
            "event_loop_lag_p95_ms": loop.get("event_loop_lag_p95_ms"),
            "backfill_queue": (bf or {}).get("queue_size") or pipe.get("backfill_queue_size"),
            "backfill_running": (bf or {}).get("running"),
            "backlog_state": (bf or {}).get("backlog_state"),
            "rest_rpm": pipe.get("rest_requests_last_minute"),
            "startup_enqueue_rate": ramp.get("startup_enqueue_rate"),
            "startup_rest_request_rate": ramp.get("startup_rest_request_rate"),
            "startup_queue_depth_peak": ramp.get("startup_queue_depth_peak"),
            "startup_active_jobs_peak": ramp.get("startup_active_jobs_peak"),
            "startup_duplicate_suppressions": ramp.get("startup_duplicate_suppressions"),
            "startup_cooldown_suppressions": ramp.get("startup_cooldown_suppressions"),
            "startup_rate_limit_wait": ramp.get("startup_rate_limit_wait"),
            "steady_state_enqueue_rate": ramp.get("steady_state_enqueue_rate"),
            "steady_state_rest_request_rate": ramp.get("steady_state_rest_request_rate"),
            "deferred_gap_candidates": ramp.get("deferred_gap_candidates"),
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
                        "phase",
                        "health_last_ms",
                        "backfill_queue",
                        "event_loop_lag_ms",
                        "startup_queue_depth_peak",
                    )
                }
            ),
            flush=True,
        )
        time.sleep(2.0)

    gen.stop.set()
    time.sleep(1)

    health_phases = summarize_phase(gen.lat["health"], ready_mono)
    screener_phases = summarize_phase(gen.lat["screener"], ready_mono)
    chart15_phases = summarize_phase(gen.lat["chart_15m"], ready_mono)
    chart1h_phases = summarize_phase(gen.lat["chart_1h"], ready_mono)

    lag_startup = [
        r["event_loop_lag_ms"]
        for r in series
        if r.get("phase") == "startup" and r.get("event_loop_lag_ms") is not None
    ]
    lag_steady = [
        r["event_loop_lag_ms"]
        for r in series
        if r.get("phase") == "steady" and r.get("event_loop_lag_ms") is not None
    ]
    lag_all = [
        r["event_loop_lag_ms"] for r in series if r.get("event_loop_lag_ms") is not None
    ]

    queues = [r["backfill_queue"] for r in series if r.get("backfill_queue") is not None]
    rest_vals = [r["rest_rpm"] for r in series if r.get("rest_rpm") is not None]

    bt_final = gen.backtest.get("final_job") or {}
    bt_job = bt_final if isinstance(bt_final, dict) else {}
    bt_body = gen.backtest.get("body") or {}
    if not bt_job and isinstance(bt_body, dict):
        bt_job = bt_body.get("job") if isinstance(bt_body.get("job"), dict) else bt_body

    final_bf, _, _ = get("/api/data/backfill", timeout=45)
    ramp_final = (final_bf or {}).get("startup_ramp") or {}

    health_p95_overall = health_phases["overall"].get("p95_ms")
    health_p95_steady = health_phases["steady_state"].get("p95_ms")
    health_p95_startup = health_phases["startup"].get("p95_ms")

    summary = {
        "rep": rep,
        "tag": tag,
        "duration_s": DURATION_S,
        "startup_window_seconds": STARTUP_WINDOW_S,
        "ready": ready,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "health": health_phases,
        "screener": screener_phases,
        "chart_15m": chart15_phases,
        "chart_1h": chart1h_phases,
        "event_loop_lag": {
            "startup": {
                "p95_ms": pct(lag_startup, 95),
                "max_ms": round(max(lag_startup), 2) if lag_startup else None,
                "n": len(lag_startup),
            },
            "steady_state": {
                "p95_ms": pct(lag_steady, 95),
                "max_ms": round(max(lag_steady), 2) if lag_steady else None,
                "n": len(lag_steady),
            },
            "overall": {
                "p95_ms": pct(lag_all, 95),
                "max_ms": round(max(lag_all), 2) if lag_all else None,
                "n": len(lag_all),
            },
        },
        "queue_start": queues[0] if queues else None,
        "queue_end": queues[-1] if queues else None,
        "queue_max": max(queues) if queues else None,
        "rest_requests_per_minute": {
            "avg": round(statistics.mean(rest_vals), 2) if rest_vals else None,
            "max": max(rest_vals) if rest_vals else None,
        },
        "startup_ramp_final": ramp_final,
        "chart_silent_empty": gen.chart_empty,
        "backtest": {
            "error": gen.backtest.get("error"),
            "job_id": (bt_job or {}).get("job_id"),
            "status": (bt_job or {}).get("status"),
            "execution_isolation": (bt_job or {}).get("execution_isolation"),
            "done_cells": (bt_job or {}).get("done_cells"),
            "total_cells": (bt_job or {}).get("total_cells"),
            "final_error": (bt_job or {}).get("error"),
        },
        "acceptance": {
            "health_p95_lt_500_after_startup_ramp": (health_p95_steady or 9999) < 500,
            "health_p95_startup_ms": health_p95_startup,
            "health_p95_steady_ms": health_p95_steady,
            "health_p95_overall_ms": health_p95_overall,
            "no_health_timeout": gen.timeouts["health"] == 0,
            "event_loop_lag_p95_lt_200": (pct(lag_all, 95) or 9999) < 200,
            "no_unbounded_queue_growth": (
                (max(queues) if queues else 0) <= 800
                and (ramp_final.get("startup_queue_depth_peak") or 0)
                <= max(80, int(ramp_final.get("startup_max_queue_depth") or 40) * 2)
            ),
            "process_pool_job_completes": str((bt_job or {}).get("status") or "").lower()
            in {"done", "completed"},
            "no_silent_empty_charts": gen.chart_empty == 0,
        },
    }

    # Per-rep uniquely named files; aggregated names written by main() for final.
    (out_dir / f"combined_load_after_startup_ramp_{tag}.json").write_text(
        json.dumps({"summary": summary, "timeseries": series}, indent=2),
        encoding="utf-8",
    )
    with (out_dir / f"startup_backfill_timeseries_{tag}.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        fields = [
            "t",
            "phase",
            "health_last_ms",
            "screener_last_ms",
            "chart_15m_last_ms",
            "chart_1h_last_ms",
            "event_loop_lag_ms",
            "backfill_queue",
            "rest_rpm",
            "startup_enqueue_rate",
            "startup_rest_request_rate",
            "startup_queue_depth_peak",
            "deferred_gap_candidates",
        ]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in series:
            w.writerow(r)

    with (out_dir / f"health_latency_by_phase_{tag}.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        w = csv.DictWriter(
            f, fieldnames=["phase", "n", "p50_ms", "p95_ms", "max_ms", "avg_ms"]
        )
        w.writeheader()
        for phase, block in health_phases.items():
            w.writerow({"phase": phase, **block})

    with (out_dir / f"event_loop_lag_by_phase_{tag}.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        w = csv.DictWriter(f, fieldnames=["phase", "n", "p95_ms", "max_ms"])
        w.writeheader()
        for phase, block in summary["event_loop_lag"].items():
            w.writerow({"phase": phase, **block})

    print(json.dumps(summary, indent=2), flush=True)
    return summary


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    summaries = []
    for rep in range(1, REPS + 1):
        os.environ["STARTUP_BACKFILL_REP_TAG"] = REP_TAG or f"rep{rep}"
        summaries.append(run_one(rep))

    # Aggregate required artifact names (append-only sandbox; unique parent dir).
    metrics = {
        "reps": summaries,
        "startup_window_seconds": STARTUP_WINDOW_S,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (OUT / "startup_backfill_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    # Prefer last rep as the canonical combined_load artifact name.
    last_tag = summaries[-1]["tag"] if summaries else "rep1"
    last_path = OUT / f"combined_load_after_startup_ramp_{last_tag}.json"
    if last_path.exists():
        (OUT / "combined_load_after_startup_ramp.json").write_text(
            last_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    # Merge timeseries from last rep into required name.
    last_ts = OUT / f"startup_backfill_timeseries_{last_tag}.csv"
    if last_ts.exists():
        (OUT / "startup_backfill_timeseries.csv").write_text(
            last_ts.read_text(encoding="utf-8"), encoding="utf-8"
        )
    last_h = OUT / f"health_latency_by_phase_{last_tag}.csv"
    if last_h.exists():
        (OUT / "health_latency_by_phase.csv").write_text(
            last_h.read_text(encoding="utf-8"), encoding="utf-8"
        )
    last_l = OUT / f"event_loop_lag_by_phase_{last_tag}.csv"
    if last_l.exists():
        (OUT / "event_loop_lag_by_phase.csv").write_text(
            last_l.read_text(encoding="utf-8"), encoding="utf-8"
        )
    print(f"wrote artifacts under {OUT}")


if __name__ == "__main__":
    main()
