"""Phase 3: baseline backtests of the 10 approved trials (H1-H8 + H2 fractal/zigzag) on the core coins,
development segment. Writes research/out/p3/p3_results.json and trades_*.csv; logs experiments.csv.

Run from repo root:  backend/.venv/Scripts/python.exe research/p3_baselines.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hypotheses as H  # noqa: E402
from common import CORE, DEV, OUT  # noqa: E402
from runner import run  # noqa: E402

TRIALS = {**{k: v for k, v in H.ALL.items()},
          "H2:fractal": lambda b: H.h2(b, "fractal"),
          "H2:zigzag": lambda b: H.h2(b, "zz")}


def main() -> None:
    out = {}
    for name, gen in TRIALS.items():
        r = run(name, gen, CORE, DEV, "dev", "core", "p3")
        out[name] = r
        p = r["pooled"]
        print(f"{name:11s} trades {p.get('trades'):4d}  exp {p.get('exp_R')}R  pf {p.get('pf')}  "
              f"boot95lo {p.get('boot95_lo')}  null_p {p.get('null_p')}  {r['seconds']}s", flush=True)
    (OUT / "p3").mkdir(parents=True, exist_ok=True)
    (OUT / "p3" / "p3_results.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
