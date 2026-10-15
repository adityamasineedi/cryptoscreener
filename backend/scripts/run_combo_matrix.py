"""Backtest the supply/demand variants plus COMBO_02 and COMBO_02 V2.1-A.

Same book as the liquidity run: BTC, ETH, SOL, 1h, 5,760 bars, long only.
V2.1-A keeps its native long and short pass. Candles load once per symbol.
Does not place paper or live orders.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from app.config import get_settings
from app.research.backtest_cpu_worker import build_matrix_row_postprocess
from app.research.combination_backtest import run_combination_backtest
from app.research.combo02_v2.research_variants import COMBO_V2_FAMILY
from app.research.config import ResearchConfig
from app.research.service import (
    _load_htf_candles_for_combo,
    _load_research_candles,
    _market_cap,
    _signal_config,
)
from app.services.database import db_manager

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAME = "1h"
LIMIT = 5760
COMBINATIONS = (
    "COMBO_SD_A",
    "COMBO_SD_B",
    "COMBO_SD_C",
    "COMBO_SD_C12",
    "COMBO_SD_C20",
    "COMBO_SD_D",
    "COMBO_SD_E",
    "COMBO_SD_F",
    "COMBO_SD_C_FRESH",
    "COMBO_02",
    "COMBO_02_V2_1_A",
)
OUT = (
    Path(__file__).resolve().parents[2]
    / "research_sandbox"
    / "combo_matrix_20261010"
    / "summary.json"
)


def _progress(event: dict) -> None:
    phase = str(event.get("phase") or "")
    if phase == "STRUCTURE_SCAN_HEARTBEAT":
        elapsed = float(event.get("elapsed_seconds") or 0)
        if int(elapsed) % 120 != 0:
            return
        if getattr(_progress, "last", None) == int(elapsed):
            return
        _progress.last = int(elapsed)  # type: ignore[attr-defined]
    print(
        f"{phase} {event.get('symbol') or ''} {event.get('timeframe') or ''} "
        f"bars={event.get('bars_processed')}/{event.get('total_bars')} "
        f"trades={event.get('trades')} elapsed={event.get('elapsed_seconds')}",
        flush=True,
    )


def _slim(row: dict) -> dict:
    review = row.get("sd_review") or {}
    split = review.get("chronological_split") or {}
    return {
        "combination_id": row.get("combination_id"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "direction": row.get("direction"),
        "status": row.get("status"),
        "sample_size": row.get("sample_size"),
        "average_R": row.get("average_R"),
        "average_R_net": row.get("average_R_net"),
        "expectancy_R": row.get("expectancy_R"),
        "profit_factor": row.get("profit_factor"),
        "max_drawdown_R": row.get("max_drawdown_R"),
        "tp1_hit_rate": row.get("tp1_hit_rate"),
        "sl_rate": row.get("sl_rate"),
        "pnl_usd_net": row.get("pnl_usd_net"),
        "fees_usd": row.get("fees_usd"),
        "bars_loaded": row.get("bars_loaded"),
        "period_start": row.get("period_start"),
        "period_end": row.get("period_end"),
        "sample_warning": review.get("sample_warning"),
        "holdout_n": split.get("holdout_n"),
        "holdout_avg_r_net": split.get("holdout_avg_r_net"),
        "development_n": split.get("development_n"),
        "development_avg_r_net": split.get("development_avg_r_net"),
        "regime_counts": review.get("regime_counts"),
    }


def _write(rows: list[dict], started: float) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "RUNNING",
        "symbols": list(SYMBOLS),
        "timeframe": TIMEFRAME,
        "limit": LIMIT,
        "direction": "LONG",
        "v2_direction": "LONG_AND_SHORT",
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "rows": rows,
    }
    OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


async def _load_symbol(symbol: str) -> dict:
    warmup = max(int(ResearchConfig().min_bars), 100)
    candles, _, meta = await _load_research_candles(
        symbol,
        TIMEFRAME,
        limit=LIMIT,
        warmup_bars=warmup,
        use_research_cache=False,
    )
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
    c5, _, _ = await _load_research_candles(
        symbol,
        "5m",
        limit=max(LIMIT * 12, 500),
        warmup_bars=max(warmup, 200),
        use_research_cache=False,
    )
    c1d, _, _ = await _load_research_candles(
        symbol,
        "1d",
        limit=max(LIMIT // 4, 200),
        warmup_bars=max(warmup // 4, 40),
        use_research_cache=False,
    )
    print(
        f"loaded {symbol} 1h={len(candles)} 4h={len(c4h or [])} "
        f"15m={len(c15)} 5m={len(c5)} 1d={len(c1d)}",
        flush=True,
    )
    return {
        "candles": candles,
        "meta": meta,
        "c1h": c1h,
        "c4h": c4h,
        "c15": c15,
        "c5": c5,
        "c1d": c1d,
    }


def _run_cell(symbol: str, combination_id: str, book: dict) -> dict:
    direction = None if combination_id in COMBO_V2_FAMILY else "LONG"
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
        candles_5m=book["c5"],
        candles_1d=book["c1d"],
        progress_callback=_progress,
    )
    row = build_matrix_row_postprocess(
        {
            "backtest_out": out,
            "risk_usd": 20.0,
            "taker_fee": 0.0004,
            "maker_fee": 0.0002,
            "leverage": 2.0,
            "principal": 1000.0,
            "include_trades": False,
            "direction": direction or "LONG",
            "combination_id": combination_id,
            "combination_name": combination_id,
            "load_meta": book["meta"],
            "candles": book["candles"],
            "analytics_on": False,
        }
    )
    return _slim(row)


def _resume_rows() -> list[dict]:
    if not OUT.exists():
        return []
    payload = json.loads(OUT.read_text(encoding="utf-8"))
    return [
        row
        for row in (payload.get("rows") or [])
        if row.get("status") == "OK" and row.get("combination_id") and row.get("symbol")
    ]


async def main() -> None:
    started = time.perf_counter()
    settings = get_settings()
    await db_manager.connect(settings, ensure_schema=False)
    rows = _resume_rows()
    done = {(str(row["combination_id"]), str(row["symbol"])) for row in rows}
    if done:
        print(f"resume {len(done)} finished cells", flush=True)
    try:
        for symbol in SYMBOLS:
            if all((combination_id, symbol) in done for combination_id in COMBINATIONS):
                print(f"skip symbol {symbol}", flush=True)
                continue
            book = await _load_symbol(symbol)
            for combination_id in COMBINATIONS:
                if (combination_id, symbol) in done:
                    print(f"skip {combination_id} {symbol}", flush=True)
                    continue
                print(f"start {combination_id} {symbol}", flush=True)
                cell_started = time.perf_counter()
                row = await asyncio.to_thread(_run_cell, symbol, combination_id, book)
                row["cell_seconds"] = round(time.perf_counter() - cell_started, 3)
                rows.append(row)
                _write(rows, started)
                print(
                    f"done {combination_id} {symbol} n={row.get('sample_size')} "
                    f"avgR={row.get('average_R')} netR={row.get('average_R_net')} "
                    f"seconds={row['cell_seconds']}",
                    flush=True,
                )
    finally:
        await db_manager.close()
    payload = json.loads(OUT.read_text(encoding="utf-8"))
    payload["status"] = "OK"
    payload["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
