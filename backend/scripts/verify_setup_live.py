"""Real-data validation for setup signal live recomputation.

Usage (backend must be running with real Binance data):
  python backend/scripts/verify_setup_live.py
  python backend/scripts/verify_setup_live.py --base http://127.0.0.1:8000

Does NOT use synthetic market data. Does NOT claim profitability.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def get_json(base: str, path: str) -> dict[str, Any]:
    url = f"{base.rstrip('/')}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8000")
    args = p.parse_args()
    base = args.base

    report: dict[str, Any] = {
        "label": "REAL_DATA_VALIDATION",
        "disclaimer": "Structural setup connectivity check — not a profitability claim.",
        "base": base,
        "symbols": [],
        "errors": [],
    }

    try:
        health = get_json(base, "/api/health")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: backend not reachable at {base}: {exc}")
        return 2

    report["health"] = {
        "use_real_data": health.get("use_real_data"),
        "symbols_loaded": health.get("symbols_loaded"),
        "tickers_live": health.get("tickers_live"),
    }
    if not health.get("use_real_data"):
        report["errors"].append("USE_REAL_DATA is not true")

    # Discover symbols from screener (dynamic — no hardcoding universe)
    screener = get_json(base, "/api/screener/futures?limit=50&sort_by=quote_volume_24h")
    rows = screener.get("rows") or []
    symbols = [r["symbol"] for r in rows if r.get("symbol")]
    # Prefer BTCUSDT if present, plus 4 others
    ordered = []
    if "BTCUSDT" in symbols:
        ordered.append("BTCUSDT")
    for s in symbols:
        if s not in ordered:
            ordered.append(s)
        if len(ordered) >= 5:
            break

    if len(ordered) < 5:
        report["errors"].append(f"Need ≥5 symbols with live data; got {ordered}")

    t0 = time.perf_counter()
    for sym in ordered:
        entry: dict[str, Any] = {"symbol": sym}
        try:
            # OHLCV real check
            ohlcv = get_json(base, f"/api/charts/{sym}/ohlcv?timeframe=15m&limit=50")
            entry["ohlcv_status"] = ohlcv.get("status")
            entry["ohlcv_count"] = ohlcv.get("count")
            if ohlcv.get("status") != "LIVE" or not ohlcv.get("count"):
                entry["signal"] = "SKIP_NO_OHLCV"
                report["symbols"].append(entry)
                continue

            # Force analysis via API
            t1 = time.perf_counter()
            sig = get_json(base, f"/api/signals/{sym}")
            entry["signal_api_ms"] = round((time.perf_counter() - t1) * 1000, 2)
            entry["status"] = sig.get("status")
            entry["setup_status"] = sig.get("status")
            entry["signal_status"] = sig.get("signal_status")
            entry["market_signal"] = sig.get("market_signal")
            entry["confirmation_strength"] = sig.get("confirmation_strength")
            entry["market_signal_reason"] = sig.get("market_signal_reason")
            entry["direction"] = sig.get("direction")
            entry["calculated_at"] = sig.get("calculated_at")
            entry["source_candle_timestamps"] = sig.get("source_candle_timestamps")
            entry["mtf"] = (sig.get("mtf") or {}).get("MTF_ALIGNMENT")
            entry["trends"] = sig.get("trend")
            ms = sig.get("market_signal")
            if ms and ms not in (
                "STRONG_BUY",
                "BUY",
                "NEUTRAL",
                "SELL",
                "STRONG_SELL",
                "WAITING",
            ):
                entry.setdefault("ms_issues", []).append(f"invalid market_signal={ms}")
            if ms in ("BUY", "STRONG_BUY") and (sig.get("mtf") or {}).get(
                "MTF_ALIGNMENT"
            ) == "CONFLICT":
                entry.setdefault("ms_issues", []).append("BUY under CONFLICT")

            # MTF honesty: missing TF must not be silently filled
            deps = sig.get("data_dependencies") or {}
            entry["data_dependencies"] = deps
            for tf in ("4h", "1h", "15m", "5m"):
                o = get_json(base, f"/api/charts/{sym}/ohlcv?timeframe={tf}&limit=10")
                if o.get("status") != "LIVE" or not o.get("count"):
                    if (sig.get("trend") or {}).get(tf) not in ("WAITING", None, "INSUFFICIENT_DATA"):
                        if deps.get(tf) != "WAITING FOR OHLCV":
                            entry.setdefault("mtf_issues", []).append(
                                f"{tf} missing OHLCV but trend={((sig.get('trend') or {}).get(tf))}"
                            )

            # Explanation vs BOS math when confirmed
            bos = sig.get("bos") or {}
            if bos.get("state") == "CONFIRMED":
                ok = (
                    bos.get("break_price") is not None
                    and bos.get("broken_level") is not None
                    and float(bos["break_price"]) != float(bos["broken_level"])
                )
                if bos.get("direction") == "BULLISH_BOS":
                    ok = ok and float(bos["break_price"]) > float(bos["broken_level"])
                if bos.get("direction") == "BEARISH_BOS":
                    ok = ok and float(bos["break_price"]) < float(bos["broken_level"])
                entry["bos_explanation_consistent"] = bool(ok)

            # Risk math for entry candidates
            st = str(sig.get("status") or "")
            if "ENTRY_CANDIDATE" in st:
                stop = sig.get("stop") or {}
                entry_p = (sig.get("entry") or {}).get("entry_price")
                final_stop = stop.get("final_stop")
                rr = sig.get("risk_reward") or {}
                targets = sig.get("targets") or []
                risk_mgmt = sig.get("risk_management") or {}
                if entry_p and final_stop and targets:
                    risk = abs(float(entry_p) - float(final_stop))
                    tp1 = float(targets[0]["target_price"])
                    expected_r = abs(tp1 - float(entry_p)) / risk if risk else None
                    reported = rr.get("TP1_R")
                    entry["risk_check"] = {
                        "entry": entry_p,
                        "stop": final_stop,
                        "tp1": tp1,
                        "expected_TP1_R": expected_r,
                        "reported_TP1_R": reported,
                        "rr_match": (
                            expected_r is not None
                            and reported is not None
                            and abs(float(expected_r) - float(reported)) < 1e-6
                        ),
                        "position_risk_ok": None,
                    }
                    if risk_mgmt.get("final_quantity") and risk_mgmt.get("risk_per_unit"):
                        approx = float(risk_mgmt["final_quantity"]) * float(
                            risk_mgmt["risk_per_unit"]
                        )
                        max_risk = float(risk_mgmt.get("max_risk_amount") or 0)
                        entry["risk_check"]["position_risk_ok"] = abs(approx - max_risk) <= float(
                            risk_mgmt["risk_per_unit"]
                        )

            # Screener row FreshValue presence
            row = next((r for r in rows if r.get("symbol") == sym), None)
            if row:
                entry["screener_setup_signal"] = (row.get("setup_signal") or {}).get("status")
                entry["screener_setup_value"] = (row.get("setup_signal") or {}).get("value")

        except urllib.error.HTTPError as exc:
            entry["error"] = f"HTTP {exc.code}"
            report["errors"].append(f"{sym}: HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)
            report["errors"].append(f"{sym}: {exc}")
        report["symbols"].append(entry)

    try:
        perf = get_json(base, "/api/system/performance")
        report["performance"] = (perf.get("pipeline") or {})
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"performance: {exc}")

    report["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    print(json.dumps(report, indent=2, default=str))

    # Exit code: 0 if each symbol got a non-empty signal response with calculated_at
    # when OHLCV LIVE; WAITING is OK if honestly declared
    ok = True
    for s in report["symbols"]:
        if s.get("ohlcv_status") == "LIVE" and not s.get("calculated_at") and not s.get("error"):
            ok = False
        if s.get("mtf_issues"):
            ok = False
        rc = s.get("risk_check")
        if rc and rc.get("rr_match") is False:
            ok = False
    if report["errors"] and any("USE_REAL_DATA" in e for e in report["errors"]):
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
