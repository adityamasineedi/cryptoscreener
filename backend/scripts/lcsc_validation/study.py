"""LARGE_CAP_SWEEP_CHOCH 1h LONG validation study (see PROTOCOL.md).

Signals come from the unchanged engine (generate_candidates + research_stop_and_targets).
Fills, costs, splits, walk-forward, regimes, concentration and Monte Carlo are computed here.

Run: PYTHONPATH=. .venv/Scripts/python.exe scripts/lcsc_validation/study.py
Writes out/results.json and out/trades_<SYM>.csv.
"""

from __future__ import annotations

import bisect
import csv
import json
import math
import pickle
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.research.multi_cap_strategies.common import research_stop_and_targets
from app.research.multi_cap_strategies.config import MultiCapResearchConfig
from app.research.multi_cap_strategies.large_cap_sweep_choch import generate_candidates

OUT = Path(__file__).resolve().parent / "out"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
UTC = timezone.utc


def d(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


EVAL_START = d("2023-01-01")
EVAL_END = d("2026-10-15")
TRAIN_END = d("2025-04-10")
VAL_END = d("2026-01-11")
WINDOWS = {
    "TRAIN": (EVAL_START, TRAIN_END),
    "VALIDATION": (TRAIN_END, VAL_END),
    "HOLDOUT": (VAL_END, EVAL_END),
    "OOS_COMBINED": (TRAIN_END, EVAL_END),
    "FULL": (EVAL_START, EVAL_END),
}
WARMUP = 1000
RISK_USD = 20.0
EQUITY = 1000.0
LEVERAGE = 2.0

COST_MODELS = {
    "GROSS": dict(next_open=False, slip_entry=0.0, slip_stop=0.0, fee=0.0, funding=False, gap=False),
    "AFTER_FEE": dict(next_open=False, slip_entry=0.0, slip_stop=0.0, fee=0.0004, funding=False, gap=False),
    "FULL": dict(next_open=True, slip_entry=0.0005, slip_stop=0.0010, fee=0.0004, funding=True, gap=True),
    "FULL_STRESS": dict(next_open=True, slip_entry=0.0010, slip_stop=0.0020, fee=0.0005, funding=True, gap=True),
}

V1 = dict(min_rr=2.0, atr_multiplier=1.5, lookback=48, swing=3)
GRID_RR = (1.5, 2.0, 2.5)
GRID_ATR = (1.0, 1.5, 2.0)
GRID_LB = (24, 48, 72)
GRID_SWING = (2, 3, 4)


def cfg_key(c: dict) -> str:
    return f"rr{c['min_rr']}_atr{c['atr_multiplier']}_lb{c['lookback']}_sw{c['swing']}"


# --------------------------------------------------------------------------- data


class Series:
    def __init__(self, sym: str) -> None:
        full = pickle.loads((OUT / f"candles_{sym}_1h.pkl").read_bytes())
        i0 = next(i for i, c in enumerate(full) if c["time"] >= EVAL_START)
        lo = max(0, i0 - WARMUP)
        self.sym = sym
        self.candles = full[lo:]
        self.eval_start = i0 - lo
        self.t = [c["time"] for c in self.candles]
        self.ts = np.array([c["time"].timestamp() for c in self.candles])
        self.o = np.array([c["open"] for c in self.candles])
        self.h = np.array([c["high"] for c in self.candles])
        self.l = np.array([c["low"] for c in self.candles])
        self.c = np.array([c["close"] for c in self.candles])
        steps = np.diff(self.ts)
        assert np.all(steps == 3600), f"{sym}: non-contiguous 1h bars"
        rows = json.loads((OUT / f"funding_{sym}.json").read_text())
        # Settlement times carry ms offsets (e.g. ...008); snap to the hour.
        self.f_t = [round(r["t"] / 1000 / 3600) * 3600 for r in rows]
        self.f_rate = [r["rate"] for r in rows]
        self.f_mark = [float(r["mark"]) if r.get("mark") else None for r in rows]
        self._regimes()

    def idx(self, when: datetime) -> int:
        """First bar index with open time >= when."""
        return bisect.bisect_left(self.ts, when.timestamp())

    def _regimes(self) -> None:
        c = self.c

        def ema(x: np.ndarray, n: int) -> np.ndarray:
            a = 2.0 / (n + 1)
            out = np.empty_like(x)
            out[0] = x[0]
            for i in range(1, len(x)):
                out[i] = a * x[i] + (1 - a) * out[i - 1]
            return out

        fast, slow = ema(c, 200), ema(c, 800)
        trend = np.where((c > fast) & (fast > slow), "TREND_UP", np.where((c < fast) & (fast < slow), "TREND_DOWN", "RANGE"))
        prev = np.concatenate([[c[0]], c[:-1]])
        tr = np.maximum(self.h - self.l, np.maximum(abs(self.h - prev), abs(self.l - prev)))
        atr = np.empty_like(tr)
        atr[:14] = np.nan
        atr[13] = tr[:14].mean()
        for i in range(14, len(tr)):
            atr[i] = (atr[i - 1] * 13 + tr[i]) / 14
        natr = atr / c
        vol = np.array(["NORMAL_VOL"] * len(c), dtype=object)
        win = 2160
        for i in range(len(c)):
            lo = max(0, i - win + 1)
            hist = natr[lo : i + 1]
            hist = hist[~np.isnan(hist)]
            if len(hist) < 500 or np.isnan(natr[i]):
                vol[i] = "UNKNOWN_VOL"
                continue
            rank = (hist < natr[i]).mean()
            vol[i] = "HIGH_VOL" if rank >= 0.80 else ("LOW_VOL" if rank <= 0.20 else "NORMAL_VOL")
        # Warm-up: the slow EMA needs ~800 bars; bars before eval start are not traded.
        self.trend = trend
        self.vol = vol


# --------------------------------------------------------------------------- signals


def candidates(s: Series, lookback: int, swing: int) -> list[dict]:
    path = OUT / f"cands_{s.sym}_lb{lookback}_sw{swing}.pkl"
    if path.exists():
        return pickle.loads(path.read_bytes())
    cfg = MultiCapResearchConfig(large_cap_htf_swing_lookback=lookback, swing_left_bars=swing, swing_right_bars=swing)
    t0 = time.perf_counter()
    raw = generate_candidates(s.sym, "1h", s.candles, config=cfg, index_start=s.eval_start)
    out = [
        {
            "i": c.entry_index,
            "close": c.entry_price,
            "inv": c.structural_invalidation,
            "atr": c.metadata.get("atr"),
            "sweep_time": c.metadata.get("sweep_time"),
        }
        for c in raw
    ]
    path.write_bytes(pickle.dumps(out))
    print(f"  signals {s.sym} lb{lookback} sw{swing}: {len(out)} in {time.perf_counter() - t0:.0f}s", flush=True)
    return out


def levels(cand: dict, conf: dict) -> tuple[float, float] | None:
    cfg = MultiCapResearchConfig(min_rr=conf["min_rr"], atr_multiplier=conf["atr_multiplier"])
    stop, tp1, *_rest, meta = research_stop_and_targets(
        direction="LONG",
        entry_price=cand["close"],
        atr=cand["atr"],
        structural_invalidation=cand["inv"],
        config=cfg,
    )
    if meta.get("status") != "OK" or stop is None or tp1 is None:
        return None
    return float(stop), float(tp1)


# --------------------------------------------------------------------------- walk


def walk(s: Series, plan: list[tuple[int, int, dict, list[dict]]], data_end: int) -> list[dict]:
    """plan: (entry_from, entry_to, config, candidates) pieces in time order. One open trade at a time
    across pieces. Bars at or after data_end are not read; trades still open there are marked at the
    last close (outcome OPEN)."""
    trades: list[dict] = []
    next_allowed = 0
    for a, b, conf, cands in plan:
        for cand in cands:
            i = cand["i"]
            if i < a or i >= b or i < next_allowed or i >= data_end - 1:
                continue
            lv = levels(cand, conf)
            if lv is None:
                continue
            stop, tp = lv
            j_exit, outcome, gap = None, "OPEN", False
            for j in range(i + 1, data_end):
                if s.l[j] <= stop:
                    j_exit, outcome, gap = j, "SL", bool(s.o[j] <= stop)
                    break
                if s.h[j] >= tp:
                    j_exit, outcome = j, "TP"
                    break
            if j_exit is None:
                j_exit = data_end - 1
            trades.append(
                {
                    "sym": s.sym,
                    "config": cfg_key(conf),
                    "i": i,
                    "exit_i": j_exit,
                    "signal_time": s.t[i].isoformat(),
                    "exit_time": s.t[j_exit].isoformat(),
                    "outcome": outcome,
                    "gap": gap,
                    "close": cand["close"],
                    "stop": stop,
                    "tp": tp,
                    "hold_bars": j_exit - i,
                    "trend": str(s.trend[i]),
                    "vol": str(s.vol[i]),
                }
            )
            next_allowed = j_exit + 1 if outcome != "OPEN" else data_end
    for t in trades:
        price_trade(s, t)
    return trades


def price_trade(s: Series, t: dict) -> None:
    C, S, TP = t["close"], t["stop"], t["tp"]
    qty = min(RISK_USD / (C - S), EQUITY * LEVERAGE / C)
    risk = qty * (C - S)
    t["qty"], t["risk_usd"] = qty, risk
    i, j = t["i"], t["exit_i"]
    for name, m in COST_MODELS.items():
        entry = s.o[i + 1] * (1 + m["slip_entry"]) if m["next_open"] else C
        if t["outcome"] == "SL":
            level = s.o[j] if (m["gap"] and t["gap"]) else S
            exit_px = level * (1 - m["slip_stop"])
        elif t["outcome"] == "TP":
            exit_px = TP
        else:
            exit_px = s.c[j] * (1 - m["slip_stop"])
        gross = qty * (exit_px - entry)
        fees = m["fee"] * qty * (entry + exit_px)
        funding = 0.0
        if m["funding"]:
            t_fill = s.ts[i + 1]
            t_last = s.ts[j]
            k = bisect.bisect_right(s.f_t, t_fill)
            while k < len(s.f_t) and s.f_t[k] <= t_last:
                mark = s.f_mark[k]
                if mark is None:
                    bi = bisect.bisect_left(s.ts, s.f_t[k])
                    mark = s.o[bi] if bi < len(s.o) else s.c[-1]
                funding -= s.f_rate[k] * qty * mark
                k += 1
        net = gross - fees + funding
        # Slippage in USD vs the frictionless fills (signal close, stop/target level).
        ideal_exit = S if t["outcome"] == "SL" else (TP if t["outcome"] == "TP" else s.c[j])
        slip = qty * ((entry - C) + (ideal_exit - exit_px))
        t[name] = {
            "entry": entry,
            "exit": exit_px,
            "gross_usd": gross,
            "fees_usd": fees,
            "funding_usd": funding,
            "slip_usd": slip,
            "net_usd": net,
            "R": net / risk,
        }


# --------------------------------------------------------------------------- metrics


def drawdown(values: list[float]) -> float:
    eq = pk = dd = 0.0
    for v in values:
        eq += v
        pk = max(pk, eq)
        dd = max(dd, pk - eq)
    return dd


def metrics(trades: list[dict], mode: str, window_hours: float | None = None) -> dict:
    tr = sorted(trades, key=lambda t: (t["exit_i"], t["i"]))
    n = len(tr)
    if n == 0:
        return {"trades": 0}
    R = [t[mode]["R"] for t in tr]
    usd = [t[mode]["net_usd"] for t in tr]
    wins = [r for r in R if r > 0]
    losses = [r for r in R if r <= 0]
    pos = sum(u for u in usd if u > 0)
    neg = -sum(u for u in usd if u < 0)
    streak = best = 0
    for r in R:
        streak = streak + 1 if r <= 0 else 0
        best = max(best, streak)
    gross_usd = sum(t["GROSS"]["net_usd"] for t in tr)
    fees = sum(t[mode]["fees_usd"] for t in tr)
    slip = sum(t[mode]["slip_usd"] for t in tr)
    fund = sum(t[mode]["funding_usd"] for t in tr)
    hold = sum(t["hold_bars"] for t in tr)
    out = {
        "trades": n,
        "net_usd": sum(usd),
        "net_R": sum(R),
        "gross_R": sum(t["GROSS"]["R"] for t in tr),
        "after_fee_R": sum(t["AFTER_FEE"]["R"] for t in tr),
        "win_rate": len(wins) / n,
        "avg_win_R": float(np.mean(wins)) if wins else None,
        "avg_loss_R": float(np.mean(losses)) if losses else None,
        "payoff": (float(np.mean(wins)) / abs(float(np.mean(losses)))) if wins and losses and np.mean(losses) != 0 else None,
        "pf": pos / neg if neg > 0 else None,
        "expectancy_R": sum(R) / n,
        "max_dd_usd": drawdown(usd),
        "max_dd_R": drawdown(R),
        "longest_losing_streak": best,
        "avg_hold_hours": hold / n,
        "exposure_pct": (100.0 * hold / window_hours) if window_hours else None,
        "fees_usd": fees,
        "slip_usd": slip,
        "funding_usd": fund,
        "gross_usd": gross_usd,
        "fee_drag_pct": (100.0 * fees / gross_usd) if gross_usd > 0 else None,
        "cost_drag_pct": (100.0 * (fees + slip - fund) / gross_usd) if gross_usd > 0 else None,
        "tp": sum(1 for t in tr if t["outcome"] == "TP"),
        "sl": sum(1 for t in tr if t["outcome"] == "SL"),
        "open": sum(1 for t in tr if t["outcome"] == "OPEN"),
        "gaps": sum(1 for t in tr if t["gap"]),
    }
    if n >= 10:
        rng = np.random.default_rng(7)
        arr = np.array(R)
        boots = rng.choice(arr, size=(5000, n), replace=True).mean(axis=1)
        out["expectancy_ci95"] = [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]
        sd = float(arr.std(ddof=1))
        out["t_stat"] = float(arr.mean() / (sd / math.sqrt(n))) if sd > 0 else None
    return out


def in_window(trades: list[dict], a: datetime, b: datetime) -> list[dict]:
    return [t for t in trades if a.isoformat() <= t["signal_time"] < b.isoformat()]


def hours(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600


def breakdown(trades: list[dict], key, mode: str = "FULL") -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    return {k: metrics(v, mode) for k, v in sorted(groups.items())}


def concentration(trades: list[dict], mode: str = "FULL") -> dict:
    R = sorted(((t[mode]["R"], t) for t in trades), key=lambda x: -x[0])
    total = sum(r for r, _ in R)
    usd_total = sum(t[mode]["net_usd"] for t in trades)
    top = [
        {
            "signal_time": t["signal_time"],
            "exit_time": t["exit_time"],
            "outcome": t["outcome"],
            "R": r,
            "net_usd": t[mode]["net_usd"],
            "trend": t["trend"],
            "vol": t["vol"],
        }
        for r, t in R[:10]
    ]
    ex = {f"excl_top{k}_R": total - sum(r for r, _ in R[:k]) for k in (1, 3, 5)}
    ex.update({f"excl_top{k}_usd": usd_total - sum(t[mode]["net_usd"] for _, t in R[:k]) for k in (1, 3, 5)})
    monthly: dict[str, float] = defaultdict(float)
    monthly_r: dict[str, float] = defaultdict(float)
    for t in trades:
        monthly[t["exit_time"][:7]] += t[mode]["net_usd"]
        monthly_r[t["exit_time"][:7]] += t[mode]["R"]
    vals = list(monthly.values())
    return {
        "total_R": total,
        "top10": top,
        **ex,
        "fragile": any(ex[f"excl_top{k}_R"] <= 0 for k in (1, 3, 5)),
        "monthly_usd": dict(sorted(monthly.items())),
        "monthly_R": dict(sorted(monthly_r.items())),
        "months": len(vals),
        "losing_months": sum(1 for v in vals if v < 0),
        "monthly_median_usd": float(np.median(vals)) if vals else None,
        "monthly_p10_usd": float(np.percentile(vals, 10)) if vals else None,
        "monthly_p90_usd": float(np.percentile(vals, 90)) if vals else None,
        "best_month_share_pct": (100.0 * max(vals) / usd_total) if vals and usd_total > 0 else None,
    }


def monte_carlo(R: list[float], sims: int = 10_000, risk: float = 0.02, seed: int = 11) -> dict:
    if len(R) < 5:
        return {}
    rng = np.random.default_rng(seed)
    arr = np.array(R)
    out = {}
    for label, sample in (
        ("shuffle", lambda: rng.permutation(arr)),
        ("bootstrap", lambda: rng.choice(arr, size=len(arr), replace=True)),
    ):
        finals, dds, dd_r = [], [], []
        for _ in range(sims):
            x = sample()
            eq = EQUITY * np.cumprod(1 + risk * x)
            peak = np.maximum.accumulate(np.concatenate([[EQUITY], eq]))[1:]
            dds.append(float(np.max(1 - eq / peak)))
            finals.append(float(eq[-1]))
            cr = np.cumsum(x)
            dd_r.append(float(np.max(np.maximum.accumulate(np.concatenate([[0.0], cr]))[1:] - cr)))
        finals, dds, dd_r = np.array(finals), np.array(dds), np.array(dd_r)
        out[label] = {
            "median_final_equity": float(np.median(finals)),
            "p5_final_equity": float(np.percentile(finals, 5)),
            "p95_final_equity": float(np.percentile(finals, 95)),
            "median_max_dd_pct": 100 * float(np.median(dds)),
            "p95_max_dd_pct": 100 * float(np.percentile(dds, 95)),
            "p_dd_ge_20pct": float((dds >= 0.20).mean()),
            "p_dd_ge_30pct": float((dds >= 0.30).mean()),
            "median_max_dd_R": float(np.median(dd_r)),
            "p95_max_dd_R": float(np.percentile(dd_r, 95)),
            "p_final_below_start": float((finals < EQUITY).mean()),
        }
    return out


# --------------------------------------------------------------------------- walk-forward


def grid_configs() -> list[dict]:
    return [
        dict(min_rr=rr, atr_multiplier=am, lookback=lb, swing=3) for rr in GRID_RR for am in GRID_ATR for lb in GRID_LB
    ]


def neighbours(c: dict) -> list[dict]:
    out = []
    for key, axis in (("min_rr", GRID_RR), ("atr_multiplier", GRID_ATR), ("lookback", GRID_LB)):
        k = axis.index(c[key])
        for kk in (k - 1, k + 1):
            if 0 <= kk < len(axis):
                out.append({**c, key: axis[kk]})
    return out


def select(s: Series, a: datetime, b: datetime, cands_by_lb: dict[int, list[dict]]) -> tuple[dict, dict]:
    ia, ib = s.idx(a), s.idx(b)
    score_raw: dict[str, tuple[float, int]] = {}
    for conf in grid_configs():
        tr = walk(s, [(ia, ib, conf, cands_by_lb[conf["lookback"]])], data_end=ib)
        n = len(tr)
        score_raw[cfg_key(conf)] = ((sum(t["FULL"]["R"] for t in tr) / n) if n else float("nan"), n)
    best, best_score = None, -1e9
    table = {}
    for conf in grid_configs():
        e, n = score_raw[cfg_key(conf)]
        vals = [score_raw[cfg_key(x)][0] for x in [conf, *neighbours(conf)]]
        vals = [v for v in vals if not math.isnan(v)]
        sc = float(np.mean(vals)) if vals else float("nan")
        table[cfg_key(conf)] = {"exp_R": e, "n": n, "score": sc}
        if n < 30 or math.isnan(sc):
            continue
        if sc > best_score + 1e-12 or (abs(sc - best_score) <= 1e-12 and cfg_key(conf) == cfg_key(V1)):
            best, best_score = conf, sc
    return (best or dict(V1)), table


def _add_months(dt: datetime, n: int) -> datetime:
    y, m = divmod(dt.month - 1 + n, 12)
    return dt.replace(year=dt.year + y, month=m + 1)


def wf_folds_calendar() -> list[tuple[datetime, datetime, datetime, datetime]]:
    folds = []
    test_a = d("2024-01-01")
    while test_a < VAL_END:
        test_b = _add_months(test_a, 3)
        if test_b >= VAL_END or (VAL_END - test_b).days < 45:
            test_b = VAL_END
        folds.append((_add_months(test_a, -12), test_a, test_a, test_b))
        test_a = test_b
    return folds


# --------------------------------------------------------------------------- stop-reversal short


def short_replay(s: Series, longs: list[dict]) -> dict:
    res: dict[str, Any] = {}
    stops = [t for t in longs if t["outcome"] == "SL"]
    for policy, mult in (("ONE_R", 1.0), ("MIRRORED_2R", None)):
        for walk_kind in ("SCREEN_NEXT_BAR", "CONSERVATIVE_STOP_BAR"):
            for costed in (False, True):
                rs = []
                wins = losses = opens = 0
                for t in stops:
                    entry_lvl, stop_lvl = t["stop"], t["close"]
                    r = stop_lvl - entry_lvl
                    target = entry_lvl - (mult * r if mult else (t["tp"] - t["close"]))
                    j0 = t["exit_i"]
                    hit, jx = None, None
                    if walk_kind == "CONSERVATIVE_STOP_BAR":
                        if s.h[j0] >= stop_lvl:
                            hit, jx = "SL", j0
                        elif s.l[j0] <= target:
                            hit, jx = "TP", j0
                    if hit is None:
                        for j in range(j0 + 1, len(s.c)):
                            if s.h[j] >= stop_lvl:
                                hit, jx = "SL", j
                                break
                            if s.l[j] <= target:
                                hit, jx = "TP", j
                                break
                    if hit is None:
                        opens += 1
                        continue
                    qty = min(RISK_USD / r, EQUITY * LEVERAGE / entry_lvl)
                    risk = qty * r
                    if not costed:
                        R = -1.0 if hit == "SL" else (entry_lvl - target) / r
                    else:
                        fill_lvl = s.o[j0] if t["gap"] else entry_lvl
                        e = fill_lvl * (1 - 0.0010)
                        if hit == "SL":
                            x = (s.o[jx] if s.o[jx] >= stop_lvl and jx > j0 else stop_lvl) * (1 + 0.0010)
                        else:
                            x = target
                        pnl = qty * (e - x) - 0.0004 * qty * (e + x)
                        k = bisect.bisect_right(s.f_t, s.ts[j0])
                        while k < len(s.f_t) and s.f_t[k] <= s.ts[jx]:
                            mark = s.f_mark[k] or s.o[min(len(s.o) - 1, bisect.bisect_left(s.ts, s.f_t[k]))]
                            pnl += s.f_rate[k] * qty * mark
                            k += 1
                        R = pnl / risk
                    rs.append(R)
                    if R > 0:
                        wins += 1
                    else:
                        losses += 1
                key = f"{policy}|{walk_kind}|{'COSTED' if costed else 'NO_COST'}"
                res[key] = {
                    "shorts": len(rs),
                    "winners": wins,
                    "losers": losses,
                    "open": opens,
                    "total_R": float(sum(rs)),
                    "expectancy_R": float(np.mean(rs)) if rs else None,
                }
    return res


# --------------------------------------------------------------------------- main


def main() -> None:
    t_start = time.perf_counter()
    results: dict[str, Any] = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "windows": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in WINDOWS.items()},
        "cost_models": COST_MODELS,
        "v1": V1,
        "symbols": {},
    }
    for sym in SYMBOLS:
        print(sym, flush=True)
        s = Series(sym)
        cands = {(lb, sw): candidates(s, lb, sw) for lb in GRID_LB for sw in (3,)}
        for sw in GRID_SWING:
            cands[(48, sw)] = candidates(s, 48, sw)
        by_lb = {lb: cands[(lb, 3)] for lb in GRID_LB}
        end = len(s.c)
        R: dict[str, Any] = {}

        # ---- v1 frozen, one continuous walk over the evaluation range
        v1 = walk(s, [(s.eval_start, end, V1, by_lb[48])], data_end=end)
        R["parity"] = {
            "trades": len(v1),
            "after_fee_net_usd": sum(t["AFTER_FEE"]["net_usd"] for t in v1 if t["outcome"] != "OPEN"),
            "closed": sum(1 for t in v1 if t["outcome"] != "OPEN"),
        }
        full_closed = [t for t in v1 if t["outcome"] != "OPEN"]
        R["v1"] = {}
        for wname, (a, b) in WINDOWS.items():
            tw = in_window(full_closed, a, b)
            R["v1"][wname] = {mode: metrics(tw, mode, hours(a, b)) for mode in COST_MODELS}
        oos = in_window(full_closed, TRAIN_END, EVAL_END)
        R["v1_by_year"] = breakdown(full_closed, lambda t: t["signal_time"][:4])
        R["v1_by_trend"] = {w: breakdown(in_window(full_closed, *WINDOWS[w]), lambda t: t["trend"]) for w in ("FULL", "TRAIN", "OOS_COMBINED")}
        R["v1_by_vol"] = {w: breakdown(in_window(full_closed, *WINDOWS[w]), lambda t: t["vol"]) for w in ("FULL", "TRAIN", "OOS_COMBINED")}
        ia, ib = s.eval_start, end
        R["bar_share_trend"] = {k: float((s.trend[ia:ib] == k).mean()) for k in ("TREND_UP", "TREND_DOWN", "RANGE")}
        R["bar_share_vol"] = {k: float((s.vol[ia:ib] == k).mean()) for k in ("HIGH_VOL", "NORMAL_VOL", "LOW_VOL", "UNKNOWN_VOL")}
        R["concentration"] = {"FULL": concentration(full_closed), "OOS_COMBINED": concentration(oos), "TRAIN": concentration(in_window(full_closed, *WINDOWS["TRAIN"]))}
        R["monte_carlo"] = {
            "OOS_COMBINED": monte_carlo([t["FULL"]["R"] for t in sorted(oos, key=lambda t: t["exit_i"])]),
            "FULL": monte_carlo([t["FULL"]["R"] for t in sorted(full_closed, key=lambda t: t["exit_i"])]),
        }

        # ---- parameter sensitivity (one at a time around v1), FULL costs
        sens: dict[str, Any] = {}
        variants = []
        for rr in GRID_RR:
            variants.append(("min_rr", rr, {**V1, "min_rr": rr}))
        for am in GRID_ATR:
            variants.append(("atr_multiplier", am, {**V1, "atr_multiplier": am}))
        for lb in GRID_LB:
            variants.append(("lookback", lb, {**V1, "lookback": lb}))
        for sw in GRID_SWING:
            variants.append(("swing", sw, {**V1, "swing": sw}))
        for param, val, conf in variants:
            tr = walk(s, [(s.eval_start, end, conf, cands[(conf["lookback"], conf["swing"])])], data_end=end)
            tr = [t for t in tr if t["outcome"] != "OPEN"]
            sens.setdefault(param, {})[str(val)] = {
                w: {k: metrics(in_window(tr, *WINDOWS[w]), "FULL").get(k) for k in ("trades", "expectancy_R", "pf", "net_R", "max_dd_R")}
                for w in ("TRAIN", "VALIDATION", "HOLDOUT", "OOS_COMBINED", "FULL")
            }
        R["sensitivity"] = sens

        # 27-config grid, full range and train (descriptive)
        grid = {}
        for conf in grid_configs():
            tr = walk(s, [(s.eval_start, end, conf, by_lb[conf["lookback"]])], data_end=end)
            tr = [t for t in tr if t["outcome"] != "OPEN"]
            grid[cfg_key(conf)] = {
                w: {k: metrics(in_window(tr, *WINDOWS[w]), "FULL").get(k) for k in ("trades", "expectancy_R", "pf")}
                for w in ("TRAIN", "OOS_COMBINED", "FULL")
            }
        R["grid"] = grid

        # ---- walk-forward re-optimisation
        folds = wf_folds_calendar()
        plan, fold_rows = [], []
        for tr_a, tr_b, te_a, te_b in folds:
            chosen, _table = select(s, tr_a, tr_b, by_lb)
            plan.append((s.idx(te_a), s.idx(te_b), chosen, by_lb[chosen["lookback"]]))
            fold_rows.append({"train": [tr_a.isoformat(), tr_b.isoformat()], "test": [te_a.isoformat(), te_b.isoformat()], "chosen": cfg_key(chosen)})
        ho_conf, ho_table = select(s, d("2025-01-11"), VAL_END, by_lb)
        plan.append((s.idx(VAL_END), end, ho_conf, by_lb[ho_conf["lookback"]]))
        fold_rows.append({"train": ["2025-01-11", VAL_END.isoformat()], "test": [VAL_END.isoformat(), EVAL_END.isoformat()], "chosen": cfg_key(ho_conf), "holdout": True})
        wf = walk(s, plan, data_end=end)
        wf_closed = [t for t in wf if t["outcome"] != "OPEN"]
        for row, (_a, _b, conf, _c) in zip(fold_rows, plan):
            a, b = d(row["test"][0][:10]), d(row["test"][1][:10])
            row["test_metrics"] = {k: metrics(in_window(wf_closed, a, b), "FULL").get(k) for k in ("trades", "expectancy_R", "net_R", "pf")}
            row["v1_same_window"] = {k: metrics(in_window(full_closed, a, b), "FULL").get(k) for k in ("trades", "expectancy_R", "net_R", "pf")}
        R["walk_forward"] = {
            "folds": fold_rows,
            "wf_tests_2024_to_val_end": {m: metrics(in_window(wf_closed, d("2024-01-01"), VAL_END), m) for m in ("GROSS", "AFTER_FEE", "FULL", "FULL_STRESS")},
            "wf_validation_window": metrics(in_window(wf_closed, TRAIN_END, VAL_END), "FULL"),
            "wf_holdout": metrics(in_window(wf_closed, VAL_END, EVAL_END), "FULL"),
            "wf_combined_oos": metrics(in_window(wf_closed, TRAIN_END, EVAL_END), "FULL"),
            "wf_all_tests_incl_holdout": metrics(in_window(wf_closed, d("2024-01-01"), EVAL_END), "FULL"),
            "v1_same_span_2024_to_end": metrics(in_window(full_closed, d("2024-01-01"), EVAL_END), "FULL"),
            "holdout_selection_table": ho_table,
        }

        # ---- stop-reversal short re-pricing (v1 longs, full range)
        R["short_replay"] = short_replay(s, full_closed)
        results["symbols"][sym] = R

        with (OUT / f"trades_{sym}.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["signal_time", "exit_time", "outcome", "gap", "close", "stop", "tp", "qty", "risk_usd", "hold_bars", "trend", "vol",
                        "R_gross", "R_after_fee", "R_full", "R_full_stress", "net_usd_full", "fees_full", "slip_full", "funding_full"])
            for t in v1:
                w.writerow([t["signal_time"], t["exit_time"], t["outcome"], t["gap"], t["close"], t["stop"], t["tp"], round(t["qty"], 6),
                            round(t["risk_usd"], 4), t["hold_bars"], t["trend"], t["vol"], round(t["GROSS"]["R"], 4), round(t["AFTER_FEE"]["R"], 4),
                            round(t["FULL"]["R"], 4), round(t["FULL_STRESS"]["R"], 4), round(t["FULL"]["net_usd"], 4), round(t["FULL"]["fees_usd"], 4),
                            round(t["FULL"]["slip_usd"], 4), round(t["FULL"]["funding_usd"], 4)])
        print(f"  done {sym} at {time.perf_counter() - t_start:.0f}s", flush=True)

    (OUT / "results.json").write_text(json.dumps(results, indent=1, default=str))
    print("wrote", OUT / "results.json")


if __name__ == "__main__":
    sys.setrecursionlimit(10000)
    main()
