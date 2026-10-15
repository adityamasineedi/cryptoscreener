"""Research screen (Agents 3/4): every variant on the research window only.

Writes out/research_screen.json (all variants, pass or fail) and out/research_screen.md.
Usage: python -B a34_screen.py [--workers 6]
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime

from common import OUT, RESEARCH, VALIDATION, UTC
import a5_eval as E

_S: dict = {}


def _init() -> None:
    _S["signals"] = E.load_signals("P1")
    _S["bars"] = E.get_bars(VALIDATION[1])


def _one(v: dict) -> dict:
    r = E.run_variant(v, _S["signals"], RESEARCH, _S["bars"])
    folds = {}
    for name, a, b in E.FOLDS:
        ts = [t for t in r["trades"] if a.timestamp() <= t["entry_t"] < b.timestamp()]
        folds[name] = E.stats(ts)["net"]
    a = r["all"]
    passed = a["trades"] >= 30 and a["net"] > 0 and (a["pf"] or 0) > 1.2
    return {**v, "research": a, "per_coin": r["per_coin"], "folds": folds, "skips": r["skips"],
            "folds_positive": sum(1 for x in folds.values() if x > 0), "screen_pass": passed,
            "fail_reasons": [w for w, bad in (("trades<30", a["trades"] < 30), ("net<=0", a["net"] <= 0),
                                              ("pf<=1.2", (a["pf"] or 0) <= 1.2)) if bad]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    variants = E.enumerate_variants(E.load_signals("P1"))
    print("variants", len(variants), flush=True)
    rows = []
    with ProcessPoolExecutor(max_workers=a.workers, initializer=_init) as ex:
        futs = [ex.submit(_one, v) for v in variants]
        for i, f in enumerate(as_completed(futs), 1):
            rows.append(f.result())
            if i % 25 == 0:
                print(i, flush=True)
    rows.sort(key=lambda r: (r["family"], -r["research"]["net"]))
    (OUT / "research_screen.json").write_text(json.dumps({"generated_utc": datetime.now(UTC).isoformat(),
                                                          "window": [RESEARCH[0].isoformat(), RESEARCH[1].isoformat()],
                                                          "variants": rows}, indent=1), encoding="utf-8")
    lines = ["| family | variant | trades | net | PF | folds + | F1 | F2 | F3 | F4 | pass |", "|" + "---|" * 11]
    for r in rows:
        x = r["research"]
        lines.append(f"| {r['family']} | {r['vid']} | {x['trades']} | {x['net']:+.0f} | {x['pf']} | {r['folds_positive']}/4 | "
                     + " | ".join(f"{r['folds'][k]:+.0f}" for k in ('F1', 'F2', 'F3', 'F4'))
                     + f" | {'PASS' if r['screen_pass'] else ','.join(r['fail_reasons'])} |")
    (OUT / "research_screen.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("passed", sum(r["screen_pass"] for r in rows), "of", len(rows))


if __name__ == "__main__":
    main()
