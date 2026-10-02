"""Acceptance snapshot for data-coverage optimization phase.

Does not claim COMPLETE unless measured goals are met.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

API = "http://127.0.0.1:8000"


async def get(client: httpx.AsyncClient, path: str) -> dict:
    r = await client.get(f"{API}{path}", timeout=30.0)
    r.raise_for_status()
    return r.json()


async def main() -> int:
    out: dict = {"phase": "FINAL DATA COVERAGE OPTIMIZATION", "complete": False}
    try:
        async with httpx.AsyncClient() as client:
            health = await get(client, "/api/health")
            cov = await get(client, "/api/data/coverage")
            bf = await get(client, "/api/data/backfill")
            oi = await get(client, "/api/data/oi-coverage")
            liq = await get(client, "/api/data/liquidations/diagnostic")
            providers = await get(client, "/api/health/providers")
            stats = await get(client, "/api/system/stats")
    except Exception as exc:  # noqa: BLE001
        print(f"LIVE_SYSTEM: UNREACHABLE ({exc})")
        print("Run backend with USE_REAL_DATA=true, then re-run this script.")
        return 2

    symbols = cov.get("symbols") or 0
    ohlcv = cov.get("ohlcv") or {}
    goals = cov.get("coverage_goals") or {}
    out["TESTS"] = "run pytest separately"
    out["DATA_COVERAGE"] = {
        "symbols": symbols,
        "ohlcv": {
            tf: {
                "available": (ohlcv.get(tf) or {}).get("available")
                or (ohlcv.get(tf) or {}).get("covered"),
                "total": (ohlcv.get(tf) or {}).get("total"),
                "pct": (ohlcv.get(tf) or {}).get("pct_available"),
            }
            for tf in ("1m", "5m", "15m", "1h", "4h", "1d")
        },
        "market_cap": cov.get("market_cap") or cov.get("fundamentals", {}).get("market_cap"),
        "tvl": cov.get("tvl") or cov.get("fundamentals", {}).get("tvl"),
        "goals": goals,
    }
    out["BACKFILL_STATUS"] = {
        "total_jobs": bf.get("total_jobs"),
        "pending": bf.get("pending"),
        "running": bf.get("running"),
        "complete": bf.get("complete"),
        "failed": bf.get("failed"),
        "retry_wait": bf.get("retry_wait"),
        "estimated_progress": bf.get("estimated_progress"),
        "adaptive": bf.get("adaptive"),
    }
    out["OI_COVERAGE"] = {
        "pct_available": oi.get("pct_available"),
        "goal_met": oi.get("goal_met"),
        "coverage": oi.get("coverage"),
        "adaptive": oi.get("adaptive"),
    }
    out["PROVIDER_STATUS"] = {
        "providers": providers.get("providers"),
        "fundamentals": providers.get("fundamentals"),
        "fundamental_reasons": cov.get("fundamental_reasons"),
    }
    out["DATABASE_STATUS"] = stats.get("database")
    out["REDIS_STATUS"] = stats.get("redis")
    out["LIQUIDATION_STATUS"] = {
        "liquidation_status": liq.get("liquidation_status"),
        "connection_status": liq.get("connection_status"),
        "raw_messages": liq.get("raw_messages"),
        "events_seen": liq.get("events_seen"),
        "dns_resolution": liq.get("dns_resolution"),
        "tls_connection": liq.get("tls_connection"),
        "bytes_received": liq.get("bytes_received"),
        "reconnect_count": liq.get("reconnect_count"),
        "note": liq.get("note"),
    }
    out["NO_FAKE_DATA_STATUS"] = {
        "use_real_data": health.get("use_real_data"),
        "ingestion": health.get("ingestion") or stats.get("ingestion"),
        "rule": "WAITING never converted to zero; no fabricated liquidations/performance",
    }
    met = bool(goals.get("all_measured_targets_met"))
    out["complete"] = met
    out["verdict"] = "COMPLETE" if met else "IN_PROGRESS — targets not yet measured as met"

    print(json.dumps(out, indent=2, default=str))
    path = Path(__file__).resolve().parents[2] / "COVERAGE_ACCEPTANCE.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {path}")
    print(f"VERDICT: {out['verdict']}")
    return 0 if met else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
