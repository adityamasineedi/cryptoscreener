"""Read-only market and ledger data for the alert sidecar.

Postgres is opened in read-only mode (session default and every transaction READ ONLY): a write would fail
at the server. Nothing here writes anywhere.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from app.alerts.config import timeframe_minutes
from app.alerts.plan import utc


class MarketData(Protocol):
    def bars_upto(self, symbol: str, timeframe: str, bar_time: datetime, limit: int) -> list[dict[str, Any]]: ...

    def closed_bars(self, symbol: str, timeframe: str, start: datetime, now: datetime) -> list[dict[str, Any]]: ...

    def paper_trade(self, position_id: str) -> dict[str, Any] | None: ...

    def v1_trade(self, symbol: str, bar_iso: str) -> dict[str, Any] | None: ...

    def paper_trades_opened(self, start: datetime, end: datetime) -> list[dict[str, Any]]: ...


def _row(r: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(r)
    snip = out.get("signal_snippet")
    if isinstance(snip, str):
        try:
            out["signal_snippet"] = json.loads(snip)
        except ValueError:
            out["signal_snippet"] = {}
    return out


class PostgresReadOnly:
    """asyncpg on a private event loop; every query in a READ ONLY transaction."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        self._loop = asyncio.new_event_loop()
        self._conn: Any = None

    def _run(self, coro: Any) -> Any:
        return self._loop.run_until_complete(coro)

    async def _connect(self) -> Any:
        import asyncpg

        if self._conn is None or self._conn.is_closed():
            self._conn = await asyncpg.connect(
                self._dsn, server_settings={"default_transaction_read_only": "on", "application_name": "trade_alerts_ro"}
            )
        return self._conn

    async def _fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        conn = await self._connect()
        async with conn.transaction(readonly=True):
            rows = await conn.fetch(query, *args)
        return [dict(r) for r in rows]

    def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        return self._run(self._fetch(query, *args))

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._run(self._conn.close())
            except Exception:  # noqa: BLE001
                pass
        self._loop.close()

    # --- MarketData ------------------------------------------------------------------------------
    def bars_upto(self, symbol: str, timeframe: str, bar_time: datetime, limit: int) -> list[dict[str, Any]]:
        rows = self.fetch(
            "SELECT time, open, high, low, close FROM ohlcv WHERE symbol=$1 AND timeframe=$2 AND time <= $3 "
            "ORDER BY time DESC LIMIT $4",
            symbol.upper(), timeframe, utc(bar_time), int(limit),
        )
        return list(reversed(rows))

    def closed_bars(self, symbol: str, timeframe: str, start: datetime, now: datetime) -> list[dict[str, Any]]:
        last_open = utc(now) - timedelta(minutes=timeframe_minutes(timeframe))
        return self.fetch(
            "SELECT time, open, high, low, close FROM ohlcv WHERE symbol=$1 AND timeframe=$2 AND time >= $3 "
            "AND time <= $4 ORDER BY time",
            symbol.upper(), timeframe, utc(start), last_open,
        )

    def paper_trade(self, position_id: str) -> dict[str, Any] | None:
        rows = self.fetch(
            "SELECT id, symbol, side, status, entry_price, stop_price, tp1_price, opened_at, source_candle_ts, "
            "timeframe, signal_snippet FROM paper_trades WHERE id=$1",
            str(position_id),
        )
        return _row(rows[0]) if rows else None

    def v1_trade(self, symbol: str, bar_iso: str) -> dict[str, Any] | None:
        rows = self.fetch(
            "SELECT id, symbol, side, status, entry_price, stop_price, tp1_price, opened_at, source_candle_ts, "
            "timeframe, signal_snippet FROM paper_trades WHERE symbol=$1 AND source_candle_ts=$2 "
            "AND signal_snippet->>'source'='V1_PAPER_WATCHER' ORDER BY opened_at LIMIT 1",
            symbol.upper(), str(bar_iso),
        )
        return _row(rows[0]) if rows else None

    def paper_trades_opened(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        rows = self.fetch(
            "SELECT id, symbol, side, status, entry_price, stop_price, tp1_price, opened_at, source_candle_ts, "
            "timeframe, signal_snippet FROM paper_trades WHERE opened_at >= $1 AND opened_at < $2 "
            "AND status IN ('OPEN','CLOSED') ORDER BY opened_at",
            utc(start), utc(end),
        )
        return [_row(r) for r in rows]


class MemoryData:
    """In-memory MarketData for tests and replays."""

    def __init__(
        self,
        bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]] | None = None,
        trades: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        self.bars = {(s.upper(), tf): sorted((dict(b) for b in v), key=lambda b: utc(b["time"]))
                     for (s, tf), v in (bars or {}).items()}
        self.trades = [dict(t) for t in (trades or [])]

    def bars_upto(self, symbol: str, timeframe: str, bar_time: datetime, limit: int) -> list[dict[str, Any]]:
        rows = [b for b in self.bars.get((symbol.upper(), timeframe), []) if utc(b["time"]) <= utc(bar_time)]
        return rows[-int(limit):]

    def closed_bars(self, symbol: str, timeframe: str, start: datetime, now: datetime) -> list[dict[str, Any]]:
        tf = timedelta(minutes=timeframe_minutes(timeframe))
        return [b for b in self.bars.get((symbol.upper(), timeframe), [])
                if utc(b["time"]) >= utc(start) and utc(b["time"]) + tf <= utc(now)]

    def paper_trade(self, position_id: str) -> dict[str, Any] | None:
        return next((t for t in self.trades if str(t.get("id")) == str(position_id)), None)

    def v1_trade(self, symbol: str, bar_iso: str) -> dict[str, Any] | None:
        return next((t for t in self.trades if t.get("symbol") == symbol.upper()
                     and str(t.get("source_candle_ts")) == str(bar_iso)
                     and (t.get("signal_snippet") or {}).get("source") == "V1_PAPER_WATCHER"), None)

    def paper_trades_opened(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return [t for t in self.trades if utc(start) <= utc(t["opened_at"]) < utc(end)
                and t.get("status") in ("OPEN", "CLOSED")]


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_env_values(path: Path, names: Sequence[str]) -> dict[str, str]:
    """Only the named variables from an env file. Values are never logged or printed."""
    import logging

    from dotenv import dotenv_values

    # dotenv warns about lines it cannot parse (line numbers only); keep the sidecar output clean.
    logging.getLogger("dotenv.main").setLevel(logging.ERROR)
    try:
        values = dotenv_values(Path(path))
    except OSError:
        return {}
    return {n: str(values[n]).strip() for n in names if values.get(n)}
