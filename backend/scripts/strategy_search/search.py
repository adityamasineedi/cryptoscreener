"""Strategy search runner: raw-signal prep, variant grid, serial shards, metrics, CSV log.

  python -B search.py prep discovery            # raw signals + regime/BTC tags per coin (cached)
  python -B search.py grid --shard i/n           # stage-1 grid at 2% risk, appends out/variants_shard<i>.csv
All fills/accounting go through sleeve_study/sim.py.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import pickle
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from functools import lru_cache

from ss_common import (FOLDS, OUT, TF_SEC, UTC, WARMUP_DAYS, candles, universe)
import strategies as S
from sim import Bars, Config, Costs, max_drawdown, simulate

TFS = ["15m", "1h", "4h", "1d"]
FALL_WIN = (datetime(2025, 9, 1, tzinfo=UTC), datetime(2026, 2, 14, tzinfo=UTC))
FALL_FOLD = "F3"
EXITS_TREND = {"R2": {"r": 2.0}, "R3": {"r": 3.0}, "TRAIL2": {"trail": 2.0}, "TRAIL3": {"trail": 3.0}}
EXITS_REV = {"R1": {"r": 1.0}, "R2": {"r": 2.0}, "R1T": {"r": 1.0, "hold_bars": 12}}
RAW = OUT / "raw"


# ------------------------------------------------------------------ regimes
@lru_cache(maxsize=None)
def _reg(coin: str, tf: str):
    rows = json.load(open(OUT / "regimes" / f"{coin}_{tf}.json", encoding="utf-8"))
    return [r["close"] for r in rows], [r["bucket"] for r in rows]


@lru_cache(maxsize=None)
def _btc():
    return {r["day"]: r["trend"] for r in json.load(open(OUT / "regimes" / "BTC_daily_trend.json", encoding="utf-8"))}


def regime_at(coin: str, tf: str, open_ts: int) -> str:
    closes, b = _reg(coin, "1d" if tf == "1d" else "4h")
    k = bisect.bisect_right(closes, open_ts) - 1
    return b[k] if k >= 0 else "NO_TRADE"


def btc_at(open_ts: int) -> str:
    return _btc().get(datetime.fromtimestamp(open_ts, UTC).date().isoformat(), "NEUTRAL")


# ------------------------------------------------------------------ prep
def prep(name: str, shard: str = "0/1") -> None:
    coins, (start, end) = universe(name)
    i, n = (int(x) for x in shard.split("/"))
    coins = coins[i::n]
    RAW.mkdir(parents=True, exist_ok=True)
    for coin in coins:
        f = RAW / f"{name}_{coin}.pkl"
        if f.exists():
            continue
        out = {}
        for tf in TFS:
            cs = candles(coin, tf, start - timedelta(days=WARMUP_DAYS[tf]), end)
            if not cs:
                continue
            for fam, params in S.FAMILY_PARAMS.items():
                for p in params:
                    rows = []
                    for s in S.generate(fam, p, cs):
                        close_ts = s["open_ts"] + TF_SEC[tf]
                        if not (start.timestamp() <= close_ts < end.timestamp()):
                            continue
                        s.update(close_ts=close_ts, bucket=regime_at(coin, tf, s["open_ts"]), btc=btc_at(s["open_ts"]))
                        rows.append(s)
                    out[(fam, p, tf)] = rows
            print(coin, tf, len(cs), flush=True)
        f.write_bytes(pickle.dumps(out))


@lru_cache(maxsize=None)
def raw(name: str, coin: str) -> dict:
    d = pickle.loads((RAW / f"{name}_{coin}.pkl").read_bytes())
    eng = RAW / f"{name}_ENG_{coin}.pkl"
    if eng.exists():
        d.update(pickle.loads(eng.read_bytes()))
    return d


# ------------------------------------------------------------------ bars
_BARS: dict = {}


def bars_for(name: str) -> Bars:
    if name in _BARS:
        return _BARS[name]
    coins, (start, end) = universe(name)
    b = Bars()
    s0 = start - timedelta(days=2)
    for c in coins:
        h = candles(c, "1h", s0, end)
        first_1h = h[0]["time"] if h else end
        if first_1h > s0 + timedelta(days=3):  # aggregate stored 15m into 1h before the stored 1h starts
            q = candles(c, "15m", s0, end)
            agg: dict = {}
            for x in q:
                if x["time"] >= first_1h:
                    break
                k = x["time"].replace(minute=0)
                a = agg.setdefault(k, dict(time=k, open=x["open"], high=x["high"], low=x["low"], close=x["close"], n=0))
                a["high"] = max(a["high"], x["high"]); a["low"] = min(a["low"], x["low"]); a["close"] = x["close"]
                a["n"] += 1
            h = [v for _, v in sorted(agg.items()) if v["n"] == 4] + h
        b.put(c, "1h", h)
        b.put(c, "15m", candles(c, "15m", s0, end))
    _BARS[name] = b
    return b


# ------------------------------------------------------------------ variants
def variant_grid() -> list[dict]:
    vs = []
    for fam, params in S.FAMILY_PARAMS.items():
        trend = fam in S.TREND_FAMILIES
        exits = EXITS_TREND if trend else EXITS_REV
        sides = ["LONG", "SHORT", "BOTH"] if trend else ["LONG", "SHORT"]
        rfs = ["TREND", "ANY"] if trend else ["RANGE", "ANY"]
        for p in params:
            for tf in TFS:
                for side in sides:
                    for rf in rfs:
                        for btc in (0, 1):
                            for sk in (1.5, 2.5):
                                for ex in exits:
                                    vs.append({"fam": fam, "p": p, "tf": tf, "side": side, "rf": rf, "btc": btc,
                                               "stop_k": sk, "exit": ex, **exits[ex]})
    for fam in ("ENG_C01", "ENG_OSEA", "ENG_OSEB", "ENG_OSED"):
        for tf in (("1h", "4h", "1d") if fam == "ENG_C01" else ("1h", "4h")):
            for side in ("LONG", "SHORT", "BOTH"):
                for rf in ("TREND", "ANY"):
                    for btc in (0, 1):
                        for ex in EXITS_TREND:
                            vs.append({"fam": fam, "p": 0, "tf": tf, "side": side, "rf": rf, "btc": btc,
                                       "stop_k": 1.0, "exit": ex, **EXITS_TREND[ex]})
    for v in vs:
        v["vid"] = f"{v['fam']}|{v['p']}|{v['tf']}|{v['side']}|{v['rf']}|btc{v['btc']}|s{v['stop_k']}|{v['exit']}"
    return vs


def _rf_ok(rf: str, side: int, bucket: str) -> bool:
    if bucket == "NO_TRADE":
        return False
    if rf == "ANY":
        return True
    if rf == "RANGE":
        return bucket == "RANGE"
    return bucket == ("UPTREND" if side > 0 else "DOWNTREND")


def build_signals(v: dict, name: str, mult: dict | None = None) -> list[dict]:
    """mult scales numeric exit parameters for the +/-20% gate: keys stop_k, r, trail."""
    mult = mult or {}
    coins, _ = universe(name)
    sk = v["stop_k"] * mult.get("stop_k", 1.0)
    r = v.get("r")
    r = r * mult.get("r", 1.0) if r else None
    tr = v.get("trail")
    tr = tr * mult.get("trail", 1.0) if tr else None
    hold = v.get("hold_bars")
    want = {"LONG": (1,), "SHORT": (-1,), "BOTH": (1, -1)}[v["side"]]
    out = []
    for coin in coins:
        for s in raw(name, coin).get((v["fam"], v["p"], v["tf"]), []):
            if s["side"] not in want or not _rf_ok(v["rf"], s["side"], s["bucket"]):
                continue
            if v["btc"] and ((s["side"] > 0 and s["btc"] == "BEARISH") or (s["side"] < 0 and s["btc"] == "BULLISH")):
                continue
            if "stop_dist" in s:  # engine source: its own structural stop distance, scaled by stop_k (1.0 = native)
                stop = s["ref"] - s["side"] * sk * s["stop_dist"]
            else:
                stop = s["ref"] - s["side"] * sk * s["atr"]
            sig = {"sid": v["vid"], "coin": coin, "tf": v["tf"], "side": "LONG" if s["side"] > 0 else "SHORT",
                   "signal_close": s["close_ts"], "stop": stop, "atr": s["atr"], "regime_bucket": s["bucket"],
                   "regime_match": True}
            if r:
                sig["target_fn"] = (lambda f, st, _r=r: f + _r * (f - st))
            else:
                sig["target"] = None
            if tr:
                sig["trail_atr"] = tr
            if hold:
                sig["max_hold_s"] = hold * TF_SEC[v["tf"]]
            out.append(sig)
    return out


# ------------------------------------------------------------------ metrics
def metrics(res: dict, name: str, window, risk: float) -> dict:
    coins, _ = universe(name)
    n = len(coins)
    days = (window[1] - window[0]).total_seconds() / 86400
    tr = res["trades"]
    net = sum(t["net"] for t in tr)
    wins = [t["net"] for t in tr if t["net"] > 0]
    losses = [t["net"] for t in tr if t["net"] <= 0]
    gl = -sum(losses)
    streak = best_streak = 0
    for t in sorted(tr, key=lambda x: x["exit_t"]):
        streak = streak + 1 if t["net"] <= 0 else 0
        best_streak = max(best_streak, streak)
    per_coin = defaultdict(float)
    for t in tr:
        per_coin[t["coin"]] += t["net"]
    months = {k: m["net"] for k, m in res["months"].items()}
    folds = {}
    for fn, a, b in FOLDS:
        folds[fn] = sum(t["net"] for t in tr if a.timestamp() <= t["entry_t"] < b.timestamp())
    fall_win = sum(t["net"] for t in tr if FALL_WIN[0].timestamp() <= t["entry_t"] < FALL_WIN[1].timestamp())
    pos_total = sum(v for v in per_coin.values() if v > 0)
    mdd = max_drawdown(res["curve"])
    base = 1000.0 * n
    scale = 10.0 / n
    by_reg = defaultdict(list)
    for t in tr:
        by_reg[t.get("regime") or "?"].append(t)
    return {
        "trades": len(tr), "net": round(net, 2), "pf": round(sum(wins) / gl, 3) if gl > 0 else None,
        "win_rate": round(len(wins) / len(tr), 3) if tr else None,
        "avg_win": round(statistics.mean(wins), 2) if wins else None,
        "avg_loss": round(statistics.mean(losses), 2) if losses else None,
        "exp_r": round(statistics.mean(t["r"] for t in tr), 3) if tr else None,
        "trades_per_30d": round(len(tr) / days * 30, 2),
        "usd_per_30d_10k": round(net * scale / days * 30, 2),
        "max_dd": round(mdd, 2), "max_dd_pct": round(100 * mdd / base, 2),
        "worst_month": round(min(months.values()), 2) if months else 0.0,
        "worst_month_10k": round(min(months.values()) * scale, 2) if months else 0.0,
        "longest_losing_streak": best_streak, "fees": round(sum(t["fees"] for t in tr), 2),
        "folds": {k: round(x, 2) for k, x in folds.items()}, "folds_pos": sum(1 for x in folds.values() if x > 0),
        "fall_fold": round(folds[FALL_FOLD], 2), "fall_win": round(fall_win, 2),
        "best_coin_share": round(max(per_coin.values()) / net, 3) if net > 0 and per_coin else None,
        "best_month_share": round(max(months.values()) / net, 3) if net > 0 and months else None,
        "months_ge_500_10k": sum(1 for x in months.values() if x * scale >= 500),
        "months_ge_1000_10k": sum(1 for x in months.values() if x * scale >= 1000),
        "n_months": len(months), "risk": risk,
        "by_regime": {k: {"trades": len(v), "net": round(sum(t["net"] for t in v), 2),
                          "win_rate": round(sum(1 for t in v if t["net"] > 0) / len(v), 3)} for k, v in by_reg.items()},
        "per_coin": {k: round(x, 2) for k, x in per_coin.items()},
        "months": {k: round(x, 2) for k, x in months.items()},
    }


def run(v: dict, name: str, risk: float, costs: Costs | None = None, mult: dict | None = None) -> dict:
    coins, window = universe(name)
    sigs = build_signals(v, name, mult)
    cfg = Config(risk_frac=risk, leverage=3.0, account_risk_cap=None, withdraw=True, costs=costs or Costs())
    res = simulate(sigs, bars_for(name), coins, window[0], window[1], cfg)
    return metrics(res, name, window, risk)


CSV_COLS = ["vid", "fam", "p", "tf", "side", "rf", "btc", "stop_k", "exit", "risk", "trades", "net", "pf", "win_rate",
            "exp_r", "max_dd", "max_dd_pct", "worst_month_10k", "usd_per_30d_10k", "folds_pos", "fall_fold", "fall_win",
            "best_coin_share", "best_month_share", "F1", "F2", "F3", "F4", "seconds"]


def grid(shard: str, risk: float = 0.02) -> None:
    i, n = (int(x) for x in shard.split("/"))
    vs = variant_grid()[i::n]
    f = OUT / f"variants_shard{i}.csv"
    done = set()
    if f.exists():
        done = {r["vid"] for r in csv.DictReader(open(f, encoding="utf-8"))}
    new = not f.exists()
    with open(f, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLS)
        if new:
            w.writeheader()
        for k, v in enumerate(vs):
            if v["vid"] in done:
                continue
            t0 = time.perf_counter()
            m = run(v, "discovery", risk)
            row = {**{c: v.get(c) for c in ("vid", "fam", "p", "tf", "side", "rf", "btc", "stop_k", "exit")},
                   **{c: m.get(c) for c in CSV_COLS if c in m}, **m["folds"], "seconds": round(time.perf_counter() - t0, 1)}
            w.writerow(row)
            fh.flush()
            if k % 50 == 0:
                print(f"[{shard}] {k}/{len(vs)} {v['vid']} {m['usd_per_30d_10k']} {row['seconds']}s", file=sys.stderr, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("name", nargs="?", default="discovery")
    ap.add_argument("--shard", default="0/1")
    a = ap.parse_args()
    if a.cmd == "prep":
        prep(a.name, a.shard)
    elif a.cmd == "grid":
        grid(a.shard)


if __name__ == "__main__":
    main()
