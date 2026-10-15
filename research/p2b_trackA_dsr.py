"""Track A DSR sensitivity, computed from the saved single validation run (research/out/p2b/trackA.json); nothing
is re-run. As registered, the Sharpe variance over the 70 development configurations is dominated by one 3-trade
cell (H4|long_bear, per-trade Sharpe -25.9). Sensitivity: the variance over configurations with >= 30 trades; N stays 70.

Run from repo root:  backend/.venv/Scripts/python.exe research/p2b_trackA_dsr.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT  # noqa: E402
from rstats import deflated_sharpe  # noqa: E402
from runner import EXP  # noqa: E402

e = pd.read_csv(EXP)
b = e[(e["coin"] == "POOLED") & (e["segment"] == "dev") & (e["universe"] == "core") & e["phase"].isin(["p3", "p4_cells"])]
v30 = float(np.nanvar(b.loc[b["trades"] >= 30, "sr_trade"], ddof=1))
res = json.loads((OUT / "p2b" / "trackA.json").read_text(encoding="utf-8"))
rows = {}
for sid in ("A1", "A2", "A3"):
    p = res[sid]["pooled"]
    rows[sid] = {"registered": res[sid]["dsr"],
                 "var_n>=30": deflated_sharpe(p["sr_trade"], p["trades"], p["skew"], p["kurt"], res["N"], v30)}
out = {"N": res["N"], "var_registered": res["var_sr"], "var_n>=30": v30, "dsr": rows}
(OUT / "p2b" / "trackA_dsr.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print(json.dumps(out, indent=1))
