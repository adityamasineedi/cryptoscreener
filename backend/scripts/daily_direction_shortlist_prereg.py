"""Pre-registered daily crypto direction screen (research only).

18 Tier 1 books + 1 combined book (book 19) on closed UTC daily bars of BTCUSDT, ETHUSDT, SOLUSDT.
Every parameter below is the hypothesis; nothing here is tuned, searched or production. A single
SURVIVE is a multiple-comparison screen hit (19 comparisons), not a confirmed edge.

Run once:  D:/cryptoscreener/backend/.venv/Scripts/python.exe backend/scripts/daily_direction_shortlist_prereg.py
Writes only under backend/reports/daily_direction_shortlist_prereg/. Refuses to run if verdict.json exists.
Holdout 2026-07-15 onward is sealed: no bar at or after it is loaded, computed, plotted or cached.

Decisions confirmed by the user on 2026-10-18 before any result was computed:
- Daily bars are UTC-day aggregations of the regime-book Binance USDT-M 1h parquet cache
  (2021-06-01 -> 2026-07-14); no local kline history exists before 2021-06-01, so warm-up from
  2019 and the 2020-2021 train start are not available (logged in not_tested.csv).
- "While" (state) books re-enter at the next open after an ATR stop if their rule still holds.
  Event books need a fresh trigger.
- The three-symbol book is one shared $10,000 account per test window.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

# ---------------------------------------------------------------------------------------------
# Frozen configuration
# ---------------------------------------------------------------------------------------------
SCRIPT_PATH = Path(__file__).resolve()
BACKEND = SCRIPT_PATH.parents[1]
RUN1_DIR = BACKEND / "reports" / "daily_direction_shortlist_prereg"
# Run 1 (sha256 6be6465b..., 2026-10-18T05:04:48Z) stays untouched in RUN1_DIR. Two engine bugs found after it
# (funding never charged: seconds vs nanoseconds timestamps; event books re-armed by a trigger that fired while
# already in a same-side position) were fixed with the user's approval, and run 2 writes here with its own lock.
OUT_DIR = RUN1_DIR / "run2_bugfix"
RUN1_BUGS = [
    "funding was never charged: funding timestamps are datetime64[s] in pandas 3 and were compared with nanosecond "
    "day stamps, so every trade's funding was 0",
    "event books (7, 8, 9, 16, 17, 19) could re-enter after a stop without a fresh trigger: a trigger that fired "
    "while already in a same-side position stayed armed",
]

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
KLINE_ROOT = Path("D:/cryptoscreener-worktrees/regime-book/backend/data/regime_book_klines")
HISTORY_START = pd.Timestamp("2019-01-01", tz="UTC")
FIRST_ENTRY = pd.Timestamp("2020-01-01", tz="UTC")
LAST_BAR = pd.Timestamp("2026-07-14", tz="UTC")          # last closed daily bar allowed
HOLDOUT_START = pd.Timestamp("2026-07-15", tz="UTC")     # sealed: nothing at/after is read
_CUT_S = int(HOLDOUT_START.timestamp())

START_EQUITY = 10_000.0
RISK = 0.005
RISK_LOW = 0.0025
LEV_CAP = 2.0
ATR_N = 14
STOP_ATR = 2.5
FEE = 0.0004
SLIP = {"BTCUSDT": 0.0005, "ETHUSDT": 0.0005, "SOLUSDT": 0.0015}
N_COMPARISONS = 19

FOLDS = [
    dict(fold=1, train_start=pd.Timestamp("2020-01-01", tz="UTC"), train_end=pd.Timestamp("2021-12-31", tz="UTC"),
         test_start=pd.Timestamp("2022-01-01", tz="UTC"), test_end=pd.Timestamp("2022-12-31", tz="UTC")),
    dict(fold=2, train_start=pd.Timestamp("2020-01-01", tz="UTC"), train_end=pd.Timestamp("2022-12-31", tz="UTC"),
         test_start=pd.Timestamp("2023-01-01", tz="UTC"), test_end=pd.Timestamp("2024-12-31", tz="UTC")),
    dict(fold=3, train_start=pd.Timestamp("2020-01-01", tz="UTC"), train_end=pd.Timestamp("2024-12-31", tz="UTC"),
         test_start=pd.Timestamp("2025-01-01", tz="UTC"), test_end=pd.Timestamp("2026-07-14", tz="UTC")),
]

BOOK_NAMES = {
    1: "SMA200", 2: "EMA200", 3: "SMA50", 4: "MA slope", 5: "Golden/death cross", 6: "Weinstein",
    7: "Donchian 20/10", 8: "Donchian 55/20", 9: "Donchian 20 gated by Weinstein", 10: "Supertrend 10/3",
    11: "ADX/DMI 14", 12: "Ichimoku 9/26/52", 13: "Ichimoku + weekly cloud", 14: "ROC 90", 15: "ROC 30",
    16: "52-week high (long)", 17: "All-time high (long)", 18: "Swing structure 5", 19: "Combined (4 side, 7 trigger)",
}
BOOK_KIND = {b: "state" for b in (1, 2, 3, 4, 6, 11, 12, 13, 14, 15, 18)}
BOOK_KIND.update({b: "event" for b in (5, 7, 8, 9, 10, 16, 17, 19)})

# ---------------------------------------------------------------------------------------------
# Loader (local stored data only; holdout filtered before collect)
# ---------------------------------------------------------------------------------------------
KLINE_SCHEMA = {"time": pl.Int64, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
                "close": pl.Float64, "volume": pl.Float64, "quote_volume": pl.Float64, "trade_count": pl.Int64}
FUNDING_SCHEMA = {"time": pl.Int64, "interval_hours": pl.Float64, "rate": pl.Float64}
FUNDING_API_SCHEMA = {"time": pl.Int64, "rate": pl.Float64}


def _check_schema(path: Path, expected: dict) -> None:
    got = dict(pl.read_parquet_schema(path))
    if got != expected:
        raise SystemExit(f"schema mismatch in {path}: expected {expected}, got {got}")


def _to_pandas(df: pl.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({c: df[c].to_numpy() for c in df.columns})


def load_hourly(symbol: str) -> pd.DataFrame | None:
    """1h Binance USDT-M klines, open-time index (UTC); rows at/after the holdout are filtered before collect."""
    path = KLINE_ROOT / "1h" / f"{symbol}.parquet"
    if not path.exists():
        return None
    _check_schema(path, KLINE_SCHEMA)
    lo = int(HISTORY_START.timestamp())
    df = pl.scan_parquet(path).filter((pl.col("time") >= lo) & (pl.col("time") < _CUT_S)).sort("time").collect()
    out = _to_pandas(df)
    out.index = pd.to_datetime(out.pop("time"), unit="s", utc=True)
    if out.index.has_duplicates:
        raise SystemExit(f"duplicate 1h bars in {symbol}")
    return out


def daily_from_hourly(h: pd.DataFrame) -> pd.DataFrame:
    """UTC calendar days with all 24 hourly bars: first open, max high, min low, last close, summed volume."""
    g = h.groupby(h.index.floor("D"))
    d = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
        "volume": g["volume"].sum(), "quote_volume": g["quote_volume"].sum(), "n_hours": g["open"].size(),
    })
    incomplete = d.index[d["n_hours"] != 24]
    if len(incomplete):
        raise SystemExit(f"incomplete daily bars (not 24 hourly rows): {list(incomplete[:5])}")
    d = d.drop(columns="n_hours")
    d = d[d.index <= LAST_BAR]
    expected = pd.date_range(d.index[0], d.index[-1], freq="D", tz="UTC")
    if len(expected) != len(d):
        raise SystemExit("missing daily bars")
    return d


def load_funding(symbol: str) -> pd.DataFrame | None:
    """Funding settlements (index = settlement time UTC; cols rate, interval_hours, rate_8h). None if no local series."""
    frames = []
    p = KLINE_ROOT / "funding" / f"{symbol}.parquet"
    if p.exists():
        _check_schema(p, FUNDING_SCHEMA)
        frames.append(pl.scan_parquet(p).filter(pl.col("time") < _CUT_S).collect())
    a = KLINE_ROOT / "funding_api" / f"{symbol}.parquet"
    if a.exists():
        _check_schema(a, FUNDING_API_SCHEMA)
        frames.append(pl.scan_parquet(a).filter(pl.col("time") < _CUT_S).collect()
                      .with_columns(pl.lit(None, dtype=pl.Float64).alias("interval_hours"))
                      .select("time", "interval_hours", "rate"))
    if not frames:
        return None
    df = pl.concat(frames).unique("time", keep="first").sort("time")
    out = _to_pandas(df)
    out.index = pd.to_datetime(out.pop("time"), unit="s", utc=True)
    # funding_api rows carry no interval: infer from spacing to the previous settlement
    gap_h = out.index.to_series().diff().dt.total_seconds().div(3600.0)
    out["interval_hours"] = out["interval_hours"].fillna(gap_h).fillna(8.0)
    out["rate_8h"] = out["rate"] * 8.0 / out["interval_hours"]
    return out


def load_all() -> tuple[dict, dict, dict]:
    """Returns (daily frames, hourly frames, funding) keyed by symbol; missing symbol -> absent key."""
    daily, hourly, funding = {}, {}, {}
    for s in SYMBOLS:
        h = load_hourly(s)
        if h is None:
            continue
        hourly[s] = h
        daily[s] = daily_from_hourly(h)
        funding[s] = load_funding(s)
    return daily, hourly, funding


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


def gate_arrays(d: pd.DataFrame) -> dict:
    """Tier 3 gate inputs (bars <= t-1). True = force flat."""
    h, l, c = (d[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    chop = prev(choppiness(h, l, c, 14))
    er = prev(efficiency_ratio(c, 10))
    with np.errstate(invalid="ignore"):
        return dict(chop=np.nan_to_num(chop) > 61.8,
                    er=~np.isnan(er) & (er < 0.30),
                    rvh=realized_vol_high(c))


# ---------------------------------------------------------------------------------------------
# Execution engine: one shared account per scope and window
# ---------------------------------------------------------------------------------------------
class SymbolData:
    def __init__(self, sym: str, d: pd.DataFrame, hourly: pd.DataFrame, funding: pd.DataFrame | None):
        self.sym = sym
        self.index = d.index
        self.o, self.h, self.l, self.c = (d[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        self.atr = prev(atr_wilder(self.h, self.l, self.c, ATR_N))
        self.slip = SLIP[sym]
        self.funding_missing = funding is None
        if funding is not None:
            px = hourly["open"].reindex(funding.index, method="ffill")
            self.f_ts = funding.index.as_unit("ns").asi8
            self.f_cash = (funding["rate"] * px).to_numpy()          # per unit of qty, long pays when > 0
            self.f_cum = np.concatenate([[0.0], np.cumsum(self.f_cash)])

    def funding_per_unit(self, t0_ns: int, t1_ns: int) -> float:
        """Sum of rate * price over settlements s with t0 < s <= t1 (long pays positive)."""
        if self.funding_missing:
            return 0.0
        i0 = np.searchsorted(self.f_ts, t0_ns, side="right")
        i1 = np.searchsorted(self.f_ts, t1_ns, side="right")
        return float(self.f_cum[i1] - self.f_cum[i0])


DAY_NS = 86_400 * 10**9


def simulate(syms: list[SymbolData], sigs: list[dict], start: pd.Timestamp, end: pd.Timestamp,
             risk_arrays: list[np.ndarray] | None = None, record: dict | None = None):
    """Shared-account daily simulation. Signals on close t fill at open t+1; stop checked before indicator
    exits; one position per symbol; no same-bar reversal; forced close at the window's last close.
    Returns (trades list, equity Series, exposure, skipped_count)."""
    idx = syms[0].index
    i0 = int(idx.searchsorted(start))
    i1 = int(idx.searchsorted(end))
    if i1 >= len(idx) or idx[i1] != end or idx[i0] != start:
        raise SystemExit(f"window {start}..{end} not on the daily index")
    cash = START_EQUITY
    n = len(syms)
    pos = [None] * n
    pending = [None] * n                       # ("enter", side) or ("exit",)
    armed = [0.0] * n
    trades, eq, skipped = [], [], 0
    pos_days = 0
    for i in range(max(i0 - 1, 0), i1 + 1):
        day_ns = idx[i].value
        for k, sd in enumerate(syms):
            sg = sigs[k]
            if i >= i0:
                # --- open ---
                p = pos[k]
                act = pending[k]
                pending[k] = None
                if p is not None:
                    gap = (p["side"] == 1 and sd.o[i] <= p["stop"]) or (p["side"] == -1 and sd.o[i] >= p["stop"])
                    if gap:
                        cash += _close(sd, p, sd.o[i], day_ns, "stop_gap", trades, i)
                        pos[k] = None
                    elif act and act[0] == "exit":
                        cash += _close(sd, p, sd.o[i], day_ns, "signal", trades, i)
                        pos[k] = None
                elif act and act[0] == "enter":
                    side = act[1]
                    armed[k] = 0.0 if sg["kind"] == "event" else armed[k]
                    atr = sd.atr[i - 1]
                    risk = risk_arrays[k][i - 1] if risk_arrays is not None else RISK
                    fill = sd.o[i] * (1 + sd.slip * side)
                    dist = STOP_ATR * atr
                    qty = risk * cash / dist
                    if qty * fill > LEV_CAP * cash:
                        skipped += 1
                    else:
                        fee = FEE * qty * fill
                        cash -= fee
                        pos[k] = dict(side=side, qty=qty, entry=fill, stop=fill - side * dist, atr=atr, fee_in=fee,
                                      entry_ns=day_ns, entry_i=i, signal_i=i - 1, risk=risk)
                # --- intrabar stop (also on the entry bar) ---
                p = pos[k]
                if p is not None:
                    hit = (p["side"] == 1 and sd.l[i] <= p["stop"]) or (p["side"] == -1 and sd.h[i] >= p["stop"])
                    if hit:
                        cash += _close(sd, p, p["stop"], day_ns + DAY_NS // 2, "stop", trades, i)
                        pos[k] = None
            # --- close ---
            if i == i1:
                if pos[k] is not None:
                    cash += _close(sd, pos[k], sd.c[i], day_ns + DAY_NS - 1, "window_end", trades, i)
                    pos[k] = None
                continue
            st = sg["state"][i]
            tg = sg["trig"][i]
            if sg["kind"] == "event":
                # a trigger arms only if it can open a trade: not while already holding that side
                if tg != 0 and st == tg and (pos[k] is None or pos[k]["side"] != tg):
                    armed[k] = tg
                if armed[k] != 0 and st != armed[k]:
                    armed[k] = 0.0
            p = pos[k]
            if p is not None:
                if st != p["side"]:
                    pending[k] = ("exit",)
            elif st != 0 and not np.isnan(sd.atr[i]) and (sg["kind"] == "state" or armed[k] == st):
                pending[k] = ("enter", st)
        if i >= i0:
            mtm = cash
            for k, sd in enumerate(syms):
                p = pos[k]
                if p is not None:
                    pos_days += 1
                    mtm += p["side"] * p["qty"] * (sd.c[i] - p["entry"])
            eq.append((idx[i], mtm))
    days = i1 - i0 + 1
    equity = pd.Series([v for _, v in eq], index=[t for t, _ in eq], name="equity")
    return trades, equity, pos_days / (days * n), skipped


def _close(sd: SymbolData, p: dict, raw_px: float, exit_ns: int, reason: str, trades: list, i: int) -> float:
    side, qty = p["side"], p["qty"]
    px = raw_px * (1 - sd.slip * side)
    price_pnl = side * qty * (px - p["entry"])
    fee_out = FEE * qty * px
    funding = -side * qty * sd.funding_per_unit(p["entry_ns"], exit_ns)
    trades.append(dict(symbol=sd.sym, side=side, signal_time=sd.index[p["signal_i"]], entry_time=sd.index[p["entry_i"]],
                       entry_price=p["entry"], exit_time=sd.index[i], exit_price=px, exit_reason=reason, qty=qty,
                       notional=qty * p["entry"], stop_price=p["stop"], atr=p["atr"], risk=p["risk"],
                       price_pnl=price_pnl, fees=p["fee_in"] + fee_out, funding=funding,
                       net_pnl=price_pnl - p["fee_in"] - fee_out + funding,
                       funding_label="missing" if sd.funding_missing else "charged"))
    return price_pnl - fee_out + funding          # entry fee already left cash at entry


def stats(trades: list, equity: pd.Series, exposure: float, skipped: int) -> dict:
    net = np.array([t["net_pnl"] for t in trades]) if trades else np.zeros(0)
    gw, gl = net[net > 0].sum(), -net[net < 0].sum()
    peak = equity.cummax()
    dd = (equity - peak)
    return dict(trades=len(trades), net_pnl=float(net.sum()), gross_win=float(gw), gross_loss=float(gl),
                profit_factor=float(gw / gl) if gl > 0 else (math.inf if gw > 0 else float("nan")),
                max_drawdown=float(-dd.min()) if len(dd) else 0.0,
                max_drawdown_pct=float((-(dd / peak)).max() * 100) if len(dd) else 0.0,
                avg_trade=float(net.mean()) if len(net) else float("nan"), exposure=exposure,
                funding=float(sum(t["funding"] for t in trades)), fees=float(sum(t["fees"] for t in trades)),
                skipped_lev_cap=skipped, days=len(equity))


# ---------------------------------------------------------------------------------------------
# Tier 1 runner
# ---------------------------------------------------------------------------------------------
SCOPES = [("BTCUSDT", ["BTCUSDT"]), ("ETHUSDT", ["ETHUSDT"]), ("SOLUSDT", ["SOLUSDT"]), ("3SYM", SYMBOLS)]
VERDICT_SCOPES = ("BTCUSDT", "3SYM")


def window_status(books_by_sym: dict, book: int, members: list, start, end, index) -> tuple[str, str]:
    """NOT_TESTED if the rule is not computable on any day of the window for some member; PARTIAL if it becomes
    computable only after the window starts."""
    i0, i1 = index.searchsorted(start), index.searchsorted(end)
    notes, status = [], "OK"
    for s in members:
        v = books_by_sym[s][book]["valid"][max(i0 - 1, 0):i1 + 1]
        if not v.any():
            return "NOT_TESTED", f"{s}: rule not computable in window (insufficient local history before 2021-06-01)"
        first = int(np.argmax(v))
        if first > 1:
            status = "PARTIAL"
            notes.append(f"{s}: first computable signal {index[max(i0 - 1, 0) + first].date()}")
    return status, "; ".join(notes)


def run_variant(sd_by_sym, books_by_sym, gates_by_sym, book, members, start, end, variant):
    syms = [sd_by_sym[s] for s in members]
    sigs, risks = [], []
    for s in members:
        b = books_by_sym[s][book]
        st = b["state"]
        if variant == "chop":
            st = np.where(gates_by_sym[s]["chop"], 0.0, st)
        elif variant == "er":
            st = np.where(gates_by_sym[s]["er"], 0.0, st)
        sigs.append(dict(state=st, trig=b["trig"], kind=b["kind"]))
        if variant == "volsize":
            risks.append(np.where(gates_by_sym[s]["rvh"] == 1.0, RISK_LOW, RISK))
        else:
            risks.append(b.get("risk", np.full(len(st), RISK)))
    return simulate(syms, sigs, start, end, risks)


def verdict_for_scope(window_rows: list[dict], pooled: dict) -> tuple[str, int]:
    pos_windows = sum(1 for r in window_rows if r["status"] != "NOT_TESTED" and r["net_pnl"] > 0)
    ok = pooled["net_pnl"] > 0 and pooled["profit_factor"] > 1.1 and pos_windows >= 2
    return ("PASS" if ok else "FAIL"), pos_windows


def pooled_row(window_rows, window_trades):
    allt = [t for ts in window_trades for t in ts]
    net = np.array([t["net_pnl"] for t in allt]) if allt else np.zeros(0)
    gw, gl = net[net > 0].sum(), -net[net < 0].sum()
    tested = [r for r in window_rows if r["status"] != "NOT_TESTED"]
    days = sum(r["days"] for r in tested)
    return dict(trades=len(allt), net_pnl=float(net.sum()), gross_win=float(gw), gross_loss=float(gl),
                profit_factor=float(gw / gl) if gl > 0 else (math.inf if gw > 0 else float("nan")),
                max_drawdown=max([r["max_drawdown"] for r in tested], default=float("nan")),
                max_drawdown_pct=max([r["max_drawdown_pct"] for r in tested], default=float("nan")),
                avg_trade=float(net.mean()) if len(net) else float("nan"),
                exposure=(sum(r["exposure"] * r["days"] for r in tested) / days) if days else float("nan"),
                funding=float(sum(t["funding"] for t in allt)), fees=float(sum(t["fees"] for t in allt)),
                skipped_lev_cap=sum(r["skipped_lev_cap"] for r in tested), days=days)


# ---------------------------------------------------------------------------------------------
# Tier 2 / Tier 3 BBW / Tier 5 event studies and descriptive rows (no trades)
# ---------------------------------------------------------------------------------------------

_T25_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
_T25_H = 20
_T25_HALVINGS = ["2020-05-11", "2024-04-20"]
_T25_FG_PATH = Path("D:/cryptoscreener/backend/data/research_cache/external/fear_greed_alternative_me_from_2022-10.json")
_T25_NAN = float("nan")


# ----------------------------------------------------------------------------- helpers
def _t25_syms(daily: dict) -> list:
    return [s for s in _T25_SYMBOLS if s in daily]


def _t25_fwd(close: pd.Series) -> pd.Series:
    """fwd20 = close[D+20]/close[D] - 1; NaN when D+20 is past the last loaded bar (2026-07-14)."""
    return close.shift(-_T25_H) / close - 1.0


def _t25_onset(cond: pd.Series) -> pd.Series:
    c = cond.fillna(False).astype(bool)
    return c & ~c.shift(1, fill_value=False)


def _t25_days(mask: pd.Series) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(mask.index[mask.to_numpy(bool)])


def _t25_in_fold(days: pd.DatetimeIndex, f: dict) -> pd.DatetimeIndex:
    return days[(days >= f["test_start"]) & (days <= f["test_end"])]


def _t25_stats(days: pd.DatetimeIndex, fwd: pd.Series, expected: int) -> dict:
    r = fwd.reindex(days).to_numpy(float)
    r = r[~np.isnan(r)]
    n = int(r.size)
    if n == 0:
        return dict(event_count=int(len(days)), n_with_fwd=0, hit_rate=_T25_NAN, median_fwd20=_T25_NAN, mean_fwd20=_T25_NAN)
    hit = (np.sign(r) == expected) if expected != 0 else (r > 0)
    return dict(event_count=int(len(days)), n_with_fwd=n, hit_rate=float(hit.mean()),
                median_fwd20=float(np.median(r)), mean_fwd20=float(r.mean()))


def _t25_d(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%d")


def _t25_event_rows(tier: int, event: str, sym: str, days: pd.DatetimeIndex, fwd: pd.Series,
                    expected: int, folds: list, note: str = "") -> list:
    rows = []
    for f in folds:
        st = _t25_stats(_t25_in_fold(days, f), fwd, expected)
        rows.append(dict(tier=tier, event=event, symbol=sym, fold=int(f["fold"]),
                         test_start=_t25_d(f["test_start"]), test_end=_t25_d(f["test_end"]),
                         expected_direction=int(expected), **st, note=note))
    return rows


def _t25_nt(tier: int, item: str, symbol: str, reason: str) -> dict:
    return dict(tier=tier, item=item, symbol=symbol, reason=reason)


def _t25_t5_row(item, sym, f, metric, value, n, note=""):
    return dict(tier=5, item=item, symbol=sym, fold=int(f["fold"]), window_start=_t25_d(f["test_start"]),
                window_end=_t25_d(f["test_end"]), metric=metric, value=value, n=int(n), note=note)


def _t25_t5_event_rows(item: str, sym: str, days: pd.DatetimeIndex, fwd: pd.Series, expected: int,
                       folds: list, note: str) -> list:
    rows = []
    for f in folds:
        st = _t25_stats(_t25_in_fold(days, f), fwd, expected)
        rows += [_t25_t5_row(item, sym, f, "event_count", st["event_count"], st["event_count"], note),
                 _t25_t5_row(item, sym, f, "hit_rate", st["hit_rate"], st["n_with_fwd"], note),
                 _t25_t5_row(item, sym, f, "median_fwd20", st["median_fwd20"], st["n_with_fwd"], note)]
    return rows


def _t25_rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1.0 / n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1.0 / n, adjust=False).mean()
    return 100.0 - 100.0 / (1.0 + g / l)


# ----------------------------------------------------------------------------- tier 2
def tier2_events(daily: dict, funding: dict, folds: list) -> tuple[list, list]:
    rows, nt = [], []
    syms = _t25_syms(daily)
    fund_note = ("onset at the 3rd consecutive settlement; settlement time floored to its UTC day "
                 "(00:00 and 08:00 of D -> D); several onsets on one day count as one event day")
    for sym in syms:
        close = daily[sym]["close"]
        fwd = _t25_fwd(close)
        fr = funding.get(sym)
        if fr is None:
            for ev in ("funding_high_3x", "funding_low_3x"):
                nt.append(_t25_nt(2, ev, sym, "no local funding series for this symbol"))
            continue
        r8 = fr["rate_8h"].sort_index()
        note = fund_note + f"; funding series {_t25_d(r8.index[0])}..{_t25_d(r8.index[-1])}"
        for ev, cond in (("funding_high_3x", r8 > 0.0003), ("funding_low_3x", r8 < -0.0001)):
            three = cond & cond.shift(1, fill_value=False) & cond.shift(2, fill_value=False)
            on = _t25_onset(three)
            days = pd.DatetimeIndex(on.index[on.to_numpy(bool)].floor("D")).unique().sort_values()
            rows += _t25_event_rows(2, ev, sym, days, fwd, 0, folds, note)

    nt.append(_t25_nt(2, "oi_up20_flat_price", "ALL",
                      "Postgres open_interest has no rows before 2026-10-01 (inside the sealed holdout); "
                      "no local OI series covers the test windows; DB not queried"))
    nt.append(_t25_nt(2, "liquidation_99pct", "ALL",
                      "Postgres liquidations rows start 2026-10-16 (holdout); no local liquidation series "
                      "covers the test windows"))
    no_rule = "no event threshold was pre-registered, so defining one after the fact would be an unregistered choice"
    nt.append(_t25_nt(2, "futures_basis", "ALL",
                      "needs spot or index/mark klines: none found locally (regime_book_klines and research_cache "
                      "ohlcv_v1 hold USDT-M perp/Postgres klines only); also " + no_rule))
    for item in ("exchange_flows", "stablecoin_flows", "coinbase_premium", "mvrv", "sopr"):
        nt.append(_t25_nt(2, item, "ALL", "no local series found (searched D:/cryptoscreener/backend/data, "
                                         "D:/cryptoscreener-worktrees/*/backend/data, research_cache/external); also " + no_rule))
    for item in ("usdt_dominance", "btc_dominance"):
        nt.append(_t25_nt(2, item, "ALL",
                          "no local dominance series; research_market_cap_observations (CoinGecko, 66 coins, "
                          "2025-10..2026-10) is not a dominance series and a derived one would be a proxy that also "
                          "misses Folds 1-2; also " + no_rule))
    nt.append(_t25_nt(2, "fear_greed", "ALL",
                      f"local Fear & Greed JSON exists ({_T25_FG_PATH.as_posix()}) but starts 2022-10-01 (first record; file not read here, holdout rows inside), "
                      "so it does not cover Fold 1; and " + no_rule))

    for h in _T25_HALVINGS:
        hd = pd.Timestamp(h, tz="UTC")
        lo, hi = hd - pd.Timedelta(days=180), hd + pd.Timedelta(days=365)
        for sym in syms:
            close = daily[sym]["close"]
            idx = close.index
            days = pd.DatetimeIndex(idx[(idx >= lo) & (idx <= hi)])
            outside = all(len(_t25_in_fold(days, f)) == 0 for f in folds) if len(days) else True
            note = (f"every day {_t25_d(lo)}..{_t25_d(hi)} is an event (no onset rule); overlapping 20d forward windows")
            if outside:
                note += "; window falls outside all test windows"
            rows += _t25_event_rows(2, f"halving_window_{h}", sym, days, _t25_fwd(close), 0, folds, note)
    return rows, nt


# ----------------------------------------------------------------------------- tier 3
def tier3_bbw_events(daily: dict, folds: list) -> list:
    rows = []
    for sym in _t25_syms(daily):
        close = daily[sym]["close"]
        mid = close.rolling(20).mean()
        sd = close.rolling(20).std(ddof=0)
        bw = 4.0 * sd / mid
        prior_min = bw.shift(1).rolling(120).min()
        on = _t25_onset(bw <= prior_min)
        rows += _t25_event_rows(3, "bbw20_2_120d_low", sym, _t25_days(on), _t25_fwd(close), 0, folds,
                                "bw <= min(bw[D-120..D-1]); onset only")
    return rows


# ----------------------------------------------------------------------------- tier 5
def _t25_weekly_macd(close: pd.Series) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    w = close.resample("W-SUN", label="right", closed="right")
    wk = pd.DataFrame({"close": w.last(), "n": w.count()})
    wk = wk[wk["n"] == 7]["close"]
    macd = wk.ewm(span=12, adjust=False).mean() - wk.ewm(span=26, adjust=False).mean()
    sig = macd.ewm(span=9, adjust=False).mean()
    diff = macd - sig
    prev = diff.shift(1)
    up = (diff > 0) & (prev <= 0)
    dn = (diff < 0) & (prev >= 0)
    return _t25_days(up), _t25_days(dn)


def _t25_divergence(close: pd.Series, rsi: pd.Series, k: int = 30) -> tuple[pd.Series, pd.Series]:
    c = close.to_numpy(float)
    r = rsi.to_numpy(float)
    n = c.size
    bear = np.zeros(n, bool)
    bull = np.zeros(n, bool)
    if n > k:
        win = np.lib.stride_tricks.sliding_window_view(c[:-1], k)  # win[i] = c[i..i+k-1] -> day D = i+k
        base = np.arange(win.shape[0])
        imax = base + win.argmax(axis=1)  # first occurrence on ties
        imin = base + win.argmin(axis=1)
        D = base + k
        with np.errstate(invalid="ignore"):
            bear[D] = (c[D] > c[imax]) & (r[D] < r[imax])
            bull[D] = (c[D] < c[imin]) & (r[D] > r[imin])
    return (_t25_onset(pd.Series(bear, index=close.index)), _t25_onset(pd.Series(bull, index=close.index)))


def tier5_confirmation(daily: dict, folds: list) -> tuple[list, list]:
    rows, nt = [], []
    syms = _t25_syms(daily)
    srch = ("searched D:/cryptoscreener/backend/data, D:/cryptoscreener-worktrees/*/backend/data, "
            "research_cache/external; not fetched")
    nt.append(_t25_nt(5, "dxy_corr60", "ALL", "no local DXY series found; " + srch))
    nt.append(_t25_nt(5, "us_real_yield_corr60", "ALL", "no local US real-yield series found; " + srch))
    nt.append(_t25_nt(5, "nasdaq_corr60", "ALL",
                      "no local Nasdaq index series; only Binance TradFi perps QQQUSDT (from 2026-04-06) and "
                      "TQQQ/SQQQUSDT (from 2026-06-29) exist, which miss Folds 1-2 and are perp proxies; " + srch))

    # ETH/BTC 90d ratio return
    if "ETHUSDT" in daily and "BTCUSDT" in daily:
        ratio = (daily["ETHUSDT"]["close"] / daily["BTCUSDT"]["close"]).dropna()
        r90 = ratio / ratio.shift(90) - 1.0
        it = "eth_btc_90d_ratio_return"
        for f in folds:
            m = (r90.index >= f["test_start"]) & (r90.index <= f["test_end"])
            x = r90[m].dropna()
            rw = ratio[(ratio.index >= f["test_start"]) & (ratio.index <= f["test_end"])]
            n = len(x)
            rows += [_t25_t5_row(it, "ETHUSDT", f, "mean_r90", float(x.mean()) if n else _T25_NAN, n),
                     _t25_t5_row(it, "ETHUSDT", f, "median_r90", float(x.median()) if n else _T25_NAN, n),
                     _t25_t5_row(it, "ETHUSDT", f, "share_r90_pos", float((x > 0).mean()) if n else _T25_NAN, n),
                     _t25_t5_row(it, "ETHUSDT", f, "window_ratio_return",
                                 float(rw.iloc[-1] / rw.iloc[0] - 1.0) if len(rw) else _T25_NAN, len(rw),
                                 "ratio last/first - 1 over the test window")]
    else:
        nt.append(_t25_nt(5, "eth_btc_90d_ratio_return", "ETHUSDT", "ETHUSDT or BTCUSDT daily missing"))

    for sym in syms:
        close = daily[sym]["close"]
        fwd = _t25_fwd(close)
        rsi = _t25_rsi(close)

        # SMA200-long RSI
        long_ = close > close.shift(1).rolling(200).mean()
        it = "sma200_long_rsi"
        note = "SMA200 over closes t-200..t-1; RSI==40 in neither bucket; daily fwd20 windows overlap"
        for f in folds:
            m = long_ & (close.index >= f["test_start"]) & (close.index <= f["test_end"]) & rsi.notna()
            rl, fl = rsi[m], fwd[m]
            n = len(rl)
            share = float(((rl >= 40) & (rl <= 50)).mean()) if n else _T25_NAN
            rows.append(_t25_t5_row(it, sym, f, "share_rsi_40_50", share, n, note))
            for tag, sel in (("gt40", rl > 40), ("lt40", rl < 40)):
                x = fl[sel].dropna()
                k = len(x)
                rows.append(_t25_t5_row(it, sym, f, f"mean_fwd20_rsi_{tag}", float(x.mean()) if k else _T25_NAN, k, note))
                rows.append(_t25_t5_row(it, sym, f, f"median_fwd20_rsi_{tag}", float(x.median()) if k else _T25_NAN, k, note))

        # Weekly MACD crosses (D = week-ending Sunday)
        up, dn = _t25_weekly_macd(close)
        wnote = ("completed W-SUN weeks only; EMAs seeded at the first complete week; D = Sunday close; expected ")
        rows += _t25_t5_event_rows("weekly_macd_cross_up", sym, up, fwd, 1, folds, wnote + "+1")
        rows += _t25_t5_event_rows("weekly_macd_cross_down", sym, dn, fwd, -1, folds, wnote + "-1")

        # RSI regular divergence vs prior 30-day swing
        bear, bull = _t25_divergence(close, rsi)
        dnote = "divergence expected to be weak; prior swing = first max/min of close[D-30..D-1]; onset only; expected "
        rows += _t25_t5_event_rows("rsi14_divergence_bearish", sym, _t25_days(bear), fwd, -1, folds, dnote + "-1")
        rows += _t25_t5_event_rows("rsi14_divergence_bullish", sym, _t25_days(bull), fwd, 1, folds, dnote + "+1")
    return rows, nt


# ---------------------------------------------------------------------------------------------
# Tier 4 level context on Tier 1 fills (descriptive; no filter, no entries)
# ---------------------------------------------------------------------------------------------

_T4_SCOPE_ORDER = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "3SYM"]
_T4_ID_COLS = ["scope", "symbol", "book", "book_name", "variant", "fold", "side", "entry_time", "entry_price",
               "exit_time", "exit_price", "exit_reason", "atr"]
_T4_AVWAP_ANCHORS = {"avwap_2022_11_21": pd.Timestamp("2022-11-21", tz="UTC"),
                     "avwap_2024_01_11": pd.Timestamp("2024-01-11", tz="UTC")}
_T4_LEVELS = ["avwap_2022_11_21", "avwap_2024_01_11", "avwap_2025_cycle_high", "prior_week_open",
              "prior_month_open", "round_number"]
_T4_ROUND_LEVELS = np.arange(10000.0, 120000.0 + 1.0, 10000.0)
_T4_SWING_N = 20
_T4_DAY = pd.Timedelta(days=1)
_T4_EXIT_GROUPS = [("stop", ("stop", "stop_gap")), ("signal_or_window_end", ("signal", "window_end"))]


def _t4_bar_pos(idx: pd.DatetimeIndex, times: pd.Series) -> np.ndarray:
    """Position of each time in idx (exact match), -1 when absent."""
    t = pd.DatetimeIndex(times)
    pos = idx.searchsorted(t, side="left")
    pos_c = np.clip(pos, 0, len(idx) - 1)
    ok = (pos < len(idx)) & (np.asarray(idx[pos_c]) == np.asarray(t)) & ~t.isna()
    return np.where(ok, pos_c, -1)


def _t4_avwap(d: pd.DataFrame, anchor: pd.Timestamp, s_pos: np.ndarray) -> np.ndarray:
    """Anchored VWAP (typical price, volume-weighted) from anchor bar through bar s_pos; NaN if s < anchor."""
    out = np.full(len(s_pos), np.nan)
    a = int(d.index.searchsorted(anchor, side="left"))
    if a >= len(d) or d.index[a] != anchor:
        return out
    tp = (d["high"].to_numpy(float) + d["low"].to_numpy(float) + d["close"].to_numpy(float)) / 3.0
    v = d["volume"].to_numpy(float)
    cpv = np.concatenate([[0.0], np.cumsum(tp * v)])
    cv = np.concatenate([[0.0], np.cumsum(v)])
    ok = s_pos >= a
    sp = s_pos[ok]
    num = cpv[sp + 1] - cpv[a]
    den = cv[sp + 1] - cv[a]
    with np.errstate(divide="ignore", invalid="ignore"):
        out[ok] = np.where(den > 0, num / den, np.nan)
    return out


def _t4_open_on(d: pd.DataFrame, days: pd.Series) -> np.ndarray:
    pos = _t4_bar_pos(d.index, days)
    o = d["open"].to_numpy(float)
    return np.where(pos >= 0, o[np.clip(pos, 0, None)], np.nan)


def _t4_round_level(price: np.ndarray) -> np.ndarray:
    """Nearest of 10k..120k; NaN on an exact tie between two levels (ambiguous) or non-finite price."""
    p = np.asarray(price, float)
    dist = np.abs(p[:, None] - _T4_ROUND_LEVELS[None, :])
    j = np.argmin(dist, axis=1)
    best = dist[np.arange(len(p)), j]
    n_best = (dist == best[:, None]).sum(axis=1)
    lvl = _T4_ROUND_LEVELS[j]
    return np.where(np.isfinite(p) & (n_best == 1), lvl, np.nan)


def _t4_swing20(d: pd.DataFrame, s_pos: np.ndarray, side: np.ndarray) -> np.ndarray:
    """Long: max(high[s-20..s-1]); short: min(low[s-20..s-1]); NaN without 20 prior bars."""
    hi = d["high"].rolling(_T4_SWING_N, min_periods=_T4_SWING_N).max().shift(1).to_numpy(float)
    lo = d["low"].rolling(_T4_SWING_N, min_periods=_T4_SWING_N).min().shift(1).to_numpy(float)
    sp = np.clip(s_pos, 0, None)
    lvl = np.where(side > 0, hi[sp], np.where(side < 0, lo[sp], np.nan))
    return np.where(s_pos >= 0, lvl, np.nan)


def _t4_per_symbol(tr: pd.DataFrame, d: pd.DataFrame) -> pd.DataFrame:
    entry_t = pd.to_datetime(tr["entry_time"], utc=True)
    fill_day = entry_t.dt.floor("D")
    sig_day = fill_day - _T4_DAY
    s_pos = _t4_bar_pos(d.index, sig_day)
    side = tr["side"].to_numpy(float)
    out = pd.DataFrame(index=tr.index)
    for name, anchor in _T4_AVWAP_ANCHORS.items():
        out[name] = _t4_avwap(d, anchor, s_pos)
    out["avwap_2025_cycle_high"] = np.nan  # blank per spec
    week_mon = fill_day - pd.to_timedelta(fill_day.dt.weekday, unit="D")
    prev_mon = week_mon - pd.Timedelta(days=7)
    prev_month_first = (fill_day.dt.tz_localize(None).dt.to_period("M") - 1).dt.to_timestamp().dt.tz_localize("UTC")
    # both opens belong to days strictly before the fill day, i.e. at or before the signal bar
    out["prior_week_open"] = _t4_open_on(d, prev_mon)
    out["prior_month_open"] = _t4_open_on(d, prev_month_first)
    out["round_number"] = np.nan  # filled from entry_price by the caller (BTCUSDT only)
    out["swing20_level"] = _t4_swing20(d, s_pos, side)
    c = d["close"].to_numpy(float)
    out["_close_s"] = np.where(s_pos >= 0, c[np.clip(s_pos, 0, None)], np.nan)
    return out


def tier4_level_context(trades: pd.DataFrame, daily: dict) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    tr = trades.reset_index(drop=True).copy()
    lv = pd.DataFrame(index=tr.index, columns=_T4_LEVELS + ["swing20_level", "_close_s"], dtype=float)
    for sym, g in tr.groupby("symbol", sort=True):
        if sym in daily:
            lv.loc[g.index] = _t4_per_symbol(g, daily[sym])[lv.columns].to_numpy(float)
    ep = tr["entry_price"].to_numpy(float)
    atr = tr["atr"].to_numpy(float)
    is_btc = (tr["symbol"] == "BTCUSDT").to_numpy()
    round_level = np.where(is_btc, _t4_round_level(ep), np.nan)
    lv["round_number"] = round_level

    per = tr[_T4_ID_COLS].copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        for name in _T4_LEVELS:
            L = lv[name].to_numpy(float)
            per[f"dist_{name}_pct"] = (ep - L) / ep * 100.0
            per[f"dist_{name}_atr"] = np.where(atr > 0, (ep - L) / atr, np.nan)
    per["round_level"] = round_level
    for name in _T4_LEVELS:
        per[f"level_{name}"] = lv[name].to_numpy(float)
    sw = lv["swing20_level"].to_numpy(float)
    cs = lv["_close_s"].to_numpy(float)
    side = tr["side"].to_numpy(float)
    have = np.isfinite(sw) & np.isfinite(cs)
    broken = np.where(side > 0, cs > sw, cs < sw)
    per["swing20_level"] = sw
    per["broken_at_signal"] = pd.array(np.where(have, broken, False), dtype="boolean")
    per.loc[~have, "broken_at_signal"] = pd.NA
    xp = tr["exit_price"].to_numpy(float)
    have_x = np.isfinite(sw) & np.isfinite(xp) & np.isfinite(atr)
    per["exit_within_1atr_of_swing20"] = pd.array(np.where(have_x, np.abs(xp - sw) <= atr, False), dtype="boolean")
    per.loc[~have_x, "exit_within_1atr_of_swing20"] = pd.NA

    summary = _t4_summary(per)
    not_tested = [
        dict(tier=4, item="volume_profile", symbol="ALL",
             reason="no local volume-at-price series exists (only OHLCV bars; D:/cryptoscreener/backend/data "
                    "holds paper JSON, research OHLCV/feature caches, funding and fear-greed only)"),
        dict(tier=4, item="avwap_2025_cycle_high", symbol="ALL",
             reason="left blank per spec (columns all NaN)"),
    ]
    return per, summary, not_tested


def _t4_fold_key(f):
    return (1, 0) if f == "ALL" else (0, int(f))


def _t4_summary(per: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    scopes = [s for s in _T4_SCOPE_ORDER if s in set(per["scope"])] + \
        sorted(set(per["scope"]) - set(_T4_SCOPE_ORDER))
    for scope in scopes:
        ps = per[per["scope"] == scope]
        for book in sorted(ps["book"].unique()):
            pb = ps[ps["book"] == book]
            book_name = str(pb["book_name"].iloc[0])
            folds = sorted(pb["fold"].unique(), key=_t4_fold_key) + ["ALL"]
            for fold in folds:
                g = pb if fold == "ALL" else pb[pb["fold"] == fold]
                base = dict(tier=4, scope=scope, book=int(book), book_name=book_name,
                            fold=fold if fold == "ALL" else int(fold))
                rows.append({**base, "metric": "n_trades", "value": float(len(g)), "n": int(len(g)), "note": ""})
                for name in _T4_LEVELS:
                    x = g[f"dist_{name}_pct"].dropna()
                    note = "blank per spec" if name == "avwap_2025_cycle_high" else (
                        "BTCUSDT only" if name == "round_number" else "")
                    rows.append({**base, "metric": f"median_dist_{name}_pct",
                                 "value": float(x.median()) if len(x) else np.nan, "n": int(len(x)), "note": note})
                for grp, reasons in _T4_EXIT_GROUPS:
                    gg = g[g["exit_reason"].isin(reasons)]
                    flag = gg["exit_within_1atr_of_swing20"].dropna()
                    k = int(flag.sum())
                    note = "exit_reason in " + "/".join(reasons)
                    rows.append({**base, "metric": f"exit_within_1atr_swing20_count_{grp}", "value": float(k),
                                 "n": int(len(flag)), "note": note})
                    rows.append({**base, "metric": f"exit_within_1atr_swing20_share_{grp}",
                                 "value": k / len(flag) if len(flag) else np.nan, "n": int(len(flag)), "note": note})
    return rows


# ---------------------------------------------------------------------------------------------
# Out of scope groups, outputs, report
# ---------------------------------------------------------------------------------------------
SKIPPED_GROUPS = [
    ("equity breadth", "equity-market breadth does not apply to a three-coin crypto perp screen; out of scope by spec"),
    ("equity calendar effects", "equity session calendar effects; crypto trades 24/7; out of scope by spec"),
    ("opening range and intraday session tools", "intraday tools; this screen uses closed daily bars only; out of scope by spec"),
    ("weekend gap", "crypto perps trade through weekends, no gap to measure; out of scope by spec"),
    ("footprint and DOM", "order-book / footprint data not stored and not daily; out of scope by spec"),
    ("Elliott", "discretionary wave counting; not pre-registrable; out of scope by spec"),
    ("harmonics", "discretionary pattern fitting; out of scope by spec"),
    ("Gann", "out of scope by spec"),
    ("astro", "out of scope by spec"),
    ("single candlestick patterns", "out of scope by spec"),
    ("dark pools", "equity-market venue data; out of scope by spec"),
    ("max pain", "options open-interest data not stored; out of scope by spec"),
    ("0DTE", "equity-index options; out of scope by spec"),
]

WARNING = (f"This screen tests {N_COMPARISONS} books (18 Tier 1 books + combined book 19). A single SURVIVE is a "
           "multiple-comparison screen hit, not a confirmed edge, not production and not a tradable edge.")
HOLDOUT_TEXT = ("The holdout from 2026-07-15 onward is sealed: no bar at or after 2026-07-15 was loaded, computed, "
                "plotted or cached (parquet rows are filtered before collect; the last daily bar is 2026-07-14).")


def load_run1_verdict() -> dict:
    p = RUN1_DIR / "verdict.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def file_sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _fmt_pf(x):
    return "inf" if isinstance(x, float) and math.isinf(x) else x


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        o = float(o)
        return None if math.isnan(o) else ("inf" if math.isinf(o) else o)
    if isinstance(o, np.integer):
        return int(o)
    return o


def _nt(tier, item, symbol, reason, status="NOT_TESTED"):
    return dict(tier=tier, item=item, symbol=symbol, status=status, reason=reason)


def main() -> int:
    script_sha = file_sha256(SCRIPT_PATH)
    verdict_path = OUT_DIR / "verdict.json"
    if verdict_path.exists():
        print(f"REFUSING TO RUN: {verdict_path} already exists (the screen has been run). Nothing was changed.")
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"script sha256 {script_sha}")

    # ---- smoke the loader on BTC, then load all three
    h_btc = load_hourly("BTCUSDT")
    if h_btc is None:
        raise SystemExit("BTCUSDT 1h cache missing; smoke failed")
    d_btc = daily_from_hourly(h_btc)
    print(f"smoke BTCUSDT: {len(d_btc)} daily bars {d_btc.index[0].date()}..{d_btc.index[-1].date()}")
    daily, hourly, funding = load_all()
    for s, d in daily.items():
        if d.index.max() >= HOLDOUT_START or hourly[s].index.max() >= HOLDOUT_START:
            raise SystemExit(f"holdout breach in {s}")
        if funding[s] is not None and funding[s].index.max() >= HOLDOUT_START:
            raise SystemExit(f"holdout breach in {s} funding")

    not_tested = []
    present = [s for s in SYMBOLS if s in daily]
    for s in SYMBOLS:
        if s not in daily:
            not_tested.append(_nt(1, "daily klines", s, "no local 1h/1d kline series for this symbol"))
    if "BTCUSDT" not in present:
        raise SystemExit("BTCUSDT missing; screen cannot run")
    index = daily["BTCUSDT"].index
    for s in present:
        if not daily[s].index.equals(index):
            raise SystemExit(f"{s} daily index differs from BTCUSDT")
    first_bar = index[0]
    for s in present:
        not_tested.append(_nt(0, "history 2019-01-01..2021-05-31", s,
                              f"no local kline history before {daily[s].index[0].date()} (regime-book Binance USDT-M 1h "
                              "cache starts 2021-06-01; Postgres 1d starts 2022-10). Warm-up from 2019 unavailable; "
                              "train windows start at the first available bar", status="PARTIAL"))
        if funding[s] is None:
            not_tested.append(_nt(1, "funding", s, "no local funding series: funding labeled missing, not charged"))
    not_tested.append(_nt(1, "funding_rates table", "ALL",
                          "Postgres funding_rates has no rows before 2026-10-01 (holdout); funding is charged from the "
                          "regime-book local funding parquet (data.binance.vision + fapi for 2026-07), same settlements",
                          status="INFO"))
    not_tested.append(_nt(1, "book 19 funding/OI veto", "ALL",
                          "open_interest has no rows before 2026-10-01 (holdout); no local OI series covers the test "
                          "windows, so book 19 runs WITHOUT the veto (labeled veto=not_applied)"))
    not_tested.append(_nt(1, "book 19 / Tier 3 realized-vol risk rule", "ALL",
                          "needs 20 + 365 daily bars; before the 1-year window is complete (first known "
                          f"{index[int(np.argmax(~np.isnan(realized_vol_high(daily['BTCUSDT']['close'].to_numpy()))))].date()}) "
                          "risk stays 0.5%", status="PARTIAL"))

    sd_by_sym = {s: SymbolData(s, daily[s], hourly[s], funding[s]) for s in present}
    books_by_sym = {s: build_books(daily[s]) for s in present}
    gates_by_sym = {s: gate_arrays(daily[s]) for s in present}
    scopes = [(name, mem) for name, mem in SCOPES if all(m in present for m in mem)]
    for name, mem in SCOPES:
        if (name, mem) not in scopes:
            not_tested.append(_nt(1, f"scope {name}", ",".join(mem), "member symbol missing locally"))
    funding_label = {name: ("missing" if any(funding[m] is None for m in mem) else "charged") for name, mem in scopes}

    # ---- Tier 1 (+ Tier 3 variants on books 1-18), deterministic order: books, scopes, folds
    summary_rows, train_rows, gate_rows = [], [], []
    trades_by_book, equity_by_book = {}, {}
    all_fills = []
    book_verdicts = {}
    for b in range(1, 20):
        scope_results = {}
        for scope, mem in scopes:
            variants = ["base"] if b == 19 else ["base", "chop", "er", "volsize"]
            per_variant = {}
            for var in variants:
                wrows, wtrades = [], []
                for f in FOLDS:
                    status, note = window_status(books_by_sym, b, mem, f["test_start"], f["test_end"], index)
                    base = dict(book=b, book_name=BOOK_NAMES[b], scope=scope, window=f"F{f['fold']}", fold=f["fold"],
                                test_start=str(f["test_start"].date()), test_end=str(f["test_end"].date()),
                                status=status, status_note=note)
                    if status == "NOT_TESTED":
                        wrows.append({**base, **dict(trades=0, net_pnl=float("nan"), profit_factor=float("nan"),
                                                     max_drawdown=float("nan"), max_drawdown_pct=float("nan"),
                                                     avg_trade=float("nan"), exposure=float("nan"), funding=float("nan"),
                                                     fees=float("nan"), gross_win=float("nan"), gross_loss=float("nan"),
                                                     skipped_lev_cap=0, days=0)})
                        wtrades.append([])
                        if var == "base":
                            not_tested.append(_nt(1, f"book {b} {BOOK_NAMES[b]} F{f['fold']}", scope, note))
                        continue
                    tr, eq, ex, sk = run_variant(sd_by_sym, books_by_sym, gates_by_sym, b, mem,
                                                 f["test_start"], f["test_end"], var)
                    for t in tr:
                        t.update(scope=scope, book=b, book_name=BOOK_NAMES[b], variant=var, fold=f["fold"])
                    wrows.append({**base, **stats(tr, eq, ex, sk)})
                    wtrades.append(tr)
                    if var == "base":
                        all_fills.extend(tr)
                        equity_by_book.setdefault(b, []).extend(
                            dict(book=b, scope=scope, fold=f["fold"], date=str(ts.date()), equity=v) for ts, v in eq.items())
                        if status == "PARTIAL":
                            not_tested.append(_nt(1, f"book {b} {BOOK_NAMES[b]} F{f['fold']}", scope, note, status="PARTIAL"))
                pooled = pooled_row(wrows, wtrades)
                sv, npos = verdict_for_scope(wrows, pooled)
                per_variant[var] = (wrows, pooled, sv, npos, wtrades)
            wrows, pooled, sv, npos, wtrades = per_variant["base"]
            scope_results[scope] = dict(pooled=pooled, verdict=sv, windows_positive=npos, rows=wrows)
            trades_by_book.setdefault(b, []).extend(t for ts in wtrades for t in ts)
            for r in wrows:
                summary_rows.append({**r, "funding_label": funding_label[scope], "windows_positive": "",
                                     "scope_verdict": "", "book_verdict": None,
                                     "veto": "not_applied (OI missing)" if b == 19 else ""})
            summary_rows.append(dict(book=b, book_name=BOOK_NAMES[b], scope=scope, window="POOLED", fold="ALL",
                                     test_start=str(FOLDS[0]["test_start"].date()), test_end=str(FOLDS[-1]["test_end"].date()),
                                     status="POOLED", status_note="test-window trades only; max drawdown = worst window",
                                     **pooled, funding_label=funding_label[scope], windows_positive=npos,
                                     scope_verdict=sv if scope in VERDICT_SCOPES else f"info ({sv})", book_verdict=None,
                                     veto="not_applied (OI missing)" if b == 19 else ""))
            if b <= 18:
                labels = [(f"F{f['fold']}", i) for i, f in enumerate(FOLDS)] + [("POOLED", None)]
                for wl, i in labels:
                    row = dict(book=b, book_name=BOOK_NAMES[b], scope=scope, window=wl)
                    for var in variants:
                        vw, vp, vsv, vnp, _ = per_variant[var]
                        src = vp if i is None else vw[i]
                        row.update({f"{var}_trades": src["trades"], f"{var}_net_pnl": src["net_pnl"],
                                    f"{var}_profit_factor": _fmt_pf(src["profit_factor"]),
                                    f"{var}_max_drawdown": src["max_drawdown"], f"{var}_exposure": src["exposure"]})
                        if i is None:
                            row[f"{var}_screen_rule"] = vsv
                    gate_rows.append(row)
            # train windows: code-run check only
            for f in FOLDS:
                ts_ = max(f["train_start"], first_bar + pd.Timedelta(days=1))
                tr, eq, ex, sk = run_variant(sd_by_sym, books_by_sym, gates_by_sym, b, mem, ts_, f["train_end"], "base")
                st = stats(tr, eq, ex, sk)
                train_rows.append(dict(book=b, book_name=BOOK_NAMES[b], scope=scope, fold=f["fold"],
                                       train_start_requested=str(f["train_start"].date()), train_start_used=str(ts_.date()),
                                       train_end=str(f["train_end"].date()), trades=st["trades"], ran_ok=True,
                                       note="code-run check only; not used in the verdict"))
        passed = all(scope_results[s]["verdict"] == "PASS" for s in VERDICT_SCOPES if s in scope_results)
        has_all = all(s in scope_results for s in VERDICT_SCOPES)
        bv = "SURVIVE" if (passed and has_all) else ("FAIL" if has_all else "NOT_TESTED")
        book_verdicts[b] = dict(name=BOOK_NAMES[b], verdict=bv, scopes={
            s: dict(pooled_net_pnl=r["pooled"]["net_pnl"], pooled_profit_factor=r["pooled"]["profit_factor"],
                    pooled_trades=r["pooled"]["trades"], windows_positive=r["windows_positive"],
                    window_net_pnl={x["window"]: x["net_pnl"] for x in r["rows"]},
                    window_status={x["window"]: x["status"] for x in r["rows"]}, screen_rule=r["verdict"])
            for s, r in scope_results.items()})
    for r in summary_rows:
        r["book_verdict"] = book_verdicts[r["book"]]["verdict"]
    survivors = [b for b in range(1, 20) if book_verdicts[b]["verdict"] == "SURVIVE"]

    # ---- descriptive tiers
    t2_rows, t2_nt = tier2_events(daily, funding, FOLDS)
    t3_bbw = tier3_bbw_events(daily, FOLDS)
    t5_rows, t5_nt = tier5_confirmation(daily, FOLDS)
    fills = pd.DataFrame(all_fills)
    t4_per, t4_sum, t4_nt = tier4_level_context(fills, daily)
    for x in t2_nt + t5_nt + t4_nt:
        not_tested.append(_nt(x["tier"], x["item"], x["symbol"], x["reason"]))
    not_tested.append(_nt(4, "role reversal / golden pocket", "ALL",
                          "reported as a count of exits within 1xATR of the prior 20-day swing level (not a filter)",
                          status="INFO"))

    # ---- write outputs
    def w(name, rows_or_df, cols=None):
        df = rows_or_df if isinstance(rows_or_df, pd.DataFrame) else pd.DataFrame(rows_or_df, columns=cols)
        if "profit_factor" in df.columns:
            df["profit_factor"] = [_fmt_pf(x) for x in df["profit_factor"]]
        df.to_csv(OUT_DIR / name, index=False, lineterminator="\n")

    sum_cols = ["book", "book_name", "scope", "window", "fold", "test_start", "test_end", "status", "status_note",
                "trades", "net_pnl", "profit_factor", "max_drawdown", "max_drawdown_pct", "avg_trade", "exposure",
                "funding", "funding_label", "fees", "gross_win", "gross_loss", "skipped_lev_cap", "days",
                "windows_positive", "scope_verdict", "book_verdict", "veto"]
    w("tier1_summary.csv", summary_rows, sum_cols)
    w("tier1_train_check.csv", train_rows)
    keep = sorted(set(survivors) | {19})
    trade_cols = ["scope", "book", "book_name", "variant", "fold", "symbol", "side", "signal_time", "entry_time",
                  "entry_price", "exit_time", "exit_price", "exit_reason", "qty", "notional", "stop_price", "atr",
                  "risk", "price_pnl", "fees", "funding", "funding_label", "net_pnl"]
    w("tier1_trades.csv", [t for b in keep for t in trades_by_book.get(b, [])], trade_cols)
    w("tier1_equity.csv", [e for b in keep for e in equity_by_book.get(b, [])], ["book", "scope", "fold", "date", "equity"])
    w("tier2_events.csv", t2_rows)
    w("tier3_gate_columns.csv", gate_rows)
    w("tier3_bbw_events.csv", t3_bbw)
    t4_per.to_csv(OUT_DIR / "tier4_level_context.csv", index=False, lineterminator="\n")
    w("tier4_level_summary.csv", t4_sum)
    w("tier5_confirmation.csv", t5_rows)
    w("not_tested.csv", not_tested, ["tier", "item", "symbol", "status", "reason"])
    w("skipped.csv", [dict(group=g, reason=r) for g, r in SKIPPED_GROUPS])

    write_report(script_sha, run_utc, book_verdicts, survivors, not_tested, funding, present)

    verdict = dict(
        script="backend/scripts/daily_direction_shortlist_prereg.py", script_sha256=script_sha, run_utc=run_utc,
        n_comparisons=N_COMPARISONS, warning=WARNING, holdout=HOLDOUT_TEXT,
        survive_rule="pooled test net PnL > 0 and PF > 1.1 and >= 2 of 3 test windows net positive, on BTCUSDT alone "
                     "and on the shared-account three-symbol book separately",
        data=dict(daily_source="UTC-day aggregation of regime-book Binance USDT-M 1h parquet (local)",
                  first_bar=str(first_bar.date()), last_bar=str(index[-1].date()),
                  funding="regime-book funding parquet (charged)", open_interest="missing (book 19 veto not applied)",
                  liquidations="missing"),
        survivors=survivors, books=book_verdicts,
        run_history=dict(this_run=2, run1_dir=str(RUN1_DIR.relative_to(BACKEND)),
                         run1_script_sha256=load_run1_verdict().get("script_sha256"),
                         run1_survivors=load_run1_verdict().get("survivors"), bugs_fixed=RUN1_BUGS,
                         note="run 1 results were seen before run 2; run 2 is a disclosed bug-fix rerun, not blind"))
    verdict_path.write_text(json.dumps(_jsonable(verdict), indent=2), encoding="utf-8")
    print(f"done; survivors: {survivors or 'none'}")
    return 0


def write_report(script_sha, run_utc, book_verdicts, survivors, not_tested, funding, present):
    L = []
    a = L.append
    a("# Daily direction shortlist — pre-registered screen\n")
    a(f"Run {run_utc} · script sha256 `{script_sha}` · research only, not production, no orders.\n")
    a(f"> **Screen warning.** {WARNING}\n")
    a(f"> **Holdout.** {HOLDOUT_TEXT}\n")
    run1 = load_run1_verdict()
    a("## Run history\n")
    a(f"This is run 2. Run 1 (script sha256 `{run1.get('script_sha256', '?')}`, {run1.get('run_utc', '?')}) is kept "
      "untouched in the parent folder. After run 1, two engine bugs were found and fixed with the user's approval; "
      "no rule, parameter, cost or window changed:\n")
    for bug in RUN1_BUGS:
        a(f"- {bug}")
    a("\nRun 1's verdicts are shown beside run 2's below. Run 1 was read before run 2 was made, so run 2 is not a "
      "blind run: treat any verdict that differs between the runs with extra caution.\n")
    a("## Verdicts\n")
    a("| # | Book | BTCUSDT pooled net | BTC PF | BTC +windows | 3SYM pooled net | 3SYM PF | 3SYM +windows | Verdict | Run 1 |")
    a("|---|---|---|---|---|---|---|---|---|---|")
    for b in range(1, 20):
        v = book_verdicts[b]
        bt, s3 = v["scopes"].get("BTCUSDT"), v["scopes"].get("3SYM")

        def cell(x):
            if x is None:
                return "–", "–", "–"
            pf = x["pooled_profit_factor"]
            pfs = "inf" if math.isinf(pf) else ("nan" if math.isnan(pf) else f"{pf:.2f}")
            return f"{x['pooled_net_pnl']:+,.0f}", pfs, f"{x['windows_positive']}/3"
        r1 = run1.get("books", {}).get(str(b), {}).get("verdict", "?")
        a(f"| {b} | {v['name']} | " + " | ".join(cell(bt)) + " | " + " | ".join(cell(s3)) + f" | **{v['verdict']}** | {r1} |")
    a("")
    a(f"Survivors: {', '.join(f'book {b} ({BOOK_NAMES[b]})' for b in survivors) if survivors else 'none'}. "
      "Per-window numbers and the ETH/SOL single-symbol rows (informational) are in tier1_summary.csv. "
      "Train windows only prove the code runs (tier1_train_check.csv).\n")
    a("## Frozen rules\n")
    a("- Closed UTC daily bars only; signal on the close, fill at the next open. Every rolling lookback excludes the "
      "signal bar (indicator values come from bars ≤ t−1; the signal bar contributes only its close). Weekly rules use "
      "completed Monday–Sunday weeks, applied from the following Monday.")
    a("- Stop 2.5 × ATR(14) (Wilder RMA of true range, bars ≤ t−1) from the fill; a gap through the stop fills at "
      "the open; the stop is checked before indicator exits and also on the entry bar.")
    a("- One position per symbol, no pyramiding, no same-bar reversal (a flip exits at the next open and the new side "
      "can fill one bar later). State books (1–4, 6, 11–15, 18) re-enter after a stop if the rule still holds; event "
      "books (5, 7–10, 16, 17, 19) need a fresh trigger. Open positions are closed at the last close of each test window.")
    a("- Donchian entries/exits are close-based versus the prior N-day high/low. Supertrend: ATR(10) Wilder, ×3, flip "
      "when the close crosses the previous bar's final band. Ichimoku cloud at t uses spans from bars ≤ t−26. "
      "Weinstein 'falling' = 30-week SMA below its value 4 weeks earlier. Book 16 triggers when close ≥ the prior "
      "252-close high. Book 17 'history' starts at the first local bar (2021-06-01). Book 9 gates entries only. "
      "Swing pivots are strict over 5 bars each side and usable from the bar after confirmation.")
    a("- Book 19: book 4 side, book 7 trigger in that side, exit on Donchian-10, ATR stop, or loss of side. "
      "Funding/OI veto NOT applied (no local open interest in the test windows). Risk 0.25% when 20-day realized "
      "vol (bars ≤ t−1) is above the 70th percentile of its trailing 365 values, else 0.5%.")
    a("- Tier 3 columns (books 1–18): Choppiness(14) > 61.8 or Kaufman ER(10) < 0.30 force flat (event books then "
      "need a fresh trigger); realized-vol sizing as in book 19. Columns only, no new verdicts.\n")
    a("## Cost model\n")
    a("Each test window starts flat at $10,000 (BTC alone, or one shared account for BTC+ETH+SOL). Risk 0.5% of "
      "realized equity at the fill; skip if notional would exceed 2× equity. Taker fee 0.04% on entry and exit "
      "notional. Adverse slippage 0.05% BTC/ETH, 0.15% SOL on every fill. Funding charged on open positions at each "
      "settlement (rate × qty × 1h open at the settlement), reported separately; intrabar stop exits are stamped "
      "at 12:00 UTC for funding.\n")
    a("## Series present\n")
    for s in present:
        a(f"- {s}: daily bars from the regime-book Binance USDT-M 1h parquet, 2021-06-01..2026-07-14; funding "
          f"{'present' if funding[s] is not None else 'missing'}.")
    a("- Not present for the test windows: open interest, liquidations, futures basis, flows, dominance, Coinbase "
      "premium, MVRV, SOPR, DXY, real yields, Nasdaq, volume-at-price. Fear & Greed exists locally from 2022-10 but "
      "no event rule was pre-registered. History before 2021-06-01 (requested from 2019) does not exist locally.\n")
    a("## Not tested / partial\n")
    for r in not_tested:
        if r["status"] in ("NOT_TESTED", "PARTIAL"):
            a(f"- [{r['status']}] tier {r['tier']} · {r['item']} · {r['symbol']}: {r['reason']}")
    a("\nOut-of-scope groups are listed in skipped.csv.\n")
    (OUT_DIR / "report.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
