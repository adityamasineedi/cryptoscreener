#!/usr/bin/env python3
"""Isolated LunarCrush API v4 diagnostic.

Does NOT start the bot. Makes ONE (paginated) real request to coins/list/v2.
Never prints the API key. Never fabricates social metrics.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow running as `python backend/scripts/diagnose_lunarcrush.py`
ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from dotenv import load_dotenv

    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask_key(key: str) -> str:
    if not key:
        return "MISSING"
    return "PRESENT"


async def run_diagnostic(*, limit: int = 1000) -> dict[str, Any]:
    import httpx

    from app.config import get_settings
    from app.ingestion.providers.lunarcrush import (
        COINS_LIST_PATH,
        candidate_bases,
        get_lunarcrush_client,
        reset_lunarcrush_client_for_tests,
    )

    settings = get_settings()
    reset_lunarcrush_client_for_tests()
    client = get_lunarcrush_client(settings)
    cfg = settings.providers_config.get("sentiment") or {}

    result: dict[str, Any] = {
        "timestamp": _now(),
        "configured": client.configured,
        "enabled": client.enabled,
        "api_key": _mask_key(client.api_key),
        "base_url": client.base_url,
        "endpoint": COINS_LIST_PATH,
        "provider_yaml": {
            "enabled": cfg.get("enabled"),
            "provider": cfg.get("provider"),
            "api_key_env": cfg.get("api_key_env"),
        },
    }

    print("LunarCrush Diagnostic")
    print("---------------------")
    print(f"Configured: {'YES' if client.configured else 'NO'}")
    print(f"API key: {_mask_key(client.api_key)}")
    print(f"Endpoint: {COINS_LIST_PATH}")

    if not client.api_key:
        result["provider_status"] = "NOT_CONFIGURED"
        print("Provider status: NOT_CONFIGURED")
        print()
        print("LunarCrush provider is implemented but not configured.")
        print("Set:")
        print("  LUNARCRUSH_API_KEY=<real key>")
        print("Then rerun diagnostic.")
        return result

    await client.start()
    url = f"{client.base_url.rstrip('/')}{COINS_LIST_PATH}"
    params = {"limit": limit, "page": 0, "sort": "market_cap_rank"}
    headers = {
        "Authorization": f"Bearer {client.api_key}",
        "User-Agent": "CryptoScreener-Diagnose/0.1",
        "Accept": "application/json",
    }
    start = time.perf_counter()
    http_status = None
    latency_ms = None
    rate_headers: dict[str, str] = {}
    body: Any = None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as http:
            resp = await http.get(url, params=params, headers=headers)
            latency_ms = (time.perf_counter() - start) * 1000
            http_status = resp.status_code
            rate_headers = {
                k: v
                for k, v in resp.headers.items()
                if "rate" in k.lower()
                or k.lower() in ("retry-after", "x-ratelimit-remaining", "x-ratelimit-limit")
            }
            try:
                body = resp.json()
            except Exception:
                body = None
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - start) * 1000
        result.update(
            {
                "http": None,
                "latency_ms": round(latency_ms, 1),
                "error": str(exc.__class__.__name__),
                "provider_status": "NETWORK_ERROR",
            }
        )
        print(f"HTTP: (network error) {exc.__class__.__name__}")
        print(f"Latency: {latency_ms:.0f} ms")
        print("Provider status: NETWORK_ERROR")
        await client.close()
        return result
    finally:
        # Ensure we never leave key material in result
        pass

    print(f"HTTP: {http_status}")
    print(f"Latency: {latency_ms:.0f} ms")

    status_map = {
        200: "HEALTHY",
        401: "AUTH_FAILED",
        403: "PLAN_FORBIDDEN",
        429: "RATE_LIMITED",
    }
    provider_status = status_map.get(http_status or 0, "HTTP_ERROR")
    if http_status and http_status >= 500:
        provider_status = "SERVER_ERROR"

    assets = []
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        assets = [a for a in body["data"] if isinstance(a, dict)]

    print(f"Assets received: {len(assets)}")
    if rate_headers:
        print(f"Rate-limit headers: {rate_headers}")

    def find_asset(symbol: str) -> dict[str, Any] | None:
        for a in assets:
            if str(a.get("symbol") or "").upper() == symbol.upper():
                return a
        return None

    btc = find_asset("BTC")
    print()
    print("BTC mapping:")
    print(f"BTCUSDT -> {candidate_bases('BTCUSDT')[-1]}")
    print(f"Found: {'YES' if btc else 'NO'}")
    if btc:
        print(f"social_dominance: {btc.get('social_dominance')}")
        print(f"social_volume_24h: {btc.get('social_volume_24h')}")
        print(f"interactions_24h: {btc.get('interactions_24h')}")
        print(f"sentiment: {btc.get('sentiment')}")

    sol = find_asset("SOL")
    print()
    print("SOL mapping:")
    print("SOLUSDT -> SOL")
    print(f"Found: {'YES' if sol else 'NO'}")
    if sol:
        print(f"sentiment: {sol.get('sentiment')}")

    print()
    print(f"Provider status: {provider_status}")

    result.update(
        {
            "http": http_status,
            "latency_ms": round(latency_ms or 0, 1),
            "assets_received": len(assets),
            "rate_limit_headers": rate_headers,
            "provider_status": provider_status,
            "btc_found": btc is not None,
            "btc": (
                {
                    "social_dominance": btc.get("social_dominance"),
                    "social_volume_24h": btc.get("social_volume_24h"),
                    "interactions_24h": btc.get("interactions_24h"),
                    "sentiment": btc.get("sentiment"),
                    "id": btc.get("id"),
                    "symbol": btc.get("symbol"),
                }
                if btc
                else None
            ),
            "sol_found": sol is not None,
            "mapping_btcusdt": candidate_bases("BTCUSDT"),
        }
    )
    await client.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose LunarCrush API connectivity")
    parser.add_argument("--limit", type=int, default=1000, help="page limit (default 1000)")
    args = parser.parse_args()
    result = asyncio.run(run_diagnostic(limit=args.limit))
    out_path = Path(__file__).resolve().parent / "diagnose_lunarcrush_result.json"
    # Never write API key
    safe = {k: v for k, v in result.items() if "key" not in k.lower() or k == "api_key"}
    if safe.get("api_key") not in ("PRESENT", "MISSING", None):
        safe["api_key"] = "PRESENT" if result.get("api_key") == "PRESENT" else "MISSING"
    out_path.write_text(json.dumps(safe, indent=2), encoding="utf-8")
    print()
    print(f"Wrote {out_path}")
    status = result.get("provider_status")
    if status in ("HEALTHY",):
        return 0
    if status == "NOT_CONFIGURED":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
