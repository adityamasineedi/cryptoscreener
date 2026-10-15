"""Phase 2b Track B (pre-registered in research/out/p2b/PREREG.md): 12 trials on the development segment, core.
B1 TSMOM {1d,4h} x N {20,40,60}; B2 H5/H8 with 1.5xATR4 stop, 3R/5R target, 10-day hold; B3 H5/H8 maker entry.
Phase 4 gates; DSR N = every configuration in experiments.csv (Sharpe variance over dev-core configs with >= 30 trades).

Run from repo root:  backend/.venv/Scripts/python.exe research/p2b_trackB.py
Writes research/out/p2b/trackB.json and trackB_tables.md.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hypotheses as H  # noqa: E402
import structure as S  # noqa: E402
import tsmom as TS  # noqa: E402
from common import CORE, DEV, OUT, TF_SEC, epoch_s  # noqa: E402
from rstats import block_bootstrap, deflated_sharpe, metrics  # noqa: E402
from runner import coin_bars, log_compute, log_experiment, n_trials, run  # noqa: E402

MAKER_FEE = 0.0002


def _close_and_atr4(s: pd.DataFrame, b: dict) -> tuple[np.ndarray, np.ndarray]:
    """Close of the signal bar (setup tf) and ATR(14,4h) of the last 4h bar closed by the signal."""
    tf = "4h" if s["stop_tf"].iloc[0] == "4h" else "1h"
    df = b[tf]
    ct = epoch_s(df["time"]) + TF_SEC[tf]
    close = pd.Series(df["close"].to_numpy(float), index=ct).reindex(s["sig_t"].to_numpy()).to_numpy()
    h4 = b["4h"]
    a4 = S.atr(*(h4[k].to_numpy(float) for k in ("high", "low", "close")))
    k = np.searchsorted(epoch_s(h4["time"]) + 14400, s["sig_t"].to_numpy(), side="right") - 1
    return close, np.where(k >= 0, a4[np.clip(k, 0, None)], np.nan)


def b2_transform(target_R: float):
    def f(s: pd.DataFrame, b: dict) -> pd.DataFrame:
        c, a4 = _close_and_atr4(s, b)
        s = s.assign(stop=c - s["side"] * 1.5 * a4, tgt_kind="R", tgt=target_R, hold_s=240 * 3600)
        return s[~np.isnan(s["stop"])]
    return f


def b3_transform(s: pd.DataFrame, b: dict) -> pd.DataFrame:
    c, _ = _close_and_atr4(s, b)
    bar = TF_SEC["4h" if s["stop_tf"].iloc[0] == "4h" else "1h"]
    return s.assign(entry="lmt", px=c, valid_s=2 * bar, cancel_px=np.nan, fill_strict=True, entry_fee=MAKER_FEE,
                    entry_slip=0.0)


def run_tsmom(tf: str, n: int) -> dict:
    t0 = time.time()
    name = f"B1:tsmom_{tf}_N{n}"
    data = {c: coin_bars(c, DEV)[tf] for c in CORE}
    raw = pd.concat([TS.coin_trades(data[c], tf, n, DEV, c) for c in CORE], ignore_index=True)
    tr = TS.book(raw)
    res = {"name": name, "segment": "dev", "universe": "core", "setups": len(raw), "candidates": len(raw)}
    p = metrics(tr, DEV)
    p.update(block_bootstrap(tr))
    p.update(TS.null(tr, data, DEV, tf))
    closes = {c: (epoch_s(d["time"]) + TF_SEC[tf], d["close"].to_numpy(float)) for c, d in data.items()}
    p["maxdd_mtm_pct"] = round(TS.mtm_drawdown(tr, closes) * 100, 2)
    p["avg_hold_days"] = round(float(((tr["exit_t"] - tr["entry_t"]) / 86400).mean()), 2)
    res["pooled"] = p
    res["per_coin"] = {c: metrics(tr[tr["coin"] == c], DEV) for c in CORE}
    res["by_side"] = {("long" if s > 0 else "short"): round(float(tr.loc[tr["side"] == s, "R"].mean()), 4)
                      for s in (1, -1) if (tr["side"] == s).any()}
    res["seconds"] = round(time.time() - t0, 1)
    tr.to_csv(OUT / "p2b" / f"trades_{name.replace(':', '_')}_dev_core.csv", index=False)
    log_experiment(res, "p2b")
    log_compute("p2b", f"{name} dev core", res["seconds"])
    return res


def main() -> None:
    (OUT / "p2b").mkdir(parents=True, exist_ok=True)
    out = {}
    for tf in ("1d", "4h"):
        for n in (20, 40, 60):
            r = run_tsmom(tf, n)
            out[r["name"]] = r
            print(r["name"], r["pooled"]["trades"], r["pooled"]["exp_R"], r["pooled"].get("boot95_lo"), flush=True)
    for hyp in ("H5", "H8"):
        for R in (3.0, 5.0):
            name = f"B2:{hyp}_atr4stop_{int(R)}R_10d"
            out[name] = run(name, H.ALL[hyp], CORE, DEV, "dev", "core", "p2b", transform=b2_transform(R))
            print(name, out[name]["pooled"]["trades"], out[name]["pooled"]["exp_R"], flush=True)
    for hyp in ("H5", "H8"):
        name = f"B3:{hyp}_maker"
        r = run(name, H.ALL[hyp], CORE, DEV, "dev", "core", "p2b", transform=b3_transform)
        r["fill_rate"] = round(r["candidates"] / r["setups"], 3) if r["setups"] else None
        out[name] = r
        print(name, r["pooled"]["trades"], r["pooled"]["exp_R"], "fill", r["fill_rate"], flush=True)
    N, var_sr = n_trials()
    gates = []
    for name, r in out.items():
        p, pc = r["pooled"], r["per_coin"]
        dsr = deflated_sharpe(p["sr_trade"], p["trades"], p["skew"], p["kurt"], N, var_sr)
        pos = sum(1 for c in CORE if (pc.get(c, {}).get("exp_R") or -1) > 0)
        g = {"config": name, "trades": p["trades"], "exp_R": p["exp_R"], "exp_R_gross": p["exp_R_gross"],
             "cost_R": p["cost_R"], "boot95": f"[{p['boot95_lo']}, {p['boot95_hi']}]", "pf": p["pf"],
             "per_month": p["trades_per_month"], "maxdd_%": p.get("maxdd_mtm_pct", p["maxdd_pct"]),
             "net_ret_%": p["net_ret_pct"], "coins_pos": pos, "null_mean": p.get("null_mean"),
             "null_p": p.get("null_p"), "dsr": dsr["dsr"], "fill_rate": r.get("fill_rate"),
             "BTC_R": pc.get("BTCUSDT", {}).get("exp_R"), "ETH_R": pc.get("ETHUSDT", {}).get("exp_R"),
             "SOL_R": pc.get("SOLUSDT", {}).get("exp_R"), "long_R": r["by_side"].get("long"),
             "short_R": r["by_side"].get("short")}
        g["PASS"] = bool(p["trades"] >= 100 and p["exp_R"] >= 0.10 and (p["boot95_lo"] or -9) > 0 and pos >= 2
                         and (p.get("null_p") or 1) < 0.05 and dsr["dsr"] >= 0.90)
        gates.append(g)
    gdf = pd.DataFrame(gates)
    cols = list(gdf.columns)
    md = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join("" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v) for v in row) + " |"
           for row in gdf.itertuples(index=False)]
    head = f"DSR: N = {N} configurations in research/experiments.csv; Sharpe variance (dev core, >=30 trades) = {var_sr:.5f}."
    (OUT / "p2b" / "trackB_tables.md").write_text(head + "\n\n" + "\n".join(md) + "\n", encoding="utf-8")
    (OUT / "p2b" / "trackB.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(head)
    print(gdf[["config", "trades", "exp_R", "exp_R_gross", "cost_R", "boot95", "coins_pos", "null_p", "dsr",
               "fill_rate", "PASS"]].to_string(index=False))


if __name__ == "__main__":
    main()
