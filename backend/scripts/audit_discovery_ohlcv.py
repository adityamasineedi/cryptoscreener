"""Audit existing 1h OHLCV for the discovery universe.

Reads persisted candles for BTCUSDT, ETHUSDT, and SOLUSDT only. Reports row
count, first and last open time, and gaps. Does not download, write, or fill
missing bars.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.research.postgres_ohlcv import load_ohlcv_series  # noqa: E402
from app.services.database import db_manager  # noqa: E402


SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAME = "1h"
BAR_STEP = timedelta(hours=1)

Candle = Mapping[str, Any]
CandleLoader = Callable[[str, str], Awaitable[Sequence[Candle]]]


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def summarize_series(symbol: str, candles: Sequence[Candle]) -> dict[str, Any]:
    """Describe one existing series. Missing bars are reported, never created."""
    ordered = sorted(candles, key=lambda row: _as_utc(row["time"]))
    if not ordered:
        return {
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "rows": 0,
            "first_open_time": None,
            "last_open_time": None,
            "gap_count": 0,
            "missing_bars": 0,
            "gaps": [],
        }

    opens = [_as_utc(row["time"]) for row in ordered]
    gaps: list[dict[str, Any]] = []
    missing_bars = 0
    for previous, nxt in zip(opens, opens[1:]):
        delta = nxt - previous
        if delta <= BAR_STEP:
            continue
        missing = int(delta.total_seconds() // BAR_STEP.total_seconds()) - 1
        if missing < 1:
            continue
        missing_bars += missing
        gaps.append(
            {
                "previous_open_time": previous.isoformat(),
                "next_open_time": nxt.isoformat(),
                "missing_bars": missing,
            }
        )

    return {
        "symbol": symbol,
        "timeframe": TIMEFRAME,
        "rows": len(ordered),
        "first_open_time": opens[0].isoformat(),
        "last_open_time": opens[-1].isoformat(),
        "gap_count": len(gaps),
        "missing_bars": missing_bars,
        "gaps": gaps,
    }


async def audit_existing_ohlcv(
    *,
    candle_loader: CandleLoader = load_ohlcv_series,
) -> dict[str, Any]:
    """Read the three discovery symbols and return a coverage report."""
    by_symbol: dict[str, dict[str, Any]] = {}
    for symbol in SYMBOLS:
        try:
            candles = await candle_loader(symbol, TIMEFRAME)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Existing PostgreSQL 1h candle read path failed for {symbol}: {exc}"
            ) from exc
        by_symbol[symbol] = summarize_series(symbol, candles)
    return {
        "timeframe": TIMEFRAME,
        "symbols": list(SYMBOLS),
        "read_only": True,
        "by_symbol": by_symbol,
    }


async def _main() -> dict[str, Any]:
    await db_manager.connect(get_settings(), ensure_schema=False)
    if db_manager.engine is None:
        raise RuntimeError("Existing PostgreSQL 1h candle read path is unavailable")
    try:
        return await audit_existing_ohlcv()
    finally:
        await db_manager.close()


def main() -> int:
    try:
        report = asyncio.run(_main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    print(json.dumps(report, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
