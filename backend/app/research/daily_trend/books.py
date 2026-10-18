"""Daily trend books for the Backtest tab (research only; no paper or live path).

Indicators and book rules are copied verbatim from
backend/scripts/daily_direction_shortlist_prereg.py (run 2, sha256 a90b046b...), the locked
pre-registered screen. Do not edit the rules here: tests/test_daily_trend_parity.py proves this
module reproduces the screen's trades on the same bars. Book 13 is not exposed (reclassified
FAIL_BY_PROTOCOL_PARTIAL_FOLD_1), nor are the FAIL books.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RISK = 0.005
RISK_LOW = 0.0025
ATR_N = 14
STOP_ATR = 2.5

BOOK_NAMES = {
    1: "SMA200", 2: "EMA200", 3: "SMA50", 4: "MA slope", 5: "Golden/death cross", 6: "Weinstein",
    7: "Donchian 20/10", 8: "Donchian 55/20", 9: "Donchian 20 gated by Weinstein", 10: "Supertrend 10/3",
    11: "ADX/DMI 14", 12: "Ichimoku 9/26/52", 13: "Ichimoku + weekly cloud", 14: "ROC 90", 15: "ROC 30",
    16: "52-week high (long)", 17: "All-time high (long)", 18: "Swing structure 5", 19: "Combined (4 side, 7 trigger)",
}
BOOK_KIND = {b: "state" for b in (1, 2, 3, 4, 6, 11, 12, 13, 14, 15, 18)}
BOOK_KIND.update({b: "event" for b in (5, 7, 8, 9, 10, 16, 17, 19)})


# ---------------------------------------------------------------------------------------------
# Indicators. "_raw" values include bar t; prev() shifts so the signal bar is excluded.
# ---------------------------------------------------------------------------------------------
def prev(a: np.ndarray, k: int = 1) -> np.ndarray:
    out = np.full(len(a), np.nan)
    out[k:] = a[:-k]
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).mean().to_numpy()


def ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(span=n, adjust=False, min_periods=n).mean().to_numpy()


def rma(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder smoothing (alpha = 1/n), NaN-aware start."""
    return pd.Series(x).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean().to_numpy()


def roll_max(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).max().to_numpy()


def roll_min(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).min().to_numpy()


def true_range(h, l, c) -> np.ndarray:
    pc = prev(c)
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    tr[0] = h[0] - l[0]
    return tr


def atr_wilder(h, l, c, n: int) -> np.ndarray:
    """ATR = Wilder RMA of true range (used for the stop and for Supertrend)."""
    return rma(true_range(h, l, c), n)


def adx_dmi(h, l, c, n: int = 14):
    up = h - prev(h)
    dn = prev(l) - l
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pdm[0] = ndm[0] = np.nan
    atr = atr_wilder(h, l, c, n)
    pdi = 100.0 * rma(pdm, n) / atr
    ndi = 100.0 * rma(ndm, n) / atr
    dx = 100.0 * np.abs(pdi - ndi) / (pdi + ndi)
    adx = rma(dx, n)
    return adx, pdi, ndi


def supertrend_dir(h, l, c, n: int = 10, mult: float = 3.0) -> np.ndarray:
    """Direction (+1/-1, 0 until first established). A flip at t compares close[t] with the previous bar's
    final band, so the signal bar is not part of the band it is compared with."""
    atr = atr_wilder(h, l, c, n)
    hl2 = (h + l) / 2.0
    bu, bl = hl2 + mult * atr, hl2 - mult * atr
    N = len(c)
    fu, fl = np.full(N, np.nan), np.full(N, np.nan)
    d = np.zeros(N)
    for t in range(N):
        if np.isnan(atr[t]):
            continue
        if t == 0 or np.isnan(fu[t - 1]):
            fu[t], fl[t] = bu[t], bl[t]
            continue
        fu[t] = bu[t] if (bu[t] < fu[t - 1] or c[t - 1] > fu[t - 1]) else fu[t - 1]
        fl[t] = bl[t] if (bl[t] > fl[t - 1] or c[t - 1] < fl[t - 1]) else fl[t - 1]
        if d[t - 1] <= 0 and c[t] > fu[t - 1]:
            d[t] = 1
        elif d[t - 1] >= 0 and c[t] < fl[t - 1]:
            d[t] = -1
        else:
            d[t] = d[t - 1]
    return d


def ichimoku_cloud(h, l, n1=9, n2=26, n3=52, disp=26):
    """Cloud top/bottom at t from spans computed on bars <= t - disp (known on the close of t)."""
    tenkan = (roll_max(h, n1) + roll_min(l, n1)) / 2.0
    kijun = (roll_max(h, n2) + roll_min(l, n2)) / 2.0
    span_a = (tenkan + kijun) / 2.0
    span_b = (roll_max(h, n3) + roll_min(l, n3)) / 2.0
    a, b = prev(span_a, disp), prev(span_b, disp)
    return np.fmax(a, b), np.fmin(a, b), ~(np.isnan(a) | np.isnan(b))


def weekly_bars(d: pd.DataFrame) -> pd.DataFrame:
    """Completed Monday..Sunday UTC weeks (index = Sunday). Weeks with < 7 daily bars are dropped."""
    r = d.resample("W-SUN", label="right", closed="right")
    w = pd.DataFrame({"open": r["open"].first(), "high": r["high"].max(), "low": r["low"].min(),
                      "close": r["close"].last(), "n": r["close"].size()})
    return w[w["n"] == 7].drop(columns="n")


def weekly_to_daily(ws: pd.Series, daily_index: pd.DatetimeIndex) -> np.ndarray:
    """A week ending Sunday S applies to daily sessions from Monday S+1 on (never to its own days)."""
    s = ws.copy()
    s.index = s.index + pd.Timedelta(days=1)
    return s.reindex(daily_index, method="ffill").to_numpy(dtype=float)


def choppiness(h, l, c, n: int = 14) -> np.ndarray:
    tr = true_range(h, l, c)
    rng = roll_max(h, n) - roll_min(l, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100.0 * np.log10(pd.Series(tr).rolling(n, min_periods=n).sum().to_numpy() / rng) / np.log10(n)
    out[~np.isfinite(out)] = np.nan
    return out


def efficiency_ratio(c, n: int = 10) -> np.ndarray:
    change = np.abs(c - prev(c, n))
    vol = pd.Series(np.abs(c - prev(c))).rolling(n, min_periods=n).sum().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        er = change / vol
    er[~np.isfinite(er)] = np.nan
    return er


def realized_vol_high(c) -> np.ndarray:
    """True where 20-day realized vol (std of daily log returns, bars <= t-1) is above the 70th percentile of
    its own trailing 365 values. NaN while the 1-year window is incomplete."""
    lr = np.log(c / prev(c))
    rv = prev(pd.Series(lr).rolling(20, min_periods=20).std().to_numpy())
    q = pd.Series(rv).rolling(365, min_periods=365).quantile(0.70).to_numpy()
    out = np.where(np.isnan(q) | np.isnan(rv), np.nan, (rv > q).astype(float))
    return out


# ---------------------------------------------------------------------------------------------
# Books. Each returns dict(state, trig, valid): state in {-1,0,1} at close t, trig (event books) in
# {-1,0,1}, valid = bool mask where the rule is computable.
# ---------------------------------------------------------------------------------------------
def _sign_vs(c, line):
    valid = ~np.isnan(line)
    st = np.where(valid, np.sign(c - np.where(valid, line, 0.0)), 0.0)
    return st, valid


def _event_regime(trig, exit_long, exit_short):
    N = len(trig)
    st = np.zeros(N)
    reg = 0.0
    for t in range(N):
        if trig[t] != 0:
            reg = trig[t]
        if reg == 1 and exit_long[t]:
            reg = 0.0
        elif reg == -1 and exit_short[t]:
            reg = 0.0
        st[t] = reg
    return st


def _donchian(c, h, l, n_entry, n_exit):
    hi_e, lo_e = prev(roll_max(h, n_entry)), prev(roll_min(l, n_entry))
    hi_x, lo_x = prev(roll_max(h, n_exit)), prev(roll_min(l, n_exit))
    valid = ~(np.isnan(hi_e) | np.isnan(lo_e) | np.isnan(hi_x) | np.isnan(lo_x))
    with np.errstate(invalid="ignore"):
        trig = np.where(valid & (c > hi_e), 1.0, np.where(valid & (c < lo_e), -1.0, 0.0))
        exit_long = valid & (c < lo_x)
        exit_short = valid & (c > hi_x)
    return trig, exit_long, exit_short, valid


def weinstein_stage(d: pd.DataFrame) -> np.ndarray:
    """+1 Stage 2, -1 Stage 4, 0 Stage 1/3, NaN until computable; mapped to later daily sessions."""
    w = weekly_bars(d)
    wc = w["close"].to_numpy()
    s30 = sma(wc, 30)
    s30_4 = prev(s30, 4)
    with np.errstate(invalid="ignore"):
        st = np.where(np.isnan(s30_4), np.nan,
                      np.where((wc > s30) & (s30 > s30_4), 1.0, np.where((wc < s30) & (s30 < s30_4), -1.0, 0.0)))
    return weekly_to_daily(pd.Series(st, index=w.index), d.index)


def weekly_cloud_state(d: pd.DataFrame) -> np.ndarray:
    w = weekly_bars(d)
    top, bot, ok = ichimoku_cloud(w["high"].to_numpy(), w["low"].to_numpy())
    wc = w["close"].to_numpy()
    st = np.where(~ok, np.nan, np.where(wc > top, 1.0, np.where(wc < bot, -1.0, 0.0)))
    return weekly_to_daily(pd.Series(st, index=w.index), d.index)


def swing_structure_state(h, l, c, k: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """Pivots: strictly above/below the k bars on each side, confirmed k bars later. A pivot is usable from
    signal bar t only if confirmed on or before t-1. Long while last two highs HH and lows HL; short on LH/LL;
    a close through the last opposite pivot flats it until a new pivot is confirmed."""
    N = len(c)
    hs = pd.Series(h)
    ls = pd.Series(l)
    left_h = hs.shift(1).rolling(k, min_periods=k).max().to_numpy()
    right_h = hs[::-1].shift(1).rolling(k, min_periods=k).max().to_numpy()[::-1]
    left_l = ls.shift(1).rolling(k, min_periods=k).min().to_numpy()
    right_l = ls[::-1].shift(1).rolling(k, min_periods=k).min().to_numpy()[::-1]
    with np.errstate(invalid="ignore"):
        ph = (h > left_h) & (h > right_h)
        pl_ = (l < left_l) & (l < right_l)
    st = np.zeros(N)
    valid = np.zeros(N, dtype=bool)
    highs, lows = [], []
    broken, version = False, None
    for t in range(N):
        p = t - k - 1                     # pivot at p is confirmed at p+k <= t-1
        if p >= 0:
            if ph[p]:
                highs.append(h[p])
            if pl_[p]:
                lows.append(l[p])
        if len(highs) < 2 or len(lows) < 2:
            continue
        valid[t] = True
        ver = (len(highs), len(lows))
        if ver != version:
            version, broken = ver, False
        h1, h2, l1, l2 = highs[-2], highs[-1], lows[-2], lows[-1]
        s = 1.0 if (h2 > h1 and l2 > l1) else (-1.0 if (h2 < h1 and l2 < l1) else 0.0)
        if s == 1 and c[t] < l2:
            broken = True
        elif s == -1 and c[t] > h2:
            broken = True
        st[t] = 0.0 if broken else s
    return st, valid


def build_books(d: pd.DataFrame) -> dict:
    o, h, l, c = (d[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    N = len(c)
    zeros = np.zeros(N)
    books = {}

    s200 = prev(sma(c, 200))
    books[1] = dict(zip(("state", "valid"), _sign_vs(c, s200)))
    books[2] = dict(zip(("state", "valid"), _sign_vs(c, prev(ema(c, 200)))))
    books[3] = dict(zip(("state", "valid"), _sign_vs(c, prev(sma(c, 50)))))

    s200_20 = prev(s200, 20)
    v4 = ~(np.isnan(s200) | np.isnan(s200_20))
    with np.errstate(invalid="ignore"):
        st4 = np.where(v4 & (c > s200) & (s200 > s200_20), 1.0, np.where(v4 & (c < s200) & (s200 < s200_20), -1.0, 0.0))
    books[4] = dict(state=st4, valid=v4)

    s50 = prev(sma(c, 50))
    v5 = ~(np.isnan(s50) | np.isnan(s200) | np.isnan(prev(s50)) | np.isnan(prev(s200)))
    with np.errstate(invalid="ignore"):
        up = v5 & (s50 > s200) & (prev(s50) <= prev(s200))
        dn = v5 & (s50 < s200) & (prev(s50) >= prev(s200))
    trig5 = np.where(up, 1.0, np.where(dn, -1.0, 0.0))
    books[5] = dict(trig=trig5, state=_event_regime(trig5, np.zeros(N, bool), np.zeros(N, bool)), valid=v5)

    stage = weinstein_stage(d)
    v6 = ~np.isnan(stage)
    books[6] = dict(state=np.where(v6, stage, 0.0), valid=v6)

    trig7, xl7, xs7, v7 = _donchian(c, h, l, 20, 10)
    books[7] = dict(trig=trig7, state=_event_regime(trig7, xl7, xs7), valid=v7)
    trig8, xl8, xs8, v8 = _donchian(c, h, l, 55, 20)
    books[8] = dict(trig=trig8, state=_event_regime(trig8, xl8, xs8), valid=v8)

    stage0 = np.where(v6, stage, 0.0)
    trig9 = np.where((trig7 != 0) & (trig7 == stage0), trig7, 0.0)
    books[9] = dict(trig=trig9, state=_event_regime(trig9, xl7, xs7), valid=v7 & v6)

    sdir = supertrend_dir(h, l, c, 10, 3.0)
    trig10 = np.where((sdir != 0) & (prev(sdir) == -sdir), sdir, 0.0)
    trig10[0] = 0.0
    books[10] = dict(trig=trig10, state=_event_regime(trig10, np.zeros(N, bool), np.zeros(N, bool)), valid=sdir != 0)

    adx, pdi, ndi = (prev(x) for x in adx_dmi(h, l, c, 14))
    v11 = ~(np.isnan(adx) | np.isnan(pdi) | np.isnan(ndi))
    with np.errstate(invalid="ignore"):
        st11 = np.where(v11 & (adx > 25) & (pdi > ndi), 1.0, np.where(v11 & (adx > 25) & (ndi > pdi), -1.0, 0.0))
    books[11] = dict(state=st11, valid=v11)

    top, bot, v12 = ichimoku_cloud(h, l)
    with np.errstate(invalid="ignore"):
        st12 = np.where(v12 & (c > top), 1.0, np.where(v12 & (c < bot), -1.0, 0.0))
    books[12] = dict(state=st12, valid=v12)

    wcl = weekly_cloud_state(d)
    v13 = v12 & ~np.isnan(wcl)
    st13 = np.where(v13 & (st12 != 0) & (st12 == np.where(v13, wcl, 0.0)), st12, 0.0)
    books[13] = dict(state=st13, valid=v13)

    for b, n in ((14, 90), (15, 30)):
        base = prev(c, n)
        v = ~np.isnan(base)
        books[b] = dict(state=np.where(v, np.sign(c / np.where(v, base, 1.0) - 1.0), 0.0), valid=v)

    hi252 = prev(roll_max(c, 252))
    v16 = ~(np.isnan(hi252) | np.isnan(s200))
    with np.errstate(invalid="ignore"):
        trig16 = np.where(v16 & (c >= hi252), 1.0, 0.0)
        below200 = v16 & (c < s200)
    books[16] = dict(trig=trig16, state=_event_regime(trig16, below200, np.zeros(N, bool)), valid=v16)

    ath = prev(np.maximum.accumulate(c))
    v17 = ~(np.isnan(ath) | np.isnan(s200))
    with np.errstate(invalid="ignore"):
        trig17 = np.where(v17 & (c >= ath), 1.0, 0.0)
        below200_17 = v17 & (c < s200)
    books[17] = dict(trig=trig17, state=_event_regime(trig17, below200_17, np.zeros(N, bool)), valid=v17)

    st18, v18 = swing_structure_state(h, l, c, 5)
    books[18] = dict(state=st18, valid=v18)

    side = st4
    trig19 = np.where((trig7 != 0) & (trig7 == side), trig7, 0.0)
    xl19 = xl7 | (side != 1)
    xs19 = xs7 | (side != -1)
    rvh = realized_vol_high(c)
    books[19] = dict(trig=trig19, state=_event_regime(trig19, xl19, xs19), valid=v4 & v7,
                     risk=np.where(rvh == 1.0, RISK_LOW, RISK), risk_rule_known=~np.isnan(rvh))
    for b in books:
        books[b].setdefault("trig", zeros if BOOK_KIND[b] == "state" else books[b]["trig"])
        books[b]["kind"] = BOOK_KIND[b]
    return books


# ---------------------------------------------------------------------------------------------
# Single-symbol trade path (the screen's simulate() for one symbol, without sizing or funding).
# Sizing does not change the path: the screen's 2x skip can't trigger at 0.5% risk on a daily ATR stop.
# ---------------------------------------------------------------------------------------------
TAB_BOOKS = (1, 2, 3, 4, 5, 6, 9, 11, 12, 14, 15, 19)   # displayed survivors; book 13 reclassified FAIL


def simulate_path(d: pd.DataFrame, book: int, i_start: int, i_end: int, slip: float,
                  books: dict | None = None) -> list[dict]:
    """Signals on close t fill at open t+1; stop 2.5 x ATR(14, bars <= t-1) from the slipped fill; gap
    through the stop fills at the open; stop checked before indicator exits and on the entry bar; one
    position; no same-bar reversal; open position closed at the close of i_end. Prices include slippage."""
    o, h, l, c = (d[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    atr = prev(atr_wilder(h, l, c, ATR_N))
    b = (books or build_books(d))[book]
    state, trig, kind = b["state"], b["trig"], b["kind"]
    risk = b.get("risk")
    pos, pending, armed = None, None, 0.0
    trades: list[dict] = []

    def close(px_raw, i, reason):
        side = pos["side"]
        trades.append(dict(side=side, signal_index=pos["signal_index"], entry_index=pos["entry_index"],
                           exit_index=i, entry_price=pos["entry"], stop_price=pos["stop"],
                           exit_price=px_raw * (1 - slip * side), exit_reason=reason, atr=pos["atr"],
                           risk_frac=pos["risk"]))

    for i in range(max(i_start - 1, 0), i_end + 1):
        if i >= i_start:
            act, pending = pending, None
            if pos is not None:
                gap = (pos["side"] == 1 and o[i] <= pos["stop"]) or (pos["side"] == -1 and o[i] >= pos["stop"])
                if gap:
                    close(o[i], i, "stop_gap")
                    pos = None
                elif act and act[0] == "exit":
                    close(o[i], i, "signal")
                    pos = None
            elif act and act[0] == "enter":
                side = act[1]
                if kind == "event":
                    armed = 0.0
                fill = o[i] * (1 + slip * side)
                dist = STOP_ATR * atr[i - 1]
                pos = dict(side=side, entry=fill, stop=fill - side * dist, atr=atr[i - 1], entry_index=i,
                           signal_index=i - 1, risk=float(risk[i - 1]) if risk is not None else RISK)
            if pos is not None:
                hit = (pos["side"] == 1 and l[i] <= pos["stop"]) or (pos["side"] == -1 and h[i] >= pos["stop"])
                if hit:
                    close(pos["stop"], i, "stop")
                    pos = None
        if i == i_end:
            if pos is not None:
                close(c[i], i, "window_end")
                pos = None
            continue
        st, tg = state[i], trig[i]
        if kind == "event":
            if tg != 0 and st == tg and (pos is None or pos["side"] != tg):
                armed = tg
            if armed != 0 and st != armed:
                armed = 0.0
        if pos is not None:
            if st != pos["side"]:
                pending = ("exit",)
        elif st != 0 and not np.isnan(atr[i]) and (kind == "state" or armed == st):
            pending = ("enter", st)
    return trades
