"""Study-2 signal rules (new research ids S2_*; no app code changed). Closed bars only: a signal on bar i uses data
through bar i's close; sim2 fills it at the next exec bar's open.

Families (entry; side LONG mirrored for SHORT):
  TPB   trend pullback: EMA50>EMA200, close>EMA200, some low of the last `m` bars <= EMA`anchor`, close > EMA`anchor`,
        close > previous high, bullish body.
  BRKA  breakout with acceptance: bar j closes above the prior-`n` high (first close above), bar j+1 closes above the
        same level -> signal at j+1.
  BRKR  breakout with retest: after a close above the prior-`n` high, within `w` bars a bar trades to level+0.25 ATR
        and closes above the level (cancelled by a close below level-0.5 ATR).
  FBR   failed breakout / rejection: high above the prior-`n` high, close back below it, close in the lower half of the
        bar -> SHORT (mirror: sweep of the prior-`n` low -> LONG). Stop = sweep extreme + `buf` ATR.
  RMR   range mean reversion: close below the lower Bollinger(20, `bbk`) band and RSI14 < `rsi` -> LONG.
  VEX   volatility-expansion: Bollinger(20,2) width rank (prior 120 bars) of the previous bar <= `pct`, close above the
        upper band -> LONG.
  VNR   NR7 breakout: previous bar has the smallest range of 7, close above its high -> LONG.
  TFD   trend following: close above the prior-`n` high -> LONG (exits are trailing/time).
  TFE   trend following: EMA`fast` crosses above EMA`slow` -> LONG.
  ENG_C01 / ENG_OSEA  existing engines (COMBO_01, OSE_A_corrected_baseline) from strategy_search's cache (through
        2026-10-08), native stop distance x `stop_k`.
Stops: ATR(14) multiples `stop_k` from the signal close unless the family defines a structural stop (FBR).
"""
from __future__ import annotations

import bisect
import json
import pickle
from functools import lru_cache

import numpy as np

from common2 import OUT, TF_SEC, candles, hourly, HERE

RDIR = OUT / "regimes"
FDIR = OUT / "feat"
ENG_RAW = HERE.parent / "out" / "raw"


# ------------------------------------------------------------------ indicators (copied from strategy_search/strategies.py)
def ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    a = 2.0 / (n + 1)
    out[n - 1] = x[:n].mean()
    for i in range(n, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def atr(h, l, c, n=14) -> np.ndarray:
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    out[n] = tr[1:n + 1].mean()
    for i in range(n + 1, len(c)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def rolling_prior(x: np.ndarray, n: int, fn) -> np.ndarray:
    """fn over x[i-n:i] (prior n bars, excluding bar i)."""
    from numpy.lib.stride_tricks import sliding_window_view
    out = np.full(len(x), np.nan)
    if len(x) <= n:
        return out
    out[n:] = fn(sliding_window_view(x, n)[:-1], axis=1)
    return out


def rsi(c: np.ndarray, n: int) -> np.ndarray:
    d = np.diff(c, prepend=c[0])
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    au, ad = up[1:n + 1].mean(), dn[1:n + 1].mean()
    for i in range(n + 1, len(c)):
        au = (au * (n - 1) + up[i]) / n
        ad = (ad * (n - 1) + dn[i]) / n
        out[i] = 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)
    return out


def bollinger(c: np.ndarray, n: int = 20, k: float = 2.0):
    from numpy.lib.stride_tricks import sliding_window_view
    mid = np.full(len(c), np.nan)
    sd = np.full(len(c), np.nan)
    if len(c) >= n:
        w = sliding_window_view(c, n)
        mid[n - 1:] = w.mean(axis=1)
        sd[n - 1:] = w.std(axis=1)
    return mid, sd


def prior_rank(x: np.ndarray, n: int) -> np.ndarray:
    """Percent of the prior n values below x[i] (no lookahead)."""
    out = np.full(len(x), np.nan)
    for i in range(n, len(x)):
        w = x[i - n:i]
        w = w[~np.isnan(w)]
        if len(w) >= n // 2 and not np.isnan(x[i]):
            out[i] = 100.0 * (w < x[i]).mean()
    return out


# ------------------------------------------------------------------ features
_F: dict = {}


def feat(coin: str, tf: str) -> dict:
    key = (coin, tf)
    if key in _F:
        return _F[key]
    FDIR.mkdir(parents=True, exist_ok=True)
    f = FDIR / f"{coin}_{tf}.pkl"
    if f.exists():
        _F[key] = pickle.loads(f.read_bytes())
        return _F[key]
    cs = hourly(coin) if tf == "1h" else candles(coin, tf)
    o = np.array([x["open"] for x in cs], float); h = np.array([x["high"] for x in cs], float)
    l = np.array([x["low"] for x in cs], float); c = np.array([x["close"] for x in cs], float)
    t_open = np.array([int(x["time"].timestamp()) for x in cs], np.int64)
    mid, sd = bollinger(c, 20)
    bw = (4 * sd) / mid
    d = {"o": o, "h": h, "l": l, "c": c, "t_close": t_open + TF_SEC[tf], "atr": atr(h, l, c, 14),
         "ema20": ema(c, 20), "ema50": ema(c, 50), "ema100": ema(c, 100), "ema200": ema(c, 200),
         "bb_mid": mid, "bb_sd": sd, "bw_rank": prior_rank(bw, 120), "rsi14": rsi(c, 14),
         "rng": h - l}
    f.write_bytes(pickle.dumps(d))
    _F[key] = d
    return d


def _hi(d, n):
    k = ("hi", n)
    if k not in d:
        d[k] = rolling_prior(d["h"], n, np.max)
    return d[k]


def _lo(d, n):
    k = ("lo", n)
    if k not in d:
        d[k] = rolling_prior(d["l"], n, np.min)
    return d[k]


def _ema(d, n):
    k = f"ema{n}"
    if k not in d:
        d[k] = ema(d["c"], n)
    return d[k]


# ------------------------------------------------------------------ raw rule signals
def raw_signals(fam: str, p: dict, coin: str, tf: str) -> list[tuple]:
    """(i, side, stop_price or None, target_price or None). stop None -> ATR stop applied later."""
    if fam.startswith("ENG_"):
        return []
    d = feat(coin, tf)
    o, h, l, c, a = d["o"], d["h"], d["l"], d["c"], d["atr"]
    n_ = len(c)
    out: list[tuple] = []
    if fam == "TPB":
        anc = _ema(d, int(p["anchor"]))
        e50, e200 = d["ema50"], d["ema200"]
        m = int(p.get("m", 3))
        lowm = rolling_prior(l, m - 1, np.min) if m > 1 else np.full(n_, np.inf)
        highm = rolling_prior(h, m - 1, np.max) if m > 1 else np.full(n_, -np.inf)
        for i in range(1, n_):
            if np.isnan(e200[i]) or np.isnan(anc[i]):
                continue
            if e50[i] > e200[i] and c[i] > e200[i] and min(l[i], lowm[i]) <= anc[i] < c[i] and c[i] > h[i - 1] \
                    and c[i] > o[i]:
                out.append((i, 1, None, None))
            elif e50[i] < e200[i] and c[i] < e200[i] and max(h[i], highm[i]) >= anc[i] > c[i] and c[i] < l[i - 1] \
                    and c[i] < o[i]:
                out.append((i, -1, None, None))
    elif fam == "BRKA":
        n = int(p["n"]); hi, lo = _hi(d, n), _lo(d, n)
        for i in range(2, n_):
            j = i - 1
            if np.isnan(hi[j]) or np.isnan(hi[j - 1]):
                continue
            if c[j] > hi[j] and c[j - 1] <= hi[j - 1] and c[i] > hi[j]:
                out.append((i, 1, None, None))
            if c[j] < lo[j] and c[j - 1] >= lo[j - 1] and c[i] < lo[j]:
                out.append((i, -1, None, None))
    elif fam == "BRKR":
        n, w = int(p["n"]), int(p.get("w", 8))
        hi, lo = _hi(d, n), _lo(d, n)
        pl = ps = None
        for i in range(n_):
            if np.isnan(a[i]) or np.isnan(hi[i]):
                continue
            if pl and i <= pl[1]:
                lev = pl[0]
                if c[i] < lev - 0.5 * a[i]:
                    pl = None
                elif l[i] <= lev + 0.25 * a[i] and c[i] > lev:
                    out.append((i, 1, None, None)); pl = None
            if ps and i <= ps[1]:
                lev = ps[0]
                if c[i] > lev + 0.5 * a[i]:
                    ps = None
                elif h[i] >= lev - 0.25 * a[i] and c[i] < lev:
                    out.append((i, -1, None, None)); ps = None
            if c[i] > hi[i]:
                pl = (hi[i], i + w)
            if c[i] < lo[i]:
                ps = (lo[i], i + w)
    elif fam == "FBR":
        n, buf = int(p["n"]), float(p.get("buf", 0.25))
        hi, lo = _hi(d, n), _lo(d, n)
        for i in range(n_):
            r = h[i] - l[i]
            if r <= 0 or np.isnan(hi[i]) or np.isnan(a[i]):
                continue
            mid = (hi[i] + lo[i]) / 2
            if h[i] > hi[i] > c[i] and (h[i] - c[i]) / r >= 0.5:
                out.append((i, -1, h[i] + buf * a[i], mid))
            elif l[i] < lo[i] < c[i] and (c[i] - l[i]) / r >= 0.5:
                out.append((i, 1, l[i] - buf * a[i], mid))
    elif fam == "RMR":
        k, th = float(p["bbk"]), float(p["rsi"])
        mid, sd, r = d["bb_mid"], d["bb_sd"], d["rsi14"]
        for i in range(n_):
            if np.isnan(sd[i]) or np.isnan(r[i]):
                continue
            if c[i] < mid[i] - k * sd[i] and r[i] < th:
                out.append((i, 1, None, mid[i]))
            elif c[i] > mid[i] + k * sd[i] and r[i] > 100 - th:
                out.append((i, -1, None, mid[i]))
    elif fam == "VEX":
        pct = float(p["pct"])
        mid, sd, rk = d["bb_mid"], d["bb_sd"], d["bw_rank"]
        for i in range(1, n_):
            if np.isnan(rk[i - 1]) or rk[i - 1] > pct or np.isnan(sd[i]):
                continue
            if c[i] > mid[i] + 2 * sd[i]:
                out.append((i, 1, None, None))
            elif c[i] < mid[i] - 2 * sd[i]:
                out.append((i, -1, None, None))
    elif fam == "VNR":
        nr = int(p.get("nr", 7))
        rmin = rolling_prior(d["rng"], nr - 1, np.min)
        for i in range(2, n_):
            j = i - 1
            if np.isnan(rmin[j]) or d["rng"][j] > rmin[j]:
                continue
            if c[i] > h[j]:
                out.append((i, 1, None, None))
            elif c[i] < l[j]:
                out.append((i, -1, None, None))
    elif fam == "TFD":
        n = int(p["n"]); hi, lo = _hi(d, n), _lo(d, n)
        for i in range(1, n_):
            if np.isnan(hi[i]) or np.isnan(hi[i - 1]):
                continue
            if c[i] > hi[i] and c[i - 1] <= hi[i - 1]:
                out.append((i, 1, None, None))
            elif c[i] < lo[i] and c[i - 1] >= lo[i - 1]:
                out.append((i, -1, None, None))
    elif fam == "TFE":
        ef, es = _ema(d, int(p["fast"])), _ema(d, int(p["slow"]))
        for i in range(1, n_):
            if np.isnan(es[i - 1]):
                continue
            if ef[i] > es[i] and ef[i - 1] <= es[i - 1]:
                out.append((i, 1, None, None))
            elif ef[i] < es[i] and ef[i - 1] >= es[i - 1]:
                out.append((i, -1, None, None))
    else:
        raise ValueError(fam)
    return out


@lru_cache(maxsize=4096)
def _raw_cached(fam: str, pkey: tuple, coin: str, tf: str) -> tuple:
    return tuple(raw_signals(fam, dict(pkey), coin, tf))


@lru_cache(maxsize=None)
def _eng(coin: str) -> dict:
    f = ENG_RAW / f"discovery_ENG_{coin}.pkl"
    return pickle.loads(f.read_bytes()) if f.exists() else {}


# ------------------------------------------------------------------ regimes (causal)
def label_before(closes: list[int], labels: list[str], t: int) -> str:
    """Label of the last bar that closed strictly before t."""
    k = bisect.bisect_left(closes, t) - 1
    return labels[k] if k >= 0 else "NO_TRADE"


def bucket(reg: str, hv: str, mapping: str) -> str:
    if mapping == "B":  # sleeve-study baseline map (HVT always counted as UPTREND)
        if reg in ("BULL_TREND", "HIGH_VOLATILITY_TREND"):
            return "UPTREND"
        if reg == "BEAR_TREND":
            return "DOWNTREND"
    else:
        if reg == "BULL_TREND" or (reg == "HIGH_VOLATILITY_TREND" and hv == "BULL"):
            return "UPTREND"
        if reg == "BEAR_TREND" or (reg == "HIGH_VOLATILITY_TREND" and hv == "BEAR"):
            return "DOWNTREND"
    if reg in ("RANGE", "HIGH_VOLATILITY_RANGE", "LOW_VOLATILITY_COMPRESSION"):
        return "RANGE"
    return "NO_TRADE"


@lru_cache(maxsize=None)
def regime_series(coin: str, tf: str, mapping: str):
    rows = json.load(open(RDIR / f"{coin}_{tf}.json", encoding="utf-8"))
    return [r[0] for r in rows], [bucket(r[1], r[2], mapping) for r in rows]


@lru_cache(maxsize=None)
def btc_series():
    rows = json.load(open(RDIR / "BTC_daily_trend.json", encoding="utf-8"))
    return [r[0] for r in rows], [r[1] for r in rows]


def regime_at(coin: str, tf: str, signal_close: int, mapping: str) -> str:
    closes, labels = regime_series(coin, "1d" if tf == "1d" else "4h", mapping)
    return label_before(closes, labels, signal_close)


def btc_at(signal_close: int) -> str:
    closes, labels = btc_series()
    k = bisect.bisect_left(closes, signal_close) - 1
    return labels[k] if k >= 0 else "NEUTRAL"


def regime_ok(mode: str, side: int, b: str) -> bool:
    if b == "NO_TRADE":
        return False
    if mode == "ANY":
        return True
    if mode == "RANGE":
        return b == "RANGE"
    if mode == "TREND":
        return b == ("UPTREND" if side > 0 else "DOWNTREND")
    if mode == "NOTCOUNTER":  # trend side or range, never against the trend
        return b in ("RANGE", "UPTREND" if side > 0 else "DOWNTREND")
    raise ValueError(mode)


@lru_cache(maxsize=None)
def _daily_closes():
    from common2 import UNIVERSE
    out = {}
    for c in UNIVERSE:
        cs = candles(c, "1d")
        out[c] = ([int(x["time"].timestamp()) + 86400 for x in cs], [x["close"] for x in cs])
    return out


@lru_cache(maxsize=200000)
def rs_rank(coin: str, day_close: int, n: int) -> float | None:
    """Percentile rank (0..1) of coin's n-day return among universe coins, using daily bars closed at day_close."""
    dc = _daily_closes()
    rets = {}
    for c, (cl, px) in dc.items():
        k = bisect.bisect_right(cl, day_close) - 1
        if k >= n and cl[k] == day_close:
            rets[c] = px[k] / px[k - n] - 1
    if coin not in rets or len(rets) < 5:
        return None
    x = rets[coin]
    return sum(1 for v in rets.values() if v < x) / (len(rets) - 1)


def rs_ok(p: dict, coin: str, side: int, tc: int) -> bool:
    """Optional relative-strength filter (keys rs_n, rs_q in p): long needs rank >= rs_q, short <= 1 - rs_q.
    Uses the last daily close strictly before the signal close."""
    if "rs_n" not in p:
        return True
    day_close = (tc - 1) // 86400 * 86400
    r = rs_rank(coin, day_close, int(p["rs_n"]))
    if r is None:
        return False
    return r >= p["rs_q"] if side > 0 else r <= 1 - p["rs_q"]


# ------------------------------------------------------------------ variant -> sim2 signals
def build(v: dict, coins: list[str], t_from: int, t_to: int, mult: dict | None = None,
          sid: str | None = None) -> list[dict]:
    """v: fam, tf, side (LONG/SHORT), p (entry params), regime, map, btc, stop_k, exit (dict).
    mult: {name: factor} scales numeric params for neighbour tests (entry params and exit/stop numbers)."""
    mult = mult or {}
    p = {k: (type(x)(round(x * mult[k])) if isinstance(x, int) else x * mult[k]) if k in mult else x
         for k, x in v["p"].items()}
    ex = {k: (x * mult[k] if (k in mult and isinstance(x, (int, float)) and not isinstance(x, bool)) else x)
          for k, x in v["exit"].items()}
    sk = v["stop_k"] * mult.get("stop_k", 1.0)
    want = 1 if v["side"] == "LONG" else -1
    tf = v["tf"]
    sid = sid or v["vid"]
    out = []
    for coin in coins:
        if v["fam"].startswith("ENG_"):
            rows = [(r["close_ts"], r["side"], r["ref"] - r["side"] * sk * r["stop_dist"], None, r["atr"], r["ref"])
                    for r in _eng(coin).get((v["fam"], 0, tf), []) if r["side"] == want]
        else:
            d = feat(coin, tf)
            rows = []
            for i, side, stop, tgt in _raw_cached(v["fam"], tuple(sorted(p.items())), coin, tf):
                if side != want or np.isnan(d["atr"][i]) or d["atr"][i] <= 0:
                    continue
                a_ = float(d["atr"][i])
                ref = float(d["c"][i])
                if stop is None:
                    stop = ref - side * sk * a_
                rows.append((int(d["t_close"][i]), side, float(stop), tgt, a_, ref))
        for tc, side, stop, tgt, a_, ref in rows:
            if not (t_from <= tc < t_to):
                continue
            b = regime_at(coin, tf, tc, v.get("map", "C"))
            if not regime_ok(v["regime"], side, b):
                continue
            if not rs_ok(p, coin, side, tc):
                continue
            if v.get("btc"):
                bt = btc_at(tc)
                if (side > 0 and bt == "BEARISH") or (side < 0 and bt == "BULLISH"):
                    continue
            s = {"sid": sid, "coin": coin, "tf": tf, "side": "LONG" if side > 0 else "SHORT", "signal_close": tc,
                 "stop": stop, "atr": a_, "regime": b,
                 "regime_match": b == ("UPTREND" if side > 0 else "DOWNTREND"), "priority": v.get("priority", 50)}
            kind = ex["kind"]
            if kind == "R":
                s["target_r"] = ex["r"]
            elif kind == "MID":
                if tgt is None or side * (tgt - ref) <= 0:
                    continue
                s["target"] = float(tgt)
            elif kind == "TRAIL":
                s["trail_k"] = ex["trail"]
            elif kind == "PT":
                s["partial_r"], s["partial_frac"], s["be_after_partial"] = ex["pr"], ex.get("pf", 0.5), True
                s["trail_k"] = ex["trail"]
                if ex.get("r"):
                    s["target_r"] = ex["r"]
            else:
                raise ValueError(kind)
            if ex.get("hold"):
                s["max_hold_s"] = int(round(ex["hold"])) * TF_SEC[tf]
            if v.get("pyramid"):
                s["pyramid"] = True
            out.append(s)
    return out
