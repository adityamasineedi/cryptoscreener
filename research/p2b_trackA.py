"""Phase 2b Track A: the three pre-registered regime subsets (research/out/p2b/PREREG.md), run ONCE on the
validation segment, core + Tier 2 pooled. Never re-run on development. DSR with N = 70 (10 Phase 3 trials +
60 regime cells), Sharpe variance from those 70 development rows.

Run from repo root:  backend/.venv/Scripts/python.exe research/p2b_trackA.py
Writes research/out/p2b/trackA.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hypotheses as H  # noqa: E402
import structure as S  # noqa: E402
from common import CORE, OUT, TIER2, VAL, epoch_s  # noqa: E402
from rstats import deflated_sharpe  # noqa: E402
from runner import EXP, run  # noqa: E402

SUBSETS = {"A1": ("H2", 1), "A2": ("H8", -1), "A3": ("H5", -1)}


def side_regime_filter(direction: int):
    def f(s: pd.DataFrame, b: dict) -> np.ndarray:
        d = b["1d"]
        z = S.daily_z(d["close"].to_numpy(float))
        usable_from = epoch_s(d["time"]) + 86400
        k = np.searchsorted(usable_from, s["sig_t"].to_numpy(), side="right") - 1
        zz = np.where(k >= 0, z[np.clip(k, 0, None)], np.nan)
        return ((s["side"].to_numpy() == direction) & (np.abs(zz) < 1)).astype(bool)
    return f


def main() -> None:
    e = pd.read_csv(EXP)
    base = e[(e["coin"] == "POOLED") & (e["segment"] == "dev") & (e["universe"] == "core")
             & (e["phase"].isin(["p3", "p4_cells"]))].drop_duplicates("config")
    N, var_sr = len(base), float(np.nanvar(base["sr_trade"].astype(float), ddof=1))
    out = {"N": N, "var_sr": var_sr}
    for sid, (hyp, d) in SUBSETS.items():
        r = run(f"{sid}:{hyp}_{'long' if d > 0 else 'short'}_side", H.ALL[hyp], CORE + TIER2, VAL, "val",
                "core+tier2", "p2b", filt=side_regime_filter(d), null_draws=300)
        p = r["pooled"]
        dsr = deflated_sharpe(p["sr_trade"], p["trades"], p["skew"], p["kurt"], N, var_sr) if p.get("trades", 0) > 2 \
            else {"dsr": None, "sr0": None}
        g = {"trades>=100": p.get("trades", 0) >= 100, "exp>=0.10": (p.get("exp_R") or -9) >= 0.10,
             "boot95lo>0": (p.get("boot95_lo") or -9) > 0, "dsr>=0.90": (dsr["dsr"] or 0) >= 0.90}
        r["dsr"], r["gates"], r["PASS"] = dsr, g, all(g.values())
        out[sid] = r
        print(sid, hyp, p.get("trades"), p.get("exp_R"), p.get("boot95_lo"), dsr, "PASS" if r["PASS"] else "fail",
              flush=True)
    (OUT / "p2b").mkdir(parents=True, exist_ok=True)
    (OUT / "p2b" / "trackA.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
