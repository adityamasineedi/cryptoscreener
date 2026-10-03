"""Poll /api/diagnostics/websocket for a fixed observation window."""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def fetch(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def summarize(payload: dict) -> dict:
    conns = payload.get("connections") or []
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "overall_status": payload.get("overall_status") or payload.get("status"),
        "connection_count": len(conns),
        "disconnect_summary": payload.get("disconnect_summary"),
        "reconnect_summary": payload.get("reconnect_summary"),
        "event_loop": payload.get("event_loop"),
        "handler_latency": payload.get("handler_latency"),
        "data_gap_summary": payload.get("data_gap_summary"),
        "issues": [
            {
                "error_code": i.get("error_code"),
                "severity": i.get("severity"),
                "message": i.get("message"),
            }
            for i in (payload.get("issues_candidates") or [])[:20]
        ],
        "connections": [
            {
                "connection_id": c.get("connection_id"),
                "stream_type": c.get("stream_type"),
                "connected": c.get("connected"),
                "reader_running": c.get("reader_running"),
                "status": c.get("status"),
                "subscription_count": c.get("subscription_count"),
                "messages_total": c.get("messages_total") or c.get("frames_total"),
                "messages_per_second": c.get("messages_per_second"),
                "bytes_per_second": c.get("bytes_per_second"),
                "disconnect_count": c.get("disconnect_count"),
                "reconnect_count": c.get("reconnect_count"),
                "disconnect_reason": c.get("disconnect_reason"),
                "close_code": c.get("close_code"),
                "last_frame_at": c.get("last_frame_at"),
            }
            for c in conns
        ],
    }


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    minutes = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    interval = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0
    out_path = Path(sys.argv[4]) if len(sys.argv) > 4 else Path("scripts/_ws_obs_series.jsonl")
    url = f"{base.rstrip('/')}/api/diagnostics/websocket"
    end = time.time() + minutes * 60.0
    n = 0
    with out_path.open("w", encoding="utf-8") as fh:
        while time.time() < end:
            try:
                payload = fetch(url)
                row = summarize(payload)
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                n += 1
                ds = row.get("disconnect_summary") or {}
                print(
                    json.dumps(
                        {
                            "n": n,
                            "overall": row["overall_status"],
                            "connections": row["connection_count"],
                            "disconnects": ds.get("total_disconnect_events"),
                            "loop_max": (row.get("event_loop") or {}).get("max_lag_ms"),
                            "loop_over_500": (row.get("event_loop") or {}).get(
                                "over_500ms"
                            ),
                        }
                    ),
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"n": n, "error": str(exc)}), flush=True)
            # sleep remaining interval
            time.sleep(interval)
    print(json.dumps({"done": True, "samples": n, "path": str(out_path)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
