"""Study-2 paper runner (prospective evaluation of a frozen book). OFF BY DEFAULT. Never places orders.

  python -B paper/runner.py dry-run             # read-only: rebuild signals on fresh bars, replay from the freeze
                                                #   timestamp, print state and the entries due at the next open
  python -B paper/runner.py run                 # same, and writes paper/state/*.json - refused unless
                                                #   paper_config.json has "enabled": true AND the book is eligible
  python -B paper/runner.py check-data          # data-quality gate only

Method: the paper result is a deterministic replay. Each invocation reloads stored bars (read-only DB session) from
warm-up through the last fully closed bar, recomputes features and causal regime labels with the same code as the
study (signals2.py, regimes2.py calls), builds the frozen components' signals and runs sim2.simulate from the freeze
timestamp. Nothing in the frozen book (rules, parameters, coins, risk, costs) can be changed here; the freeze file's
sha256 is checked against paper_config.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent
sys.path.insert(0, str(STUDY))

import common2 as C  # noqa: E402

UTC = timezone.utc
CFG = HERE / "paper_config.json"
STATE = HERE / "state"
MAX_STALE_H = 3          # last closed 1h bar must be at most this old
GAP_LOOKBACK_D = 14      # no missing 1h bars in the last 14 days on any book coin


def load_cfg() -> dict:
    cfg = json.load(open(CFG, encoding="utf-8"))
    fz = STUDY / cfg["freeze_file"]
    sha = hashlib.sha256(fz.read_bytes()).hexdigest()
    if sha != cfg["freeze_sha256"]:
        sys.exit(f"freeze file hash mismatch: {sha} != {cfg['freeze_sha256']}")
    cfg["freeze"] = json.load(open(fz, encoding="utf-8"))
    return cfg


def _agg(rows: list[dict], sec: int) -> list[dict]:
    """Complete higher-tf bars from 1h bars (only groups with every hour present)."""
    groups: dict = {}
    for x in rows:
        k = int(x["time"].timestamp()) // sec * sec
        groups.setdefault(k, []).append(x)
    out = []
    for k in sorted(groups):
        g = sorted(groups[k], key=lambda x: x["time"])
        if len(g) != sec // 3600:
            continue
        out.append({"time": datetime.fromtimestamp(k, UTC), "open": g[0]["open"], "high": max(x["high"] for x in g),
                    "low": min(x["low"] for x in g), "close": g[-1]["close"], "volume": sum(x["volume"] for x in g)})
    return out


def fresh_loader(now: datetime):
    """candles()/hourly() replacements that read through the last closed bar and extend stale 4h/1d series with
    complete aggregates of stored 1h bars. Uncached."""
    from _db import load_candles
    memo: dict = {}

    def candles(coin, tf, start=C.DATA_FROM, end=None):
        key = (coin, tf)
        if key not in memo:
            rows = load_candles(coin, tf, C.DATA_FROM, now)
            sec = C.TF_SEC[tf]
            rows = [r for r in rows if int(r["time"].timestamp()) + sec <= int(now.timestamp())]
            if tf in ("4h", "1d") and rows:
                last = int(rows[-1]["time"].timestamp()) + sec
                h = [r for r in load_candles(coin, "1h", datetime.fromtimestamp(last, UTC), now)
                     if int(r["time"].timestamp()) + 3600 <= int(now.timestamp())]
                rows += _agg(h, sec)
            memo[key] = rows
        return memo[key]

    def hourly(coin, end=None):
        return candles(coin, "1h")

    return candles, hourly


def check_data(coins: list[str], now: datetime, candles) -> list[str]:
    bad = []
    for c in coins:
        h = candles(c, "1h")
        if not h:
            bad.append(f"{c}: no 1h bars"); continue
        last_close = h[-1]["time"] + timedelta(hours=1)
        if now - last_close > timedelta(hours=MAX_STALE_H):
            bad.append(f"{c}: last closed 1h bar {last_close.isoformat()} older than {MAX_STALE_H}h")
        t0 = now - timedelta(days=GAP_LOOKBACK_D)
        ts_ = [int(x["time"].timestamp()) for x in h if x["time"] >= t0]
        miss = sum(1 for a, b in zip(ts_, ts_[1:]) if b - a != 3600)
        if miss:
            bad.append(f"{c}: {miss} gaps in the last {GAP_LOOKBACK_D} days of 1h bars")
    return bad


def replay(cfg: dict, now: datetime, replay_from: datetime | None = None) -> dict:
    book = cfg["freeze"]["book"]
    if any(v["fam"].startswith("ENG_") for v in book["components"]):
        sys.exit("engine-sourced components need a forward engine walk; not supported by this runner")
    candles, hourly = fresh_loader(now)
    C.candles = candles
    C.hourly = hourly
    import signals2 as SG
    import regimes2 as RG
    SG.candles, SG.hourly = candles, hourly
    RG.candles = candles
    SG.FDIR = STATE / f"feat_{now:%Y%m%dT%H}"
    SG.RDIR = STATE / f"regimes_{now:%Y%m%dT%H}"
    RG.RDIR = SG.RDIR
    SG.RDIR.mkdir(parents=True, exist_ok=True)
    SG._F.clear()
    for f in (SG.regime_series, SG.btc_series, SG._daily_closes, SG.rs_rank, SG._raw_cached):
        f.cache_clear()
    RG.btc_daily()
    for coin in sorted(set(C.UNIVERSE)):  # RS ranks and BTC need the whole universe
        for tf in ("4h", "1d"):
            p = SG.RDIR / f"{coin}_{tf}.json"
            if not p.exists():
                p.write_text(json.dumps(RG.label(coin, tf)), encoding="utf-8")
    from sim2 import Bars, Config, Costs, simulate
    from metrics2 import metrics
    start = replay_from or datetime.fromisoformat(cfg["freeze"]["windows"]["prospective_paper_from"])
    end = now.replace(minute=0, second=0, microsecond=0)
    coins = book["coins"]
    sigs = []
    for k, v in enumerate(sorted(book["components"], key=lambda x: x["priority"])):
        v = dict(v, priority=k)
        tf_coins = [c for c in coins if (v["tf"] != "15m" or c in C.COINS_15M)]
        sigs += SG.build(v, tf_coins, int(start.timestamp()), int(end.timestamp()) + 1)
    b = Bars()
    man = json.load(open(STUDY / "DATA_MANIFEST.json", encoding="utf-8"))
    lot = {c: 10.0 ** -int(m["qty_precision"]) for c, m in man["contracts"].items()}
    for c in coins:
        b.put(c, "1h", hourly(c))
        if c in C.COINS_15M:
            b.put(c, "15m", candles(c, "15m"))
    sleeves = list(coins) + [f"CASH{i}" for i in range(10 - len(coins))]
    simcfg = Config(risk_frac=book["selected_risk"], risk_cap_abs=200.0, lot_step=lot, costs=Costs())
    if end <= start:
        return {"note": "no closed bar after the freeze yet", "signals_due": []}
    res = simulate([s for s in sigs if s["signal_close"] < int(end.timestamp())], b, sleeves, start, end, simcfg)
    due = [s for s in sigs if s["signal_close"] == int(end.timestamp())]
    m = metrics(res, folds=False)
    return {"as_of": end.isoformat(), "start": start.isoformat(), "metrics": m, "trades": res["trades"],
            "events": res["events"], "signals_due_next_open": [{k: s[k] for k in ("coin", "tf", "side", "stop")}
                                                               for s in due],
            "evaluation": evaluation(m, start, end)}


def evaluation(m: dict, start: datetime, end: datetime) -> dict:
    full_months = m["full_months"]
    days = (end - start).total_seconds() / 86400
    net30 = m["usd30_10k"]
    status = "NOT_YET_TESTABLE"
    reasons = []
    if m["stopped"]:
        status, reasons = "FAILED_DD_STOP", ["20% cash-flow-adjusted drawdown stop triggered"]
    elif full_months >= 6 and m["trades"] >= 100:
        status = "TARGET_MET" if net30 >= 500 else "TARGET_NOT_MET"
    return {"elapsed_days": round(days, 1), "full_months": full_months, "closed_trades": m["trades"],
            "net_30day_usd": net30, "gap_to_500": round(max(0.0, 500 - net30), 2),
            "gap_to_1000": round(max(0.0, 1000 - net30), 2), "status": status, "reasons": reasons,
            "robust_floor": "needs >= 6 full months and >= 100 closed trades; positive expectancy interval; "
                            "risk limits respected; positive at 1.5x costs (recompute with study2 tools)"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["dry-run", "run", "check-data"])
    ap.add_argument("--now", help="override 'now' (UTC ISO) for reproducible replays")
    ap.add_argument("--replay-from", help="dry-run only: plumbing/parity check from an earlier UTC ISO time "
                                          "(not paper evidence)")
    a = ap.parse_args()
    cfg = load_cfg()
    now = datetime.fromisoformat(a.now) if a.now else datetime.now(UTC)
    book = cfg["freeze"]["book"]
    if a.cmd == "check-data":
        candles, _ = fresh_loader(now)
        bad = check_data(book["coins"], now, candles)
        print(json.dumps({"ok": not bad, "problems": bad}, indent=1)); return
    if a.cmd == "run":
        if not cfg.get("enabled"):
            sys.exit("paper runner disabled (paper_config.json enabled=false); use dry-run")
        if book["status"] not in ("NOT_YET_TESTABLE",):
            sys.exit(f"book status {book['status']}: not eligible for paper")
    candles, _ = fresh_loader(now)
    bad = check_data(book["coins"], now, candles)
    if bad and a.cmd == "run":
        sys.exit("data quality gate failed: " + "; ".join(bad))
    if a.replay_from and a.cmd != "dry-run":
        sys.exit("--replay-from is a dry-run parity check only")
    out = replay(cfg, now, datetime.fromisoformat(a.replay_from) if a.replay_from else None)
    out["data_quality"] = bad or "ok"
    summary = {k: out[k] for k in out if k not in ("trades", "metrics")}
    if "metrics" in out:
        summary["metrics"] = {k: out["metrics"][k] for k in ("trades", "net", "usd30_10k", "max_dd_pct", "withdrawn",
                                                             "final_equity", "months")}
    print(json.dumps(summary, indent=1, default=str))
    if a.replay_from:
        (HERE / "parity_trades.json").write_text(json.dumps(out.get("trades", []), default=str), encoding="utf-8")
    if a.cmd == "run":
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / f"paper_{now:%Y%m%dT%H%M}.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
