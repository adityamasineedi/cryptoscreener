"""Admin-regime backtest of the passed book on the 27 cap coins.

The BTC daily trend picks the sides bar by bar, one shared position per
symbol, one equity curve for the run. Entry and exit rules are unchanged.
Reads stored candles only. Does not place paper or live orders and writes no
database table. Output is one JSON file; a finished symbol is not run again.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from app.research import passed_regime_backtest as regime
from app.research.btc_daily_regime import MIN_DAILY_BARS
from app.research.passed_cap_book import PASSED_SIDES, cap_symbols

_SPEC = importlib.util.spec_from_file_location(
    "cap_mtf_risk",
    Path(__file__).with_name("run_cap_mtf_risk.py"),
)
cap = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(cap)

# About six months of 1h bars, as in run_passed_cap_book.py.
BARS_1H = 4320
# Extra daily bars so the first 1h bar already has a settled trend.
DAILY_LEAD = MIN_DAILY_BARS + 140


def _default_out() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return (
        Path(__file__).resolve().parents[2]
        / "research_sandbox"
        / f"passed_regime_book_{stamp}"
        / "summary.json"
    )


async def _btc_daily(bars_1h: int) -> list[dict]:
    candles, _, _ = await cap._load_research_candles(
        "BTCUSDT",
        "1d",
        limit=bars_1h // 24 + DAILY_LEAD,
        warmup_bars=30,
        use_research_cache=False,
    )
    return list(candles)


def _resume(out: Path, bars_1h: int) -> list[dict]:
    if not out.exists():
        return []
    payload = json.loads(out.read_text(encoding="utf-8"))
    if payload.get("bars_1h") != bars_1h:
        return []
    return [row for row in (payload.get("symbol_results") or []) if row.get("status") == "OK"]


def _write(out: Path, payload: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _payload(results: list[dict], clock, *, status: str, bars_1h: int, started: float) -> dict:
    book = regime.build_result(results, clock, principal=cap.PRINCIPAL)
    book["status"] = status if book["status"] == "OK" else book["status"]
    book["bars_1h"] = bars_1h
    book["passed_sides"] = [list(side) for side in PASSED_SIDES]
    book["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    # Kept so a later run can skip finished symbols and shards can be merged.
    book["symbol_results"] = results
    return book


async def main(symbols: list[str], out: Path, bars_1h: int) -> None:
    started = time.perf_counter()
    cap.BARS = {"1h": bars_1h, "4h": 240}
    await cap.db_manager.connect(cap.get_settings(), ensure_schema=False)
    results = [r for r in _resume(out, bars_1h) if r.get("symbol") in symbols]
    done = {r["symbol"] for r in results}
    try:
        btc_daily = await _btc_daily(bars_1h)
        clock = regime.RegimeClock(btc_daily, cap.SignalConfig())
        print(
            f"coins={len(symbols)} sides={len(PASSED_SIDES)} bars={bars_1h} "
            f"btc_daily={len(btc_daily)} resume={len(done)}",
            flush=True,
        )
        for symbol in symbols:
            if symbol in done:
                print(f"skip {symbol}", flush=True)
                continue
            book = await cap._load(symbol, "1h")
            if book is None:
                continue
            t0 = time.perf_counter()
            row = await asyncio.to_thread(
                regime.run_symbol,
                symbol,
                book,
                clock,
                signal_config=cap._signal_config(),
                research_config=cap.ResearchConfig(),
                market_cap=cap._market_cap(symbol),
            )
            row["symbol_seconds"] = round(time.perf_counter() - t0, 3)
            results.append(row)
            _write(out, _payload(results, clock, status="RUNNING", bars_1h=bars_1h, started=started))
            print(
                f"done {symbol} signals={len(row['signals'])} trades={len(row['trades'])} "
                f"walks={row['walks']} neutral_short_history_bars={row['short_history_bars']} "
                f"errors={len(row['errors'])} seconds={row['symbol_seconds']}",
                flush=True,
            )
    finally:
        await cap.db_manager.close()
    payload = _payload(results, clock, status="OK", bars_1h=bars_1h, started=started)
    _write(out, payload)
    summary = payload["summary"]
    print(
        f"wrote {out} trades={summary['trades']} net={round(summary['pnl_usd_net'], 2)} "
        f"max_dd={round(summary['max_drawdown_usd'], 2)} "
        f"short_history_symbol_bars={summary['by_trend']['NEUTRAL']['short_history_symbol_bars']}",
        flush=True,
    )


async def merge(paths: list[Path], out: Path) -> None:
    """Join shard files (run with --symbols/--out) into one book and one curve."""
    results: list[dict] = []
    bars_1h = BARS_1H
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        bars_1h = int(payload.get("bars_1h") or bars_1h)
        results.extend(payload.get("symbol_results") or [])
    rank = {symbol: n for n, symbol in enumerate(cap_symbols())}
    results.sort(key=lambda row: rank.get(row.get("symbol"), len(rank)))
    await cap.db_manager.connect(cap.get_settings(), ensure_schema=False)
    try:
        clock = regime.RegimeClock(await _btc_daily(bars_1h), cap.SignalConfig())
    finally:
        await cap.db_manager.close()
    _write(out, _payload(results, clock, status="OK", bars_1h=bars_1h, started=time.perf_counter()))
    print(f"wrote {out} symbols={len(results)}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="", help="Comma-separated subset of the large/mid book")
    parser.add_argument("--out", default="", help="Summary JSON path")
    parser.add_argument("--bars", type=int, default=BARS_1H, help="1h bars per symbol")
    parser.add_argument("--merge", default="", help="Comma-separated shard files to join into --out")
    args = parser.parse_args()
    target = Path(args.out) if args.out else _default_out()
    if args.merge:
        asyncio.run(merge([Path(p) for p in args.merge.split(",") if p.strip()], target))
    else:
        chosen = cap._selected_symbols(args.symbols) or list(cap_symbols())
        asyncio.run(main(chosen, target, args.bars))
