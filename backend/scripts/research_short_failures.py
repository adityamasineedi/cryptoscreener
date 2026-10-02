"""Research why LH-short (TREND_BOS SHORT) underperformed HL-long.

Loads last N bars, runs COMBO_02 SHORT vs LONG, prints trade-level MAE/MFE,
hold time, RR at entry, and structure/BOS context near entry.
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
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.config import ResearchConfig
from app.research.postgres_ohlcv import load_ohlcv_series_tail
from app.services.database import db_manager
from app.signals.config import SignalConfig

LIMIT = 1200
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TFS = ["15m", "1h"]
OUT = Path(__file__).resolve().parent / "_short_failure_research.json"


def _summarize(trades: list, direction: str) -> dict:
    closed = [t for t in trades if getattr(t, "outcome", None) not in (None, "OPEN")]
    if not closed:
        return {"direction": direction, "n": 0}
    rs = [float(t.r_multiple) for t in closed if t.r_multiple is not None]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    mae = [float(t.mae_r) for t in closed if t.mae_r is not None]
    mfe = [float(t.mfe_r) for t in closed if t.mfe_r is not None]
    holds = [int(t.holding_bars) for t in closed if t.holding_bars is not None]
    outcomes: dict[str, int] = {}
    for t in closed:
        outcomes[str(t.outcome)] = outcomes.get(str(t.outcome), 0) + 1
    # How often MFE reached 1R / 2R before death
    mfe_ge_1 = sum(1 for x in mfe if x >= 1.0)
    mfe_ge_2 = sum(1 for x in mfe if x >= 2.0)
    # Losers that never got +0.5R favorable
    loser_no_excursion = 0
    for t in closed:
        if t.r_multiple is not None and t.r_multiple <= 0 and (t.mfe_r or 0) < 0.5:
            loser_no_excursion += 1
    return {
        "direction": direction,
        "n": len(closed),
        "avg_R": sum(rs) / len(rs) if rs else None,
        "avg_win_R": sum(wins) / len(wins) if wins else None,
        "avg_loss_R": sum(losses) / len(losses) if losses else None,
        "win_rate": len(wins) / len(closed) if closed else None,
        "avg_MAE_R": sum(mae) / len(mae) if mae else None,
        "avg_MFE_R": sum(mfe) / len(mfe) if mfe else None,
        "median_hold": sorted(holds)[len(holds) // 2] if holds else None,
        "avg_hold": sum(holds) / len(holds) if holds else None,
        "outcomes": outcomes,
        "mfe_ge_1R_pct": mfe_ge_1 / len(mfe) if mfe else None,
        "mfe_ge_2R_pct": mfe_ge_2 / len(mfe) if mfe else None,
        "losers_never_0_5R_mfe": loser_no_excursion,
        "trades": [
            {
                "i": t.entry_index,
                "time": t.signal_time,
                "entry": t.entry_price,
                "stop": t.stop_price,
                "tp1": t.tp1,
                "rr": t.rr,
                "outcome": t.outcome,
                "R": t.r_multiple,
                "MAE_R": t.mae_r,
                "MFE_R": t.mfe_r,
                "hold": t.holding_bars,
                "stop_dist_pct": abs(t.entry_price - t.stop_price) / t.entry_price
                if t.entry_price
                else None,
            }
            for t in closed
        ],
    }


async def main() -> None:
    await db_manager.connect(get_settings())
    scfg = SignalConfig()
    rcfg = ResearchConfig()
    combo = get_combination("COMBO_02")
    assert combo
    report: dict = {"label": "SHORT_FAILURE_RESEARCH", "limit": LIMIT, "rows": []}
    try:
        for tf in TFS:
            for sym in SYMBOLS:
                candles = await load_ohlcv_series_tail(sym, tf, limit=LIMIT)
                print(f"\n=== {sym} {tf} candles={len(candles)} ===", flush=True)
                side_stats = {}
                for direction in ("LONG", "SHORT"):
                    out = run_combination_backtest(
                        sym,
                        tf,
                        candles,
                        combo,
                        signal_config=scfg,
                        research_config=rcfg,
                        direction_filter=direction,
                    )
                    trades = out.get("trades") or []
                    # trades may be dicts from serialization — normalize
                    if trades and isinstance(trades[0], dict):
                        from types import SimpleNamespace

                        trades = [SimpleNamespace(**t) for t in trades]
                    stats = _summarize(trades, direction)
                    side_stats[direction] = stats
                    print(
                        f"  {direction}: n={stats['n']} avgR={stats.get('avg_R')} "
                        f"win={stats.get('win_rate')} MAE={stats.get('avg_MAE_R')} "
                        f"MFE={stats.get('avg_MFE_R')} mfe>=2R={stats.get('mfe_ge_2R_pct')} "
                        f"outcomes={stats.get('outcomes')}",
                        flush=True,
                    )

                # Entry quality snapshot: stop distance + trend strength at short entries
                short_trades = side_stats["SHORT"].get("trades") or []
                enriched = []
                for tr in short_trades:
                    i = int(tr["i"])
                    setup = evaluate_combination_at_bar(
                        symbol=sym,
                        timeframe=tf,
                        candles=candles,
                        as_of_index=i,
                        combination=combo,
                        signal_config=scfg,
                        research_config=rcfg,
                        compute_sd=False,
                    )
                    trend = (setup.get("tf_analysis") or {}).get("trend") or {}
                    bos = (setup.get("tf_analysis") or {}).get("bos") or {}
                    # next 5 bars: did price go up into stop immediately?
                    immediate_up = False
                    if i + 1 < len(candles):
                        nxt = candles[i + 1]
                        hi = float(nxt.get("high") or 0)
                        if hi >= float(tr["stop"] or 0):
                            immediate_up = True
                    enriched.append(
                        {
                            **tr,
                            "trend": trend.get("trend"),
                            "trend_strength": trend.get("trend_strength"),
                            "trend_reason": trend.get("reason"),
                            "bos_dir": bos.get("direction"),
                            "bos_level": bos.get("broken_level"),
                            "stop_hit_next_bar": immediate_up,
                        }
                    )
                stop_next = sum(1 for e in enriched if e.get("stop_hit_next_bar"))
                avg_stop_pct = None
                if enriched:
                    dists = [e["stop_dist_pct"] for e in enriched if e.get("stop_dist_pct")]
                    avg_stop_pct = sum(dists) / len(dists) if dists else None

                long_s = side_stats["LONG"]
                short_s = side_stats["SHORT"]
                row = {
                    "symbol": sym,
                    "timeframe": tf,
                    "long": {k: v for k, v in long_s.items() if k != "trades"},
                    "short": {k: v for k, v in short_s.items() if k != "trades"},
                    "short_trades_detail": enriched,
                    "short_stop_hit_next_bar": stop_next,
                    "short_avg_stop_dist_pct": avg_stop_pct,
                }
                report["rows"].append(row)
                print(
                    f"  SHORT stop@next_bar={stop_next}/{short_s['n']} "
                    f"avg_stop_dist%={avg_stop_pct}",
                    flush=True,
                )
    finally:
        await db_manager.close()

    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("\n=== CROSS-CUT FINDINGS ===", flush=True)
    # Aggregate shorts vs longs
    tot_s = tot_l = 0
    sum_s = sum_l = 0.0
    mfe2_s = mfe2_n = 0
    next_bar_stops = short_n = 0
    for row in report["rows"]:
        s, l = row["short"], row["long"]
        if s.get("n"):
            tot_s += s["n"]
            sum_s += (s.get("avg_R") or 0) * s["n"]
            if s.get("mfe_ge_2R_pct") is not None:
                mfe2_s += s["mfe_ge_2R_pct"] * s["n"]
                mfe2_n += s["n"]
            next_bar_stops += row.get("short_stop_hit_next_bar") or 0
            short_n += s["n"]
        if l.get("n"):
            tot_l += l["n"]
            sum_l += (l.get("avg_R") or 0) * l["n"]
    print(f"LONG  n={tot_l} pooled_avgR={sum_l/tot_l if tot_l else None}", flush=True)
    print(f"SHORT n={tot_s} pooled_avgR={sum_s/tot_s if tot_s else None}", flush=True)
    print(f"SHORT MFE>=2R rate={mfe2_s/mfe2_n if mfe2_n else None}", flush=True)
    print(f"SHORT stopped on next bar={next_bar_stops}/{short_n}", flush=True)
    print("WROTE", OUT, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
