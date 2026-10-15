"""Phase 2b Track B1: time-series momentum (pre-registered in research/out/p2b/PREREG.md).

Signal at each bar close: sign(C_t / C_{t-N} - 1). Always in the market; on a sign change exit and reverse at the
next bar open. Notional at entry = E * 0.10 / sigma_ann (60-bar log-return sd, annualised), capped at 2E, fixed
for the trade. Costs: taker + slippage on each fill, flat funding 0.01%/8h of notional, both sides.
R unit: notional * 2 * ATR(14)/price at the signal bar. Open trades close at the segment's last bar close.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import structure as S
from common import FUNDING_8H, SLIP, TAKER, TF_SEC, epoch_s

PPY = {"1d": 365, "4h": 2190}
VOL_TARGET, NOTIONAL_CAP, VOL_N = 0.10, 2.0, 60
FUND_SEC = 8 * 3600


def coin_trades(df: pd.DataFrame, tf: str, n: int, seg: tuple, coin: str) -> pd.DataFrame:
    """Per-unit trades (sizing applied later by book())."""
    sec = TF_SEC[tf]
    t = epoch_s(df["time"])
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = S.atr(h, l, c, 14)
    lr = np.diff(np.log(c), prepend=np.nan)
    sig_ann = pd.Series(lr).rolling(VOL_N).std().to_numpy() * np.sqrt(PPY[tf])
    sgn = np.full(len(c), 0.0)
    sgn[n:] = np.sign(c[n:] / c[:-n] - 1)
    a0, b0 = int(seg[0].timestamp()), int(seg[1].timestamp())
    idx = np.flatnonzero((t >= a0) & (t + sec <= b0))
    rows, pos = [], None
    for k, i in enumerate(idx):
        last = k == len(idx) - 1
        if last:
            break
        j = idx[k + 1]          # next bar inside the segment
        s = sgn[i]
        if s == 0 or np.isnan(sig_ann[i]) or np.isnan(a[i]) or (pos is not None and pos["side"] == s):
            continue
        if pos is not None:
            rows.append(_close(pos, o[j], int(t[j]), coin))
        pos = {"side": int(s), "sig_t": int(t[i] + sec), "entry_t": int(t[j]),
               "fill": o[j] * (1 + s * SLIP), "atr": a[i] * o[j] / c[i], "sigma": sig_ann[i]}
    if pos is not None:
        e = idx[-1]
        rows.append(_close(pos, c[e], int(t[e] + sec), coin, at_close=True))
    return pd.DataFrame(rows)


def _close(pos: dict, px_raw: float, t_exit: int, coin: str, at_close: bool = False) -> dict:
    s, fill = pos["side"], pos["fill"]
    px = px_raw * (1 - s * SLIP)
    nf = t_exit // FUND_SEC - pos["entry_t"] // FUND_SEC
    gross = s * (px - fill)
    cost = TAKER * (fill + px) + FUNDING_8H * fill * nf
    unit = 2 * pos["atr"]
    return {"coin": coin, "side": s, "sig_t": pos["sig_t"], "entry_t": pos["entry_t"], "exit_t": t_exit,
            "fill": fill, "exit_px": px, "sigma": pos["sigma"], "R": (gross - cost) / unit, "R_gross": gross / unit,
            "cost_R": cost / unit, "ret_unit": (gross - cost) / fill, "why": "segment_end" if at_close else "flip"}


def book(tr: pd.DataFrame, equity0: float = 10_000.0) -> pd.DataFrame:
    t = tr.sort_values(["entry_t", "coin"]).reset_index(drop=True)
    eq, open_ = equity0, []
    out = []
    for r in t.to_dict("records"):
        open_.sort(key=lambda x: x["exit_t"])
        while open_ and open_[0]["exit_t"] <= r["entry_t"]:
            eq += open_.pop(0)["pnl"]
        notional = min(eq * VOL_TARGET / r["sigma"], NOTIONAL_CAP * eq)
        rec = {**r, "notional": notional, "qty": notional / r["fill"], "pnl": notional * r["ret_unit"],
               "eq_at_entry": eq}
        open_.append(rec)
        out.append(rec)
    return pd.DataFrame(out)


def mtm_drawdown(trades: pd.DataFrame, closes: dict, equity0: float = 10_000.0) -> float:
    """Max drawdown of equity marked at every bar close (realised + open PnL). closes[coin] = (t_close, close)."""
    if trades.empty:
        return 0.0
    grid = np.unique(np.concatenate([v[0] for v in closes.values()]))
    eq = np.full(len(grid), equity0)
    for r in trades.to_dict("records"):
        tc, cc = closes[r["coin"]]
        px = pd.Series(cc, index=tc).reindex(grid).ffill().to_numpy()
        live = (grid >= r["entry_t"]) & (grid < r["exit_t"])
        eq[live] += r["qty"] * r["side"] * (px[live] - r["fill"])
        eq[grid >= r["exit_t"]] += r["pnl"]
    peak = np.maximum.accumulate(eq)
    return float(((peak - eq) / peak).max())


def null(tr: pd.DataFrame, data: dict, seg: tuple, tf: str, draws: int = 300, seed: int = 5) -> dict:
    """Random side, random start; same coin mix and holding-duration distribution; same R unit and costs."""
    rng = np.random.default_rng(seed)
    sec = TF_SEC[tf]
    a0, b0 = int(seg[0].timestamp()), int(seg[1].timestamp())
    prep = {}
    for coin, df in data.items():
        t = epoch_s(df["time"])
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        prep[coin] = (t, o, c, S.atr(h, l, c, 14), np.flatnonzero((t >= a0) & (t + sec <= b0)))
    coins = tr["coin"].to_numpy()
    dur = ((tr["exit_t"] - tr["entry_t"]) // sec).clip(lower=1).astype(int).to_numpy()
    n = len(tr)
    means = []
    for _ in range(draws):
        pick = rng.integers(0, n, n)
        rs = []
        for k in pick:
            t, o, c, a, idx = prep[coins[k]]
            d = dur[k]
            if len(idx) <= d + 2:
                continue
            p = int(rng.integers(0, len(idx) - d - 1))
            i, j = idx[p], idx[p + 1]
            e = min(idx[p + 1 + d] if p + 1 + d < len(idx) else idx[-1], idx[-1])
            s = 1 if rng.random() < 0.5 else -1
            fill = o[j] * (1 + s * SLIP)
            px = o[e] * (1 - s * SLIP)
            nf = (t[e]) // FUND_SEC - t[j] // FUND_SEC
            unit = 2 * a[i] * o[j] / c[i]
            if not unit > 0:
                continue
            rs.append((s * (px - fill) - TAKER * (fill + px) - FUNDING_8H * fill * nf) / unit)
        means.append(np.mean(rs))
    means = np.array(means)
    real = float(tr["R"].mean())
    return {"null_mean": round(float(means.mean()), 4), "null_p": round((1 + int((means >= real).sum())) / (1 + len(means)), 4)}
