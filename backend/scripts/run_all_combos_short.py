"""Short-window pass: every stored coin, every combination, long and short.

14 days of 1h (336 bars). V2 variants already trade both sides in one pass.
Does not place paper or live orders.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from app.config import get_settings
from app.research.backtest_cpu_worker import build_matrix_row_postprocess
from app.research.bos_combinations import COMBINATIONS
from app.research.combination_backtest import run_combination_backtest
from app.research.combo02_v2.research_variants import COMBO_V2_FAMILY
from app.research.config import ResearchConfig
from app.research.postgres_ohlcv import list_symbols_with_ohlcv
from app.research.service import (
    _load_htf_candles_for_combo,
    _load_research_candles,
    _market_cap,
    _signal_config,
)
from app.services.database import db_manager

TIMEFRAME = "1h"
LIMIT = 336
# Same evaluator as COMBO_02_V2_1_A. One pass is enough.
SKIP = frozenset({"COMBO_02_V2_1A"})
OUT = (
    Path(__file__).resolve().parents[2]
    / "research_sandbox"
    / "combo_matrix_short_20261010"
    / "summary.json"
)


def _cell(symbol: str, combination_id: str, book: dict, direction: str | None) -> dict:
    out = run_combination_backtest(
        symbol=symbol,
        timeframe=TIMEFRAME,
        candles=book["candles"],
        combination=combination_id,
        signal_config=_signal_config(),
        research_config=ResearchConfig(),
        market_cap=_market_cap(symbol),
        direction_filter=direction,
        candles_1h=book["c1h"],
        candles_4h=book["c4h"],
        candles_15m=book["c15"],
        candles_1d=book["c1d"],
    )
    label = "LONG_AND_SHORT" if direction is None else direction
    row = build_matrix_row_postprocess(
        {
            "backtest_out": out,
            "risk_usd": 20.0,
            "taker_fee": 0.0004,
            "maker_fee": 0.0002,
            "leverage": 2.0,
            "principal": 1000.0,
            "include_trades": False,
            "direction": label if direction is None else direction,
            "combination_id": combination_id,
            "combination_name": combination_id,
            "load_meta": book["meta"],
            "candles": book["candles"],
            "analytics_on": False,
        }
    )
    row["direction"] = label
    return {
        "combination_id": row.get("combination_id"),
        "symbol": row.get("symbol"),
        "direction": label,
        "status": row.get("status"),
        "sample_size": row.get("sample_size"),
        "average_R": row.get("average_R"),
        "average_R_net": row.get("average_R_net"),
        "expectancy_R": row.get("expectancy_R"),
        "profit_factor": row.get("profit_factor"),
        "max_drawdown_R": row.get("max_drawdown_R"),
        "pnl_usd_net": row.get("pnl_usd_net"),
        "bars_loaded": row.get("bars_loaded"),
        "period_start": row.get("period_start"),
        "period_end": row.get("period_end"),
    }


def _write(rows: list[dict], symbols: list[str], started: float, status: str) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "status": status,
                "symbols": symbols,
                "timeframe": TIMEFRAME,
                "limit": LIMIT,
                "directions": ["LONG", "SHORT"],
                "v2_direction": "LONG_AND_SHORT",
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "rows": rows,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


async def _load_symbol(symbol: str) -> dict | None:
    warmup = max(int(ResearchConfig().min_bars), 100)
    candles, _, meta = await _load_research_candles(
        symbol,
        TIMEFRAME,
        limit=LIMIT,
        warmup_bars=warmup,
        use_research_cache=False,
    )
    if len(candles) < 80:
        print(f"skip {symbol} only {len(candles)} 1h bars", flush=True)
        return None
    c1h, c4h = await _load_htf_candles_for_combo(
        symbol,
        require_htf=True,
        setup_timeframe=TIMEFRAME,
        limit=LIMIT,
        start_date=None,
        end_date=None,
        warmup_bars=warmup,
        use_research_cache=False,
    )
    c15, _, _ = await _load_research_candles(
        symbol,
        "15m",
        limit=max(LIMIT * 4, 500),
        warmup_bars=max(warmup, 200),
        use_research_cache=False,
    )
    c1d, _, _ = await _load_research_candles(
        symbol,
        "1d",
        limit=max(LIMIT // 4, 120),
        warmup_bars=30,
        use_research_cache=False,
    )
    print(
        f"loaded {symbol} 1h={len(candles)} 4h={len(c4h or [])} 15m={len(c15)} 1d={len(c1d)}",
        flush=True,
    )
    return {"candles": candles, "meta": meta, "c1h": c1h, "c4h": c4h, "c15": c15, "c1d": c1d}


def _resume_rows() -> list[dict]:
    if not OUT.exists():
        return []
    payload = json.loads(OUT.read_text(encoding="utf-8"))
    return [
        row
        for row in (payload.get("rows") or [])
        if row.get("combination_id") and row.get("symbol") and row.get("direction")
    ]


def _passes(combination_id: str) -> list[str | None]:
    if combination_id in COMBO_V2_FAMILY:
        return [None]
    return ["LONG", "SHORT"]


async def main() -> None:
    started = time.perf_counter()
    await db_manager.connect(get_settings(), ensure_schema=False)
    rows = _resume_rows()
    done = {
        (str(row["combination_id"]), str(row["symbol"]), str(row.get("direction")))
        for row in rows
    }
    symbols: list[str] = []
    try:
        symbols = await list_symbols_with_ohlcv(TIMEFRAME)
        ids = [cid for cid in COMBINATIONS if cid not in SKIP]
        print(
            f"coins={len(symbols)} strategies={len(ids)} bars={LIMIT} resume={len(done)}",
            flush=True,
        )
        _write(rows, symbols, started, "RUNNING")
        for symbol in symbols:
            needed = [
                (combination_id, "LONG_AND_SHORT" if direction is None else direction)
                for combination_id in ids
                for direction in _passes(combination_id)
            ]
            if all((cid, symbol, label) in done for cid, label in needed):
                print(f"skip symbol {symbol}", flush=True)
                continue
            book = await _load_symbol(symbol)
            if book is None:
                continue
            for combination_id in ids:
                for direction in _passes(combination_id):
                    label = "LONG_AND_SHORT" if direction is None else direction
                    if (combination_id, symbol, label) in done:
                        continue
                    print(f"start {combination_id} {symbol} {label}", flush=True)
                    t0 = time.perf_counter()
                    row = await asyncio.to_thread(_cell, symbol, combination_id, book, direction)
                    row["cell_seconds"] = round(time.perf_counter() - t0, 3)
                    rows.append(row)
                    _write(rows, symbols, started, "RUNNING")
                    print(
                        f"done {combination_id} {symbol} {label} n={row.get('sample_size')} "
                        f"seconds={row['cell_seconds']}",
                        flush=True,
                    )
    finally:
        await db_manager.close()
    _write(rows, symbols, started, "OK")
    print(f"wrote {OUT} rows={len(rows)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
