"""Append-only hourly long and short liquidation notionals.

Events are parsed by the existing force-order engine (`parse_force_order`).
A SELL is a long liquidation. Notional is order price × quantity, as E34
states. This process opens its own `!forceOrder@arr` socket; it does not
call the live ingestion object, so the engine's windows, alerts and
persistence are unchanged.

A hour is stored only when this process was running for the whole hour.
Hours before the start, hours lost to a restart, and the partial hour at
startup are absent, not zero. Quiet universe coins in a sealed hour are
stored as zero so they count as present for the p99.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.engines.liquidations.engine import LiquidationEvent, parse_force_order
from app.research.liq_flush.costs import SEAL_GRACE_SECONDS
from app.research.liq_flush.execute import floor_hour
from app.research.liq_flush.ledger import append_jsonl, read_jsonl, resolve_store
from app.research.liq_flush.universe import UNIVERSE

UTC = timezone.utc


def first_full_hour(now: datetime) -> datetime:
    """The first hour whose open this process can still observe in full."""
    now = now.astimezone(UTC)
    hour = floor_hour(now)
    if now == hour:
        return hour
    return hour + timedelta(hours=1)


@dataclass
class _Bucket:
    long_notional: float = 0.0
    short_notional: float = 0.0
    long_count: int = 0
    short_count: int = 0


@dataclass
class Recorder:
    store: Path
    symbols: tuple[str, ...] = UNIVERSE
    buckets: dict[tuple[str, datetime], _Bucket] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    late_dropped: int = 0
    observing_from: datetime | None = None
    resume_from: datetime | None = None
    last_sealed: datetime | None = None
    void_hours: set[datetime] = field(default_factory=set)

    @classmethod
    def open(cls, store: Path, *, production: bool = False) -> Recorder:
        path = resolve_store(store, production=production)
        path.mkdir(parents=True, exist_ok=True)
        rec = cls(store=path)
        rec._load()
        return rec

    @property
    def hourly_path(self) -> Path:
        return self.store / "hourly.jsonl"

    @property
    def meta_path(self) -> Path:
        return self.store / "recorder_meta.json"

    def begin_session(self, now: datetime) -> None:
        """Call once when the recorder process starts. A mid-hour start skips that hour."""
        self.resume_from = first_full_hour(now)
        if self.observing_from is None:
            self.observing_from = self.resume_from
        self._save_meta()

    def note_disconnect(self, now: datetime) -> None:
        """A dropped socket means this clock hour was not fully observed. It stays absent."""
        self.void_hours.add(floor_hour(now))
        self._save_meta()

    def observe(self, event: LiquidationEvent) -> None:
        if self.resume_from is None:
            raise RuntimeError("recorder session has not started")
        hour = floor_hour(event.event_time or event.timestamp)
        symbol = str(event.symbol).upper()
        if symbol not in self.symbols:
            return
        if hour < self.resume_from:
            return
        if self.last_sealed is not None and hour <= self.last_sealed:
            self.late_dropped += 1
            return
        side = _side(event.side)
        if side is None:
            return
        key = event.event_key
        if key:
            if key in self.seen:
                return
            self.seen.add(key)
        bucket = self.buckets.setdefault((symbol, hour), _Bucket())
        notional = float(event.price) * float(event.quantity)
        if side == "long":
            bucket.long_notional += notional
            bucket.long_count += 1
        else:
            bucket.short_notional += notional
            bucket.short_count += 1

    def seal_due(self, now: datetime) -> list[datetime]:
        """Append one row per universe coin for each hour that has fully closed."""
        if self.resume_from is None:
            raise RuntimeError("recorder session has not started")
        now = now.astimezone(UTC)
        grace = timedelta(seconds=SEAL_GRACE_SECONDS)
        if self.last_sealed is None:
            hour = self.resume_from
        else:
            hour = self.last_sealed + timedelta(hours=1)
            if hour < self.resume_from:
                hour = self.resume_from
        already = _sealed_hours(self.hourly_path)
        sealed: list[datetime] = []
        while hour + timedelta(hours=1) + grace <= now:
            if hour in self.void_hours:
                # Absent on purpose: a disconnect, not a zero hour.
                for symbol in self.symbols:
                    self.buckets.pop((symbol, hour), None)
                self.last_sealed = hour
                hour = hour + timedelta(hours=1)
                continue
            if hour not in already:
                rows = []
                for symbol in self.symbols:
                    bucket = self.buckets.pop((symbol, hour), _Bucket())
                    rows.append({
                        "complete": True,
                        "hour": _iso(hour),
                        "long_count": bucket.long_count,
                        "long_notional": bucket.long_notional,
                        "short_count": bucket.short_count,
                        "short_notional": bucket.short_notional,
                        "symbol": symbol,
                    })
                append_jsonl(self.hourly_path, rows)
                already.add(hour)
            else:
                for symbol in self.symbols:
                    self.buckets.pop((symbol, hour), None)
            self.last_sealed = hour
            sealed.append(hour)
            hour = hour + timedelta(hours=1)
        self._save_meta()
        return sealed

    def _load(self) -> None:
        hours = _sealed_hours(self.hourly_path)
        self.last_sealed = max(hours) if hours else None
        if not self.meta_path.exists():
            return
        meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
        self.observing_from = _parse(meta.get("observing_from"))
        self.resume_from = _parse(meta.get("resume_from"))
        self.late_dropped = int(meta.get("late_dropped") or 0)
        self.void_hours = set()
        for item in meta.get("void_hours") or []:
            parsed = _parse(item)
            if parsed is not None:
                self.void_hours.add(parsed)

    def _save_meta(self) -> None:
        payload = {
            "last_sealed": _iso(self.last_sealed) if self.last_sealed else None,
            "late_dropped": self.late_dropped,
            "observing_from": _iso(self.observing_from) if self.observing_from else None,
            "resume_from": _iso(self.resume_from) if self.resume_from else None,
            "void_hours": [_iso(hour) for hour in sorted(self.void_hours)],
        }
        tmp = self.meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.meta_path)


def ingest_payload(recorder: Recorder, payload: dict[str, Any], *, received_at: datetime | None = None) -> bool:
    """Parse one force-order object with the engine's parser and record it."""
    event = parse_force_order(payload, received_at=received_at or datetime.now(UTC))
    if event is None:
        return False
    recorder.observe(event)
    return True


def ingest_message(recorder: Recorder, data: Any, *, received_at: datetime | None = None) -> int:
    """Accept a raw socket frame, including a combined-stream envelope."""
    payload = data
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], (dict, list)) and "e" not in data:
        payload = data["data"]
    items = payload if isinstance(payload, list) else [payload]
    n = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if "result" in item and "e" not in item:
            continue
        if ingest_payload(recorder, item, received_at=received_at):
            n += 1
    return n


def load_hourly(path: Path) -> dict[str, dict[datetime, tuple[float, float]]]:
    """Complete hours only. The first row for a (symbol, hour) wins; the file is append-once."""
    out: dict[str, dict[datetime, tuple[float, float]]] = {}
    for row in read_jsonl(path):
        if not row.get("complete"):
            continue
        symbol = str(row["symbol"]).upper()
        hour = _parse(row["hour"])
        book = out.setdefault(symbol, {})
        if hour in book:
            continue
        book[hour] = (float(row["long_notional"]), float(row["short_notional"]))
    return out


def _side(side: str) -> str | None:
    # Same rule as the liquidation engine: SELL = long liquidation, BUY = short.
    if side == "SELL":
        return "long"
    if side == "BUY":
        return "short"
    return None


def _sealed_hours(path: Path) -> set[datetime]:
    return {_parse(row["hour"]) for row in read_jsonl(path) if row.get("hour")}


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).replace(microsecond=0).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
