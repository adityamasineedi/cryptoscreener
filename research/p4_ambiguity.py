"""Phase 4 robustness: optimistic upper bound for intrabar stop/target ambiguity.

The engine assumes the stop is hit first when one execution bar reaches both stop and target (and that a limit
fill bar reaching the stop is a loss). This script re-scores every Phase 3 trade that ended 'stop' on a bar that
also reached the target as a target exit, and also sets every cost to zero, to bound how much the conservative
assumptions could be hiding.

Run from repo root:  backend/.venv/Scripts/python.exe research/p4_ambiguity.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CORE, DEV, OUT, SLIP, TAKER  # noqa: E402
from engine import Exec  # noqa: E402
from p3_baselines import TRIALS  # noqa: E402
from runner import coin_bars, trades_file  # noqa: E402


def main() -> None:
    ex = {(c, tf): Exec.of(coin_bars(c, DEV)[tf], tf) for c in CORE for tf in ("1h", "15m")}
    rows = []
    for name in TRIALS:
        t = pd.read_csv(OUT / "p3" / trades_file(name, "dev", "core"))
        amb = np.zeros(len(t), bool)
        for i, r in enumerate(t.itertuples()):
            if r.why != "stop":
                continue
            e = ex[(r.coin, r.exec_tf)]
            j = int(np.searchsorted(e.t, r.exit_t - e.sec))
            amb[i] = (e.h[j] >= r.tgt) if r.side > 0 else (e.l[j] <= r.tgt)
        R_opt = t["R"].to_numpy().copy()
        tgtR_net = t["tgt_R"] - (TAKER * (t["fill"] + t["tgt"]) + 0) / t["rpu"]  # target exit, fees kept
        R_opt[amb] = tgtR_net[amb] - SLIP * t["tgt"][amb] / t["rpu"][amb]
        rows.append({"config": name, "trades": len(t), "ambiguous_stop_bars": int(amb.sum()),
                     "exp_R_net": round(float(t["R"].mean()), 4),
                     "exp_R_net_target_first": round(float(R_opt.mean()), 4),
                     "exp_R_zero_cost": round(float(t["R_gross"].mean()), 4),
                     "exp_R_zero_cost_target_first": round(float((R_opt + t["cost_R"].to_numpy()).mean()), 4)})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "p4" / "ambiguity_bound.csv", index=False)
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
