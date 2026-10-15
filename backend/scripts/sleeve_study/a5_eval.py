"""Variant evaluation for the sleeve study (research screen, validation, account runs).

Reads out/signals/<phase>/*.json and out/regimes/*.json; candles through the read-only loader.
Every number goes through sim.simulate (next-bar-open fills, taker + slippage, stop-first).
"""
from __future__ import annotations

import bisect
import glob
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from functools import lru_cache

from common import (FINAL, OUT, RESEARCH, STUDY_START, STUDY_SYMBOLS, UTC, VALIDATION, coins_for_tf)
from sim import Bars, Config, Costs, simulate

FOLDS = [("F1", datetime(2023, 6, 1, tzinfo=UTC), datetime(2023, 12, 1, tzinfo=UTC)),
         ("F2", datetime(2023, 12, 1, tzinfo=UTC), datetime(2024, 6, 1, tzinfo=UTC)),
         ("F3", datetime(2024, 6, 1, tzinfo=UTC), datetime(2024, 11, 1, tzinfo=UTC)),
         ("F4", datetime(2024, 11, 1, tzinfo=UTC), datetime(2025, 4, 1, tzinfo=UTC))]

CONT_LONG = {"COMBO_01", "COMBO_02_LOCAL", "COMBO_02", "COMBO_02_V2_1_A:TREND_FOLLOWING", "SMALL_CAP_VOLUME_BOS",
             "OSE_A_corrected_baseline", "OSE_B_acceptance", "OSE_C_retest", "OSE_D_faster_confirmation", "CANDLE12_V2"}
RANGE_LONG = {"LARGE_CAP_SWEEP_CHOCH", "MID_CAP_FVG_DISCOUNT", "COMBO_02_V2_1_A:RANGE_MEAN_REVERSION",
              "COMBO_03_TRANSITION_B"}
CONT_SHORT = {"COMBO_01", "COMBO_02_LOCAL", "COMBO_02", "COMBO_02_V2_1_A:TREND_FOLLOWING",
              "SHORT_PULLBACK_REJECTION_RESEARCH", "OSE_A_corrected_baseline", "OSE_B_acceptance", "OSE_C_retest",
              "OSE_D_faster_confirmation", "CANDLE12_V2"}
RANGE_SHORT = {"COMBO_02_V2_1_A:RANGE_MEAN_REVERSION", "COMBO_03_TRANSITION_B"}
FAMILY_REGIME = {"LONG_CONT": "UPTREND", "LONG_RANGE": "RANGE", "SHORT_CONT": "DOWNTREND", "SHORT_RANGE": "RANGE"}
FAMILY_BTC = {"LONG_CONT": "BULLISH", "SHORT_CONT": "BEARISH", "LONG_RANGE": "NEUTRAL", "SHORT_RANGE": "NEUTRAL"}
SOURCE_FAMILY = {"COMBO_01": "COMBO_01|LOCAL", "COMBO_02_LOCAL": "COMBO_01|LOCAL"}  # counted once
EXITS = ("X_NAT", "X_15R", "X_3R")
RFS = ("RF1", "RF2")


def family_of(sid: str, side: str) -> str | None:
    if side == "LONG":
        return "LONG_CONT" if sid in CONT_LONG else "LONG_RANGE" if sid in RANGE_LONG else None
    return "SHORT_CONT" if sid in CONT_SHORT else "SHORT_RANGE" if sid in RANGE_SHORT else None


# ------------------------------------------------------------------ regimes
@lru_cache(maxsize=None)
def _regime_series(coin: str, tf: str):
    rows = json.load(open(OUT / "regimes" / f"{coin}_{tf}.json", encoding="utf-8"))
    closes = [int(datetime.fromisoformat(r["close"]).timestamp()) for r in rows]
    return closes, [r["bucket"] for r in rows], [r["regime"] for r in rows], [r["hv_side"] for r in rows]


@lru_cache(maxsize=None)
def _btc_days():
    rows = json.load(open(OUT / "regimes" / "BTC_daily_trend.json", encoding="utf-8"))
    return {r["day"]: r["trend"] for r in rows}


def regime_at(coin: str, setup_tf: str, signal_open_ts: int) -> tuple[str, str, str | None]:
    tf = "1d" if setup_tf == "1d" else "4h"
    closes, buckets, regs, hv = _regime_series(coin, tf)
    k = bisect.bisect_right(closes, signal_open_ts) - 1  # last bar whose close <= signal bar open
    if k < 0:
        return "NO_TRADE", "UNKNOWN", None
    return buckets[k], regs[k], hv[k]


def btc_at(signal_open_ts: int) -> str:
    day = datetime.fromtimestamp(signal_open_ts, UTC).date().isoformat()
    return _btc_days().get(day, "NEUTRAL")


# ------------------------------------------------------------------ signals
def load_signals(phase: str) -> list[dict]:
    out = []
    for f in glob.glob(str(OUT / "signals" / phase / "*.json")):
        d = json.load(open(f, encoding="utf-8"))
        for s in d["signals"]:
            fam = family_of(s["sid"], s["side"])
            if fam is None:
                continue
            so = int(datetime.fromisoformat(s["signal_open"]).timestamp())
            b, reg, hv = regime_at(s["coin"], s["tf"], so)
            s.update(family=fam, regime_bucket=b, regime_raw=reg, hv_side=hv, btc=btc_at(so),
                     rf1=(b == FAMILY_REGIME[fam]))
            s["rf2"] = s["rf1"] and s["btc"] == FAMILY_BTC[fam]
            s["regime_match"] = s["rf1"]
            out.append(s)
    return out


def target_fn(exit_pair: str, tp1: float | None):
    if exit_pair == "X_15R":
        return lambda f, st: f + 1.5 * (f - st)
    if exit_pair == "X_3R":
        return lambda f, st: f + 3.0 * (f - st)
    if tp1 is None:  # OSE native target is 2R from the fill
        return lambda f, st: f + 2.0 * (f - st)
    return lambda f, st, _t=tp1: _t


# ------------------------------------------------------------------ bars
_BARS: Bars | None = None


def get_bars(end: datetime) -> Bars:
    """1h exec bars for every coin (AVAX before its stored 1h start is aggregated from stored 15m),
    15m exec bars for the 15m-eligible coins. Loaded only up to `end`."""
    global _BARS
    if _BARS is not None and getattr(_BARS, "end", None) == end:
        return _BARS
    from _db import load_candles
    b = Bars()
    start = STUDY_START - timedelta(days=3)
    for c in STUDY_SYMBOLS:
        h = load_candles(c, "1h", start, end)
        if c == "AVAXUSDT":
            first_1h = h[0]["time"] if h else end
            q = load_candles(c, "15m", start, first_1h)
            agg: dict = {}
            for x in q:
                k = x["time"].replace(minute=0)
                a = agg.get(k)
                if a is None:
                    agg[k] = dict(time=k, open=x["open"], high=x["high"], low=x["low"], close=x["close"], n=1)
                else:
                    a["high"] = max(a["high"], x["high"]); a["low"] = min(a["low"], x["low"])
                    a["close"] = x["close"]; a["n"] += 1
            h = [v for k, v in sorted(agg.items()) if v["n"] == 4] + h
        b.put(c, "1h", h)
    for c in coins_for_tf("15m"):
        b.put(c, "15m", load_candles(c, "15m", start, end))
    b.end = end
    _BARS = b
    return b


# ------------------------------------------------------------------ metrics
def stats(trades: list[dict]) -> dict:
    n = len(trades)
    net = sum(t["net"] for t in trades)
    gains = sum(t["net"] for t in trades if t["net"] > 0)
    loss = -sum(t["net"] for t in trades if t["net"] < 0)
    return {"trades": n, "net": round(net, 2), "pf": (round(gains / loss, 3) if loss > 0 else (None if n == 0 else 99.0)),
            "win_rate": round(sum(1 for t in trades if t["net"] > 0) / n, 3) if n else None,
            "fees": round(sum(t["fees"] for t in trades), 2)}


def variant_signals(signals: list[dict], v: dict) -> list[dict]:
    out = []
    for s in signals:
        if (s["sid"], s["side"], s["tf"]) != (v["sid"], v["side"], v["tf"]):
            continue
        if not s[v["rf"].lower()]:
            continue
        out.append({**s, "target_fn": target_fn(v["exit"], s.get("tp1"))})
    return out


def enumerate_variants(signals: list[dict]) -> list[dict]:
    keys = sorted({(s["sid"], s["side"], s["tf"], s["family"]) for s in signals})
    vs = []
    for sid, side, tf, fam in keys:
        for rf in RFS:
            for ex in EXITS:
                vs.append({"sid": sid, "side": side, "tf": tf, "family": fam, "rf": rf, "exit": ex,
                           "vid": f"{sid}|{side}|{tf}|{rf}|{ex}"})
    return vs


def screen_config(costs: Costs | None = None) -> Config:
    return Config(account_risk_cap=None, withdraw=False, costs=costs or Costs())


def run_variant(v: dict, signals: list[dict], window: tuple[datetime, datetime], bars: Bars,
                cfg: Config | None = None) -> dict:
    sigs = variant_signals(signals, v)
    coins = sorted({s["coin"] for s in sigs}) or [STUDY_SYMBOLS[0]]
    res = simulate(sigs, bars, coins, window[0], window[1], cfg or screen_config())
    per_coin = defaultdict(list)
    for t in res["trades"]:
        per_coin[t["coin"]].append(t)
    return {"all": stats(res["trades"]), "per_coin": {c: stats(ts) for c, ts in per_coin.items()},
            "trades": res["trades"], "skips": len(res["skips"])}
