"""Reproduce the Backtest-screen LCSC 1h LONG result through run_engine_cell."""
from __future__ import annotations
import pickle, sys
from datetime import datetime, timezone
from pathlib import Path
from app.research.engine_backtest import run_engine_cell

OUT = Path(__file__).resolve().parent / "out"

def cell(sym: str, start: str, end: str | None = None) -> dict:
    candles = pickle.loads((OUT / f"candles_{sym}_1h.pkl").read_bytes())
    s = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    i0 = next(i for i, c in enumerate(candles) if c["time"] >= s)
    if end:
        e = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
        candles = [c for c in candles if c["time"] < e]
    lo = max(0, i0 - 1000)
    sub = candles[lo:]
    return run_engine_cell({
        "strategy_id": "LARGE_CAP_SWEEP_CHOCH", "direction": "LONG", "symbol": sym, "timeframe": "1h",
        "candles": sub, "index_start": i0 - lo, "risk_usd": 20.0, "taker_fee": 0.0004, "maker_fee": 0.0002,
        "leverage": 2.0, "principal": 1000.0, "market_cap": None,
    })

if __name__ == "__main__":
    sym, start = sys.argv[1], sys.argv[2]
    end = sys.argv[3] if len(sys.argv) > 3 else None
    res = cell(sym, start, end)
    tr = res["trades"]
    net = sum(t["net_pnl_usd"] for t in tr); fees = sum(t["fee_total_usd"] for t in tr)
    wins = sum(1 for t in tr if t["net_pnl_usd"] > 0)
    eq = pk = dd = 0.0
    for t in tr:
        eq += t["net_pnl_usd"]; pk = max(pk, eq); dd = max(dd, pk - eq)
    print(sym, start, end, "n", len(tr), "net", round(net, 2), "fees", round(fees, 2), "win%", round(100 * wins / max(1, len(tr)), 1),
          "maxDD", round(dd, 2), "sum r_gross", round(sum(t["r_gross"] or 0 for t in tr), 2), "open", res["open_trades_at_end"],
          "first", tr[0]["entry_time"] if tr else None, "last", tr[-1]["exit_time"] if tr else None)
