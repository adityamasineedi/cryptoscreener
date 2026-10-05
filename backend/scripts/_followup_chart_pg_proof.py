#!/usr/bin/env python3
"""Prove Postgres chart fallback path offline + race for cold memory window."""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
from pathlib import Path

OUT = Path("reports/full_system_performance_followup")
BASE = os.environ.get("FOLLOWUP_API_BASE", "http://127.0.0.1:8001")


def get(path: str, timeout: float = 30.0):
    t0 = time.perf_counter()
    with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
        body = json.loads(r.read().decode())
    return body, (time.perf_counter() - t0) * 1000.0


async def postgres_tail_proof() -> dict:
    from app.config import get_settings
    from app.research.postgres_ohlcv import load_ohlcv_series_tail
    from app.services.database import db_manager

    settings = get_settings()
    await db_manager.connect(settings)
    out = {}
    for tf in ("15m", "1h"):
        t0 = time.perf_counter()
        rows = await load_ohlcv_series_tail("BTCUSDT", tf, limit=200)
        ms = (time.perf_counter() - t0) * 1000
        out[tf] = {
            "rows": len(rows or []),
            "latency_ms": round(ms, 2),
            "first": (rows[0]["time"].isoformat() if rows else None),
            "last": (rows[-1]["time"].isoformat() if rows else None),
            "sufficient_for_limit_200": len(rows or []) >= 200,
        }
    await db_manager.close()
    return out


def race_until_sources(max_s: float = 120.0) -> list[dict]:
    """Poll charts rapidly; capture earliest sources after readiness."""
    samples = []
    deadline = time.time() + max_s
    ready = False
    while time.time() < deadline:
        try:
            h, _ = get("/api/health", timeout=5)
            if h.get("ingestion") == "live":
                ready = True
        except Exception:  # noqa: BLE001
            if not ready:
                time.sleep(0.5)
                continue
        if not ready:
            time.sleep(0.5)
            continue
        for tf in ("15m", "1h"):
            try:
                body, ms = get(f"/api/charts/BTCUSDT/ohlcv?timeframe={tf}&limit=200", timeout=20)
                samples.append(
                    {
                        "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "timeframe": tf,
                        "source": body.get("source"),
                        "fallback_used": body.get("fallback_used"),
                        "returned_count": body.get("returned_count") or body.get("count"),
                        "closed_count": body.get("closed_count"),
                        "open_included": body.get("open_included"),
                        "latency_ms": round(ms, 2),
                        "error": body.get("error"),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                samples.append({"t": time.time(), "timeframe": tf, "error": str(exc)})
        # Stop once we have both TFs from memory after seeing any non-memory, or after 20 samples
        if len(samples) >= 20:
            break
        time.sleep(0.5)
    return samples


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pg = await postgres_tail_proof()
    # Merge into cold validation artifact
    cold_path = OUT / "chart_cold_validation.json"
    cold = {}
    if cold_path.exists():
        cold = json.loads(cold_path.read_text(encoding="utf-8"))
    cold["postgres_tail_offline_proof"] = pg
    cold["cold_window_note"] = (
        "Lifespan blocks HTTP until orchestrator.start returns; by first /api/health=live "
        "hydrate often already finished for majors. Offline PG tail proves fallback data "
        "availability; live cold source capture requires racing chart requests in the first "
        "seconds after startup complete."
    )
    cold["race_samples"] = race_until_sources(60)
    cold_path.write_text(json.dumps(cold, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"postgres": pg, "race_n": len(cold["race_samples"])}, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
