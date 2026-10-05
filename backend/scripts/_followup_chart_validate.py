#!/usr/bin/env python3
"""Validate chart OHLCV warm sources (memory/postgres/rest metadata)."""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
OUT_DIR = Path("reports/full_system_performance_followup")


def get(path: str, timeout: float = 60.0) -> tuple[dict, float]:
    t0 = time.perf_counter()
    with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
        body = json.loads(r.read().decode())
    return body, (time.perf_counter() - t0) * 1000.0


def run_case(label: str, path: str, n: int = 10) -> dict:
    samples = []
    for i in range(n):
        try:
            body, ms = get(path)
            samples.append(
                {
                    "i": i,
                    "ms": round(ms, 2),
                    "source": body.get("source"),
                    "returned_count": body.get("returned_count") or body.get("count"),
                    "requested_limit": body.get("requested_limit"),
                    "fallback_used": body.get("fallback_used"),
                    "deduplicated": body.get("deduplicated"),
                    "sorted": body.get("sorted"),
                    "first_timestamp": body.get("first_timestamp"),
                    "last_timestamp": body.get("last_timestamp"),
                    "error": body.get("error"),
                    "ok": True,
                }
            )
        except Exception as exc:  # noqa: BLE001
            samples.append({"i": i, "ok": False, "error": str(exc)})
    ok = [s for s in samples if s.get("ok")]
    return {
        "label": label,
        "path": path,
        "n": n,
        "ok": len(ok),
        "errors": n - len(ok),
        "avg_ms": round(sum(s["ms"] for s in ok) / len(ok), 2) if ok else None,
        "min_returned": min((s["returned_count"] or 0) for s in ok) if ok else None,
        "max_returned": max((s["returned_count"] or 0) for s in ok) if ok else None,
        "sources": sorted({s.get("source") for s in ok}),
        "samples": samples,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # wait briefly for live
    for _ in range(30):
        try:
            h, _ = get("/api/health", timeout=10)
            if h.get("ingestion") == "live":
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)

    warm = {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phase": "warm",
        "cases": [
            run_case("btc_15m", "/api/charts/BTCUSDT/ohlcv?timeframe=15m&limit=200"),
            run_case("btc_1h", "/api/charts/BTCUSDT/ohlcv?timeframe=1h&limit=200"),
        ],
    }
    out = OUT_DIR / "chart_warm_validation.json"
    out.write_text(json.dumps(warm, indent=2), encoding="utf-8")
    print(json.dumps({c["label"]: {"avg_ms": c["avg_ms"], "min_ret": c["min_returned"], "sources": c["sources"]} for c in warm["cases"]}, indent=2))
    print("wrote", out)


if __name__ == "__main__":
    main()
