"""Phase 2 descriptive statistics on the DEV segment, core coins only. No trade outcomes are computed.

1. Structure-method comparison: swings/month, confirmation delay, swing size, distance price has already
   travelled from the pivot when the pivot becomes known, share of bars by structural trend.
2. Structural trend vs the Phase 1 volatility-normalised daily regime (agreement on direction).
3. Setup frequency per hypothesis and the round-trip cost expressed in R at the setup's stop distance.

Run from repo root:  backend/.venv/Scripts/python.exe research/p2_stats.py
Writes research/out/p2/p2_stats.md and CSVs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hypotheses as H  # noqa: E402
import structure as S  # noqa: E402
from common import CORE, DEV, OUT, SLIP, TAKER, bars, epoch_s  # noqa: E402

D0 = int(DEV[0].timestamp())
D1 = int(DEV[1].timestamp())
MONTHS = (D1 - D0) / 86400 / 30.44
COST_RT = 2 * (TAKER + SLIP)


def md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(out)


def swing_events(st: dict) -> list[tuple[int, int, float, int]]:
    ev = []
    for kind, k in ((1, "sh"), (-1, "sl")):
        conf = st[f"{k}_conf"]
        ch = np.flatnonzero(~np.isnan(conf) & (np.r_[np.nan, conf[:-1]] != conf))
        ev += [(int(conf[i]), int(st[f"{k}_piv"][i]), float(st[k][i]), kind) for i in ch]
    return sorted(ev)


def method_stats(df: pd.DataFrame, tf: str, coin: str) -> list[dict]:
    t = epoch_s(df["time"])
    h, l, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    a = S.atr(h, l, c, 14)
    zz_pct = 0.03 if tf == "4h" else 0.015
    methods = {"fractal k=3": S.fractal_swings(h, l, 3), "DC 2.5×ATR": S.dc_swings(h, l, 2.5 * a),
               f"zigzag {zz_pct:.1%}": S.dc_swings(h, l, np.where(np.isnan(a), np.nan, zz_pct * c))}
    inwin = (t >= D0) & (t < D1)
    rows = []
    for name, st in methods.items():
        ev = [e for e in swing_events(st) if D0 <= t[e[0]] < D1 and not np.isnan(a[e[0]])]
        delay = [e[0] - e[1] for e in ev]
        travel = [abs(c[e[0]] - e[2]) / a[e[0]] for e in ev]
        amp = [abs(ev[i][2] - ev[i - 1][2]) / a[ev[i][0]] for i in range(1, len(ev))]
        tr = st["trend"][inwin]
        rows.append(dict(coin=coin, tf=tf, method=name, swings_per_month=round(len(ev) / MONTHS, 1),
                         median_delay_bars=float(np.median(delay)), median_swing_atr=round(float(np.median(amp)), 2),
                         median_travel_at_confirm_atr=round(float(np.median(travel)), 2),
                         pct_up=round(float((tr == 1).mean()) * 100), pct_down=round(float((tr == -1).mean()) * 100),
                         pct_range=round(float((tr == 0).mean()) * 100)))
    return rows


def trend_vs_z(b: dict, coin: str) -> dict:
    d = b["1d"]
    z = S.daily_z(d["close"].to_numpy(float))
    zl = np.where(z >= 1, 1, np.where(z <= -1, -1, 0)).astype(float)
    zl[np.isnan(z)] = np.nan
    x4 = b["4h"]
    t4 = epoch_s(x4["time"])
    h, l, c = (x4[k].to_numpy(float) for k in ("high", "low", "close"))
    st = S.dc_swings(h, l, 2.5 * S.atr(h, l, c, 14))
    zm = S.asof_map(t4, 14400, epoch_s(d["time"]), 86400, zl)
    w = (t4 >= D0) & (t4 < D1) & ~np.isnan(zm)
    tr, zz = st["trend"][w], zm[w]
    both = (tr != 0) & (zz != 0)
    return dict(coin=coin, bars_4h=int(w.sum()), pct_dc_trend_nonzero=round(float((tr != 0).mean()) * 100),
                pct_z_trend_nonzero=round(float((zz != 0).mean()) * 100),
                pct_same_sign_when_both_trend=round(float((tr[both] == zz[both]).mean()) * 100) if both.any() else None,
                pct_opposite_sign_when_both_trend=round(float((tr[both] == -zz[both]).mean()) * 100) if both.any() else None)


def main() -> None:
    out = OUT / "p2"
    out.mkdir(parents=True, exist_ok=True)
    ms, tz, fq = [], [], []
    for coin in CORE:
        b = bars(coin, DEV[0], DEV[1])
        for tf in ("1h", "4h"):
            ms += method_stats(b[tf], tf, coin)
        tz.append(trend_vs_z(b, coin))
        for name, g in H.ALL.items():
            s = g(b)
            s = s[(s["sig_t"] >= D0) & (s["sig_t"] < D1)]
            ref = s["px"].where(s["entry"] == "lmt")
            ref = ref.fillna(pd.Series(_close_at(b, s), index=s.index))
            stop_pct = (ref - s["stop"]).abs() / ref
            fq.append(dict(hyp=name, coin=coin, setups=len(s), per_month=round(len(s) / MONTHS, 1),
                           long=int((s["side"] > 0).sum()), short=int((s["side"] < 0).sum()),
                           limit_orders=int((s["entry"] == "lmt").sum()),
                           median_stop_pct=round(float(stop_pct.median()) * 100, 2),
                           median_stop_atr=round(float(((ref - s["stop"]).abs() / s["atr"]).median()), 2),
                           median_cost_R=round(float((COST_RT / stop_pct).median()), 3)))
    ms, tz, fq = pd.DataFrame(ms), pd.DataFrame(tz), pd.DataFrame(fq)
    ms.to_csv(out / "structure_methods.csv", index=False)
    tz.to_csv(out / "trend_vs_z.csv", index=False)
    fq.to_csv(out / "setup_frequency.csv", index=False)
    agg = fq.groupby("hyp", sort=False).agg(setups_3coins=("setups", "sum"), per_month_3coins=("per_month", lambda s: round(s.sum(), 1)),
                                            long=("long", "sum"), short=("short", "sum"),
                                            limit_orders=("limit_orders", "sum"),
                                            median_stop_pct=("median_stop_pct", "median"),
                                            median_stop_atr=("median_stop_atr", "median"),
                                            median_cost_R=("median_cost_R", "median")).reset_index()
    text = ["### Structure methods (dev, core coins)", md(ms),
            "### 4h DC structural trend vs daily volatility-normalised regime (dev)", md(tz),
            "### Setup frequency per hypothesis (dev, sum of BTC+ETH+SOL; limit setups are orders, not fills)",
            md(agg), "Per coin:", md(fq)]
    (out / "p2_stats.md").write_text("\n\n".join(text) + "\n", encoding="utf-8")
    print("\n\n".join(text))


def _close_at(b: dict, s: pd.DataFrame) -> np.ndarray:
    """Close of the signal bar (the bar ending at sig_t) on the setup's stop timeframe."""
    out = np.full(len(s), np.nan)
    for tf, sec in (("1h", 3600), ("4h", 14400)):
        df = b[tf]
        close_t = epoch_s(df["time"]) + sec
        idx = pd.Series(df["close"].to_numpy(float), index=close_t)
        m = (s["stop_tf"] == tf).to_numpy()
        out[m] = idx.reindex(s["sig_t"].to_numpy()[m]).to_numpy()
    return out


if __name__ == "__main__":
    main()
