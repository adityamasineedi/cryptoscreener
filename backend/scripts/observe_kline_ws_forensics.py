"""Observe one kline shard under load (diagnostic only)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

from app.ingestion.klines import generate_kline_streams, shard_streams
from app.ingestion.kline_ws_manager import KlineShardConnection
from app.ingestion.ws_forensics import ws_forensics


async def fetch_symbols(limit: int = 120) -> list[str]:
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    syms = [
        s["symbol"]
        for s in data.get("symbols") or []
        if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"
    ]
    return sorted(syms)[:limit]


async def main(hold_s: float = 90.0, symbols: int = 120) -> int:
    syms = await fetch_symbols(symbols)
    streams = generate_kline_streams(syms, ["1m", "5m", "15m", "1h"])
    shards = shard_streams(streams, max_streams_per_connection=900)
    shard0 = shards[0]
    print(
        json.dumps(
            {
                "symbols": len(syms),
                "total_streams": len(streams),
                "shard0_streams": shard0.stream_count,
                "shard_count": len(shards),
            }
        ),
        flush=True,
    )

    candles = {"n": 0}

    async def on_candle(_c):
        candles["n"] += 1

    await ws_forensics.event_loop.start()
    conn = KlineShardConnection(
        name="observe_kline_shard_0",
        base_ws="wss://fstream.binance.com",
        streams=shard0.streams,
        handler=on_candle,
    )
    await conn.start()
    print(f"holding {hold_s:.0f}s...", flush=True)
    await asyncio.sleep(hold_s)
    st = conn.status()
    report = ws_forensics.build_report()
    print(
        json.dumps(
            {
                "candles": candles["n"],
                "status": {
                    k: st.get(k)
                    for k in (
                        "connection_id",
                        "connected",
                        "reader_running",
                        "streams",
                        "subscribed",
                        "message_count",
                        "candle_count",
                        "disconnect_count",
                        "reconnect_count",
                        "disconnect_reason",
                        "disconnect_evidence",
                        "close_code",
                        "last_message_at",
                    )
                },
                "disconnect_summary": report["disconnect_summary"],
                "event_loop": report["event_loop"],
                "issues": report["issues_candidates"],
                "forensics": st.get("forensics"),
            },
            indent=2,
            default=str,
        )
    )
    await conn.stop()
    await ws_forensics.event_loop.stop()
    return 0


if __name__ == "__main__":
    hold = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
    nsym = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    raise SystemExit(asyncio.run(main(hold, nsym)))
