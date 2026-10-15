"""Phase 4: statistical comparison and the pre-declared "promising" gates (development segment, core pooled).
Also runs every Phase 3 configuration unchanged on Tier 2 (dev) as cross-coin evidence (no fitting), buy-and-hold
for context, and a descriptive regime x direction breakdown of the core trades (not used for selection).

Run from repo root:  backend/.venv/Scripts/python.exe research/p4_compare.py
Writes research/out/p4/p4_tables.md and p4_gates.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import structure as S  # noqa: E402
from common import CORE, DEV, OUT, TIER2, epoch_s  # noqa: E402
from p3_baselines import TRIALS  # noqa: E402
from rstats import deflated_sharpe  # noqa: E402
from runner import coin_bars, n_trials, run, trades_file  # noqa: E402


def md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join("" if (isinstance(v, float) and np.isnan(v)) or v is None else str(v) for v in r)
            + " |" for r in df.itertuples(index=False)]
    return "\n".join(out)


def regime_labels(coin: str) -> tuple[np.ndarray, np.ndarray]:
    d = coin_bars(coin, DEV)["1d"]
    z = S.daily_z(d["close"].to_numpy(float))
    lab = np.where(np.isnan(z), "na", np.where(z >= 1, "bull", np.where(z <= -1, "bear", "side")))
    return epoch_s(d["time"]) + 86400, lab   # label usable from the day's close


def main() -> None:
    p3 = json.loads((OUT / "p3" / "p3_results.json").read_text(encoding="utf-8"))
    tier2 = {name: run(name, gen, TIER2, DEV, "dev", "tier2", "p4", null_draws=0) for name, gen in TRIALS.items()}
    N, var_sr = n_trials()
    gates, comp = [], []
    for name, r in p3.items():
        p = r["pooled"]
        pc = r["per_coin"]
        dsr = deflated_sharpe(p["sr_trade"], p["trades"], p["skew"], p["kurt"], N, var_sr)
        pos_coins = sum(1 for c in CORE if pc.get(c, {}).get("exp_R", -1) > 0)
        g = {"config": name, "trades": p["trades"], "g1_trades>=100": p["trades"] >= 100,
             "exp_R": p["exp_R"], "g2_exp>=0.10": p["exp_R"] >= 0.10,
             "boot95_lo": p["boot95_lo"], "g3_boot95lo>0": p["boot95_lo"] > 0,
             "coins_positive": pos_coins, "g4_>=2of3": pos_coins >= 2,
             "null_p": p["null_p"], "g5_null_p<0.05": p["null_p"] < 0.05,
             "dsr": dsr["dsr"], "g6_dsr>=0.90": dsr["dsr"] >= 0.90}
        g["PROMISING"] = all(v for k, v in g.items() if k.startswith("g"))
        gates.append(g)
        t2 = tier2[name]["pooled"]
        comp.append({"config": name, "trades": p["trades"], "per_month": p["trades_per_month"],
                     "win%": p["win_rate"], "avg_win_R": p["avg_win_R"], "avg_loss_R": p["avg_loss_R"],
                     "exp_R_gross": p["exp_R_gross"], "cost_R": p["cost_R"], "exp_R_net": p["exp_R"],
                     "boot95": f"[{p['boot95_lo']}, {p['boot95_hi']}]", "pf": p["pf"],
                     "net_ret_%": p["net_ret_pct"], "maxdd_%": p["maxdd_pct"], "max_consec_L": p["max_consec_losses"],
                     "months_pos_%": p["months_pos_pct"], "worst_month_%": p["month_worst_pct"],
                     "exposure_%": p["exposure_pct"], "sr_trade": p["sr_trade"],
                     "BTC_R": pc.get("BTCUSDT", {}).get("exp_R"), "ETH_R": pc.get("ETHUSDT", {}).get("exp_R"),
                     "SOL_R": pc.get("SOLUSDT", {}).get("exp_R"), "long_R": r["by_side"].get("long"),
                     "short_R": r["by_side"].get("short"), "null_mean_R": p["null_mean"],
                     "tier2_trades": t2.get("trades"), "tier2_exp_R": t2.get("exp_R"), "tier2_pf": t2.get("pf")})
    gates, comp = pd.DataFrame(gates), pd.DataFrame(comp)
    # buy and hold, core dev
    bh = []
    for c in CORE:
        h1 = coin_bars(c, DEV)["1h"]
        t = epoch_s(h1["time"])
        w = h1[(t >= int(DEV[0].timestamp())) & (t < int(DEV[1].timestamp()))]
        bh.append({"coin": c, "buy_hold_%": round((w["close"].iloc[-1] / w["open"].iloc[0] - 1) * 100, 1)})
    # descriptive regime x direction breakdown (core dev trades)
    rows = []
    labs = {c: regime_labels(c) for c in CORE}
    for name in p3:
        f = OUT / "p3" / trades_file(name, "dev", "core")
        if not f.exists():
            continue
        t = pd.read_csv(f)
        reg = []
        for c, s in zip(t["coin"], t["sig_t"]):
            lt, lab = labs[c]
            k = int(np.searchsorted(lt, s, side="right")) - 1
            reg.append(lab[k] if k >= 0 else "na")
        t["regime"] = reg
        t["dir"] = np.where(t["side"] > 0, "long", "short")
        g = t.groupby(["regime", "dir"])["R"].agg(["count", "mean"]).round(3)
        for (rg, d), v in g.iterrows():
            rows.append({"config": name, "regime": rg, "dir": d, "n": int(v["count"]), "exp_R": v["mean"]})
    rd = pd.DataFrame(rows).pivot_table(index="config", columns=["regime", "dir"], values=["exp_R", "n"])
    rd.columns = [f"{a}_{b}_{c}" for a, b, c in rd.columns]
    rd = rd.reset_index()
    keep = ["config"] + [f"exp_R_{r}_{d}" for r in ("bull", "side", "bear") for d in ("long", "short")] + \
           [f"n_{r}_{d}" for r in ("bull", "side", "bear") for d in ("long", "short")]
    rd = rd[[k for k in keep if k in rd.columns]]
    text = [f"Trials counted for the deflated Sharpe: N = {N} distinct configurations in research/experiments.csv; "
            f"variance of per-trade Sharpe across development trials = {var_sr:.6f}.",
            "### Gates (development, core pooled)", md(gates),
            "### Comparison (development, core pooled; Tier-2 = same rules, development, never fitted)", md(comp),
            "### Buy and hold, development segment", md(pd.DataFrame(bh)),
            "### Descriptive: net R by regime at signal (daily z) and direction, core development trades "
            "(not used for any selection)", md(rd)]
    (OUT / "p4").mkdir(parents=True, exist_ok=True)
    (OUT / "p4" / "p4_tables.md").write_text("\n\n".join(text) + "\n", encoding="utf-8")
    (OUT / "p4" / "p4_gates.json").write_text(gates.to_json(orient="records", indent=1), encoding="utf-8")
    print("\n\n".join(text))


if __name__ == "__main__":
    main()
