#!/usr/bin/env python3
"""Strategy ranking, GRID family: run every grid variant the repo defines.

Read-only. Calls the locked engines, never edits them:
  - app.grid_trading (long futures grid book; range from app.grid_trading.scanner)
  - app.research.grid_range_research (regime-gated two-sided grid, 1h gate / 15m fills)

Usage (from backend/):
  .venv/Scripts/python.exe -B scripts/strategy_rank/grid/run_grid_rank.py strategies
  .venv/Scripts/python.exe -B scripts/strategy_rank/grid/run_grid_rank.py coverage
  .venv/Scripts/python.exe -B scripts/strategy_rank/grid/run_grid_rank.py run --workers 3
  .venv/Scripts/python.exe -B scripts/strategy_rank/grid/run_grid_rank.py collect
  .venv/Scripts/python.exe -B scripts/strategy_rank/grid/run_grid_rank.py sanity

Cells are written atomically to cells/<strategy>__<symbol>__<tf>.json and skipped
when present, so `run` is resumable.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
REPO = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

CELLS = HERE / "cells"
TRADES = HERE / "trades"

SYMBOLS = (
    "BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT DOGEUSDT ADAUSDT TRXUSDT AVAXUSDT LINKUSDT".split()
)
WINDOW_START = datetime(2026, 2, 14, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 10, 12, tzinfo=timezone.utc)  # exclusive
TF_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
CAPITAL = 1000.0

# ---- grid trading book (app.grid_trading) ---------------------------------
GTB_ENGINE = "grid_trading_book"
GTB_TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]
SCAN_LIMIT = 400  # ScanRequest.limit default and the UI "Bars" default
SCAN_GRID_COUNT = 8  # UI always scans with locked.grid_count
# frontend/src/gridTrading/GridTradingPanel.tsx `locked`, except the two fee
# fields, which the task fixes at maker 0.02% / taker 0.04%.
GTB_BASE = {
    "capital": CAPITAL,
    "max_capital": 100000.0,
    "fee_bps": 2.0,  # maker; UI/engine default 4.0
    "taker_fee_bps": 4.0,  # UI omits it (falls back to fee_bps = 4.0)
    "slippage_bps": 1.0,
    "max_drawdown": 0.35,
    "max_open_orders": 20,
    "leverage": 1.0,  # engine rejects leverage > 1
    "paused": False,
    "funding_mode": "charge",
    "intrabar_path": "worst",
}
GTB_VARIANTS = {
    # id: (grid_type, grid_count, origin)
    "GTB-GEO-8": ("geometric", 8, "UI locked preset (GridTradingPanel.tsx)"),
    "GTB-GEO-4": ("geometric", 4, "UI grid type x GridRunRequest.grid_counts default [4, 8, 12]"),
    "GTB-GEO-12": ("geometric", 12, "UI grid type x GridRunRequest.grid_counts default [4, 8, 12]"),
    "GTB-ARI-8": ("arithmetic", 8, "GridConfig.grid_type default x UI grid count"),
    "GTB-ARI-4": ("arithmetic", 4, "GridConfig.grid_type default x grid_counts default"),
    "GTB-ARI-12": ("arithmetic", 12, "GridConfig.grid_type default x grid_counts default"),
}
GTB_INSAMPLE = "GTB-GEO-8-UI-INSAMPLE-RANGE"
GTB_INSAMPLE_REASON = (
    "NOT_RUN: the UI flow scans the last N<=2000 bars (default 400) and backtests the same bars, "
    "so the range is chosen from the candles it is tested on (look-ahead). It cannot be applied "
    "to the fixed 2026-02-14..2026-10-12 window without that look-ahead, and ScanRequest.limit "
    "caps the scan at 2000 bars. The causal equivalent (scan the 400 bars before the window) is "
    "what the GTB-* rows run."
)

# ---- grid range research (app.research.grid_range_research) ---------------
GRR_ENGINE = "grid_range_research"
GRR_TIMEFRAME = "15m"  # fills on 15m; regime gate, bounds and kills on 1h
GRR_VARIANTS = ["GRID-A", "GRID-B", "GRID-C", "GRID-A-LVC"]
GRR_WARMUP_1H = 1000  # bars before the window, only so EMA200/ATR percentile are formed

ROW_KEYS = [
    "family", "engine", "strategy_id", "direction", "symbol", "timeframe", "status", "reason",
    "window_start_utc", "window_end_utc", "eval_bars", "warmup_bars", "missing_bars",
    "trades_closed", "fills", "wins", "losses", "win_rate", "gross_pnl_usd", "fees_usd",
    "funding_usd", "funding_coverage_pct", "unrealized_pnl_usd_at_end", "net_pnl_usd",
    "return_pct", "profit_factor", "max_drawdown_usd", "max_drawdown_pct", "time_in_range_pct",
    "range_break_count", "open_inventory_at_end", "execution_model", "principal_usd", "leverage",
    "taker_fee_pct", "maker_fee_pct", "trades_file", "notes",
]

GTB_EXEC = (
    "Candle OHLC only. Resting maker limit at each grid level; a buy fills at the level price "
    "when bar low <= level, its paired sell (next level up) fills when bar high >= level, no "
    "slippage on grid fills, maker fee each side. Bars touching both sides use intrabar_path="
    "worst (both orders simulated, lower close equity kept). Stop/max-drawdown flatten is taker "
    "+ 1 bp slippage. Long only, unleveraged, static range."
)
GRR_EXEC = (
    "15m OHLC touch of an arithmetic level inside a 1h-regime-gated session opens a position at "
    "the level price +/-0.02% adverse slippage (modelled as MARKET, taker both legs). Exits are "
    "checked before entries on each 15m bar; target = adjacent level, stop = one spacing away; "
    "if a bar hits both, stop wins. Session end (regime kill / range break / end of data) "
    "flattens at the 1h close with slippage and taker fee. Long at/below mid, short at/above mid."
)


# --------------------------------------------------------------------------- util
def iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def jclean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): jclean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jclean(v) for v in value]
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(jclean(payload), indent=1), encoding="utf-8")
    os.replace(tmp, path)


def cell_name(strategy_id: str, symbol: str, tf: str) -> str:
    return f"{strategy_id}__{symbol}__{tf}"


def base_row(engine: str, strategy_id: str, direction: str, symbol: str, tf: str) -> dict[str, Any]:
    row = {key: None for key in ROW_KEYS}
    row.update(
        family="grid",
        engine=engine,
        strategy_id=strategy_id,
        direction=direction,
        symbol=symbol,
        timeframe=tf,
        status="NOT_RUN",
        reason="",
        window_start_utc=iso(WINDOW_START),
        window_end_utc=iso(WINDOW_END),
        principal_usd=CAPITAL,
        notes="",
    )
    return row


def db_url() -> str:
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').replace("postgresql+asyncpg", "postgresql")
    raise RuntimeError("DATABASE_URL not found in .env")


async def connect():
    import asyncpg

    conn = await asyncpg.connect(
        db_url(), server_settings={"default_transaction_read_only": "on"}, timeout=60
    )
    proof = {
        "default_transaction_read_only": await conn.fetchval("SHOW default_transaction_read_only"),
        "transaction_read_only": await conn.fetchval("SHOW transaction_read_only"),
    }
    if proof["transaction_read_only"] != "on":
        await conn.close()
        raise RuntimeError(f"session is not read-only: {proof}")
    return conn, proof


def to_candles(rows: Any) -> list[dict[str, Any]]:
    # Same shape as app.research.postgres_ohlcv._rows_to_candles.
    return [
        {
            "time": r[0],
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5] or 0),
        }
        for r in rows
    ]


async def load_range(conn, symbol: str, tf: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT time, open, high, low, close, volume FROM ohlcv "
        "WHERE symbol = $1 AND timeframe = $2 AND time >= $3 AND time < $4 ORDER BY time ASC",
        symbol, tf, start, end,
    )
    return to_candles(rows)


async def load_before(conn, symbol: str, tf: str, before: datetime, limit: int) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT time, open, high, low, close, volume FROM ("
        " SELECT time, open, high, low, close, volume FROM ohlcv"
        " WHERE symbol = $1 AND timeframe = $2 AND time < $3 ORDER BY time DESC LIMIT $4"
        ") recent ORDER BY time ASC",
        symbol, tf, before, limit,
    )
    return to_candles(rows)


async def load_settlements(conn, symbol: str, start: datetime, end: datetime) -> tuple[list[dict[str, Any]], str | None]:
    """Same query as app.grid_trading.settlements._read_settlements (rows part)."""
    try:
        found = await conn.fetch(
            "SELECT funding_time, funding_rate, mark_price FROM grid_funding_settlements "
            "WHERE symbol = $1 AND funding_time >= $2 AND funding_time <= $3 ORDER BY funding_time",
            symbol, start, end,
        )
    except Exception as exc:  # table missing
        if "does not exist" in str(exc):
            return [], "Funding is unavailable. Table grid_funding_settlements is missing."
        raise
    rows = [
        {"time": r[0], "rate": float(r[1]), "mark_price": None if r[2] is None else float(r[2])}
        for r in found
    ]
    return rows, None


def expected_bars(tf: str) -> int:
    return int((WINDOW_END - WINDOW_START).total_seconds() // TF_SECONDS[tf])


# --------------------------------------------------------------------------- grid trading book
def pair_fills(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """FIFO buy/sell per slot (engine._cycle_profits / UI gridTrades). A flatten sell
    (slot None) closes every still-open slot buy at once and is one row."""
    pending: dict[int, list[dict[str, Any]]] = {}
    trips: list[dict[str, Any]] = []
    for fill in fills:
        slot = fill.get("slot")
        qty, px, fee = float(fill["qty"]), float(fill["price"]), float(fill["fee"])
        if fill["side"] == "buy":
            pending.setdefault(int(slot), []).append(fill)
            continue
        if slot is None:
            debit = fees = 0.0
            first = None
            for stack in pending.values():
                for buy in stack:
                    debit += float(buy["qty"]) * float(buy["price"]) + float(buy["fee"])
                    fees += float(buy["fee"])
                    first = buy["time"] if first is None else min(first, buy["time"])
            pending.clear()
            trips.append(
                {
                    "kind": fill.get("reason") or "flatten",
                    "slot": None,
                    "entry_time": first,
                    "exit_time": fill["time"],
                    "entry_price": None,
                    "exit_price": px,
                    "qty": qty,
                    "fees": fees + fee,
                    "net_pnl": qty * px - fee - debit,
                }
            )
            continue
        stack = pending.get(int(slot)) or []
        if not stack:
            continue
        buy = stack.pop(0)
        debit = float(buy["qty"]) * float(buy["price"]) + float(buy["fee"])
        trips.append(
            {
                "kind": "grid",
                "slot": int(slot),
                "entry_time": buy["time"],
                "exit_time": fill["time"],
                "entry_price": float(buy["price"]),
                "exit_price": px,
                "qty": qty,
                "fees": float(buy["fee"]) + fee,
                "net_pnl": qty * px - fee - debit,
            }
        )
    return trips


def gtb_cell(
    variant: str,
    symbol: str,
    tf: str,
    candles: list[dict[str, Any]],
    prior: list[dict[str, Any]],
    funding: list[dict[str, Any]],
    funding_reason: str | None,
) -> dict[str, Any]:
    from app.grid_trading.config import GridConfig
    from app.grid_trading.engine import run_grid
    from app.grid_trading.scanner import score_series

    grid_type, grid_count, _origin = GTB_VARIANTS[variant]
    row = base_row(GTB_ENGINE, variant, "LONG", symbol, tf)
    row.update(
        execution_model=GTB_EXEC,
        leverage=1.0,
        taker_fee_pct=GTB_BASE["taker_fee_bps"] / 100.0,
        maker_fee_pct=GTB_BASE["fee_bps"] / 100.0,
        eval_bars=len(candles),
        warmup_bars=len(prior),
        missing_bars=expected_bars(tf) - len(candles),
    )
    if len(candles) < 2:
        row.update(status="NOT_RUN", reason="no candles in the evaluation window")
        return row
    if len(prior) < 30:
        row.update(status="NOT_RUN", reason=f"only {len(prior)} bars before the window; scanner needs 30")
        return row
    t0 = time.perf_counter()
    scan = score_series(
        prior,
        fee_bps=GTB_BASE["fee_bps"],
        slippage_bps=GTB_BASE["slippage_bps"],
        grid_count=SCAN_GRID_COUNT,
        symbol=symbol,
        timeframe=tf,
    )
    if scan.get("range_low") is None or scan.get("range_high") is None or scan.get("stop_price") is None:
        row.update(status="NOT_RUN", reason=f"scanner returned no range ({scan.get('reason')})")
        return row
    config = GridConfig(
        symbol=symbol,
        timeframe=tf,
        mode="backtest",
        grid_type=grid_type,
        range_low=scan["range_low"],
        range_high=scan["range_high"],
        grid_count=grid_count,
        stop_price=scan["stop_price"],
        **GTB_BASE,
    )
    result = run_grid(candles, config, funding or None)
    if funding_reason:  # api._apply_funding_reason
        result["funding_status"] = "unavailable"
        result["net_pnl"] = None
        result["funding_included"] = False
        result["warnings"] = [funding_reason]
    elapsed = time.perf_counter() - t0

    fills = result["fills"]
    trips = pair_fills(fills)
    wins = [t["net_pnl"] for t in trips if t["net_pnl"] > 0]
    losses = [t["net_pnl"] for t in trips if t["net_pnl"] < 0]
    fees_paid = float(result["fees_paid"])
    realized = float(result["realized_pnl"])
    unrealized = float(result["unrealized_pnl"])
    before_funding = float(result["pnl_before_funding"])
    funding_paid = float(result["funding_paid"])
    status = result["funding_status"]
    funded = status == "charged" and result["net_pnl"] is not None
    net = float(result["net_pnl"]) if funded else before_funding

    peak = CAPITAL
    dd_usd = 0.0
    for point in result["equity_curve"]:
        equity = float(point["equity"])
        peak = max(peak, equity)
        dd_usd = max(dd_usd, peak - equity)
    events = result["events"]
    range_exits = sum(1 for e in events if e.get("type") == "range_exit")
    kept_events = [e for e in events if e.get("type") != "range_exit"]
    seen = len(result["equity_curve"])
    last_close = candles[seen - 1]["close"]
    base = float(result["base"])
    buys =sum(1 for f in fills if f["side"] == "buy")
    slot_sells = sum(1 for f in fills if f["side"] == "sell" and f.get("slot") is not None)
    flattened = any(f.get("slot") is None for f in fills)
    open_slots = 0 if flattened else buys - slot_sells

    halt_time = next((iso(e.get("time")) for e in kept_events if e.get("type") in ("stop", "drawdown", "paused")), None)
    trades_file = f"trades/{cell_name(variant, symbol, tf)}.json"
    write_json(
        HERE / trades_file,
        {
            "cell": cell_name(variant, symbol, tf),
            "engine": GTB_ENGINE,
            "config": config.model_dump(),
            "scan": scan,
            "levels": result["levels"],
            "summary": {
                k: result[k]
                for k in (
                    "accepted", "error", "halted", "fill_count", "touch_count", "fill_rate",
                    "round_trips", "cycle_count", "dual_touch_bars", "fees_paid", "realized_pnl",
                    "unrealized_pnl", "net_pnl", "pnl_before_funding", "funding_status",
                    "funding_paid", "funding_mark_prices", "funding_closes",
                    "funding_coverage_pct", "pct_time_in_range", "avg_profit_per_grid_cycle",
                    "quote", "base", "equity", "liquidated_equity", "return", "max_drawdown",
                    "bar_count", "warnings", "buy_and_hold",
                )
            },
            "bars_simulated": seen,
            "last_close": last_close,
            "round_trips": trips,
            "fills": fills,
            "events_without_range_exit": kept_events,
            "range_exit_bars": range_exits,
        },
    )

    notes = [
        "net_pnl_usd = realized_pnl + unrealized_pnl "
        + ("- funding_paid (engine net_pnl; funding INCLUDED)." if funded else
           f"(engine pnl_before_funding; NET_EXCLUDES_FUNDING because funding_status={status}, "
           "engine hides funded net)."),
        "Open inventory is marked to the last close with no exit fee/slippage; "
        "gross_pnl_usd = net before fees and funding (includes 1bp slippage on a stop/drawdown flatten); "
        "unrealized_pnl_usd_at_end is the engine figure (cost basis includes buy fees) and is already inside gross+fees.",
        f"funding_status={status}; funding_usd is the engine's signed funding (negative = paid)"
        + ("" if funded else " over the stored settlements only and is NOT in net_pnl_usd") + ".",
        f"range_low={config.range_low:.8g} range_high={config.range_high:.8g} stop={config.stop_price:.8g} "
        f"from scanner on the {len(prior)} {tf} bars before the window "
        f"(scan score={scan.get('score')}, eligible={scan.get('eligible')}, reason={scan.get('reason')}); "
        "static range for the whole window.",
        f"halted={result['halted']} halt_time={halt_time} accepted={result['accepted']} error={result['error']} "
        f"bars_simulated={seen}/{len(candles)} dual_touch_bars={result['dual_touch_bars']}.",
        "trades_closed = slot buy->sell cycles + 1 if a stop/drawdown flatten closed inventory; "
        f"engine round_trips={result['round_trips']}; flatten={'yes' if flattened else 'no'}.",
        f"open_inventory_at_end is USD notional at last close (qty={base:.8g}, open_slots={open_slots}).",
        f"engine liquidated return (taker+slippage exit, incl funding)={result['return'] * 100:.4f}%; "
        f"buy_and_hold return={result['buy_and_hold']['return'] * 100:.4f}%.",
        "max_drawdown is on bar-close marked equity (incl. funding when charged), pct is of peak equity. "
        "range_break_count = number of bars that traded outside [range_low, range_high] (engine range_exit events), not distinct breaks; "
        "after a stop the book is flat for the rest of the window.",
        "Fees: maker 2 bps / taker 4 bps as fixed by the task (engine default fee_bps=4, taker falls back to fee_bps; UI sends fee_bps=4). "
        "Leverage 1 (engine rejects >1; the task's leverage 2 is not available).",
        f"data {iso(candles[0]['time'])}..{iso(candles[-1]['time'])}.",
    ]
    if result["warnings"]:
        notes.append("engine warnings: " + " | ".join(str(w) for w in result["warnings"]))

    row.update(
        status="OK",
        reason="",
        trades_closed=len(trips),
        fills=len(fills),
        wins=len(wins),
        losses=len(losses),
        win_rate=(len(wins) / len(trips)) if trips else None,
        gross_pnl_usd=realized + unrealized + fees_paid,
        fees_usd=-fees_paid,
        funding_usd=((-funding_paid + 0.0) if status in ("charged", "incomplete") else None),
        funding_coverage_pct=(
            None if result["funding_coverage_pct"] is None else float(result["funding_coverage_pct"]) * 100.0
        ),
        unrealized_pnl_usd_at_end=unrealized,
        net_pnl_usd=net,
        return_pct=net / CAPITAL * 100.0,
        profit_factor=(sum(wins) / abs(sum(losses))) if losses and wins else None,
        max_drawdown_usd=dd_usd,
        max_drawdown_pct=float(result["max_drawdown"]) * 100.0,
        time_in_range_pct=float(result["pct_time_in_range"]) * 100.0,
        range_break_count=range_exits,
        open_inventory_at_end=base * last_close,
        trades_file=trades_file,
        notes=" ".join(notes),
    )
    row["_elapsed_s"] = round(elapsed, 2)
    return row


async def gtb_task(symbol: str, tf: str, variants: list[str]) -> list[str]:
    todo = [v for v in variants if not (CELLS / f"{cell_name(v, symbol, tf)}.json").exists()]
    if not todo:
        return []
    conn, _proof = await connect()
    try:
        candles = await load_range(conn, symbol, tf, WINDOW_START, WINDOW_END)
        prior = await load_before(conn, symbol, tf, WINDOW_START, SCAN_LIMIT)
        funding: list[dict[str, Any]] = []
        reason = None
        if candles:
            funding, reason = await load_settlements(conn, symbol, candles[0]["time"], candles[-1]["time"])
    finally:
        await conn.close()
    done = []
    for variant in todo:
        try:
            row = gtb_cell(variant, symbol, tf, candles, prior, funding, reason)
        except Exception as exc:
            row = base_row(GTB_ENGINE, variant, "LONG", symbol, tf)
            row.update(
                status="ERROR",
                reason=f"{type(exc).__name__}: {exc}",
                notes=traceback.format_exc()[-600:],
                eval_bars=len(candles),
                execution_model=GTB_EXEC,
            )
        write_json(CELLS / f"{cell_name(variant, symbol, tf)}.json", row)
        done.append(f"{cell_name(variant, symbol, tf)} {row['status']} {row.get('_elapsed_s')}s net={row.get('net_pnl_usd')}")
    return done


# --------------------------------------------------------------------------- grid range research
def grr_cell(
    variant_id: str,
    symbol: str,
    c1h: list[dict[str, Any]],
    c15: list[dict[str, Any]],
    idx0: int,
    regimes: list[str],
    bounds: list[Any],
    map_15: list[list[int]],
) -> dict[str, Any]:
    from app.research.grid_range_research.config import get_variant
    from app.research.grid_range_research.engine import run_grid_backtest
    from app.research.grid_range_research.risk import GridRiskCaps

    variant = get_variant(variant_id)
    caps = GridRiskCaps(
        risk_usd_per_position=20.0,
        max_simultaneous=variant.max_simultaneous,
        account_equity=CAPITAL,
        leverage=2.0,
        taker_fee=0.0004,
        maker_fee=0.0002,
    )
    row = base_row(GRR_ENGINE, variant_id, "NEUTRAL", symbol, GRR_TIMEFRAME)
    row.update(
        execution_model=GRR_EXEC,
        leverage=2.0,
        taker_fee_pct=0.04,
        maker_fee_pct=0.02,
        eval_bars=len(c15),
        warmup_bars=idx0,
        missing_bars=expected_bars("15m") - len(c15),
    )
    t0 = time.perf_counter()
    result = run_grid_backtest(
        symbol, c1h, c15, variant,
        index_start=idx0,
        risk_caps=caps,
        precomputed_regimes=regimes,
        precomputed_bounds=bounds,
        precomputed_map_15=map_15,
    )
    elapsed = time.perf_counter() - t0
    cycles = result.cycles
    sessions = result.sessions
    nets = [float(c["net_pnl"]) for c in cycles if c.get("net_pnl") is not None]
    unpriced = len(cycles) - len(nets)
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    gross = sum(float(c["gross_pnl"]) for c in cycles if c.get("gross_pnl") is not None)
    fees = sum(float(c["fees"]) for c in cycles if c.get("fees") is not None)
    net = sum(nets)
    equity = peak = CAPITAL
    dd_usd = dd_pct = 0.0
    for x in nets:
        equity += x
        peak = max(peak, equity)
        dd_usd = max(dd_usd, peak - equity)
        dd_pct = max(dd_pct, (peak - equity) / peak if peak > 0 else 0.0)
    eval_regimes = regimes[idx0:]
    enabled = sum(1 for r in eval_regimes if r in variant.enabled_regimes)
    breaks = sum(1 for s in sessions if str(s.get("reason_grid_stopped") or "").startswith("RANGE_BREAK"))
    capped = sum(1 for c in cycles if c.get("qty") is not None and float(c["qty"]) * float(c["entry_price"]) >= CAPITAL * 2.0 - 1e-6)
    total_r = sum(float(c["r_multiple"]) for c in cycles)

    trades_file = f"trades/{cell_name(variant_id, symbol, GRR_TIMEFRAME)}.json"
    write_json(
        HERE / trades_file,
        {
            "cell": cell_name(variant_id, symbol, GRR_TIMEFRAME),
            "engine": GRR_ENGINE,
            "variant": {
                "variant_id": variant.variant_id,
                "n_levels": variant.n_levels,
                "max_simultaneous": variant.max_simultaneous,
                "include_lvc": variant.include_lvc,
                "enabled_regimes": sorted(variant.enabled_regimes),
            },
            "risk_summary": result.risk_summary,
            "funding_status": result.funding_status,
            "round_trips": cycles,
            "sessions": sessions,
        },
    )
    notes = [
        "net_pnl_usd = sum of per-cycle net_pnl (gross price PnL incl. 0.02% adverse slippage on entry and exit, "
        "plus taker fees on both legs). FUNDING_NOT_MODELED by this engine: funding_usd is null and net EXCLUDES funding.",
        "No open inventory at the end: the engine flattens the open session at the last 1h close (END_OF_DATA), so unrealized is 0 and that exit is inside net.",
        "Sizing: fixed $20 risk per position (stop one grid spacing away), qty capped at notional $2000 per position "
        f"(equity 1000 x leverage 2); {capped} of {len(cycles)} cycles hit that cap. No compounding. "
        f"max simultaneous positions={variant.max_simultaneous}, so priced exposure can reach {variant.max_simultaneous} x $2000 "
        "(the notional cap is per position, not per book). The engine has no equity/bankruptcy stop: "
        "losses are summed on fixed sizing and can exceed the $1000 principal.",
        f"n_levels={variant.n_levels} arithmetic between last confirmed 1h swing low/high; sessions={len(sessions)}; "
        f"enabled-regime hours={enabled}/{len(eval_regimes)} ({(enabled / len(eval_regimes) * 100 if eval_regimes else 0):.1f}%).",
        "timeframe=15m is the fill timeframe; regime gate, range bounds and kills are on 1h. The engine supports no other timeframe.",
        f"warmup_bars = {idx0} 1h bars before the window, used only to form the 1h indicators (original script used 100; more warmup changes no parameter).",
        "max_drawdown is on closed-cycle equity (1000 + cumulative net), not marked to market intrabar; pct is of peak equity. "
        "range_break_count = sessions ended by RANGE_BREAK_UPPER/LOWER.",
        f"total net R on $20 requested risk = {total_r:.4f}; cycles without a priced net_pnl = {unpriced}.",
        f"data 15m {iso(c15[0]['time'])}..{iso(c15[-1]['time'])}; 1h {iso(c1h[0]['time'])}..{iso(c1h[-1]['time'])}.",
    ]
    row.update(
        status="OK",
        reason="",
        trades_closed=len(cycles),
        fills=2 * len(cycles),
        wins=len(wins),
        losses=len(losses),
        win_rate=(len(wins) / len(nets)) if nets else None,
        gross_pnl_usd=gross,
        fees_usd=fees,
        funding_usd=None,
        funding_coverage_pct=None,
        unrealized_pnl_usd_at_end=0.0,
        net_pnl_usd=net,
        return_pct=net / CAPITAL * 100.0,
        profit_factor=(sum(wins) / abs(sum(losses))) if losses and wins else None,
        max_drawdown_usd=dd_usd,
        max_drawdown_pct=dd_pct * 100.0,
        time_in_range_pct=None,
        range_break_count=breaks,
        open_inventory_at_end=0.0,
        trades_file=trades_file,
        notes=" ".join(notes),
    )
    row["_elapsed_s"] = round(elapsed, 2)
    return row


async def grr_task(symbol: str, variants: list[str]) -> list[str]:
    todo = [v for v in variants if not (CELLS / f"{cell_name(v, symbol, GRR_TIMEFRAME)}.json").exists()]
    if not todo:
        return []
    conn, _proof = await connect()
    try:
        warm = await load_before(conn, symbol, "1h", WINDOW_START, GRR_WARMUP_1H)
        body = await load_range(conn, symbol, "1h", WINDOW_START, WINDOW_END)
        c15 = await load_range(conn, symbol, "15m", WINDOW_START, WINDOW_END)
    finally:
        await conn.close()
    c1h = warm + body
    idx0 = len(warm)
    done = []
    shared: tuple[Any, Any, Any] | None = None
    shared_error: str | None = None
    if body and c15:
        try:
            from app.research.grid_range_research.engine import (
                _build_1h_to_15m_map,
                _precompute_regimes_and_bounds,
            )

            regimes, bounds = _precompute_regimes_and_bounds(c1h, symbol=symbol, index_start=idx0)
            shared = (regimes, bounds, _build_1h_to_15m_map(c1h, c15))
        except Exception as exc:
            shared_error = f"{type(exc).__name__}: {exc}"
    for variant in todo:
        row = base_row(GRR_ENGINE, variant, "NEUTRAL", symbol, GRR_TIMEFRAME)
        row.update(execution_model=GRR_EXEC, eval_bars=len(c15), warmup_bars=idx0, leverage=2.0,
                   taker_fee_pct=0.04, maker_fee_pct=0.02)
        if not body or not c15:
            row.update(status="NOT_RUN", reason="no 1h or 15m candles in the evaluation window")
        elif shared is None:
            row.update(status="ERROR", reason=shared_error or "precompute failed")
        else:
            try:
                row = grr_cell(variant, symbol, c1h, c15, idx0, *shared)
            except Exception as exc:
                row.update(status="ERROR", reason=f"{type(exc).__name__}: {exc}",
                           notes=traceback.format_exc()[-600:])
        write_json(CELLS / f"{cell_name(variant, symbol, GRR_TIMEFRAME)}.json", row)
        done.append(f"{cell_name(variant, symbol, GRR_TIMEFRAME)} {row['status']} {row.get('_elapsed_s')}s net={row.get('net_pnl_usd')}")
    return done


# --------------------------------------------------------------------------- phases
def run_task(task: tuple) -> list[str]:
    try:
        if task[0] == GTB_ENGINE:
            return asyncio.run(gtb_task(task[1], task[2], task[3]))
        return asyncio.run(grr_task(task[1], task[2]))
    except Exception as exc:
        return [f"TASK FAILED {task[:3]}: {type(exc).__name__}: {exc}"]


def phase_strategies() -> None:
    items = []
    for vid, (grid_type, count, origin) in GTB_VARIANTS.items():
        items.append(
            {
                "id": vid,
                "engine": GTB_ENGINE,
                "module": "app.grid_trading",
                "direction": "LONG",
                "origin": origin,
                "supported_timeframes": GTB_TIMEFRAMES,
                "timeframe_note": "UI offers 15m/1h/4h/1d; the engine rejects no timeframe and the loader knows 5m, so 5m is run too.",
                "parameters": {
                    "grid_type": grid_type,
                    "grid_count": count,
                    **GTB_BASE,
                    "range_low/range_high/stop_price": (
                        f"app.grid_trading.scanner.score_series on the {SCAN_LIMIT} bars before the window "
                        f"(grid_count={SCAN_GRID_COUNT}): min low / max high inset 5% each side, stop = 0.99 x min low"
                    ),
                },
                "engine_defaults_that_differ": {
                    "fee_bps": "engine 4.0, UI 4.0 (run: 2.0 maker as fixed by the task)",
                    "taker_fee_bps": "engine None -> fee_bps, UI omits (run: 4.0)",
                    "max_drawdown": "engine 0.5, UI 0.35 (run: UI 0.35)",
                    "max_open_orders": "engine 50, UI 20 (run: UI 20)",
                    "max_capital": "engine 1,000,000, UI 100,000 (run: UI)",
                    "leverage": "engine 1.0 and rejects > 1 (task asked 2; not available)",
                },
            }
        )
    items.append(
        {
            "id": GTB_INSAMPLE,
            "engine": GTB_ENGINE,
            "module": "app.grid_trading",
            "direction": "LONG",
            "origin": "UI onBacktest flow as shipped (scan and backtest the same bars)",
            "supported_timeframes": GTB_TIMEFRAMES,
            "status": "NOT_RUN",
            "reason": GTB_INSAMPLE_REASON,
            "parameters": {"grid_type": "geometric", "grid_count": 8, **GTB_BASE},
        }
    )
    from app.research.grid_range_research.config import GRID_KILL_REGIMES, get_variant

    for vid in GRR_VARIANTS:
        v = get_variant(vid)
        items.append(
            {
                "id": vid,
                "engine": GRR_ENGINE,
                "module": "app.research.grid_range_research",
                "direction": "NEUTRAL",
                "origin": "GRID_VARIANTS in config.py (run_grid_range_research.py runs all four)",
                "supported_timeframes": [GRR_TIMEFRAME],
                "timeframe_note": "Fixed architecture: 1h regime/bounds + 15m fills. 5m/1h/4h/1d are not supported.",
                "parameters": {
                    "n_levels": v.n_levels,
                    "max_simultaneous": v.max_simultaneous,
                    "include_lvc": v.include_lvc,
                    "enabled_regimes": sorted(v.enabled_regimes),
                    "kill_regimes": sorted(GRID_KILL_REGIMES),
                    "risk_usd_per_position": 20.0,
                    "account_equity": CAPITAL,
                    "leverage": 2.0,
                    "taker_fee": 0.0004,
                    "maker_fee": 0.0002,
                    "slippage_rate": 0.0002,
                    "warmup_1h_bars": GRR_WARMUP_1H,
                    "funding": "FUNDING_NOT_MODELED",
                },
            }
        )
    write_json(
        HERE / "_strategies.json",
        {
            "family": "grid",
            "symbols": SYMBOLS,
            "window_start_utc": iso(WINDOW_START),
            "window_end_utc_exclusive": iso(WINDOW_END),
            "capital_usd": CAPITAL,
            "strategies": items,
        },
    )
    print(f"wrote _strategies.json ({len(items)} strategies)")


async def phase_coverage() -> None:
    from app.grid_trading.funding import assess_coverage, distinct_settlements, normalize_funding

    conn, proof = await connect()
    out: dict[str, Any] = {
        "window_start_utc": iso(WINDOW_START),
        "window_end_utc_exclusive": iso(WINDOW_END),
        "source_table": "grid_funding_settlements (charge mode never reads funding_rates)",
        "method": "app.grid_trading.funding.assess_coverage over [window start, last 1h candle open]; "
                  "a gap wider than 1.5x the median stored interval counts as missing settlements. "
                  "Each cell also carries the engine's own funding_coverage_pct for its candles.",
        "engine_rule": "coverage < 95% => funding_status=incomplete, funded net PnL hidden",
        "read_only": proof,
        "symbols": {},
    }
    try:
        for symbol in SYMBOLS:
            last = await conn.fetchval(
                "SELECT max(time) FROM ohlcv WHERE symbol=$1 AND timeframe='1h' AND time >= $2 AND time < $3",
                symbol, WINDOW_START, WINDOW_END,
            )
            end = last or (WINDOW_END - timedelta(hours=1))
            rows, reason = await load_settlements(conn, symbol, WINDOW_START, end)
            try:
                ranges = await conn.fetch(
                    'SELECT "from", "to", fetched_at FROM grid_funding_fetch_ranges WHERE symbol=$1 ORDER BY 1', symbol
                )
            except Exception as exc:
                ranges = []
                reason = reason or str(exc)
            total = await conn.fetchrow(
                "SELECT count(*), min(funding_time), max(funding_time) FROM grid_funding_settlements WHERE symbol=$1",
                symbol,
            ) if not reason else (0, None, None)
            entry: dict[str, Any] = {
                "settlements_in_window": len(rows),
                "first": iso(rows[0]["time"]) if rows else None,
                "last": iso(rows[-1]["time"]) if rows else None,
                "with_mark_price": sum(1 for r in rows if r["mark_price"] is not None),
                "table_rows_all_time": total[0],
                "table_first": iso(total[1]),
                "table_last": iso(total[2]),
                "fetch_ranges": [{"from": iso(r[0]), "to": iso(r[1]), "fetched_at": iso(r[2])} for r in ranges],
                "reason": reason,
            }
            if rows:
                norm = distinct_settlements(normalize_funding(rows))
                stamps = [float(r["stamp"]) for r in norm]
                gaps = [b - a for a, b in zip(stamps, stamps[1:])]
                cov = assess_coverage(norm, window_start=WINDOW_START.timestamp(), window_end=end.timestamp())
                entry.update(
                    median_interval_hours=(median(gaps) / 3600.0) if gaps else None,
                    max_gap_hours=(max(gaps) / 3600.0) if gaps else None,
                    coverage_pct=cov["funding_coverage_pct"] * 100.0,
                    engine_status="charged" if cov["funding_coverage_pct"] >= 0.95 else "incomplete",
                    warnings=cov["warnings"],
                    uncovered_from=iso(datetime.fromtimestamp(cov["uncovered_from"], timezone.utc)) if cov["uncovered_from"] else None,
                    uncovered_to=iso(datetime.fromtimestamp(cov["uncovered_to"], timezone.utc)) if cov["uncovered_to"] else None,
                )
            else:
                entry.update(coverage_pct=0.0, engine_status="unavailable", warnings=[reason or "no settlement rows"])
            out["symbols"][symbol] = entry
            print(symbol, entry["settlements_in_window"], entry.get("coverage_pct"), entry["engine_status"])
    finally:
        await conn.close()
    write_json(HERE / "_funding_coverage.json", out)


def build_tasks(args) -> list[tuple]:
    symbols = args.symbols.split(",") if args.symbols else SYMBOLS
    tfs = args.timeframes.split(",") if args.timeframes else GTB_TIMEFRAMES
    gtb = args.variants.split(",") if args.variants else list(GTB_VARIANTS)
    gtb = [v for v in gtb if v in GTB_VARIANTS]
    grr = args.variants.split(",") if args.variants else list(GRR_VARIANTS)
    grr = [v for v in grr if v in GRR_VARIANTS]
    tasks: list[tuple] = []
    if args.engine in ("all", "grr") and grr:
        tasks += [(GRR_ENGINE, s, grr) for s in symbols]
    if args.engine in ("all", "gtb") and gtb:
        # slow timeframes first so the pool stays balanced
        for tf in [t for t in GTB_TIMEFRAMES if t in tfs]:
            tasks += [(GTB_ENGINE, s, tf, gtb) for s in symbols]
    return tasks


def phase_run(args) -> None:
    from multiprocessing import Pool

    CELLS.mkdir(parents=True, exist_ok=True)
    TRADES.mkdir(parents=True, exist_ok=True)
    marker = HERE / "_run_started.json"
    if not marker.exists():
        write_json(marker, {"started_utc": iso(datetime.now(timezone.utc))})
    tasks = build_tasks(args)
    workers = max(1, min(3, args.workers))
    t0 = time.perf_counter()
    print(f"{len(tasks)} tasks, {workers} workers", flush=True)
    if workers == 1:
        for task in tasks:
            for line in run_task(task):
                print(f"[{time.perf_counter() - t0:7.1f}s] {line}", flush=True)
    else:
        with Pool(workers, maxtasksperchild=4) as pool:
            for lines in pool.imap_unordered(run_task, tasks):
                for line in lines:
                    print(f"[{time.perf_counter() - t0:7.1f}s] {line}", flush=True)
    print(f"done in {time.perf_counter() - t0:.1f}s", flush=True)


def phase_collect() -> None:
    rows = []
    missing = []
    order: list[tuple[str, str, str, str]] = []
    for vid in GRR_VARIANTS:
        order += [(GRR_ENGINE, vid, s, GRR_TIMEFRAME) for s in SYMBOLS]
    for vid in GTB_VARIANTS:
        for tf in GTB_TIMEFRAMES:
            order += [(GTB_ENGINE, vid, s, tf) for s in SYMBOLS]
    for engine, vid, symbol, tf in order:
        path = CELLS / f"{cell_name(vid, symbol, tf)}.json"
        if path.exists():
            cell = json.loads(path.read_text(encoding="utf-8"))
        else:
            missing.append(path.name)
            cell = base_row(engine, vid, "LONG" if engine == GTB_ENGINE else "NEUTRAL", symbol, tf)
            cell.update(status="NOT_RUN", reason="cell was not executed (runner did not reach it)")
        rows.append({key: cell.get(key) for key in ROW_KEYS})
    for tf in GTB_TIMEFRAMES:
        for symbol in SYMBOLS:
            cell = base_row(GTB_ENGINE, GTB_INSAMPLE, "LONG", symbol, tf)
            cell.update(status="NOT_RUN", reason=GTB_INSAMPLE_REASON, execution_model=GTB_EXEC,
                        leverage=1.0, taker_fee_pct=0.04, maker_fee_pct=0.02)
            rows.append({key: cell.get(key) for key in ROW_KEYS})
    write_json(HERE / "rows.json", rows)

    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        key = f"{row['engine']}|{row['timeframe']}"
        counts.setdefault(key, {"OK": 0, "NOT_RUN": 0, "ERROR": 0})[row["status"]] += 1
    started = None
    marker = HERE / "_run_started.json"
    if marker.exists():
        started = json.loads(marker.read_text(encoding="utf-8")).get("started_utc")

    def git(*cmd: str) -> str:
        return subprocess.run(["git", "-C", str(REPO), *cmd], capture_output=True, text=True).stdout

    proof = asyncio.run(_proof())
    write_json(
        HERE / "_meta.json",
        {
            "git_head": git("rev-parse", "HEAD").strip(),
            "git_branch": git("rev-parse", "--abbrev-ref", "HEAD").strip(),
            "git_status_porcelain_lines": len([l for l in git("status", "--porcelain").splitlines() if l.strip()]),
            "runner": str(Path(__file__).relative_to(REPO)).replace("\\", "/"),
            "runner_sha1": hashlib.sha1(Path(__file__).read_bytes()).hexdigest(),
            "started_utc": started,
            "finished_utc": iso(datetime.now(timezone.utc)),
            "read_only_proof": {
                **proof,
                "how": "every asyncpg connection is opened with server_settings default_transaction_read_only=on "
                       "and the runner refuses to continue unless SHOW transaction_read_only returns 'on'. "
                       "Only SELECT/SHOW statements are issued; no backfill script was run.",
            },
            "rows": len(rows),
            "counts_by_engine_timeframe": counts,
            "cells_missing": missing,
            "python": sys.version,
        },
    )
    print(json.dumps(counts, indent=1))
    print(f"rows={len(rows)} missing={len(missing)}")


async def _proof() -> dict[str, Any]:
    conn, proof = await connect()
    try:
        proof["server_version"] = await conn.fetchval("SHOW server_version")
        proof["current_database"] = await conn.fetchval("SELECT current_database()")
    finally:
        await conn.close()
    return proof


def phase_sanity(args) -> None:
    """Recompute net from the saved fills/cycles, independently of the engine totals."""
    out = []
    for name in args.cells.split(","):
        cell = json.loads((CELLS / f"{name}.json").read_text(encoding="utf-8"))
        data = json.loads((HERE / cell["trades_file"]).read_text(encoding="utf-8"))
        if cell["engine"] == GTB_ENGINE:
            fills = data["fills"]
            buys = sum(f["qty"] * f["price"] for f in fills if f["side"] == "buy")
            sells = sum(f["qty"] * f["price"] for f in fills if f["side"] == "sell")
            fees = sum(f["fee"] for f in fills)
            qty = sum(f["qty"] if f["side"] == "buy" else -f["qty"] for f in fills)
            mark = qty * data["last_close"]
            funding = sum(e["payment"] for e in data["events_without_range_exit"] if e["type"] == "funding")
            gross = sells - buys + mark
            before = gross - fees
            item = {
                "cell": name,
                "identity": "net = (sell notional - buy notional + open qty x last close) - fees - funding paid",
                "sell_notional": sells,
                "buy_notional": buys,
                "open_qty": qty,
                "last_close": data["last_close"],
                "open_inventory_marked": mark,
                "fees_from_fills": fees,
                "funding_paid_from_events": funding,
                "recomputed_gross": gross,
                "recomputed_pnl_before_funding": before,
                "recomputed_net_with_funding": before - funding,
                "row_gross_pnl_usd": cell["gross_pnl_usd"],
                "row_fees_usd": cell["fees_usd"],
                "row_funding_usd": cell["funding_usd"],
                "row_net_pnl_usd": cell["net_pnl_usd"],
                "engine_net_pnl": data["summary"]["net_pnl"],
                "engine_pnl_before_funding": data["summary"]["pnl_before_funding"],
                "engine_realized": data["summary"]["realized_pnl"],
                "engine_unrealized": data["summary"]["unrealized_pnl"],
                "engine_quote_plus_base_mark_minus_capital": data["summary"]["quote"] + qty * data["last_close"] - CAPITAL,
                "round_trip_net_sum": sum(t["net_pnl"] for t in data["round_trips"]),
                "funding_status": data["summary"]["funding_status"],
            }
            target = (before - funding) if data["summary"]["funding_status"] == "charged" else before
            item["abs_diff_vs_row_net"] = abs(target - cell["net_pnl_usd"])
            item["row_gross_plus_fees_plus_funding"] = cell["gross_pnl_usd"] + cell["fees_usd"] + (
                cell["funding_usd"] if data["summary"]["funding_status"] == "charged" else 0.0
            )
        else:
            cycles = data["round_trips"]
            gross = fees = 0.0
            for c in cycles:
                sign = 1.0 if c["direction"] == "LONG" else -1.0
                gross += sign * (c["exit_price"] - c["entry_price"]) * c["qty"]
                fees += (c["entry_price"] + c["exit_price"]) * c["qty"] * 0.0004
            item = {
                "cell": name,
                "identity": "net = sum(qty x signed (exit - entry)) - 0.04% x (entry + exit notional); no funding, no open inventory",
                "recomputed_gross": gross,
                "recomputed_fees": -fees,
                "recomputed_net": gross - fees,
                "row_gross_pnl_usd": cell["gross_pnl_usd"],
                "row_fees_usd": cell["fees_usd"],
                "row_net_pnl_usd": cell["net_pnl_usd"],
                "abs_diff_vs_row_net": abs(gross - fees - cell["net_pnl_usd"]),
            }
        item["pass"] = item["abs_diff_vs_row_net"] < 1e-4
        out.append(item)
        print(json.dumps(item, indent=1))
    write_json(HERE / "_sanity.json", out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["strategies", "coverage", "run", "collect", "sanity"])
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--engine", choices=["all", "gtb", "grr"], default="all")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--timeframes", default="")
    parser.add_argument("--variants", default="")
    parser.add_argument("--cells", default="")
    args = parser.parse_args()
    if args.phase == "strategies":
        phase_strategies()
    elif args.phase == "coverage":
        asyncio.run(phase_coverage())
    elif args.phase == "run":
        phase_run(args)
    elif args.phase == "collect":
        phase_collect()
    else:
        phase_sanity(args)


if __name__ == "__main__":
    main()
