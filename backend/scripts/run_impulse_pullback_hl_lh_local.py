"""In-process impulse/pullback backtest: HL longs / LH shorts via trend gate.

COMBO_03 = Trend + BOS + Pullback
COMBO_04 = Trend + BOS + Impulse + Pullback
Trend BULLISH ⇒ HH+HL structure (longs). Trend BEARISH ⇒ LH+LL (shorts).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.research.bos_combinations import get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.config import ResearchConfig
from app.research.postgres_ohlcv import load_ohlcv_series_tail
from app.services.database import db_manager
from app.signals.config import SignalConfig

RISK = 20.0
LIMIT = 1200
COMBOS = ["COMBO_03", "COMBO_04"]
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TFS = ["15m", "1h"]
DIRS = ["LONG", "SHORT"]
OUT = Path(__file__).resolve().parent / "_bt_impulse_pullback_hl_lh.json"


async def main() -> None:
    settings = get_settings()
    await db_manager.connect(settings)
    if not db_manager.enabled or db_manager.engine is None:
        raise RuntimeError("DATABASE unavailable — cannot load research OHLCV")
    scfg = SignalConfig()
    rcfg = ResearchConfig()
    rows: list[dict] = []
    cache: dict[tuple[str, str], list] = {}

    try:
        for tf in TFS:
            for sym in SYMBOLS:
                key = (sym, tf)
                print(f"loading {sym} {tf} …", flush=True)
                cache[key] = await load_ohlcv_series_tail(sym, tf, limit=LIMIT)
                print(f"  candles={len(cache[key])}", flush=True)

        for combo_id in COMBOS:
            combo = get_combination(combo_id)
            assert combo is not None
            for tf in TFS:
                for sym in SYMBOLS:
                    candles = cache[(sym, tf)]
                    for direction in DIRS:
                        structure = (
                            "HL (bullish HH+HL)"
                            if direction == "LONG"
                            else "LH (bearish LH+LL)"
                        )
                        print(
                            f"run {combo_id} {sym} {tf} {direction} …",
                            flush=True,
                        )
                        out = run_combination_backtest(
                            sym,
                            tf,
                            candles,
                            combo,
                            signal_config=scfg,
                            research_config=rcfg,
                            direction_filter=direction,
                        )
                        r = out.get("result") or {}
                        n = int(out.get("sample_size") or r.get("sample_size") or 0)
                        avg = r.get("average_R")
                        pnl = (avg * n * RISK) if avg is not None and n else None
                        row = {
                            "combo": combo_id,
                            "name": combo.name,
                            "symbol": sym,
                            "timeframe": tf,
                            "direction": direction,
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
                            "elapsed_seconds": out.get("elapsed_seconds"),
                        }
                        rows.append(row)
                        print(
                            f"  n={n} avgR={avg} tp1={row['tp1_rate']} "
                            f"sl={row['sl_rate']} pf={row['pf']} PnL={pnl} "
                            f"t={out.get('elapsed_seconds'):.1f}s",
                            flush=True,
                        )
    finally:
        await db_manager.close()

    OUT.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print("\n=== RESULTS (impulse/pullback · HL long / LH short) ===", flush=True)
    print(f"$1000 capital · 2% risk → $20/R · last {LIMIT} bars\n", flush=True)
    for row in rows:
        n = row["sample"]
        if not n:
            print(
                f"{row['name']:32} {row['symbol']:8} {row['timeframe']:3} "
                f"{row['direction']:5} {row['structure']:22} n=0",
                flush=True,
            )
            continue
        print(
            f"{row['name']:32} {row['symbol']:8} {row['timeframe']:3} "
            f"{row['direction']:5} {row['structure']:22} "
            f"n={n:3d} avgR={row['avg_R']:+.3f} "
            f"tp1={row['tp1_rate']:.0%} sl={row['sl_rate']:.0%} "
            f"pf={row['pf']} PnL=${row['pnl_usd_2pct']:+.2f}",
            flush=True,
        )
    print("WROTE", OUT, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
