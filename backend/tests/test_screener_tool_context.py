"""Owner D188 part 1: cost as R, HTF arrows on closed bars only, BTC context, research badge (golden examples)."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone

import pytest

from app.screener_tool import badges, btc_context, costs, htf
from app.screener_tool.context import build_context

NOW = datetime(2026, 10, 22, 13, 30, tzinfo=timezone.utc)


# --- cost as R ---------------------------------------------------------------------------------
def test_cost_as_r_golden_btc_one_percent_stop():
    c = costs.cost_as_r(100.0, 99.0, "BTCUSDT", side="LONG")
    assert c["status"] == "OK" and c["slippage_bps"] == 2.0 and not c["thin_coin"]
    assert c["round_trip_rate"] == pytest.approx(0.0014)  # 2 x (0.05% + 0.02%)
    assert c["stop_distance_pct"] == pytest.approx(1.0)
    assert c["cost_r"] == pytest.approx(0.14)
    assert c["cost_ok"] is True and c["cost_too_high"] is False


def test_cost_as_r_too_high_above_quarter_r():
    c = costs.cost_as_r(100.0, 99.5, "SOLUSDT", side="LONG")  # 0.5% stop -> 0.28 R
    assert c["cost_r"] == pytest.approx(0.28)
    assert c["cost_too_high"] is True and c["cost_ok"] is False
    edge = costs.cost_as_r(100.0, 100.56, "SOLUSDT", side="SHORT")  # 0.56% stop -> exactly 0.25 R: still OK
    assert edge["cost_r"] == pytest.approx(0.25) and edge["cost_ok"] is True


@pytest.mark.parametrize("coin", ["AAVE", "APT", "ARB", "DOT", "ETC", "OP", "FIL"])
def test_thin_coins_use_5bp_slippage(coin):
    c = costs.cost_as_r(1.0, 0.992, f"{coin}USDT", side="LONG")
    assert c["thin_coin"] and c["slippage_bps"] == 5.0
    assert c["round_trip_rate"] == pytest.approx(0.002)  # 2 x (0.05% + 0.05%)
    assert c["cost_r"] == pytest.approx(0.25) and c["cost_ok"] is True
    assert costs.cost_as_r(1.0, 0.993, f"{coin}USDT")["cost_too_high"] is True  # 0.7% stop -> 0.286 R


def test_cost_as_r_unusable_levels():
    assert costs.cost_as_r(None, 99, "BTCUSDT")["status"] == "NO_LEVELS"
    assert costs.cost_as_r(100, 100, "BTCUSDT")["status"] == "INVALID_STOP"
    wrong = costs.cost_as_r(100, 101, "BTCUSDT", side="LONG")
    assert wrong["status"] == "STOP_WRONG_SIDE" and wrong["cost_r"] is None and wrong["cost_ok"] is None


# --- HTF arrows ----------------------------------------------------------------------------------
def _bars(closes, tf, last_open):
    step = htf.timeframe_delta(tf)
    n = len(closes)
    return [{"open_time": last_open - step * (n - 1 - i), "close": c, "is_closed": True} for i, c in enumerate(closes)]


def test_ema_golden_and_same_recurrence_as_the_frozen_helper():
    closes = list(range(1, 61))
    assert htf.ema(closes, 50) == pytest.approx(35.5)  # a linear series lags (period - 1) / 2
    assert htf.ema(closes, 20) == pytest.approx(50.5)
    from app.engines.mtf.indicators import ema as frozen_ema  # read-only import of the frozen file

    rnd = random.Random(7)
    series = [100 + rnd.uniform(-5, 5) for _ in range(250)]
    for period in (20, 50):
        assert htf.ema(series, period) == pytest.approx(frozen_ema(series, period), rel=1e-12)


def test_htf_arrows_golden_rising_series():
    # 60 closed 4h bars, the last opened 8h before NOW (closed 4h before NOW).
    st = htf.htf_state(_bars(list(range(1, 61)), "4h", NOW - timedelta(hours=8)), "4h", NOW)
    assert st["status"] == "OK" and st["bars"] == 60
    assert (st["close"], st["ema20"], st["ema50"]) == (60.0, pytest.approx(50.5), pytest.approx(35.5))
    assert st["close_vs_ema50"] == "UP" and st["ema20_vs_ema50"] == "UP" and st["trend"] == "UP"


def test_htf_never_uses_the_open_bar():
    closed = _bars(list(range(1, 61)), "4h", NOW - timedelta(hours=4))  # last bar closes exactly at NOW
    forming = {"open_time": NOW - timedelta(hours=2), "close": 0.01, "is_closed": True}  # mislabelled, still open
    flagged_open = {"open_time": NOW - timedelta(hours=1), "close": 0.01, "is_closed": False}
    st = htf.htf_state([*closed, forming, flagged_open], "4h", NOW)
    assert st["bars"] == 60 and st["close"] == 60.0 and st["close_vs_ema50"] == "UP"
    assert st["last_closed_bar"] == (NOW - timedelta(hours=4)).isoformat()
    # Two hours later the crash bar has closed: now it counts, and the close is below EMA50.
    later = htf.htf_state([*closed, forming], "4h", NOW + timedelta(hours=2))
    assert later["bars"] == 61 and later["close"] == 0.01 and later["close_vs_ema50"] == "DOWN"
    assert later["ema20_vs_ema50"] == "UP" and later["trend"] == "MIXED"


def test_htf_daily_bar_counts_only_after_24h():
    day = NOW.replace(hour=0, minute=0)
    bars = _bars(list(range(100, 40, -1)), "1d", day)  # last bar opened today 00:00, still forming at 13:30
    st = htf.htf_state(bars, "1d", NOW)
    assert st["bars"] == 59 and st["status"] == "OK" and st["close"] == 42.0
    assert st["trend"] == "DOWN"
    assert htf.htf_state(bars[:30], "1d", NOW)["status"] == "SHORT_HISTORY"
    assert htf.htf_state([], "1d", NOW)["status"] == "NO_DATA"


def test_htf_agrees():
    up = {"status": "OK", "trend": "UP"}
    down = {"status": "OK", "trend": "DOWN"}
    assert htf.htf_agrees("LONG", {"4h": up, "1d": up}) is True
    assert htf.htf_agrees("LONG", {"4h": up, "1d": down}) is False
    assert htf.htf_agrees("SHORT", {"4h": down, "1d": down}) is True
    assert htf.htf_agrees("LONG", {"4h": up, "1d": {"status": "SHORT_HISTORY"}}) is None
    assert htf.htf_agrees(None, {"4h": up, "1d": up}) is None


# --- BTC context -----------------------------------------------------------------------------------
def _settlements(rates, last):
    return [{"time": last - timedelta(hours=8) * (len(rates) - 1 - i), "rate": r} for i, r in enumerate(rates)]


def test_funding_state_d175_golden():
    last = NOW - timedelta(hours=5, minutes=30)  # 08:00 UTC settlement
    rows = _settlements([0.0001] * 6 + [0.00013, 0.00012, 0.00011], last)
    st = btc_context.funding_state(rows, NOW)
    assert st["status"] == "OK" and st["settlements"] == 9
    assert st["mean_72h"] == pytest.approx((0.0006 + 0.00036) / 9)
    assert st["state"] == "ABOVE_BASE" and st["share_above_base"] == pytest.approx(3 / 9)
    below = btc_context.funding_state(_settlements([0.00005] * 9, last), NOW)
    assert below["state"] == "BELOW_BASE"
    neg = btc_context.funding_state(_settlements([-0.0001] * 9, last), NOW)
    assert neg["state"] == "NEGATIVE"
    at = btc_context.funding_state(_settlements([0.0001] * 9, last), NOW)
    assert at["state"] == "AT_BASE"


def test_funding_no_data_when_stale_short_or_missing():
    assert btc_context.funding_state([], NOW)["text"] == "funding: no data"
    stale = btc_context.funding_state(_settlements([0.0001] * 9, NOW - timedelta(hours=9)), NOW)
    assert stale["status"] == "NO_DATA" and stale["text"] == "funding: no data" and "older than 8 h" in stale["reason"]
    short = btc_context.funding_state(_settlements([0.0001] * 5, NOW - timedelta(hours=1)), NOW)
    assert short["status"] == "NO_DATA" and short["settlements"] == 5


def test_btc_funding_reads_the_store_never_the_exchange():
    calls = []

    async def loader(symbol, start, end):
        calls.append((symbol, start, end))
        return {"status": "never_fetched", "rows": []}

    st = asyncio.run(btc_context.btc_funding(NOW, loader=loader))
    assert calls and calls[0][0] == "BTCUSDT" and calls[0][2] == NOW
    assert st["text"] == "funding: no data" and st["reason"] == "never_fetched"


def _daily(n, end_open, start=100.0, step=1.0):
    return [
        {"open_time": end_open - timedelta(days=n - 1 - i), "open": start + i * step, "high": start + i * step + 2,
         "low": start + i * step - 2, "close": start + i * step + 0.5, "volume": 1.0, "is_closed": True}
        for i in range(n)
    ]


def test_btc_regime_is_the_live_daily_switch_on_closed_bars():
    from app.research.btc_daily_regime import daily_trend
    from app.signals.config import SignalConfig

    rnd = random.Random(3)
    today = NOW.replace(hour=0, minute=0)
    bars = _daily(120, today)
    for b in bars:  # some swings so the trend is not degenerate
        wiggle = rnd.uniform(-6, 6)
        b["high"] += abs(wiggle)
        b["low"] -= abs(wiggle)
    got = btc_context.btc_regime(NOW, candles=bars)
    want = daily_trend(bars[:-1], SignalConfig())  # today's bar is still open at 13:30
    assert got["status"] == "OK" and got["trend"] == want["trend"]
    assert got["bars"] == 119 and got["as_of_bar"] == want["as_of_bar"]
    assert btc_context.btc_regime(NOW, candles=[])["status"] == "NO_DATA"


# --- badge -------------------------------------------------------------------------------------------
def test_badge_per_setup_type_is_never_a_signal():
    rows = [
        {"tier": "READY", "bos": "BULLISH_BOS", "setup_trend": "BULLISH", "pullback": "ACTIVE"},
        {"tier": "NEAR", "bos": "BULLISH_BOS", "setup_trend": "BULLISH"},
        {"tier": "FORMING", "setup_trend": "BULLISH"},
        {"tier": "WAITING", "bos": "BULLISH_BOS", "setup_trend": "BULLISH"},
    ]
    got = [badges.badge_for(r) for r in rows]
    assert [g["setup_type"] for g in got] == ["TREND_BOS_PULLBACK", "TREND_BOS", "STRUCTURE_FORMING", "NO_SETUP"]
    for g in got:
        assert g["text"] == "Pattern untested / no edge found (FINAL_VERDICT)"
        blob = " ".join(str(v) for v in g.values()).lower()
        assert "not a buy or sell signal" in blob
        assert "buy signal" not in blob.replace("not a buy or sell signal", "")


# --- the whole context -------------------------------------------------------------------------------
def test_build_context_rows_btc_and_db_fallback_only_for_short_series():
    up4 = _bars(list(range(1, 61)), "4h", NOW - timedelta(hours=8))
    up1d = _bars(list(range(1, 61)), "1d", NOW.replace(hour=0, minute=0) - timedelta(days=1))
    memory = {("ETHUSDT", "4h"): up4, ("ETHUSDT", "1d"): up1d, ("SOLUSDT", "4h"): up4}
    asked = []

    async def fallback(symbols, tf, now):
        asked.append((tuple(symbols), tf))
        return {"SOLUSDT": up1d} if tf == "1d" else {}

    async def funding(now):
        return btc_context.funding_state([], now)

    rows = [
        {"symbol": "ETHUSDT", "direction": "LONG", "tier": "READY", "entry_price": 2500.0, "stop_price": 2450.0,
         "bos": "BULLISH_BOS", "setup_trend": "BULLISH", "pullback": "ACTIVE"},
        {"symbol": "SOLUSDT", "direction": "LONG", "tier": "NEAR", "entry_price": 100.0, "stop_price": 99.7},
        {"symbol": "bad sym!", "entry_price": 1, "stop_price": 0.5},
    ]
    out = asyncio.run(build_context(rows, now=NOW, memory=lambda s, tf: memory.get((s, tf), []),
                                    fallback=fallback, regime=lambda now: {"trend": "BULLISH"}, funding=funding))
    assert out["read_only"] is True and len(out["rows"]) == 2
    eth, sol = out["rows"]
    assert eth["cost"]["cost_r"] == pytest.approx(0.07) and eth["cost"]["cost_ok"] is True
    assert eth["htf_agrees"] is True and eth["htf"]["4h"]["source"] == "memory"
    assert sol["cost"]["cost_too_high"] is True  # 0.3% stop -> 0.467 R
    assert sol["htf"]["1d"]["source"] == "db" and sol["htf_agrees"] is True
    assert asked == [(("SOLUSDT",), "1d")]
    assert out["btc"]["regime"]["trend"] == "BULLISH" and out["btc"]["funding"]["text"] == "funding: no data"
    assert out["rules"]["badge"] == badges.BADGE_TEXT


def test_routes_are_mounted_and_context_works_without_a_database():
    from fastapi.testclient import TestClient

    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert {"/api/screener-tool/context", "/api/my-trades", "/api/my-trades/export.csv"} <= paths
    client = TestClient(app)  # no lifespan: nothing starts
    r = client.post("/api/screener-tool/context",
                    json={"rows": [{"symbol": "ETHUSDT", "direction": "LONG", "entry_price": 100, "stop_price": 99}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows"][0]["cost"]["cost_r"] == pytest.approx(0.14)
    assert body["rows"][0]["htf"]["4h"]["status"] in ("NO_DATA", "SHORT_HISTORY", "OK")
    assert body["btc"]["funding"]["text"] == "funding: no data"
    assert client.post("/api/screener-tool/context", json={"rows": "x"}).status_code == 422
