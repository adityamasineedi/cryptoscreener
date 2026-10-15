"""Large- and mid-cap book, all strategies, 1h and 4h.

Research sizing only. A $1,000 account risks $20 on a large cap and $10 on a
mid cap. That stake is cut in half when the trade is against the BTC daily
trend. Entry rules are unchanged. Does not place paper or live orders.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from app.config import get_settings
from app.research.backtest_cpu_worker import build_matrix_row_postprocess
from app.research.bos_combinations import COMBINATIONS
from app.research.combination_backtest import run_combination_backtest
from app.research.config import ResearchConfig
from app.research.passed_cap_book import LARGE, MID, PRINCIPAL_USD
from app.research.service import (
    _load_htf_candles_for_combo,
    _load_research_candles,
    _market_cap,
    _signal_config,
)
from app.services.database import db_manager
from app.signals.config import SignalConfig
from app.signals.swing_detector import detect_swings
from app.signals.trend_engine import infer_trend

PRINCIPAL = PRINCIPAL_USD
BARS = {"1h": 480, "4h": 240}
SKIP = frozenset({"COMBO_02_V2_1A"})
OUT = (
    Path(__file__).resolve().parents[2]
    / "research_sandbox"
    / "cap_mtf_risk_20261010"
    / "summary.json"
)


def cap_group(symbol: str) -> str:
    return "large" if symbol in LARGE else "mid"


def risk_usd(symbol: str, direction: str, market: str) -> float:
    """Dollar risk for one trade on a $1,000 account."""
    stake = 20.0 if cap_group(symbol) == "large" else 10.0
    if market == "BULLISH" and direction == "SHORT":
        return stake * 0.5
    if market == "BEARISH" and direction == "LONG":
        return stake * 0.5
    return stake


def _resume_rows() -> list[dict]:
    if not OUT.exists():
        return []
    payload = json.loads(OUT.read_text(encoding="utf-8"))
    return [
        row
        for row in (payload.get("rows") or [])
        if row.get("combination_id") and row.get("symbol") and row.get("timeframe")
    ]


def _write(payload: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


async def _btc_direction() -> str:
    candles, _, _ = await _load_research_candles(
        "BTCUSDT", "1d", limit=180, warmup_bars=30, use_research_cache=False
    )
    if len(candles) < 40:
        return "NEUTRAL"
    cfg = SignalConfig()
    swing_cfg = cfg.swing_for("1d")
    swings = detect_swings(
        candles,
        left=swing_cfg.swing_left_bars,
        right=swing_cfg.swing_right_bars,
        symbol="BTCUSDT",
        timeframe="1d",
        as_of_index=len(candles) - 1,
    )
    trend = str(infer_trend(list(swings)).get("trend") or "")
    if trend in {"BULLISH", "BEARISH"}:
        return trend
    return "NEUTRAL"


async def _load(symbol: str, timeframe: str) -> dict | None:
    limit = BARS[timeframe]
    warmup = max(int(ResearchConfig().min_bars), 80)
    candles, _, meta = await _load_research_candles(
        symbol, timeframe, limit=limit, warmup_bars=warmup, use_research_cache=False
    )
    if len(candles) < 80:
        print(f"skip {symbol} {timeframe} bars={len(candles)}", flush=True)
        return None
    c1h, c4h = await _load_htf_candles_for_combo(
        symbol,
        require_htf=True,
        setup_timeframe=timeframe,
        limit=limit,
        start_date=None,
        end_date=None,
        warmup_bars=warmup,
        use_research_cache=False,
    )
    c15, _, _ = await _load_research_candles(
        symbol,
        "15m",
        limit=max(limit * (16 if timeframe == "4h" else 4), 400),
        warmup_bars=80,
        use_research_cache=False,
    )
    c1d, _, _ = await _load_research_candles(
        symbol, "1d", limit=max(limit // 6, 120), warmup_bars=20, use_research_cache=False
    )
    print(
        f"loaded {symbol} {timeframe}={len(candles)} 4h={len(c4h or [])} 15m={len(c15)}",
        flush=True,
    )
    return {
        "candles": candles,
        "meta": meta,
        "c1h": c1h,
        "c4h": c4h,
        "c15": c15,
        "c1d": c1d,
    }


def _cell(
    symbol: str,
    timeframe: str,
    combination_id: str,
    book: dict,
    direction: str,
    market: str,
) -> dict:
    stake = risk_usd(symbol, direction, market)
    out = run_combination_backtest(
        symbol=symbol,
        timeframe=timeframe,
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
    row = build_matrix_row_postprocess(
        {
            "backtest_out": out,
            "risk_usd": stake,
            "taker_fee": 0.0004,
            "maker_fee": 0.0002,
            "leverage": 2.0,
            "principal": PRINCIPAL,
            "include_trades": False,
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
        "timeframe": timeframe,
        "direction": direction,
        "cap_group": cap_group(symbol),
        "market_direction": market,
        "risk_usd": stake,
        "principal_usd": PRINCIPAL,
        "status": row.get("status"),
        "sample_size": row.get("sample_size"),
        "average_R": row.get("average_R"),
        "average_R_net": row.get("average_R_net"),
        "pnl_usd_net": row.get("pnl_usd_net"),
        "max_drawdown_R": row.get("max_drawdown_R"),
        "bars_loaded": row.get("bars_loaded"),
    }


async def main(symbols: list[str] | None = None) -> None:
    started = time.perf_counter()
    await db_manager.connect(get_settings(), ensure_schema=False)
    rows = _resume_rows()
    done = {
        (r["combination_id"], r["symbol"], r["timeframe"], r["direction"]) for r in rows
    }
    symbols = list(symbols) if symbols else list(LARGE) + list(MID)
    ids = [cid for cid in COMBINATIONS if cid not in SKIP]
    try:
        market = await _btc_direction()
        print(
            f"market={market} coins={len(symbols)} strategies={len(ids)} resume={len(done)}",
            flush=True,
        )
        for timeframe in ("1h", "4h"):
            for symbol in symbols:
                if all(
                    (cid, symbol, timeframe, side) in done
                    for cid in ids
                    for side in ("LONG", "SHORT")
                ):
                    print(f"skip {symbol} {timeframe}", flush=True)
                    continue
                book = await _load(symbol, timeframe)
                if book is None:
                    continue
                for combination_id in ids:
                    for direction in ("LONG", "SHORT"):
                        key = (combination_id, symbol, timeframe, direction)
                        if key in done:
                            continue
                        print(
                            f"start {combination_id} {symbol} {timeframe} {direction} "
                            f"risk={risk_usd(symbol, direction, market)}",
                            flush=True,
                        )
                        t0 = time.perf_counter()
                        row = await asyncio.to_thread(
                            _cell,
                            symbol,
                            timeframe,
                            combination_id,
                            book,
                            direction,
                            market,
                        )
                        row["cell_seconds"] = round(time.perf_counter() - t0, 3)
                        rows.append(row)
                        done.add(key)
                        _write(
                            {
                                "status": "RUNNING",
                                "market_direction": market,
                                "principal_usd": PRINCIPAL,
                                "risk_rule": (
                                    "large $20, mid $10, half when the trade "
                                    "fights the BTC daily trend"
                                ),
                                "symbols": symbols,
                                "timeframes": ["1h", "4h"],
                                "elapsed_seconds": round(time.perf_counter() - started, 3),
                                "rows": rows,
                            }
                        )
                        print(
                            f"done {combination_id} {symbol} {timeframe} {direction} "
                            f"n={row.get('sample_size')} seconds={row['cell_seconds']}",
                            flush=True,
                        )
    finally:
        await db_manager.close()
    if OUT.exists():
        payload = json.loads(OUT.read_text(encoding="utf-8"))
    else:
        payload = {"rows": rows}
    payload["status"] = "OK"
    payload["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    _write(payload)
    print(f"wrote {OUT} rows={len(rows)}", flush=True)


def _selected_symbols(raw: str) -> list[str] | None:
    if not raw.strip():
        return None
    allowed = {name: name for name in (*LARGE, *MID)}
    chosen = []
    for part in raw.split(","):
        symbol = part.strip().upper()
        if symbol not in allowed:
            raise SystemExit(f"not in the large/mid book: {symbol}")
        chosen.append(symbol)
    return chosen


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="", help="Comma-separated subset of the large/mid book")
    parser.add_argument("--out", default="", help="Summary JSON path for this shard")
    args = parser.parse_args()
    if args.out:
        OUT = Path(args.out)
    asyncio.run(main(_selected_symbols(args.symbols)))
