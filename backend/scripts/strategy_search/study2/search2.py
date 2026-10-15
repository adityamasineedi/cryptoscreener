"""Study-2 discovery runner (research window only).

  python -B search2.py screen N --shard i/n   # every registered variant of ROUND<N>.json at 2% risk -> out/variants.csv
  python -B search2.py gates N --shard i/n    # 1.5x-cost and +/-20% neighbour runs for variants passing cheap gates
  python -B search2.py summary N              # gate table for round N -> out/round<N>_summary.json
Every simulation is appended to out/variants.csv (one row) and out/variants_detail.jsonl (months, coins, folds).
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
import time
from functools import lru_cache

from common2 import COINS_15M, HERE, OUT, RESEARCH, UNIVERSE, hourly, candles, ts
import signals2 as SG
from metrics2 import metrics
from sim2 import Bars, Config, Costs, simulate

CSV = OUT / "variants.csv"
DETAIL = OUT / "variants_detail.jsonl"
COLS = ["round", "base", "vid", "purpose", "window", "risk", "cost", "mult", "fam", "tf", "side", "regime", "map", "btc",
        "p", "stop_k", "exit", "pyramid", "trades", "longs", "shorts", "net", "usd30_10k", "pf", "win_rate", "exp_r",
        "exp_r_se", "trades_per_30d", "avg_risk_usd", "max_dd_pct", "stopped", "coin_share", "month_share",
        "worst_month_10k", "fees", "exec", "funding_hyp", "F1", "F2", "F3", "F4", "folds_pos", "seconds", "shard_file"]

# ---------------------------------------------------------------- exits / variant expansion (registered per round)
EXITS = {
    "R2": {"kind": "R", "r": 2.0},
    "R3": {"kind": "R", "r": 3.0},
    "TRAIL3": {"kind": "TRAIL", "trail": 3.0},
    "TRAIL4": {"kind": "TRAIL", "trail": 4.0},
    "PT": {"kind": "PT", "pr": 1.0, "pf": 0.5, "trail": 2.5},
    "PT3": {"kind": "PT", "pr": 1.0, "pf": 0.5, "trail": 3.0},
    "R1": {"kind": "R", "r": 1.0},
    "R15T": {"kind": "R", "r": 1.5, "hold": 12},
    "MIDT": {"kind": "MID", "hold": 24},
}
CLASS = {
    "TREND": {"stops": [1.5, 2.5], "exits": ["R2", "TRAIL3", "PT"], "btc": [0, 1]},
    "TF": {"stops": [2.5, 3.5], "exits": ["TRAIL3", "TRAIL4", "PT3"], "btc": [0, 1]},
    "REV": {"stops": [1.0, 1.5], "exits": ["R1", "R15T", "MIDT"], "btc": [0, 1]},
    "FBR": {"stops": [1.0], "bufs": [0.25, 0.5], "exits": ["R1", "R15T", "MIDT"], "btc": [0, 1]},
    "ENG": {"stops": [1.0], "exits": ["R2", "TRAIL3", "PT"], "btc": [0, 1]},
    "PYR": {"stops": [2.0, 3.0], "exits": ["TRAIL3", "TRAIL4", "PT3"], "btc": [0, 1]},
}


def expand(base: dict) -> list[dict]:
    cl = CLASS[base["class"]]
    out = []
    bufs = cl.get("bufs", [None])
    for sk in cl["stops"]:
        for buf in bufs:
            for ex in cl["exits"]:
                for btc in cl["btc"]:
                    p = dict(base["p"])
                    if buf is not None:
                        p["buf"] = buf
                    v = {"base": base["id"], "fam": base["fam"], "tf": base["tf"], "side": base["side"],
                         "regime": base["regime"], "map": base.get("map", "C"), "btc": btc, "p": p, "stop_k": sk,
                         "exit": dict(EXITS[ex]), "exit_name": ex, "pyramid": base.get("pyramid", False)}
                    tag = f"b{buf}" if buf is not None else f"s{sk}"
                    v["vid"] = f"{base['id']}|{tag}|{ex}|btc{btc}"
                    out.append(v)
    return out


def load_round(n: int) -> tuple[list[dict], list[dict]]:
    r = json.load(open(HERE / f"ROUND{n}.json", encoding="utf-8"))
    vs = []
    for b in r["bases"]:
        vs += [dict(v, round=n) for v in (expand(b) if "variants" not in b else b["variants"])]
    return r["bases"], vs


# ---------------------------------------------------------------- data / runs
_BARS: Bars | None = None
LOT: dict = {}


def bars() -> Bars:
    global _BARS
    if _BARS is None:
        b = Bars()
        for c in UNIVERSE:
            b.put(c, "1h", hourly(c))
        for c in COINS_15M:
            b.put(c, "15m", candles(c, "15m"))
        _BARS = b
        man = json.load(open(HERE / "DATA_MANIFEST.json", encoding="utf-8"))
        for c, m in man["contracts"].items():
            LOT[c] = 10.0 ** -int(m["qty_precision"])
    return _BARS


def coins_for(tf: str) -> list[str]:
    return COINS_15M if tf == "15m" else UNIVERSE


def run(v: dict, window=RESEARCH, risk: float = 0.02, costs: Costs | None = None, mult: dict | None = None,
        coins: list[str] | None = None, sleeves: list[str] | None = None, cap_abs: float | None = None,
        sigs: list[dict] | None = None) -> tuple[dict, dict]:
    b = bars()
    coins = coins or coins_for(v["tf"])
    sleeves = sleeves or coins
    if sigs is None:
        sigs = SG.build(v, coins, ts(window[0]), ts(window[1]), mult=mult)
    cfg = Config(risk_frac=risk, risk_cap_abs=cap_abs if cap_abs is not None else 20.0 * len(sleeves),
                 lot_step=LOT, costs=costs or Costs())
    res = simulate(sigs, b, sleeves, window[0], window[1], cfg)
    return res, metrics(res)


def log(v: dict, m: dict, purpose: str, window: str, risk: float, cost: str, mult: dict | None, secs: float,
        shard_file: str) -> None:
    row = {"round": v.get("round"), "base": v.get("base"), "vid": v["vid"], "purpose": purpose, "window": window,
           "risk": risk, "cost": cost, "mult": json.dumps(mult or {}), "fam": v["fam"], "tf": v["tf"],
           "side": v["side"], "regime": v["regime"], "map": v.get("map", "C"), "btc": v["btc"],
           "p": json.dumps(v["p"]), "stop_k": v["stop_k"], "exit": v.get("exit_name") or json.dumps(v["exit"]),
           "pyramid": v.get("pyramid", False), "seconds": round(secs, 1), "shard_file": shard_file,
           **{k: m.get(k) for k in COLS if k in m and k != "base"}, **m.get("folds", {})}
    f = OUT / shard_file
    new = not f.exists()
    with open(f, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerow(row)
    with open(OUT / shard_file.replace(".csv", "_detail.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"vid": v["vid"], "purpose": purpose, "window": window, "risk": risk, "cost": cost,
                             "mult": mult or {}, "metrics": m}) + "\n")


def logged(purpose: str | None = None) -> list[dict]:
    rows = []
    for f in sorted(glob.glob(str(OUT / "variants_*.csv"))):
        rows += [r for r in csv.DictReader(open(f, encoding="utf-8")) if purpose is None or r["purpose"] == purpose]
    return rows


# ---------------------------------------------------------------- gates
def numeric_params(v: dict) -> list[str]:
    keys = [k for k, x in v["p"].items() if isinstance(x, (int, float)) and not isinstance(x, bool)]
    if "buf" not in v["p"]:
        keys.append("stop_k")
    keys += [k for k, x in v["exit"].items() if k != "kind" and isinstance(x, (int, float))]
    return keys


def cheap_fails(r: dict) -> list[str]:
    f = lambda k: float(r[k]) if r[k] not in ("", "None", None) else None  # noqa: E731
    bad = []
    if int(r["trades"]) < 150: bad.append("trades<150")
    if f("net") <= 0: bad.append("net<=0")
    if int(r["folds_pos"]) < 3: bad.append("folds<3")
    if f("net") > 0 and (f("coin_share") or 1) > 0.35: bad.append("coin>35%")
    if f("net") > 0 and (f("month_share") or 1) > 0.30: bad.append("month>30%")
    if r["stopped"] == "True": bad.append("dd_stop")
    return bad


def cmd_screen(n: int, shard: str) -> None:
    i, k = (int(x) for x in shard.split("/"))
    _, vs = load_round(n)
    sf = f"variants_r{n}_s{i}.csv"
    done = {r["vid"] for r in logged("screen")}
    todo = [v for v in vs[i::k] if v["vid"] not in done]
    for j, v in enumerate(todo):
        t0 = time.perf_counter()
        _, m = run(v)
        log(v, m, "screen", "research", 0.02, "base", None, time.perf_counter() - t0, sf)
        print(f"[r{n} {shard}] {j + 1}/{len(todo)} {v['vid']} tr={m['trades']} $30={m['usd30_10k']} "
              f"folds={m['folds_pos']} {time.perf_counter() - t0:.1f}s", file=sys.stderr, flush=True)


def cmd_gates(n: int, shard: str) -> None:
    i, k = (int(x) for x in shard.split("/"))
    _, vs = load_round(n)
    vmap = {v["vid"]: v for v in vs}
    screen = {r["vid"]: r for r in logged("screen") if r["vid"] in vmap}
    cand = sorted(vid for vid, r in screen.items() if not cheap_fails(r))
    sf = f"variants_r{n}_g{i}.csv"
    have = {(r["vid"], r["purpose"], r["mult"]) for r in logged()}
    for vid in cand[i::k]:
        v = vmap[vid]
        jobs = [("cost1.5", None)]
        for key in numeric_params(v):
            for mul in (0.8, 1.2):
                jobs.append(("neighbor", {key: mul}))
        for purpose, mult in jobs:
            if (vid, purpose, json.dumps(mult or {})) in have:
                continue
            t0 = time.perf_counter()
            if purpose == "cost1.5":
                _, m = run(v, costs=Costs(scale=1.5))
                log(v, m, purpose, "research", 0.02, "1.5x", None, time.perf_counter() - t0, sf)
            else:
                _, m = run(v, mult=mult)
                log(v, m, purpose, "research", 0.02, "base", mult, time.perf_counter() - t0, sf)
            print(f"[g{n} {shard}] {vid} {purpose} {mult} net={m['net']}", file=sys.stderr, flush=True)


def gate_table(n: int | None = None) -> list[dict]:
    rows = logged()
    scr = {r["vid"]: r for r in rows if r["purpose"] == "screen" and (n is None or r["round"] == str(n))}
    extra: dict = {}
    for r in rows:
        if r["purpose"] in ("cost1.5", "neighbor") and r["vid"] in scr:
            extra.setdefault(r["vid"], []).append(r)
    out = []
    for vid, r in scr.items():
        fails = cheap_fails(r)
        rec = {"vid": vid, "round": r["round"], "base": vid.split("|")[0], "fam": r["fam"], "tf": r["tf"], "side": r["side"],
               "trades": int(r["trades"]), "net": float(r["net"]), "usd30_10k": float(r["usd30_10k"]),
               "folds_pos": int(r["folds_pos"]), "F4": float(r["F4"]), "coin_share": r["coin_share"],
               "month_share": r["month_share"], "max_dd_pct": float(r["max_dd_pct"]), "exp_r": r["exp_r"],
               "pf": r["pf"], "fails": fails}
        if not fails:
            ex = extra.get(vid, [])
            cost = [x for x in ex if x["purpose"] == "cost1.5"]
            nb = [x for x in ex if x["purpose"] == "neighbor"]
            if not cost or not nb:
                rec["fails"] = ["gates_not_run"]
            else:
                if float(cost[0]["net"]) <= 0: rec["fails"].append("cost1.5x")
                nets = [float(x["net"]) for x in nb]
                rec["cost15_net"] = float(cost[0]["net"])
                rec["neighbors"] = {x["mult"]: float(x["net"]) for x in nb}
                if min(nets) <= 0: rec["fails"].append("neighbor<=0")
                if sum(nets) / len(nets) < 0.5 * float(r["net"]): rec["fails"].append("neighbor_spike")
        rec["pass"] = not rec["fails"]
        out.append(rec)
    return out


def cmd_summary(n: int) -> None:
    t = gate_table(n)
    t.sort(key=lambda r: -r["usd30_10k"])
    (OUT / f"round{n}_summary.json").write_text(json.dumps(t, indent=1), encoding="utf-8")
    print("variants", len(t), "pass", sum(r["pass"] for r in t))
    for r in t[:40]:
        print(f"{r['vid']:<45} {r['side']:<5} tr={r['trades']:<5} $30={r['usd30_10k']:>8} F={r['folds_pos']} "
              f"F4={r['F4']:>8} dd={r['max_dd_pct']:>5} R={r['exp_r']} {','.join(r['fails']) or 'PASS'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["screen", "gates", "summary"])
    ap.add_argument("n", type=int)
    ap.add_argument("--shard", default="0/1")
    a = ap.parse_args()
    if a.cmd == "screen":
        cmd_screen(a.n, a.shard)
    elif a.cmd == "gates":
        cmd_gates(a.n, a.shard)
    else:
        cmd_summary(a.n)


if __name__ == "__main__":
    main()
