"""Virtual book. One position per coin. The ledger is append-only; a torn
final line is ignored and that bar is processed again.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research.liq_flush import contract
from app.research.liq_flush.costs import PRICE_WARMUP
from app.research.liq_flush.execute import (
    Bar,
    FillResult,
    Position,
    floor_hour,
    plan_entry,
    try_exit,
)
from app.research.liq_flush.ledger import (
    append_jsonl,
    judge,
    read_jsonl,
    resolve_store,
    status_from_events,
)
from app.research.liq_flush.signal import (
    first_complete_hour,
    side_at,
    window_values,
)
from app.research.liq_flush.universe import UNIVERSE

UTC = timezone.utc


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).replace(microsecond=0).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def trade_id(symbol: str, side: str, entry_hour: datetime) -> str:
    return f"{symbol}|{side}|{_iso(entry_hour)}"


def align_bars(origin: datetime, bars: Sequence[Bar]) -> tuple[list[Bar], datetime | None]:
    """Contiguous closed bars from ``origin``. A hole is a data gap.

    The returned gap is the first missing hour at or before the last bar
    we actually received. A short tail (the exchange has not published the
    latest hour yet) is not a gap.
    """
    by = {bar.open_time: bar for bar in bars if bar.open_time >= origin}
    if not by:
        return [], None
    last = max(by)
    out: list[Bar] = []
    hour = origin
    while hour <= last:
        bar = by.get(hour)
        if bar is None:
            return out, hour
        out.append(bar)
        hour += timedelta(hours=1)
    return out, None


class Book:
    def __init__(self, store: Path, *, production: bool = False) -> None:
        self.store = resolve_store(store, production=production)
        self.store.mkdir(parents=True, exist_ok=True)
        self.path = self.store / "ledger.jsonl"

    def events(self) -> list[dict[str, Any]]:
        return read_jsonl(self.path)

    def status(self, now: datetime) -> dict[str, Any]:
        return status_from_events(self.events(), now)

    def ensure_start(self, now: datetime) -> dict[str, Any]:
        now = now.astimezone(UTC)
        existing = self.events()
        start = next((row for row in existing if row.get("type") == "start"), None)
        fingerprint = contract.fingerprint()
        if start is None:
            origin = floor_hour(now) - timedelta(hours=PRICE_WARMUP)
            start = {
                "at": _iso(now),
                "code_fingerprint": fingerprint,
                "contract_id": contract.CONTRACT_ID,
                "origin": _iso(origin),
                "type": "start",
                "universe": list(UNIVERSE),
            }
            append_jsonl(self.path, [start])
            return start
        if start.get("code_fingerprint") != fingerprint:
            self.halt("FINGERPRINT", now)
        return start

    def halted(self) -> str | None:
        for row in self.events():
            if row.get("type") == "halt":
                return str(row.get("code"))
        return None

    def halt(self, code: str, now: datetime, **extra: Any) -> None:
        if self.halted():
            return
        row = {"at": _iso(now), "code": code, "type": "halt"}
        row.update(extra)
        append_jsonl(self.path, [row])

    def heartbeat(self, now: datetime) -> None:
        state = self.status(now)
        append_jsonl(self.path, [{
            "at": _iso(now),
            "closed_trades": state["closed_trades"],
            "open_trades": state["open_trades"],
            "read": state["read"],
            "type": "heartbeat",
        }])

    def advance(
        self,
        bars_by_symbol: Mapping[str, Sequence[Bar]],
        funding_by_symbol: Mapping[str, Sequence[tuple[int, float]]],
        liq: Mapping[str, Mapping[datetime, tuple[float, float]]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        """Apply newly closed bars. Returns the ledger rows appended."""
        now = now.astimezone(UTC)
        if contract.drift():
            self.halt("CODE_DRIFT", now)
            return self.events()[-1:]
        self.ensure_start(now)
        if self.halted():
            return []
        written: list[dict[str, Any]] = []
        for symbol in UNIVERSE:
            if self.halted():
                break
            aligned, gap = align_bars(self.origin(), bars_by_symbol.get(symbol, ()))
            rows = self._advance_symbol(
                symbol,
                aligned,
                funding_by_symbol.get(symbol, ()),
                liq.get(symbol, {}),
            )
            written.extend(rows)
            if gap is not None:
                self.halt("DATA_GAP", now, symbol=symbol, missing_hour=_iso(gap))
                written.append(self.events()[-1])
                break
        return written

    def origin(self) -> datetime:
        start = next(row for row in self.events() if row.get("type") == "start")
        return _parse(start["origin"])

    def _advance_symbol(
        self,
        symbol: str,
        bars: Sequence[Bar],
        funding: Sequence[tuple[int, float]],
        liq: Mapping[datetime, tuple[float, float]],
    ) -> list[dict[str, Any]]:
        done = {
            _parse(row["bar"])
            for row in self.events()
            if row.get("type") == "step" and row.get("symbol") == symbol
        }
        pos, last_exit = _restore(symbol, self.events(), self.origin())
        long_map = {hour: values[0] for hour, values in liq.items()}
        short_map = {hour: values[1] for hour, values in liq.items()}
        written: list[dict[str, Any]] = []
        for index, bar in enumerate(bars):
            if bar.open_time in done:
                continue
            if index != _hour_index(self.origin(), bar.open_time):
                self.halt("BAR_INDEX", datetime.now(UTC), symbol=symbol, bar=_iso(bar.open_time))
                written.append(self.events()[-1])
                return written
            pos, last_exit, opened, closed, reject = _on_bar(
                index, bars, long_map, short_map, funding, symbol, pos, last_exit,
            )
            row: dict[str, Any] = {
                "bar": _iso(bar.open_time),
                "close": closed,
                "open": opened,
                "reject": reject,
                "symbol": symbol,
                "type": "step",
            }
            append_jsonl(self.path, [row])
            written.append(row)
            done.add(bar.open_time)
            if closed and _maybe_read(self.path):
                written.append(self.events()[-1])
        return written


def _hour_index(origin: datetime, hour: datetime) -> int:
    return int((hour - origin).total_seconds() // 3600)


def _restore(symbol: str, events: Sequence[dict[str, Any]], origin: datetime) -> tuple[Position | None, int]:
    pos: Position | None = None
    last_exit = -1
    for row in events:
        if row.get("type") != "step" or row.get("symbol") != symbol:
            continue
        opened = row.get("open")
        closed = row.get("close")
        if opened and (pos is None or pos.entry_hour != _parse(opened["entry_hour"])):
            pos = _position_from(opened, origin)
        if closed:
            last_exit = _hour_index(origin, _parse(closed["exit_hour"]))
            pos = None
    return pos, last_exit


def _position_from(row: dict[str, Any], origin: datetime) -> Position:
    entry_hour = _parse(row["entry_hour"])
    signal_hour = _parse(row["signal_hour"])
    return Position(
        symbol=row["symbol"],
        side=row["side"],
        signal_index=_hour_index(origin, signal_hour),
        entry_index=_hour_index(origin, entry_hour),
        signal_hour=signal_hour,
        entry_hour=entry_hour,
        fill=float(row["fill"]),
        stop=float(row["stop"]),
        target=float(row["target"]),
        qty=float(row["qty"]),
        capped=bool(row["capped"]),
        slip=float(row["slip"]),
        atr=float(row["atr"]),
    )


def _on_bar(
    index: int,
    bars: Sequence[Bar],
    long_map: Mapping[datetime, float],
    short_map: Mapping[datetime, float],
    funding: Sequence[tuple[int, float]],
    symbol: str,
    pos: Position | None,
    last_exit: int,
) -> tuple[Position | None, int, dict[str, Any] | None, dict[str, Any] | None, str | None]:
    opened = None
    closed = None
    reject = None
    if pos is not None and index >= pos.entry_index:
        result = try_exit(pos, bars, index, funding)
        if result is not None:
            closed = _close_row(pos, result)
            last_exit = result.exit_index
            pos = None
    if pos is None and index >= 2:
        signal_index = index - 2
        if signal_index > last_exit:
            side = _side_for(bars, signal_index, long_map, short_map)
            if side == "BOTH":
                reject = "both_sides"
            elif side in ("LONG", "SHORT"):
                planned = plan_entry(bars, signal_index, side, symbol)
                if planned is None:
                    reject = "invalid_stop"
                else:
                    pos = planned
                    opened = _open_row(pos)
                    result = try_exit(pos, bars, index, funding)
                    if result is not None:
                        closed = _close_row(pos, result)
                        last_exit = result.exit_index
                        pos = None
    return pos, last_exit, opened, closed, reject


def _side_for(
    bars: Sequence[Bar],
    signal_index: int,
    long_map: Mapping[datetime, float],
    short_map: Mapping[datetime, float],
) -> str | None:
    bar = bars[signal_index]
    nxt = bars[signal_index + 1]
    hour = bar.open_time
    return side_at(
        long_now=long_map.get(hour),
        short_now=short_map.get(hour),
        long_prior=window_values(long_map, hour),
        short_prior=window_values(short_map, hour),
        first_long=first_complete_hour(long_map),
        first_short=first_complete_hour(short_map),
        bar_open=hour,
        open_=bar.o,
        high=bar.h,
        low=bar.l,
        close=bar.c,
        next_close=nxt.c,
    )


def _open_row(pos: Position) -> dict[str, Any]:
    return {
        "atr": pos.atr,
        "capped": pos.capped,
        "entry_hour": _iso(pos.entry_hour),
        "fill": pos.fill,
        "qty": pos.qty,
        "side": pos.side,
        "signal_hour": _iso(pos.signal_hour),
        "slip": pos.slip,
        "stop": pos.stop,
        "symbol": pos.symbol,
        "target": pos.target,
        "trade_id": trade_id(pos.symbol, pos.side, pos.entry_hour),
    }


def _close_row(pos: Position, result: FillResult) -> dict[str, Any]:
    return {
        "capped": pos.capped,
        "exit_hour": _iso(result.exit_hour),
        "exit_px": result.exit_px,
        "fees": result.fees,
        "funding": result.funding,
        "gross": result.gross,
        "net": result.net,
        "r": result.r,
        "raw": result.raw,
        "reason": result.reason,
        "side": pos.side,
        "symbol": pos.symbol,
        "trade_id": trade_id(pos.symbol, pos.side, pos.entry_hour),
    }


def _maybe_read(path: Path) -> bool:
    events = read_jsonl(path)
    if any(row.get("type") == "read" for row in events):
        return False
    closes = []
    for row in events:
        if row.get("type") == "step" and row.get("close"):
            closes.append(row["close"])
    if len(closes) < 30:
        return False
    append_jsonl(path, [judge(closes)])
    return True


def closed_trades(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row["close"] for row in events if row.get("type") == "step" and row.get("close")]
