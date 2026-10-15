"""Runs one hypothesis configuration on a coin list and segment; logs to research/experiments.csv and the
compute log (research/out/compute_log.csv). Used by p3_baselines.py, p4_compare.py, p5_improve.py, p6_validate.py.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from common import OUT, ROOT, bars, epoch_s
from engine import Exec, book, conflicts, purge, resolve_all
from rstats import block_bootstrap, metrics, random_null

EXP = ROOT / "research" / "experiments.csv"
COMPUTE = OUT / "compute_log.csv"
EXEC_FOR_STOP = {"4h": "1h", "1h": "15m"}
_BARS: dict = {}


def trades_file(name: str, seg_name: str, universe: str) -> str:
    """Windows-safe trade-log name: ':' in a config name would create an NTFS alternate data stream."""
    return f"trades_{name.replace(':', '_')}_{seg_name}_{universe}.csv"


def coin_bars(coin: str, seg: tuple) -> dict:
    k = (coin, seg)
    if k not in _BARS:
        _BARS[k] = bars(coin, seg[0], seg[1])
    return _BARS[k]


def setups_for(gen, coin: str, seg: tuple, filt=None, transform=None) -> tuple[pd.DataFrame, dict]:
    b = coin_bars(coin, seg)
    s = gen(b)
    if s.empty:
        return s, b
    s = s.assign(coin=coin)
    if transform is not None and len(s):
        s = transform(s, b)          # applied before the purge so changed holding times are respected
    s = purge(s, seg)
    s = s.assign(exec_tf=s["stop_tf"].map(EXEC_FOR_STOP))
    if (s["exec_tf"] == "15m").any():
        m15 = b["15m"]
        first = int(epoch_s(m15["time"])[0]) if len(m15) else 2**62
        s = s[(s["exec_tf"] != "15m") | (s["sig_t"] >= first)]
    if filt is not None and len(s):
        s = s[filt(s, b)]
    return conflicts(s), b


def run(name: str, gen, coins: list[str], seg: tuple, seg_name: str, universe: str, phase: str,
        risk: float = 0.005, null_draws: int = 300, filt=None, transform=None, log: bool = True,
        save: bool = True) -> dict:
    t0 = time.time()
    resolved, ex_by = [], {}
    for coin in coins:
        s, b = setups_for(gen, coin, seg, filt, transform)
        if s.empty:
            continue
        etf = s["exec_tf"].iloc[0]
        ex = Exec.of(b[etf], etf)
        ex_by[(coin, etf)] = ex
        c4 = b["4h"]
        r = resolve_all(s, ex, epoch_s(c4["time"]), c4["close"].to_numpy(float))
        resolved.append(r)
    allr = pd.concat(resolved, ignore_index=True) if resolved else pd.DataFrame()
    skips = allr["skip"].value_counts().to_dict() if "skip" in allr else {}
    cand = allr[allr["skip"].isna()] if "skip" in allr else allr
    tr = book(cand, risk=risk) if len(cand) else pd.DataFrame()
    res = {"name": name, "segment": seg_name, "universe": universe, "coins": coins, "setups": int(len(allr)),
           "skips": skips, "candidates": int(len(cand))}
    res["pooled"] = metrics(tr, seg) if len(tr) else {"trades": 0}
    if len(tr):
        res["pooled"].update(block_bootstrap(tr))
        if null_draws:
            res["pooled"].update(random_null(tr, ex_by, seg, draws=null_draws))
    res["per_coin"] = {c: metrics(tr[tr["coin"] == c], seg) for c in coins} if len(tr) else {}
    res["by_side"] = ({("long" if s > 0 else "short"): round(float(tr.loc[tr["side"] == s, "R"].mean()), 4)
                       for s in (1, -1) if (tr["side"] == s).any()} if len(tr) else {})
    res["exits"] = tr["why"].value_counts().to_dict() if len(tr) else {}
    res["seconds"] = round(time.time() - t0, 1)
    if save and len(tr):
        d = OUT / phase
        d.mkdir(parents=True, exist_ok=True)
        tr.drop(columns=[c for c in ("skip",) if c in tr]).to_csv(d / trades_file(name, seg_name, universe),
                                                                  index=False)
    if log:
        log_experiment(res, phase)
    log_compute(phase, f"{name} {seg_name} {universe}", res["seconds"])
    return res


def log_experiment(res: dict, phase: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hyp, _, variant = res["name"].partition(":")
    rows = []
    for coin, m in [("POOLED", res["pooled"])] + list(res["per_coin"].items()):
        rows.append({"logged_at": now, "phase": phase, "config": res["name"], "hyp": hyp, "variant": variant or "base",
                     "segment": res["segment"], "universe": res["universe"], "coin": coin,
                     **{k: m.get(k) for k in ("trades", "trades_per_month", "win_rate", "exp_R", "exp_R_gross",
                                              "cost_R", "pf", "sr_trade", "skew", "kurt", "net_ret_pct",
                                              "maxdd_pct", "boot95_lo", "null_p")}})
    new = pd.DataFrame(rows)
    if EXP.exists():
        old = pd.read_csv(EXP)
        key = ["phase", "config", "segment", "universe", "coin"]
        old = old.merge(new[key], on=key, how="left", indicator=True)
        old = old[old["_merge"] == "left_only"].drop(columns="_merge")
        new = pd.concat([old, new], ignore_index=True)
    new.to_csv(EXP, index=False)


def log_compute(phase: str, item: str, seconds: float) -> None:
    row = pd.DataFrame([{"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "phase": phase,
                         "item": item, "seconds": seconds}])
    if COMPUTE.exists():
        row = pd.concat([pd.read_csv(COMPUTE), row], ignore_index=True)
    row.to_csv(COMPUTE, index=False)


def n_trials() -> tuple[int, float]:
    """Distinct trial configurations in experiments.csv and the variance of their pooled per-trade Sharpe
    (development segment, core universe; falls back to all pooled rows)."""
    e = pd.read_csv(EXP)
    pooled = e[e["coin"] == "POOLED"]
    n = int(pooled["config"].nunique())
    dev = pooled[(pooled["segment"] == "dev") & (pooled["universe"] == "core")].drop_duplicates("config")
    dev = dev[dev["trades"] >= 30]   # Phase 2b amendment: tiny cells give degenerate Sharpe estimates
    v = float(np.nanvar(dev["sr_trade"].astype(float), ddof=1)) if len(dev) > 2 else float("nan")
    return n, v
