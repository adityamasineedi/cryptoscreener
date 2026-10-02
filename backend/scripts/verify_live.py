from __future__ import annotations

import json
import urllib.request


def get(path: str):
    with urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=60) as r:
        return json.loads(r.read().decode())


def main() -> None:
    stats = get("/api/system/stats")
    keys = [
        "symbols",
        "ticker_live",
        "kline_live",
        "websocket_connections",
        "kline_websocket_connections",
        "active_streams",
        "rest_requests_last_minute",
        "rate_limit_errors",
        "database",
        "redis",
        "ohlcv_closed_ingested",
        "oi_rows",
        "liquidation_rows",
        "ingestion",
    ]
    print("SYSTEM STATS")
    print(json.dumps({k: stats.get(k) for k in keys}, indent=2))

    prov = get("/api/health/providers")
    print("REST last minute", prov.get("rest_last_minute"))

    scr = get("/api/screener/futures?limit=3")
    print("SCREENER total", scr["total"], "rows", len(scr["rows"]))
    row = scr["rows"][0]
    for f in [
        "symbol",
        "price",
        "open_interest",
        "relative_volume",
        "structure",
        "market_cap",
        "tvl",
        "liquidation",
    ]:
        v = row.get(f)
        if isinstance(v, dict):
            print(f"  {f}: status={v.get('status')} value={v.get('value')}")
        else:
            print(f"  {f}:", v)

    sym = row["symbol"]
    ohlc = get(f"/api/charts/{sym}/ohlcv?timeframe=15m&limit=50")
    print("OHLCV", sym, "status", ohlc["status"], "count", ohlc["count"])
    oi = get(f"/api/charts/{sym}/oi")
    print("OI", sym, "status", oi["status"], "history", len(oi.get("history") or []))
    liq = get(f"/api/charts/{sym}/liquidations")
    print("LIQ", sym, "status", liq["status"], "events", len(liq.get("events") or []))

    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/screener/filter",
        data=json.dumps(
            {
                "filters": [{"field": "change_24h_pct", "operator": ">", "value": 0}],
                "limit": 5,
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        filt = json.loads(r.read().decode())
    print("FILTER total", filt["total"], "page", len(filt["rows"]))


if __name__ == "__main__":
    main()
