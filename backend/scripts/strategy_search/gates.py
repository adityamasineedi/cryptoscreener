"""Stage 2: candidate gates at every risk level, frozen-risk rule, ranking, freeze file (DECLARATIONS.md).

  python -B gates.py screen                 # pre-filter stage-1 CSV (2% risk) -> out/stage2_input.json
  python -B gates.py check --shard i/n      # full gates per variant and risk -> out/stage2_shard<i>.jsonl
  python -B gates.py freeze                 # rank, pick top 3 distinct, write FREEZE_<date>.json
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from datetime import datetime

from ss_common import HERE, OUT, UTC
import search as SR
from sim import Costs

RISKS = [0.005, 0.01, 0.015, 0.02, 0.05]
DD_CAP_PCT = 25.0


def base_gates(m: dict) -> list[str]:
    bad = []
    if m["trades"] < 150: bad.append("trades<150")
    if m["net"] <= 0: bad.append("net<=0")
    if m["folds_pos"] < 3: bad.append("folds<3")
    if m["fall_fold"] <= 0: bad.append("F3<=0")
    if m["fall_win"] <= 0: bad.append("fallwin<=0")
    if m["net"] > 0 and (m["best_coin_share"] or 1) > 0.35: bad.append("coin>35%")
    if m["net"] > 0 and (m["best_month_share"] or 1) > 0.30: bad.append("month>30%")
    return bad


def load_stage1() -> list[dict]:
    rows = []
    for f in glob.glob(str(OUT / "variants_shard*.csv")):
        rows += list(csv.DictReader(open(f, encoding="utf-8")))
    return rows


def cmd_screen() -> None:
    rows = load_stage1()
    vmap = {v["vid"]: v for v in SR.variant_grid()}
    keep = []
    for r in rows:
        f = lambda k: float(r[k]) if r[k] not in ("", "None") else None  # noqa: E731
        if (int(r["trades"]) >= 150 and f("net") > 0 and int(r["folds_pos"]) >= 3 and f("fall_fold") > 0
                and f("fall_win") > 0):
            keep.append(vmap[r["vid"]])
    print("stage1 rows", len(rows), "-> stage2", len(keep))
    (OUT / "stage2_input.json").write_text(json.dumps(keep, indent=1), encoding="utf-8")


def check_one(v: dict) -> dict:
    out = {"vid": v["vid"], "v": v, "risks": {}}
    for risk in RISKS:
        m = SR.run(v, "discovery", risk)
        bad = base_gates(m)
        rec = {"base": {k: m[k] for k in m if k not in ("per_coin", "months")}, "fails": list(bad)}
        if not bad:
            ms = SR.run(v, "discovery", risk, costs=Costs(scale=1.5))
            rec["cost_1.5x_net"] = ms["net"]
            if ms["net"] <= 0: rec["fails"].append("cost1.5x")
            for key in ("stop_k", "r", "trail"):
                if key != "stop_k" and not v.get(key):
                    continue
                for mul in (0.8, 1.2):
                    mm = SR.run(v, "discovery", risk, mult={key: mul})
                    rec[f"{key}x{mul}_net"] = mm["net"]
                    if mm["net"] <= 0: rec["fails"].append(f"{key}x{mul}")
        rec["dd_ok"] = m["max_dd_pct"] <= DD_CAP_PCT
        out["risks"][str(risk)] = rec
    passing = [float(r) for r, rec in out["risks"].items() if not rec["fails"] and rec["dd_ok"]]
    out["frozen_risk"] = max(passing) if passing else None
    return out


def cmd_check(shard: str) -> None:
    i, n = (int(x) for x in shard.split("/"))
    vs = json.load(open(OUT / "stage2_input.json", encoding="utf-8"))[i::n]
    f = OUT / f"stage2_shard{i}.jsonl"
    done = set()
    if f.exists():
        done = {json.loads(l)["vid"] for l in open(f, encoding="utf-8")}
    with open(f, "a", encoding="utf-8") as fh:
        for k, v in enumerate(vs):
            if v["vid"] in done:
                continue
            r = check_one(v)
            fh.write(json.dumps(r) + "\n"); fh.flush()
            print(f"[{shard}] {k + 1}/{len(vs)} {v['vid']} frozen_risk={r['frozen_risk']}", file=sys.stderr, flush=True)


def cmd_freeze() -> None:
    recs = [json.loads(l) for f in glob.glob(str(OUT / "stage2_shard*.jsonl")) for l in open(f, encoding="utf-8")]
    cands = []
    for r in recs:
        if r["frozen_risk"] is None:
            continue
        b = r["risks"][str(r["frozen_risk"])]["base"]
        cands.append((b["usd_per_30d_10k"], r))
    cands.sort(key=lambda x: -x[0])
    pick, seen = [], set()
    for usd, r in cands:
        v = r["v"]
        key = (v["fam"], v["tf"], v["side"])
        if key in seen:
            continue
        seen.add(key)
        pick.append({"label": "CANDIDATE", "vid": v["vid"], "rule": v, "frozen_risk": r["frozen_risk"],
                     "discovery": r["risks"][str(r["frozen_risk"])]})
        if len(pick) == 3:
            break
    if not pick:  # fallback written in DECLARATIONS.md
        rows = [x for x in load_stage1() if int(x["trades"]) >= 150 and float(x["net"]) > 0]
        rows.sort(key=lambda x: -float(x["usd_per_30d_10k"]))
        vmap = {v["vid"]: v for v in SR.variant_grid()}
        for x in rows:
            v = vmap[x["vid"]]
            key = (v["fam"], v["tf"], v["side"])
            if key in seen:
                continue
            seen.add(key)
            m = SR.run(v, "discovery", 0.02)
            pick.append({"label": "NOT_A_CANDIDATE", "vid": v["vid"], "rule": v, "frozen_risk": 0.02,
                         "failed_gates": base_gates(m), "discovery": {"base": {k: m[k] for k in m if k not in ("per_coin",)}}})
            if len(pick) == 3:
                break
    stamp = datetime.now(UTC)
    fz = {"frozen_utc": stamp.isoformat(), "declarations": "DECLARATIONS.md",
          "universe_rule": "rules are coin-agnostic; holdout runs every rule on all 11 holdout coins, 2024-10-09 -> 2026-10-08",
          "accounting": {"engine": "scripts/sleeve_study/sim.py", "entry": "next bar open", "taker": 0.0004,
                         "slippage_per_side": 0.0002, "sensitivity": ["slippage 0.05%", "slippage 0.10%",
                         "1.5x taker+slippage", "funding 0.01%/8h"], "leverage_cap": 3.0, "sleeve_usd": 1000,
                         "withdrawal": "month end, equity above N x $1,000; losses carry", "pyramiding": "none"},
          "regime_map": {"UPTREND": "BULL_TREND + HVT bull reason", "DOWNTREND": "BEAR_TREND + HVT bear reason",
                         "RANGE": "RANGE, HIGH_VOLATILITY_RANGE, LOW_VOLATILITY_COMPRESSION",
                         "NO_TRADE": "CHOPPY, TRANSITION, UNKNOWN"},
          "books": pick, "candidates_passing_all_gates": len(cands)}
    p = HERE / f"FREEZE_{stamp:%Y%m%d}.json"
    if p.exists():
        sys.exit(f"{p} exists; the freeze is written once")
    p.write_text(json.dumps(fz, indent=1), encoding="utf-8")
    print(p, json.dumps([{"vid": b["vid"], "label": b["label"], "risk": b["frozen_risk"],
                          "usd30": b["discovery"]["base"]["usd_per_30d_10k"]} for b in pick], indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["screen", "check", "freeze"])
    ap.add_argument("--shard", default="0/1")
    a = ap.parse_args()
    {"screen": cmd_screen, "check": lambda: cmd_check(a.shard), "freeze": cmd_freeze}[a.cmd]()


if __name__ == "__main__":
    main()
