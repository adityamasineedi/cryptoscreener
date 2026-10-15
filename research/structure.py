"""Causal market-structure detectors (Phase 2).

Contract: every function returns per-bar numpy arrays aligned to its input; the value at index t is the state
known at the CLOSE of bar t and may use bars 0..t only. research/p2_lookahead.py verifies this by truncation.
Swing outputs carry both the pivot bar index and the bar on which the pivot became known (confirmation).
"""
from __future__ import annotations

import numpy as np

NAN = np.nan


# ---------------------------------------------------------------- volatility / volume
def atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    """Wilder ATR."""
    m = len(c)
    out = np.full(m, NAN)
    if m < n:
        return out
    tr = np.empty(m)
    tr[0] = h[0] - l[0]
    tr[1:] = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    a = tr[:n].mean()
    out[n - 1] = a
    for i in range(n, m):
        a += (tr[i] - a) / n
        out[i] = a
    return out


def rel_volume(v: np.ndarray, n: int = 168) -> np.ndarray:
    """Volume of bar t divided by the median volume of the previous n bars (bar t excluded)."""
    m = len(v)
    out = np.full(m, NAN)
    for i in range(n, m):
        med = np.median(v[i - n:i])
        out[i] = v[i] / med if med > 0 else NAN
    return out


def donchian_prior(h: np.ndarray, l: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Highest high / lowest low of bars t-n..t-1 (bar t excluded)."""
    m = len(h)
    hi = np.full(m, NAN)
    lo = np.full(m, NAN)
    for i in range(n, m):
        hi[i] = h[i - n:i].max()
        lo[i] = l[i - n:i].min()
    return hi, lo


# ---------------------------------------------------------------- swing state helpers
def _swing_state(m: int, events: list[tuple[int, int, float, int]]) -> dict[str, np.ndarray]:
    """events: (confirm_bar, pivot_bar, price, kind +1 high / -1 low). Returns the last two confirmed highs and
    lows known at each bar, their pivot/confirm bars, the structural trend and the last confirmed swing kind."""
    keys = ("sh", "sh_prev", "sl", "sl_prev", "sh_piv", "sl_piv", "sh_conf", "sl_conf")
    st = {k: np.full(m, NAN) for k in keys}
    st["trend"] = np.zeros(m)
    st["last_kind"] = np.zeros(m)
    events = sorted(events)
    cur = {k: NAN for k in keys}
    last_kind = 0
    j = 0
    for t in range(m):
        while j < len(events) and events[j][0] == t:
            conf, piv, px, kind = events[j]
            if kind > 0:
                cur["sh_prev"], cur["sh"], cur["sh_piv"], cur["sh_conf"] = cur["sh"], px, piv, conf
            else:
                cur["sl_prev"], cur["sl"], cur["sl_piv"], cur["sl_conf"] = cur["sl"], px, piv, conf
            last_kind = kind
            j += 1
        for k in keys:
            st[k][t] = cur[k]
        if not np.isnan(cur["sh_prev"]) and not np.isnan(cur["sl_prev"]):
            if cur["sh"] > cur["sh_prev"] and cur["sl"] > cur["sl_prev"]:
                st["trend"][t] = 1
            elif cur["sh"] < cur["sh_prev"] and cur["sl"] < cur["sl_prev"]:
                st["trend"][t] = -1
        st["last_kind"][t] = last_kind
    return st


# ---------------------------------------------------------------- method 1: fractal swings
def fractal_swings(h: np.ndarray, l: np.ndarray, k: int = 3) -> dict[str, np.ndarray]:
    """Pivot high at i if h[i] > max(h[i-k:i]) and h[i] >= max(h[i+1:i+k+1]); known at the close of bar i+k."""
    m = len(h)
    ev = []
    for i in range(k, m - k):
        if h[i] > h[i - k:i].max() and h[i] >= h[i + 1:i + k + 1].max():
            ev.append((i + k, i, float(h[i]), 1))
        if l[i] < l[i - k:i].min() and l[i] <= l[i + 1:i + k + 1].min():
            ev.append((i + k, i, float(l[i]), -1))
    return _swing_state(m, ev)


# ---------------------------------------------------------------- methods 2/3: directional change / zigzag
def dc_swings(h: np.ndarray, l: np.ndarray, thr: np.ndarray) -> dict[str, np.ndarray]:
    """Directional change. While tracking a high, a swing high is confirmed on the first bar whose low is at
    least thr[t] below the running high (and vice versa). thr = theta*ATR gives the ATR-normalised method;
    thr = pct*close gives a causal percentage zigzag. Bars with thr NaN only update the running extremes."""
    m = len(h)
    ev = []
    mode = 0
    hi, hi_i, lo, lo_i = h[0], 0, l[0], 0
    for t in range(m):
        if mode >= 0 and h[t] > hi:
            hi, hi_i = h[t], t
        if mode <= 0 and l[t] < lo:
            lo, lo_i = l[t], t
        th = thr[t]
        if np.isnan(th):
            continue
        if mode == 0:
            if h[t] - lo >= th and lo_i < t:
                ev.append((t, lo_i, float(lo), -1)); mode = 1; hi, hi_i = h[t], t
            elif hi - l[t] >= th and hi_i < t:
                ev.append((t, hi_i, float(hi), 1)); mode = -1; lo, lo_i = l[t], t
        elif mode == 1:
            if hi - l[t] >= th:
                ev.append((t, hi_i, float(hi), 1)); mode = -1; lo, lo_i = l[t], t
        else:
            if h[t] - lo >= th:
                ev.append((t, lo_i, float(lo), -1)); mode = 1; hi, hi_i = h[t], t
    return _swing_state(m, ev)


# ---------------------------------------------------------------- methods 4/5: BOS and CHoCH
def breaks(c: np.ndarray, st: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """First close beyond the last confirmed swing level (levels as known at t-1; each level breaks once).
    BOS = break with prior trend not against it; CHoCH = break against a confirmed opposite trend.
    Outputs +1/-1 at the break bar and the broken level."""
    m = len(c)
    bos = np.zeros(m)
    choch = np.zeros(m)
    level = np.full(m, NAN)
    used_hi = used_lo = NAN
    for t in range(1, m):
        sh, sl, tr = st["sh"][t - 1], st["sl"][t - 1], st["trend"][t - 1]
        if not np.isnan(sh) and c[t] > sh and sh != used_hi:
            used_hi = sh
            (choch if tr == -1 else bos)[t] = 1
            level[t] = sh
        elif not np.isnan(sl) and c[t] < sl and sl != used_lo:
            used_lo = sl
            (choch if tr == 1 else bos)[t] = -1
            level[t] = sl
    return {"bos": bos, "choch": choch, "level": level}


# ---------------------------------------------------------------- method 6: liquidity sweep + reclaim
def sweeps(h: np.ndarray, l: np.ndarray, c: np.ndarray, st: dict[str, np.ndarray], a: np.ndarray,
           min_depth: float = 0.1, max_depth: float = 1.5, max_age: int = 60) -> dict[str, np.ndarray]:
    """Bar t trades through the last confirmed swing low (known at t-1) by >= min_depth*ATR and <= max_depth*ATR
    and closes back above it -> +1 (bullish sweep). Mirror for highs -> -1. A level counts once: any earlier
    trade through it since confirmation consumes it. Outputs side, swept level and sweep extreme."""
    m = len(c)
    side = np.zeros(m)
    lvl = np.full(m, NAN)
    ext = np.full(m, NAN)
    lo_gone = hi_gone = NAN
    for t in range(1, m):
        sl, sh = st["sl"][t - 1], st["sh"][t - 1]
        at = a[t]
        if np.isnan(at):
            continue
        if not np.isnan(sl) and sl != lo_gone and l[t] < sl:
            lo_gone = sl
            d = sl - l[t]
            if (min_depth * at <= d <= max_depth * at and c[t] > sl and t - st["sl_piv"][t - 1] <= max_age):
                side[t], lvl[t], ext[t] = 1, sl, l[t]
                continue
        if not np.isnan(sh) and sh != hi_gone and h[t] > sh:
            hi_gone = sh
            d = h[t] - sh
            if (min_depth * at <= d <= max_depth * at and c[t] < sh and t - st["sh_piv"][t - 1] <= max_age):
                side[t], lvl[t], ext[t] = -1, sh, h[t]
    return {"side": side, "level": lvl, "extreme": ext}


# ---------------------------------------------------------------- method 7: ranges and compression
def compression_breakout(h: np.ndarray, l: np.ndarray, c: np.ndarray, a_long: np.ndarray, n: int = 12,
                         max_width: float = 3.0, cooldown: int = 12) -> dict[str, np.ndarray]:
    """Box = bars t-n..t-1. Compressed if box width <= max_width * a_long[t-1]. Close beyond the box -> +1/-1.
    No new event for `cooldown` bars after an event."""
    m = len(c)
    side = np.zeros(m)
    top = np.full(m, NAN)
    bot = np.full(m, NAN)
    last = -10**9
    for t in range(n, m):
        if t - last <= cooldown or np.isnan(a_long[t - 1]):
            continue
        hi, lo = h[t - n:t].max(), l[t - n:t].min()
        if hi - lo > max_width * a_long[t - 1]:
            continue
        if c[t] > hi:
            side[t] = 1
        elif c[t] < lo:
            side[t] = -1
        else:
            continue
        top[t], bot[t], last = hi, lo, t
    return {"side": side, "top": top, "bot": bot}


def failed_breakouts(h: np.ndarray, l: np.ndarray, c: np.ndarray, a: np.ndarray, n: int = 48,
                     max_width: float = 7.0, poke: float = 0.1) -> dict[str, np.ndarray]:
    """Range = bars t-n..t-1 (Donchian, bar t excluded), qualifying if width <= max_width*ATR[t-1].
    Short (-1): the high of bar t (or of bar t-1 if t-1 closed above the range) exceeds the range high by
    >= poke*ATR and bar t closes back inside. Long (+1) mirrors at the range low. Outputs side, range high/low,
    and the breakout extreme."""
    m = len(c)
    side = np.zeros(m)
    rh_o = np.full(m, NAN)
    rl_o = np.full(m, NAN)
    ext = np.full(m, NAN)
    for t in range(n + 1, m):
        at = a[t - 1]
        if np.isnan(at):
            continue
        # use the range that existed before the breakout attempt started
        start = t - 1 if (c[t - 1] > h[t - 1 - n:t - 1].max() or c[t - 1] < l[t - 1 - n:t - 1].min()) else t
        rh, rl = h[start - n:start].max(), l[start - n:start].min()
        if rh - rl > max_width * at:
            continue
        xh = h[start:t + 1].max()
        xl = l[start:t + 1].min()
        if xh >= rh + poke * at and rl < c[t] < rh:
            side[t], ext[t] = -1, xh
        elif xl <= rl - poke * at and rl < c[t] < rh:
            side[t], ext[t] = 1, xl
        else:
            continue
        rh_o[t], rl_o[t] = rh, rl
    return {"side": side, "rh": rh_o, "rl": rl_o, "extreme": ext}


# ---------------------------------------------------------------- supply / demand zones
def sd_zones(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, a: np.ndarray,
             impulse: float = 2.0, base_max: float = 0.7, close_frac: float = 0.7) -> dict[str, np.ndarray]:
    """Zone created at the close of impulse bar t: range >= impulse*ATR[t-1], close in the outer
    (1-close_frac) of its range, close beyond every base bar's extreme. Base = the 1-3 bars directly before t
    whose ranges are each <= base_max*ATR[t-1]. Demand zone (+1) = [min base low, max base body top];
    supply zone (-1) = [min base body bottom, max base high]."""
    m = len(c)
    side = np.zeros(m)
    ztop = np.full(m, NAN)
    zbot = np.full(m, NAN)
    for t in range(4, m):
        at = a[t - 1]
        if np.isnan(at):
            continue
        rng = h[t] - l[t]
        if rng < impulse * at:
            continue
        base = []
        for j in (t - 1, t - 2, t - 3):
            if h[j] - l[j] <= base_max * at:
                base.append(j)
            else:
                break
        if not base:
            continue
        bh, bl = max(h[j] for j in base), min(l[j] for j in base)
        body_top = max(max(o[j], c[j]) for j in base)
        body_bot = min(min(o[j], c[j]) for j in base)
        if c[t] >= l[t] + close_frac * rng and c[t] > bh:
            side[t], ztop[t], zbot[t] = 1, body_top, bl
        elif c[t] <= h[t] - close_frac * rng and c[t] < bl:
            side[t], ztop[t], zbot[t] = -1, bh, body_bot
    return {"side": side, "top": ztop, "bot": zbot}


# ---------------------------------------------------------------- regime
def daily_z(c_daily: np.ndarray, look: int = 30, vol_n: int = 90) -> np.ndarray:
    """Phase 1 regime score on closed daily bars: ln(C_t/C_{t-look}) / (sd(daily log ret, vol_n) * sqrt(look))."""
    m = len(c_daily)
    z = np.full(m, NAN)
    r = np.diff(np.log(c_daily), prepend=np.nan)
    for t in range(max(look, vol_n), m):
        sd = np.std(r[t - vol_n + 1:t + 1], ddof=1)
        z[t] = np.log(c_daily[t] / c_daily[t - look]) / (sd * np.sqrt(look))
    return z


# ---------------------------------------------------------------- higher-timeframe mapping
def asof_map(lo_open: np.ndarray, lo_sec: int, hi_open: np.ndarray, hi_sec: int, values: np.ndarray) -> np.ndarray:
    """For each lower-TF bar, the higher-TF value from the last higher-TF bar that CLOSED at or before the
    lower-TF bar's close. Inputs are epoch seconds of bar opens."""
    k = np.searchsorted(hi_open + hi_sec, lo_open + lo_sec, side="right") - 1
    out = np.full(len(lo_open), NAN)
    ok = k >= 0
    out[ok] = values[k[ok]]
    return out
