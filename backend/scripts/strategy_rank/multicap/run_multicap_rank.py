"""Strategy-rank runner: multi-cap strategies + orphan backtestable strategies.

Read-only. Calls the repo's pure strategy / trade-evaluation functions and the
repo fee function (app.research.trade_fees). Does not touch backend/app, does
not write to the DB or backend/reports. Writes only next to this file.

Usage (from D:\\cryptoscreener\\backend):
    .venv\\Scripts\\python.exe -B scripts\\strategy_rank\\multicap\\run_multicap_rank.py [--time] [--only FAMILY] [--workers 3]

Resumable: one result file per job under cells/, written atomically.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(HERE))

import _db  # noqa: E402

# Window selection: env RANK_WINDOW_ID (inherited by worker processes).
#   IS (default)  -> original in-sample window, original output paths (untouched layout)
#   ISCHECK       -> in-sample dates, written to oos/_check (reproduction test)
#   OOS1 / OOS2   -> earlier windows, written to oos/
WID = os.environ.get("RANK_WINDOW_ID", "IS").upper()
_WINDOWS = {
    "IS": ((2026, 2, 14), (2026, 10, 12)),
    "ISCHECK": ((2026, 2, 14), (2026, 10, 12)),
    "OOS1": ((2025, 6, 19), (2026, 2, 14)),
    "OOS2": ((2024, 10, 23), (2025, 6, 19)),
}
if WID not in _WINDOWS:
    raise SystemExit(f"unknown RANK_WINDOW_ID {WID}")
OUT = HERE if WID == "IS" else (HERE / "oos" / "_check" if WID == "ISCHECK" else HERE / "oos")
CELLS = OUT / "cells"
TRADES = OUT / "trades"
SUFFIX = "" if WID == "IS" else f"__{WID}"
REQUESTED_WARMUP = {"multicap": 1000, "candle12": 1000, "ose": 500, "spr": 4320, "bsc": 3000}

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "TRXUSDT", "AVAXUSDT", "LINKUSDT",
]
WINDOW_START = datetime(*_WINDOWS[WID][0], tzinfo=timezone.utc)
WINDOW_END = datetime(*_WINDOWS[WID][1], tzinfo=timezone.utc)
PRINCIPAL = 1000.0
RISK_USD = 20.0
LEVERAGE = 2.0
TAKER = 0.0004
MAKER = 0.0002
TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}

ROW_KEYS = [
    "family", "engine", "strategy_id", "direction", "symbol", "timeframe", "status",
    "reason", "window_start_utc", "window_end_utc", "eval_bars", "warmup_bars",
    "missing_bars", "trades_closed", "wins", "losses", "win_rate", "gross_pnl_usd",
    "fees_usd", "net_pnl_usd", "return_pct", "profit_factor", "avg_r_net",
    "max_drawdown_usd", "max_drawdown_r", "median_hold_hours", "avg_hold_hours",
    "open_trades_at_end", "execution_model", "principal_usd", "risk_usd", "leverage",
    "taker_fee_pct", "maker_fee_pct", "trades_file", "notes",
]


# --------------------------------------------------------------------------- util
def _atomic_write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        ts = value
    else:
        try:
            ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _load(symbol: str, tf: str, warmup_bars: int, start: datetime = WINDOW_START):
    """Return (candles, first_eval_index, eval_bars, warmup_bars, missing_bars)."""
    lo = start - timedelta(minutes=TF_MIN[tf] * warmup_bars)
    candles = _db.load_candles(symbol, tf, lo, WINDOW_END)
    first = next((i for i, c in enumerate(candles) if c["time"] >= start), len(candles))
    eval_bars = len(candles) - first
    expected = int((WINDOW_END - start).total_seconds() // (TF_MIN[tf] * 60))
    return candles, first, eval_bars, first, max(0, expected - eval_bars)


def base_row(**kw: Any) -> dict[str, Any]:
    row = {k: None for k in ROW_KEYS}
    row.update(
        window_start_utc=WINDOW_START.isoformat(),
        window_end_utc=WINDOW_END.isoformat(),
        principal_usd=PRINCIPAL, risk_usd=RISK_USD, leverage=LEVERAGE,
        taker_fee_pct=TAKER * 100.0, maker_fee_pct=MAKER * 100.0,
        status="OK", reason="", notes="",
    )
    if WID != "IS":
        row["window_id"] = WID
    row.update(kw)
    return row


def not_run(reason: str, status: str = "NOT_RUN", **kw: Any) -> dict[str, Any]:
    return base_row(status=status, reason=reason, **kw)


def enrich(trade_dicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repo fee/sizing model, fixed study parameters. Constant-equity leverage cap."""
    from app.research.trade_fees import enrich_trades

    return enrich_trades(
        trade_dicts, risk_usd=RISK_USD, taker_fee=TAKER, maker_fee=MAKER,
        leverage=LEVERAGE, account_equity=PRINCIPAL,
    )


def finish_row(row: dict[str, Any], enriched: list[dict[str, Any]], open_count: int) -> dict[str, Any]:
    """Fill metrics from enriched closed trades (entry_time / exit_time keys required)."""
    rows = [t for t in enriched if t.get("net_pnl_usd") is not None]
    rows.sort(key=lambda t: (str(t.get("exit_time") or ""), str(t.get("entry_time") or "")))
    net = [float(t["net_pnl_usd"]) for t in rows]
    gross = [float(t["gross_pnl_usd"]) for t in rows]
    fees = [float(t["fee_total_usd"]) for t in rows]
    r_net = [float(t["r_net"]) for t in rows if t.get("r_net") is not None]
    holds = []
    for t in rows:
        a, b = _ts(t.get("entry_time")), _ts(t.get("exit_time"))
        if a and b:
            holds.append((b - a).total_seconds() / 3600.0)

    def dd(seq: list[float]) -> float | None:
        if not seq:
            return None
        cum = peak = worst = 0.0
        for p in seq:
            cum += p
            peak = max(peak, cum)
            worst = max(worst, peak - cum)
        return worst

    wins = sum(1 for p in net if p > 0)
    losses = sum(1 for p in net if p < 0)
    win_usd = sum(p for p in net if p > 0)
    loss_usd = -sum(p for p in net if p < 0)
    n = len(rows)
    row.update(
        trades_closed=n, wins=wins, losses=losses,
        win_rate=(wins / n) if n else None,
        gross_pnl_usd=sum(gross) if n else 0.0,
        fees_usd=sum(fees) if n else 0.0,
        net_pnl_usd=sum(net) if n else 0.0,
        return_pct=(sum(net) / PRINCIPAL * 100.0) if n else 0.0,
        profit_factor=(win_usd / loss_usd) if loss_usd > 0 else None,
        avg_r_net=(sum(r_net) / len(r_net)) if r_net else None,
        max_drawdown_usd=dd(net),
        max_drawdown_r=dd([float(t["r_net"]) for t in rows if t.get("r_net") is not None]),
        median_hold_hours=statistics.median(holds) if holds else None,
        avg_hold_hours=(sum(holds) / len(holds)) if holds else None,
        open_trades_at_end=open_count,
    )
    name = f"{row['strategy_id']}__{row['direction']}__{row['symbol']}__{row['timeframe']}{SUFFIX}.json"
    name = name.replace(":", "_").replace("/", "_")
    _atomic_write(TRADES / name, rows)
    row["trades_file"] = f"trades/{name}"
    return row


def market_caps() -> dict[str, float | None]:
    """Latest persisted market-cap observation at/before the window end (read-only)."""
    async def go() -> dict[str, float | None]:
        conn = await _db._connect()
        try:
            rows = await conn.fetch(
                "SELECT DISTINCT ON (symbol) symbol, market_cap, effective_time "
                "FROM research_market_cap_observations "
                "WHERE symbol = ANY($1) AND effective_time <= $2 "
                "ORDER BY symbol, effective_time DESC",
                SYMBOLS, WINDOW_END,
            )
        finally:
            await conn.close()
        return {r[0]: (float(r[1]) if r[1] is not None else None) for r in rows}
    try:
        return asyncio.run(go())
    except Exception:  # noqa: BLE001
        return {}


# ------------------------------------------------------------------- adapters
def job_multicap(symbol: str, tf: str) -> list[dict[str, Any]]:
    from app.research.combination_backtest import evaluate_candidate_trades
    from app.research.multi_cap_strategies import (
        large_cap_sweep_choch, mid_cap_fvg_discount, small_cap_volume_bos,
    )
    from app.research.multi_cap_strategies.adapter import MultiCapStrategyAdapter
    from app.research.multi_cap_strategies.cap_filter import symbol_eligible_for_strategy
    from app.research.multi_cap_strategies.config import MultiCapResearchConfig

    cfg = MultiCapResearchConfig()  # engine defaults untouched (signal + SL/TP params)
    adapter = MultiCapStrategyAdapter(cfg)
    caps = market_caps()
    mcap = caps.get(symbol)
    candles, first, eval_bars, warm, missing = _load(symbol, tf, 1000)
    out: list[dict[str, Any]] = []
    for mod in (large_cap_sweep_choch, mid_cap_fvg_discount, small_cap_volume_bos):
        d = mod.DEFINITION
        t0 = time.perf_counter()
        ok, label, why = symbol_eligible_for_strategy(symbol, mcap, d.asset_group, config=cfg)
        tier = (
            f"cap tier: designed for {d.asset_group}; repo cap_filter on latest persisted "
            f"market cap ({mcap}) resolves '{label}' -> {why} "
            f"({'INSIDE' if ok else 'OUTSIDE'} designed tier). "
        )
        row = base_row(
            family="multicap",
            engine="multi_cap_strategies.<strategy>.generate_candidates + "
                   "combination_backtest.evaluate_candidate_trades + trade_fees.enrich_trades",
            strategy_id=d.strategy_id, direction=d.direction, symbol=symbol, timeframe=tf,
            eval_bars=eval_bars, warmup_bars=warm, missing_bars=missing,
            execution_model=(
                "MARKET entry at signal-bar close; exits from next bar; ATR(14)x1.5 or "
                "structural stop (farther), TP1 at 2R (first target touched exits full size); "
                "same-bar SL+TP -> SL (CONSERVATIVE); one open trade at a time; taker both "
                "sides on executed notional; no slippage in USD"
            ),
        )
        try:
            if tf not in d.timeframes:
                out.append({**row, "status": "NOT_RUN",
                            "reason": f"timeframe {tf} not in strategy definition {d.timeframes}"})
                continue
            cands = mod.generate_candidates(symbol, tf, candles, config=cfg, index_start=first)
            trades = adapter.candidates_to_trades(cands, asset_group=label, period_label="FULL")
            evaluated = evaluate_candidate_trades(
                candles, trades, handling=cfg.ambiguous_handling,
                one_open_at_a_time=cfg.one_open_at_a_time,
            )
            closed = [t.to_dict() for t in evaluated if t.outcome and t.outcome != "OPEN"]
            opened = sum(1 for t in evaluated if t.outcome == "OPEN")
            en = enrich(closed)
            for t in en:
                t["entry_time"] = t.get("signal_time")
            row["notes"] = (
                tier
                + "Cap filter lives in MultiCapStrategyAdapter.generate_candidates (universe "
                  "selection), not in the strategy module; the pure module-level "
                  "generate_candidates was called directly, no gate inside strategy logic "
                  "disabled. service.py/repository.py (persisting) not used. "
                + f"signals={len(cands)} sized={len(trades)} evaluated={len(evaluated)}. "
                + "Engine defaults that differ from the study: risk_usd=100 (implied equity "
                  "5000), slippage_rate=0.0002 (engine applies it only to an auxiliary "
                  "r_net_with_slippage field, never to USD); study uses risk 20 / equity 1000 "
                  "/ leverage 2 via enrich_trades(account_equity=1000, leverage=2). "
                  "avg_r_net is on realized (post leverage-cap) risk. "
                + f"cell_seconds={time.perf_counter() - t0:.2f}"
            )
            out.append(finish_row(row, en, opened))
        except Exception as exc:  # noqa: BLE001
            out.append({**row, "status": "ERROR",
                        "reason": f"{type(exc).__name__}: {exc}",
                        "notes": traceback.format_exc()[-600:]})
    return out


def job_spr(symbol: str, tf: str) -> list[dict[str, Any]]:
    from app.research.short_pullback_rejection.constants import (
        DEFAULT_THRESHOLDS, STOP_BUFFER_025, STRATEGY_ID, TP_NEAREST_SUPPORT,
    )
    from app.research.short_pullback_rejection.signals import generate_signals_for_symbol
    from app.research.short_pullback_rejection.simulation import simulate_signals

    t0 = time.perf_counter()
    warm_1h = 24 * 180
    c1, first, eval_bars, warm, missing = _load(symbol, "1h", warm_1h)
    c4 = _db.load_candles(symbol, "4h", WINDOW_START - timedelta(days=180), WINDOW_END)
    th = {**DEFAULT_THRESHOLDS, "risk_usd": RISK_USD, "principal_usd": PRINCIPAL}
    hold = int(th["max_hold_bars"])
    row = base_row(
        family="short_pullback_rejection",
        engine="short_pullback_rejection.signals.generate_signals_for_symbol + "
               "simulation.simulate_signals (calls trade_fees.enrich_trade_execution)",
        strategy_id=STRATEGY_ID, direction="SHORT", symbol=symbol, timeframe="1h",
        eval_bars=eval_bars, warmup_bars=warm, missing_bars=missing,
        execution_model=(
            "MARKET SHORT at rejection-bar close; managed from next 1h bar; stop above "
            "rejection high + 0.25 ATR, TP nearest confirmed support; same-bar SL+TP -> SL; "
            "time stop at 48 bars (close); trades simulated independently (overlap allowed); "
            "taker both sides; no slippage"
        ),
    )
    sigs = generate_signals_for_symbol(
        symbol, c1, c4, thresholds=th, stop_buffer_atr=0.25, tp_mode="nearest_support", tp_r=None,
    )
    for s in sigs:
        s["stop_variant"], s["tp_variant"] = STOP_BUFFER_025, TP_NEAREST_SUPPORT
    trades = simulate_signals(sigs, {symbol: c1}, risk_usd=RISK_USD)
    last = len(c1) - 1
    closed, opened = [], 0
    for t in trades:
        ei = int(t["entry_index"])
        if ei < first:
            continue
        # Engine time-stops at the last loaded bar; a hold cut short by the window end is
        # an open trade, not a closed one.
        if t.get("outcome") == "TIME" and t.get("exit_index") == last and ei + hold > last:
            opened += 1
            continue
        t = dict(t)
        t["entry_time"] = c1[ei]["time"].isoformat()
        t.pop("r_net", None)
        closed.append(t)
    en = []
    from app.research.trade_fees import enrich_trade_execution
    for t in closed:
        e = enrich_trade_execution(
            t, risk_usd=RISK_USD, taker_fee=TAKER, maker_fee=MAKER,
            leverage=LEVERAGE, account_equity=PRINCIPAL,
        )
        # must agree with the package's own enrichment (same function, defaults)
        if abs(float(e["net_pnl_usd"]) - float(t["net_pnl_usd"])) > 1e-9:
            raise RuntimeError("enrichment mismatch vs package simulation")
        en.append(e)
    row["notes"] = (
        "Primary variant only (stop_buffer_0_25 / nearest_support = package runner "
        "defaults); the 3 stop x 4 TP grid is a sensitivity study and was not run. "
        "HTF regime from 4h candles. Package runner (writes backend/reports) not used; "
        f"pure signal+simulation functions called. signals_total={len(sigs)} "
        f"(incl. warm-up), entries_in_window={len(closed) + opened}. Trades whose 48-bar "
        "hold was truncated by the window end are counted as open, not closed. USD from the "
        "package's own enrich_trade_execution call (re-computed here with explicit "
        "equity 1000 / leverage 2 and checked equal). Engine default risk_usd=20 matches. "
        f"cell_seconds={time.perf_counter() - t0:.2f}"
    )
    return [finish_row(row, en, opened)]


def job_bsc(symbol: str, tf: str) -> list[dict[str, Any]]:
    from app.research.bos_strategy_comparison.runner import run_multi_strategy_backtest
    from app.research.bos_strategy_comparison.strategies import STRATEGIES
    from app.research.trade_fees import enrich_trade_execution

    t0 = time.perf_counter()
    candles, first, eval_bars, warm, missing = _load(symbol, "15m", 3000)
    c4 = _db.load_candles(symbol, "4h", WINDOW_START - timedelta(days=180), WINDOW_END)
    c1 = _db.load_candles(symbol, "1h", WINDOW_START - timedelta(days=120), WINDOW_END)
    c5 = _db.load_candles(symbol, "5m", WINDOW_START - timedelta(days=31), WINDOW_END)
    res = run_multi_strategy_backtest(
        symbol, "15m", candles, list(STRATEGIES.keys()),
        candles_4h=c4, candles_1h=c1, candles_5m=c5, index_start=first,
    )
    out: list[dict[str, Any]] = []
    elapsed = time.perf_counter() - t0
    for sid in STRATEGIES:
        payload = (res.get("strategies") or {}).get(sid) or {}
        for direction in ("LONG", "SHORT"):
            row = base_row(
                family="bos_strategy_comparison",
                engine="bos_strategy_comparison.runner.run_multi_strategy_backtest "
                       "(production SignalEngine entry/SL/TP) + trade_fees.enrich_trade_execution",
                strategy_id=sid, direction=direction, symbol=symbol, timeframe="15m",
                eval_bars=eval_bars, warmup_bars=warm, missing_bars=missing,
                execution_model=(
                    "15m setup TF with 4h/1h HTF alignment and 5m context; entry/SL/TP from "
                    "production signal engines at the qualifying bar; exits from next bar; "
                    "same-bar SL+TP -> SL (CONSERVATIVE); one open trade per strategy across "
                    "both directions; fee by entry_type (MARKET taker / LIMIT_RETEST maker), "
                    "taker exit; no slippage in USD"
                ),
            )
            status = str(payload.get("status") or res.get("status") or "ERROR")
            if status != "OK":
                out.append({**row, "status": "ERROR" if status == "ERROR" else "NOT_RUN",
                            "reason": f"engine status {status}: {res.get('reason') or ''}"})
                continue
            tr = [t for t in payload.get("trades") or [] if t.get("direction") == direction]
            closed = [t for t in tr if t.get("exit_reason") not in (None, "OPEN") and t.get("exit_price") is not None]
            opened = len(tr) - len(closed)
            en = []
            for t in closed:
                e = enrich_trade_execution(
                    {**t, "stop_price": t["sl"], "outcome": t["exit_reason"],
                     "r_multiple": t.get("gross_R")},
                    risk_usd=RISK_USD, taker_fee=TAKER, maker_fee=MAKER,
                    leverage=LEVERAGE, account_equity=PRINCIPAL,
                )
                e.pop("condition_snapshot", None)
                en.append(e)
            row["notes"] = (
                "Engine reports R only (net_R at notional risk_usd=100, plus 0.0002 slippage "
                "as an R adjustment); USD here from trade_fees.enrich_trade_execution with "
                "risk 20 / equity 1000 / leverage 2, engine slippage adjustment not carried "
                "into USD. Rows are split by direction from one both-direction run, so the "
                "one-open-at-a-time lock is shared between the LONG and SHORT rows. "
                f"kind={(payload.get('strategy') or {}).get('kind')}. "
                f"job_seconds(all 7 strategies)={elapsed:.1f}"
            )
            out.append(finish_row(row, en, opened))
    return out


def job_candle12(symbol: str, tf: str) -> list[dict[str, Any]]:
    from app.research import candle12_v2 as c12
    from app.research.trade_fees import enrich_trade_execution

    t0 = time.perf_counter()
    candles, first, eval_bars, warm, missing = _load(symbol, tf, 1000)
    res = c12.run_candle12_v2(symbol, tf, candles, index_start=first, progress_every=0)
    v2 = c12.Candle12V2Config(**{
        k: res["v2_config"][k] for k in ("trading_fee", "entry_slippage", "exit_slippage")
    }) if res.get("v2_config") else None
    out = []
    elapsed = time.perf_counter() - t0
    for direction in ("LONG", "SHORT"):
        row = base_row(
            family="candle12", engine="candle12_v2.run_candle12_v2 + trade_fees.enrich_trade_execution",
            strategy_id="CANDLE12_V2", direction=direction, symbol=symbol, timeframe=tf,
            eval_bars=eval_bars, warmup_bars=warm, missing_bars=missing,
            execution_model=(
                "Candle-1 BOS setup, Candle-2 stop-entry at C1 high/low trigger with 0.02% "
                "adverse entry slippage; structural stop, production targets; exit fill with "
                "0.02% adverse slippage (engine _exit_fill); same-bar SL+TP -> SL; one open "
                "trade at a time across both directions; taker both sides"
            ),
        )
        if res.get("status") not in (None, "OK") and not res.get("trades"):
            out.append({**row, "status": "NOT_RUN", "reason": f"engine status {res.get('status')}"})
            continue
        tr = [t for t in res.get("trades") or [] if t.get("direction") == direction]
        closed = [t for t in tr if t.get("outcome") not in (None, "OPEN") and t.get("exit_price") is not None]
        opened = len(tr) - len(closed)
        en = []
        for t in closed:
            fill = c12._exit_fill(direction, float(t["exit_price"]), v2)
            snap = dict(t.get("condition_snapshot") or {})
            e = enrich_trade_execution(
                {**t, "exit_price": fill, "raw_exit_price": t["exit_price"],
                 "entry_time": t.get("signal_time"),
                 "condition_snapshot": {"entry_type": snap.get("entry_type") or "MARKET"}},
                risk_usd=RISK_USD, taker_fee=TAKER, maker_fee=MAKER,
                leverage=LEVERAGE, account_equity=PRINCIPAL,
            )
            e["engine_net_R"] = t.get("r_multiple")
            en.append(e)
        row["notes"] = (
            "Engine reports net R per unit (fee 0.04%/side, slippage 0.02%/side); USD from "
            "trade_fees.enrich_trade_execution on the engine's entry (slippage included) and "
            "the engine's own slipped exit fill (candle12_v2._exit_fill). Rows split by "
            "direction from one both-direction run (shared position lock). candle12_hypothesis "
            f"(v1 A/B) is superseded by v2 and not run. engine_status={res.get('status')} "
            f"job_seconds={elapsed:.1f}"
        )
        out.append(finish_row(row, en, opened))
    return out


def job_ose(symbol: str, tf: str) -> list[dict[str, Any]]:
    from app.research.ohlcv_structure_experiment.config import VARIANTS
    from app.research.ohlcv_structure_experiment.engine import breaks_for_variant, run_variant
    from app.research.trade_fees import enrich_trade_execution

    t0 = time.perf_counter()
    candles, first, eval_bars, warm, missing = _load(symbol, tf, 500)
    out = []
    for variant in VARIANTS:
        events = breaks_for_variant(candles, symbol=symbol, variant=variant)
        events = [e for e in events if e.break_index >= first]
        records = run_variant(candles, events, variant)
        for direction in ("LONG", "SHORT"):
            row = base_row(
                family="ohlcv_structure_experiment",
                engine="ohlcv_structure_experiment.engine.run_variant + trade_fees.enrich_trade_execution",
                strategy_id=f"OSE_{variant}", direction=direction, symbol=symbol, timeframe=tf,
                eval_bars=eval_bars, warmup_bars=warm, missing_bars=missing,
                execution_model=(
                    "first close through a confirmed swing; entry next bar open with 0.02% "
                    "adverse slippage; stop = opposite confirmed swing; target 2R; 20-bar hold "
                    "then close exit with slippage; gap-through-stop exits at open; same-bar "
                    "-> stop; one position per symbol; taker both sides"
                ),
            )
            closed, opened = [], 0
            for r in records:
                if str(r.event.direction).upper() not in (direction, direction.lower()):
                    if str(r.event.direction).upper()[:1] != direction[:1]:
                        continue
                if r.status == "entry_qualified":
                    opened += 1  # qualified but window incomplete -> never filled
                    continue
                if r.status != "filled" or r.exit_price is None or r.entry_price is None:
                    continue
                e = enrich_trade_execution(
                    {"direction": direction, "entry_price": r.entry_price,
                     "stop_price": r.stop_price, "exit_price": r.exit_price,
                     "outcome": r.outcome, "entry_type": "MARKET",
                     "entry_time": candles[r.entry_index]["time"].isoformat(),
                     "exit_time": candles[r.exit_index]["time"].isoformat(),
                     "entry_index": r.entry_index, "exit_index": r.exit_index,
                     "engine_net_r": r.net_r, "event_id": r.event.event_id,
                     "target_price": r.target_price, "ambiguous": r.ambiguous},
                    risk_usd=RISK_USD, taker_fee=TAKER, maker_fee=MAKER,
                    leverage=LEVERAGE, account_equity=PRINCIPAL,
                )
                closed.append(e)
            row["notes"] = (
                "Research timing experiment (BOS/CHoCH break entry rules), R-only engine with "
                "fee 4 bps/side and slippage 2 bps in its own net_r; USD from "
                "trade_fees.enrich_trade_execution on the engine's slipped entry/exit prices. "
                "No funding passed. The experiment has no declared timeframe; run on 15m and 1h. "
                "Break events restricted to break_index inside the window. "
                f"events={len(events)} job_seconds={time.perf_counter() - t0:.1f}"
            )
            out.append(finish_row(row, closed, opened))
    return out


FAMILIES = {
    "multicap": (job_multicap, ["5m", "15m", "1h"]),
    "spr": (job_spr, ["1h"]),
    "candle12": (job_candle12, ["5m", "15m", "1h"]),
    "ose": (job_ose, ["15m", "1h"]),
    "bsc": (job_bsc, ["15m"]),
}


def cell_path(family: str, symbol: str, tf: str) -> Path:
    return CELLS / f"{family}__{symbol}__{tf}{SUFFIX}.json"


def run_job(family: str, symbol: str, tf: str) -> tuple[str, float, int]:
    path = cell_path(family, symbol, tf)
    t0 = time.perf_counter()
    try:
        rows = FAMILIES[family][0](symbol, tf)
    except Exception as exc:  # noqa: BLE001
        rows = [not_run(
            f"{type(exc).__name__}: {exc}", status="ERROR", family=family,
            engine=family, strategy_id=f"{family}:ALL", direction="ALL",
            symbol=symbol, timeframe=tf, notes=traceback.format_exc()[-800:],
        )]
    need = REQUESTED_WARMUP.get(family)
    for r in rows:
        w = r.get("warmup_bars")
        if need and w is not None and w < need:
            r["notes"] = (f"SHORT WARMUP: {w} bars loaded before the window, {need} requested "
                          f"(history starts too late). ") + str(r.get("notes") or "")
    _atomic_write(path, rows)
    return path.name, time.perf_counter() - t0, len(rows)


def static_rows() -> list[dict[str, Any]]:
    """Strategies owned here that cannot produce rows; one explicit NOT_RUN row each."""
    def nr(family, engine, sid, direction, tf, reason):
        return not_run(reason, family=family, engine=engine, strategy_id=sid,
                       direction=direction, symbol="ALL", timeframe=tf)
    bsc = []
    if not list(CELLS.glob("bsc__*.json")):
        from app.research.bos_strategy_comparison.strategies import STRATEGIES
        bsc = [
            nr("bos_strategy_comparison",
               "bos_strategy_comparison.runner.run_multi_strategy_backtest", sid,
               "LONG_SHORT", "15m",
               "time budget: the timing job (BTCUSDT 15m, all 7 strategies in one pass, "
               "23,040 eval bars, one worker) had not finished after about 40 minutes and "
               "was stopped; ten symbols would need several hours against a 60 minute "
               "budget. Adapter job_bsc is in this runner (--only bsc) and is resumable.")
            for sid in STRATEGIES
        ]
    return bsc + [
        nr("multicap", "multi_cap_strategies.reference", "MULTI_CAP_REFERENCE", "LONG", "ALL",
           "reference.py is a numpy cross-check of the signal detectors (sweep / FVG / "
           "discount / volume-BOS indices) only: no entry price, stop, target or trade "
           "simulation exists, so there is nothing to backtest. baseline_forensics.py is a "
           "post-hoc artifact audit, not a strategy."),
        nr("signals", "app.signals.backtest.run_backtest", "LIVE_SIGNAL_ENGINE", "LONG_SHORT", "ALL",
           "run_backtest returns only an aggregate BacktestReport in R (trade list is a "
           "local variable, no per-trade entry/stop/exit is exposed), so the repo fee/sizing "
           "function cannot be applied without editing backend/app; no USD row is possible."),
        nr("candle12", "candle12_hypothesis.run_hypothesis_a / run_hypothesis_b",
           "CANDLE12_HYPOTHESIS_V1", "LONG_SHORT", "ALL",
           "superseded by candle12_v2 (unbounded full-history scans, no fee/slippage model); "
           "the v2 engine was run instead."),
    ]


def finalize(started: str) -> None:
    rows: list[dict[str, Any]] = []
    for p in sorted(CELLS.glob("*.json")):
        rows.extend(json.loads(p.read_text(encoding="utf-8")))
    if WID == "IS":
        rows.extend(static_rows())
        rows = [{k: r.get(k) for k in ROW_KEYS} for r in rows]
        _atomic_write(HERE / "rows.json", rows)
    else:
        # oos/cells holds every OOS window; one combined file
        rows = [{**{k: r.get(k) for k in ROW_KEYS}, "window_id": r.get("window_id")} for r in rows]
        _atomic_write(OUT / ("rows_oos.json" if WID != "ISCHECK" else "rows_check.json"), rows)
    git = lambda *a: subprocess.run(["git", *a], cwd=BACKEND, capture_output=True, text=True).stdout
    meta = {
        "git_head": git("rev-parse", "HEAD").strip(),
        "git_status_porcelain_lines": len([l for l in git("status", "--porcelain").splitlines() if l.strip()]),
        "runner": str(Path(__file__).name),
        "runner_sha1": hashlib.sha1(Path(__file__).read_bytes()).hexdigest(),
        "db_helper_sha1": hashlib.sha1((HERE / "_db.py").read_bytes()).hexdigest(),
        "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "read_only_proof": _db.read_only_proof(),
        "window": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        "last_window_id_run": WID,
        "windows_in_rows": sorted({str(r.get("window_id") or "IS") for r in rows}),
        "windows": {k: [datetime(*v[0], tzinfo=timezone.utc).isoformat(),
                        datetime(*v[1], tzinfo=timezone.utc).isoformat()]
                    for k, v in _WINDOWS.items()},
        "symbols": SYMBOLS,
        "parameters": {"principal_usd": PRINCIPAL, "risk_usd": RISK_USD, "leverage": LEVERAGE,
                       "taker_fee": TAKER, "maker_fee": MAKER},
        "rows": len(rows),
        "status_counts": {s: sum(1 for r in rows if r["status"] == s) for s in ("OK", "NOT_RUN", "ERROR")},
    }
    _atomic_write(OUT / "_meta.json", meta)
    print(json.dumps(meta["status_counts"]), "rows", len(rows))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--time", action="store_true", help="time one job per family/timeframe (BTCUSDT) and exit")
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--finalize-only", action="store_true")
    ap.add_argument("--tfs", default="", help="comma list; restrict timeframes")
    ap.add_argument("--symbols", default="", help="comma list; restrict symbols")
    args = ap.parse_args()
    CELLS.mkdir(parents=True, exist_ok=True)
    TRADES.mkdir(parents=True, exist_ok=True)
    started_file = OUT / "_started.txt"
    if not started_file.exists():
        started_file.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    started = started_file.read_text(encoding="utf-8").strip()
    fams = [f for f in FAMILIES if not args.only or f in args.only.split(",")]
    tfs = [t for t in args.tfs.split(",") if t]
    syms = [x for x in args.symbols.split(",") if x] or (SYMBOLS[:1] if args.time else SYMBOLS)
    jobs = [(f, s, tf) for f in fams for tf in FAMILIES[f][1] if not tfs or tf in tfs
            for s in syms]
    todo = [j for j in jobs if not cell_path(*j).exists()]
    if not args.finalize_only:
        print(f"jobs={len(jobs)} todo={len(todo)}", flush=True)
        with ProcessPoolExecutor(max_workers=min(3, max(1, args.workers))) as pool:
            futs = {pool.submit(run_job, *j): j for j in todo}
            for fut in as_completed(futs):
                try:
                    name, secs, n = fut.result()
                    print(f"done {name} rows={n} {secs:.1f}s", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"FAILED {futs[fut]} {exc!r}", flush=True)
    if not args.time:
        finalize(started)


if __name__ == "__main__":
    main()
