"""Acceptance checks for DATABASE_ENABLED + REDIS_ENABLED persistence.

Run while the API is up with Redis/DB enabled:
  python scripts/verify_persistence.py
"""

from __future__ import annotations

import json
import sys
import urllib.request


BASE = "http://127.0.0.1:8000"


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode())


def main() -> int:
    health = get("/api/health")
    providers = get("/api/health/providers")
    coverage = get("/api/data/coverage")
    backfill = get("/api/data/backfill")
    oi = get("/api/data/oi-coverage")
    liq = get("/api/data/liquidations/diagnostic")
    perf = get("/api/system/performance")

    report = {
        "redis": health.get("redis"),
        "database": health.get("database"),
        "symbols": health.get("symbols_loaded"),
        "tickers_live": health.get("tickers_live"),
        "persistence": providers.get("persistence"),
        "db_health": providers.get("database"),
        "redis_health": providers.get("redis"),
        "backfill": backfill,
        "oi_pct": oi.get("pct_available"),
        "oi_coverage": oi.get("coverage"),
        "liquidations_diag": {
            k: liq.get(k)
            for k in (
                "connection_status",
                "raw_messages",
                "events_seen",
                "parser_errors",
                "last_message_time",
            )
        },
        "coverage_pct": {
            "market_cap": (coverage.get("market_cap") or {}).get("pct_available"),
            "tvl": (coverage.get("tvl") or {}).get("pct_available"),
            "ohlcv": {
                tf: (v or {}).get("pct_available")
                for tf, v in (coverage.get("ohlcv") or {}).items()
            },
            "historical_performance": (
                (coverage.get("historical_performance") or {}).get("performance_1d") or {}
            ).get("pct_available"),
        },
        "performance_pipeline": perf.get("pipeline"),
        "providers": [
            {
                "provider": p.get("provider") or p.get("name"),
                "enabled": p.get("enabled"),
                "healthy": p.get("healthy"),
                "status": p.get("status"),
                "requests": p.get("requests"),
                "429_count": p.get("429_count"),
            }
            for p in (providers.get("providers") or [])
        ],
    }
    print(json.dumps(report, indent=2))

    redis_ok = str(health.get("redis")).lower() in {"ok", "connected"}
    db_ok = str(health.get("database")).lower() in {"ok", "connected"}
    if not redis_ok:
        print("WARN: Redis not CONNECTED", file=sys.stderr)
    if not db_ok:
        print("WARN: Database not CONNECTED", file=sys.stderr)
    return 0 if redis_ok and db_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
