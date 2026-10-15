"""Fibonacci pullback-depth tags for COMBO_02 LONG 1h trades (research only, no filter).

Read-only. Candles come from the same loader call the COMBO_02 walk used
(service._load_research_candles, read-only DB session set up by
combo/run_combo_rank._worker_init, imported unchanged). Nothing is written outside
replay/out/fib/.

Depth definition (long), at signal bar i on the 1h setup series:
  swings = detect_swings(series, left/right/min_dist from SignalConfig.swing_for("1h"),
                         as_of_index=i)          # only swings with bar_index + right <= i
  H = last confirmed swing HIGH with bar_index < i  (must equal the engine's BOS broken level,
      taken from SignalEngine.analyze_timeframe(..., as_of_index=i).bos.broken_level)
  L = last confirmed swing LOW with bar_index < H.bar_index
  leg = H - L
  depth = (H - min(low[H+1 .. i-1])) / leg
Buckets (lower bound inclusive): <0.382, [0.382,0.5), [0.5,0.618), [0.618,0.786], >0.786.

Usage (from backend/):
  python -B scripts/strategy_rank/replay/fib_tags.py tags      # needs DB (read-only)
  python -B scripts/strategy_rank/replay/fib_tags.py report    # pure file join
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(r"D:\cryptoscreener\backend")
SR = BACKEND / "scripts" / "strategy_rank"
COMBO = SR / "combo"
REPLAY = SR / "replay"
OUT = REPLAY / "out" / "fib"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(COMBO))

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
           "TRXUSDT", "AVAXUSDT", "LINKUSDT"]
WINDOWS = {  # same date args the COMBO_02 cells were walked with (run_combo_rank / plan_oos.json)
    "IS": ("2026-02-14", "2026-10-11"),
    "OOS1": ("2025-06-19", "2026-02-13"),
    "OOS2": ("2024-10-23", "2025-06-18"),
}
BUCKETS = ["lt_0.382", "0.382-0.5", "0.5-0.618", "0.618-0.786", "gt_0.786", "no_valid_swing"]


def stored_path(sym: str, wid: str) -> Path:
    if wid == "IS":
        return COMBO / "trades" / f"COMBO_02__LONG__{sym}__1h.json"
    return COMBO / "oos" / "trades" / f"COMBO_02__LONG__{sym}__1h__{wid}.json"


def agent1_path(model: str, sym: str, wid: str) -> Path:
    return REPLAY / "out" / model / f"COMBO_02__LONG__{sym}__1h__{wid}.json"


def parse_ts(s: str) -> datetime:
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def bucket_of(depth: float) -> str:
    if depth < 0.382:
        return "lt_0.382"
    if depth < 0.5:
        return "0.382-0.5"
    if depth < 0.618:
        return "0.5-0.618"
    if depth <= 0.786:
        return "0.618-0.786"
    return "gt_0.786"


# --------------------------------------------------------------------------- tags
def cmd_tags() -> int:
    import run_combo_rank as rc

    rc._worker_init(quiet=False)
    import app.research.service as service
    from app.research.combination_engine import _clone_signal_config
    from app.research.bos_combinations import get_combination
    from app.research.config import ResearchConfig
    from app.signals._candle_utils import candle_time, series_ohlcv
    from app.signals.signal_engine import SignalEngine
    from app.signals.swing_detector import detect_swings

    loop = rc._W["loop"]
    combo = get_combination("COMBO_02")
    rcfg = ResearchConfig()
    warmup = max(int(rcfg.min_bars), 100)
    scfg = service._signal_config()
    local_cfg = _clone_signal_config(scfg, rcfg, require_htf_alignment=bool(combo.require_htf_alignment))
    sw = local_cfg.swing_for("1h")
    eng = SignalEngine(local_cfg)
    cfg_info = {"left": sw.swing_left_bars, "right": sw.swing_right_bars,
                "minimum_swing_distance_atr": sw.minimum_swing_distance_atr,
                "atr_period": local_cfg.atr_period, "trend_mode": getattr(local_cfg, "trend_mode", None)}
    print("swing cfg 1h:", cfg_info, "db:", rc._W["ro"], flush=True)

    tags: list[dict] = []
    for wid, (sd, ed) in WINDOWS.items():
        for sym in SYMBOLS:
            trades = json.loads(stored_path(sym, wid).read_text(encoding="utf-8"))
            candles, eval_start, meta = loop.run_until_complete(service._load_research_candles(
                sym, "1h", limit=5760, warmup_bars=warmup, start_date=sd, end_date=ed,
                use_research_cache=False, tail_warmup=True))
            _, highs, lows, closes, _ = series_ohlcv(candles)
            tindex = {candle_time(c): k for k, c in enumerate(candles)}
            for t in trades:
                rec = {"window": wid, "symbol": sym, "signal_time": t["signal_time"], "entry": t["entry"]}
                i = tindex.get(parse_ts(t["signal_time"]))
                if i is None:
                    rec.update(bucket="no_valid_swing", reason="signal bar not in series")
                    tags.append(rec)
                    continue
                rec["signal_index"] = i
                rec["entry_eq_close"] = abs(float(closes[i]) - float(t["entry"])) <= 1e-9 * max(1.0, abs(float(t["entry"])))
                swings = detect_swings(
                    candles, left=sw.swing_left_bars, right=sw.swing_right_bars, symbol=sym, timeframe="1h",
                    atr_period=local_cfg.atr_period, minimum_swing_distance_atr=sw.minimum_swing_distance_atr,
                    as_of_index=i, highs=highs, lows=lows, closes=closes)
                assert all(s.bar_index + sw.swing_right_bars <= i for s in swings)
                ta = eng.analyze_timeframe(sym, "1h", candles, as_of_index=i, swings=swings,
                                           serialize_swings=False)
                bos = ta.get("bos") or {}
                bos_level = bos.get("broken_level") if bos.get("direction") == "BULLISH_BOS" else None
                rec["bos_level"] = bos_level
                rec["bos_direction"] = bos.get("direction")
                hs = [s for s in swings if s.swing_type == "HIGH" and s.bar_index < i]
                if not hs:
                    rec.update(bucket="no_valid_swing", reason="no confirmed high")
                    tags.append(rec)
                    continue
                H = hs[-1]
                rec.update(H=float(H.price), H_index=H.bar_index, H_time=str(H.timestamp))
                if bos_level is None:
                    rec.update(bucket="no_valid_swing", reason="no bullish BOS at signal bar (H not equal to BOS level)")
                    tags.append(rec)
                    continue
                if float(bos_level) != float(H.price):
                    rec.update(bucket="no_valid_swing", reason="H not equal to BOS level")
                    tags.append(rec)
                    continue
                ls_ = [s for s in swings if s.swing_type == "LOW" and s.bar_index < H.bar_index]
                if not ls_:
                    rec.update(bucket="no_valid_swing", reason="no confirmed low before H")
                    tags.append(rec)
                    continue
                L = ls_[-1]
                rec.update(L=float(L.price), L_index=L.bar_index, L_time=str(L.timestamp))
                leg = float(H.price) - float(L.price)
                rec["leg"] = leg
                if leg <= 0:
                    rec.update(bucket="no_valid_swing", reason="non-positive leg")
                    tags.append(rec)
                    continue
                between = [float(x) for x in lows[L.bar_index + 1: H.bar_index]]
                if between and min(between) < float(L.price):
                    rec.update(bucket="no_valid_swing", reason="low between L and H undercuts L")
                    tags.append(rec)
                    continue
                win = [float(x) for x in lows[H.bar_index + 1: i]]
                if not win:
                    rec.update(bucket="no_valid_swing", reason="empty pullback window")
                    tags.append(rec)
                    continue
                mn = min(win)
                depth = (float(H.price) - mn) / leg
                rec.update(pullback_low=mn, depth=depth, bucket=bucket_of(depth), reason=None)
                tags.append(rec)
            print(f"{wid} {sym}: {len(trades)} trades tagged, series {len(candles)} bars", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    payload = {"swing_config_1h": cfg_info, "db_session": rc._W["ro"], "sql_audit": rc._W["sql_audit"],
               "sql_violations": rc._W["violations"], "tags": tags}
    (OUT / "tags.json").write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    print("wrote", OUT / "tags.json", "sql:", rc._W["sql_audit"], "violations:", rc._W["violations"])
    return 0


# --------------------------------------------------------------------------- report
def _pnl_stored(t: dict) -> tuple[float, float, float]:
    return float(t["net"]), float(t["gross"]), abs(float(t["fee"]))


def _pnl_agent1(t: dict) -> tuple[float, float, float]:
    return float(t["net_pnl_usd"]), float(t["gross_pnl_usd"]), abs(float(t["fee_total_usd"]))


def load_source(model: str) -> dict | None:
    """{(wid, sym, signal_time): (net, gross, fee_abs)} or None when files are missing."""
    out = {}
    for wid in WINDOWS:
        for sym in SYMBOLS:
            if model == "stored":
                p, fn = stored_path(sym, wid), _pnl_stored
                rows = json.loads(p.read_text(encoding="utf-8"))
            else:
                p, fn = agent1_path(model, sym, wid), _pnl_agent1
                if not p.exists():
                    return None
                rows = json.loads(p.read_text(encoding="utf-8"))["trades"]
            for t in rows:
                if str(t.get("outcome") or "").upper() in ("", "OPEN", "NONE"):
                    continue
                out[(wid, sym, t["signal_time"])] = fn(t)
    return out


def stats(vals: list[tuple[float, float, float]]) -> dict:
    n = len(vals)
    net = sum(v[0] for v in vals)
    gross = sum(v[1] for v in vals)
    fee = sum(v[2] for v in vals)
    gains = sum(v[0] for v in vals if v[0] > 0)
    lost = sum(-v[0] for v in vals if v[0] < 0)
    pf = (gains / lost) if lost > 0 else (None if gains == 0 else float("inf"))
    fails = []
    if not net > 0:
        fails.append("net<=0")
    if pf is None or not pf > 1.2:
        fails.append("PF<=1.2")
    if n < 15:
        fails.append("trades<15")
    if not fee < gross:
        fails.append("fees>=gross")
    return {"n": n, "net": round(net, 2), "gross": round(gross, 2), "fees": round(fee, 2),
            "pf": (round(pf, 3) if pf not in (None, float("inf")) else pf), "pass": not fails, "fails": fails}


def fmt_pf(pf) -> str:
    return "n/a" if pf is None else ("inf" if pf == float("inf") else f"{pf:.2f}")


def cmd_report() -> int:
    tp = json.loads((OUT / "tags.json").read_text(encoding="utf-8"))
    tags = {(r["window"], r["symbol"], r["signal_time"]): r for r in tp["tags"]}
    models = {"optimistic(stored)": load_source("stored")}
    for m, label in (("opt", "optimistic(agent1)"), ("slip", "slip0.02%")):
        src = load_source(m)
        models[label] = src
    res: dict = {"swing_config_1h": tp["swing_config_1h"], "models": {}, "tag_summary": {}}
    md: list[str] = []

    # tag summary
    reasons: dict = {}
    mism = {"compared": 0, "H_ne_bos": 0, "no_bos": 0}
    entry_bad = 0
    for r in tp["tags"]:
        if r.get("reason"):
            reasons.setdefault(r["window"], {}).setdefault(r["reason"], 0)
            reasons[r["window"]][r["reason"]] += 1
        if r.get("H") is not None:
            if r.get("bos_level") is None:
                mism["no_bos"] += 1
            else:
                mism["compared"] += 1
                mism["H_ne_bos"] += int(float(r["bos_level"]) != float(r["H"]))
        if r.get("entry_eq_close") is False:
            entry_bad += 1
    res["tag_summary"] = {"reasons_by_window": reasons, "H_vs_bos": mism, "entry_ne_signal_close": entry_bad,
                          "total_tagged": len(tp["tags"])}
    md.append(f"Tagged {len(tp['tags'])} trades. H vs BOS level: {mism}. entry != signal close: {entry_bad}")
    md.append(f"No-valid-swing reasons: {json.dumps(reasons)}")

    for label, src in models.items():
        if src is None:
            res["models"][label] = "MISSING"
            md.append(f"\n## {label}: files missing")
            continue
        missing_tag = [k for k in src if k not in tags]
        missing_pnl = [k for k in tags if k not in src]
        mres: dict = {"unjoined_trades": len(missing_tag), "tags_without_pnl": len(missing_pnl), "windows": {}}
        md.append(f"\n## {label}  (trades without tag: {len(missing_tag)}, tags without pnl: {len(missing_pnl)})")
        for wid in WINDOWS:
            keys = [k for k in src if k[0] == wid and k in tags]
            allv = [src[k] for k in keys]
            w: dict = {"unfiltered": stats(allv), "buckets": {}, "per_coin": {}}
            for b in BUCKETS:
                w["buckets"][b] = stats([src[k] for k in keys if tags[k]["bucket"] == b])
            for sym in SYMBOLS:
                ks = [k for k in keys if k[1] == sym]
                w["per_coin"][sym] = {"unfiltered": stats([src[k] for k in ks]),
                                      **{b: stats([src[k] for k in ks if tags[k]["bucket"] == b]) for b in BUCKETS}}
            fib = [w["per_coin"][s]["0.5-0.618"] for s in SYMBOLS]
            w["fib_0.5_0.618_floor_fails"] = sum(1 for x in fib if x["n"] < 15)
            w["fib_0.5_0.618_pass_count"] = sum(1 for x in fib if x["pass"])
            # winner check: 0.5-0.618 beats every other depth bucket and unfiltered on net and PF
            tgt = w["buckets"]["0.5-0.618"]

            def pfv(x):
                return -1.0 if x["pf"] is None else float(x["pf"])
            others = [w["buckets"][b] for b in BUCKETS if b != "0.5-0.618" and w["buckets"][b]["n"] > 0]
            others.append(w["unfiltered"])
            w["fib_wins"] = all(tgt["net"] > o["net"] and pfv(tgt) > pfv(o) for o in others)
            mres["windows"][wid] = w

            md.append(f"\n### {label} {wid}  (0.5-0.618 wins on net and PF: {w['fib_wins']})")
            md.append("| bucket | trades | net USD | PF | fees USD |")
            md.append("|---|---:|---:|---:|---:|")
            for b in BUCKETS + ["unfiltered"]:
                x = w["unfiltered"] if b == "unfiltered" else w["buckets"][b]
                md.append(f"| {b} | {x['n']} | {x['net']:+.2f} | {fmt_pf(x['pf'])} | {x['fees']:.2f} |")
            md.append("per coin (n / net / PF):")
            md.append("| coin | " + " | ".join(BUCKETS) + " | 0.5-0.618 floor | 0.5-0.618 pass |")
            md.append("|---|" + "---|" * (len(BUCKETS) + 2))
            for sym in SYMBOLS:
                pc = w["per_coin"][sym]
                cells = [f"{pc[b]['n']} / {pc[b]['net']:+.1f} / {fmt_pf(pc[b]['pf'])}" for b in BUCKETS]
                f = pc["0.5-0.618"]
                md.append(f"| {sym} | " + " | ".join(cells) +
                          f" | {'FAIL' if f['n'] < 15 else 'ok'} | {'PASS' if f['pass'] else 'fail: ' + ','.join(f['fails'])} |")
            md.append(f"0.5-0.618 trades in window: {tgt['n']}; floor fails {w['fib_0.5_0.618_floor_fails']}/10; "
                      f"report-rule passes {w['fib_0.5_0.618_pass_count']}/10")
        res["models"][label] = mres

    # verdict over required models (optimistic + slip)
    opt = res["models"].get("optimistic(agent1)")
    if not isinstance(opt, dict):
        opt = res["models"]["optimistic(stored)"]
    slip = res["models"].get("slip0.02%")
    wins_opt = all(opt["windows"][w]["fib_wins"] for w in WINDOWS)
    if isinstance(slip, dict):
        wins_slip = all(slip["windows"][w]["fib_wins"] for w in WINDOWS)
        verdict = "worth a later filter" if (wins_opt and wins_slip) else "do not build"
    else:
        verdict = ("do not build (already fails under optimistic)" if not wins_opt
                   else "PENDING slip files")
    res["verdict"] = verdict
    md.append(f"\nVERDICT: {verdict}")
    (OUT / "fib_report.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    (OUT / "fib_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    sys.exit(cmd_tags() if cmd == "tags" else cmd_report())
