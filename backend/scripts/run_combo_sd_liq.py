"""Run the research COMBO_SD_LIQ backtest on the stored OHLCV book.

Same request the Backtest panel sends for a 60-day lookback on 1h:
BTC, ETH, and SOL, long only, 5760 bars. Does not touch paper or live orders.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.config import get_settings
from app.research.backtest_cpu_pool import get_research_cpu_pool
from app.research.service import get_bos_research_service
from app.services.database import db_manager


def _progress(event: dict) -> None:
    phase = event.get("phase")
    symbol = event.get("symbol") or ""
    tf = event.get("timeframe") or ""
    elapsed = event.get("elapsed_seconds")
    print(f"{phase} {symbol} {tf} elapsed={elapsed}", flush=True)


async def main() -> None:
    # The process pool must be created on the main thread. Creating it later
    # from the backtest worker thread hangs on Windows.
    get_research_cpu_pool()
    settings = get_settings()
    await db_manager.connect(settings)
    service = get_bos_research_service()
    try:
        result = await service.strategy_matrix(
        combination_id="COMBO_SD_LIQ",
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        timeframes=["1h"],
        direction="LONG",
        limit=5760,
        risk_usd=20.0,
        principal_usd=1000.0,
        leverage=2.0,
        taker_fee=0.0004,
        maker_fee=0.0002,
        include_trades=True,
            progress_callback=_progress,
        )
    finally:
        await db_manager.close()
    rows_out = []
    for row in result.get("rows") or []:
        trades = row.get("trades") or []
        liquidity = [
            (trade.get("sd_structure") or {}).get("liquidity_event")
            for trade in trades
            if isinstance(trade, dict)
        ]
        rows_out.append(
            {
                "symbol": row.get("symbol"),
                "timeframe": row.get("timeframe"),
                "status": row.get("status"),
                "trades": len(trades),
                "expectancy_R": row.get("expectancy_R"),
                "net_R": row.get("net_R") or row.get("total_R"),
                "win_rate": row.get("win_rate"),
                "profit_factor": row.get("profit_factor"),
                "liquidity_events": liquidity,
                "keys": sorted(row.keys()),
            }
        )
    summary = {
        "status": result.get("status"),
        "combination_id": result.get("combination_id"),
        "direction": result.get("direction"),
        "limit": result.get("limit"),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "rows": rows_out,
    }
    out = Path(__file__).resolve().parents[2] / "research_sandbox" / "combo_sd_liq_20261010" / "summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
