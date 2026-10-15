"""Phase 3+ backtest engine: resolves setups into trades (C3-C6) and runs one book per hypothesis (C7-C9).

Fill and exit rules reproduce backend/scripts/sleeve_study/sim.py (market entry at the next exec-bar open plus
adverse slippage; stop before target in the same bar; gap fills at the open; time exit at the exec-bar close),
and add what sim.py lacks: limit entries with expiry and close-based cancel (C4), price-level targets (H6), and
15m execution for 1h-stop setups. research/p3_parity.py checks market-entry trades against sim.py.

Trade R is net of fees, slippage and flat funding:  R = [side*(exit-fill) - fee*(fill+exit) - fund*fill*n] / |fill-stop|.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from common import FUNDING_8H, SLIP, TAKER, TF_SEC, epoch_s

FUND_SEC = 8 * 3600


@dataclass
class Exec:
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    sec: int

    @classmethod
    def of(cls, df: pd.DataFrame, tf: str) -> "Exec":
        return cls(epoch_s(df["time"]), *(df[k].to_numpy(float) for k in ("open", "high", "low", "close")), TF_SEC[tf])


def _first(mask: np.ndarray) -> int:
    """Index of the first True, or -1."""
    if not mask.size:
        return -1
    i = int(np.argmax(mask))
    return i if mask[i] else -1


def resolve(s: dict, ex: Exec, cancel_t: np.ndarray | None = None, cancel_c: np.ndarray | None = None,
            cancel_sec: int = 14400, slip: float = SLIP, taker: float = TAKER, fund: float = FUNDING_8H) -> dict | None:
    """Resolve one setup. Returns a trade dict, or a dict with 'skip' reason. cancel_t/cancel_c are the stop-TF
    bar opens/closes used for the C4 close-based cancel."""
    side, stop = int(s["side"]), float(s["stop"])
    sig = int(s["sig_t"])
    i0 = int(np.searchsorted(ex.t, sig))
    if i0 >= len(ex.t) or ex.t[i0] - sig > 4 * ex.sec:
        return {"skip": "no_exec_bar"}
    target_on_fill_bar = True
    if s["entry"] == "mkt":
        fi = i0
        raw = ex.o[fi]
        if side * (raw - stop) <= 0:
            return {"skip": "open_through_stop"}
    else:
        px = float(s["px"])
        i1 = int(np.searchsorted(ex.t, sig + int(s["valid_s"])))
        seg_l = ex.l[i0:i1] if side > 0 else ex.h[i0:i1]
        if s.get("fill_strict"):   # maker variant: price must trade through the limit
            hit = _first(seg_l < px) if side > 0 else _first(seg_l > px)
        else:
            hit = _first(seg_l <= px) if side > 0 else _first(seg_l >= px)
        if hit < 0:
            return {"skip": "not_filled"}
        fi = i0 + hit
        cpx = s.get("cancel_px")
        if cancel_t is not None and cpx is not None and not (isinstance(cpx, float) and math.isnan(cpx)):
            # stop-TF bars closing after the signal and no later than the fill bar's open
            k0 = int(np.searchsorted(cancel_t + cancel_sec, sig, side="right"))
            k1 = int(np.searchsorted(cancel_t + cancel_sec, ex.t[fi], side="right"))
            if k1 > k0 and (side * (cancel_c[k0:k1] - float(cpx)) < 0).any():
                return {"skip": "cancelled"}
        raw = min(ex.o[fi], px) if side > 0 else max(ex.o[fi], px)
        target_on_fill_bar = False
        if side * (raw - stop) <= 0:
            return {"skip": "fill_through_stop"}
    e_slip = s.get("entry_slip", slip)
    e_slip = slip if e_slip is None or (isinstance(e_slip, float) and math.isnan(e_slip)) else float(e_slip)
    e_fee = s.get("entry_fee", taker)
    e_fee = taker if e_fee is None or (isinstance(e_fee, float) and math.isnan(e_fee)) else float(e_fee)
    fill = raw * (1 + side * e_slip)
    rpu = side * (fill - stop)
    if rpu <= 0:
        return {"skip": "slip_through_stop"}
    if s["tgt_kind"] == "R":
        tgt = fill + side * float(s["tgt"]) * rpu
    else:
        tgt = float(s["tgt"])
        if side * (tgt - fill) <= 0:
            return {"skip": "fill_through_target"}
    entry_t = int(ex.t[fi])
    # bars from the fill bar until the time exit (inclusive)
    jt = int(np.searchsorted(ex.t, entry_t + int(s["hold_s"]) - ex.sec))
    last = min(jt, len(ex.t) - 1)
    o, h, l, c = ex.o[fi:last + 1], ex.h[fi:last + 1], ex.l[fi:last + 1], ex.c[fi:last + 1]
    n = len(o)
    first = np.zeros(n, bool)
    first[0] = True
    if side > 0:
        stop_gap = ~first & (o <= stop)
        stop_hit = l <= stop
        tgt_gap = ~first & (o >= tgt)
        tgt_hit = h >= tgt
    else:
        stop_gap = ~first & (o >= stop)
        stop_hit = h >= stop
        tgt_gap = ~first & (o <= tgt)
        tgt_hit = l <= tgt
    if not target_on_fill_bar:
        tgt_hit = tgt_hit & ~first
    js, jg = _first(stop_hit | stop_gap), _first(tgt_hit | tgt_gap)
    if js >= 0 and (jg < 0 or js <= jg):
        j = js
        px_raw, why = (o[j], "stop_gap") if stop_gap[j] else (stop, "stop")
    elif jg >= 0:
        j = jg
        px_raw, why = (o[j], "target_gap") if tgt_gap[j] else (tgt, "target")
    else:
        j = n - 1
        px_raw = c[j]
        why = "time" if jt <= len(ex.t) - 1 else "data_end"
    exit_px = px_raw * (1 - side * slip)
    exit_t = int(ex.t[fi + j]) + ex.sec
    nfund = exit_t // FUND_SEC - entry_t // FUND_SEC
    gross = side * (exit_px - fill)
    fees = e_fee * fill + taker * exit_px
    funding = fund * fill * nfund
    return {"entry_t": entry_t, "exit_t": exit_t, "fill": fill, "stop": stop, "tgt": tgt, "exit_px": exit_px,
            "why": why, "rpu": rpu, "R": (gross - fees - funding) / rpu, "R_gross": gross / rpu,
            "cost_R": (fees + funding) / rpu, "stop_pct": rpu / fill, "tgt_R": side * (tgt - fill) / rpu}


def resolve_all(setups: pd.DataFrame, ex: Exec, cancel_t=None, cancel_c=None) -> pd.DataFrame:
    rows = []
    for s in setups.to_dict("records"):
        r = resolve(s, ex, cancel_t, cancel_c)
        rows.append({**s, **r})
    return pd.DataFrame(rows)


def purge(setups: pd.DataFrame, seg: tuple) -> pd.DataFrame:
    """C9: signals inside the segment whose waiting window plus holding time ends inside it."""
    a, b = int(seg[0].timestamp()), int(seg[1].timestamp())
    end = setups["sig_t"] + setups["valid_s"].fillna(0) + setups["hold_s"]
    return setups[(setups["sig_t"] >= a) & (end <= b)]


def conflicts(setups: pd.DataFrame) -> pd.DataFrame:
    """C8: drop both sides when a long and a short fire on the same coin at the same close."""
    g = setups.groupby(["coin", "sig_t"])["side"].transform(lambda x: x.nunique())
    return setups[g == 1]


def book(trades: pd.DataFrame, equity0: float = 10_000.0, risk: float = 0.005, lev: float = 3.0,
         cap: float = 0.02) -> pd.DataFrame:
    """C7 portfolio: chronological; a trade is taken when its coin has no open position at signal and entry
    time and total open initial risk stays <= cap*equity. Risk = risk*realised equity; notional <= lev*equity."""
    t = trades.dropna(subset=["entry_t"]).sort_values(["entry_t", "coin"]).reset_index(drop=True)
    eq = equity0
    open_pos: list[dict] = []
    taken = []
    for r in t.to_dict("records"):
        # realise everything closed by this entry
        open_pos.sort(key=lambda p: p["exit_t"])
        while open_pos and open_pos[0]["exit_t"] <= r["entry_t"]:
            p = open_pos.pop(0)
            eq += p["pnl"]
        busy = any(p["coin"] == r["coin"] and p["exit_t"] > r["sig_t"] for p in open_pos)
        if busy:
            continue
        risk_usd = risk * eq
        notional = risk_usd / r["rpu"] * r["fill"]
        if notional > lev * eq:
            risk_usd = lev * eq * r["rpu"] / r["fill"]
        if sum(p["risk_usd"] for p in open_pos) + risk_usd > cap * eq + 1e-9:
            continue
        pnl = r["R"] * risk_usd
        rec = {**r, "risk_usd": risk_usd, "pnl": pnl, "eq_at_entry": eq}
        open_pos.append(rec)
        taken.append(rec)
    return pd.DataFrame(taken)
