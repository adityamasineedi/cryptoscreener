"""Agents 3/4 - signal extraction from EXISTING strategy code (read-only, no app edits).

Each source yields normalized signals: the bar that produced the signal, its side, the strategy's own
(native) stop and first target. Fills, exits, sizing and costs are NOT taken from the engines; the
sleeve simulator re-does them (next-bar open entry, taker + slippage).

Sources (call sites copied from scripts/strategy_rank/*):
  combo   app.research.combination_backtest.run_combination_backtest via run_combo_rank._run_walk
          (position-gated walk; its own exits only decide which later signals it could see)
  mc      app.research.multi_cap_strategies.<mod>.generate_candidates + adapter.candidates_to_trades (ungated)
  spr     app.research.short_pullback_rejection.signals.generate_signals_for_symbol (ungated, 3-bar cooldown)
  ose     app.research.ohlcv_structure_experiment.engine.breaks_for_variant + qualify (ungated)
  c12     app.research.candle12_v2.run_candle12_v2 (gated, shared lock across sides)

Usage: python -B a34_signals.py --phase P1 [--only combo] [--workers 6]
  P1 = signals up to 2025-08-31 (research + validation).  P2 = up to 2026-02-13 (after the freeze only).
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import BACKEND, OUT, STUDY_START, TF_SEC, coins_for_tf  # noqa: E402

SDIR = OUT / "signals"
PHASE_END = {"P1": "2025-08-31", "P2": "2026-02-13"}
UTC = timezone.utc

COMBO_IDS = ["COMBO_01", "COMBO_02_LOCAL", "COMBO_02", "COMBO_03_TRANSITION_B", "COMBO_02_V2_1_A"]
V2_IDS = {"COMBO_02_V2_1_A"}
NEEDS_1H_HTF = {"COMBO_02", "COMBO_03_TRANSITION_B", "COMBO_02_V2_1_A"}


def _rank_module():
    p = BACKEND / "scripts" / "strategy_rank" / "combo" / "run_combo_rank.py"
    spec = importlib.util.spec_from_file_location("run_combo_rank", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["run_combo_rank"] = m
    spec.loader.exec_module(m)
    return m


_M: dict = {}


def _init() -> None:
    m = _rank_module()
    m._worker_init(quiet=True)
    _M["rank"] = m


def _iso(t) -> str:
    if isinstance(t, str):
        t = datetime.fromisoformat(t.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return t.astimezone(UTC).isoformat()


def _sig(sid, coin, tf, side, signal_open, ref, stop, tp1, meta=None, signal_close=None):
    so = datetime.fromisoformat(_iso(signal_open))
    sc = datetime.fromisoformat(_iso(signal_close)) if signal_close is not None else so + timedelta(seconds=TF_SEC[tf])
    return {"sid": sid, "coin": coin, "tf": tf, "side": side, "signal_open": so.isoformat(),
            "signal_close": int(sc.timestamp()), "ref": float(ref), "stop": float(stop),
            "tp1": (float(tp1) if tp1 is not None else None), "meta": meta or {}}


# ---------------------------------------------------------------- sources
def src_combo(strategy: str, coin: str, tf: str, phase: str) -> list[dict]:
    m = _M["rank"]
    m._CTX["window"] = {"id": f"SLEEVE_{phase}", "start_date": STUDY_START.date().isoformat(),
                        "end_date_inclusive": PHASE_END[phase],
                        "start_utc": STUDY_START.isoformat(), "end_utc": PHASE_END[phase]}
    out = []
    dirs = [None] if strategy in V2_IDS else ["LONG", "SHORT"]
    for d in dirs:
        res = _M["rank"]._W["loop"].run_until_complete(m._run_walk(strategy, coin, tf, d, None))
        for t in res.get("walk_trades") or []:
            snap = t.get("condition_snapshot") or {}
            side = str(t.get("direction")).upper()
            sid = strategy
            if strategy in V2_IDS:
                sid = f"{strategy}:{snap.get('playbook') or 'UNKNOWN'}"
            out.append(_sig(sid, coin, tf, side, t["signal_time"], t["entry_price"], t["stop_price"], t.get("tp1"),
                            {"entry_type": snap.get("entry_type"), "playbook": snap.get("playbook"),
                             "engine_outcome": t.get("outcome")}))
    return out


def src_mc(strategy: str, coin: str, tf: str, phase: str) -> list[dict]:
    from _db import load_candles
    from app.research.multi_cap_strategies import large_cap_sweep_choch, mid_cap_fvg_discount, small_cap_volume_bos
    from app.research.multi_cap_strategies.adapter import MultiCapStrategyAdapter
    from app.research.multi_cap_strategies.config import MultiCapResearchConfig

    mod = {"LARGE_CAP_SWEEP_CHOCH": large_cap_sweep_choch, "MID_CAP_FVG_DISCOUNT": mid_cap_fvg_discount,
           "SMALL_CAP_VOLUME_BOS": small_cap_volume_bos}[strategy]
    cfg = MultiCapResearchConfig()
    end = datetime.fromisoformat(PHASE_END[phase]).replace(tzinfo=UTC) + timedelta(days=1)
    candles = load_candles(coin, tf, STUDY_START - timedelta(seconds=TF_SEC[tf] * 1000), end)
    first = next(i for i, c in enumerate(candles) if c["time"] >= STUDY_START)
    cands = mod.generate_candidates(coin, tf, candles, config=cfg, index_start=first)
    trades = MultiCapStrategyAdapter(cfg).candidates_to_trades(cands, asset_group="study", period_label="FULL")
    return [_sig(strategy, coin, tf, t.direction, candles[t.entry_index]["time"], t.entry_price, t.stop_price, t.tp1)
            for t in trades]


def src_spr(strategy: str, coin: str, tf: str, phase: str) -> list[dict]:
    from _db import load_candles
    from app.research.short_pullback_rejection.constants import DEFAULT_THRESHOLDS
    from app.research.short_pullback_rejection.signals import generate_signals_for_symbol

    end = datetime.fromisoformat(PHASE_END[phase]).replace(tzinfo=UTC) + timedelta(days=1)
    c1 = load_candles(coin, "1h", STUDY_START - timedelta(days=180), end)
    c4 = load_candles(coin, "4h", STUDY_START - timedelta(days=180), end)
    th = {**DEFAULT_THRESHOLDS, "risk_usd": 20.0, "principal_usd": 1000.0}
    sigs = generate_signals_for_symbol(coin, c1, c4, thresholds=th, stop_buffer_atr=0.25,
                                       tp_mode="nearest_support", tp_r=None)
    out = []
    for s in sigs:
        so = c1[int(s["entry_index"])]["time"]
        if so < STUDY_START:
            continue
        out.append(_sig(strategy, coin, "1h", "SHORT", so, s["entry_price"], s["stop_price"], s["take_profit_price"]))
    return out


def src_ose(strategy: str, coin: str, tf: str, phase: str) -> list[dict]:
    from _db import load_candles
    from app.research.ohlcv_structure_experiment.engine import breaks_for_variant, qualify

    variant = strategy.replace("OSE_", "")
    end = datetime.fromisoformat(PHASE_END[phase]).replace(tzinfo=UTC) + timedelta(days=1)
    candles = load_candles(coin, tf, STUDY_START - timedelta(seconds=TF_SEC[tf] * 500), end)
    first = next(i for i, c in enumerate(candles) if c["time"] >= STUDY_START)
    out = []
    for ev in breaks_for_variant(candles, symbol=coin, variant=variant):
        if ev.break_index < first or ev.call_type != "bos_with_trend":
            continue
        rec = qualify(ev, candles, variant)
        if rec.status != "entry_qualified" or rec.entry_index is None or rec.entry_index >= len(candles):
            continue
        side = "LONG" if str(ev.direction).upper().startswith("L") else "SHORT"
        q = rec.entry_index - 1  # qualification bar; entry at the next bar's open
        out.append(_sig(strategy, coin, tf, side, candles[q]["time"], candles[q]["close"], ev.stop, None,
                        {"call_type": ev.call_type}))
    return out


def src_c12(strategy: str, coin: str, tf: str, phase: str) -> list[dict]:
    from _db import load_candles
    from app.research import candle12_v2 as c12

    end = datetime.fromisoformat(PHASE_END[phase]).replace(tzinfo=UTC) + timedelta(days=1)
    candles = load_candles(coin, tf, STUDY_START - timedelta(seconds=TF_SEC[tf] * 1000), end)
    first = next(i for i, c in enumerate(candles) if c["time"] >= STUDY_START)
    res = c12.run_candle12_v2(coin, tf, candles, index_start=first, progress_every=0)
    return [_sig(strategy, coin, tf, str(t["direction"]).upper(), t["signal_time"], t["entry_price"], t["stop_price"],
                 t.get("tp1")) for t in res.get("trades") or []]


SOURCES = {"combo": src_combo, "mc": src_mc, "spr": src_spr, "ose": src_ose, "c12": src_c12}


def plan() -> list[tuple[str, str, str, str]]:
    tasks = []
    for s in COMBO_IDS:
        for tf in ("1h", "4h", "1d", "15m"):
            if s in V2_IDS:
                if tf == "15m":
                    continue
                coins = coins_for_tf("15m")  # V2 needs 15m confirmation candles over the window
            elif s == "COMBO_03_TRANSITION_B" and tf in ("15m", "1d"):
                continue
            else:
                coins = coins_for_tf(tf)
                if s in NEEDS_1H_HTF:
                    coins = [c for c in coins if c in coins_for_tf("1h")]
            tasks += [("combo", s, c, tf) for c in coins]
    for s in ("LARGE_CAP_SWEEP_CHOCH", "MID_CAP_FVG_DISCOUNT", "SMALL_CAP_VOLUME_BOS"):
        for tf in ("15m", "1h"):
            tasks += [("mc", s, c, tf) for c in coins_for_tf(tf)]
    tasks += [("spr", "SHORT_PULLBACK_REJECTION_RESEARCH", c, "1h") for c in coins_for_tf("1h")]
    for v in ("A_corrected_baseline", "B_acceptance", "C_retest", "D_faster_confirmation"):
        for tf in ("15m", "1h", "4h"):
            tasks += [("ose", f"OSE_{v}", c, tf) for c in coins_for_tf(tf)]
    for tf in ("15m", "1h"):
        tasks += [("c12", "CANDLE12_V2", c, tf) for c in coins_for_tf(tf)]
    return tasks


def _path(task, phase) -> Path:
    src, s, c, tf = task
    return SDIR / phase / f"{src}__{s}__{c}__{tf}.json"


def run_task(task, phase) -> tuple:
    t0 = time.perf_counter()
    if _path(task, phase).exists():  # another runner finished it
        return task, "OK (exists)", 0, 0.0
    try:
        sigs = SOURCES[task[0]](task[1], task[2], task[3], phase)
        p = _path(task, phase)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"task": task, "phase": phase, "seconds": time.perf_counter() - t0,
                                   "signals": sigs}), encoding="utf-8")
        os.replace(tmp, p)
        return task, "OK", len(sigs), time.perf_counter() - t0
    except Exception as exc:  # noqa: BLE001
        return task, f"ERROR {type(exc).__name__}: {exc} {traceback.format_exc()[-800:]}", 0, time.perf_counter() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=["P1", "P2"])
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--shard", default="")  # "i/n": serial, no process pool
    a = ap.parse_args()
    tasks = [t for t in plan() if (not a.only or t[0] in a.only.split(",") or t[1] in a.only.split(","))]
    tasks = [t for t in tasks if not _path(t, a.phase).exists()]
    # slow V2 walks first so they overlap with everything else
    tasks.sort(key=lambda t: (0 if t[1] in V2_IDS else 1 if t[3] == "15m" else 2))
    if a.limit:
        tasks = tasks[: a.limit]
    print(f"tasks={len(tasks)}", flush=True)
    log = OUT / f"a34_extract_{a.phase}.log"
    if a.shard:
        i, n = (int(x) for x in a.shard.split("/"))
        tasks = tasks[i::n]
        _init()
        for k, t in enumerate(tasks, 1):
            task, st, cnt, sec = run_task(t, a.phase)
            line = f"[{a.shard}] {k}/{len(tasks)} {task} {st[:400]} n={cnt} {sec:.0f}s"
            print(line, file=sys.stderr, flush=True)
            with open(log, "a", encoding="utf-8") as fh:
                print(line, file=fh)
        return
    with ProcessPoolExecutor(max_workers=a.workers, initializer=_init) as ex:
        futs = [ex.submit(run_task, t, a.phase) for t in tasks]
        for i, f in enumerate(as_completed(futs), 1):
            task, st, n, sec = f.result()
            line = f"{i}/{len(tasks)} {task} {st[:400]} n={n} {sec:.0f}s"
            print(line, flush=True)
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")


if __name__ == "__main__":
    main()
