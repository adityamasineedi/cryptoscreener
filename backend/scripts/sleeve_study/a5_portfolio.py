"""Agent 5 - portfolio. Validation selection (mechanical, PROTOCOL.md), account simulation, freeze, final test.

  python -B a5_portfolio.py validate   # validation only; writes out/validation.json, out/validation_table.md, FREEZE draft
  python -B a5_portfolio.py final      # reads FREEZE.json, runs the final test ONCE (refuses if already run)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone

from common import FINAL, HERE, OUT, RESEARCH, UTC, VALIDATION
import a5_eval as E
from sim import Config, Costs, max_drawdown, simulate

FREEZE = HERE / "FREEZE.json"
FINAL_OUT = OUT / "final_result.json"

_S: dict = {}


def _init_val() -> None:
    _S["signals"] = E.load_signals("P1")
    _S["bars"] = E.get_bars(VALIDATION[1])


def _val_one(v: dict) -> dict:
    r = E.run_variant(v, _S["signals"], VALIDATION, _S["bars"])
    return {"vid": v["vid"], "validation": r["all"], "per_coin_val": r["per_coin"]}


# ------------------------------------------------------------------ account report
def account_report(res: dict, window: tuple[datetime, datetime]) -> dict:
    days = (window[1] - window[0]).total_seconds() / 86400
    months = res["months"]
    nets = [m["net"] for m in months.values()]
    total = sum(nets)
    # daily net from the marked curve (cumulative withdrawals added back)
    by_day = {}
    for t, e in res["curve"]:
        by_day[datetime.fromtimestamp(t, UTC).date()] = e
    ds = sorted(by_day)
    daily = [by_day[b] - by_day[a] for a, b in zip(ds, ds[1:])]
    per_coin = defaultdict(float)
    for t in res["trades"]:
        per_coin[t["coin"]] += t["net"]
    best_coin = max(per_coin.items(), key=lambda kv: kv[1]) if per_coin else (None, 0.0)
    best_month = max(months.items(), key=lambda kv: kv[1]["net"]) if months else (None, {"net": 0})
    worst_month = min(months.items(), key=lambda kv: kv[1]["net"]) if months else (None, {"net": 0})
    full_months = {k: v for k, v in months.items()
                   if not (k == window[1].strftime("%Y-%m") and window[1].day != 1)}
    return {
        "total_net": round(total, 2), "days": round(days, 2),
        "avg_per_30d": round(total / days * 30, 2), "avg_per_day": round(total / days, 2),
        "months": {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in months.items()},
        "months_ge_1000": sum(1 for v in full_months.values() if v["net"] >= 1000),
        "full_months": len(full_months),
        "median_day": round(statistics.median(daily), 2) if daily else None,
        "max_drawdown": round(max_drawdown(res["curve"]), 2),
        "withdrawn": round(res["withdrawn"], 2), "trades": len(res["trades"]),
        "per_coin": {k: round(v, 2) for k, v in sorted(per_coin.items(), key=lambda kv: -kv[1])},
        "best_coin": best_coin[0], "best_coin_share": round(best_coin[1] / total, 3) if total > 0 else None,
        "best_month": best_month[0], "best_month_share": round(best_month[1]["net"] / total, 3) if total > 0 else None,
        "worst_month": worst_month[0], "worst_month_net": round(worst_month[1]["net"], 2),
        "skips": dict(_count(res["skips"])),
    }


def _count(skips):
    c = defaultdict(int)
    for s in skips:
        c[s["why"]] += 1
    return c


def book_signals(signals: list[dict], cells: list[dict]) -> list[dict]:
    out = []
    want = {(c["sid"], c["side"], c["tf"], c["coin"]): c for c in cells}
    for s in signals:
        c = want.get((s["sid"], s["side"], s["tf"], s["coin"]))
        if c is None or not s[c["rf"].lower()]:
            continue
        out.append({**s, "sid": c["new_id"], "target_fn": E.target_fn(c["exit"], s.get("tp1"))})
    return out


def run_book(cells, signals, bars, window, *, risk=0.02, cost_scale=1.0, funding_8h=0.0):
    coins = sorted({c["coin"] for c in cells})
    pri = {c["new_id"]: c["priority"] for c in cells}
    cfg = Config(risk_frac=risk, costs=Costs(scale=cost_scale, funding_flat_8h=funding_8h))
    res = simulate(book_signals(signals, cells), bars, coins, window[0], window[1], cfg, priority=pri)
    return res, account_report(res, window)


COLUMNS = {"base_2pct": {}, "risk_1pct": {"risk": 0.01}, "costs_1.5x": {"cost_scale": 1.5},
           "funding_0.01pct_8h": {"funding_8h": 0.0001}}


def new_id(c: dict) -> str:
    fam = {"LONG_CONT": "LC", "LONG_RANGE": "LR", "SHORT_CONT": "SC", "SHORT_RANGE": "SR"}[c["family"]]
    src = c["sid"].replace("COMBO_02_V2_1_A:", "V21A_").replace(":", "_")
    return f"SLV1_{fam}_{src}_{c['tf']}_{c['rf']}_{c['exit']}"


# ------------------------------------------------------------------ validate
def cmd_validate(workers: int) -> None:
    scr = json.load(open(OUT / "research_screen.json", encoding="utf-8"))["variants"]
    passed = [v for v in scr if v["screen_pass"]]
    print("screened variants", len(passed), "of", len(scr), flush=True)
    _init_val()  # serial: the Windows process pool intermittently loses its queue semaphore here
    vals = [_val_one(v) for v in passed]
    vmap = {v["vid"]: v for v in vals}
    for v in passed:
        v.update(vmap[v["vid"]])
    # eligible cells
    cells = []
    # Brief: "A short's validation net must stand on its own." A short variant enters only if its own
    # validation net (all its coins) is > 0. Omitted from the first validation pass; applied before the freeze.
    excluded_shorts = [v["vid"] for v in passed if v["side"] == "SHORT" and v["validation"]["net"] <= 0]
    for v in passed:
        if v["vid"] in excluded_shorts:
            continue
        for coin, st in v["per_coin_val"].items():
            rn = (v["per_coin"].get(coin) or {}).get("net", 0)
            if st["net"] > 0 and rn > 0:
                cells.append({"vid": v["vid"], "sid": v["sid"], "side": v["side"], "tf": v["tf"], "rf": v["rf"],
                              "exit": v["exit"], "family": v["family"], "coin": coin, "val_net": st["net"],
                              "val_trades": st["trades"], "res_net": rn,
                              "source_family": E.SOURCE_FAMILY.get(v["sid"], v["sid"])})
    best: dict = {}
    for c in cells:
        k = (c["coin"], c["family"])
        if k not in best or c["val_net"] > best[k]["val_net"]:
            best[k] = c
    score = defaultdict(float)
    for (coin, fam), c in best.items():
        score[coin] += c["val_net"]
    coins = [c for c, s in sorted(score.items(), key=lambda kv: -kv[1]) if s > 0][:10]
    chosen = sorted([c for (coin, fam), c in best.items() if coin in coins], key=lambda c: -c["val_net"])
    for i, c in enumerate(chosen):
        c["new_id"] = new_id(c)
        c["priority"] = i
    signals = E.load_signals("P1")
    bars = E.get_bars(VALIDATION[1])
    reports = {}
    for name, kw in COLUMNS.items():
        _, reports[name] = run_book(chosen, signals, bars, VALIDATION, **kw)
    out = {"generated_utc": datetime.now(UTC).isoformat(),
           "validation_window": [VALIDATION[0].isoformat(), VALIDATION[1].isoformat()],
           "screened_variants": [{"vid": v["vid"], "family": v["family"], "research": v["research"],
                                  "validation": v["validation"], "per_coin_val": v["per_coin_val"]} for v in passed],
           "eligible_cells": cells, "excluded_shorts_validation_net_le_0": excluded_shorts, "coin_scores": dict(score), "coins": coins, "book": chosen, "account": reports}
    (OUT / "validation.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: {kk: r[kk] for kk in ("total_net", "avg_per_30d", "months_ge_1000", "worst_month_net",
                                                 "max_drawdown", "best_coin_share", "best_month_share")}
                      for k, r in reports.items()}, indent=1))


# ------------------------------------------------------------------ final
def cmd_final() -> None:
    if FINAL_OUT.exists():
        sys.exit("final test already run; it is not reopened")
    fz = json.load(open(FREEZE, encoding="utf-8"))
    digest = hashlib.sha256(FREEZE.read_bytes()).hexdigest()
    cells = fz["book"]
    signals = E.load_signals("P2")
    bars = E.get_bars(FINAL[1])
    reports, ledgers = {}, {}
    for name, kw in COLUMNS.items():
        res, reports[name] = run_book(cells, signals, bars, FINAL, **kw)
        ledgers[name] = res["trades"]
    FINAL_OUT.write_text(json.dumps({"generated_utc": datetime.now(UTC).isoformat(), "freeze_sha256": digest,
                                     "window": [FINAL[0].isoformat(), FINAL[1].isoformat()],
                                     "account": reports, "ledger_base": ledgers["base_2pct"]}, indent=1),
                         encoding="utf-8")
    print(json.dumps(reports, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["validate", "final"])
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    cmd_validate(a.workers) if a.cmd == "validate" else cmd_final()


if __name__ == "__main__":
    main()
