"""Count all-day structure labels on the stored 15m book.

upper_trend = bull. short_trend = bear. sideways = neither pair.
reversal = a close through the protective swing while that pair still stands.
Research only. Does not place trades.
"""

from __future__ import annotations

import asyncio
from collections import Counter

from sqlalchemy import text

from app.config import get_settings
from app.research.sd_structure.sequence import classify_day_structure
from app.research.service import _load_research_candles
from app.services.database import db_manager
from app.signals.config import SignalConfig
from app.signals.swing_detector import detect_swings, extend_swings

BARS = 5760  # 60 days of 15m
GROUPS = ("BTC", "ETH", "large-cap", "mid-cap")


async def _universe() -> list[tuple[str, str]]:
    if db_manager.engine is None:
        return []
    async with db_manager.engine.begin() as conn:
        rows = await conn.execute(
            text(
                """
                SELECT DISTINCT ON (symbol) symbol, cap_group
                FROM research_market_cap_observations
                WHERE cap_group = ANY(:groups)
                ORDER BY symbol, effective_time DESC
                """
            ),
            {"groups": list(GROUPS)},
        )
        return [(str(row[0]).upper(), str(row[1])) for row in rows.fetchall()]


async def _one(symbol: str, cap_group: str) -> None:
    candles, _, _ = await _load_research_candles(
        symbol, "15m", limit=BARS, use_research_cache=False
    )
    cfg = SignalConfig().swing_for("15m")
    counts: Counter[str] = Counter()
    swings: list = []
    start = cfg.swing_left_bars + cfg.swing_right_bars + 2
    for index in range(start, len(candles)):
        if index == start:
            swings = detect_swings(
                candles,
                left=cfg.swing_left_bars,
                right=cfg.swing_right_bars,
                symbol=symbol,
                timeframe="15m",
                as_of_index=index,
            )
        else:
            swings = extend_swings(
                swings,
                candles,
                left=cfg.swing_left_bars,
                right=cfg.swing_right_bars,
                symbol=symbol,
                timeframe="15m",
                as_of_index=index,
            )
        counts[
            classify_day_structure(
                swings, candles, index, right_bars=cfg.swing_right_bars
            )
        ] += 1
    total = sum(counts.values()) or 1
    print(symbol, cap_group, "bars", total, flush=True)
    for name in ("upper_trend", "short_trend", "sideways", "reversal"):
        n = counts[name]
        print(f"  {name:14} {n:6d}  {100.0 * n / total:5.1f}%", flush=True)


async def main() -> None:
    await db_manager.connect(get_settings(), ensure_schema=False)
    try:
        universe = await _universe()
        print("symbols", len(universe), flush=True)
        for symbol, cap_group in universe:
            await _one(symbol, cap_group)
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
