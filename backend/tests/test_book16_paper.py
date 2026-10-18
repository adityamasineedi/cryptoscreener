"""In-app virtual paper for 4h book 16: same trades as the screen ledger from START + 1 bar, incremental
passes equal one pass, booked trades never change, nothing before START, read-only route."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.services import book16_paper as bp

BAR = pd.Timedelta(hours=4)
SYN_START = pd.Timestamp("2021-06-01", tz="UTC") + 1100 * BAR


def _frame(seed: int, n: int = 3600) -> pd.DataFrame:
    r = np.random.default_rng(seed)
    idx = pd.date_range("2021-06-01", periods=n, freq="4h", tz="UTC")
    c = 30000 * np.exp(np.cumsum(r.normal(0.0004, 0.016, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(r.normal(0, 0.002, n))
    h = np.maximum(o, c) * np.exp(np.abs(r.normal(0, 0.006, n)))
    l = np.minimum(o, c) * np.exp(-np.abs(r.normal(0, 0.006, n)))
    return pd.DataFrame(dict(open=o, high=h, low=l, close=c), index=idx)


FRAMES = {"BTCUSDT": _frame(1), "ETHUSDT": _frame(2)}
_r = np.random.default_rng(9)
FUND_IDX = pd.date_range(pd.Timestamp("2021-06-01", tz="UTC"), FRAMES["BTCUSDT"].index[-1] + BAR, freq="8h")
FUNDING = {s: pd.Series(_r.normal(1e-4, 2e-4, len(FUND_IDX)), index=FUND_IDX) for s in FRAMES}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, "START", SYN_START)
    state = {"now": None, "drop": None}

    async def fake_range(symbol, tf, *, start, end_exclusive, warmup_bars=0):
        assert tf == "4h" and end_exclusive is None
        d = FRAMES[symbol]
        d = d[d.index >= pd.Timestamp(start)]
        if state["drop"] is not None:
            d = d.drop(d.index[state["drop"]])
        # The DB may hold the still-forming bar: the service must drop it.
        d = d[d.index <= state["now"]]
        return [{"time": t.to_pydatetime(), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                 "volume": 1.0} for t, r in zip(d.index, d.itertuples())]

    async def fake_fapi(client, path, params):
        if path == "/fapi/v1/fundingInfo":
            return [{"symbol": s, "fundingIntervalHours": 8} for s in FRAMES]
        f = FUNDING[params["symbol"]]
        lo = pd.Timestamp(params["startTime"], unit="ms", tz="UTC")
        hi = pd.Timestamp(params["endTime"], unit="ms", tz="UTC")
        f = f[(f.index >= lo) & (f.index <= hi)].iloc[: params["limit"]]
        return [{"fundingTime": int(t.value // 10**6), "fundingRate": str(v)} for t, v in f.items()]

    monkeypatch.setattr("app.research.postgres_ohlcv.load_ohlcv_series_range", fake_range)
    monkeypatch.setattr(bp, "_fapi", fake_fapi)
    return state, tmp_path


def _sync(state, path, now: pd.Timestamp):
    state["now"] = now
    return asyncio.run(bp.sync(now.to_pydatetime(), path=path))


def _expected(symbol: str, now: pd.Timestamp) -> list[dict]:
    from app.research.daily_trend.books import build_books
    from app.research.daily_trend.funding import FundingSchedule

    m = bp._screen()
    d = FRAMES[symbol]
    d = d[(d.index + BAR <= now) & (d.index >= SYN_START - 1000 * BAR)]
    d.index = d.index.as_unit("ns")
    f = FUNDING[symbol]
    f = f[(f.index >= SYN_START) & (f.index <= now)]
    fr = pd.DataFrame({"rate": f.to_numpy(), "documented_interval_hours": 8.0}, index=f.index.as_unit("ns"))
    b = build_books(d)[16]
    trades, _, _ = m.simulate_ledger(symbol, d, BAR, dict(state=b["state"], trig=b["trig"], kind="event"),
                                     np.full(len(d), m.RISK), 1001, len(d) - 1,
                                     FundingSchedule(fr, d.index, d["open"].to_numpy()))
    return trades


def test_frozen_constants_match_fwd1():
    assert bp.START == pd.Timestamp("2026-10-19 00:00", tz="UTC")
    assert bp.SYMBOLS == ("BTCUSDT", "ETHUSDT") and bp.WARMUP_BARS == 1000 and bp.MIRRORS == "BOOK16_4H_FWD-1"


def test_waiting_before_first_forward_bar_closes(env):
    state, tmp = env
    out = _sync(state, tmp / "s.json", SYN_START + BAR - pd.Timedelta(minutes=1))
    assert out["status"] == "WAITING_FOR_FIRST_FORWARD_BAR"
    assert bp.load_state(tmp / "s.json")["books"]["BTCUSDT"]["closed"] == []


def test_trades_match_screen_and_incremental_equals_single(env):
    state, tmp = env
    end = FRAMES["BTCUSDT"].index[-1] + BAR + pd.Timedelta(minutes=10)
    t, steps = SYN_START + BAR + pd.Timedelta(minutes=5), []
    while t < end:
        steps.append(t)
        t += BAR if len(steps) < 20 else 53 * BAR
    steps.append(end)
    for now in steps:
        assert _sync(state, tmp / "inc.json", now)["status"] == "COLLECTING"
    _sync(state, tmp / "one.json", end)
    inc, one = bp.load_state(tmp / "inc.json"), bp.load_state(tmp / "one.json")
    for s in bp.SYMBOLS:
        key = lambda b: [json.dumps(x, sort_keys=True) for x in b["closed"]]  # noqa: E731
        assert key(inc["books"][s]) == key(one["books"][s])
        want = [w for w in _expected(s, end) if w["exit_reason"] != "window_end"]
        got = one["books"][s]["closed"] + one["books"][s]["pending_closed"]
        assert len(got) == len(want) > 3
        for g, w in zip(got, want):
            assert (g["entry_time"], g["exit_time"], g["exit_reason"]) == (w["entry_time"], w["exit_time"],
                                                                           w["exit_reason"])
            assert g["net_pnl"] == pytest.approx(w["net_pnl"], rel=1e-12)
            assert pd.Timestamp(g["entry_time"]) >= SYN_START + BAR


def test_booked_trade_change_stops_the_book(env):
    state, tmp = env
    p = tmp / "s.json"
    _sync(state, p, SYN_START + 900 * BAR)
    st = bp.load_state(p)
    s = next(s for s in bp.SYMBOLS if st["books"][s]["closed"])
    st["books"][s]["closed"][0]["net_pnl"] += 1.0
    bp.save_state(st, p)
    out = _sync(state, p, SYN_START + 901 * BAR)
    assert out["status"] == "INTEGRITY"
    assert _sync(state, p, SYN_START + 950 * BAR)["status"] == "INTEGRITY"   # stays stopped


def test_data_gap_is_an_error_and_retried(env):
    state, tmp = env
    state["drop"] = 1500
    out = _sync(state, tmp / "s.json", SYN_START + 600 * BAR)
    assert out["status"] == "ERROR" and "DATA_GAP" in out["error"]
    state["drop"] = None
    assert _sync(state, tmp / "s.json", SYN_START + 601 * BAR)["status"] == "COLLECTING"


def test_route_is_read_only(monkeypatch, tmp_path):
    from app.api import routes

    p = tmp_path / "s.json"
    monkeypatch.setattr(bp, "STATE_PATH", p)
    out = asyncio.run(routes.paper_book16())
    assert out["mirrors"] == "BOOK16_4H_FWD-1" and out["live_orders"] is False and "loop_enabled" in out
    assert not p.exists()
