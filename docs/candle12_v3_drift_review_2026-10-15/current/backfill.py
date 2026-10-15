"""Mainnet candle and funding backfill for the V3 paper adapter.

- Fetches closed 1h klines and funding settlements from Binance's public mainnet
  market-data API (read-only; no order endpoint exists here).
- Writes them with idempotent inserts (ON CONFLICT DO NOTHING): a stored row is
  never overwritten. A fetched row that differs from the stored one is reported
  as a conflict, not applied.
- Never fabricates: a bar or settlement the exchange did not return is reported
  as a gap and left missing.
- Records the last successful backfill per symbol and data type in a JSON
  watermark file. A failed fetch leaves the watermark where it was.
"""

from __future__ import annotations

import asyncio
import json
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from app.research.candle12_v3_paper.feeds import BinanceFundingSource, BinanceKlineSource
from app.research.candle12_v3_paper.modes import MAINNET_DATA_BASE

UTC = timezone.utc
HOUR = timedelta(hours=1)
FUNDING_INTERVAL = timedelta(hours=8)
CANDLES = "candles_1h"
FUNDING = "funding"
SOURCE = "binance_fapi_candle12_v3_backfill"


class Repository(Protocol):
    def insert_candles(self, symbol: str, rows: Sequence[Mapping[str, Any]]) -> tuple[int, list[dict[str, Any]]]: ...

    def insert_funding(self, symbol: str, rows: Sequence[Mapping[str, Any]]) -> tuple[int, list[dict[str, Any]]]: ...

    def candle_times(self, symbol: str, start: datetime, end: datetime) -> list[datetime]: ...

    def funding_times(self, symbol: str, start: datetime, end: datetime) -> list[datetime]: ...


@dataclass
class InMemoryRepository:
    candles: dict[tuple[str, datetime], dict[str, Any]] = field(default_factory=dict)
    funding: dict[tuple[str, datetime], dict[str, Any]] = field(default_factory=dict)

    @staticmethod
    def _insert(table: dict, symbol: str, rows: Sequence[Mapping[str, Any]], keys: Sequence[str]):
        inserted, conflicts = 0, []
        for r in rows:
            key = (symbol, r["time"])
            if key in table:
                if any(table[key].get(k) != r.get(k) for k in keys):
                    conflicts.append({"time": r["time"].isoformat(), "stored": {k: table[key].get(k) for k in keys},
                                      "fetched": {k: r.get(k) for k in keys}})
                continue
            table[key] = dict(r)
            inserted += 1
        return inserted, conflicts

    def insert_candles(self, symbol, rows):
        return self._insert(self.candles, symbol, rows, ("open", "high", "low", "close", "volume"))

    def insert_funding(self, symbol, rows):
        return self._insert(self.funding, symbol, rows, ("rate",))

    def candle_times(self, symbol, start, end):
        return sorted(t for (s, t) in self.candles if s == symbol and start <= t < end)

    def funding_times(self, symbol, start, end):
        return sorted(t for (s, t) in self.funding if s == symbol and start <= t < end)


class PostgresRepository:
    """ohlcv (timeframe '1h') and grid_funding_settlements, the tables the paper CLI reads."""

    def _run(self, coro_fn: Callable[[Any], Any]) -> Any:
        import sys

        from app.research.candle12_v3_paper import contract as C

        sys.path.insert(0, str(C.BACKEND / "scripts" / "strategy_rank" / "multicap"))
        import _db  # type: ignore[import-not-found]

        import asyncpg

        async def go() -> Any:
            conn = await asyncpg.connect(_db._dsn())  # writable; _db._connect is read-only by design
            try:
                return await coro_fn(conn)
            finally:
                await conn.close()

        return asyncio.run(go())

    def insert_candles(self, symbol, rows):
        async def go(conn):
            inserted, conflicts = 0, []
            for r in rows:
                status = await conn.execute(
                    "INSERT INTO ohlcv (time, symbol, timeframe, open, high, low, close, volume, source) "
                    "VALUES ($1,$2,'1h',$3,$4,$5,$6,$7,$8) ON CONFLICT (time, symbol, timeframe) DO NOTHING",
                    r["time"], symbol, r["open"], r["high"], r["low"], r["close"], r["volume"], SOURCE)
                if status.endswith(" 1"):
                    inserted += 1
                    continue
                row = await conn.fetchrow("SELECT open, high, low, close FROM ohlcv WHERE time=$1 AND symbol=$2 "
                                          "AND timeframe='1h'", r["time"], symbol)
                if row and any(abs(float(row[k]) - float(r[k])) > 1e-9 for k in ("open", "high", "low", "close")):
                    conflicts.append({"time": r["time"].isoformat(), "stored": dict(row),
                                      "fetched": {k: r[k] for k in ("open", "high", "low", "close")}})
            return inserted, conflicts

        return self._run(go)

    def insert_funding(self, symbol, rows):
        async def go(conn):
            inserted, conflicts = 0, []
            for r in rows:
                status = await conn.execute(
                    "INSERT INTO grid_funding_settlements (symbol, funding_time, funding_rate, mark_price) "
                    "VALUES ($1,$2,$3,$4) ON CONFLICT (symbol, funding_time) DO NOTHING",
                    symbol, r["time"], r["rate"], r.get("mark_price"))
                if status.endswith(" 1"):
                    inserted += 1
                    continue
                row = await conn.fetchrow("SELECT funding_rate FROM grid_funding_settlements WHERE symbol=$1 "
                                          "AND funding_time=$2", symbol, r["time"])
                if row and abs(float(row[0]) - float(r["rate"])) > 1e-12:
                    conflicts.append({"time": r["time"].isoformat(), "stored": float(row[0]), "fetched": r["rate"]})
            return inserted, conflicts

        return self._run(go)

    def candle_times(self, symbol, start, end):
        async def go(conn):
            rows = await conn.fetch("SELECT time FROM ohlcv WHERE symbol=$1 AND timeframe='1h' AND time>=$2 "
                                    "AND time<$3 ORDER BY time", symbol, start, end)
            return [r[0] if r[0].tzinfo else r[0].replace(tzinfo=UTC) for r in rows]

        return self._run(go)

    def funding_times(self, symbol, start, end):
        async def go(conn):
            rows = await conn.fetch("SELECT funding_time FROM grid_funding_settlements WHERE symbol=$1 AND "
                                    "funding_time>=$2 AND funding_time<$3 ORDER BY funding_time", symbol, start, end)
            return [r[0] if r[0].tzinfo else r[0].replace(tzinfo=UTC) for r in rows]

        return self._run(go)


# ------------------------------------------------------------------ gaps


def candle_gaps(times: Sequence[datetime], start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Missing hourly bars in [start, end), as ranges of bar-open times."""
    have = set(times)
    gaps, run_start, prev = [], None, None
    t = start
    while t < end:
        if t not in have:
            run_start = run_start or t
            prev = t
        elif run_start is not None:
            gaps.append({"from": run_start.isoformat(), "to": prev.isoformat(),
                         "bars": int((prev - run_start) / HOUR) + 1})
            run_start = None
        t += HOUR
    if run_start is not None:
        gaps.append({"from": run_start.isoformat(), "to": prev.isoformat(), "bars": int((prev - run_start) / HOUR) + 1})
    return gaps


def funding_gaps(times: Sequence[datetime], start: datetime, end: datetime,
                 interval: timedelta = FUNDING_INTERVAL) -> list[dict[str, Any]]:
    """Expected settlements (every ``interval`` from 00:00 UTC) in [start, end) with no stored row within 60 s."""
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    s = epoch + ((start - epoch + interval - timedelta(microseconds=1)) // interval) * interval
    missing = []
    stored = sorted(times)
    while s < end:
        if not any(abs((t - s).total_seconds()) < 60 for t in stored):
            missing.append(s.isoformat())
        s += interval
    return [{"settlement": m} for m in missing]


# ------------------------------------------------------------------ watermarks


def load_watermarks(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save_watermarks(path: Path, marks: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(marks, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ backfill


def backfill(
    symbols: Sequence[str],
    start: datetime,
    end: datetime,
    *,
    repo: Repository,
    state_path: Path,
    now: datetime | None = None,
    kline_source: Callable[[str], Any] | None = None,
    funding_source: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    end = min(end, now)
    kline_source = kline_source or (lambda s: BinanceKlineSource(s, "1h", base_url=MAINNET_DATA_BASE))
    funding_source = funding_source or (lambda s: BinanceFundingSource(s, base_url=MAINNET_DATA_BASE))
    marks = load_watermarks(state_path)
    report: dict[str, Any] = {"start": start.isoformat(), "end": end.isoformat(), "symbols": {}, "ok": True}
    for sym in [s.upper() for s in symbols]:
        out: dict[str, Any] = {}
        sym_marks = marks.setdefault(sym, {})
        # closed bars only: a bar is final once open + 1h <= now
        last_closed_open = datetime.fromtimestamp((int(end.timestamp()) // 3600) * 3600, tz=UTC) - HOUR
        bar_end = last_closed_open + HOUR
        try:
            bars = [b for b in kline_source(sym).history(start, now) if start <= b["time"] < bar_end]
            inserted, conflicts = repo.insert_candles(sym, bars)
            gaps = candle_gaps(repo.candle_times(sym, start, bar_end), start, bar_end)
            out[CANDLES] = {"fetched": len(bars), "inserted": inserted, "existing": len(bars) - inserted,
                            "conflicts": conflicts, "gaps": gaps, "status": "OK" if not gaps else "GAPS"}
            if bars:
                sym_marks[CANDLES] = {"last_success_at": now.isoformat(), "last_bar_open": bars[-1]["time"].isoformat()}
        except Exception as exc:  # noqa: BLE001 - reported, watermark untouched
            out[CANDLES] = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
            report["ok"] = False
        try:
            rows = [r for r in funding_source(sym).settlements(start, end) if start <= r["time"] < end]
            inserted, conflicts = repo.insert_funding(sym, rows)
            gaps = funding_gaps(repo.funding_times(sym, start, end), start, end)
            out[FUNDING] = {"fetched": len(rows), "inserted": inserted, "existing": len(rows) - inserted,
                            "conflicts": conflicts, "gaps": gaps, "status": "OK" if not gaps else "GAPS"}
            if rows:
                sym_marks[FUNDING] = {"last_success_at": now.isoformat(), "last_settlement": rows[-1]["time"].isoformat()}
        except Exception as exc:  # noqa: BLE001
            out[FUNDING] = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
            report["ok"] = False
        if any(v.get("conflicts") for v in out.values()):
            report["ok"] = False
        report["symbols"][sym] = out
    _save_watermarks(state_path, marks)
    report["watermarks"] = marks
    return report


def run_forever(make_report: Callable[[], dict[str, Any]], *, interval_s: float, max_loops: int | None = None,
                sleep: Callable[[float], None] = _time.sleep, emit: Callable[[dict[str, Any]], None] = print) -> int:
    """Foreground runner: backfill, report, sleep; repeat. Returns the number of failed passes."""
    failures, loops = 0, 0
    while max_loops is None or loops < max_loops:
        loops += 1
        report = make_report()
        failures += 0 if report.get("ok") else 1
        emit({"pass": loops, "at": datetime.now(UTC).isoformat(), "ok": report.get("ok"),
              "symbols": {s: {k: v.get("status") for k, v in d.items()} for s, d in report["symbols"].items()}})
        if max_loops is None or loops < max_loops:
            sleep(interval_s)
    return failures
