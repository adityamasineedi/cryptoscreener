"""HL-long / LH-short research: COMBO_02 (Trend+BOS) + impulse-aware diagnostics.

Pullback currently never reaches ACTIVE/CONFIRMED on recent history, so
COMBO_03/04 cannot produce trades. This script:
1) reports pullback/impulse state mix
2) backtests COMBO_02 LONG (HL) vs SHORT (LH)
3) counts bars where impulse is true under HL/LH context
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.research.bos_combinations import get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.config import ResearchConfig
from app.research.postgres_ohlcv import load_ohlcv_series_tail
from app.services.database import db_manager
from app.signals.config import SignalConfig

RISK = 20.0
STEP = 2
OUT = Path(__file__).resolve().parent / "_bt_hl_lh_long_short.json"

# Bar → calendar days (approx): 15m = bars/96, 1h = bars/24
# DB currently has ~12k bars 15m (~127d) and ~12k bars 1h (~508d).


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="HL-long / LH-short COMBO_02 backtest (raise --limit for more days)."
    )
    p.add_argument(
        "--limit",
        type=int,
        default=1200,
        help="OHLCV bars to load from DB tail (default 1200 ~ 12.5d on 15m, 50d on 1h)",
    )
    p.add_argument(
        "--symbols",
        default="BTCUSDT,ETHUSDT,SOLUSDT",
        help="Comma-separated symbols",
    )
    p.add_argument("--tfs", default="15m,1h", help="Comma-separated timeframes")
    p.add_argument(
        "--direction",
        choices=("LONG", "SHORT", "BOTH"),
        default="BOTH",
        help="Trade side to backtest (use LONG for long-strategy only)",
    )
    p.add_argument(
        "--skip-diag",
        action="store_true",
        help="Skip pullback/impulse scan (faster on large --limit)",
    )
    return p.parse_args()


async def main() -> None:
    args = _parse_args()
    limit = max(int(args.limit), 1)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    tfs = [t.strip() for t in args.tfs.split(",") if t.strip()]
    directions = (
        ("LONG", "SHORT") if args.direction == "BOTH" else (args.direction,)
    )

    await db_manager.connect(get_settings())
    scfg = SignalConfig()
    rcfg = ResearchConfig()
    combo02 = get_combination("COMBO_02")
    combo04 = get_combination("COMBO_04")
    assert combo02 and combo04
    rows: list[dict] = []
    print(f"limit={limit} symbols={symbols} tfs={tfs} direction={args.direction}")
    try:
        for tf in tfs:
            for sym in symbols:
                candles = await load_ohlcv_series_tail(sym, tf, limit=limit)
                print(f"\n{sym} {tf} loaded={len(candles)} bars")
                pb_states: Counter[str] = Counter()
                impulse_hl = impulse_lh = 0
                if not args.skip_diag:
                    for i in range(rcfg.min_bars, len(candles), STEP):
                        setup = evaluate_combination_at_bar(
                            symbol=sym,
                            timeframe=tf,
                            candles=candles,
                            as_of_index=i,
                            combination=combo04,
                            signal_config=scfg,
                            research_config=rcfg,
                            compute_sd=False,
                        )
                        tf_a = setup.get("tf_analysis") or {}
                        pb = tf_a.get("pullback") or {}
                        pb_states[str(pb.get("pullback_state") or "NONE")] += 1
                        gates = setup.get("gates") or {}
                        if gates.get("bos") and gates.get("trend") and gates.get("impulse"):
                            if setup.get("direction") == "LONG":
                                impulse_hl += 1
                            elif setup.get("direction") == "SHORT":
                                impulse_lh += 1
                    print(f"  pullback_states={dict(pb_states)}")
                    print(
                        f"  impulse under HL-long ctx={impulse_hl}  "
                        f"LH-short ctx={impulse_lh}"
                    )

                for direction in directions:
                    structure = (
                        "HL (bullish HH+HL)"
                        if direction == "LONG"
                        else "LH (bearish LH+LL)"
                    )
                    out = run_combination_backtest(
                        sym,
                        tf,
                        candles,
                        combo02,
                        signal_config=scfg,
                        research_config=rcfg,
                        direction_filter=direction,
                    )
                    r = out.get("result") or {}
                    n = int(out.get("sample_size") or r.get("sample_size") or 0)
                    avg = r.get("average_R")
                    pnl = (avg * n * RISK) if avg is not None and n else None
                    row = {
                        "combo": "COMBO_02",
                        "name": "TREND_BOS",
                        "symbol": sym,
                        "timeframe": tf,
                        "direction": direction,
                        "structure": structure,
                        "sample": n,
                        "avg_R": avg,
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
                        "pullback_states": dict(pb_states),
                        "impulse_in_structure_ctx": impulse_hl
                        if direction == "LONG"
                        else impulse_lh,
                    }
                    rows.append(row)
                    if n:
                        print(
                            f"  TREND_BOS {direction}/{structure}: n={n} avgR={avg:+.3f} "
                            f"tp1={row['tp1_rate']:.0%} sl={row['sl_rate']:.0%} "
                            f"PnL=${pnl:+.2f}"
                        )
                    else:
                        print(f"  TREND_BOS {direction}/{structure}: n=0")
    finally:
        await db_manager.close()

    OUT.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print("\nWROTE", OUT)


if __name__ == "__main__":
    asyncio.run(main())
