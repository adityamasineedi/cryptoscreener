"""Parity check: market-entry trades resolved by research/engine.py vs the existing simulator
(backend/scripts/sleeve_study/sim.py), funding off. Uses H1 and H5 (4h setups, 1h execution) on BTC, dev segment.

Run from repo root:  backend/.venv/Scripts/python.exe research/p3_parity.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import importlib.util  # noqa: E402

import hypotheses as H  # noqa: E402

_spec = importlib.util.spec_from_file_location("sleeve_sim", HERE.parent / "backend" / "scripts" / "sleeve_study" / "sim.py")
sim = importlib.util.module_from_spec(_spec)
sys.modules["sleeve_sim"] = sim
_spec.loader.exec_module(sim)
from common import DEV, OUT, SLIP, TAKER, bars  # noqa: E402
from engine import Exec, purge, resolve  # noqa: E402


def main() -> None:
    coin = "BTCUSDT"
    b = bars(coin, DEV[0], DEV[1])
    ex = Exec.of(b["1h"], "1h")
    sb = sim.Bars()
    sb.put(coin, "1h", b["1h"].to_dict("records"))
    out = []
    for hyp in ("H1", "H5"):
        st = purge(H.ALL[hyp](b), DEV)
        mine = {int(s["sig_t"]): resolve(s, ex, fund=0.0) for s in st.to_dict("records")}
        sigs = [{"sid": f"{hyp}_{i}", "coin": coin, "tf": "4h", "side": "LONG" if s["side"] > 0 else "SHORT",
                 "signal_close": int(s["sig_t"]), "stop": float(s["stop"]), "target_fn": (lambda f, st_, R=s["tgt"]: f + R * (f - st_)),
                 "max_hold_s": int(s["hold_s"])} for i, s in enumerate(st.to_dict("records"))]
        cfg = sim.Config(sleeve_base=1e9, risk_frac=0.001, leverage=1e6, account_risk_cap=None, withdraw=False,
                         costs=sim.Costs(taker=TAKER, slip=SLIP, funding_flat_8h=0.0))
        res = sim.simulate(sigs, sb, [coin], DEV[0], DEV[1], cfg)
        n_cmp = n_bad = 0
        worst = 0.0
        for tr in res["trades"]:
            m = mine.get(int(tr["signal_close"]))
            if m is None or "skip" in m:
                n_bad += 1
                continue
            n_cmp += 1
            d = abs(m["R"] - tr["r"])
            worst = max(worst, d)
            if d > 1e-6 or m["exit_t"] != tr["exit_t"]:
                n_bad += 1
        out.append(dict(hyp=hyp, sim_trades=len(res["trades"]), compared=n_cmp, mismatches=n_bad,
                        max_abs_R_diff=f"{worst:.2e}"))
    df = pd.DataFrame(out)
    (OUT / "p3").mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "p3" / "parity.csv", index=False)
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
