"""CANDLE12_V3_EXEC_FIX paper adapter command line.

  smoke             non-acceptance smoke run on seen bars (simulated broker)
  dry-run           replay seen bars through the adapter (simulated broker)
  reconcile         reconcile a store against the engine
  resolve-exception close an exception with an explanation
  freeze-contract   write docs/candle12_v3_oos_contract.lock.json
  oos-evaluate      the contract's OOS read (refuses early / small samples / non-OOS stores)
  run               live loop: --mode virtual_paper_mainnet (mainnet data, simulated broker; the only
                    OOS-eligible mode) or --mode testnet_broker_check (testnet data + testnet broker,
                    guarded, NON_OOS). --recover resumes a saved store after reconciling the venue.
  broker-check      supervised testnet order-lifecycle check; prints BROKER_CHECK_PASS or BROKER_CHECK_FAIL
  backfill-data     idempotent mainnet 1h candle + funding backfill with gap report (--loop for continuous)
  freeze-oos        clean tree, before the OOS start -> versioned lock (supersession verified); commit + tag it
  preflight         launch-readiness gate for virtual_paper_mainnet; PREFLIGHT_PASS or a failure list
  status            store status from disk: mode, state, position, orders, exceptions, OOS eligibility
  kill-switch       engage / release / show the global kill switch (blocks new entries only)

Run from backend/: python -m app.research.candle12_v3_paper.cli <command> ...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.research import candle12_v3 as v3
from app.research.candle12_v3_paper import contract as C
from app.research.candle12_v3_paper import guard
from app.research.candle12_v3_paper import modes as M
from app.research.candle12_v3_paper.adapter import PaperAdapter
from app.research.candle12_v3_paper.broker import SimulatedBroker
from app.research.candle12_v3_paper.reconcile import reconcile
from app.research.candle12_v3_paper.safety import DEFAULT_KILL_SWITCH, SafetyLimits
from app.research.candle12_v3_paper.store import OOS_STORE_MODE, PaperStore, StoreBusy

UTC = timezone.utc
DATA_ROOT = C.BACKEND / "data" / "candle12_v3_paper"
SMOKE_START = datetime(2025, 10, 1, tzinfo=UTC)  # seen bars, inside the stored funding history
SMOKE_END = datetime(2026, 3, 1, tzinfo=UTC)


def _db():
    sys.path.insert(0, str(C.BACKEND / "scripts" / "strategy_rank" / "multicap"))
    import _db  # type: ignore[import-not-found]

    return _db


def load_window(start: datetime, end: datetime) -> tuple[list[dict[str, Any]], int]:
    """Closed bars [start - warm-up, end) and the index of ``start``."""
    db = _db()
    raw = db.load_candles(C.SYMBOL, C.TIMEFRAME, start - timedelta(hours=C.WARMUP_BARS + 400), end)
    first = next(i for i, b in enumerate(raw) if b["time"] >= start)
    candles = raw[first - C.WARMUP_BARS:]
    return candles, C.WARMUP_BARS


def load_funding(start: datetime, end: datetime) -> list[dict[str, Any]]:
    db = _db()

    async def go() -> list[dict[str, Any]]:
        conn = await db._connect()
        try:
            rows = await conn.fetch(
                "SELECT funding_time, funding_rate, mark_price FROM grid_funding_settlements "
                "WHERE symbol = $1 AND funding_time >= $2 AND funding_time < $3 ORDER BY funding_time",
                C.SYMBOL, start, end)
        finally:
            await conn.close()
        return [{"time": r[0], "rate": float(r[1]), "mark_price": r[2]} for r in rows]

    return asyncio.run(go())


def _parse_day(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def cmd_smoke(args: argparse.Namespace) -> int:
    from app.research.candle12_v3_paper.smoke import run_smoke

    start, end = _parse_day(args.start), _parse_day(args.end)
    candles, first = load_window(start, end)
    funding = load_funding(candles[0]["time"], end)
    root = Path(args.store) if args.store else DATA_ROOT / "smoke" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    result = run_smoke(candles, first_trade_index=first, store_root=root, funding_rows=funding)
    print(json.dumps(result, indent=1, default=str))
    return 0 if result["result"] == "SMOKE_PASS" else 1


def cmd_dry_run(args: argparse.Namespace) -> int:
    start, end = _parse_day(args.start), _parse_day(args.end)
    candles, first = load_window(start, end)
    v3.check_oos_guard(candles)
    funding = load_funding(candles[0]["time"], end)
    cfg = C.contract_config()
    step, _, min_notional, _ = cfg.exchange_filter(C.SYMBOL)
    root = Path(args.store) if args.store else DATA_ROOT / "dry_run" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    store = PaperStore(root, mode="DRY_RUN", manifest={"window_start": start.isoformat(), "window_end": end.isoformat()})
    broker = SimulatedBroker(slippage=cfg.entry_slippage, fee_rate=cfg.taker_fee, step=step, min_notional=min_notional)
    PaperAdapter(broker=broker, store=store, cfg=cfg, funding_rows=funding).replay(candles, first)
    rec = reconcile(paper_trades=store.read("trades"), paper_signals=store.read("signals"), candles=candles,
                    first_trade_index=first, cfg=cfg, symbol=C.SYMBOL, timeframe=C.TIMEFRAME, funding_rows=funding)
    for e in rec["exceptions"]:
        store.exception(e["id"], e["category"], **{k: v for k, v in e.items() if k not in ("id", "category")})
    (root / "reconciliation.json").write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"store": str(root), "status": rec["status"], "paper_trades": rec["paper_trades"],
                      "engine_trades": rec["engine_trades"], "exceptions": len(rec["exceptions"])}, indent=1))
    return 0 if rec["status"] == "RECONCILED" else 1


def cmd_reconcile(args: argparse.Namespace) -> int:
    root = Path(args.store)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest["mode"] == M.spec(M.TESTNET_BROKER_CHECK).store_mode:
        return _refuse("TESTNET_NOT_COMPARABLE", "testnet fills are never reconciled against the engine under "
                                                 "OOS tolerances; testnet_broker_check validates the API only")
    if manifest["mode"] == "PAPER":
        return _refuse(M.MIXED_DATA_BROKER_MODE, "legacy PAPER stores mixed mainnet candles with testnet fills; "
                                                 "they are never reconciled against the engine")
    store = PaperStore(root, mode=manifest["mode"])
    start = datetime.fromisoformat(manifest["history_start"])
    live = manifest["mode"] == OOS_STORE_MODE
    end = datetime.now(UTC) if live else datetime.fromisoformat(manifest["window_end"])
    db = _db()
    candles = db.load_candles(C.SYMBOL, C.TIMEFRAME, start, end)
    if not live:
        v3.check_oos_guard(candles)
    funding = load_funding(start, end)
    rec = reconcile(paper_trades=store.read("trades"), paper_signals=store.read("signals"), candles=candles,
                    first_trade_index=int(manifest["first_trade_index"]), cfg=C.contract_config(),
                    symbol=C.SYMBOL, timeframe=C.TIMEFRAME, funding_rows=funding)
    for e in rec["exceptions"]:
        store.exception(e["id"], e["category"], **{k: v for k, v in e.items() if k not in ("id", "category")})
    (root / "reconciliation.json").write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"status": rec["status"], "exceptions": len(rec["exceptions"]),
                      "open_exceptions": len(store.open_exceptions())}, indent=1))
    return 0 if not store.open_exceptions() else 1


def cmd_resolve(args: argparse.Namespace) -> int:
    manifest = json.loads((Path(args.store) / "manifest.json").read_text(encoding="utf-8"))
    PaperStore(Path(args.store), mode=manifest["mode"]).resolve_exception(args.id, args.note, args.by)
    return 0


def cmd_freeze(args: argparse.Namespace) -> int:
    path = Path(args.lock) if args.lock else C.LOCK_PATH
    if path.exists():
        # Never overwrite a lock, frozen or not: a new contract version goes through freeze-oos
        # under a new CONTRACT_ID, with the old lock preserved as superseded.
        return _refuse("LOCK_EXISTS", f"{path} exists; a lock is never overwritten")
    lock = C.write_lock(path)
    print(json.dumps({"lock": str(path), "config_fingerprint": lock["config_fingerprint"],
                      "code_fingerprint": lock["code_fingerprint"]["combined"]}, indent=1))
    return 0


def cmd_oos(args: argparse.Namespace) -> int:
    from app.research.candle12_v3_paper.oos import OOSRefused, evaluate_oos

    now = datetime.now(UTC)
    if now < C.earliest_evaluation() and not args.diagnostic:
        print(f"REFUSED: TOO_EARLY (earliest {C.earliest_evaluation().isoformat()})")
        return 2
    if os.environ.get(v3.OOS_ALLOW_ENV) != "1":
        print(f"REFUSED: set {v3.OOS_ALLOW_ENV}=1 for the OOS read")
        return 2
    root = Path(args.store)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    store = PaperStore(root, mode=manifest["mode"])
    db = _db()
    candles = db.load_candles(C.SYMBOL, C.TIMEFRAME, C.OOS_START - timedelta(hours=C.WARMUP_BARS), now)
    funding = load_funding(candles[0]["time"], now)
    try:
        read = evaluate_oos(candles=candles, funding_rows=funding, now=now, lock=C.load_lock(),
                            open_exceptions=store.open_exceptions(), store_mode=manifest["mode"],
                            diagnostic=args.diagnostic, read_log=root / "oos_reads.jsonl", manifest=manifest)
    except OOSRefused as exc:
        print(f"REFUSED: {exc.reason} {exc.detail}")
        return 2
    print(json.dumps(read, indent=1, default=str))
    return 0


def run_live(adapter: PaperAdapter, source: Any, *, funding_source: Any | None, poll_s: float,
             clock=lambda: datetime.now(UTC), max_loops: int | None = None, state: dict[str, Any] | None = None,
             first_trade_at: datetime | None = None, history_start: datetime | None = None,
             heartbeat_minutes: float | None = None, sleep=time.sleep,
             drift_check: Any | None = None) -> dict[str, Any] | None:
    """Poll closed klines; open bar k when it starts; close it when it is final.

    With ``state`` the adapter is resumed from its snapshot and reconciled with
    the venue (recovery.py) before any bar is processed; RecoveryHalted propagates.
    """
    from app.research.candle12_v3_paper.recovery import run_recovery

    if getattr(source, "base_url", None):
        M.check_pair(source.base_url, adapter.broker.name)
    first_trade_at = first_trade_at or C.OOS_START
    now = clock()
    start_at = history_start or first_trade_at - adapter.interval * C.WARMUP_BARS
    history = source.history(start_at, now)

    def refresh_funding(at: datetime) -> None:
        if funding_source is not None:
            adapter.funding_rows = sorted(
                ({**r, "time": v3._as_utc(r["time"])} for r in funding_source.settlements(start_at, at)),
                key=lambda r: r["time"])

    recovery = None
    if state:
        refresh_funding(now)
        adapter.resume(history, int(state["first_trade_index"]), state)
        recovery = run_recovery(adapter, now=now)
    else:
        last = history[-1]["time"]
        oos_index = next((i for i, b in enumerate(history) if b["time"] >= first_trade_at), None)
        if oos_index is None:
            oos_index = len(history) + int((first_trade_at - (last + adapter.interval)) / adapter.interval)
        adapter.start(history, first_trade_index=oos_index)
    every = timedelta(minutes=heartbeat_minutes or adapter.safety.heartbeat_minutes)
    next_beat = clock()
    opened: int | None = None
    loops = 0
    while max_loops is None or loops < max_loops:
        loops += 1
        now = clock()
        for bar in source.history(adapter.closed[-1]["time"] + adapter.interval, now):
            k = len(adapter.closed)
            if opened != k:
                adapter.on_bar_open(k, bar["time"], bar["open"], now)
            refresh_funding(now)
            adapter.on_bar_close(bar, now)
            opened = None
        forming = source.forming_bar(now)
        k = len(adapter.closed)
        if forming and forming["time"] == adapter.closed[-1]["time"] + adapter.interval and opened != k:
            adapter.on_bar_open(k, forming["time"], forming["open"], now, current_price=source.last_price())
            opened = k
        if now >= next_beat:
            next_beat = now + every
            drift = drift_check() if drift_check else None
            if drift:
                adapter.store.alert("FINGERPRINT_DRIFT", codes=drift)
                adapter.halt("FINGERPRINT_DRIFT", codes=drift)
            adapter.store.refresh_run_lock()
            adapter.store.heartbeat(last_closed=adapter.snapshot()["last_closed_time"], slots=list(adapter.slots),
                                    halted=adapter.halted, kill_switch=adapter.safety.kill_switch_engaged(),
                                    inflight=list(adapter.inflight))
        if max_loops is None or loops < max_loops:
            sleep(poll_s)
    return recovery


def _refuse(code: str, detail: str = "") -> int:
    print(f"REFUSED: {code} {detail}".rstrip())
    return 2


def cmd_run(args: argparse.Namespace) -> int:
    from app.research.candle12_v3_paper.binance import PAPER_ENV, BinanceTestnetBroker, PaperGuardError
    from app.research.candle12_v3_paper.feeds import BinanceFundingSource, BinanceKlineSource
    from app.research.candle12_v3_paper.recovery import RecoveryHalted

    if args.paper and os.environ.get(PAPER_ENV) != "1":
        return _refuse("PAPER_ENV", f"--paper needs {PAPER_ENV}=1")
    if args.paper and args.mode != M.TESTNET_BROKER_CHECK:
        # The old `run --paper` sent testnet orders against mainnet candles.
        return _refuse(M.MIXED_DATA_BROKER_MODE, "--paper (testnet fills) needs --mode testnet_broker_check "
                                                 "(testnet candles); mainnet candles go with the simulated broker")
    try:
        spec = M.resolve(args.mode, data_base=args.data_url,
                         broker_name=M.TESTNET_BROKER if args.mode == M.TESTNET_BROKER_CHECK else M.SIMULATED)
    except M.ModeError as exc:
        return _refuse(exc.code, exc.detail)

    cfg = C.contract_config()
    step, _, min_notional, _ = cfg.exchange_filter(C.SYMBOL)
    root = Path(args.store) if args.store else DATA_ROOT / spec.mode / "live"
    pre = guard.preflight(spec.mode)
    eligible = bool(pre["eligible"])
    if spec.mode == M.VIRTUAL_PAPER_MAINNET and not eligible and not args.allow_non_oos:
        if (root / "manifest.json").exists():
            PaperStore(root, mode=spec.store_mode).alert("FINGERPRINT_DRIFT", codes=pre["codes"])
        return _refuse(pre["status"], f"{pre['codes']}; an OOS-eligible run needs a clean freeze-oos lock "
                                      "(or pass --allow-non-oos for a NON_OOS virtual run)")
    if (root / "manifest.json").exists():
        existing = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if existing.get("counts_toward_oos") and not eligible:
            return _refuse(pre["status"], "this store counts toward OOS and the fingerprints drifted; it cannot resume")
    if spec.mode == M.VIRTUAL_PAPER_MAINNET and eligible:
        from app.research.candle12_v3_paper.preflight import PREFLIGHT_PASS, run_preflight

        pf = run_preflight(spec.mode, [C.SYMBOL], root, recover=args.recover)
        if pf["result"] != PREFLIGHT_PASS:
            print(json.dumps({"failures": pf["failures"], "warnings": pf["warnings"]}, indent=1, default=str))
            return _refuse("PREFLIGHT_FAIL", ", ".join(f["name"] for f in pf["failures"]))
        if pf["warnings"]:
            print(json.dumps({"warnings": pf["warnings"]}, indent=1, default=str))

    if spec.mode == M.TESTNET_BROKER_CHECK:
        if not args.paper:
            return _refuse("PAPER_FLAG_REQUIRED", "testnet_broker_check sends testnet orders: pass --paper explicitly")
        if os.environ.get(PAPER_ENV) != "1":
            return _refuse("PAPER_ENV", f"testnet_broker_check needs {PAPER_ENV}=1")
        try:
            broker: Any = BinanceTestnetBroker(symbol=C.SYMBOL, api_key=os.environ.get("BINANCE_TESTNET_API_KEY", ""),
                                               api_secret=os.environ.get("BINANCE_TESTNET_API_SECRET", ""),
                                               base_url=spec.data_base)
        except PaperGuardError as exc:
            return _refuse("PAPER_GUARD", str(exc))
    else:
        broker = SimulatedBroker(slippage=cfg.entry_slippage, fee_rate=cfg.taker_fee, step=step,
                                 min_notional=min_notional, equity=cfg.account_equity, leverage=cfg.leverage)

    counts = spec.counts_toward_oos and eligible
    store = PaperStore(root, mode=spec.store_mode, manifest={
        "execution_mode": spec.mode, "contract_id": C.CONTRACT_ID, "config_fingerprint": C.config_fingerprint(),
        "code_fingerprint": C.code_fingerprint()["combined"], "data_venue": spec.data_venue, "broker_venue": spec.broker,
        "counts_toward_oos": counts, "strategy_validation": spec.strategy_validation,
        "label": M.OOS_ELIGIBLE if counts else M.NON_OOS,
    })
    try:
        store.acquire_run_lock()
    except StoreBusy as exc:
        return _refuse("CONCURRENT_RUN", str(exc))
    try:
        return _run_with_lock(args, spec, store, root, broker, cfg)
    finally:
        store.release_run_lock()


def _run_with_lock(args: argparse.Namespace, spec: Any, store: PaperStore, root: Path, broker: Any,
                   cfg: Any) -> int:
    from app.research.candle12_v3_paper.feeds import BinanceFundingSource, BinanceKlineSource
    from app.research.candle12_v3_paper.recovery import RecoveryHalted

    state = store.load_state()
    if state and not args.recover:
        return _refuse("RECOVERY_REQUIRED", f"{root} has a saved state; restart with --recover")
    if args.recover and not state:
        print(f"nothing to recover in {root}; starting fresh")
    safety = SafetyLimits(kill_switch_path=Path(args.kill_switch) if args.kill_switch else DEFAULT_KILL_SWITCH,
                          max_daily_order_attempts=args.max_daily_orders,
                          max_consecutive_broker_errors=args.max_broker_errors,
                          heartbeat_minutes=args.heartbeat_minutes)
    adapter = PaperAdapter(broker=broker, store=store, cfg=cfg, safety=safety,
                           require_funding=spec.mode == M.VIRTUAL_PAPER_MAINNET)
    source = BinanceKlineSource(C.SYMBOL, C.TIMEFRAME, base_url=spec.data_base)
    funding = BinanceFundingSource(C.SYMBOL, base_url=spec.data_base)
    if spec.mode == M.TESTNET_BROKER_CHECK:
        now = datetime.now(UTC)
        first_at = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        first_at = C.OOS_START
    hist_start = (datetime.fromisoformat(store.manifest["history_start"])
                  if state and store.manifest.get("history_start") else None)

    def drift() -> list[str] | None:
        if not store.manifest.get("counts_toward_oos"):
            return None
        return guard.fingerprint_status()["codes"] or None

    try:
        run_live(adapter, source, funding_source=funding, poll_s=args.poll, state=state if args.recover else None,
                 first_trade_at=first_at, history_start=hist_start, drift_check=drift)
    except RecoveryHalted as exc:
        print(f"HALTED: {exc.code} {json.dumps(exc.detail, default=str)}")
        return 3
    return 0


def cmd_broker_check(args: argparse.Namespace) -> int:
    from app.research.candle12_v3_paper.binance import PAPER_ENV, BinanceTestnetBroker, PaperGuardError
    from app.research.candle12_v3_paper.broker_check import BROKER_CHECK_PASS, run_broker_check

    if not args.paper:
        return _refuse("PAPER_FLAG_REQUIRED", "broker-check sends testnet orders: pass --paper explicitly")
    if os.environ.get(PAPER_ENV) != "1":
        return _refuse("PAPER_ENV", f"broker-check needs {PAPER_ENV}=1")
    spec = M.spec(M.TESTNET_BROKER_CHECK)
    try:
        broker = BinanceTestnetBroker(symbol=args.symbol, api_key=os.environ.get("BINANCE_TESTNET_API_KEY", ""),
                                      api_secret=os.environ.get("BINANCE_TESTNET_API_SECRET", ""),
                                      base_url=args.base_url or spec.data_base)
    except PaperGuardError as exc:
        return _refuse("PAPER_GUARD", str(exc))
    root = Path(args.store) if args.store else DATA_ROOT / "broker_check" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    store = PaperStore(root, mode=spec.store_mode, manifest={
        "execution_mode": spec.mode, "symbol": args.symbol.upper(), "counts_toward_oos": False,
        "strategy_validation": False, "label": M.NON_OOS})
    result = run_broker_check(broker, store, max_trades=args.max_trades, exit_wait_s=args.exit_wait, poll_s=args.poll)
    store.update_manifest(broker_check_result=result["result"])
    print(json.dumps(result, indent=1, default=str))
    print(result["result"])
    return 0 if result["result"] == BROKER_CHECK_PASS else 1


def _parse_when(s: str) -> datetime:
    if s.lower() == "now":
        return datetime.now(UTC)
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def cmd_backfill(args: argparse.Namespace) -> int:
    from app.research.candle12_v3_paper.backfill import PostgresRepository, backfill, run_forever

    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    state_path = Path(args.state) if args.state else DATA_ROOT / "backfill_state.json"
    repo = PostgresRepository()

    def once() -> dict[str, Any]:
        return backfill(symbols, _parse_when(args.start), _parse_when(args.end), repo=repo, state_path=state_path)

    if args.loop:
        failures = run_forever(once, interval_s=args.interval_s, max_loops=args.max_loops,
                               emit=lambda r: print(json.dumps(r, default=str), flush=True))
        return 0 if failures == 0 else 1
    report = once()
    print(json.dumps(report, indent=1, default=str))
    return 0 if report["ok"] else 1


def cmd_freeze_oos(args: argparse.Namespace) -> int:
    from app.research.candle12_v3_paper.freeze import FreezeError, freeze_oos

    try:
        out = freeze_oos(lock_path=Path(args.lock) if args.lock else None)
    except FreezeError as exc:
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, default=str)
        return _refuse(exc.code, detail)
    print(json.dumps(out, indent=1, default=str))
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    from app.research.candle12_v3_paper.preflight import PREFLIGHT_PASS, run_preflight

    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    root = Path(args.store) if args.store else DATA_ROOT / args.mode / "live"
    result = run_preflight(args.mode, symbols, root, recover=args.recover)
    print(json.dumps(result, indent=1, default=str))
    print(result["result"])
    return 0 if result["result"] == PREFLIGHT_PASS else 1


def cmd_status(args: argparse.Namespace) -> int:
    from app.research.candle12_v3_paper.status import build_status

    print(json.dumps(build_status(Path(args.store)), indent=1, default=str))
    return 0


def cmd_kill_switch(args: argparse.Namespace) -> int:
    from app.research.candle12_v3_paper.safety import engage_kill_switch, release_kill_switch

    path = Path(args.path) if args.path else DEFAULT_KILL_SWITCH
    if args.action == "engage":
        engage_kill_switch(path, by=args.by, reason=args.reason or "")
    elif args.action == "release":
        release_kill_switch(path)
    print(json.dumps({"kill_switch": path.exists(), "path": str(path)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="candle12_v3_paper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("smoke")
    s.add_argument("--start", default=SMOKE_START.date().isoformat())
    s.add_argument("--end", default=SMOKE_END.date().isoformat())
    s.add_argument("--store")
    d = sub.add_parser("dry-run")
    d.add_argument("--start", required=True)
    d.add_argument("--end", required=True)
    d.add_argument("--store")
    r = sub.add_parser("reconcile")
    r.add_argument("--store", required=True)
    x = sub.add_parser("resolve-exception")
    x.add_argument("--store", required=True)
    x.add_argument("--id", required=True)
    x.add_argument("--note", required=True)
    x.add_argument("--by", required=True)
    f = sub.add_parser("freeze-contract")
    f.add_argument("--lock")
    o = sub.add_parser("oos-evaluate")
    o.add_argument("--store", required=True)
    o.add_argument("--diagnostic", action="store_true",
                   help="NON-ACCEPTANCE diagnostic read: allowed early, logged, never an OOS pass")
    run = sub.add_parser("run")
    run.add_argument("--mode", choices=M.MODES, help="required: virtual_paper_mainnet or testnet_broker_check")
    run.add_argument("--paper", action="store_true",
                     help="legacy flag: testnet fills; only valid with --mode testnet_broker_check")
    run.add_argument("--recover", action="store_true", help="resume a saved store after venue reconciliation")
    run.add_argument("--allow-non-oos", action="store_true",
                     help="run virtual_paper_mainnet without a clean freeze; the store is NON_OOS forever")
    run.add_argument("--data-url", help="override the market-data host (must match the mode's venue)")
    run.add_argument("--store")
    run.add_argument("--poll", type=float, default=15.0)
    run.add_argument("--kill-switch", help=f"kill-switch file (default {DEFAULT_KILL_SWITCH})")
    run.add_argument("--max-daily-orders", type=int, default=20)
    run.add_argument("--max-broker-errors", type=int, default=5)
    run.add_argument("--heartbeat-minutes", type=float, default=5.0)
    bc = sub.add_parser("broker-check")
    bc.add_argument("--symbol", default=C.SYMBOL)
    bc.add_argument("--paper", action="store_true", help="required: this command sends testnet orders")
    bc.add_argument("--max-trades", type=int, default=1)
    bc.add_argument("--store")
    bc.add_argument("--base-url", help="testnet REST host (default https://demo-fapi.binance.com)")
    bc.add_argument("--exit-wait", type=float, default=900.0, help="seconds to wait for a tight exit to trigger")
    bc.add_argument("--poll", type=float, default=5.0)
    bf = sub.add_parser("backfill-data")
    bf.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    bf.add_argument("--start", required=True)
    bf.add_argument("--end", default="now")
    bf.add_argument("--state", help="watermark file (default data/candle12_v3_paper/backfill_state.json)")
    bf.add_argument("--loop", action="store_true", help="foreground continuous ingestion")
    bf.add_argument("--interval-s", type=float, default=3600.0)
    bf.add_argument("--max-loops", type=int)
    fz = sub.add_parser("freeze-oos")
    fz.add_argument("--lock")
    pf = sub.add_parser("preflight")
    pf.add_argument("--mode", required=True, choices=M.MODES)
    pf.add_argument("--symbols", default=C.SYMBOL)
    pf.add_argument("--store")
    pf.add_argument("--recover", action="store_true", help="check a restart of a saved store")
    st = sub.add_parser("status")
    st.add_argument("--store", required=True)
    ks = sub.add_parser("kill-switch")
    ks.add_argument("action", choices=("engage", "release", "status"))
    ks.add_argument("--by", default="operator")
    ks.add_argument("--reason")
    ks.add_argument("--path")
    args = ap.parse_args(argv)
    return {
        "smoke": cmd_smoke, "dry-run": cmd_dry_run, "reconcile": cmd_reconcile, "resolve-exception": cmd_resolve,
        "freeze-contract": cmd_freeze, "oos-evaluate": cmd_oos, "run": cmd_run, "broker-check": cmd_broker_check,
        "backfill-data": cmd_backfill, "freeze-oos": cmd_freeze_oos, "status": cmd_status, "preflight": cmd_preflight,
        "kill-switch": cmd_kill_switch,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
