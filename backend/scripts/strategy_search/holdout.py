"""Holdout: run the frozen books once on the 11 holdout coins (2024-10-09 -> 2026-10-08). Refuses a second run.

  python -B holdout.py prep     # regimes + raw signals for holdout coins (allowed only after a FREEZE file exists)
  python -B holdout.py run      # the one holdout run -> out/holdout_result.json
"""
from __future__ import annotations

import glob
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from ss_common import HERE, OUT, UTC, frozen, universe
import search as SR
from sim import Costs

RESULT = OUT / "holdout_result.json"
COLUMNS = {"base": Costs(), "slip_0.05pct": Costs(slip=0.0005), "slip_0.10pct": Costs(slip=0.0010),
           "cost_1.5x": Costs(scale=1.5), "funding_0.01pct_8h": Costs(funding_flat_8h=0.0001)}


def freeze_file() -> Path:
    fs = sorted(glob.glob(str(HERE / "FREEZE_*.json")))
    if not fs:
        sys.exit("no FREEZE file")
    return Path(fs[-1])


def cmd_prep() -> None:
    if not frozen():
        sys.exit("freeze first")
    py = sys.executable
    subprocess.run([py, "-B", "regimes_ext.py", "holdout"], cwd=HERE, check=True)
    subprocess.run([py, "-B", "search.py", "prep", "holdout"], cwd=HERE, check=True)
    fz = json.load(open(freeze_file(), encoding="utf-8"))
    if any(b["rule"]["fam"].startswith("ENG_") for b in fz["books"]):
        subprocess.run([py, "-B", "engine_prep.py", "holdout"], cwd=HERE, check=True)


def cmd_run() -> None:
    if RESULT.exists():
        sys.exit("holdout already run; it is not reopened")
    fpath = freeze_file()
    fz = json.load(open(fpath, encoding="utf-8"))
    out = {"run_utc": datetime.now(UTC).isoformat(), "freeze_file": fpath.name,
           "freeze_sha256": hashlib.sha256(fpath.read_bytes()).hexdigest(), "books": []}
    for b in fz["books"]:
        v, risk = b["rule"], b["frozen_risk"]
        cols = {name: SR.run(v, "holdout", risk, costs=c) for name, c in COLUMNS.items()}
        disc = SR.run(v, "discovery", risk)
        out["books"].append({"vid": b["vid"], "label": b["label"], "risk": risk, "holdout": cols, "discovery": disc})
        print(b["vid"], {k: (m["usd_per_30d_10k"], m["trades"]) for k, m in cols.items()}, flush=True)
    RESULT.write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    {"prep": cmd_prep, "run": cmd_run}[sys.argv[1]]()
