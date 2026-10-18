"""4h trend book 16 on the Backtest tab: the pre-registered 4h/1h screen's trades and funding, its two coins,
LONG only, and the sealed holdout (nothing at or after 2026-07-15)."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from app.research import engine_backtest as eb
from app.research.backtest_job import BacktestJobService
from app.research.backtest_ui_config import BacktestConfigError
from app.research.daily_trend import books as dt
from app.research.daily_trend import funding as fu
from app.research.engine_backtest import (
    DAILY_TREND_HOLDOUT_CODE,
    ENGINE_STRATEGIES,
    FUNDING_PNL_LABEL_COMPLETE,
    FUNDING_PNL_LABEL_INCOMPLETE,
    daily_trend_dates,
    engine_request_error,
    run_engine_cell,
)

SID = "MTF_4H_16_BREAKOUT252"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCREEN = SCRIPTS / "daily_direction_shortlist_mtf_prereg.py"
SCREEN_SHA256 = "263ecc52d2cfb9d875d3de698c01a7b415b39334e933c97342de984f8db0eba4"
BUNDLE_SHA256 = "3ccc74cda4a8305a57ab15bde0d72a2087970c1b2cceb50443ec94fb074818e2"
BAR = pd.Timedelta(hours=4)
I_START = 1000


def _screen():
    spec = importlib.util.spec_from_file_location("mtf_screen_prereg", SCREEN)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _hourly(seed: int, n: int = 4 * 4000, start: str = "2021-06-01") -> pd.DataFrame:
    r = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="h", tz="UTC")
    c = 30000 * np.exp(np.cumsum(r.normal(0.0001, 0.008, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(r.normal(0, 0.001, n))
    h = np.maximum(o, c) * np.exp(np.abs(r.normal(0, 0.004, n)))
    l = np.minimum(o, c) * np.exp(-np.abs(r.normal(0, 0.004, n)))
    return pd.DataFrame(dict(open=o, high=h, low=l, close=c, volume=1.0), index=idx)


def _funding(hourly: pd.DataFrame, seed: int) -> pd.DataFrame:
    """8h settlements: two dropped records (MISSING), every 25th and the last 40 without a documented
    interval (UNRESOLVED, like the July 2026 fapi rows); the series stops 3 days before the bars."""
    r = np.random.default_rng(100 + seed)
    idx = pd.date_range(hourly.index[0], hourly.index[-1] - pd.Timedelta(days=3), freq="8h", tz="UTC")
    f = pd.DataFrame({"rate": r.normal(1e-4, 2e-4, len(idx)), "documented_interval_hours": 8.0}, index=idx)
    f = f.drop(f.index[[900, 1700]])
    f.iloc[-40:, f.columns.get_loc("documented_interval_hours")] = np.nan
    f.iloc[::25, f.columns.get_loc("documented_interval_hours")] = np.nan
    return f


def _candles(d: pd.DataFrame) -> list[dict]:
    return [{"time": ts.to_pydatetime(), "open": float(r.open), "high": float(r.high), "low": float(r.low),
             "close": float(r.close), "volume": 1.0} for ts, r in zip(d.index, d.itertuples())]


def _cell(candles: list[dict], symbol: str = "BTCUSDT", index_start: int = I_START, **extra) -> dict:
    return run_engine_cell({
        "strategy_id": SID, "direction": "LONG", "symbol": symbol, "timeframe": "4h", "candles": candles,
        "index_start": index_start, "risk_usd": 20.0, "taker_fee": 0.0004, "maker_fee": 0.0002,
        "leverage": 2.0, "principal": 1000.0, "now_utc": datetime(2030, 1, 1, tzinfo=timezone.utc), **extra})


# ---------------------------------------------------------------- pins and registry


def test_screen_runner_copy_is_the_executed_version():
    assert hashlib.sha256(SCREEN.read_bytes()).hexdigest() == SCREEN_SHA256


def test_funding_bundle_is_pinned_and_sealed():
    assert hashlib.sha256(fu.BUNDLE.read_bytes()).hexdigest() == BUNDLE_SHA256
    raw = pd.read_csv(fu.BUNDLE)
    assert set(raw["symbol"]) == {"BTCUSDT", "ETHUSDT"}
    ts = pd.to_datetime(raw["time"], utc=True)
    assert ts.max() < pd.Timestamp("2026-07-15", tz="UTC")
    for s in ("BTCUSDT", "ETHUSDT"):
        f = fu.bundled_funding(s)
        july = f.index >= pd.Timestamp("2026-06-30 16:00", tz="UTC")
        assert f.loc[~july, "documented_interval_hours"].notna().all()
        assert f.loc[f.index >= pd.Timestamp("2026-07-01", tz="UTC"), "documented_interval_hours"].isna().all()
    assert fu.bundled_funding("SOLUSDT") is None


def test_registry_4h_book():
    spec = ENGINE_STRATEGIES[SID]
    assert spec["family"] == "daily_trend" and spec["book"] == 16
    assert spec["directions"] == ("LONG",) and spec["timeframes"] == ("4h",)
    assert spec["symbols"] == ("BTCUSDT", "ETHUSDT") and spec["bar_hours"] == 4 and spec["funding"] is True
    assert eb.is_daily_trend(SID)
    assert engine_request_error(SID, "LONG", ["4h"], ["BTCUSDT", "ETHUSDT"]) is None
    assert "BTCUSDT, ETHUSDT only; got SOLUSDT" in engine_request_error(SID, "LONG", ["4h"], ["SOLUSDT"])
    assert "LONG only" in engine_request_error(SID, "SHORT", ["4h"])
    assert "4h" in engine_request_error(SID, "LONG", ["1h"])


# ---------------------------------------------------------------- parity with the screen ledger


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_trades_and_funding_match_the_screen_ledger(seed, monkeypatch):
    m = _screen()
    h = _hourly(seed)
    d, dropped = m.aggregate(h, "4h", 4)
    assert not dropped
    f = _funding(h, seed)
    monkeypatch.setattr(fu, "bundled_funding", lambda s: f)

    fs = m.FundingSeries(f, h)
    reasons, statuses = set(), set()
    want = _compare_with_screen(m, d, fs, reasons, statuses)
    assert len(want) > 3
    # Cut the bars inside a long trade so it closes at window_end (bound = that bar's close). The cut bar
    # closes off a settlement instant: one exactly at the close has no loaded bar to mark it here (see
    # test_settlement_at_the_last_close_is_unmarked), while the screen read the next 1h open.
    t = max(want, key=lambda w: pd.Timestamp(w["exit_time"]) - pd.Timestamp(w["entry_time"]))
    cut = int(d.index.get_loc(pd.Timestamp(t["exit_time"]))) - 2
    while d.index[cut].hour % 8:
        cut -= 1
    assert cut > int(d.index.get_loc(pd.Timestamp(t["entry_time"])))
    _compare_with_screen(m, d.iloc[: cut + 1], fs, reasons, statuses)
    assert {"signal", "window_end"} <= reasons
    assert "UNRESOLVED" in statuses and "COMPLETE" in statuses


def _compare_with_screen(m, d: pd.DataFrame, fs, reasons: set, statuses: set) -> list[dict]:
    screen_books = m.build_books(d, None, BAR)
    np.testing.assert_array_equal(screen_books["16"]["state"], dt.build_books(d)[16]["state"])
    np.testing.assert_array_equal(screen_books["16"]["trig"], dt.build_books(d)[16]["trig"])
    sig = dict(state=screen_books["16"]["state"], trig=screen_books["16"]["trig"], kind="event")
    want, _, skipped = m.simulate_ledger("BTCUSDT", d, BAR, sig, np.full(len(d), m.RISK), I_START, len(d) - 1, fs)
    assert skipped == 0
    got = _cell(_candles(d))["trades"]
    assert len(got) == len(want)
    for g, w in zip(got, want):
        assert g["direction"] == "LONG" and w["side"] == 1
        assert pd.Timestamp(g["signal_time"]) == pd.Timestamp(w["signal_time"])
        assert pd.Timestamp(g["entry_time"]) == pd.Timestamp(w["entry_time"])
        assert pd.Timestamp(g["exit_time"]) == pd.Timestamp(w["exit_time"])
        assert g["exit_reason"] == w["exit_reason"]
        assert g["entry_price"] == pytest.approx(w["entry_price"], rel=1e-12)
        assert g["exit_price"] == pytest.approx(w["exit_price"], rel=1e-12)
        assert g["stop_price"] == pytest.approx(w["stop_price"], rel=1e-12)
        # Same settlements and marks; only the size differs (fixed $ here, compounding 0.5% there).
        assert g["funding_usd"] / g["qty"] == pytest.approx(w["funding"] / w["qty"], rel=1e-9, abs=1e-12)
        assert g["funding_settlements"] == w["funding_settlements_included"]
        assert g["funding_coverage_status"] == w["funding_coverage_status"]
        assert g["funding_missing_records"] == w["funding_missing_records"]
        assert g["funding_unknown_schedule_checks"] == w["funding_unknown_schedule_checks"]
        assert g["funding_timing_status"] == w["funding_timing_status"]
        assert g["net_pnl_usd"] == pytest.approx(g["net_pnl_before_funding_usd"] + g["funding_usd"], rel=1e-12)
        assert g["r_net"] == pytest.approx(g["net_pnl_usd"] / g["risk_usd"], rel=1e-12)
        reasons.add(g["exit_reason"])
        statuses.add(g["funding_coverage_status"])
    return want


def test_entry_open_settlement_excluded_and_exit_open_included():
    idx = pd.date_range("2024-01-01", periods=12, freq="4h", tz="UTC")
    o = np.arange(100.0, 112.0)
    # Coverage needs the bracket: the first settlement after the exit (bar 8) must exist.
    f = pd.DataFrame({"rate": [0.001] * 5, "documented_interval_hours": 8.0},
                     index=pd.DatetimeIndex([idx[0], idx[2], idx[4], idx[6], idx[8]]))
    fs = fu.FundingSchedule(f, idx, o)
    # Long 2 units entered at the open of bar 2 (a settlement instant), exit at the open of bar 6.
    r = fs.trade(1, 2.0, int(idx[2].value), int(idx[6].value))
    assert r["funding_settlements"] == 2  # bars 4 and 6, not 2
    assert r["funding_usd"] == pytest.approx(-2.0 * 0.001 * (o[4] + o[6]))
    assert r["funding_coverage_status"] == "COMPLETE"
    # A settlement that is not on a bar open has no 1h mark here: not posted, PARTIAL.
    off = pd.DataFrame({"rate": [0.001] * 4, "documented_interval_hours": [8.0, 8.0, 10.0, 14.0]},
                       index=pd.DatetimeIndex([idx[0], idx[2], idx[4] + pd.Timedelta(hours=2), idx[8]]))
    r = fu.FundingSchedule(off, idx, o).trade(1, 1.0, int(idx[1].value), int(idx[6].value))
    assert r["funding_unmarked_settlements"] == 1 and r["funding_coverage_status"] == "PARTIAL"


def test_settlement_at_the_last_close_is_unmarked():
    # window_end posts through the last bar's close; a settlement at that instant needs the next bar's
    # open, which is not loaded: not charged, counted unmarked, PARTIAL (never guessed).
    idx = pd.date_range("2024-01-01", periods=6, freq="4h", tz="UTC")
    f = pd.DataFrame({"rate": [0.001] * 5, "documented_interval_hours": 8.0},
                     index=pd.DatetimeIndex([idx[0], idx[2], idx[4], idx[5] + pd.Timedelta(hours=4),
                                             idx[5] + pd.Timedelta(hours=12)]))
    r = fu.FundingSchedule(f, idx, np.arange(1.0, 7.0)).trade(1, 1.0, int(idx[1].value),
                                                                int((idx[5] + pd.Timedelta(hours=4)).value))
    assert r["funding_settlements"] == 2 and r["funding_unmarked_settlements"] == 1
    assert r["funding_coverage_status"] == "PARTIAL"


# ---------------------------------------------------------------- guards


def _frame_to(end: str, n: int = 1400) -> pd.DataFrame:
    h = _hourly(5, n=4 * n, start=str(pd.Timestamp(end) - pd.Timedelta(hours=4 * n)))
    return _screen().aggregate(h, "4h", 4)[0]


def test_runner_refuses_other_symbols_and_holdout_and_gaps(monkeypatch):
    d = _frame_to("2026-07-15")
    c = _candles(d)
    assert c[-1]["time"] == datetime(2026, 7, 14, 20, tzinfo=timezone.utc)
    with pytest.raises(Exception, match="BTCUSDT, ETHUSDT only"):
        _cell(c, symbol="SOLUSDT")
    sealed = c + [{**c[-1], "time": datetime(2026, 7, 15, tzinfo=timezone.utc)}]
    with pytest.raises(RuntimeError, match="sealed holdout"):
        _cell(sealed)
    with pytest.raises(ValueError, match="missing 4h bars"):
        _cell(c[:1200] + c[1201:])


def test_last_bar_window_end_funding_is_unresolved_with_the_real_bundle():
    # The last 4h bar closes at 2026-07-15 00:00: that settlement is sealed, so a trade held to the end is
    # UNRESOLVED (and July 2026 rows have no documented schedule anyway).
    d = _frame_to("2026-07-15")
    out = _cell(_candles(d), index_start=len(d) - 400)
    row = eb.build_engine_row(out, strategy_id=SID, direction="LONG", symbol="BTCUSDT", timeframe="4h",
                              risk_usd=20.0, principal=1000.0, leverage=2.0, bars_loaded=len(d), warmup_bars=0,
                              candle_source=None, period_start=None, period_end=None)
    assert "funding_total_usd" in row and row["pnl_label"] in (FUNDING_PNL_LABEL_COMPLETE, FUNDING_PNL_LABEL_INCOMPLETE)
    for t in out["trades"]:
        if t["exit_reason"] == "window_end":
            assert t["funding_coverage_status"] == "UNRESOLVED"
            assert row["funding_coverage_status"] == "UNRESOLVED"
            assert row["pnl_label"] == FUNDING_PNL_LABEL_INCOMPLETE


def test_dates_tail_counts_4h_bars():
    assert daily_trend_dates(None, None, 12, bar_hours=4) == ("2026-07-13", "2026-07-14")
    assert daily_trend_dates(None, None, 13, bar_hours=4) == ("2026-07-12", "2026-07-14")
    assert daily_trend_dates(None, None, 2190, bar_hours=4) == ("2025-07-15", "2026-07-14")
    with pytest.raises(eb.DailyTrendHoldoutError, match="4h trend book: holdout") as err:
        daily_trend_dates(None, "2026-07-15", 100, bar_hours=4)
    assert err.value.code == DAILY_TREND_HOLDOUT_CODE


def _start(svc: BacktestJobService, **kw):
    return svc.start(symbols=kw.pop("symbols", ["BTCUSDT"]), timeframes=["4h"], direction="LONG",
                     combination_id=SID, limit=kw.pop("limit", 1200), **kw)


def test_job_refuses_other_symbols_and_the_holdout():
    svc = BacktestJobService()
    with pytest.raises(BacktestConfigError) as err:
        asyncio.run(_start(svc, symbols=["BTCUSDT", "SOLUSDT"]))
    assert err.value.code == "engine_request_unsupported"
    with pytest.raises(BacktestConfigError) as err:
        asyncio.run(_start(svc, start_date="2025-01-01", end_date="2026-07-15"))
    assert err.value.code == DAILY_TREND_HOLDOUT_CODE
    assert svc.status()["status"] == "idle"


def test_job_tail_request_loads_a_bounded_4h_range():
    d = _frame_to("2026-07-15")
    range_mock = AsyncMock(return_value=_candles(d))
    tail_mock = AsyncMock(side_effect=AssertionError("tail loader used"))
    db = MagicMock()
    db.enabled, db.engine = True, object()

    async def in_process(payload, **_kw):
        return run_engine_cell(payload)

    async def go():
        svc = BacktestJobService()
        await _start(svc, limit=600)
        await svc._task
        return svc.status()

    with (patch("app.services.database.db_manager", db),
          patch("app.research.postgres_ohlcv.load_ohlcv_series_range", new=range_mock),
          patch("app.research.postgres_ohlcv.load_ohlcv_series_tail", new=tail_mock),
          patch.object(eb, "run_engine_cell_isolated", new=in_process)):
        status = asyncio.run(go())
    assert status["status"] == "done", status.get("error")
    tail_mock.assert_not_called()
    kw = range_mock.await_args.kwargs
    assert kw["end_exclusive"] == datetime(2026, 7, 15, tzinfo=timezone.utc)
    assert kw["start"] == datetime(2026, 7, 15, tzinfo=timezone.utc) - timedelta(days=100)
    (row,) = status["rows"]
    assert row["engine_family"] == "daily_trend" and row["timeframe"] == "4h"
    assert row["pnl_label"] in (FUNDING_PNL_LABEL_COMPLETE, FUNDING_PNL_LABEL_INCOMPLETE)


def test_chart_candles_with_holdout_flag_stop_before_2026_07_15():
    from app.api import routes

    captured = {}

    async def fake_range(sym, tf, *, start, end_exclusive, warmup_bars):
        captured.update(start=start, end_exclusive=end_exclusive)
        return []

    engine = MagicMock()
    for holdout, capped in ((True, True), (False, False)):
        captured.clear()
        with patch("app.research.postgres_ohlcv.load_ohlcv_series_range", side_effect=fake_range), \
                patch("app.services.database.db_manager.engine", engine):
            asyncio.run(routes.research_ohlcv_candles(symbol="BTCUSDT", timeframe="4h", start="2026-07-10",
                                                      end="2026-07-14", pad_bars=64, holdout=holdout))
        assert (captured["end_exclusive"] <= datetime(2026, 7, 15, tzinfo=timezone.utc)) is capped


def test_vectorbt_replays_the_before_funding_r():
    pytest.importorskip("vectorbt")
    from app.research.vectorbt_orders_engine import engine_replay_orders
    from app.research.vectorbt_replay import check_parity

    d = _frame_to("2026-07-15")
    trades = _cell(_candles(d), index_start=400)["trades"]
    assert trades and any(t["funding_usd"] != 0 for t in trades)
    orders = engine_replay_orders(SID, trades)
    res = check_parity(orders)
    assert res.passed, res.to_dict(max_details=3)
