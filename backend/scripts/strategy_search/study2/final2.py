"""One-time historical re-check of the frozen study-2 books on the TEST split (2026-03-24 -> 2026-10-13).

The split was previously inspected by other studies (EXPOSURE_LEDGER.json): this run is labelled contaminated and
cannot produce a target pass. It is run once (refuses a second run) and never used to rank, drop, add, retune or rescale.

  python -B final2.py run      -> out/final_recheck.json, out/ledger_<book>_<window>.csv
"""
from __future__ import annotations

import csv
import glob
import hashlib
import json
import random
import statistics
import sys
from datetime import datetime, timezone

from common2 import HERE, OUT, RESEARCH, TEST, VALIDATION
from select2 import COSTS, SEED, run_combo

UTC = timezone.utc
RESULT = OUT / "final_recheck.json"
LEDGER_COLS = ["sid", "coin", "tf", "side", "signal_close", "entry_t", "exit_t", "fill", "stop0", "risk_usd", "adds",
               "partial", "gross", "fees", "exec", "funding", "net", "r", "reason", "regime", "hold_h"]


def freeze_books() -> tuple[list[dict], list[str]]:
    fp = sorted(glob.glob(str(HERE / "FREEZE_S2_PRIMARY_*.json")))
    fb = sorted(glob.glob(str(HERE / "FREEZE_S2_BACKUPS_*.json")))
    if not fp:
        sys.exit("no freeze")
    p = json.load(open(fp[-1], encoding="utf-8"))
    b = json.load(open(fb[-1], encoding="utf-8")) if fb else {"books": []}
    return [p["book"]] + b["books"], [fp[-1], fb[-1] if fb else ""]


def bootstrap(daily: list[float], block: int, n_paths: int = 5000, horizon: int = 30, seed: int = SEED) -> dict:
    rng = random.Random(seed + block)
    if len(daily) < block:
        return {}
    sums = []
    for _ in range(n_paths):
        path = []
        while len(path) < horizon:
            s = rng.randrange(0, len(daily) - block + 1)
            path += daily[s:s + block]
        sums.append(sum(path[:horizon]))
    sums.sort()
    q = lambda x: round(sums[int(x * (len(sums) - 1))], 2)  # noqa: E731
    return {"block_days": block, "paths": n_paths, "p_ge_500": round(sum(s >= 500 for s in sums) / len(sums), 4),
            "p_ge_1000": round(sum(s >= 1000 for s in sums) / len(sums), 4), "p_le_0": round(sum(s <= 0 for s in sums)
                                                                                           / len(sums), 4),
            "q05": q(0.05), "q50": q(0.5), "q95": q(0.95)}


def daily_pnl(res: dict) -> list[float]:
    out, prev = [], None
    for t, eq, cf in res["curve"]:
        if t % 86400 == 0:
            if prev is not None:
                out.append(cf - prev)
            prev = cf
    return out


def write_ledger(name: str, res: dict) -> str:
    f = OUT / f"ledger_{name}.csv"
    with open(f, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_COLS, extrasaction="ignore")
        w.writeheader()
        for t in res["trades"]:
            row = dict(t)
            for k in ("signal_close", "entry_t", "exit_t"):
                row[k] = datetime.fromtimestamp(t[k], UTC).isoformat()
            w.writerow(row)
    return f.name


def evaluate(book: dict, window, label: str, columns: bool) -> dict:
    vs = sorted(book["components"], key=lambda v: v["priority"])
    out = {}
    risks = sorted({book["selected_risk"], book["benchmark_risk"]})
    for risk in risks:
        res, m = run_combo(vs, window, risk=risk, coins=book["coins"], ten_k=True)
        rec = {"base": m, "ledger": write_ledger(f"{book['id']}_{label}_{risk}", res),
               "bootstrap": [bootstrap(daily_pnl(res), b) for b in (5, 10, 20)]}
        if columns:
            for cn, c in COSTS.items():
                if cn == "base":
                    continue
                _, mc = run_combo(vs, window, risk=risk, costs=c, coins=book["coins"], ten_k=True)
                rec[cn] = mc
        out[str(risk)] = rec
    return out


def cmd_run() -> None:
    if RESULT.exists():
        sys.exit("historical re-check already run; it is not reopened")
    books, files = freeze_books()
    out = {"run_utc": datetime.now(UTC).isoformat(), "label": "HISTORICAL RE-CHECK (CONTAMINATED WINDOW)",
           "window": [TEST[0].isoformat(), TEST[1].isoformat()],
           "freeze_files": {f.split("study2")[-1]: hashlib.sha256(open(f, "rb").read()).hexdigest() for f in files if f},
           "books": []}
    for b in books:
        rec = {"role": b["role"], "id": b["id"], "status": b["status"], "selected_risk": b["selected_risk"],
               "coins": b["coins"], "test": evaluate(b, TEST, "test", True),
               "validation": evaluate(b, VALIDATION, "validation", False),
               "research": evaluate(b, RESEARCH, "research", False)}
        out["books"].append(rec)
        sel = rec["test"][str(b["selected_risk"])]["base"]
        print(b["role"], b["id"], "test $/30d", sel["usd30_10k"], "trades", sel["trades"], flush=True)
    RESULT.write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    {"run": cmd_run}[sys.argv[1]]()
