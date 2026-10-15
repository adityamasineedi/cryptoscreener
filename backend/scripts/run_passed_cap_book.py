"""Retest the strategy sides that passed the 10 Oct cap book.

Same 27 coins and the same dollar-risk rule. Entry and exit rules are unchanged.
Only the passing sides are run, on 1h, for 4,320 bars (about six months) so
results can be split by calendar month. Does not place paper or live orders.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import time
from pathlib import Path

from app.research.passed_cap_book import PASSED_SIDES

_SPEC = importlib.util.spec_from_file_location(
    "cap_mtf_risk",
    Path(__file__).with_name("run_cap_mtf_risk.py"),
)
cap = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(cap)

# About six months of 1h bars. The original pass used 480 bars.
cap.BARS = {"1h": 4320, "4h": 240}
cap.OUT = (
    Path(__file__).resolve().parents[2]
    / "research_sandbox"
    / "passed_cap_book_20261010"
    / "summary.json"
)

# Sides with combined net above $0 and at least 5 trades on the short book.
PASSED = PASSED_SIDES


def _months(trades: list[dict]) -> dict[str, dict[str, float | int]]:
    buckets: dict[str, dict[str, float | int]] = {}
    for trade in trades:
        if trade.get("outcome") == "OPEN":
            continue
        stamp = str(trade.get("exit_time") or trade.get("signal_time") or "")
        key = stamp[:7] if len(stamp) >= 7 else "undated"
        bucket = buckets.setdefault(key, {"trades": 0, "pnl_usd_net": 0.0})
        bucket["trades"] = int(bucket["trades"]) + 1
        bucket["pnl_usd_net"] = float(bucket["pnl_usd_net"]) + float(trade.get("net_pnl_usd") or 0.0)
    for bucket in buckets.values():
        bucket["pnl_usd_net"] = round(float(bucket["pnl_usd_net"]), 2)
    return dict(sorted(buckets.items()))


def _cell(symbol: str, combination_id: str, book: dict, direction: str, market: str) -> dict:
    stake = cap.risk_usd(symbol, direction, market)
    out = cap.run_combination_backtest(
        symbol=symbol,
        timeframe="1h",
        candles=book["candles"],
        combination=combination_id,
        signal_config=cap._signal_config(),
        research_config=cap.ResearchConfig(),
        market_cap=cap._market_cap(symbol),
        direction_filter=direction,
        candles_1h=book["c1h"],
        candles_4h=book["c4h"],
        candles_15m=book["c15"],
        candles_1d=book["c1d"],
    )
    enriched = cap.build_matrix_row_postprocess(
        {
            "backtest_out": out,
            "risk_usd": stake,
            "taker_fee": 0.0004,
            "maker_fee": 0.0002,
            "leverage": 2.0,
            "principal": cap.PRINCIPAL,
            "include_trades": True,
            "direction": direction,
            "combination_id": combination_id,
            "combination_name": combination_id,
            "load_meta": book["meta"],
            "candles": book["candles"],
            "analytics_on": False,
        }
    )
    return {
        "combination_id": combination_id,
        "symbol": symbol,
        "timeframe": "1h",
        "direction": direction,
        "cap_group": cap.cap_group(symbol),
        "market_direction": market,
        "risk_usd": stake,
        "principal_usd": cap.PRINCIPAL,
        "status": enriched.get("status"),
        "sample_size": enriched.get("sample_size"),
        "average_R": enriched.get("average_R"),
        "average_R_net": enriched.get("average_R_net"),
        "pnl_usd_net": enriched.get("pnl_usd_net"),
        "max_drawdown_R": enriched.get("max_drawdown_R"),
        "bars_loaded": enriched.get("bars_loaded"),
        "period_start": enriched.get("period_start"),
        "period_end": enriched.get("period_end"),
        "months": _months(list(enriched.get("trades") or [])),
    }


async def main() -> None:
    started = time.perf_counter()
    await cap.db_manager.connect(cap.get_settings(), ensure_schema=False)
    rows = cap._resume_rows()
    done = {(r["combination_id"], r["symbol"], r["timeframe"], r["direction"]) for r in rows}
    symbols = list(cap.LARGE) + list(cap.MID)
    try:
        market = await cap._btc_direction()
        print(
            f"market={market} coins={len(symbols)} sides={len(PASSED)} "
            f"bars=4320 resume={len(done)}",
            flush=True,
        )
        for symbol in symbols:
            pending = [
                side for side in PASSED
                if (side[0], symbol, side[1], side[2]) not in done
            ]
            if not pending:
                print(f"skip {symbol}", flush=True)
                continue
            book = await cap._load(symbol, "1h")
            if book is None:
                continue
            for combination_id, timeframe, direction in pending:
                key = (combination_id, symbol, timeframe, direction)
                print(
                    f"start {combination_id} {symbol} {timeframe} {direction} "
                    f"risk={cap.risk_usd(symbol, direction, market)}",
                    flush=True,
                )
                t0 = time.perf_counter()
                row = await asyncio.to_thread(
                    _cell, symbol, combination_id, book, direction, market
                )
                row["cell_seconds"] = round(time.perf_counter() - t0, 3)
                rows.append(row)
                done.add(key)
                cap._write(
                    {
                        "status": "RUNNING",
                        "market_direction": market,
                        "principal_usd": cap.PRINCIPAL,
                        "bars_1h": 4320,
                        "passed_sides": [list(side) for side in PASSED],
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                        "rows": rows,
                    }
                )
                print(
                    f"done {combination_id} {symbol} n={row.get('sample_size')} "
                    f"net={row.get('pnl_usd_net')} seconds={row['cell_seconds']}",
                    flush=True,
                )
    finally:
        await cap.db_manager.close()
    payload = json.loads(cap.OUT.read_text(encoding="utf-8")) if cap.OUT.exists() else {"rows": rows}
    payload["status"] = "OK"
    payload["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    cap._write(payload)
    print(f"wrote {cap.OUT} rows={len(rows)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
