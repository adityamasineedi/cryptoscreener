"""Read-only OHLCV loader for the strategy-rank multicap runner.

Opens its own asyncpg connection with default_transaction_read_only=on.
Same SELECT as app.research.postgres_ohlcv.load_ohlcv_series_range; never writes.
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

ROOT = Path(__file__).resolve().parents[4]


def _dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not url:
        raise RuntimeError("DATABASE_URL not found")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)


async def _connect() -> asyncpg.Connection:
    return await asyncpg.connect(
        _dsn(), server_settings={"default_transaction_read_only": "on"}
    )


async def _load(symbol: str, tf: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    conn = await _connect()
    try:
        rows = await conn.fetch(
            "SELECT time, open, high, low, close, volume FROM ohlcv "
            "WHERE symbol = $1 AND timeframe = $2 AND time >= $3 AND time < $4 "
            "ORDER BY time ASC",
            symbol, tf, start, end,
        )
    finally:
        await conn.close()
    return [
        {
            "time": r[0] if r[0].tzinfo else r[0].replace(tzinfo=timezone.utc),
            "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
            "close": float(r[4]), "volume": float(r[5] or 0),
        }
        for r in rows
    ]


def load_candles(symbol: str, tf: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    return asyncio.run(_load(symbol, tf, start, end))


async def _proof() -> dict[str, Any]:
    conn = await _connect()
    out: dict[str, Any] = {}
    try:
        out["default_transaction_read_only"] = await conn.fetchval(
            "SHOW default_transaction_read_only"
        )
        out["transaction_read_only"] = await conn.fetchval("SHOW transaction_read_only")
        try:
            await conn.execute("CREATE TEMP TABLE _strategy_rank_probe (x int)")
            out["write_probe"] = "UNEXPECTEDLY_SUCCEEDED"
        except Exception as exc:  # noqa: BLE001
            out["write_probe"] = f"REJECTED: {type(exc).__name__}: {exc}"
    finally:
        await conn.close()
    return out


def read_only_proof() -> dict[str, Any]:
    return asyncio.run(_proof())


async def _coverage(symbols: list[str], tfs: list[str]) -> list[dict[str, Any]]:
    conn = await _connect()
    try:
        rows = await conn.fetch(
            "SELECT symbol, timeframe, min(time), max(time), count(*) FROM ohlcv "
            "WHERE symbol = ANY($1) AND timeframe = ANY($2) GROUP BY 1,2 ORDER BY 1,2",
            symbols, tfs,
        )
    finally:
        await conn.close()
    return [
        {"symbol": r[0], "timeframe": r[1], "min": r[2].isoformat(),
         "max": r[3].isoformat(), "count": r[4]}
        for r in rows
    ]


def coverage(symbols: list[str], tfs: list[str]) -> list[dict[str, Any]]:
    return asyncio.run(_coverage(symbols, tfs))
