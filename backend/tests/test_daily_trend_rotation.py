"""Walk-forward rotation: pre-registered rule, no look-ahead, sizing (docs/daily_trend_rotation_prereg.md)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.research.daily_trend import rotation as rot
from app.research.daily_trend.books import TAB_BOOKS, build_books
from app.research.engine_backtest import ENGINE_STRATEGIES, run_engine_cell

PREREG = Path(__file__).resolve().parents[2] / "docs" / "daily_trend_rotation_prereg.md"


def _synthetic(seed: int = 3, n: int = 1500) -> pd.DataFrame:
    r = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-01", periods=n, freq="D", tz="UTC")
    c = 30000 * np.exp(np.cumsum(r.normal(0.0004, 0.035, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(r.normal(0, 0.004, n))
    h = np.maximum(o, c) * np.exp(np.abs(r.normal(0, 0.015, n)))
    l = np.minimum(o, c) * np.exp(-np.abs(r.normal(0, 0.015, n)))
    return pd.DataFrame(dict(open=o, high=h, low=l, close=c), index=idx)


def test_prereg_document_is_unchanged():
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == (
        "24da2ebd557cbd424a1807497eb1d3029d6ba3beb5c81a20070f8ba18ec13364")


def test_rule_constants_match_prereg():
    assert (rot.LOOKBACK_MONTHS, rot.TOP_K, rot.STATIC_BOOKS) == (6, 3, 12)


def test_selection_top3_positive_ties_to_lower_book_flat_when_none():
    d = _synthetic(n=900)
    books = build_books(d)
    first = rot.first_rebalance(d, books)
    i_score = int(d.index.get_indexer([first - pd.Timedelta(days=10)])[0])

    def trade(r_net):  # a closed shadow trade 10 days before the first rebalance with a given net R
        entry, stop = 100.0, 90.0
        return dict(side=1, entry_price=entry, stop_price=stop, exit_price=entry + r_net * (entry - stop),
                    entry_index=i_score - 5, exit_index=i_score)

    shadows = {b: [] for b in TAB_BOOKS}
    shadows[2], shadows[3], shadows[4], shadows[5] = [trade(1.0)], [trade(3.0)], [trade(1.0)], [trade(-2.0)]
    sched = rot.schedule(d, shadows, books, 0.0, len(d) - 1)
    assert sched[0]["month"] == first
    assert sched[0]["selected"] == [3, 2, 4]          # highest first; 2 and 4 tie -> lower book first
    flat = rot.schedule(d, {b: [trade(-1.0)] for b in TAB_BOOKS}, books, 0.0, len(d) - 1)
    assert flat[0]["selected"] == []


def test_first_rebalance_is_six_months_after_all_books_computable():
    d = _synthetic()
    books = build_books(d)
    firsts = [d.index[int(books[b]["valid"].argmax())] for b in TAB_BOOKS]
    first = rot.first_rebalance(d, books)
    assert first.day == 1 and first >= max(firsts) + pd.DateOffset(months=6)
    assert first - pd.DateOffset(months=1) < max(firsts) + pd.DateOffset(months=6)


def test_no_lookahead_future_prices_do_not_change_past_selections():
    d = _synthetic()
    books = build_books(d)
    items, sched = rot.rotation_items(d, 0.0005, 0.0004, 1, len(d) - 1, books)
    cut = sched[len(sched) // 2]["month"]
    d2 = d.copy()
    later = d2.index >= cut
    rng = np.random.default_rng(99)
    shock = np.exp(np.cumsum(rng.normal(0, 0.05, later.sum())))
    for k in ("open", "high", "low", "close"):
        d2.loc[later, k] = d2.loc[later, k].to_numpy() * shock
    d2["high"] = d2[["open", "high", "low", "close"]].max(axis=1)
    d2["low"] = d2[["open", "high", "low", "close"]].min(axis=1)
    _, sched2 = rot.rotation_items(d2, 0.0005, 0.0004, 1, len(d2) - 1, build_books(d2))
    before = [(m["month"], m["selected"]) for m in sched if m["month"] <= cut]
    before2 = [(m["month"], m["selected"]) for m in sched2 if m["month"] <= cut]
    assert before == before2 and len(before) >= 3


def test_rotation_trades_belong_to_selected_books_and_month():
    d = _synthetic()
    items, sched = rot.rotation_items(d, 0.0005, 0.0004, 1, len(d) - 1)
    months = [m["month"] for m in sched]
    sel = {m["month"]: set(m["selected"]) for m in sched}
    assert items
    for b, t, scale in items:
        e = d.index[t["entry_index"]]
        m = max(x for x in months if x <= e)
        assert b in sel[m] and scale == pytest.approx(1 / 3)


def _cells(sid):
    d = _synthetic()
    candles = [dict(time=ts.to_pydatetime(), open=r.open, high=r.high, low=r.low, close=r.close, volume=1.0)
               for ts, r in zip(d.index, d.itertuples())]
    return run_engine_cell(dict(strategy_id=sid, direction="BOTH", symbol="BTCUSDT", timeframe="1d",
                                candles=candles, index_start=900, risk_usd=50.0, taker_fee=0.0004,
                                maker_fee=0.0002, leverage=2.0, principal=10000.0))


@pytest.mark.parametrize("sid,scale", [("DAILY_TREND_ROTATION", 1 / 3), ("DAILY_TREND_STATIC12", 1 / 12)])
def test_engine_cells_scale_risk(sid, scale):
    assert ENGINE_STRATEGIES[sid]["family"] == "daily_trend"
    out = _cells(sid)
    trades = out.get("trades") or out.get("closed_trades") or []
    assert trades
    for t in trades:
        assert t["risk_scale"] == pytest.approx(scale)
        expect = 50.0 * scale * t["risk_frac"] / 0.005
        assert float(t["risk_usd"]) == pytest.approx(expect, rel=1e-9)
        assert t["entry_index"] >= 900
