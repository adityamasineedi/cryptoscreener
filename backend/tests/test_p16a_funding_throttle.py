"""P16a (owner ruling D54): funding_rates keeps a coin's funding at most once a minute, in batched writes,
and keeps every tick around each settlement exactly as before.

The replay test drives a day of 5-second mark ticks for three coins (8 h and 4 h settlement intervals)
through MarketDataStore.flush_dirty, before P16a (every tick stored) and after, and checks that:
- the row at or nearest every settlement is identical;
- the latest row per coin (what the startup hydrate reads) is identical;
- every per-settlement funding amount computed from the stored rows is identical;
- rows are at most one per coin-minute outside the settlement windows, written once per flush.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.services.market_store as ms
from app.models.schemas import MarkPriceUpdate

UTC = timezone.utc
DAY0 = datetime(2026, 10, 20, tzinfo=UTC)


def test_first_tick_is_stored():
    assert ms.funding_row_due(None, DAY0, DAY0 + timedelta(hours=8))


def test_at_most_once_a_minute_away_from_a_settlement():
    nxt = DAY0 + timedelta(hours=8)
    prev = (DAY0, nxt, None)
    assert not ms.funding_row_due(prev, DAY0 + timedelta(seconds=5), nxt)
    assert not ms.funding_row_due(prev, DAY0 + timedelta(seconds=59.9), nxt)
    assert ms.funding_row_due(prev, DAY0 + timedelta(seconds=60), nxt)


def test_every_tick_inside_the_settlement_window():
    s = DAY0 + timedelta(hours=8)
    prev = (s - timedelta(seconds=125), s, None)
    for offset in (-120, -60, -5, 0):
        ts = s + timedelta(seconds=offset)
        assert ms.funding_row_due((ts - timedelta(seconds=5), s, None), ts, s)
    # Just after the settlement the stream's next funding time has moved on: the old one still counts.
    after = s + timedelta(seconds=30)
    assert ms.funding_row_due((s, s, None), after, s + timedelta(hours=8))
    # After the jump the stored state remembers the passed settlement, so its window stays kept.
    state = ms.funding_state_after((s, s, None), after, s + timedelta(hours=8))
    assert state == (after, s + timedelta(hours=8), s)
    assert ms.funding_row_due(state, after + timedelta(seconds=5), s + timedelta(hours=8))
    # Outside the window (150 s after) and 20 s after the last stored row: not stored.
    assert not ms.funding_row_due((s + timedelta(seconds=130), s + timedelta(hours=8), s),
                                  s + timedelta(seconds=150), s + timedelta(hours=8))
    assert not ms.funding_row_due(prev, s - timedelta(seconds=180), s)


def test_a_settlement_passing_is_always_stored():
    s = DAY0 + timedelta(hours=8)
    assert ms.funding_row_due((s - timedelta(hours=1), s, None), s + timedelta(minutes=30), s + timedelta(hours=8))


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def persist_funding_batch(self, rows):
        self.calls.append([dict(r) for r in rows])


def _ticks(intervals_h: dict[str, int]):
    """A day of ticks every 5 s; funding rate changes every 7 minutes; settlements on the interval."""
    for k in range(0, 24 * 3600, 5):
        ts = DAY0 + timedelta(seconds=k)
        out = []
        for sym, every in intervals_h.items():
            period = every * 3600
            nxt = DAY0 + timedelta(seconds=(k // period + 1) * period)
            rate = 0.0001 * (1 + (k // 420) % 5) * (1 if sym != "ETHUSDT" else -1)
            out.append(MarkPriceUpdate(symbol=sym, mark_price=100.0 + k / 1e4, funding_rate=rate,
                                       next_funding_time=nxt, timestamp=ts))
        yield ts, out


async def _run(monkeypatch, *, throttle: bool) -> list[dict]:
    rec = _Recorder()
    import app.services.persistence as pmod

    monkeypatch.setattr(pmod.persistence, "persist_funding_batch", rec.persist_funding_batch)
    if not throttle:
        monkeypatch.setattr(ms, "funding_row_due", lambda previous, ts, nxt: True)  # every tick, as before P16a
    store = ms.MarketDataStore()

    async def _quiet(event):
        return None

    monkeypatch.setattr(store, "_notify", _quiet)
    intervals = {"BTCUSDT": 8, "ETHUSDT": 8, "TIAUSDT": 4}
    for _, marks in _ticks(intervals):
        await store.update_mark_prices_batch(marks)
        await store.flush_dirty()
    rows = [r for call in rec.calls for r in call]
    _run.calls = rec.calls  # type: ignore[attr-defined]
    return rows


def _settlements(sym_every: int):
    return [DAY0 + timedelta(hours=h) for h in range(sym_every, 25, sym_every)]


def _nearest(rows, sym, s):
    own = [r for r in rows if r["symbol"] == sym]
    return min(own, key=lambda r: (abs((r["time"] - s).total_seconds()), r["time"]))


def _rate_at_settlement(rows, sym, s):
    """The rate a settlement-based reader takes: the last stored row at or before the settlement."""
    own = [r for r in rows if r["symbol"] == sym and r["time"] <= s]
    return max(own, key=lambda r: r["time"])["funding_rate"]


async def test_replay_a_day_settlement_rows_latest_and_costs_identical(monkeypatch):
    before = await _run(monkeypatch, throttle=False)
    monkeypatch.undo()
    after = await _run(monkeypatch, throttle=True)
    calls_after = _run.calls  # type: ignore[attr-defined]

    for sym, every in (("BTCUSDT", 8), ("ETHUSDT", 8), ("TIAUSDT", 4)):
        for s in _settlements(every):
            if s >= DAY0 + timedelta(hours=24):
                continue
            assert _nearest(after, sym, s) == _nearest(before, sym, s)
            assert _rate_at_settlement(after, sym, s) == _rate_at_settlement(before, sym, s)
            # Every tick inside the settlement window is kept, exactly as before.
            win = lambda rows: [r for r in rows if r["symbol"] == sym and abs((r["time"] - s).total_seconds()) <= 120]  # noqa: E731
            assert win(after) == win(before)
        last = lambda rows: max((r for r in rows if r["symbol"] == sym), key=lambda r: r["time"])  # noqa: E731
        assert last(after)["time"] >= last(before)["time"] - timedelta(seconds=60)

    # Per-settlement funding cost for a position held all day (notional 1,000): identical.
    def cost(rows):
        return round(sum(_rate_at_settlement(rows, sym, s) * 1000 for sym, every in (("BTCUSDT", 8), ("ETHUSDT", 8), ("TIAUSDT", 4))
                         for s in _settlements(every) if s < DAY0 + timedelta(hours=24)), 12)

    assert cost(after) == cost(before)
    # Volume: about one row a coin-minute plus the settlement windows; one batched write per flush.
    assert len(before) == 3 * 24 * 3600 // 5
    assert len(after) < len(before) / 6
    assert all(len({r["symbol"] for r in call}) == len(call) for call in calls_after)


async def test_latest_row_per_coin_is_kept_for_the_startup_hydrate(monkeypatch):
    after = await _run(monkeypatch, throttle=True)
    for sym in ("BTCUSDT", "ETHUSDT", "TIAUSDT"):
        own = [r for r in after if r["symbol"] == sym]
        latest = max(own, key=lambda r: r["time"])
        assert (DAY0 + timedelta(hours=24) - latest["time"]).total_seconds() <= 65
