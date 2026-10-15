"""Research backtest: COMBO_SD_C on the major USDT-M book, 1h, two years.

Same fee and risk defaults as the Backtest panel. Does not open paper or live trades.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.research.backtest_cpu_pool import get_research_cpu_pool
from app.research.service import get_bos_research_service
from app.research.v1_production import V1_SYMBOLS
from app.services.database import db_manager


def _progress(event: dict) -> None:
    phase = str(event.get("phase") or "")
    if phase in {"CONTEXT_BUILD", "DB_QUERY_START"} or event.get("bars_processed"):
        print(
            f"{phase} {event.get('symbol') or ''} {event.get('timeframe') or ''} "
            f"bars={event.get('bars_processed')}/{event.get('total_bars')} "
            f"trades={event.get('trades')}",
            flush=True,
        )


async def main() -> None:
    get_research_cpu_pool()
    await db_manager.connect(get_settings())
    service = get_bos_research_service()
    symbols = sorted(V1_SYMBOLS)
    # 180 days of 1h. A full two-year pass also loads 5m/15m/4h/1d and stalled
    # on the first coin. This window is still inside the stored history.
    rows_out = []
    try:
        for sym in symbols:
            print(f"START {sym}", flush=True)
            result = await service.strategy_matrix(
                combination_id="COMBO_SD_C",
                symbols=[sym],
                timeframes=["1h"],
                direction="LONG",
                limit=4320,
                start_date="2026-04-13",
                end_date="2026-10-10",
                risk_usd=20.0,
                principal_usd=1000.0,
                leverage=2.0,
                taker_fee=0.0004,
                maker_fee=0.0002,
                include_trades=True,
                progress_callback=_progress,
            )
            for row in result.get("rows") or []:
                trades = row.get("trades") or []
                item = {
                    "symbol": row.get("symbol"),
                    "timeframe": row.get("timeframe"),
                    "status": row.get("status"),
                    "trades": row.get("sample_size") or len(trades),
                    "win_rate": row.get("win_rate"),
                    "average_R": row.get("average_R") or row.get("expectancy_R"),
                    "net_pnl": row.get("net_pnl") or row.get("total_pnl"),
                    "profit_factor": row.get("profit_factor"),
                    "max_drawdown_R": row.get("max_drawdown_R"),
                }
                rows_out.append(item)
                print("ROW " + json.dumps(item, default=str), flush=True)
    finally:
        await db_manager.close()

    summary = {
        "combination_id": "COMBO_SD_C",
        "direction": "LONG",
        "timeframe": "1h",
        "start_date": "2026-04-13",
        "end_date": "2026-10-10",
        "rows": rows_out,
    }
    out = (
        Path(__file__).resolve().parents[2]
        / "research_sandbox"
        / "combo_sd_c_majors_20261010"
        / "summary.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
