"""One-shot research: impulse/pullback combos with HL-long / LH-short via trend gate."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
RISK = 20.0  # $1000 * 2%
COMBOS = ["COMBO_03", "COMBO_04", "COMBO_05"]
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TFS = ["15m", "1h"]
DIRS = ["LONG", "SHORT"]
LIMIT = 2000
OUT = Path(__file__).resolve().parent / "_bt_impulse_pullback_hl_lh.json"


def fetch(combo: str, symbol: str, tf: str, direction: str) -> dict:
    q = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "timeframe": tf,
            "direction": direction,
            "limit": LIMIT,
        }
    )
    url = f"{BASE}/api/research/bos-combinations/{combo}/backtest?{q}"
    with urllib.request.urlopen(url, timeout=300) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    rows: list[dict] = []
    for c in COMBOS:
        for tf in TFS:
            for s in SYMBOLS:
                for d in DIRS:
                    structure = "HL (bullish HH+HL)" if d == "LONG" else "LH (bearish LH+LL)"
                    try:
                        data = fetch(c, s, tf, d)
                        r = data.get("result") or {}
                        n = int(data.get("sample_size") or r.get("sample_size") or 0)
                        avg = r.get("average_R")
                        pnl = (avg * n * RISK) if avg is not None and n else None
                        row = {
                            "combo": c,
                            "name": (r.get("condition_definition") or {}).get("name") or c,
                            "symbol": s,
                            "timeframe": tf,
                            "direction": d,
                            "structure": structure,
                            "sample": n,
                            "avg_R": avg,
                            "median_R": r.get("median_R"),
                            "tp1_rate": r.get("tp1_hit_rate"),
                            "sl_rate": r.get("sl_rate"),
                            "pf": r.get("profit_factor"),
                            "max_dd_R": r.get("max_drawdown_R"),
                            "pnl_usd_2pct": pnl,
                            "tp1_hits": r.get("tp1_hits"),
                            "sl_hits": r.get("sl_hits"),
                            "period_start": r.get("period_start"),
                            "period_end": r.get("period_end"),
                            "r_values": r.get("r_values") or [],
                        }
                        rows.append(row)
                        print(
                            f"{c} {s} {tf} {d}/{structure}: n={n} avgR={avg} "
                            f"tp1={row['tp1_rate']} sl={row['sl_rate']} pf={row['pf']} PnL={pnl}"
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(c, s, tf, d, "ERR", exc)
                        rows.append(
                            {
                                "combo": c,
                                "symbol": s,
                                "timeframe": tf,
                                "direction": d,
                                "structure": structure,
                                "error": str(exc),
                            }
                        )
    OUT.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print("WROTE", OUT, "rows", len(rows))

    # Compact summary: only rows with samples
    print("\n=== NONZERO SAMPLES ===")
    for row in rows:
        if row.get("sample"):
            print(
                f"{row['name']} | {row['symbol']} {row['timeframe']} {row['direction']} "
                f"({row['structure']}) | n={row['sample']} avgR={row['avg_R']:.3f} "
                f"tp1={row['tp1_rate']:.0%} sl={row['sl_rate']:.0%} "
                f"pf={row['pf']} PnL=${row['pnl_usd_2pct']:.2f}"
            )


if __name__ == "__main__":
    main()
