"""Phase 3+ statistics: trade metrics, weekly block bootstrap, deflated Sharpe, random-entry null."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from statistics import NormalDist

from engine import Exec, resolve

WEEK = 7 * 86400
EULER = 0.5772156649
N01 = NormalDist()


def metrics(tr: pd.DataFrame, seg: tuple, equity0: float = 10_000.0) -> dict:
    a, b = int(seg[0].timestamp()), int(seg[1].timestamp())
    months = (b - a) / 86400 / 30.44
    if tr.empty:
        return {"trades": 0, "trades_per_month": 0.0}
    t = tr.sort_values("exit_t")
    R = t["R"].to_numpy()
    pnl = t["pnl"].to_numpy()
    eq = equity0 + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.r_[equity0, eq])[1:]
    dd = float(((peak - eq) / peak).max()) if len(eq) else 0.0
    wins, losses = R[R > 0], R[R <= 0]
    # consecutive losses in exit order
    streak = mx = 0
    for r in R:
        streak = streak + 1 if r <= 0 else 0
        mx = max(mx, streak)
    # monthly returns on equity at month start (closed-trade basis)
    mk = pd.to_datetime(t["exit_t"], unit="s", utc=True).dt.strftime("%Y-%m").to_numpy()
    mon = pd.Series(pnl).groupby(mk).sum()
    eq_start = equity0 + mon.cumsum().shift(fill_value=0.0)
    mret = (mon / eq_start) * 100
    all_months = pd.period_range(seg[0].strftime("%Y-%m"), (seg[1] - pd.Timedelta(seconds=1)).strftime("%Y-%m"),
                                 freq="M").astype(str)
    mret = mret.reindex(all_months, fill_value=0.0)
    # exposure: share of segment time with >= 1 open position
    iv = sorted(zip(t["entry_t"], t["exit_t"]))
    covered, cur_s, cur_e = 0, None, None
    for s, e in iv:
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                covered += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        covered += cur_e - cur_s
    exposure = covered / (b - a)
    sd = R.std(ddof=1) if len(R) > 1 else float("nan")
    net_ret = (eq[-1] / equity0 - 1) * 100
    gp, gl = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    return {
        "trades": int(len(R)), "trades_per_month": round(len(R) / months, 2),
        "win_rate": round(float((R > 0).mean()) * 100, 1),
        "avg_win_R": round(float(wins.mean()), 3) if len(wins) else None,
        "avg_loss_R": round(float(losses.mean()), 3) if len(losses) else None,
        "exp_R": round(float(R.mean()), 4), "exp_R_gross": round(float(t["R_gross"].mean()), 4),
        "cost_R": round(float(t["cost_R"].mean()), 4),
        "pf": round(float(gp / gl), 3) if gl > 0 else None,
        "net_ret_pct": round(float(net_ret), 2), "maxdd_pct": round(dd * 100, 2),
        "max_consec_losses": int(mx),
        "months_pos_pct": round(float((mret > 0).mean()) * 100, 1),
        "month_median_pct": round(float(mret.median()), 3), "month_worst_pct": round(float(mret.min()), 3),
        "month_best_pct": round(float(mret.max()), 3),
        "exposure_pct": round(exposure * 100, 1),
        "ret_per_exposure": round(float(net_ret / (exposure * 100)), 3) if exposure > 0 else None,
        "sr_trade": round(float(R.mean() / sd), 4) if sd and sd > 0 else None,
        "skew": round(float(pd.Series(R).skew()), 3), "kurt": round(float(pd.Series(R).kurt() + 3), 3),
    }


def block_bootstrap(tr: pd.DataFrame, reps: int = 5000, seed: int = 7) -> dict:
    """Mean net R with weekly clusters (by entry week) resampled with replacement."""
    if len(tr) < 2:
        return {"boot95_lo": None, "boot95_hi": None, "boot90_lo": None}
    wk = (tr["entry_t"] // WEEK).to_numpy()
    g = pd.DataFrame({"wk": wk, "R": tr["R"].to_numpy()}).groupby("wk")["R"].agg(["sum", "count"])
    s, c = g["sum"].to_numpy(), g["count"].to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(s), (reps, len(s)))
    means = s[idx].sum(1) / c[idx].sum(1)
    q = np.percentile(means, [2.5, 5, 97.5])
    return {"boot95_lo": round(float(q[0]), 4), "boot90_lo": round(float(q[1]), 4), "boot95_hi": round(float(q[2]), 4)}


def deflated_sharpe(sr: float, T: int, skew: float, kurt: float, n_trials: int, var_sr: float) -> dict:
    """Bailey & Lopez de Prado (2014). sr: per-trade Sharpe; kurt: non-excess kurtosis."""
    if n_trials < 2 or not var_sr or var_sr <= 0:
        sr0 = 0.0
    else:
        sr0 = math.sqrt(var_sr) * ((1 - EULER) * N01.inv_cdf(1 - 1 / n_trials)
                                   + EULER * N01.inv_cdf(1 - 1 / (n_trials * math.e)))
    den = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    z = (sr - sr0) * math.sqrt(T - 1) / math.sqrt(den) if den > 0 and T > 1 else float("nan")
    return {"sr0": round(sr0, 4), "dsr": round(float(N01.cdf(z)), 4)}


def random_null(tr: pd.DataFrame, ex_by: dict, seg: tuple, draws: int = 300, seed: int = 11) -> dict:
    """Null: same number of trades, same coin/side/stop%/target-R/hold mix (sampled from the real trades),
    market entries at uniformly random exec-bar opens. Returns the null mean distribution and one-sided p."""
    if tr.empty:
        return {"null_mean": None, "null_p": None}
    rng = np.random.default_rng(seed)
    a, b = int(seg[0].timestamp()), int(seg[1].timestamp())
    real = float(tr["R"].mean())
    rows = tr[["coin", "side", "stop_pct", "tgt_R", "hold_s", "exec_tf"]].to_dict("records")
    n = len(rows)
    means = np.empty(draws)
    for d in range(draws):
        pick = rng.integers(0, n, n)
        rs = []
        for k in pick:
            r = rows[k]
            ex: Exec = ex_by[(r["coin"], r["exec_tf"])]
            lo = int(np.searchsorted(ex.t, a))
            hi = int(np.searchsorted(ex.t, b - int(r["hold_s"])))
            if hi <= lo:
                continue
            i = int(rng.integers(lo, hi))
            o = ex.o[i]
            s = {"side": r["side"], "sig_t": int(ex.t[i]), "entry": "mkt", "stop": o * (1 - r["side"] * r["stop_pct"]),
                 "tgt_kind": "R", "tgt": r["tgt_R"], "hold_s": r["hold_s"]}
            x = resolve(s, ex)
            if x and "R" in x:
                rs.append(x["R"])
        means[d] = np.mean(rs) if rs else np.nan
    means = means[~np.isnan(means)]
    p = (1 + int((means >= real).sum())) / (1 + len(means))
    return {"null_mean": round(float(means.mean()), 4), "null_p95": round(float(np.percentile(means, 95)), 4),
            "null_p": round(p, 4)}
