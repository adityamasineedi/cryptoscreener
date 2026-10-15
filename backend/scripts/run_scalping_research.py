"""Run versioned scalping research against the configured PostgreSQL OHLCV.

This command writes raw research metrics only.  It never mutates validation,
paper eligibility, screener publication, notifications, or order state.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.research.postgres_ohlcv import load_ohlcv_series_range
from app.research.intraday_bos_sd.scalping import SCALPING_STRATEGIES, run_backtest
from app.services.database import db_manager


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def coverage() -> list[dict[str, Any]]:
    if db_manager.engine is None:
        return []
    async with db_manager.engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT timeframe, COUNT(*) AS bars,
                       COUNT(DISTINCT symbol) AS symbols,
                       MIN(time) AS first_bar, MAX(time) AS last_bar
                FROM ohlcv
                WHERE timeframe IN ('1m', '5m', '15m')
                GROUP BY timeframe ORDER BY timeframe
                """
            )
        )
        return [
            {
                **dict(row._mapping),
                "first_bar": row.first_bar.isoformat() if row.first_bar else None,
                "last_bar": row.last_bar.isoformat() if row.last_bar else None,
            }
            for row in result.fetchall()
        ]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="", help="Comma-separated futures symbols")
    parser.add_argument("--start", default="", help="Inclusive ISO-8601 UTC time")
    parser.add_argument("--end", default="", help="Exclusive ISO-8601 UTC time")
    parser.add_argument("--output", default="", help="Optional JSON artifact path")
    parser.add_argument("--coverage-only", action="store_true")
    args = parser.parse_args()

    connection_error = None
    try:
        await asyncio.wait_for(
            db_manager.connect(get_settings(), ensure_schema=False), timeout=15.0
        )
    except TimeoutError:
        connection_error = "database_connection_timeout_15s"
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_status": db_manager.status,
        "database_error": connection_error,
        "coverage": [] if connection_error else await coverage(),
        "registrations": [
            {
                "strategy_id": item.strategy_id,
                "variant": item.variant,
                "specification_complete": item.specification_complete,
                "research_only": item.research_only,
            }
            for item in SCALPING_STRATEGIES
        ],
        "validation_status": "NOT_EVALUATED",
        "results": {},
    }
    if not args.coverage_only:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if not symbols:
            raise SystemExit("--symbols is required unless --coverage-only is used")
        if db_manager.engine is None or connection_error:
            report["results_error"] = connection_error or "database_unavailable"
        else:
            start, end = _timestamp(args.start), _timestamp(args.end)
            for symbol in symbols:
                series = {
                    tf: await load_ohlcv_series_range(
                        symbol, tf, start=start, end_exclusive=end, warmup_bars=30
                    )
                    for tf in ("1m", "5m", "15m")
                }
                report["results"][symbol] = {
                    "bars": {tf: len(rows) for tf, rows in series.items()},
                    "backtest": run_backtest(
                        symbol=symbol,
                        candles_1m=series["1m"],
                        candles_5m=series["5m"],
                        candles_15m=series["15m"],
                    ),
                }
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        path = Path(args.output).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")
        print(path)
    else:
        print(payload)
    if db_manager.engine is not None:
        await db_manager.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
