"""Run the research SD backtest on the stored BTC/ETH/SOL 1h tail.

Uses 15m as the trigger and 4h/1d as the director. Prints sample size and
the main rejection reasons. Research only.
"""

from __future__ import annotations

import asyncio
from collections import Counter

from app.config import get_settings
from app.research.combination_backtest import run_combination_backtest
from app.research.service import _load_htf_candles_for_combo, _load_research_candles
from app.services.database import db_manager

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
# 1,440 one-hour bars is 60 days. The panel's "60d" preset sends 5,760 bars
# (240 days); that tail rechecks the 15m sequence on every hour and does not
# finish in a normal run.
LIMIT = 1440


async def _one(symbol: str, combination_id: str) -> None:
    candles, eval_start, _meta = await _load_research_candles(
        symbol, "1h", limit=LIMIT, use_research_cache=False
    )
    _c1h, c4h = await _load_htf_candles_for_combo(
        symbol,
        require_htf=True,
        setup_timeframe="1h",
        limit=LIMIT,
        start_date=None,
        end_date=None,
        warmup_bars=100,
        use_research_cache=False,
    )
    c15, _, _ = await _load_research_candles(
        symbol, "15m", limit=LIMIT * 4, use_research_cache=False
    )
    c5, _, _ = await _load_research_candles(
        symbol, "5m", limit=LIMIT * 12, use_research_cache=False
    )
    c1d, _, _ = await _load_research_candles(
        symbol, "1d", limit=max(LIMIT // 4, 200), use_research_cache=False
    )
    out = run_combination_backtest(
        symbol,
        "1h",
        candles,
        combination_id,
        direction_filter="LONG",
        index_start=eval_start or None,
        candles_4h=c4h,
        candles_15m=c15,
        candles_5m=c5,
        candles_1d=c1d,
    )
    result = out.get("result") or {}
    counts: Counter[str] = Counter()
    for rec in (out.get("entry_diagnostics") or {}).values():
        counts[str(rec.get("primary_rejection_reason") or rec.get("setup_state"))] += 1
    print(
        combination_id,
        symbol,
        "bars",
        len(candles),
        "trades",
        result.get("sample_size"),
        "expectancy_R",
        result.get("expectancy_R"),
        "average_R",
        result.get("average_R"),
        "tp1",
        result.get("tp1_hit_rate"),
        "sl",
        result.get("sl_rate"),
        "seconds",
        round(float(out.get("elapsed_seconds") or 0), 1),
        flush=True,
    )
    for reason, n in counts.most_common(8):
        print(f"  {n:6d}  {reason}", flush=True)


async def main() -> None:
    await db_manager.connect(get_settings(), ensure_schema=False)
    try:
        for combination_id in ("COMBO_SD_A", "COMBO_SD_B"):
            for symbol in SYMBOLS:
                await _one(symbol, combination_id)
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
