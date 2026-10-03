#!/usr/bin/env python3
"""Diagnose free sentiment providers (socialtickers + XOOMAR). No API key required."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from dotenv import load_dotenv

    if (ROOT / ".env").exists():
        load_dotenv(ROOT / ".env")
except ImportError:
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run() -> dict[str, Any]:
    from app.config import get_settings
    from app.ingestion.providers.free_social import (
        candidate_bases,
        get_free_social_client,
        reset_free_social_client_for_tests,
    )
    from app.ingestion.providers.sentiment import reset_sentiment_provider_for_tests

    get_settings.cache_clear()
    reset_sentiment_provider_for_tests()
    reset_free_social_client_for_tests()
    settings = get_settings()
    client = get_free_social_client(settings)

    print("Free Social Diagnostic (socialtickers + XOOMAR)")
    print("----------------------------------------------")
    print(f"Configured: {'YES' if client.configured else 'NO'}")
    print(f"API key required: NO")
    print(f"socialtickers: {client.st_base}{client._st_path}")  # noqa: SLF001
    print(f"XOOMAR:        {client.xo_base}{client._xo_path}")  # noqa: SLF001

    if not client.configured:
        print("Provider status: NOT_CONFIGURED")
        print("Enable sentiment.provider: free_social in config/providers.yaml")
        return {"provider_status": "NOT_CONFIGURED", "configured": False}

    await client.start()
    t0 = time.perf_counter()
    ok = await client.refresh_universe(force=True)
    latency_ms = (time.perf_counter() - t0) * 1000
    client.coverage_for_universe(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "UNKNOWNTOKENUSDT"]
    )
    diag = client.diagnostic(universe_size=4)

    print(f"Refresh OK: {ok}")
    print(f"Latency: {latency_ms:.0f} ms")
    print(f"socialtickers assets: {client.stats.socialtickers_received}")
    print(f"XOOMAR crypto assets: {client.stats.xoomar_received}")
    print(f"Union received: {client.stats.assets_received}")
    print()

    for label, binance in (("BTC", "BTCUSDT"), ("ETH", "ETHUSDT"), ("SOL", "SOLUSDT")):
        m = client.map_symbol(binance)
        fields = client.metrics_for_symbol(binance)
        print(f"{binance} -> {m.provider_symbol} ({m.mapping_status})")
        print(
            f"  dominance={fields['social_dominance'].value} "
            f"mentions={fields['mentions'].value} "
            f"engagement={fields['engagement'].value} "
            f"sentiment={fields['sentiment'].value} "
            f"({fields['sentiment'].status.value})"
        )

    unknown = client.metrics_for_symbol("UNKNOWNTOKENUSDT")
    print()
    print(
        f"UNKNOWNTOKENUSDT: {unknown['social_dominance'].status.value} "
        f"— {(unknown['social_dominance'].methodology or '')[:80]}"
    )
    print()
    print(f"Provider status: {diag.get('status')}")

    await client.close()
    return {
        "timestamp": _now(),
        "configured": True,
        "ok": ok,
        "latency_ms": round(latency_ms, 1),
        "diagnostic": diag,
        "btc_bases": candidate_bases("BTCUSDT"),
        "provider_status": diag.get("status") or ("HEALTHY" if ok else "UNAVAILABLE"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    result = asyncio.run(run())
    out = Path(__file__).resolve().parent / "diagnose_free_social_result.json"
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print()
    print(f"Wrote {out}")
    return 0 if result.get("provider_status") in ("LIVE", "HEALTHY", "CACHED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
