"""Read-only ranking rows for the SCALPING engine and the INTRADAY BOS/SD engine.

Writes only under backend/scripts/strategy_rank/scalp/. Calls engine code, never edits it.
The DB session is read-only (default_transaction_read_only=on) and every SQL statement
is audited (SELECT / SHOW only).

INTRADAY_BOS_SD_V1 (variants A, B, C, D)
  Pure functions called per symbol:
    candles.series_from_rows / drop_incomplete_tail / validate_series
    setups.prepare_book -> setups.generate_signals(book, variant)
    portfolio.run_portfolio(candidates, slippage=..., initial_equity=...)
  NOT called: scripts/run_intraday_bos_sd.py::run (it writes reports/intraday_bos_sd/
  latest_report.json and state.json, and it executes SET statement_timeout).

SCALPING (scalp_*_v1)
  Pure function called per symbol: scalping.backtest.run_backtest(symbol, 1m, 5m, 15m).
  The engine needs 1m candles for every strategy. 1m history starts 2026-10-01, so the
  fixed window cannot be run: rows.json carries NOT_RUN rows for it. A separate, clearly
  labelled short-window run on the available 1m history goes to rows_supplemental_1m.json.
  The scalping engine has no sizing and no fees (R only). USD figures in the supplemental
  rows come from the repo's own app.research.trade_fees.enrich_trade_execution.

Usage (from backend/):
  python -B scripts/strategy_rank/scalp/run_scalp_intraday.py --workers 3
  python -B scripts/strategy_rank/scalp/run_scalp_intraday.py --only intraday --symbols BTCUSDT
  python -B scripts/strategy_rank/scalp/run_scalp_intraday.py --assemble-only
"""

from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import asyncio
import copy
import hashlib
import json
import math
import statistics
import subprocess
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(r"D:\cryptoscreener")
BACKEND = REPO / "backend"
OUT = BACKEND / "scripts" / "strategy_rank" / "scalp"
CELLS = OUT / "_cells"
TRADES = OUT / "trades"
TRADES_SUPP = OUT / "trades_supplemental_1m"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
           "ADAUSDT", "TRXUSDT", "AVAXUSDT", "LINKUSDT"]
WINDOW_START = datetime(2026, 2, 14, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 10, 12, tzinfo=timezone.utc)
LOAD_START = datetime(2026, 1, 15, tzinfo=timezone.utc)      # 5m history starts 2026-01-15
SCALP_AUX_START = datetime(2026, 9, 28, tzinfo=timezone.utc)  # 5m/15m warmup for the 1m run

PRINCIPAL_USD = 1000.0
RISK_USD = 20.0
LEVERAGE = 2.0
TAKER_FEE_PCT = 0.04
MAKER_FEE_PCT = 0.02

INTRADAY_VARIANTS = ("A", "B", "C", "D")
INTRADAY_TF = "5m"
INTRADAY_TIER = "LARGE_LIQUIDITY"
SCALP_TF = "1m"
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}

ROW_KEYS = [
    "family", "engine", "strategy_id", "direction", "symbol", "timeframe", "status", "reason",
    "window_start_utc", "window_end_utc", "eval_bars", "warmup_bars", "missing_bars",
    "trades_closed", "wins", "losses", "win_rate", "gross_pnl_usd", "fees_usd", "net_pnl_usd",
    "return_pct", "profit_factor", "avg_r_net", "max_drawdown_usd", "max_drawdown_r",
    "median_hold_hours", "avg_hold_hours", "open_trades_at_end", "execution_model",
    "principal_usd", "risk_usd", "leverage", "taker_fee_pct", "maker_fee_pct", "trades_file",
    "notes",
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def iso(ts: Any) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).isoformat()
    return str(ts)


def as_dt(ts: Any) -> datetime:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def clean(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, str, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, datetime):
        return iso(obj)
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [clean(v) for v in obj]
    return str(obj)


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(clean(payload), indent=1, allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)


def cell_done(path: Path) -> bool:
    try:
        return bool(json.loads(path.read_text(encoding="utf-8")).get("complete"))
    except Exception:  # noqa: BLE001
        return False


def series_gaps(times: list[datetime], tf: str, lo: datetime, hi: datetime) -> dict[str, Any]:
    """Bars with lo <= open < hi, and how many are missing against a gap-free grid."""
    step = TF_SECONDS[tf]
    inside = [t for t in times if lo <= t < hi]
    expected = int((hi - lo).total_seconds() // step)
    return {
        "bars": len(inside),
        "expected": expected,
        "missing": max(0, expected - len(inside)),
        "first": iso(inside[0]) if inside else None,
        "last": iso(inside[-1]) if inside else None,
    }


def metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """trades: dicts with net_pnl_usd, gross_pnl_usd, fees_usd (negative), r_net, hold_hours,
    exit_time. Ordered by exit time for the drawdown walk."""
    trades = sorted(trades, key=lambda t: (t["exit_time"], t["entry_time"]))
    n = len(trades)
    net = [float(t["net_pnl_usd"]) for t in trades]
    r = [float(t["r_net"]) for t in trades if t.get("r_net") is not None]
    wins = sum(1 for v in net if v > 0)
    losses = sum(1 for v in net if v < 0)
    gains = sum(v for v in net if v > 0)
    lost = sum(-v for v in net if v < 0)
    eq = peak = dd = 0.0
    for v in net:
        eq += v
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    eq = peak = ddr = 0.0
    for v in r:
        eq += v
        peak = max(peak, eq)
        ddr = max(ddr, peak - eq)
    holds = [float(t["hold_hours"]) for t in trades if t.get("hold_hours") is not None]
    net_sum = sum(net) if n else 0.0
    return {
        "trades_closed": n,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / n) if n else None,
        "gross_pnl_usd": sum(float(t["gross_pnl_usd"]) for t in trades) if n else 0.0,
        "fees_usd": sum(float(t["fees_usd"]) for t in trades) if n else 0.0,
        "net_pnl_usd": net_sum,
        "return_pct": net_sum / PRINCIPAL_USD * 100.0,
        "profit_factor": (gains / lost) if lost > 0 else None,
        "avg_r_net": (sum(r) / len(r)) if r else None,
        "max_drawdown_usd": dd if n else None,
        "max_drawdown_r": ddr if r else None,
        "median_hold_hours": statistics.median(holds) if holds else None,
        "avg_hold_hours": (sum(holds) / len(holds)) if holds else None,
    }


def make_row(**kw: Any) -> dict[str, Any]:
    row = {k: None for k in ROW_KEYS}
    row.update(kw)
    extra = set(row) - set(ROW_KEYS)
    if extra:
        raise RuntimeError(f"row has unknown keys {sorted(extra)}")
    return {k: row[k] for k in ROW_KEYS}


# --------------------------------------------------------------------------
# worker side
# --------------------------------------------------------------------------
_W: dict[str, Any] = {}


def _dsn() -> str:
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return url
    raise RuntimeError("DATABASE_URL not found in .env")


def _worker_init() -> None:
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    os.chdir(str(BACKEND))
    import logging

    logging.getLogger("dotenv.main").setLevel(logging.ERROR)
    from sqlalchemy import event, text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.services.database import db_manager

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    engine = create_async_engine(
        _dsn(), pool_size=1, max_overflow=0, pool_pre_ping=True,
        connect_args={"server_settings": {
            "default_transaction_read_only": "on",
            "application_name": "strategy_rank_scalp_readonly",
        }},
    )
    sql_audit: dict[str, int] = {}

    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        head = (statement or "").lstrip().split(None, 1)[0].upper() if (statement or "").strip() else "?"
        sql_audit[head] = sql_audit.get(head, 0) + 1
        if head not in ("SELECT", "SHOW"):
            raise RuntimeError(f"non-SELECT statement blocked: {statement[:120]!r}")

    event.listen(engine.sync_engine, "before_cursor_execute", _before)

    async def _probe() -> dict[str, str]:
        async with engine.connect() as conn:
            a = (await conn.execute(text("SHOW default_transaction_read_only"))).scalar()
            b = (await conn.execute(text("SHOW transaction_read_only"))).scalar()
        return {"default_transaction_read_only": str(a), "transaction_read_only": str(b)}

    ro = loop.run_until_complete(_probe())
    if ro["default_transaction_read_only"] != "on" or ro["transaction_read_only"] != "on":
        raise RuntimeError(f"DB session is not read-only: {ro}")
    db_manager.engine = engine
    db_manager.enabled = True
    db_manager.status = "ok"
    db_manager.schema_ready = True
    _W.update(loop=loop, engine=engine, sql_audit=sql_audit, ro=ro)


# ---------------------------- INTRADAY ------------------------------------
def _intraday_trade(fill: Any, setup: Any) -> dict[str, Any]:
    sig = fill.signal
    hold = (fill.exit_time - fill.entry_time).total_seconds() / 3600.0
    return {
        "symbol": sig.symbol,
        "variant": sig.variant,
        "direction": sig.side,
        "tier": sig.tier,
        "signal_time": iso(sig.setup_time),
        "entry_index": sig.entry_index,
        "entry_time": iso(fill.entry_time),
        "entry_bar_open_raw": setup.opens[sig.entry_index],
        "entry_price": fill.entry_price,
        "stop_price": fill.stop,
        "target_price": fill.target,
        "exit_time": iso(fill.exit_time),
        "exit_price": fill.exit_price,
        "exit_reason": fill.exit_reason,
        "quantity": fill.quantity,
        "notional_entry_usd": fill.notional,
        "risk_usd_actual": abs(fill.entry_price - fill.stop) * fill.quantity,
        "gross_pnl_usd": fill.gross_pnl,
        "fees_usd": fill.fees,
        "funding_pnl_usd": fill.funding_pnl,
        "slippage_cost_entry_usd": fill.slippage_cost,
        "net_pnl_usd": fill.net_pnl,
        "r_net": fill.r_multiple,
        "hold_hours": hold,
        "zone_low": sig.zone_low,
        "zone_high": sig.zone_high,
        "volume_ratio": sig.volume_ratio,
    }


async def _intraday_symbol(symbol: str) -> dict[str, Any]:
    from app.research.intraday_bos_sd import spec
    from app.research.intraday_bos_sd.candles import (
        drop_incomplete_tail, series_from_rows, validate_series,
    )
    from app.research.intraday_bos_sd.portfolio import run_portfolio
    from app.research.intraday_bos_sd.setups import generate_signals, prepare_book
    from app.research.postgres_ohlcv import load_ohlcv_range_with_quote

    timing: dict[str, float] = {}
    t0 = time.perf_counter()
    now = datetime.now(timezone.utc)
    rows = {}
    for tf in ("5m", "15m", "1h"):
        rows[tf] = await load_ohlcv_range_with_quote(symbol, tf, start=LOAD_START, end_exclusive=WINDOW_END)
    timing["load_seconds"] = round(time.perf_counter() - t0, 2)
    setup = drop_incomplete_tail(series_from_rows("5m", rows["5m"]), now)
    zone = drop_incomplete_tail(series_from_rows("15m", rows["15m"]), now)
    direction = drop_incomplete_tail(series_from_rows("1h", rows["1h"]), now)
    q5 = validate_series(setup)
    times5 = [b.open_time for b in setup.bars]
    cov = series_gaps(times5, "5m", WINDOW_START, WINDOW_END)
    warm = sum(1 for t in times5 if t < WINDOW_START)
    data_end = setup.close_time(len(setup) - 1) if len(setup) else None
    coverage = {
        "5m": cov, "warmup_bars_5m": warm,
        "15m": series_gaps([b.open_time for b in zone.bars], "15m", WINDOW_START, WINDOW_END),
        "1h": series_gaps([b.open_time for b in direction.bars], "1h", WINDOW_START, WINDOW_END),
        "first_5m_bar": iso(times5[0]) if times5 else None,
        "last_5m_bar_close": iso(data_end),
        "validate_series_5m": {"ok": q5.ok, "errors": q5.errors[:8], "gap_fraction": q5.gap_fraction},
    }
    if not setup.bars or not zone.bars or not direction.bars:
        raise RuntimeError(f"missing candles: 5m={len(setup)} 15m={len(zone)} 1h={len(direction)}")

    t0 = time.perf_counter()
    base_book = prepare_book(symbol, INTRADAY_TIER, setup, direction, zone, None)
    timing["prepare_book_seconds"] = round(time.perf_counter() - t0, 2)

    runner_gate = ("scripts/run_intraday_bos_sd.py admits BTC/ETH/SOL as LARGE_LIQUIDITY by name"
                   if symbol in {"BTCUSDT", "ETHUSDT", "SOLUSDT"} else
                   "scripts/run_intraday_bos_sd.py would EXCLUDE this symbol (classify_membership "
                   "with liquidity_rank=None -> rank_unavailable); run here as LARGE_LIQUIDITY "
                   "because tier is a caller argument of prepare_book")
    out_rows: list[dict[str, Any]] = []
    trade_files: dict[str, list[dict[str, Any]]] = {}
    for variant in INTRADAY_VARIANTS:
        t0 = time.perf_counter()
        # generate_signals marks zones touched, so each variant gets its own copy of the
        # prepared book (the repo runner calls prepare_book again per variant; same result).
        book = copy.copy(base_book)
        book.zones = [copy.copy(z) for z in base_book.zones]
        signals, rejected = generate_signals(book, variant)
        in_win = [s for s in signals
                  if WINDOW_START <= setup.bars[s.entry_index].open_time < WINDOW_END]
        cands = [{"signal": s, "setup": setup, "zones": book.zones, "execution": None,
                  "funding": None, "returns": None} for s in in_win]
        res = run_portfolio(cands, slippage=spec.BASELINE_SLIPPAGE_RATE, initial_equity=PRINCIPAL_USD)
        res0 = run_portfolio(cands, slippage=0.0, initial_equity=PRINCIPAL_USD)
        timing[f"variant_{variant}_seconds"] = round(time.perf_counter() - t0, 2)

        def split(result: Any) -> tuple[list[dict], list[dict]]:
            closed, open_ = [], []
            for f in result.trades:
                d = _intraday_trade(f, setup)
                deadline = f.entry_time + timedelta(hours=spec.MAX_HOLD_HOURS)
                forced = f.exit_reason == "max_hold" and f.exit_time < deadline
                d["forced_close_at_data_end"] = forced
                (open_ if forced else closed).append(d)
            return closed, open_

        closed, open_ = split(res)
        closed0, _open0 = split(res0)
        rej: dict[str, int] = {}
        for r in res.rejections:
            rej[r["reason"]] = rej.get(r["reason"], 0) + 1
        sid = f"{spec.STRATEGY_ID}.{variant}"
        common_note = (
            f"window data ends {iso(data_end)} (requested end {iso(WINDOW_END)}); "
            f"signals_total={len(signals)} in_window={len(in_win)} portfolio_rejections={rej}; "
            f"SIZING IS THE ENGINE'S, NOT THE REQUESTED ONE: risk = {spec.LARGE_RISK_PCT * 100:.2f}% of "
            f"current equity (${PRINCIPAL_USD * spec.LARGE_RISK_PCT:.2f} at start, compounding; requested $20), "
            f"leverage cap {spec.LEVERAGE_CAP}x (requested 2x), qty step {spec.ASSUMED_QTY_STEP}, "
            f"min notional {spec.ASSUMED_MIN_NOTIONAL} -- these are module constants, not parameters; "
            f"initial_equity=1000 passed (engine default {spec.INITIAL_EQUITY:.0f}); "
            f"taker fee = engine constant {spec.TAKER_FEE_RATE} (matches 0.04%); maker never applied; "
            f"slippage = engine default {spec.BASELINE_SLIPPAGE_RATE} per fill, adverse, on entry/stop/max-hold "
            f"exits (not on target); gross_pnl_usd is measured on slipped prices; "
            f"funding not applied (funding=None; the funding_rates table holds a ~4s rate stream from "
            f"2026-10-01, not settlements); 1m execution series not used (1m starts 2026-10-01; passing a "
            f"partial 1m series makes simulate_trade reject earlier trades as no_path) so exits walk 5m bars; "
            f"single-symbol account per cell (the repo runner shares one account across symbols); "
            f"tier={INTRADAY_TIER}: {runner_gate}; "
            f"same walk at slippage=0: trades={len(closed0)} net_usd={sum(t['net_pnl_usd'] for t in closed0):.4f} "
            f"avg_r_net={(sum(t['r_net'] for t in closed0) / len(closed0)) if closed0 else None}"
        )
        exec_model = ("signal on 5m close; market entry at NEXT 5m open with 0.05% adverse slippage; "
                      "stop/target checked on 5m bars from the entry bar, stop first when both hit; "
                      "stop fills at stop (or bar open on a gap) with 0.05% adverse slippage; target fills "
                      "at target with no slippage; 4h max hold exits at bar close with slippage; taker fee "
                      "0.04% of executed notional on both legs")
        for d in ("BOTH", "LONG", "SHORT"):
            sub = closed if d == "BOTH" else [t for t in closed if t["direction"] == d]
            sub_open = open_ if d == "BOTH" else [t for t in open_ if t["direction"] == d]
            m = metrics(sub)
            fname = f"{sid}__{d}__{symbol}__{INTRADAY_TF}.json"
            trade_files[fname] = sorted(sub, key=lambda t: t["entry_time"])
            note = common_note
            if d != "BOTH":
                note = (f"SUBSET of the BOTH walk ({d} trades only), not a separate direction-filtered "
                        f"backtest: the engine blocks a new entry while the symbol has an open position "
                        f"and sizes from shared equity; ") + note
            out_rows.append(make_row(
                family="intraday", engine="intraday_bos_sd", strategy_id=sid, direction=d,
                symbol=symbol, timeframe=INTRADAY_TF, status="OK", reason=None,
                window_start_utc=iso(WINDOW_START), window_end_utc=iso(WINDOW_END),
                eval_bars=cov["bars"], warmup_bars=warm, missing_bars=cov["missing"],
                open_trades_at_end=len(sub_open), execution_model=exec_model,
                principal_usd=PRINCIPAL_USD, risk_usd=PRINCIPAL_USD * spec.LARGE_RISK_PCT,
                leverage=spec.LEVERAGE_CAP, taker_fee_pct=spec.TAKER_FEE_RATE * 100.0,
                maker_fee_pct=MAKER_FEE_PCT, trades_file=f"trades/{fname}", notes=note, **m,
            ))
    return {"rows": out_rows, "trade_files": trade_files, "coverage": coverage, "timing": timing}


# ---------------------------- SCALPING ------------------------------------
def scalp_strategy_ids() -> list[dict[str, Any]]:
    from app.research.scalping.catalog import SCALPING_STRATEGIES

    return [{"strategy_id": f"{s.strategy_id}.{s.variant}", "engine_key": s.key,
             "specification_complete": s.specification_complete} for s in SCALPING_STRATEGIES]


async def _scalp_symbol(symbol: str) -> dict[str, Any]:
    from app.research.postgres_ohlcv import load_ohlcv_series_range
    from app.research.scalping.backtest import run_backtest
    from app.research.trade_fees import enrich_trade_execution

    timing: dict[str, float] = {}
    t0 = time.perf_counter()
    c1 = await load_ohlcv_series_range(symbol, "1m", start=None, end_exclusive=WINDOW_END)
    c5 = await load_ohlcv_series_range(symbol, "5m", start=SCALP_AUX_START, end_exclusive=WINDOW_END)
    c15 = await load_ohlcv_series_range(symbol, "15m", start=SCALP_AUX_START, end_exclusive=WINDOW_END)
    timing["load_seconds"] = round(time.perf_counter() - t0, 2)
    if not c1:
        raise RuntimeError("no 1m candles")
    t1 = [as_dt(c["time"]) for c in c1]
    win_lo, win_hi = t1[0], t1[-1] + timedelta(minutes=1)
    cov = series_gaps(t1, "1m", win_lo, win_hi)
    segs = 1 + sum(1 for a, b in zip(t1, t1[1:]) if (b - a) != timedelta(minutes=1))
    coverage = {"1m": cov, "1m_segments": segs, "5m_bars": len(c5), "15m_bars": len(c15),
                "aux_start": iso(SCALP_AUX_START)}
    t0 = time.perf_counter()
    out = run_backtest(symbol=symbol, candles_1m=c1, candles_5m=c5, candles_15m=c15)
    timing["run_backtest_seconds"] = round(time.perf_counter() - t0, 2)

    exec_model = ("signal on 1m close; entry at NEXT 1m open, no slippage; stop/target checked on 1m "
                  "bars from the entry bar, stop first when both hit (CONSERVATIVE); stop and 1.5R target "
                  "fill exactly at their level; time exit at the close of the last allowed 1m bar; one "
                  "open trade per strategy key; the ENGINE applies no fees and no sizing")
    rows_out: list[dict[str, Any]] = []
    trade_files: dict[str, list[dict[str, Any]]] = {}
    for reg in scalp_strategy_ids():
        sid = reg["strategy_id"]
        if not reg["specification_complete"]:
            rows_out.append(make_row(
                family="scalp", engine="scalping", strategy_id=sid, direction="BOTH", symbol=symbol,
                timeframe=SCALP_TF, status="NOT_RUN",
                reason="catalog registration has specification_complete=False; the engine has no "
                       "evaluator for this id",
                window_start_utc=iso(win_lo), window_end_utc=iso(win_hi),
                principal_usd=PRINCIPAL_USD, risk_usd=RISK_USD, leverage=LEVERAGE,
                taker_fee_pct=TAKER_FEE_PCT, maker_fee_pct=MAKER_FEE_PCT,
                notes="SUPPLEMENTAL short 1m window, not the fixed evaluation window",
            ))
            continue
        block = (out.get("strategies") or {}).get(reg["engine_key"]) or {"trades": [], "metrics": None}
        closed, open_ = [], []
        for t in block["trades"]:
            if t.get("exit_index") is None or t.get("exit_price") is None or t.get("outcome") == "OPEN":
                open_.append(t)
                continue
            e = enrich_trade_execution(
                t, risk_usd=RISK_USD, taker_fee=TAKER_FEE_PCT / 100.0, maker_fee=MAKER_FEE_PCT / 100.0,
                leverage=LEVERAGE, account_equity=PRINCIPAL_USD,
            )
            hold = (as_dt(t["exit_time"]) - as_dt(t["entry_time"])).total_seconds() / 3600.0
            closed.append({
                "symbol": symbol, "strategy_id": sid, "direction": t["direction"],
                "signal_time": t["signal_time"], "entry_index": t["entry_index"],
                "entry_time": t["entry_time"], "entry_price": t["entry_price"],
                "stop_price": t["stop_price"], "target_price": t["target_price"],
                "exit_index": t["exit_index"], "exit_time": t["exit_time"],
                "exit_price": t["exit_price"], "outcome": t["outcome"],
                "ambiguous_intrabar": t["ambiguous_intrabar"],
                "max_holding_minutes": t["max_holding_minutes"],
                "r_gross_engine": t["r_multiple"],
                "quantity": e["qty"], "notional_entry_usd": e["notional_entry_usd"],
                "notional_exit_usd": e["notional_exit_usd"],
                "capped_by_leverage": e["capped_by_leverage"],
                "risk_usd_actual": e["risk_usd"], "requested_risk_usd": e["requested_risk_usd"],
                "gross_pnl_usd": e["gross_pnl_usd"], "fee_entry_usd": e["fee_entry_usd"],
                "fee_exit_usd": e["fee_exit_usd"], "fees_usd": e["fee_total_usd"],
                "net_pnl_usd": e["net_pnl_usd"], "r_net": e["r_net"], "hold_hours": hold,
            })
        em = block.get("metrics") or {}
        capped = sum(1 for t in closed if t["capped_by_leverage"])
        base_note = (
            "SUPPLEMENTAL short 1m window, not the fixed evaluation window (1m history starts "
            f"2026-10-01 and is gappy: {cov['missing']} of {cov['expected']} 1m bars missing, {segs} "
            "contiguous segments; the engine restarts ATR/RVOL/swings at every gap and counts max-hold "
            "in bars, so a trade can span a gap); warmup: the engine skips the first 20 1m bars of the "
            f"series and of each segment's RVOL; 5m/15m loaded from {iso(SCALP_AUX_START)}; "
            "USD, fees and r_net are NOT engine output: app.research.trade_fees.enrich_trade_execution "
            "with risk_usd=20, account_equity=1000 (fixed, no compounding), leverage=2 (entry notional "
            f"capped at $2000; {capped} of {len(closed)} trades capped, their actual risk is below $20), "
            "taker 0.04% on both legs, slippage 0; engine raw (gross, fee-free) metrics: "
            f"trades={em.get('trades')} win_rate={em.get('win_rate')} expectancy_r={em.get('expectancy_r')} "
            f"net_r={em.get('net_r')} ambiguous_intrabar={em.get('ambiguous_intrabar')}"
        )
        for d in ("BOTH", "LONG", "SHORT"):
            sub = closed if d == "BOTH" else [t for t in closed if t["direction"] == d]
            sub_open = open_ if d == "BOTH" else [t for t in open_ if t["direction"] == d]
            fname = f"{sid}__{d}__{symbol}__{SCALP_TF}.json"
            trade_files[fname] = sub
            note = base_note
            if d != "BOTH":
                note = (f"SUBSET of the BOTH walk ({d} trades only): one_open_per_strategy blocks "
                        "entries across both directions; ") + note
            rows_out.append(make_row(
                family="scalp", engine="scalping", strategy_id=sid, direction=d, symbol=symbol,
                timeframe=SCALP_TF, status="OK", reason=None,
                window_start_utc=iso(win_lo), window_end_utc=iso(win_hi),
                eval_bars=cov["bars"], warmup_bars=20, missing_bars=cov["missing"],
                open_trades_at_end=len(sub_open), execution_model=exec_model,
                principal_usd=PRINCIPAL_USD, risk_usd=RISK_USD, leverage=LEVERAGE,
                taker_fee_pct=TAKER_FEE_PCT, maker_fee_pct=MAKER_FEE_PCT,
                trades_file=f"trades_supplemental_1m/{fname}", notes=note, **metrics(sub),
            ))
    return {"rows": rows_out, "trade_files": trade_files, "coverage": coverage, "timing": timing}


def run_task(kind: str, symbol: str) -> dict[str, Any]:
    path = CELLS / f"{kind}__{symbol}.json"
    t0 = time.perf_counter()
    started = datetime.now(timezone.utc).isoformat()
    try:
        fn = _intraday_symbol if kind == "intraday" else _scalp_symbol
        res = _W["loop"].run_until_complete(fn(symbol))
        tdir = TRADES if kind == "intraday" else TRADES_SUPP
        for fname, trades in res.pop("trade_files").items():
            atomic_write(tdir / fname, trades)
        payload = {"complete": True, "status": "OK", "kind": kind, "symbol": symbol, **res}
    except Exception as exc:  # noqa: BLE001
        payload = {"complete": True, "status": "ERROR", "kind": kind, "symbol": symbol,
                   "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-3000:]}
    payload.update(started_utc=started, seconds=round(time.perf_counter() - t0, 2),
                   db_session=dict(_W["ro"]), sql_audit=dict(_W["sql_audit"]), pid=os.getpid())
    atomic_write(path, payload)
    return {"kind": kind, "symbol": symbol, "status": payload["status"], "seconds": payload["seconds"],
            "error": payload.get("error")}


# --------------------------------------------------------------------------
# parent side
# --------------------------------------------------------------------------
def strategies_doc() -> list[dict[str, Any]]:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from app.research.intraday_bos_sd import spec

    docs = []
    ab = spec.spec_document()["ablations"]
    for v in INTRADAY_VARIANTS:
        docs.append({
            "id": f"{spec.STRATEGY_ID}.{v}", "engine": "intraday_bos_sd", "family": "intraday",
            "engine_native_id": f"{spec.STRATEGY_ID} ablation variant {v}", "rule": ab[v],
            "directions": ["LONG", "SHORT"],
            "direction_filter": "none in the engine; one walk trades both, rows are BOTH + subsets",
            "supported_timeframes": ["5m"],
            "timeframe_note": "fixed in spec.py: setup 5m, zones 15m, direction 1h, optional 1m "
                              "execution. 15m/1h/4h/1d are not selectable setup timeframes.",
            "required_auxiliary_data": ["15m candles (zones)", "1h candles (direction)",
                                        "optional 1m candles (execution path; not used here)",
                                        "optional funding settlements (not used here)"],
            "spec_hash": spec.spec_hash(), "strategy_version": spec.STRATEGY_VERSION,
        })
    for reg in scalp_strategy_ids():
        docs.append({
            "id": reg["strategy_id"], "engine": "scalping", "family": "scalp",
            "engine_native_id": reg["engine_key"],
            "specification_complete": reg["specification_complete"],
            "directions": ["LONG", "SHORT"] if reg["specification_complete"] else [],
            "direction_filter": "none in the engine; one walk trades both, rows are BOTH + subsets",
            "supported_timeframes": ["1m"],
            "timeframe_note": "signals, entries and exits are all on 1m candles. None of "
                              "5m/15m/1h/4h/1d is a selectable timeframe. REQUIRES 1m.",
            "required_auxiliary_data": ["1m candles (signal + execution)", "15m candles (trend)"]
                                       + (["5m candles (zones)"] if "sweep" in reg["strategy_id"] else []),
        })
    return docs


def scalp_main_window_rows() -> list[dict[str, Any]]:
    rows = []
    for reg in scalp_strategy_ids():
        for sym in SYMBOLS:
            if reg["specification_complete"]:
                reason = ("engine requires 1m candles for signals and fills; 1m history starts "
                          "2026-10-01, so the 2026-02-14..2026-10-12 window cannot be run. No 5m/15m/"
                          "1h/4h/1d mode exists in this engine. See rows_supplemental_1m.json for a "
                          "short-window run on the available 1m history.")
            else:
                reason = ("catalog registration has specification_complete=False; the engine has no "
                          "evaluator for this id (and it would also require 1m)")
            rows.append(make_row(
                family="scalp", engine="scalping", strategy_id=reg["strategy_id"], direction="BOTH",
                symbol=sym, timeframe=SCALP_TF, status="NOT_RUN", reason=reason,
                window_start_utc=iso(WINDOW_START), window_end_utc=iso(WINDOW_END),
                principal_usd=PRINCIPAL_USD, risk_usd=RISK_USD, leverage=LEVERAGE,
                taker_fee_pct=TAKER_FEE_PCT, maker_fee_pct=MAKER_FEE_PCT,
                execution_model="not run", notes="timeframe 1m is the engine's only timeframe",
            ))
    return rows


def assemble(meta_extra: dict[str, Any]) -> dict[str, Any]:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from app.research.intraday_bos_sd import spec

    rows: list[dict[str, Any]] = []
    supp: list[dict[str, Any]] = []
    ro_proof, audit, cells_info = [], {}, []
    for sym in SYMBOLS:
        for kind, target in (("intraday", rows), ("scalp", supp)):
            p = CELLS / f"{kind}__{sym}.json"
            if not cell_done(p):
                status, reason = "NOT_RUN", "task did not run (no cell file)"
                cell = None
            else:
                cell = json.loads(p.read_text(encoding="utf-8"))
                status, reason = cell["status"], cell.get("error")
                ro_proof.append(cell.get("db_session"))
                for k, v in (cell.get("sql_audit") or {}).items():
                    audit[k] = max(audit.get(k, 0), v)
                cells_info.append({"kind": kind, "symbol": sym, "status": status,
                                   "seconds": cell.get("seconds"), "timing": cell.get("timing"),
                                   "coverage": cell.get("coverage")})
            if cell and status == "OK":
                target.extend(cell["rows"])
                continue
            ids = ([f"{spec.STRATEGY_ID}.{v}" for v in INTRADAY_VARIANTS] if kind == "intraday"
                   else [r["strategy_id"] for r in scalp_strategy_ids()])
            for sid in ids:
                target.append(make_row(
                    family="intraday" if kind == "intraday" else "scalp",
                    engine="intraday_bos_sd" if kind == "intraday" else "scalping",
                    strategy_id=sid, direction="BOTH", symbol=sym,
                    timeframe=INTRADAY_TF if kind == "intraday" else SCALP_TF,
                    status=status, reason=reason,
                    window_start_utc=iso(WINDOW_START) if kind == "intraday" else None,
                    window_end_utc=iso(WINDOW_END) if kind == "intraday" else None,
                    principal_usd=PRINCIPAL_USD, risk_usd=RISK_USD, leverage=LEVERAGE,
                    taker_fee_pct=TAKER_FEE_PCT, maker_fee_pct=MAKER_FEE_PCT,
                ))
    rows.extend(scalp_main_window_rows())
    atomic_write(OUT / "rows.json", rows)
    atomic_write(OUT / "rows_supplemental_1m.json", supp)
    atomic_write(OUT / "_strategies.json", strategies_doc())

    def git(*a: str) -> str:
        return subprocess.run(["git", *a], cwd=str(REPO), capture_output=True, text=True).stdout

    counts: dict[str, int] = {}
    for r in rows:
        k = f"{r['engine']}|{r['timeframe']}|{r['status']}"
        counts[k] = counts.get(k, 0) + 1
    counts_supp: dict[str, int] = {}
    for r in supp:
        k = f"{r['engine']}|{r['timeframe']}|{r['status']}"
        counts_supp[k] = counts_supp.get(k, 0) + 1
    meta = {
        "git_head": git("rev-parse", "HEAD").strip(),
        "git_status_porcelain_lines": len([l for l in git("status", "--porcelain").splitlines() if l.strip()]),
        "runner": str(Path(__file__).resolve()),
        "runner_sha1": hashlib.sha1(Path(__file__).read_bytes()).hexdigest(),
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "read_only_proof": {
            "connect_arg": "server_settings.default_transaction_read_only=on",
            "probed_per_worker": [p for i, p in enumerate(ro_proof) if p and p not in ro_proof[:i]],
            "sql_audit_statement_heads_max_per_worker": audit,
            "non_select_statements_blocked_by_listener": True,
        },
        "window": {"start": iso(WINDOW_START), "end": iso(WINDOW_END), "load_start": iso(LOAD_START)},
        "requested": {"principal_usd": PRINCIPAL_USD, "risk_usd": RISK_USD, "leverage": LEVERAGE,
                      "taker_fee_pct": TAKER_FEE_PCT, "maker_fee_pct": MAKER_FEE_PCT},
        "engine_defaults_vs_used": {
            "intraday_bos_sd": {
                "initial_equity": {"engine_default": spec.INITIAL_EQUITY, "used": PRINCIPAL_USD,
                                   "how": "run_portfolio(initial_equity=...) parameter"},
                "risk_per_trade": {"engine": f"{spec.LARGE_RISK_PCT} of current equity (LARGE tier)",
                                   "requested_usd": RISK_USD, "used": "engine constant (not a parameter)"},
                "leverage_cap": {"engine": spec.LEVERAGE_CAP, "requested": LEVERAGE,
                                 "used": spec.LEVERAGE_CAP, "why": "module constant, not a parameter"},
                "taker_fee_rate": {"engine": spec.TAKER_FEE_RATE, "requested": TAKER_FEE_PCT / 100.0,
                                   "used": spec.TAKER_FEE_RATE},
                "maker_fee_rate": "not used by the engine (all fills taker)",
                "slippage_rate": {"engine_default": spec.BASELINE_SLIPPAGE_RATE,
                                  "used": spec.BASELINE_SLIPPAGE_RATE,
                                  "also": "slippage=0 result recorded in each row's notes"},
                "funding": "engine applies it only when rows are passed; passed None",
                "execution_1m": "engine walks 1m when passed; passed None, exits walk 5m",
            },
            "scalping": {
                "fees": {"engine": "none (fees_included=False)", "used_in_supplemental":
                         "trade_fees.enrich_trade_execution taker 0.0004 both legs"},
                "sizing": {"engine": "none (R multiples only)", "used_in_supplemental":
                           "risk_usd=20, account_equity=1000, leverage=2 notional cap"},
                "slippage": {"engine": "none", "used": 0.0},
            },
        },
        "row_counts_rows_json": counts,
        "row_counts_rows_supplemental_1m_json": counts_supp,
        "cells": cells_info,
        **meta_extra,
    }
    prev = OUT / "_meta.json"
    if "started_utc" not in meta and prev.exists():
        try:
            meta["started_utc"] = json.loads(prev.read_text(encoding="utf-8")).get("started_utc")
        except Exception:  # noqa: BLE001
            pass
    atomic_write(prev, meta)
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--only", choices=["intraday", "scalp"], default=None)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--assemble-only", action="store_true")
    args = ap.parse_args()
    workers = max(1, min(int(args.workers), 3))
    started = datetime.now(timezone.utc).isoformat()
    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or SYMBOLS
    OUT.mkdir(parents=True, exist_ok=True)
    extra: dict[str, Any] = {}
    if not args.assemble_only:
        tasks = [(k, s) for k in ("intraday", "scalp") if args.only in (None, k) for s in syms]
        todo = [t for t in tasks if args.force or not cell_done(CELLS / f"{t[0]}__{t[1]}.json")]
        print(f"tasks={len(tasks)} todo={len(todo)} workers={workers}", flush=True)
        if todo:
            with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init) as ex:
                futs = {ex.submit(run_task, k, s): (k, s) for k, s in todo}
                for f in as_completed(futs):
                    try:
                        print(json.dumps(f.result()), flush=True)
                    except Exception as exc:  # noqa: BLE001
                        print(f"TASK CRASH {futs[f]}: {exc!r}", flush=True)
        extra["started_utc"] = started
    meta = assemble(extra)
    print(json.dumps({"rows": meta["row_counts_rows_json"],
                      "supplemental": meta["row_counts_rows_supplemental_1m_json"]}, indent=1))


if __name__ == "__main__":
    main()
