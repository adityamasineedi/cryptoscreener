"""CANDLE12_V3_EXEC_FIX paper adapter command line.

  smoke             non-acceptance smoke run on seen bars (simulated broker)
  dry-run           replay seen bars through the adapter (simulated broker)
  reconcile         reconcile a store against the engine
  resolve-exception close an exception with an explanation
  freeze-contract   write docs/candle12_v3_oos_contract.lock.json
  oos-evaluate      the contract's OOS read (refuses early / small samples)
  run               live loop; simulated broker unless --paper (testnet, guarded)

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
from app.research.candle12_v3_paper.adapter import PaperAdapter
from app.research.candle12_v3_paper.broker import SimulatedBroker
from app.research.candle12_v3_paper.reconcile import reconcile
from app.research.candle12_v3_paper.store import PaperStore

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
    store = PaperStore(root, mode=manifest["mode"])
    start = datetime.fromisoformat(manifest["history_start"])
    end = datetime.now(UTC) if manifest["mode"] == "PAPER" else datetime.fromisoformat(manifest["window_end"])
    db = _db()
    candles = db.load_candles(C.SYMBOL, C.TIMEFRAME, start, end)
    if manifest["mode"] != "PAPER":
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
    if path.exists() and not args.force:
        print(f"{path} exists; the contract is already frozen (use --force only for a new contract version)")
        return 2
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
                            diagnostic=args.diagnostic, read_log=root / "oos_reads.jsonl")
    except OOSRefused as exc:
        print(f"REFUSED: {exc.reason} {exc.detail}")
        return 2
    print(json.dumps(read, indent=1, default=str))
    return 0


def run_live(adapter: PaperAdapter, source: Any, *, funding_source: Any | None, poll_s: float,
             clock=lambda: datetime.now(UTC), max_loops: int | None = None) -> None:
    """Poll closed klines; open bar k when it starts; close it when it is final."""
    now = clock()
    history = source.history(C.OOS_START - adapter.interval * C.WARMUP_BARS, now)
    last = history[-1]["time"]
    oos_index = next((i for i, b in enumerate(history) if b["time"] >= C.OOS_START), None)
    if oos_index is None:
        oos_index = len(history) + int((C.OOS_START - (last + adapter.interval)) / adapter.interval)
    adapter.start(history, first_trade_index=oos_index)
    opened: int | None = None
    loops = 0
    while max_loops is None or loops < max_loops:
        loops += 1
        now = clock()
        for bar in source.history(adapter.closed[-1]["time"] + adapter.interval, now):
            k = len(adapter.closed)
            if opened != k:
                adapter.on_bar_open(k, bar["time"], bar["open"], now)
            if funding_source is not None:
                adapter.funding_rows = sorted(
                    ({**r, "time": v3._as_utc(r["time"])}
                     for r in funding_source.settlements(history[0]["time"], now)), key=lambda r: r["time"])
            adapter.on_bar_close(bar, now)
            opened = None
        forming = source.forming_bar(now)
        k = len(adapter.closed)
        if forming and forming["time"] == adapter.closed[-1]["time"] + adapter.interval and opened != k:
            adapter.on_bar_open(k, forming["time"], forming["open"], now, current_price=source.last_price())
            opened = k
        time.sleep(poll_s)


def cmd_run(args: argparse.Namespace) -> int:
    from app.research.candle12_v3_paper.feeds import BinanceFundingSource, BinanceKlineSource

    cfg = C.contract_config()
    step, _, min_notional, _ = cfg.exchange_filter(C.SYMBOL)
    if args.paper:
        from app.research.candle12_v3_paper.binance import PAPER_ENV, BinanceTestnetBroker, PaperGuardError

        if os.environ.get(PAPER_ENV) != "1":
            print(f"REFUSED: --paper needs {PAPER_ENV}=1")
            return 2
        try:
            broker: Any = BinanceTestnetBroker(symbol=C.SYMBOL, api_key=os.environ.get("BINANCE_TESTNET_API_KEY", ""),
                                               api_secret=os.environ.get("BINANCE_TESTNET_API_SECRET", ""))
        except PaperGuardError as exc:
            print(f"REFUSED: {exc}")
            return 2
        mode = "PAPER"
    else:
        broker = SimulatedBroker(slippage=cfg.entry_slippage, fee_rate=cfg.taker_fee, step=step, min_notional=min_notional)
        mode = "DRY_RUN"
    root = Path(args.store) if args.store else DATA_ROOT / mode.lower() / "live"
    store = PaperStore(root, mode=mode, manifest={"contract_id": C.CONTRACT_ID, "config_fingerprint": C.config_fingerprint(),
                                                  "code_fingerprint": C.code_fingerprint()["combined"]})
    adapter = PaperAdapter(broker=broker, store=store, cfg=cfg)
    run_live(adapter, BinanceKlineSource(C.SYMBOL, C.TIMEFRAME), funding_source=BinanceFundingSource(C.SYMBOL),
             poll_s=args.poll)
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
    f.add_argument("--force", action="store_true")
    o = sub.add_parser("oos-evaluate")
    o.add_argument("--store", required=True)
    o.add_argument("--diagnostic", action="store_true",
                   help="NON-ACCEPTANCE diagnostic read: allowed early, logged, never an OOS pass")
    run = sub.add_parser("run")
    run.add_argument("--paper", action="store_true", help="send orders to the futures TESTNET (needs env guard)")
    run.add_argument("--store")
    run.add_argument("--poll", type=float, default=15.0)
    args = ap.parse_args(argv)
    return {
        "smoke": cmd_smoke, "dry-run": cmd_dry_run, "reconcile": cmd_reconcile, "resolve-exception": cmd_resolve,
        "freeze-contract": cmd_freeze, "oos-evaluate": cmd_oos, "run": cmd_run,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
