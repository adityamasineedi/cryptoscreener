"""Owner entry point for the E34 LIQ-FLUSH forward paper runner.

No orders, no API keys, no alerts. The default store is the live path and
is created only when the owner starts this process.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.research.liq_flush import contract
from app.research.liq_flush.book import Book
from app.research.liq_flush.feeds import fetch_closed_bars, fetch_funding
from app.research.liq_flush.ledger import (
    PRODUCTION_STORE,
    read_jsonl,
    resolve_store,
    status_from_events,
)
from app.research.liq_flush.recorder import Recorder, load_hourly
from app.research.liq_flush.stream import stream_force_orders
from app.research.liq_flush.universe import UNIVERSE

UTC = timezone.utc


def _store(args: argparse.Namespace) -> Path:
    production = bool(args.production or args.store is None)
    return resolve_store(args.store or PRODUCTION_STORE, production=production)


def _halt_if_drift(store: Path, production: bool) -> bool:
    if not contract.drift():
        return False
    Book(store, production=production).halt("CODE_DRIFT", datetime.now(UTC))
    return True


async def _run_loop(recorder: Recorder, book: Book) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(stream_force_orders(recorder, stop))
    try:
        while not stop.is_set():
            now = datetime.now(UTC)
            if contract.drift():
                book.halt("CODE_DRIFT", now)
                break
            recorder.seal_due(now)
            book.ensure_start(now)
            if book.halted():
                break
            origin = book.origin()
            bars = {symbol: fetch_closed_bars(symbol, origin, now) for symbol in UNIVERSE}
            funding = {symbol: fetch_funding(symbol, origin, now) for symbol in UNIVERSE}
            book.advance(bars, funding, load_hourly(recorder.hourly_path), now)
            book.heartbeat(now)
            if book.halted():
                break
            await asyncio.sleep(30)
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def cmd_run(store: Path, production: bool) -> int:
    if _halt_if_drift(store, production):
        return 2
    now = datetime.now(UTC)
    recorder = Recorder.open(store, production=production)
    recorder.begin_session(now)
    book = Book(store, production=production)
    asyncio.run(_run_loop(recorder, book))
    return 0 if not book.halted() else 2


def cmd_step(store: Path, production: bool) -> int:
    """One book pass over hours the recorder has already sealed. Does not open the socket."""
    if _halt_if_drift(store, production):
        return 2
    now = datetime.now(UTC)
    book = Book(store, production=production)
    book.ensure_start(now)
    if book.halted():
        return 2
    origin = book.origin()
    bars = {symbol: fetch_closed_bars(symbol, origin, now) for symbol in UNIVERSE}
    funding = {symbol: fetch_funding(symbol, origin, now) for symbol in UNIVERSE}
    book.advance(bars, funding, load_hourly(store / "hourly.jsonl"), now)
    return 0 if not book.halted() else 2


def cmd_status(store: Path, production: bool) -> int:
    del production
    path = store / "ledger.jsonl"
    events = read_jsonl(path) if path.exists() else []
    text = json.dumps(status_from_events(events, datetime.now(UTC)), sort_keys=True)
    sys.stdout.write(text + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="liq_flush")
    parser.add_argument("--store", type=Path, default=None)
    parser.add_argument("--production", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="record liquidations and mark the virtual book")
    sub.add_parser("step", help="mark the book once from the sealed hourly file")
    sub.add_parser("status", help="counts; net and PF only after the 30-trade read")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    production = bool(args.production or args.store is None)
    store = _store(args)
    if args.cmd == "run":
        return cmd_run(store, production)
    if args.cmd == "step":
        return cmd_step(store, production)
    if args.cmd == "status":
        return cmd_status(store, production)
    parser.error(args.cmd)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
