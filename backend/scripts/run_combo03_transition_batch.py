#!/usr/bin/env python3
"""Run COMBO_03 validation one symbol/combo at a time (separate processes)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "LINKUSDT",
    "DOGEUSDT",
    "SUIUSDT",
]
COMBOS = [
    "COMBO_03_TRANSITION",
    "COMBO_03_TRANSITION_B",
    "COMBO_03_TRANSITION_C",
]

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "run_combo03_transition_validation.py"


def main() -> int:
    py = sys.executable
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env["PYTHONPATH"] = str(ROOT.parent)
    for cid in COMBOS:
        for sym in SYMBOLS:
            out_marker = (
                ROOT.parent
                / "reports"
                / "combo03_transition"
                / cid.lower()
                / f"{sym}_result.json"
            )
            if out_marker.exists():
                print(f"SKIP existing {cid}/{sym}", flush=True)
                continue
            print(f"RUN {cid}/{sym}", flush=True)
            rc = subprocess.call(
                [
                    py,
                    "-u",
                    str(SCRIPT),
                    "--combos",
                    cid,
                    "--symbols",
                    sym,
                ],
                cwd=str(ROOT.parent),
                env=env,
            )
            if rc != 0:
                print(f"FAIL {cid}/{sym} rc={rc}", flush=True)
                return rc
    # Final aggregate pass over all completed artifacts is already written per run;
    # re-run last combo with all symbols if every marker exists to rebuild comparison.
    print("Batch complete.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
