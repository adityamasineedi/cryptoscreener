"""Phase 2b, step 0: log every regime x direction subset already looked at in Phase 4 (development, core) to
research/experiments.csv as its own configuration, so that every later deflated Sharpe counts them.
Uses only the Phase 3 development trades (research/out/p3/trades_*_dev_core.csv); no new data is read.

Run from repo root:  backend/.venv/Scripts/python.exe research/p2b_cells.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CORE, DEV, OUT  # noqa: E402
from p3_baselines import TRIALS  # noqa: E402
from p4_compare import regime_labels  # noqa: E402
from rstats import metrics  # noqa: E402
from runner import log_experiment, trades_file  # noqa: E402


def label_at(labs: dict, coin: str, sig_t: np.ndarray) -> np.ndarray:
    lt, lab = labs[coin]
    k = np.searchsorted(lt, sig_t, side="right") - 1
    return np.where(k >= 0, lab[np.clip(k, 0, None)], "na")


def main() -> None:
    labs = {c: regime_labels(c) for c in CORE}
    n = 0
    for name in TRIALS:
        t = pd.read_csv(OUT / "p3" / trades_file(name, "dev", "core"))
        t["regime"] = [label_at(labs, c, np.array([s]))[0] for c, s in zip(t["coin"], t["sig_t"])]
        t["dir"] = np.where(t["side"] > 0, "long", "short")
        for (rg, d), g in t.groupby(["regime", "dir"]):
            res = {"name": f"{name}|{d}_{rg}", "segment": "dev", "universe": "core", "pooled": metrics(g, DEV),
                   "per_coin": {}}
            log_experiment(res, "p4_cells")
            n += 1
    print("cells logged:", n)


if __name__ == "__main__":
    main()
