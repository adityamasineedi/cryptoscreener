"""Coin-agnostic signal rules for the strategy search (new research rules; no app code changed).

Every rule reads closed bars only: a signal on bar i uses data through bar i's close and is filled by
sleeve_study/sim.py at bar i+1's open. Stops are ATR multiples (numeric, so the +/-20% gate is defined).

Families
  DON   Donchian breakout: close beyond the prior N-bar high/low                  (breakout / continuation)
  PB    EMA pullback: EMA50 vs EMA200 trend, bar tags EMA20 and closes back beyond it with body (pullback)
  BRT   breakout then retest of the broken N-bar level within 8 bars              (breakout and retest)
  SWP   sweep of the prior N-bar low/high with a close back inside               (failure)
  BB    close outside the 20-bar Bollinger band (k sigma)                        (mean reversion)
  RSI2  RSI(2) extreme with the bar on the EMA200 trend side                       (pullback mean reversion)
  SQZ   Bollinger width in its lowest P-th percentile (120 bars), then close outside the band (vol expansion)
"""
from __future__ import annotations

import math

import numpy as np

FAMILY_PARAMS = {
    "DON": [20, 55],
    "PB": [20, 50],
    "BRT": [20, 55],
    "SWP": [20, 48],
    "BB": [2.0, 2.5],
    "RSI2": [5, 10],
    "SQZ": [10, 20],
}
TREND_FAMILIES = {"DON", "PB", "BRT", "SQZ"}
REVERSION_FAMILIES = {"SWP", "BB", "RSI2"}


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
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
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
    w = sliding_window_view(x, n)  # w[k] = x[k:k+n]
    out[n:] = fn(w[:-1], axis=1)
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
    return mid, mid + k * sd, mid - k * sd


def prior_percentile_rank(x: np.ndarray, n: int) -> np.ndarray:
    """Rank (0-100) of x[i] within x[i-n:i] (prior window, no lookahead)."""
    out = np.full(len(x), np.nan)
    for i in range(n, len(x)):
        w = x[i - n:i]
        w = w[~np.isnan(w)]
        if len(w) >= n // 2 and not math.isnan(x[i]):
            out[i] = 100.0 * (w < x[i]).mean()
    return out


def generate(family: str, param, cs: list[dict]) -> list[dict]:
    """Raw signals: bar index, side, reference close, ATR at the signal bar. Regime, stop and exit applied later."""
    o = np.array([x["open"] for x in cs], float)
    h = np.array([x["high"] for x in cs], float)
    l = np.array([x["low"] for x in cs], float)
    c = np.array([x["close"] for x in cs], float)
    a = atr(h, l, c, 14)
    sig: list[tuple[int, int]] = []  # (i, side)
    if family == "DON":
        hi, lo = rolling_prior(h, param, np.max), rolling_prior(l, param, np.min)
        for i in range(len(c)):
            if c[i] > hi[i]:
                sig.append((i, 1))
            elif c[i] < lo[i]:
                sig.append((i, -1))
    elif family == "PB":
        e_fast, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
        anchor = e_fast if param == 20 else e50
        for i in range(len(c)):
            if np.isnan(e200[i]):
                continue
            if e50[i] > e200[i] and l[i] <= anchor[i] < c[i] and c[i] > o[i]:
                sig.append((i, 1))
            elif e50[i] < e200[i] and h[i] >= anchor[i] > c[i] and c[i] < o[i]:
                sig.append((i, -1))
    elif family == "BRT":
        hi, lo = rolling_prior(h, param, np.max), rolling_prior(l, param, np.min)
        pend_l = pend_s = None  # (level, expiry index)
        for i in range(len(c)):
            if np.isnan(a[i]):
                continue
            if pend_l and i <= pend_l[1]:
                lev = pend_l[0]
                if c[i] < lev - 0.5 * a[i]:
                    pend_l = None
                elif l[i] <= lev + 0.25 * a[i] and c[i] > lev:
                    sig.append((i, 1)); pend_l = None
            if pend_s and i <= pend_s[1]:
                lev = pend_s[0]
                if c[i] > lev + 0.5 * a[i]:
                    pend_s = None
                elif h[i] >= lev - 0.25 * a[i] and c[i] < lev:
                    sig.append((i, -1)); pend_s = None
            if c[i] > hi[i]:
                pend_l = (hi[i], i + 8)
            if c[i] < lo[i]:
                pend_s = (lo[i], i + 8)
    elif family == "SWP":
        hi, lo = rolling_prior(h, param, np.max), rolling_prior(l, param, np.min)
        for i in range(len(c)):
            rng = h[i] - l[i]
            if rng <= 0:
                continue
            if l[i] < lo[i] < c[i] and (c[i] - l[i]) / rng >= 0.5:
                sig.append((i, 1))
            elif h[i] > hi[i] > c[i] and (h[i] - c[i]) / rng >= 0.5:
                sig.append((i, -1))
    elif family == "BB":
        mid, up, dn = bollinger(c, 20, param)
        for i in range(len(c)):
            if c[i] < dn[i]:
                sig.append((i, 1))
            elif c[i] > up[i]:
                sig.append((i, -1))
    elif family == "RSI2":
        r, e200 = rsi(c, 2), ema(c, 200)
        for i in range(len(c)):
            if np.isnan(r[i]) or np.isnan(e200[i]):
                continue
            if r[i] < param and c[i] > e200[i]:
                sig.append((i, 1))
            elif r[i] > 100 - param and c[i] < e200[i]:
                sig.append((i, -1))
    elif family == "SQZ":
        mid, up, dn = bollinger(c, 20, 2.0)
        bw = (up - dn) / mid
        pr = prior_percentile_rank(bw, 120)
        for i in range(1, len(c)):
            if np.isnan(pr[i - 1]) or pr[i - 1] > param:
                continue
            if c[i] > up[i]:
                sig.append((i, 1))
            elif c[i] < dn[i]:
                sig.append((i, -1))
    else:
        raise ValueError(family)
    out = []
    for i, side in sig:
        if np.isnan(a[i]) or a[i] <= 0:
            continue
        out.append({"i": i, "open_ts": int(cs[i]["time"].timestamp()), "side": side, "ref": float(c[i]),
                    "atr": float(a[i])})
    return out


def atr_at(cs: list[dict], ts_open: list[int]) -> dict[int, float]:
    """ATR(14) of the setup series at the given bar open times (for engine-sourced signals)."""
    h = np.array([x["high"] for x in cs], float)
    l = np.array([x["low"] for x in cs], float)
    c = np.array([x["close"] for x in cs], float)
    a = atr(h, l, c, 14)
    idx = {int(x["time"].timestamp()): k for k, x in enumerate(cs)}
    return {t: float(a[idx[t]]) for t in ts_open if t in idx and not np.isnan(a[idx[t]])}
