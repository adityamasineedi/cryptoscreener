"""Daily trend books on the Backtest tab's engine path (research only, no DB).

The engine row must carry exactly the trades books.simulate_path makes on the same bars,
and nothing at or after 2026-07-15 (the screen's sealed holdout) may be loaded.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from app.research import engine_backtest as eb
from app.research.backtest_job import BacktestJobService
from app.research.backtest_ui_config import BacktestConfigError
from app.research.daily_trend.books import BOOK_NAMES, RISK, TAB_BOOKS, build_books, simulate_path
from app.research.engine_backtest import (
    DAILY_TREND_HOLDOUT_CODE,
    ENGINE_STRATEGIES,
    DailyTrendHoldoutError,
    daily_trend_dates,
    engine_request_error,
    run_engine_cell,
)

DAILY_IDS = [sid for sid, s in ENGINE_STRATEGIES.items()
             if s["family"] == "daily_trend" and isinstance(s["book"], int)]
I_START = 400


def _synthetic(seed: int = 0, n: int = 1500, start: str = "2021-06-01") -> pd.DataFrame:
    # Same generator as tests/test_daily_trend_parity.py.
    r = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="D", tz="UTC")
    c = 30000 * np.exp(np.cumsum(r.normal(0.0004, 0.035, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(r.normal(0, 0.004, n))
    h = np.maximum(o, c) * np.exp(np.abs(r.normal(0, 0.015, n)))
    l = np.minimum(o, c) * np.exp(-np.abs(r.normal(0, 0.015, n)))
    return pd.DataFrame(dict(open=o, high=h, low=l, close=c), index=idx)


def _candles(d: pd.DataFrame) -> list[dict]:
    return [
        {
            "time": ts.to_pydatetime(),
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
            "volume": 1.0,
        }
        for ts, r in zip(d.index, d.itertuples())
    ]


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return _synthetic()


@pytest.fixture(scope="module")
def candles(frame) -> list[dict]:
    return _candles(frame)


@pytest.fixture(scope="module")
def books(frame) -> dict:
    return build_books(frame)


def _cell(sid: str, direction: str, candles: list[dict], symbol: str = "BTCUSDT", **extra) -> dict:
    return run_engine_cell(
        {
            "strategy_id": sid,
            "direction": direction,
            "symbol": symbol,
            "timeframe": "1d",
            "candles": candles,
            "index_start": I_START,
            "risk_usd": 20.0,
            "taker_fee": 0.0004,
            "maker_fee": 0.0002,
            "leverage": 2.0,
            "principal": 1000.0,
            **extra,
        }
    )


def _iso(ts: pd.Timestamp) -> str:
    return ts.to_pydatetime().isoformat()


# ---------------------------------------------------------------- registry


def test_registry_has_the_twelve_tab_books():
    assert len(DAILY_IDS) == 12
    assert tuple(ENGINE_STRATEGIES[s]["book"] for s in DAILY_IDS) == TAB_BOOKS
    warm = {s: ENGINE_STRATEGIES[s]["warmup_bars"] for s in DAILY_IDS}
    assert warm == {
        "DAILY_TREND_01_SMA200": 400,
        "DAILY_TREND_02_EMA200": 1000,
        "DAILY_TREND_03_SMA50": 400,
        "DAILY_TREND_04_MA_SLOPE": 400,
        "DAILY_TREND_05_GOLDEN_CROSS": 400,
        "DAILY_TREND_06_WEINSTEIN": 400,
        "DAILY_TREND_09_DONCHIAN20_WEINSTEIN": 400,
        "DAILY_TREND_11_ADX_DMI": 400,
        "DAILY_TREND_12_ICHIMOKU": 400,
        "DAILY_TREND_14_ROC90": 400,
        "DAILY_TREND_15_ROC30": 400,
        "DAILY_TREND_19_COMBINED": 400,
    }
    for sid in DAILY_IDS:
        spec = ENGINE_STRATEGIES[sid]
        assert spec["timeframes"] == ("1d",)
        assert spec["directions"] == ("LONG", "SHORT", "BOTH")
        assert spec["name"].endswith(BOOK_NAMES[spec["book"]])
        assert f"book {spec['book']} " in spec["execution_model"]
        assert "NO funding" in spec["execution_model"]
        assert engine_request_error(sid, "BOTH", ["1d"]) is None
        assert "1d" in engine_request_error(sid, "LONG", ["1h"])


# ---------------------------------------------------------------- trade path parity


@pytest.mark.parametrize("direction", ["LONG", "SHORT", "BOTH"])
@pytest.mark.parametrize("sid", DAILY_IDS)
def test_engine_trades_equal_simulate_path(sid, direction, frame, candles, books):
    book = ENGINE_STRATEGIES[sid]["book"]
    want = simulate_path(frame, book, I_START, len(frame) - 1, 0.0005, books)
    keep = {"LONG": (1,), "SHORT": (-1,), "BOTH": (1, -1)}[direction]
    want = [w for w in want if w["side"] in keep]
    out = _cell(sid, direction, candles)
    got = sorted(out["trades"], key=lambda t: t["entry_index"])
    assert len(got) == len(want)
    assert out["open_trades_at_end"] == 0
    for g, w in zip(got, want):
        assert g["direction"] == ("LONG" if w["side"] == 1 else "SHORT")
        # Executed fills are simulate_path's fills, bit for bit.
        assert g["entry_price"] == w["entry_price"]
        assert g["exit_price"] == w["exit_price"]
        assert g["stop_price"] == w["stop_price"]
        assert g["slippage_rate"] == 0.0
        assert g["notional_entry_usd"] / g["qty"] == pytest.approx(w["entry_price"], rel=1e-12)
        assert g["notional_exit_usd"] / g["qty"] == pytest.approx(w["exit_price"], rel=1e-12)
        assert (g["signal_index"], g["entry_index"], g["exit_index"]) == (
            w["signal_index"], w["entry_index"], w["exit_index"]
        )
        assert g["signal_time"] == _iso(frame.index[w["signal_index"]])
        assert g["entry_time"] == g["fill_time"] == _iso(frame.index[w["entry_index"]])
        assert g["exit_time"] == _iso(frame.index[w["exit_index"]])
        assert g["exit_reason"] == w["exit_reason"]
        assert g["holding_bars"] == w["exit_index"] - w["entry_index"] + 1
        assert g["outcome"] == {
            "stop": "SL", "stop_gap": "SL", "signal": "SIGNAL_EXIT", "window_end": "WINDOW_END"
        }[w["exit_reason"]]
        # Fixed-$ sizing on the screen's stop distance; book 19 halves risk in high vol.
        assert g["risk_frac"] == w["risk_frac"]
        risk = 20.0 * w["risk_frac"] / RISK
        assert g["requested_risk_usd"] == pytest.approx(risk)
        if not g["capped_by_leverage"]:
            assert g["qty"] == pytest.approx(risk / abs(w["entry_price"] - w["stop_price"]), rel=1e-12)
            assert g["risk_usd"] == pytest.approx(risk, rel=1e-9)
        # Taker both sides on the fills.
        assert g["fee_entry_usd"] == pytest.approx(-0.0004 * g["qty"] * w["entry_price"], abs=1e-8)
        assert g["fee_exit_usd"] == pytest.approx(-0.0004 * g["qty"] * w["exit_price"], abs=1e-8)
        if w["exit_reason"] == "signal":
            assert g["signal_exit_time"] == g["exit_time"]
        if w["exit_reason"] == "window_end":
            assert g["exit_index"] == len(frame) - 1


def test_book_19_trades_some_at_reduced_risk(frame, candles):
    out = _cell("DAILY_TREND_19_COMBINED", "BOTH", candles)
    assert {t["risk_frac"] for t in out["trades"]} == {0.005, 0.0025}
    half = [t for t in out["trades"] if t["risk_frac"] == 0.0025]
    assert all(t["requested_risk_usd"] == pytest.approx(10.0) for t in half)


def test_slippage_is_per_symbol(frame, candles, books):
    want = simulate_path(frame, 1, I_START, len(frame) - 1, 0.0015, books)
    got = sorted(_cell("DAILY_TREND_01_SMA200", "BOTH", candles, symbol="SOLUSDT")["trades"], key=lambda t: t["entry_index"])
    assert [g["entry_price"] for g in got] == [w["entry_price"] for w in want]
    assert [g["exit_price"] for g in got] == [w["exit_price"] for w in want]
    assert all(g["slippage_in_fills"] == 0.0015 for g in got)


def test_both_is_long_plus_short(candles):
    both = _cell("DAILY_TREND_14_ROC90", "BOTH", candles)["trades"]
    longs = _cell("DAILY_TREND_14_ROC90", "LONG", candles)["trades"]
    shorts = _cell("DAILY_TREND_14_ROC90", "SHORT", candles)["trades"]
    key = lambda t: (t["entry_index"], t["direction"])  # noqa: E731
    assert sorted(map(key, both)) == sorted(map(key, longs + shorts))
    assert longs and shorts


def test_still_forming_last_bar_is_dropped(frame, candles):
    last = candles[-1]["time"]
    out = _cell("DAILY_TREND_03_SMA50", "BOTH", candles, now_utc=last + timedelta(hours=12))
    d = frame.iloc[:-1]
    want = simulate_path(d, 3, I_START, len(d) - 1, 0.0005)
    got = sorted(out["trades"], key=lambda t: t["entry_index"])
    assert [(g["entry_index"], g["exit_index"], g["exit_price"]) for g in got] == [
        (w["entry_index"], w["exit_index"], w["exit_price"]) for w in want
    ]
    assert max(g["exit_index"] for g in got) == len(d) - 1


# ---------------------------------------------------------------- holdout guard


def test_runner_raises_on_a_holdout_candle():
    d = _synthetic(n=600, start="2024-12-01")  # runs to 2026-07-23
    assert d.index[-1] >= pd.Timestamp("2026-07-15", tz="UTC")
    with pytest.raises(RuntimeError, match="holdout"):
        _cell("DAILY_TREND_01_SMA200", "LONG", _candles(d))


def test_runner_accepts_bars_up_to_2026_07_14():
    d = _synthetic(n=600, start="2024-11-22")
    assert d.index[-1] == pd.Timestamp("2026-07-14", tz="UTC")
    out = _cell("DAILY_TREND_01_SMA200", "LONG", _candles(d))
    assert out["status"] in ("OK", "SUCCESS_EMPTY")


def test_daily_trend_dates():
    assert daily_trend_dates(None, "2026-07-14", 500) == (None, "2026-07-14")
    assert daily_trend_dates("2024-01-01", "2026-07-14", 500) == ("2024-01-01", "2026-07-14")
    assert daily_trend_dates("2024-01-01", None, 500) == ("2024-01-01", "2026-07-14")
    # Tail: the `limit` days ending 2026-07-14 (end_exclusive 2026-07-15).
    assert daily_trend_dates(None, None, 10) == ("2026-07-05", "2026-07-14")
    assert daily_trend_dates(None, "2026-07-14T23:59:00Z", 10) == (None, "2026-07-14")
    for end in ("2026-07-15", "2026-10-18", "2026-07-15T00:00:00Z"):
        with pytest.raises(DailyTrendHoldoutError, match="sealed"):
            daily_trend_dates(None, end, 500)
    with pytest.raises(DailyTrendHoldoutError):
        daily_trend_dates("2026-07-15", None, 500)


def _start(svc: BacktestJobService, **kw):
    return svc.start(
        symbols=["BTCUSDT"],
        timeframes=["1d"],
        direction=kw.pop("direction", "LONG"),
        combination_id="DAILY_TREND_01_SMA200",
        limit=kw.pop("limit", 1200),
        **kw,
    )


def test_job_rejects_end_date_2026_07_15():
    svc = BacktestJobService()
    with pytest.raises(BacktestConfigError) as err:
        asyncio.run(_start(svc, start_date="2025-01-01", end_date="2026-07-15"))
    assert err.value.code == DAILY_TREND_HOLDOUT_CODE
    assert "end date must be <= 2026-07-14" in str(err.value)
    assert svc.status()["status"] == "idle"


def test_job_rejects_start_inside_holdout():
    svc = BacktestJobService()
    with pytest.raises(BacktestConfigError) as err:
        asyncio.run(_start(svc, start_date="2026-08-01"))
    assert err.value.code == DAILY_TREND_HOLDOUT_CODE


def _fake_db():
    db = MagicMock()
    db.enabled = True
    db.engine = object()
    return db


async def _in_process(payload, **_kw):
    return run_engine_cell(payload)


def _run_job(svc: BacktestJobService, frame: pd.DataFrame, **kw):
    """Start a daily trend job with mocked Postgres loaders; returns (job status, range mock, tail mock)."""
    range_mock = AsyncMock(return_value=_candles(frame))
    tail_mock = AsyncMock(side_effect=AssertionError("tail loader used for daily_trend"))

    async def go():
        await _start(svc, **kw)
        await svc._task
        return svc.status()

    with (
        patch("app.services.database.db_manager", _fake_db()),
        patch("app.research.postgres_ohlcv.load_ohlcv_series_range", new=range_mock),
        patch("app.research.postgres_ohlcv.load_ohlcv_series_tail", new=tail_mock),
        patch.object(eb, "run_engine_cell_isolated", new=_in_process),
    ):
        status = asyncio.run(go())
    return status, range_mock, tail_mock


def _short_frame() -> pd.DataFrame:
    return _synthetic(n=700, start="2024-08-14")  # ends 2026-07-13


def test_job_accepts_end_date_2026_07_14_and_loads_a_bounded_range():
    svc = BacktestJobService()
    status, range_mock, tail_mock = _run_job(
        svc, _short_frame(), start_date="2025-06-01", end_date="2026-07-14"
    )
    assert status["status"] == "done", status.get("error")
    tail_mock.assert_not_called()
    range_mock.assert_awaited_once()
    kw = range_mock.await_args.kwargs
    assert kw["end_exclusive"] == datetime(2026, 7, 15, tzinfo=timezone.utc)
    assert kw["start"] == datetime(2025, 6, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("direction", ["LONG", "BOTH"])
def test_tail_request_never_uses_the_tail_loader(direction):
    svc = BacktestJobService()
    status, range_mock, tail_mock = _run_job(svc, _short_frame(), limit=300, direction=direction)
    assert status["status"] == "done", status.get("error")
    tail_mock.assert_not_called()
    range_mock.assert_awaited_once()
    kw = range_mock.await_args.kwargs
    assert kw["end_exclusive"] == datetime(2026, 7, 15, tzinfo=timezone.utc)
    assert kw["start"] == datetime(2026, 7, 15, tzinfo=timezone.utc) - timedelta(days=300)
    assert status["start_date"] == "2025-09-18" and status["end_date"] == "2026-07-14"
    (row,) = status["rows"]
    assert row["direction"] == direction
    assert row["engine_family"] == "daily_trend"


def test_engine_matrix_without_dates_never_uses_the_tail_loader():
    tail_mock = AsyncMock(side_effect=AssertionError("tail loader used for daily_trend"))
    range_mock = AsyncMock(return_value=_candles(_short_frame()))
    with (
        patch("app.services.database.db_manager", _fake_db()),
        patch("app.research.postgres_ohlcv.load_ohlcv_series_range", new=range_mock),
        patch("app.research.postgres_ohlcv.load_ohlcv_series_tail", new=tail_mock),
        patch.object(eb, "run_engine_cell_isolated", new=_in_process),
    ):
        out = asyncio.run(
            eb.engine_strategy_matrix(
                strategy_id="DAILY_TREND_06_WEINSTEIN", symbol="BTCUSDT", timeframe="1d",
                direction="SHORT", limit=200, risk_usd=20.0, principal_usd=1000.0,
                start_date=None, end_date=None, taker_fee=0.0004, maker_fee=0.0002, leverage=2.0,
            )
        )
        with pytest.raises(DailyTrendHoldoutError):
            asyncio.run(
                eb.engine_strategy_matrix(
                    strategy_id="DAILY_TREND_06_WEINSTEIN", symbol="BTCUSDT", timeframe="1d",
                    direction="SHORT", limit=200, risk_usd=20.0, principal_usd=1000.0,
                    start_date=None, end_date="2026-07-20", taker_fee=0.0004, maker_fee=0.0002,
                    leverage=2.0,
                )
            )
    assert out["status"] == "OK"
    tail_mock.assert_not_called()
    assert range_mock.await_args.kwargs["end_exclusive"] == datetime(2026, 7, 15, tzinfo=timezone.utc)


def test_sync_get_route_rejects_daily_trend():
    from app.api.routes import research_long_strategy_backtest

    with patch("app.research.service.get_bos_research_service", side_effect=AssertionError("no load")):
        out = asyncio.run(
            research_long_strategy_backtest(
                symbols="BTCUSDT", timeframes="1d", direction="LONG",
                combination_id="DAILY_TREND_01_SMA200", limit=1200, risk_usd=20.0,
                principal_usd=1000.0, leverage=2.0, risk_mode=None, research_risk_override=False,
                taker_fee_pct=0.04, maker_fee_pct=0.02, include_trades=True,
                start_date=None, end_date=None,
            )
        )
    assert out["status"] == "ERROR"
    assert out["error_code"] == DAILY_TREND_HOLDOUT_CODE
    assert out["rows"] == []


def test_loss_analysis_candle_load_stops_before_the_holdout():
    from types import SimpleNamespace

    from app.research.trade_loss_analysis import loss_analysis_for_current_job

    d = _short_frame()
    out = _cell("DAILY_TREND_15_ROC30", "BOTH", _candles(d))
    row = {
        "symbol": "BTCUSDT", "timeframe": "1d", "combination_id": "DAILY_TREND_15_ROC30",
        "engine_family": "daily_trend", "direction": "BOTH", "trades": out["trades"],
        "period_start": out["trades"][0]["entry_time"], "period_end": _iso(d.index[-1]),
    }
    job = SimpleNamespace(status="done", rows=[row], include_trades=True, job_id="x",
                          combination_id="DAILY_TREND_15_ROC30")
    loader = AsyncMock(return_value=(_candles(d), 0, {}))
    res = asyncio.run(
        loss_analysis_for_current_job("BTCUSDT", "1d", service=SimpleNamespace(_job=job), candle_loader=loader)
    )
    assert loader.await_args.kwargs["end_date"] == "2026-07-14"
    assert res["replay_check"]["computable"] is True


# ---------------------------------------------------------------- loss analysis + VectorBT


def test_loss_analysis_rewalk_reproduces_every_trade(frame, candles):
    from app.research.trade_loss_analysis import analyze_row, exit_model_for

    assert exit_model_for("DAILY_TREND_04_MA_SLOPE").computable
    for sid in ("DAILY_TREND_04_MA_SLOPE", "DAILY_TREND_19_COMBINED", "DAILY_TREND_11_ADX_DMI"):
        trades = _cell(sid, "BOTH", candles)["trades"]
        row = {"symbol": "BTCUSDT", "timeframe": "1d", "combination_id": sid,
               "engine_family": "daily_trend", "direction": "BOTH", "trades": trades}
        res = analyze_row(row, candles, strategy_id=sid)
        replay = res["replay_check"]
        assert replay["computable"] is True
        assert replay["replayed_exactly"] == replay["trades"] == len(trades), sid


def test_vectorbt_replays_daily_trend_rows(candles):
    pytest.importorskip("vectorbt")
    from app.research.vectorbt_orders_engine import engine_replay_orders
    from app.research.vectorbt_replay import check_parity

    for sid in ("DAILY_TREND_01_SMA200", "DAILY_TREND_19_COMBINED"):
        trades = _cell(sid, "BOTH", candles)["trades"]
        orders = engine_replay_orders(sid, trades)
        assert len(orders) == len(trades)
        res = check_parity(orders)
        assert res.passed, (sid, res.to_dict(max_details=3))


def test_runner_refuses_missing_daily_bars(candles):
    gapped = candles[:600] + candles[601:]
    with pytest.raises(ValueError, match="missing daily bars"):
        _cell("DAILY_TREND_01_SMA200", "BOTH", gapped)


def test_chart_candles_for_1d_never_reach_the_holdout():
    from app.api import routes

    captured = {}

    async def fake_range(sym, tf, *, start, end_exclusive, warmup_bars):
        captured.update(start=start, end_exclusive=end_exclusive)
        return []

    engine = MagicMock()
    with patch("app.research.postgres_ohlcv.load_ohlcv_series_range", side_effect=fake_range), \
            patch("app.services.database.db_manager.engine", engine):
        asyncio.run(routes.research_ohlcv_candles(symbol="BTCUSDT", timeframe="1d", start="2026-07-10",
                                                  end="2026-07-14", pad_bars=64))
    assert captured["end_exclusive"] <= datetime(2026, 7, 15, tzinfo=timezone.utc)
    captured.clear()
    with patch("app.research.postgres_ohlcv.load_ohlcv_series_range", side_effect=fake_range), \
            patch("app.services.database.db_manager.engine", engine):
        asyncio.run(routes.research_ohlcv_candles(symbol="BTCUSDT", timeframe="1h", start="2026-07-10",
                                                  end="2026-07-14", pad_bars=64))
    assert captured["end_exclusive"] > datetime(2026, 7, 15, tzinfo=timezone.utc)  # intraday unchanged
