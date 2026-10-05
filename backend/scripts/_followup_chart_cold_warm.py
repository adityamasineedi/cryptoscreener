#!/usr/bin/env python3
"""Cold + warm chart validation with full restart orchestration helpers.

Cold mode expects a freshly started API (empty/partial memory). Warm mode
assumes ingestion=live and hydrate finished.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("FOLLOWUP_API_BASE", "http://127.0.0.1:8000")
OUT = Path("reports/full_system_performance_followup")


def get(path: str, timeout: float = 60.0) -> tuple[dict | None, float, str | None]:
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
            body = json.loads(r.read().decode())
        return body, (time.perf_counter() - t0) * 1000.0, None
    except Exception as exc:  # noqa: BLE001
        return None, (time.perf_counter() - t0) * 1000.0, str(exc)


def wait_ready(max_s: float = 300.0) -> dict:
    deadline = time.time() + max_s
    last_err = None
    while time.time() < deadline:
        body, ms, err = get("/api/health", timeout=15)
        if body and body.get("ingestion") in ("live", "starting", "ok"):
            return {"health": body, "health_ms": ms, "ready_at": time.time()}
        last_err = err or (body or {}).get("ingestion")
        time.sleep(2)
    raise RuntimeError(f"API not ready: {last_err}")


def snapshot_state() -> dict:
    health, _, herr = get("/api/health", timeout=20)
    perf, _, perr = get("/api/system/performance", timeout=30)
    bf, _, berr = get("/api/data/backfill", timeout=45)
    pipe = (perf or {}).get("pipeline") or {}
    return {
        "health_err": herr,
        "perf_err": perr,
        "bf_err": berr,
        "ingestion": (health or {}).get("ingestion"),
        "hydrate_active": pipe.get("hydrate_active"),
        "backfill_active": pipe.get("backfill_active"),
        "backfill_queue": (bf or {}).get("queue_size") or pipe.get("backfill_queue_size"),
        "hydrated_candles": (bf or {}).get("hydrated_candles"),
        "completed_series": (bf or {}).get("completed_series") or (bf or {}).get("complete"),
        "event_loop": (perf or {}).get("event_loop"),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def analyze_candles(body: dict) -> dict:
    candles = body.get("candles") or []
    times = []
    for c in candles:
        ot = c.get("open_time")
        times.append(ot if isinstance(ot, str) else str(ot))
    dup = len(times) - len(set(times))
    sorted_asc = times == sorted(times)
    return {
        "duplicate_count": dup,
        "sorted_ascending": sorted_asc,
        "first_candle_time": times[0] if times else None,
        "last_candle_time": times[-1] if times else None,
        "closed_count": body.get("closed_count"),
        "open_included": body.get("open_included"),
        "contract_note": body.get("contract_note"),
    }


def sample_chart(symbol: str, timeframe: str, limit: int = 200) -> dict:
    path = f"/api/charts/{symbol}/ohlcv?timeframe={timeframe}&limit={limit}"
    body, ms, err = get(path, timeout=60)
    row = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "symbol": symbol,
        "timeframe": timeframe,
        "requested_limit": limit,
        "latency_ms": round(ms, 2),
        "error": err,
        "path": path,
    }
    if body is None:
        row.update(
            {
                "returned_count": 0,
                "source": None,
                "fallback_used": None,
                "silent_empty": False,
            }
        )
        return row
    meta = analyze_candles(body)
    returned = body.get("returned_count") or body.get("count") or 0
    row.update(
        {
            "returned_count": returned,
            "source": body.get("source"),
            "fallback_used": body.get("fallback_used"),
            "first_candle_time": meta["first_candle_time"],
            "last_candle_time": meta["last_candle_time"],
            "duplicate_count": meta["duplicate_count"],
            "sorted_ascending": meta["sorted_ascending"],
            "closed_count": meta["closed_count"],
            "open_included": meta["open_included"],
            "contract_note": meta["contract_note"],
            "api_error_field": body.get("error"),
            "silent_empty": returned == 0 and not body.get("error") and err is None,
        }
    )
    return row


def run_phase(phase: str, n: int = 10) -> dict:
    state_before = snapshot_state()
    cases = [
        ("BTCUSDT", "15m"),
        ("BTCUSDT", "1h"),
    ]
    # Immediate pair once
    immediate = [sample_chart(s, tf) for s, tf in cases]
    # Wait for hydrate if cold
    if phase == "cold":
        deadline = time.time() + 240
        while time.time() < deadline:
            st = snapshot_state()
            if st.get("hydrate_active") is False and (
                (st.get("hydrated_candles") or 0) > 0 or st.get("ingestion") == "live"
            ):
                break
            time.sleep(3)
    state_after_hydrate = snapshot_state()
    post_hydrate = [sample_chart(s, tf) for s, tf in cases]
    repeats = {f"{s}_{tf}": [] for s, tf in cases}
    for _ in range(n):
        for s, tf in cases:
            repeats[f"{s}_{tf}"].append(sample_chart(s, tf))
            time.sleep(0.2)
    return {
        "phase": phase,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "state_before": state_before,
        "state_after_hydrate": state_after_hydrate,
        "immediate": immediate,
        "post_hydrate": post_hydrate,
        "repeats": repeats,
        "contract": {
            "limit_applies_to": "closed_candles",
            "open_tip_may_add": 1,
            "explanation": (
                "requested_limit=200 returns up to 200 closed bars; when an open/"
                "forming candle exists it is appended, so returned_count may be 201. "
                "This is deliberate live-tip semantics, not an off-by-one bug."
            ),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["cold", "warm", "both"], default="warm")
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    wait_ready()
    if args.phase in ("cold", "both"):
        cold = run_phase("cold", n=args.n)
        (OUT / "chart_cold_validation.json").write_text(
            json.dumps(cold, indent=2), encoding="utf-8"
        )
        print("wrote chart_cold_validation.json")
    if args.phase in ("warm", "both"):
        # Ensure hydrate settled
        time.sleep(2)
        warm = run_phase("warm", n=args.n)
        (OUT / "chart_warm_validation.json").write_text(
            json.dumps(warm, indent=2), encoding="utf-8"
        )
        print("wrote chart_warm_validation.json")


if __name__ == "__main__":
    main()
