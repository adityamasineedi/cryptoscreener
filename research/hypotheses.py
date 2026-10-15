"""Phase 2 hypotheses H1-H8: setup generators (signal side only; fills and exits belong to the Phase 3 simulator).

Each generator takes one coin's bars (common.bars) and returns a DataFrame of setups, one row per signal, with
the signal bar's close time (epoch s) and every order parameter fixed at that moment. All parameters live in P
and were fixed before any outcome was looked at. See research/REPORT.md, "Phase 2".
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import structure as S
from common import epoch_s

P = {
    "atr_n": 14,
    "min_stop_atr4": 0.75, "max_stop_atr4": 3.0,   # stops measured in 4h ATR (H1, H2, H4, H5, H7)
    "min_stop_atr1": 1.5, "max_stop_atr1": 5.0,    # stops measured in 1h ATR (H3, H6, H8)
    "target_R": 2.0,
    "hold_4h_s": 30 * 14400,                       # 5 days
    "H1": {"k": 3, "min_depth": 0.1, "max_depth": 1.5, "max_age": 60, "stop_pad": 0.25},
    "H2": {"theta": 2.5, "zz_pct": 0.03, "lmt_pad": 0.1, "stop_atr": 1.0, "cancel_atr": 0.5, "valid_bars": 12},
    "H3": {"theta": 2.5, "lmt_pad": 0.1, "stop_pad": 0.25, "valid_bars": 8, "hold_s": 48 * 3600},
    "H4": {"impulse": 2.0, "base_max": 0.7, "close_frac": 0.7, "stop_pad": 0.25, "valid_bars": 60},
    "H5": {"n": 12, "atr_long": 100, "max_width": 3.0, "cooldown": 12},
    "H6": {"n": 48, "max_width": 7.0, "poke": 0.1, "stop_pad": 0.25, "min_reward_R": 1.0, "hold_s": 24 * 3600},
    "H7": {"theta": 2.5, "fib_min": 0.382, "fib_dead": 0.786, "stop_pad": 0.5,
           "window_s": 120 * 3600},
    "H8": {"range_atr": 2.0, "rv_n": 168, "rv_min": 2.5, "donch": 24, "close_frac": 0.75, "stop_pad": 0.1,
           "hold_s": 24 * 3600},
}

COLS = ["hyp", "sig_t", "side", "entry", "px", "valid_s", "cancel_px", "stop", "tgt_kind", "tgt", "hold_s",
        "exec_tf", "stop_tf", "atr"]


def _arr(df: pd.DataFrame) -> dict[str, np.ndarray]:
    return {"t": epoch_s(df["time"]), "o": df["open"].to_numpy(float), "h": df["high"].to_numpy(float),
            "l": df["low"].to_numpy(float), "c": df["close"].to_numpy(float), "v": df["volume"].to_numpy(float)}


def _clamp_stop(side: int, ref: float, stop: float, a: float, lo: float, hi: float) -> float | None:
    """Widen the stop to at least lo*ATR from ref; return None when it is wider than hi*ATR."""
    d = side * (ref - stop)
    if d < lo * a:
        stop = ref - side * lo * a
    elif d > hi * a:
        return None
    return float(stop)


def _row(hyp, sig_t, side, entry, px, valid_s, cancel_px, stop, tgt_kind, tgt, hold_s, exec_tf, stop_tf, a):
    return dict(zip(COLS, (hyp, int(sig_t), int(side), entry, px, valid_s, cancel_px, stop, tgt_kind, tgt, hold_s,
                           exec_tf, stop_tf, a)))


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=COLS)


# ------------------------------------------------------------------ H1 4h sweep + reclaim (fractal swings)
def h1(b: dict) -> pd.DataFrame:
    p, x = P["H1"], _arr(b["4h"])
    a = S.atr(x["h"], x["l"], x["c"], P["atr_n"])
    st = S.fractal_swings(x["h"], x["l"], p["k"])
    sw = S.sweeps(x["h"], x["l"], x["c"], st, a, p["min_depth"], p["max_depth"], p["max_age"])
    rows = []
    for t in np.flatnonzero(sw["side"]):
        s = int(sw["side"][t])
        stop = _clamp_stop(s, x["c"][t], sw["extreme"][t] - s * p["stop_pad"] * a[t], a[t],
                           P["min_stop_atr4"], P["max_stop_atr4"])
        if stop is None:
            continue
        rows.append(_row("H1", x["t"][t] + 14400, s, "mkt", np.nan, 0, np.nan, stop, "R", P["target_R"],
                         P["hold_4h_s"], "1h", "4h", a[t]))
    return _frame(rows)


# ------------------------------------------------------------------ H2 4h BOS + first retest (DC swings)
def h2(b: dict, method: str = "dc") -> pd.DataFrame:
    """method: 'dc' (ATR directional change, baseline), 'zz' (fixed-% zigzag), 'fractal' (k=3) - Phase 3 comparison."""
    p, x = P["H2"], _arr(b["4h"])
    a = S.atr(x["h"], x["l"], x["c"], P["atr_n"])
    if method == "dc":
        st = S.dc_swings(x["h"], x["l"], p["theta"] * a)
    elif method == "zz":
        st = S.dc_swings(x["h"], x["l"], np.where(np.isnan(a), np.nan, p["zz_pct"] * x["c"]))
    else:
        st = S.fractal_swings(x["h"], x["l"], 3)
    br = S.breaks(x["c"], st)
    rows = []
    for t in np.flatnonzero(br["bos"]):
        s, lv, at = int(br["bos"][t]), br["level"][t], a[t]
        if np.isnan(at):
            continue
        lmt = lv + s * p["lmt_pad"] * at
        stop = lv - s * p["stop_atr"] * at
        stop = _clamp_stop(s, lmt, stop, at, P["min_stop_atr4"], P["max_stop_atr4"])
        if stop is None:
            continue
        marketable = s * (x["c"][t] - lmt) <= 0
        rows.append(_row("H2", x["t"][t] + 14400, s, "mkt" if marketable else "lmt", np.nan if marketable else lmt,
                         p["valid_bars"] * 14400, lv - s * p["cancel_atr"] * at, stop, "R", P["target_R"],
                         P["hold_4h_s"], "1h", "4h", at))
    return _frame(rows)


# ------------------------------------------------------------------ H3 1h CHoCH + retest (DC swings), 15m fills
def h3(b: dict) -> pd.DataFrame:
    p, x = P["H3"], _arr(b["1h"])
    a = S.atr(x["h"], x["l"], x["c"], P["atr_n"])
    st = S.dc_swings(x["h"], x["l"], p["theta"] * a)
    br = S.breaks(x["c"], st)
    rows = []
    for t in np.flatnonzero(br["choch"]):
        s, lv, at = int(br["choch"][t]), br["level"][t], a[t]
        piv = st["sh_piv"][t - 1] if s > 0 else st["sl_piv"][t - 1]
        if np.isnan(at) or np.isnan(piv):
            continue
        piv = int(piv)
        ext = x["l"][piv:t + 1].min() if s > 0 else x["h"][piv:t + 1].max()
        lmt = lv + s * p["lmt_pad"] * at
        stop = _clamp_stop(s, lmt, ext - s * p["stop_pad"] * at, at, P["min_stop_atr1"], P["max_stop_atr1"])
        if stop is None:
            continue
        marketable = s * (x["c"][t] - lmt) <= 0
        rows.append(_row("H3", x["t"][t] + 3600, s, "mkt" if marketable else "lmt", np.nan if marketable else lmt,
                         p["valid_bars"] * 3600, np.nan, stop, "R", P["target_R"], p["hold_s"], "15m", "1h", at))
    return _frame(rows)


# ------------------------------------------------------------------ H4 4h supply/demand zone first retest
def h4(b: dict) -> pd.DataFrame:
    p, x = P["H4"], _arr(b["4h"])
    a = S.atr(x["h"], x["l"], x["c"], P["atr_n"])
    z = S.sd_zones(x["o"], x["h"], x["l"], x["c"], a, p["impulse"], p["base_max"], p["close_frac"])
    rows = []
    for t in np.flatnonzero(z["side"]):
        s, at = int(z["side"][t]), a[t]
        prox, dist = (z["top"][t], z["bot"][t]) if s > 0 else (z["bot"][t], z["top"][t])
        stop = _clamp_stop(s, prox, dist - s * p["stop_pad"] * at, at, P["min_stop_atr4"], P["max_stop_atr4"])
        if stop is None:
            continue
        rows.append(_row("H4", x["t"][t] + 14400, s, "lmt", prox, p["valid_bars"] * 14400, dist, stop, "R",
                         P["target_R"], P["hold_4h_s"], "1h", "4h", at))
    return _frame(rows)


# ------------------------------------------------------------------ H5 4h compression breakout
def h5(b: dict) -> pd.DataFrame:
    p, x = P["H5"], _arr(b["4h"])
    a = S.atr(x["h"], x["l"], x["c"], P["atr_n"])
    al = S.atr(x["h"], x["l"], x["c"], p["atr_long"])
    cb = S.compression_breakout(x["h"], x["l"], x["c"], al, p["n"], p["max_width"], p["cooldown"])
    rows = []
    for t in np.flatnonzero(cb["side"]):
        s, at = int(cb["side"][t]), a[t]
        stop = _clamp_stop(s, x["c"][t], (cb["top"][t] + cb["bot"][t]) / 2, at, P["min_stop_atr4"],
                           P["max_stop_atr4"])
        if stop is None:
            continue
        rows.append(_row("H5", x["t"][t] + 14400, s, "mkt", np.nan, 0, np.nan, stop, "R", P["target_R"],
                         P["hold_4h_s"], "1h", "4h", at))
    return _frame(rows)


# ------------------------------------------------------------------ H6 1h failed breakout -> range re-entry
def h6(b: dict) -> pd.DataFrame:
    p, x = P["H6"], _arr(b["1h"])
    a = S.atr(x["h"], x["l"], x["c"], P["atr_n"])
    fb = S.failed_breakouts(x["h"], x["l"], x["c"], a, p["n"], p["max_width"], p["poke"])
    rows = []
    for t in np.flatnonzero(fb["side"]):
        s, at, c = int(fb["side"][t]), a[t], x["c"][t]
        stop = _clamp_stop(s, c, fb["extreme"][t] - s * p["stop_pad"] * at, at, P["min_stop_atr1"],
                           P["max_stop_atr1"])
        if stop is None:
            continue
        mid = (fb["rh"][t] + fb["rl"][t]) / 2
        if s * (mid - c) < p["min_reward_R"] * s * (c - stop):
            continue
        rows.append(_row("H6", x["t"][t] + 3600, s, "mkt", np.nan, 0, np.nan, stop, "px", mid, p["hold_s"], "15m",
                         "1h", at))
    return _frame(rows)


# ------------------------------------------------------------------ H7 4h trend, controlled retracement, 1h trigger
def h7(b: dict) -> pd.DataFrame:
    p, x4, x1 = P["H7"], _arr(b["4h"]), _arr(b["1h"])
    a4 = S.atr(x4["h"], x4["l"], x4["c"], P["atr_n"])
    st = S.dc_swings(x4["h"], x4["l"], p["theta"] * a4)
    m = {}
    piv_t = {}
    for k in ("sh", "sl", "trend", "last_kind"):
        m[k] = S.asof_map(x1["t"], 3600, x4["t"], 14400, st[k])
    m["atr4"] = S.asof_map(x1["t"], 3600, x4["t"], 14400, a4)
    for k in ("sh", "sl"):
        piv = st[f"{k}_piv"]
        conf = st[f"{k}_conf"]
        ok = ~np.isnan(piv)
        pt = np.full(len(piv), np.nan)
        ct = np.full(len(piv), np.nan)
        pt[ok] = x4["t"][piv[ok].astype(int)]
        ct[ok] = x4["t"][conf[ok].astype(int)] + 14400
        piv_t[k] = S.asof_map(x1["t"], 3600, x4["t"], 14400, pt)
        m[f"{k}_conf_t"] = S.asof_map(x1["t"], 3600, x4["t"], 14400, ct)
    rows = []
    fired: set = set()
    for t in range(1, len(x1["c"])):
        if np.isnan(m["last_kind"][t]) or np.isnan(m["atr4"][t]):   # no closed 4h bar mapped yet
            continue
        s = int(m["last_kind"][t])
        if s == 0 or int(m["trend"][t]) != s:
            continue
        ext, opp = (m["sh"][t], m["sl"][t]) if s > 0 else (m["sl"][t], m["sh"][t])
        conf_t = m["sh_conf_t"][t] if s > 0 else m["sl_conf_t"][t]
        pv_t = piv_t["sh"][t] if s > 0 else piv_t["sl"][t]
        key = (s, conf_t)
        if key in fired or x1["t"][t] + 3600 - conf_t > p["window_s"]:
            continue
        leg = s * (ext - opp)
        if not leg > 0:
            continue
        i0 = int(np.searchsorted(x1["t"], pv_t))
        seg_l = x1["l"][i0:t + 1] if s > 0 else x1["h"][i0:t + 1]
        deepest = seg_l.min() if s > 0 else seg_l.max()
        closes = x1["c"][i0:t + 1]
        dead_lvl = ext - s * p["fib_dead"] * leg
        if (s > 0 and closes.min() < dead_lvl) or (s < 0 and closes.max() > dead_lvl):
            fired.add(key)   # invalidated: no entry on this swing
            continue
        retr = s * (ext - deepest) / leg
        trig = x1["c"][t] > x1["h"][t - 1] if s > 0 else x1["c"][t] < x1["l"][t - 1]
        if not (p["fib_min"] <= retr and trig and s * (ext - x1["c"][t]) > 0):
            continue
        if x1["t"][t] < conf_t:   # trigger bar must open after the swing is known
            continue
        a = m["atr4"][t]
        stop = _clamp_stop(s, x1["c"][t], deepest - s * p["stop_pad"] * a, a, P["min_stop_atr4"], P["max_stop_atr4"])
        fired.add(key)
        if stop is None:
            continue
        rows.append(_row("H7", x1["t"][t] + 3600, s, "mkt", np.nan, 0, np.nan, stop, "R", P["target_R"],
                         P["hold_4h_s"], "1h", "4h", a))
    return _frame(rows)


# ------------------------------------------------------------------ H8 1h volume-supported expansion
def h8(b: dict) -> pd.DataFrame:
    p, x = P["H8"], _arr(b["1h"])
    a = S.atr(x["h"], x["l"], x["c"], P["atr_n"])
    rv = S.rel_volume(x["v"], p["rv_n"])
    dh, dl = S.donchian_prior(x["h"], x["l"], p["donch"])
    rows = []
    for t in range(1, len(x["c"])):
        at = a[t - 1]
        if np.isnan(at) or np.isnan(rv[t]) or np.isnan(dh[t]):
            continue
        h, l, c = x["h"][t], x["l"][t], x["c"][t]
        rng = h - l
        if rng < p["range_atr"] * at or rv[t] < p["rv_min"]:
            continue
        if c > dh[t] and c >= l + p["close_frac"] * rng:
            s, ext = 1, l
        elif c < dl[t] and c <= h - p["close_frac"] * rng:
            s, ext = -1, h
        else:
            continue
        stop = _clamp_stop(s, c, ext - s * p["stop_pad"] * a[t], a[t], P["min_stop_atr1"], P["max_stop_atr1"])
        if stop is None:
            continue
        rows.append(_row("H8", x["t"][t] + 3600, s, "mkt", np.nan, 0, np.nan, stop, "R", P["target_R"], p["hold_s"],
                         "15m", "1h", a[t]))
    return _frame(rows)


ALL = {"H1": h1, "H2": h2, "H3": h3, "H4": h4, "H5": h5, "H6": h6, "H7": h7, "H8": h8}
