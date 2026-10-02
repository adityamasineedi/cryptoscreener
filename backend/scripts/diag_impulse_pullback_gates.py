"""Diagnose how often HL/LH + impulse + pullback gates fire (research-only)."""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.research.bos_combinations import get_combination
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.config import ResearchConfig
from app.research.postgres_ohlcv import load_ohlcv_series_tail
from app.services.database import db_manager
from app.signals.config import SignalConfig

LIMIT = 1200
STEP = 3  # every 3rd bar for speed
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TFS = ["15m", "1h"]


async def main() -> None:
    await db_manager.connect(get_settings())
    scfg = SignalConfig()
    rcfg = ResearchConfig()
    combo = get_combination("COMBO_04")
    assert combo
    try:
        for tf in TFS:
            for sym in SYMBOLS:
                candles = await load_ohlcv_series_tail(sym, tf, limit=LIMIT)
                counts: Counter[str] = Counter()
                long_hl = short_lh = full_long = full_short = 0
                for i in range(rcfg.min_bars, len(candles), STEP):
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
                    gates = setup.get("gates") or {}
                    for k, v in gates.items():
                        if v:
                            counts[k] += 1
                    counts["bars"] += 1
                    trend = ((setup.get("tf_analysis") or {}).get("trend") or {}).get("trend")
                    direction = setup.get("direction")
                    if gates.get("bos") and gates.get("trend") and direction == "LONG":
                        long_hl += 1
                    if gates.get("bos") and gates.get("trend") and direction == "SHORT":
                        short_lh += 1
                    if setup.get("status") in (
                        "LONG_ENTRY_CANDIDATE",
                        "SHORT_ENTRY_CANDIDATE",
                    ):
                        if direction == "LONG":
                            full_long += 1
                        else:
                            full_short += 1
                    # also count impulse+pullback without requiring all for COMBO_04
                    if gates.get("impulse") and gates.get("pullback"):
                        counts["impulse_and_pullback"] += 1
                    if (
                        gates.get("bos")
                        and gates.get("trend")
                        and gates.get("impulse")
                        and gates.get("pullback")
                    ):
                        counts["bos_trend_impulse_pullback"] += 1

                n = counts["bars"] or 1
                print(f"\n=== {sym} {tf} bars_checked={counts['bars']} candles={len(candles)} ===")
                for k in (
                    "bos",
                    "trend",
                    "impulse",
                    "pullback",
                    "rvol",
                    "impulse_and_pullback",
                    "bos_trend_impulse_pullback",
                ):
                    print(f"  {k:28} {counts[k]:5d}  ({100*counts[k]/n:5.1f}%)")
                print(f"  BOS+trend LONG (HL ctx)     {long_hl:5d}")
                print(f"  BOS+trend SHORT (LH ctx)    {short_lh:5d}")
                print(f"  FULL COMBO_04 LONG setups   {full_long:5d}")
                print(f"  FULL COMBO_04 SHORT setups  {full_short:5d}")
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
