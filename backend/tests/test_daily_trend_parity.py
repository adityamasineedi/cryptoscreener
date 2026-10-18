"""The Backtest-tab daily trend module must reproduce the locked pre-registered screen trade for trade."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.research.daily_trend import books as dt

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "daily_direction_shortlist_prereg.py"
SCRIPT_SHA256 = "a90b046b187e0270ed3cbadec5ec5df913f5afac0147d33b4c35b8322a1ca1e8"


def _screen():
    spec = importlib.util.spec_from_file_location("daily_screen_prereg", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _synthetic(seed: int, n: int = 1500) -> pd.DataFrame:
    r = np.random.default_rng(seed)
    idx = pd.date_range("2021-06-01", periods=n, freq="D", tz="UTC")
    c = 30000 * np.exp(np.cumsum(r.normal(0.0004, 0.035, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(r.normal(0, 0.004, n))
    h = np.maximum(o, c) * np.exp(np.abs(r.normal(0, 0.015, n)))
    l = np.minimum(o, c) * np.exp(-np.abs(r.normal(0, 0.015, n)))
    return pd.DataFrame(dict(open=o, high=h, low=l, close=c, volume=1.0, quote_volume=1.0), index=idx)


def test_screen_script_is_the_locked_run2_version():
    assert hashlib.sha256(SCRIPT.read_bytes()).hexdigest() == SCRIPT_SHA256


def test_tab_books_exclude_book_13_and_fail_books():
    assert dt.TAB_BOOKS == (1, 2, 3, 4, 5, 6, 9, 11, 12, 14, 15, 19)


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("symbol", ["BTCUSDT", "SOLUSDT"])
def test_trade_path_matches_screen(seed, symbol):
    m = _screen()
    d = _synthetic(seed)
    sd = m.SymbolData(symbol, d, None, None)
    screen_books = m.build_books(d)
    tab_books = dt.build_books(d)
    windows = [(400, 700), (701, 1100), (1101, len(d) - 1)]
    compared = 0
    for b in dt.TAB_BOOKS:
        for k in ("state", "trig"):
            np.testing.assert_array_equal(screen_books[b][k], tab_books[b][k])
        for i0, i1 in windows:
            sig = dict(state=screen_books[b]["state"], trig=screen_books[b]["trig"], kind=screen_books[b]["kind"])
            risk = screen_books[b].get("risk", np.full(len(d), m.RISK))
            want, _, _, _ = m.simulate([sd], [sig], d.index[i0], d.index[i1], [risk])
            got = dt.simulate_path(d, b, i0, i1, m.SLIP[symbol], tab_books)
            assert len(got) == len(want), (b, i0)
            for g, w in zip(got, want):
                assert d.index[g["signal_index"]] == w["signal_time"]
                assert d.index[g["entry_index"]] == w["entry_time"]
                assert d.index[g["exit_index"]] == w["exit_time"]
                assert g["side"] == w["side"] and g["exit_reason"] == w["exit_reason"]
                assert g["entry_price"] == pytest.approx(w["entry_price"], rel=1e-12)
                assert g["exit_price"] == pytest.approx(w["exit_price"], rel=1e-12)
                assert g["stop_price"] == pytest.approx(w["stop_price"], rel=1e-12)
                assert g["risk_frac"] == w["risk"]
                compared += 1
    assert compared > 200
