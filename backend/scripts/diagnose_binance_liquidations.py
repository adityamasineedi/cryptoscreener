#!/usr/bin/env python3
"""Isolated Binance Futures liquidation WebSocket diagnostic.

Does NOT start the bot. Connects dedicated sockets and reports raw frame traffic.
Never fabricates liquidation events.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:
    print("websockets package required", file=sys.stderr)
    raise


MARKET_TYPE = "USD-M Futures"
BASE_HOST = "wss://fstream.binance.com"


@dataclass
class ProbeResult:
    label: str
    url: str
    market_type: str = MARKET_TYPE
    stream_name: str = ""
    connected: bool = False
    handshake_ok: bool = False
    connect_error: str | None = None
    close_code: int | None = None
    close_reason: str | None = None
    frames_received: int = 0
    bytes_received: int = 0
    json_frames: int = 0
    parse_errors: int = 0
    force_order_events: int = 0
    last_message_at: str | None = None
    first_frame_preview: str | None = None
    duration_s: float = 0.0
    samples: list[dict[str, Any]] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _preview(raw: str | bytes, n: int = 200) -> str:
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    return text[:n]


def _count_force_orders(data: Any) -> int:
    if isinstance(data, dict):
        if data.get("e") == "forceOrder":
            return 1
        inner = data.get("data")
        if isinstance(inner, dict) and inner.get("e") == "forceOrder":
            return 1
        if isinstance(inner, list):
            return sum(1 for x in inner if isinstance(x, dict) and x.get("e") == "forceOrder")
    if isinstance(data, list):
        return sum(1 for x in data if isinstance(x, dict) and x.get("e") == "forceOrder")
    return 0


async def probe_url(
    *,
    label: str,
    url: str,
    stream_name: str,
    duration_s: float,
    subscribe_payload: dict[str, Any] | None = None,
) -> ProbeResult:
    result = ProbeResult(label=label, url=url, stream_name=stream_name)
    started = time.monotonic()
    deadline = started + duration_s
    print(f"\n=== {label} ===", flush=True)
    print(f"market_type={MARKET_TYPE}", flush=True)
    print(f"base_ws_url={BASE_HOST}", flush=True)
    print(f"stream_name={stream_name}", flush=True)
    print(f"url={url}", flush=True)
    if subscribe_payload is not None:
        print(f"subscription_request={json.dumps(subscribe_payload)}", flush=True)

    try:
        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=20,
            open_timeout=20,
            max_queue=1024,
        ) as ws:
            result.connected = True
            result.handshake_ok = True
            print(f"CONNECTED at {_now()}", flush=True)

            if subscribe_payload is not None:
                await ws.send(json.dumps(subscribe_payload))
                print("SUBSCRIBE sent", flush=True)

            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(5.0, remaining))
                except asyncio.TimeoutError:
                    continue
                except ConnectionClosed as exc:
                    result.close_code = exc.code
                    result.close_reason = str(exc.reason)
                    print(
                        f"DISCONNECTED code={exc.code} reason={exc.reason!r}",
                        flush=True,
                    )
                    break

                nbytes = len(raw) if isinstance(raw, (bytes, str)) else 0
                result.frames_received += 1
                result.bytes_received += nbytes
                result.last_message_at = _now()
                preview = _preview(raw)
                if result.first_frame_preview is None:
                    result.first_frame_preview = preview
                    print(f"FIRST_FRAME len={nbytes} preview={preview!r}", flush=True)
                else:
                    print(
                        f"FRAME#{result.frames_received} len={nbytes} preview={preview!r}",
                        flush=True,
                    )

                try:
                    data = json.loads(raw)
                    result.json_frames += 1
                    fo = _count_force_orders(data)
                    result.force_order_events += fo
                    if len(result.samples) < 5:
                        result.samples.append(
                            {
                                "at": result.last_message_at,
                                "bytes": nbytes,
                                "force_orders": fo,
                                "keys": list(data.keys())[:12]
                                if isinstance(data, dict)
                                else type(data).__name__,
                            }
                        )
                    # Capture SUBSCRIBE ack
                    if isinstance(data, dict) and (
                        "result" in data or "error" in data or "id" in data
                    ):
                        print(f"subscription_response={json.dumps(data)[:500]}", flush=True)
                except json.JSONDecodeError:
                    result.parse_errors += 1
                    print("PARSE_ERROR non-json frame", flush=True)
    except Exception as exc:  # noqa: BLE001
        result.connect_error = f"{type(exc).__name__}: {exc}"
        result.handshake_ok = False
        print(f"CONNECT_FAILED {result.connect_error}", flush=True)

    result.duration_s = round(time.monotonic() - started, 2)
    print(
        f"DONE frames={result.frames_received} bytes={result.bytes_received} "
        f"json={result.json_frames} forceOrders={result.force_order_events} "
        f"parse_errors={result.parse_errors} duration_s={result.duration_s}",
        flush=True,
    )
    return result


async def dns_tls_check(host: str = "fstream.binance.com") -> dict[str, Any]:
    import socket

    out: dict[str, Any] = {"host": host, "dns_ok": False, "tls_ok": False}
    try:
        infos = socket.getaddrinfo(host, 443)
        out["dns_ok"] = True
        out["addrs"] = sorted({i[4][0] for i in infos})[:8]
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                out["tls_ok"] = True
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Binance forceOrder streams")
    parser.add_argument(
        "--duration",
        type=float,
        default=300.0,
        help="Seconds per probe (default 300 = 5 minutes)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run 60s probes instead of full duration",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run all probes concurrently for the same wall-clock window",
    )
    args = parser.parse_args()
    duration = 60.0 if args.quick else float(args.duration)

    print("Binance Futures liquidation diagnostic", flush=True)
    print(f"started_at={_now()} duration_s={duration}", flush=True)
    net = await dns_tls_check()
    print(f"dns_tls={json.dumps(net)}", flush=True)

    probes = [
        {
            "label": "legacy_aggregate_ws",
            "url": f"{BASE_HOST}/ws/!forceOrder@arr",
            "stream_name": "!forceOrder@arr",
        },
        {
            "label": "market_aggregate_ws",
            "url": f"{BASE_HOST}/market/ws/!forceOrder@arr",
            "stream_name": "!forceOrder@arr",
        },
        {
            "label": "legacy_symbol_forceOrder",
            "url": f"{BASE_HOST}/ws/btcusdt@forceOrder",
            "stream_name": "btcusdt@forceOrder",
        },
        {
            "label": "market_symbol_forceOrder",
            "url": f"{BASE_HOST}/market/ws/btcusdt@forceOrder",
            "stream_name": "btcusdt@forceOrder",
        },
        {
            "label": "market_combined_aggregate",
            "url": f"{BASE_HOST}/market/stream?streams=!forceOrder@arr",
            "stream_name": "!forceOrder@arr",
        },
        {
            "label": "control_market_aggTrade",
            "url": f"{BASE_HOST}/market/ws/btcusdt@aggTrade",
            "stream_name": "btcusdt@aggTrade",
        },
        {
            "label": "control_legacy_aggTrade",
            "url": f"{BASE_HOST}/ws/btcusdt@aggTrade",
            "stream_name": "btcusdt@aggTrade",
        },
        {
            "label": "subscribe_market_base",
            "url": f"{BASE_HOST}/market/ws",
            "stream_name": "!forceOrder@arr",
            "subscribe_payload": {
                "method": "SUBSCRIBE",
                "params": ["!forceOrder@arr"],
                "id": 1,
            },
        },
        {
            "label": "subscribe_legacy_base",
            "url": f"{BASE_HOST}/ws",
            "stream_name": "!forceOrder@arr",
            "subscribe_payload": {
                "method": "SUBSCRIBE",
                "params": ["!forceOrder@arr"],
                "id": 2,
            },
        },
    ]

    results: list[ProbeResult] = []
    if args.parallel:
        results = list(
            await asyncio.gather(
                *[
                    probe_url(
                        label=p["label"],
                        url=p["url"],
                        stream_name=p["stream_name"],
                        duration_s=duration,
                        subscribe_payload=p.get("subscribe_payload"),
                    )
                    for p in probes
                ]
            )
        )
    else:
        # Fast path first: control streams + both aggregate forms in parallel,
        # then remaining probes if needed.
        first_batch = [p for p in probes if p["label"] in {
            "legacy_aggregate_ws",
            "market_aggregate_ws",
            "control_market_aggTrade",
            "control_legacy_aggTrade",
            "market_symbol_forceOrder",
            "legacy_symbol_forceOrder",
        }]
        rest = [p for p in probes if p not in first_batch]
        results.extend(
            await asyncio.gather(
                *[
                    probe_url(
                        label=p["label"],
                        url=p["url"],
                        stream_name=p["stream_name"],
                        duration_s=duration,
                        subscribe_payload=p.get("subscribe_payload"),
                    )
                    for p in first_batch
                ]
            )
        )
        # Only run subscribe / combined if aggregates were silent
        agg_frames = sum(
            r.frames_received
            for r in results
            if "aggregate" in r.label or "symbol_forceOrder" in r.label
        )
        if agg_frames == 0:
            results.extend(
                await asyncio.gather(
                    *[
                        probe_url(
                            label=p["label"],
                            url=p["url"],
                            stream_name=p["stream_name"],
                            duration_s=min(duration, 90.0),
                            subscribe_payload=p.get("subscribe_payload"),
                        )
                        for p in rest
                    ]
                )
            )

    summary = []
    for r in results:
        summary.append(
            {
                "label": r.label,
                "url": r.url,
                "stream_name": r.stream_name,
                "market_type": r.market_type,
                "connected": r.connected,
                "handshake_ok": r.handshake_ok,
                "connect_error": r.connect_error,
                "close_code": r.close_code,
                "close_reason": r.close_reason,
                "frames_received": r.frames_received,
                "bytes_received": r.bytes_received,
                "json_frames": r.json_frames,
                "parse_errors": r.parse_errors,
                "force_order_events": r.force_order_events,
                "last_message_at": r.last_message_at,
                "first_frame_preview": r.first_frame_preview,
                "duration_s": r.duration_s,
                "samples": r.samples,
            }
        )

    report = {
        "started_note": "Isolated diagnostic — no fabricated events",
        "dns_tls": net,
        "duration_requested_s": duration,
        "probes": summary,
    }
    out_path = (
        __file__.replace("diagnose_binance_liquidations.py", "")
        + "diagnose_binance_liquidations_result.json"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\n=== SUMMARY ===", flush=True)
    for s in summary:
        print(
            f"{s['label']}: connected={s['connected']} frames={s['frames_received']} "
            f"forceOrders={s['force_order_events']} err={s['connect_error']}",
            flush=True,
        )
    print(f"wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
