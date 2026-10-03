"""Short live WS forensics observation (diagnostic only; no signal changes).

Connects ticker + markPrice array streams via WebSocketConnection forensics
and prints disconnect/reconnect/event-loop evidence after a hold period.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingestion.binance_futures_ws import market_ws_url
from app.ingestion.ws_forensics import ws_forensics
from app.ingestion.ws_manager import WebSocketManager


async def main(hold_s: float = 90.0) -> int:
    base = "wss://fstream.binance.com"
    mgr = WebSocketManager()
    counts = {"ticker": 0, "mark": 0}

    async def on_ticker(data):
        counts["ticker"] += 1

    async def on_mark(data):
        counts["mark"] += 1

    await ws_forensics.event_loop.start()
    await mgr.ensure(
        name="observe_ticker",
        url=market_ws_url(base, "!ticker@arr"),
        handler=on_ticker,
        stream_type="ticker",
        expected_streams=1,
    )
    await mgr.ensure(
        name="observe_mark",
        url=market_ws_url(base, "!markPrice@arr@1s"),
        handler=on_mark,
        stream_type="mark_price",
        expected_streams=1,
    )

    print(f"holding {hold_s:.0f}s for live observation...", flush=True)
    await asyncio.sleep(hold_s)

    report = ws_forensics.build_report()
    status = mgr.status()
    out = {
        "handler_counts": counts,
        "manager_status": [
            {
                "name": r.get("name"),
                "connection_id": r.get("connection_id"),
                "connected": r.get("connected"),
                "reader_running": r.get("reader_running"),
                "message_count": r.get("message_count"),
                "disconnect_count": r.get("disconnect_count"),
                "reconnect_count": r.get("reconnect_count"),
                "disconnect_reason": r.get("disconnect_reason"),
                "disconnect_evidence": r.get("disconnect_evidence"),
                "close_code": r.get("close_code"),
                "last_error": r.get("last_error"),
            }
            for r in status
        ],
        "disconnect_summary": report["disconnect_summary"],
        "reconnect_summary": report["reconnect_summary"],
        "event_loop": report["event_loop"],
        "issues": report["issues_candidates"],
        "connections": report["connections"],
    }
    print(json.dumps(out, indent=2, default=str))
    await mgr.stop_all()
    await ws_forensics.event_loop.stop()
    return 0


if __name__ == "__main__":
    hold = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
    raise SystemExit(asyncio.run(main(hold)))
