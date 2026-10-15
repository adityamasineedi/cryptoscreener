"""Study-2 validation selection and freeze (PROTOCOL.md section 8).

  python -B select2.py pool        # gate-passing pool, best per base, overlap dedupe -> out/pool.json
  python -B select2.py build       # P1/P2/P3 on research + coin choice -> out/portfolios.json
  python -B select2.py validate    # validation runs at 1/2/3/5/10% + cost columns -> out/validation.json
  python -B select2.py freeze      # selected risk, primary/backups -> FREEZE_S2_PRIMARY_<utc>.json / _BACKUPS_
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone

from common2 import HERE, OUT, RESEARCH, UNIVERSE, VALIDATION, TEST, ts
import search2 as S
import signals2 as SG
from sim2 import Costs

UTC = timezone.utc
RISKS = [0.01, 0.02, 0.03, 0.05, 0.10]
COSTS = {"base": Costs(), "cost1.5x": Costs(scale=1.5), "exec0.05": Costs(slip=0.0005), "exec0.10": Costs(slip=0.0010)}
SEED = 20261014


def all_variants() -> dict:
    vm = {}
    for f in sorted(glob.glob(str(HERE / "ROUND*.json"))):
        n = int(f.split("ROUND")[-1].split(".")[0])
        vm.update({v["vid"]: v for v in S.load_round(n)[1]})
    return vm


def combo_signals(vs: list[dict], coins: list[str], window) -> list[dict]:
    sigs = []
    for k, v in enumerate(vs):
        v = dict(v, priority=k)
        sigs += SG.build(v, [c for c in coins if c in S.coins_for(v["tf"])], ts(window[0]), ts(window[1]))
    return sigs


def run_combo(vs: list[dict], window, risk=0.02, costs=None, coins=None, ten_k=False):
    coins = coins or UNIVERSE
    sigs = combo_signals(vs, coins, window)
    if ten_k:
        sleeves = list(coins) + [f"CASH{i}" for i in range(10 - len(coins))]
        cap = 200.0
    else:
        sleeves, cap = list(coins), 20.0 * len(coins)
    return S.run(vs[0], window, risk=risk, costs=costs, coins=coins, sleeves=sleeves, cap_abs=cap, sigs=sigs)


def overlap(ta: list[dict], tb: list[dict]) -> float:
    """Share of the smaller trade list whose holding interval overlaps a same-coin, same-side trade of the other."""
    if not ta or not tb:
        return 0.0
    idx: dict = {}
    for t in tb:
        idx.setdefault((t["coin"], t["side"]), []).append((t["entry_t"], t["exit_t"]))
    hit = sum(1 for t in ta if any(a < t["exit_t"] and t["entry_t"] < b for a, b in idx.get((t["coin"], t["side"]), [])))
    idx2: dict = {}
    for t in ta:
        idx2.setdefault((t["coin"], t["side"]), []).append((t["entry_t"], t["exit_t"]))
    hit2 = sum(1 for t in tb if any(a < t["exit_t"] and t["entry_t"] < b for a, b in idx2.get((t["coin"], t["side"]), [])))
    return max(hit / len(ta), hit2 / len(tb))


def cmd_pool() -> None:
    vm = all_variants()
    table = S.gate_table()
    passing = [r for r in table if r["pass"]]
    label = "CANDIDATE"
    if not passing:  # PROTOCOL 8.7
        label = "NOT_A_CANDIDATE"
        cands = [r for r in table if r["trades"] >= 150 and r["net"] > 0]
    else:
        cands = passing
    best: dict = {}
    for r in sorted(cands, key=lambda r: -r["usd30_10k"]):
        best.setdefault(r["base"], r)
    ranked = sorted(best.values(), key=lambda r: -r["usd30_10k"])
    trades = {}
    for r in ranked[:30]:
        res, _ = S.run(vm[r["vid"]])
        trades[r["vid"]] = res["trades"]
    pool, dups = [], []
    for r in ranked[:30]:
        dup = next((p for p in pool if overlap(trades[r["vid"]], trades[p["vid"]]) > 0.5), None)
        if dup:
            dups.append({"vid": r["vid"], "duplicate_of": dup["vid"],
                         "overlap": round(overlap(trades[r["vid"]], trades[dup["vid"]]), 3)})
            continue
        pool.append(dict(r, label=label))
        if label == "NOT_A_CANDIDATE" and len(pool) == 3:
            break
    out = {"label": label, "passing_variants": len(passing), "pool": pool, "duplicates": dups}
    (OUT / "pool.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(label, "passing", len(passing), "pool", [(p["vid"], p["usd30_10k"]) for p in pool], "dups", len(dups))


def _research_combo(vids: list[str], vm: dict):
    return run_combo([vm[x] for x in vids], RESEARCH)


def cmd_build() -> None:
    vm = all_variants()
    pool = json.load(open(OUT / "pool.json", encoding="utf-8"))
    P = pool["pool"]
    if not P:
        sys.exit("empty pool")
    books = []
    books.append(("P1", [P[0]["vid"]]))
    longs = [p for p in P if p["side"] == "LONG"]
    shorts = [p for p in P if p["side"] == "SHORT"]
    if longs and shorts:
        books.append(("P2", [longs[0]["vid"], shorts[0]["vid"]]))
    comp = [P[0]["vid"]]
    _, m = _research_combo(comp, vm)
    best = m["usd30_10k"]
    log = [{"components": list(comp), "usd30_10k": best}]
    for p in P[1:]:
        if len(comp) == 4:
            break
        trial = comp + [p["vid"]]
        _, mt = _research_combo(trial, vm)
        log.append({"components": trial, "usd30_10k": mt["usd30_10k"], "stopped": mt["stopped"]})
        if mt["usd30_10k"] > best and not mt["stopped"]:
            comp, best = trial, mt["usd30_10k"]
    books.append(("P3", comp))
    seen, out = [], []
    for name, comps in books:
        key = sorted(comps)
        if key in seen:
            continue
        seen.append(key)
        res, m = _research_combo(comps, vm)
        coins = [c for c, v in sorted(m["per_coin"].items(), key=lambda kv: -kv[1]) if v > 0][:10]
        out.append({"name": name, "components": comps, "research_16coin": {k: m[k] for k in m if k != "skips"},
                    "coins": coins})
        print(name, comps, m["usd30_10k"], coins)
    (OUT / "portfolios.json").write_text(json.dumps({"books": out, "greedy_log": log, "label": pool["label"]}, indent=1),
                                         encoding="utf-8")


def cmd_validate() -> None:
    vm = all_variants()
    pf = json.load(open(OUT / "portfolios.json", encoding="utf-8"))
    out = []
    for b in pf["books"]:
        vs = [vm[x] for x in b["components"]]
        rec = {"name": b["name"], "components": b["components"], "coins": b["coins"], "research": {}, "validation": {}}
        for risk in RISKS:
            _, mr = run_combo(vs, RESEARCH, risk=risk, coins=b["coins"], ten_k=True)
            rec["research"][str(risk)] = mr
            rec["validation"][str(risk)] = {}
            for cn, c in COSTS.items():
                _, mv = run_combo(vs, VALIDATION, risk=risk, costs=c, coins=b["coins"], ten_k=True)
                rec["validation"][str(risk)][cn] = mv
            v = rec["validation"][str(risk)]
            print(b["name"], risk, "research", mr["usd30_10k"], mr["max_dd_pct"], "| validation",
                  {k: x["usd30_10k"] for k, x in v.items()}, v["base"]["max_dd_pct"], flush=True)
        out.append(rec)
    (OUT / "validation.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


def selected_risk(rec: dict) -> float | None:
    ok = []
    for risk in (0.01, 0.02, 0.03, 0.05):
        r, v = rec["research"][str(risk)], rec["validation"][str(risk)]
        if (not r["stopped"] and not v["base"]["stopped"] and v["base"]["max_dd_pct"] <= 10.0
                and r["max_dd_pct"] <= 15.0 and v["cost1.5x"]["net"] > 0):
            ok.append(risk)
    return max(ok) if ok else None


def code_state() -> dict:
    root = HERE.parents[3]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    diff = subprocess.run(["git", "diff", "HEAD"], cwd=root, capture_output=True).stdout
    files = {}
    for f in sorted(glob.glob(str(HERE / "*.py"))) + [str(HERE / "PROTOCOL.md")]:
        files[f.split("study2")[-1].lstrip("\\/")] = hashlib.sha256(open(f, "rb").read()).hexdigest()
    app = {}
    for rel in ("app/research/market_structure/regime.py", "app/research/market_structure/structure.py",
                "app/research/passed_regime_backtest.py", "app/research/btc_daily_regime.py"):
        app[rel] = hashlib.sha256(open(HERE.parents[2] / rel, "rb").read()).hexdigest()
    return {"git_head": head, "uncommitted_diff_sha256": hashlib.sha256(diff).hexdigest(), "study_files_sha256": files,
            "app_files_sha256": app}


def cmd_freeze() -> None:
    if glob.glob(str(HERE / "FREEZE_S2_PRIMARY_*.json")):
        sys.exit("a primary freeze exists; freeze files are write-once")
    vm = all_variants()
    val = json.load(open(OUT / "validation.json", encoding="utf-8"))
    pf = json.load(open(OUT / "portfolios.json", encoding="utf-8"))
    rows = []
    for rec in val:
        risk = selected_risk(rec)
        v2 = rec["validation"]["0.02"]
        eligible = v2["base"]["net"] > 0 and v2["cost1.5x"]["net"] > 0 and pf["label"] == "CANDIDATE"
        score = rec["validation"][str(risk)]["base"]["usd30_10k"] if (eligible and risk) else None
        rows.append({"rec": rec, "risk": risk, "eligible": bool(eligible and risk), "score": score,
                     "v2_usd30": v2["base"]["usd30_10k"]})
    elig = sorted([r for r in rows if r["eligible"]], key=lambda r: -r["score"])
    rest = sorted([r for r in rows if not r["eligible"]], key=lambda r: -r["v2_usd30"])
    order = elig + rest
    man = json.load(open(HERE / "DATA_MANIFEST.json", encoding="utf-8"))
    stamp = datetime.now(UTC)
    common = {"frozen_utc": stamp.isoformat(), "protocol": "PROTOCOL.md", "protocol_sha256":
              hashlib.sha256(open(HERE / "PROTOCOL.md", "rb").read()).hexdigest(),
              "data": {"ohlcv_sha256_all_v1_symbols": man["ohlcv_sha256"], "manifest_generated": man["generated_utc"]},
              "code": code_state(), "seeds": {"bootstrap": SEED},
              "windows": {"research": [RESEARCH[0].isoformat(), RESEARCH[1].isoformat()],
                          "validation": [VALIDATION[0].isoformat(), VALIDATION[1].isoformat()],
                          "historical_recheck_contaminated": [TEST[0].isoformat(), TEST[1].isoformat()],
                          "prospective_paper_from": stamp.isoformat()},
              "accounting": {"engine": "study2/sim2.py", "account_usd": 10000, "sleeves": 10, "sleeve_usd": 1000,
                             "leverage_cap": 2.0, "open_risk_cap": "min($200, 2% of marked account equity)",
                             "daily_pause": 0.02, "weekly_pause": 0.05, "dd_stop": 0.20, "taker": 0.0004,
                             "execution_per_side": 0.0002, "funding_hypothetical_per_8h": 0.0001,
                             "withdrawal": "PROTOCOL.md section 7", "conflicts": "PROTOCOL.md section 6",
                             "lot_step": "10^-qty_precision (symbols snapshot)", "min_notional": 5.0},
              "regime_map": "MAP_CORRECTED unless a component says map B (PROTOCOL.md section 3)"}
    books = []
    for k, r in enumerate(order):
        rec = r["rec"]
        role = "PRIMARY" if k == 0 else f"BACKUP_{k}"
        status = "NOT_YET_TESTABLE" if r["eligible"] else ("NOT_ELIGIBLE" if pf["label"] == "CANDIDATE"
                                                            else "NOT_A_CANDIDATE")
        risk = r["risk"] if r["eligible"] else 0.02
        books.append({"role": role, "id": f"S2_{rec['name']}_{stamp:%Y%m%d}", "status": status,
                      "selected_risk": risk, "benchmark_risk": 0.02, "coins": rec["coins"],
                      "components": [{**vm[x], "priority": i} for i, x in enumerate(rec["components"])],
                      "validation_usd30_selected": rec["validation"][str(risk)]["base"]["usd30_10k"],
                      "validation_usd30_2pct": r["v2_usd30"],
                      "research_usd30_selected": rec["research"][str(risk)]["usd30_10k"],
                      "rationale": ("highest validation $/30d among eligible portfolios at its selected risk"
                                    if r["eligible"] else "no eligible portfolio; frozen for information only")})
    p = HERE / f"FREEZE_S2_PRIMARY_{stamp:%Y%m%dT%H%M%SZ}.json"
    q = HERE / f"FREEZE_S2_BACKUPS_{stamp:%Y%m%dT%H%M%SZ}.json"
    p.write_text(json.dumps({**common, "book": books[0]}, indent=1), encoding="utf-8")
    q.write_text(json.dumps({**common, "books": books[1:]}, indent=1), encoding="utf-8")
    for f in (p, q):
        print(f.name, hashlib.sha256(f.read_bytes()).hexdigest())
    print(json.dumps([{k: b[k] for k in ("role", "id", "status", "selected_risk", "validation_usd30_selected",
                                         "validation_usd30_2pct")} for b in books], indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["pool", "build", "validate", "freeze"])
    a = ap.parse_args()
    {"pool": cmd_pool, "build": cmd_build, "validate": cmd_validate, "freeze": cmd_freeze}[a.cmd]()


if __name__ == "__main__":
    main()
