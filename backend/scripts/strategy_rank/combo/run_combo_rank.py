"""Strategy ranking, part "combo": every combination-engine strategy, LONG and SHORT,
10 coins x 5 timeframes, one fixed evaluation window.

Read-only. The database session is read-only at connection level and every SQL
statement is audited (SELECT / SHOW only). Nothing under backend/app is edited; the
engine is only called:

  app.research.service._load_research_candles / _load_htf_candles_for_combo
  app.research.combination_backtest.run_combination_backtest
  app.research.backtest_cpu_worker.build_matrix_row_postprocess   (fees / sizing)

This is the per-cell body of ResearchService.strategy_matrix (what the Backtest screen
runs). Deviations: validate_backtest_request is not called (it only accepts 1h),
analytics attach is off, no artifacts are written, the walk runs in this process.

Usage (from backend/):
  python -B scripts/strategy_rank/combo/run_combo_rank.py enumerate
  python -B scripts/strategy_rank/combo/run_combo_rank.py probe --strategies A,B --tfs 1h,5m [--budget 120]
  python -B scripts/strategy_rank/combo/run_combo_rank.py run --plan plan.json [--workers 3]
  python -B scripts/strategy_rank/combo/run_combo_rank.py collect --plan plan.json
"""

from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import asyncio
import hashlib
import json
import math
import statistics
import subprocess
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(r"D:\cryptoscreener")
BACKEND = REPO / "backend"
OUT = BACKEND / "scripts" / "strategy_rank" / "combo"
CELLS = OUT / "cells"
TRADES = OUT / "trades"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
           "TRXUSDT", "AVAXUSDT", "LINKUSDT"]
TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]
TF_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
WINDOW_START = "2026-02-14T00:00:00Z"
WINDOW_END = "2026-10-12T00:00:00Z"
START_DATE = "2026-02-14"          # engine: start <= ts
END_DATE_INCLUSIVE = "2026-10-11"  # engine: ts < end_date + 1 day = 2026-10-12T00:00Z
PRINCIPAL_USD = 1000.0
RISK_USD = 20.0
LEVERAGE = 2.0
TAKER_FEE_PCT = 0.04
MAKER_FEE_PCT = 0.02

# Per-task context. Default = the in-sample grid (cells/, trades/, rows.json).
# A plan phase may carry "window": {"id","start_date","end_date_inclusive","start_utc","end_utc"}
# and "out": "<subdir>"; then cells/trades go under <subdir>/ and file names get __<window id>.
_CTX: dict[str, Any] = {"window": None, "out": None}


def _win() -> dict[str, Any]:
    w = _CTX.get("window")
    if w:
        return w
    return {"id": None, "start_date": START_DATE, "end_date_inclusive": END_DATE_INCLUSIVE,
            "start_utc": WINDOW_START, "end_utc": WINDOW_END}


def _dirs() -> tuple[Path, Path, str]:
    out = _CTX.get("out")
    if out:
        return OUT / out / "cells", OUT / out / "trades", f"{out}/trades"
    return CELLS, TRADES, "trades"


ROW_KEYS = ["family", "engine", "strategy_id", "direction", "symbol", "timeframe", "status", "reason",
            "window_start_utc", "window_end_utc", "eval_bars", "warmup_bars", "missing_bars",
            "trades_closed", "wins", "losses", "win_rate", "gross_pnl_usd", "fees_usd", "net_pnl_usd",
            "return_pct", "profit_factor", "avg_r_net", "max_drawdown_usd", "max_drawdown_r",
            "median_hold_hours", "avg_hold_hours", "open_trades_at_end", "execution_model",
            "principal_usd", "risk_usd", "leverage", "taker_fee_pct", "maker_fee_pct", "trades_file",
            "notes"]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
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
    try:
        import numpy as np

        if isinstance(obj, np.generic):
            return clean(obj.item())
    except Exception:  # noqa: BLE001
        pass
    return str(obj)


def iso(ts: Any) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).isoformat()
    return str(ts)


def as_dt(ts: Any) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000.0 if ts > 1e11 else ts, tz=timezone.utc)
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(clean(payload), indent=1, allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)


def cell_name(strategy: str, direction: str, symbol: str, tf: str) -> str:
    wid = _win()["id"]
    return f"{strategy}__{direction}__{symbol}__{tf}" + (f"__{wid}" if wid else "") + ".json"


def cell_done(path: Path) -> bool:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return d.get("status") in ("OK", "NOT_RUN", "ERROR") and d.get("final") is True


def base_row(strategy: str, direction: str, symbol: str, tf: str) -> dict[str, Any]:
    row = {k: None for k in ROW_KEYS}
    row.update(family="combo", engine="combination_engine", strategy_id=strategy, direction=direction,
               symbol=symbol, timeframe=tf, window_start_utc=_win()["start_utc"], window_end_utc=_win()["end_utc"],
               principal_usd=PRINCIPAL_USD, risk_usd=RISK_USD, leverage=LEVERAGE,
               taker_fee_pct=TAKER_FEE_PCT, maker_fee_pct=MAKER_FEE_PCT, notes="")
    if _win()["id"]:
        row["window_id"] = _win()["id"]
    return row


def strategy_table() -> list[dict[str, Any]]:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from app.research.bos_combinations import COMBINATIONS
    from app.research.combo02_v2.research_variants import COMBO_V2_FAMILY
    from app.research.combo03_transition.variants import COMBO_03_FAMILY
    from app.research.sd_structure.variants import SD_STRUCTURE_FAMILY

    out = []
    for cid, combo in COMBINATIONS.items():
        is_v2 = cid in COMBO_V2_FAMILY
        is_sd = cid in SD_STRUCTURE_FAMILY
        is_c3 = cid in COMBO_03_FAMILY
        fam = "COMBO_02_V2" if is_v2 else "SD_STRUCTURE" if is_sd else "COMBO_03_TRANSITION" if is_c3 else "BOS_GATES"
        aux_loaded = []
        if bool(combo.require_htf_alignment) or is_v2 or is_sd:
            aux_loaded += ["1h (unless setup tf is 1h)", "4h (unless setup tf is 4h)"]
        if is_v2:
            aux_loaded.append("15m")
        if is_sd:
            aux_loaded += ["15m and 5m (when setup tf is 1h/4h/1d)", "1d (unless setup tf is 1d)"]
        out.append({
            "strategy_id": cid, "name": combo.name, "engine_family": fam,
            "supports": ["LONG", "SHORT"],
            "direction_handling": ("service passes direction_filter=None for the COMBO_02_V2 family: one "
                                   "unfiltered LONG+SHORT walk, rows are the per-direction subsets of it"
                                   if is_v2 else "direction_filter=LONG|SHORT passed to the walk (one walk per direction)"),
            "auxiliary_timeframes_loaded_by_service": aux_loaded,
            "require_htf_alignment": bool(combo.require_htf_alignment),
            "gates": combo.to_dict()["gates"],
        })
    return out


# --------------------------------------------------------------------------
# worker
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


def _worker_init(quiet: bool = True) -> None:
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    if quiet:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 1)
        except OSError:
            pass
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    os.chdir(str(BACKEND))
    import logging

    logging.getLogger("dotenv.main").setLevel(logging.ERROR)

    from sqlalchemy import event, text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.services.database import db_manager
    import app.research.service as service

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    engine = create_async_engine(
        _dsn(), pool_size=1, max_overflow=0, pool_pre_ping=True,
        connect_args={"server_settings": {"default_transaction_read_only": "on",
                                          "application_name": "strategy_rank_combo_readonly"}},
    )
    sql_audit: dict[str, int] = {}
    violations: list[str] = []

    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        head = (statement or "").lstrip().split(None, 1)[0].upper() if (statement or "").strip() else "?"
        sql_audit[head] = sql_audit.get(head, 0) + 1
        if head not in ("SELECT", "SHOW"):
            violations.append(statement[:200])
            raise RuntimeError(f"strategy_rank: non-SELECT statement blocked: {statement[:120]!r}")

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

    # Memoise the engine's own candle loader per argument set (same function, same
    # arguments). A fresh list of fresh dicts is handed out on every call.
    orig_load = service._load_research_candles
    cache: dict[tuple, tuple] = {}

    async def cached_load(symbol, timeframe, *, limit=500, start_date=None, end_date=None,
                          warmup_bars=100, use_research_cache=True, tail_warmup=False):
        key = (str(symbol).upper(), str(timeframe).lower(), int(limit), start_date, end_date,
               int(warmup_bars), bool(use_research_cache), bool(tail_warmup))
        if key not in cache:
            cache[key] = await orig_load(
                symbol, timeframe, limit=limit, start_date=start_date, end_date=end_date,
                warmup_bars=warmup_bars, use_research_cache=use_research_cache, tail_warmup=tail_warmup)
        candles, eval_start, meta = cache[key]
        return [dict(c) for c in candles], eval_start, dict(meta)

    service._load_research_candles = cached_load
    _W.update(loop=loop, engine=engine, sql_audit=sql_audit, violations=violations, ro=ro,
              cache=cache, cache_symbol=None)


def series_info(candles: list[dict[str, Any]] | None, tf: str, from_index: int = 0) -> dict[str, Any]:
    step = TF_SECONDS[tf]
    times = [as_dt(c.get("time")) for c in (candles or [])]
    times = [t for t in times if t is not None]
    missing = 0
    for k, (a, b) in enumerate(zip(times, times[1:])):
        gap = int(round((b - a).total_seconds() / step)) - 1
        if gap > 0 and k + 1 >= from_index:
            missing += gap
    return {"bars": len(times), "first": iso(times[0]) if times else None,
            "last": iso(times[-1]) if times else None, "missing": missing}


def trade_out(t: dict[str, Any], tf: str) -> dict[str, Any]:
    hb = t.get("holding_bars")
    return {
        "direction": t.get("direction"),
        "signal_time": t.get("signal_time"),
        "entry_time": t.get("entry_time"),
        "entry": t.get("entry_price"),
        "stop": t.get("stop_price"),
        "targets": [t.get("tp1"), t.get("tp2"), t.get("tp3")],
        "exit_time": t.get("exit_time"),
        "exit_price": t.get("exit_price"),
        "outcome": t.get("outcome"),
        "gross": t.get("gross_pnl_usd"),
        "fee": t.get("fee_total_usd"),
        "net": t.get("net_pnl_usd"),
        "r_gross": t.get("r_multiple"),
        "r_net": t.get("r_net"),
        "holding_bars": hb,
        "holding_hours": (float(hb) * TF_SECONDS[tf] / 3600.0) if hb is not None else None,
    }


def metrics(trades: list[dict[str, Any]], tf: str) -> dict[str, Any]:
    n = len(trades)
    net = [float(t["net_pnl_usd"]) for t in trades]
    gross = [float(t["gross_pnl_usd"]) for t in trades if t.get("gross_pnl_usd") is not None]
    fees = [float(t.get("fee_total_usd") or 0.0) for t in trades]
    r_net = [float(t["r_net"]) for t in trades if t.get("r_net") is not None]
    wins = sum(1 for v in net if v > 0)
    losses = sum(1 for v in net if v < 0)
    gains = sum(v for v in net if v > 0)
    lost = sum(-v for v in net if v < 0)

    def dd(vals: list[float]) -> float | None:
        if not vals:
            return 0.0 if n == 0 else None
        eq = peak = worst = 0.0
        for v in vals:
            eq += v
            peak = max(peak, eq)
            worst = min(worst, eq - peak)
        return abs(worst)

    holds = [float(t["holding_bars"]) * TF_SECONDS[tf] / 3600.0 for t in trades if t.get("holding_bars") is not None]
    fee_sum = sum(fees)
    net_sum = sum(net)
    return {
        "trades_closed": n, "wins": wins, "losses": losses,
        "win_rate": (wins / n) if n else None,
        "gross_pnl_usd": sum(gross) if n else 0.0,
        "fees_usd": -abs(fee_sum) if n else 0.0,
        "net_pnl_usd": net_sum if n else 0.0,
        "return_pct": (net_sum / PRINCIPAL_USD * 100.0) if n else 0.0,
        "profit_factor": (gains / lost) if lost > 0 else None,
        "avg_r_net": (sum(r_net) / len(r_net)) if r_net else None,
        "max_drawdown_usd": dd(net),
        "max_drawdown_r": dd(r_net) if (r_net or n == 0) else None,
        "median_hold_hours": statistics.median(holds) if holds else None,
        "avg_hold_hours": (sum(holds) / len(holds)) if holds else None,
    }


async def _run_walk(strategy: str, symbol: str, tf: str, direction: str | None,
                    budget_s: float | None) -> dict[str, Any]:
    """One engine walk + postprocess. Returns raw pieces for row building."""
    import app.research.service as service
    from app.research.backtest_cpu_worker import build_matrix_row_postprocess
    from app.research.bos_combinations import get_combination
    from app.research.combination_backtest import run_combination_backtest
    from app.research.combo02_v2.research_variants import COMBO_V2_FAMILY
    from app.research.config import ResearchConfig
    from app.research.sd_structure.variants import SD_STRUCTURE_FAMILY

    combo = get_combination(strategy)
    if combo is None:
        raise RuntimeError(f"unknown combination {strategy}")
    engine_id = str(combo.combination_id)
    # Same choices as ResearchService.strategy_matrix.
    rcfg = ResearchConfig.for_new_combo02_v2_run() if engine_id == "COMBO_02_V2" else ResearchConfig()
    warmup = max(int(rcfg.min_bars), 100)
    lim = 5760  # ignored by the loader when dates are given; only sizes nothing in dated mode
    kw = dict(start_date=_win()["start_date"], end_date=_win()["end_date_inclusive"],
              use_research_cache=False, tail_warmup=True)

    t0 = time.perf_counter()
    candles, eval_start, load_meta = await service._load_research_candles(
        symbol, tf, limit=lim, warmup_bars=warmup, **kw)
    is_v2 = engine_id in COMBO_V2_FAMILY
    is_sd = engine_id in SD_STRUCTURE_FAMILY
    c1h, c4h = await service._load_htf_candles_for_combo(
        symbol, require_htf=bool(combo.require_htf_alignment) or is_v2 or is_sd,
        setup_timeframe=tf, limit=lim, warmup_bars=warmup, **kw)
    c15 = c5 = c1d = None
    needs_ltf = is_sd and tf not in {"5m", "15m"}
    if is_v2 or needs_ltf:
        c15, _, _ = await service._load_research_candles(
            symbol, "15m", limit=max(lim * 4, 500), warmup_bars=max(warmup, 200), **kw)
    if needs_ltf:
        c5, _, _ = await service._load_research_candles(
            symbol, "5m", limit=max(lim * 12, 500), warmup_bars=max(warmup, 200), **kw)
    if is_sd and tf != "1d":
        c1d, _, _ = await service._load_research_candles(
            symbol, "1d", limit=max(lim // 4, 200), warmup_bars=max(warmup // 4, 40), **kw)
    load_s = time.perf_counter() - t0

    scfg = service._signal_config()
    mcap = service._market_cap(load_meta["symbol"])
    idx0 = eval_start if eval_start > 0 else None
    walk_dir = None if is_v2 else direction

    prog = {"bars": 0, "total": 0}

    def _pcb(p):  # noqa: ANN001
        prog["bars"], prog["total"] = p.get("bars_processed") or 0, p.get("total_bars") or 0

    t0 = time.perf_counter()
    deadline = (t0 + budget_s) if budget_s else None
    out = run_combination_backtest(
        str(load_meta["symbol"]), str(load_meta["timeframe"]), list(candles), engine_id,
        signal_config=scfg, research_config=rcfg, market_cap=mcap,
        direction_filter=walk_dir, index_start=idx0,
        should_cancel=(lambda: time.perf_counter() > deadline) if deadline else None,
        candles_1h=c1h, candles_4h=c4h, candles_15m=c15, candles_5m=c5, candles_1d=c1d,
        progress_callback=_pcb, job_id=None,
    )
    walk_s = time.perf_counter() - t0

    aux = {}
    for name, s, atf in (("1h", c1h, "1h"), ("4h", c4h, "4h"), ("15m", c15, "15m"), ("5m", c5, "5m"), ("1d", c1d, "1d")):
        if s is not None:
            aux[name] = series_info(s, atf)
    info = series_info(candles, tf, from_index=int(eval_start or 0))
    res = {
        "engine_id": engine_id, "walk_direction_filter": walk_dir, "is_v2": is_v2,
        "walk_status": (out or {}).get("status"), "walk_seconds": round(walk_s, 3),
        "load_seconds": round(load_s, 3), "progress": dict(prog),
        "bars_loaded": len(candles), "warmup_bars": int(eval_start or 0),
        "eval_bars": max(0, len(candles) - int(eval_start or 0)),
        "missing_bars": info["missing"], "first_bar": info["first"], "last_bar": info["last"],
        "first_eval_bar": iso(as_dt(candles[eval_start].get("time"))) if candles and eval_start < len(candles) else None,
        "aux": aux, "candle_source": load_meta.get("candle_source"),
        "data_quality": {k: ((out or {}).get("data_quality") or {}).get(k)
                         for k in ("status", "data_quality", "reason", "coverage_ratio", "missing_candles", "gap_count")},
        "reason": (out or {}).get("reason") or (out or {}).get("disclaimer"),
    }
    if out is None or out.get("status") != "OK":
        return res

    row = build_matrix_row_postprocess({
        "backtest_out": out, "risk_usd": RISK_USD, "taker_fee": TAKER_FEE_PCT / 100.0,
        "maker_fee": MAKER_FEE_PCT / 100.0, "leverage": LEVERAGE, "principal": PRINCIPAL_USD,
        "include_trades": True, "direction": direction or "LONG",
        "combination_id": engine_id, "combination_name": combo.name,
        "load_meta": load_meta, "candles": candles, "candles_1h": c1h, "candles_4h": c4h,
        "candles_15m": None, "index_start": idx0, "job_id": None,
        "analytics_on": False, "write_artifacts": False,
    })
    try:
        from app.research.causal_execution import clear_isolated_execution_runs

        clear_isolated_execution_runs()  # process memory only
    except Exception:  # noqa: BLE001
        pass
    res.update(
        row_trades=list(row.get("trades") or []),
        walk_trades=list(out.get("trades") or []),
        execution_model=row.get("pnl_model") or row.get("execution_model") or out.get("execution_model"),
        walk_execution_model=out.get("execution_model"),
        row_sample_size=row.get("sample_size"), row_net=row.get("pnl_usd_net"),
        row_gross=row.get("pnl_usd"), row_fees=row.get("fees_usd"),
        setups_processed=out.get("setups_processed"), candles_processed=out.get("candles_processed"),
    )
    return res


def _is_closed(t: dict[str, Any]) -> bool:
    return t.get("net_pnl_usd") is not None and str(t.get("outcome") or "").upper() not in ("", "OPEN", "NONE")


def rows_from_walk(strategy: str, symbol: str, tf: str, directions: list[str], res: dict[str, Any],
                   budget_s: float | None) -> list[tuple[dict[str, Any], list[dict[str, Any]] | None]]:
    out = []
    for d in directions:
        row = base_row(strategy, d, symbol, tf)
        row.update(eval_bars=res["eval_bars"], warmup_bars=res["warmup_bars"], missing_bars=res["missing_bars"])
        notes = [f"bars: first_eval={res['first_eval_bar']} last={res['last_bar']} loaded={res['bars_loaded']}",
                 f"walk {res['walk_seconds']}s"]
        if tf != "1h":
            notes.append("non-1h setup timeframe: validate_backtest_request (1h only) not called; engine called "
                         "directly with the same risk/fee/leverage values")
        else:
            notes.append("validate_backtest_request not called (uniform path for all timeframes); flat risk_usd 20")
        if tf == "1d" and not _win()["id"]:
            notes.append("1d candles end before window end (see last bar)")
        if tf == "5m" and not _win()["id"]:
            notes.append("5m history starts 2026-01-15; engine warmup of 100 bars before window start is available")
        if int(res["warmup_bars"]) < 100:
            notes.append(f"SHORT WARMUP: {res['warmup_bars']} setup bars before the window (engine asks for 100)")
        ws_dt, fe_dt = as_dt(_win()["start_utc"]), as_dt(res.get("first_eval_bar"))
        if ws_dt and fe_dt and fe_dt > ws_dt:
            notes.append(f"WINDOW NOT FULLY COVERED: first evaluated bar {res['first_eval_bar']} is after window start")
        for k, v in res["aux"].items():
            a_first = as_dt(v.get("first"))
            if ws_dt and a_first and a_first > ws_dt:
                notes.append(f"AUX {k} STARTS INSIDE WINDOW at {v['first']}")
            elif ws_dt and a_first and k == "4h" and (ws_dt - a_first).total_seconds() < 50 * 14400:
                notes.append(f"SHORT 4h WARMUP: 4h series starts {v['first']} (engine asks for 50 bars before the window)")
        if res["aux"]:
            notes.append("aux: " + ", ".join(f"{k}[{v['bars']} bars {v['first']}..{v['last']}]" for k, v in res["aux"].items()))
        ws = res["walk_status"]
        if ws == "CANCELLED":
            p = res["progress"]
            frac = (p["bars"] / p["total"]) if p.get("total") else None
            est = (res["walk_seconds"] / frac) if frac else None
            row.update(status="NOT_RUN", reason=(
                f"runtime budget: walk cancelled after {res['walk_seconds']:.0f}s at "
                f"{p['bars']}/{p['total']} bars; estimated full walk "
                f"{(est / 60.0):.1f} min" if est else f"runtime budget: walk cancelled after {res['walk_seconds']:.0f}s"))
            row["notes"] = "; ".join(notes)
            out.append((row, None))
            continue
        if ws != "OK":
            row.update(status="NOT_RUN" if ws == "INSUFFICIENT_DATA" else "ERROR",
                       reason=f"engine walk status {ws}: {res.get('reason')} {res.get('data_quality')}")
            row["notes"] = "; ".join(notes)
            out.append((row, None))
            continue
        closed = [t for t in res["row_trades"] if _is_closed(t) and str(t.get("direction") or "").upper() == d]
        other = [t for t in res["row_trades"] if _is_closed(t) and str(t.get("direction") or "").upper() != d]
        m = metrics(closed, tf)
        row.update(m)
        row["open_trades_at_end"] = sum(1 for t in res["walk_trades"]
                                        if t.get("outcome") == "OPEN" and str(t.get("direction") or "").upper() == d)
        row["execution_model"] = res["execution_model"]
        row["status"] = "OK"
        row["reason"] = ""
        row["trades_file"] = f"{_dirs()[2]}/{cell_name(strategy, d, symbol, tf)}"
        if res["is_v2"]:
            notes.append("COMBO_02_V2 family: the service passes direction_filter=None, so this is the "
                         f"{d} subset of ONE unfiltered LONG+SHORT walk (the other side's positions blocked "
                         "entries; sizing used the combined running equity); not a separate direction-filtered backtest; "
                         f"other-direction closed trades in the same walk: {len(other)}")
            if res["engine_id"] == "COMBO_02_V2":
                notes.append("figures are the causal next-open book that the screen shows for COMBO_02_V2 "
                             f"(walk execution_model={res['walk_execution_model']})")
        else:
            if other:
                notes.append(f"WARNING: {len(other)} closed trades in the other direction despite direction_filter={d}")
        if m["trades_closed"] == 0:
            notes.append("zero closed trades")
        notes.append("hold hours = holding_bars x timeframe; drawdown from closed trades in list order")
        row["notes"] = "; ".join(notes)
        out.append((row, [trade_out(t, tf) for t in closed]))
    return out


def run_task(task: dict[str, Any]) -> list[dict[str, Any]]:
    """One worker task: one (symbol, timeframe) with a list of strategies."""
    loop = _W["loop"]
    symbol, tf = task["symbol"], task["tf"]
    _CTX["window"], _CTX["out"] = task.get("window"), task.get("out")
    CELLS, TRADES, _rel = _dirs()
    if _W.get("cache_symbol") != symbol:
        _W["cache"].clear()
        _W["cache_symbol"] = symbol
    results = []
    for strategy in task["strategies"]:
        budget = task.get("budget_s")
        is_v2 = strategy.startswith("COMBO_02_V2")
        groups = [["LONG", "SHORT"]] if is_v2 else [["LONG"], ["SHORT"]]
        for dirs in groups:
            paths = [CELLS / cell_name(strategy, d, symbol, tf) for d in dirs]
            if not task.get("force") and all(cell_done(p) for p in paths):
                results.append({"cell": paths[0].name, "status": "SKIPPED"})
                continue
            t0 = time.perf_counter()
            try:
                res = loop.run_until_complete(_run_walk(strategy, symbol, tf, None if is_v2 else dirs[0], budget))
                pairs = rows_from_walk(strategy, symbol, tf, dirs, res, budget)
            except BaseException as exc:  # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                pairs = []
                for d in dirs:
                    row = base_row(strategy, d, symbol, tf)
                    row.update(status="ERROR", reason=f"{type(exc).__name__}: {exc}"[:600],
                               notes=traceback.format_exc()[-900:])
                    pairs.append((row, None))
            for (row, trades), d in zip(pairs, dirs):
                if trades is not None:
                    atomic_write(TRADES / cell_name(strategy, d, symbol, tf), trades)
                atomic_write(CELLS / cell_name(strategy, d, symbol, tf), {
                    "final": True, "status": row["status"], "row": row,
                    "cell_seconds": round(time.perf_counter() - t0, 3),
                    "sql_audit": dict(_W["sql_audit"]), "sql_violations": list(_W["violations"]),
                    "db_session": dict(_W["ro"]), "pid": os.getpid(),
                    "written_utc": datetime.now(timezone.utc).isoformat(),
                })
                results.append({"cell": cell_name(strategy, d, symbol, tf), "status": row["status"],
                                "seconds": round(time.perf_counter() - t0, 2), "reason": row["reason"][:160] if row["reason"] else ""})
    return results


# --------------------------------------------------------------------------
# parent
# --------------------------------------------------------------------------
def git_meta() -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(["git", "--no-optional-locks", "-C", str(REPO), *args],
                              capture_output=True, text=True, check=False).stdout
    return {"git_head": run("rev-parse", "HEAD").strip(),
            "git_status_porcelain_lines": len([ln for ln in run("status", "--porcelain").splitlines() if ln.strip()])}


def cmd_enumerate(_args) -> int:
    table = strategy_table()
    atomic_write(OUT / "_strategies.json", {"count": len(table), "source": "app.research.bos_combinations.COMBINATIONS",
                                            "strategies": table})
    print(f"{len(table)} strategies -> {OUT / '_strategies.json'}")
    return 0


def cmd_probe(args) -> int:
    _worker_init(quiet=True)
    strategies = args.strategies.split(",")
    for tf in args.tfs.split(","):
        for s in strategies:
            for sym in args.symbols.split(","):
                d = None if s.startswith("COMBO_02_V2") else args.direction
                t0 = time.perf_counter()
                try:
                    res = _W["loop"].run_until_complete(_run_walk(s, sym, tf, d, args.budget))
                    p = res["progress"]
                    frac = (p["bars"] / p["total"]) if p.get("total") else None
                    est = res["walk_seconds"] / frac if frac else None
                    n = len([t for t in res.get("row_trades", []) if _is_closed(t)])
                    msg = (f"{s} {tf} {sym} dir={d} status={res['walk_status']} walk={res['walk_seconds']}s "
                           f"load={res['load_seconds']}s bars={p['bars']}/{p['total']} est_full={est and round(est, 1)}s "
                           f"closed={n} exec={res.get('execution_model')} eval={res['eval_bars']} warm={res['warmup_bars']} "
                           f"miss={res['missing_bars']} last={res['last_bar']}")
                except Exception as exc:  # noqa: BLE001
                    msg = f"{s} {tf} {sym} ERROR {type(exc).__name__}: {exc} ({time.perf_counter() - t0:.1f}s)"
                sys.stderr.write(msg + "\n")
                sys.stderr.flush()
    sys.stderr.write(f"sql_audit={_W['sql_audit']} ro={_W['ro']}\n")
    return 0


def load_plan(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_run(args) -> int:
    plan = load_plan(args.plan)
    started = datetime.now(timezone.utc)
    tasks = []
    for phase in plan["phases"]:
        for tf in phase["timeframes"]:
            for sym in phase.get("symbols") or SYMBOLS:
                tasks.append({"symbol": sym, "tf": tf, "strategies": phase["strategies"],
                              "budget_s": phase.get("budget_s"), "force": args.force, "phase": phase["name"],
                              "window": phase.get("window"), "out": phase.get("out")})
    total = len(tasks)
    print(f"[combo_rank] tasks={total} workers={args.workers}", flush=True)
    t0 = time.perf_counter()
    done_n = 0
    counts: dict[str, int] = {}
    with ProcessPoolExecutor(max_workers=min(3, max(1, args.workers)), initializer=_worker_init) as pool:
        pending: dict[Any, dict[str, Any]] = {}
        queue = list(tasks)
        while queue or pending:
            while queue and len(pending) < min(3, max(1, args.workers)):
                t = queue.pop(0)
                pending[pool.submit(run_task, t)] = t
            done, _ = wait(list(pending), timeout=60, return_when=FIRST_COMPLETED)
            for fut in done:
                t = pending.pop(fut)
                done_n += 1
                try:
                    rs = fut.result()
                    for r in rs:
                        counts[r["status"]] = counts.get(r["status"], 0) + 1
                except BaseException as exc:  # noqa: BLE001
                    counts["WORKER_FAIL"] = counts.get("WORKER_FAIL", 0) + 1
                    print(f"[combo_rank] worker failure on {t['phase']} {t['symbol']} {t['tf']}: {exc!r}", flush=True)
                print(f"[combo_rank] {done_n}/{total} {t['phase']} {t['symbol']} {t['tf']} "
                      f"elapsed={time.perf_counter() - t0:.0f}s counts={counts}", flush=True)
    runs_path = OUT / "_runs.json"
    runs = json.loads(runs_path.read_text(encoding="utf-8")) if runs_path.exists() else []
    runs.append({"started_utc": started.isoformat(), "finished_utc": datetime.now(timezone.utc).isoformat(),
                 "plan": args.plan, "workers": args.workers, "counts": counts,
                 "runner_sha1": hashlib.sha1(Path(__file__).read_bytes()).hexdigest(), **git_meta()})
    atomic_write(runs_path, runs)
    return 0


def cmd_collect(args) -> int:
    plan = load_plan(args.plan)
    not_run: dict[str, str] = plan.get("not_run", {})   # "STRATEGY|tf" or "STRATEGY|*" -> reason
    table = [s["strategy_id"] for s in strategy_table()]
    rows = []
    audit: dict[str, int] = {}
    per_pid: dict[str, dict[str, int]] = {}
    violations = 0
    ro_seen = set()
    for s in table:
        for d in ("LONG", "SHORT"):
            for sym in SYMBOLS:
                for tf in TIMEFRAMES:
                    p = CELLS / cell_name(s, d, sym, tf)
                    if p.exists():
                        c = json.loads(p.read_text(encoding="utf-8"))
                        rows.append({k: c["row"].get(k) for k in ROW_KEYS})
                        per_pid[str(c.get("pid"))] = max(per_pid.get(str(c.get("pid")), {}), c.get("sql_audit") or {},
                                                        key=lambda a: sum(a.values()) if a else 0)
                        violations += len(c.get("sql_violations") or [])
                        ro_seen.add(json.dumps(c.get("db_session"), sort_keys=True))
                    else:
                        row = base_row(s, d, sym, tf)
                        reason = not_run.get(f"{s}|{tf}") or not_run.get(f"{s}|*") or "not reached before the run was stopped"
                        row.update(status="NOT_RUN", reason=reason)
                        rows.append(row)
    for a in per_pid.values():
        for k, v in a.items():
            audit[k] = audit.get(k, 0) + v
    atomic_write(OUT / "rows.json", rows)
    runs_path = OUT / "_runs.json"
    runs = json.loads(runs_path.read_text(encoding="utf-8")) if runs_path.exists() else []
    counts: dict[str, dict[str, int]] = {}
    for r in rows:
        counts.setdefault(r["timeframe"], {}).setdefault(r["status"], 0)
        counts[r["timeframe"]][r["status"]] += 1
    atomic_write(OUT / "_meta.json", {
        **git_meta(),
        "runner": str(Path(__file__).resolve()),
        "runner_sha1": hashlib.sha1(Path(__file__).read_bytes()).hexdigest(),
        "start_utc": runs[0]["started_utc"] if runs else None,
        "finish_utc": datetime.now(timezone.utc).isoformat(),
        "runs": runs,
        "rows": len(rows), "counts_by_timeframe": counts,
        "sql_audit_statement_counts": audit,
        "sql_audit_basis": "per worker process: the largest cumulative counter seen in that process's cell files, summed over processes",
        "sql_violations": violations,
        "read_only_proof": {
            "connect_arg": "server_settings.default_transaction_read_only=on",
            "probed_in_every_worker": [json.loads(x) for x in sorted(ro_seen)],
            "statement_guard": "before_cursor_execute raises on anything but SELECT / SHOW",
        },
        "window": {"start_utc": WINDOW_START, "end_utc": WINDOW_END, "engine_start_date": START_DATE,
                   "engine_end_date_inclusive": END_DATE_INCLUSIVE},
        "parameters": {"principal_usd": PRINCIPAL_USD, "risk_usd": RISK_USD, "leverage": LEVERAGE,
                       "taker_fee_pct": TAKER_FEE_PCT, "maker_fee_pct": MAKER_FEE_PCT},
        "plan": plan,
    })
    print(json.dumps(counts, indent=1))
    return 0


def cmd_collect_window(args) -> int:
    """Rows for windowed phases (out-of-sample). Does not touch rows.json."""
    plan = load_plan(args.plan)
    rows = []
    audit_ok = True
    for phase in plan["phases"]:
        if not phase.get("window") or phase.get("out") != args.out:
            continue
        _CTX["window"], _CTX["out"] = phase["window"], phase["out"]
        cells_dir = _dirs()[0]
        for s in phase["strategies"]:
            for d in ("LONG", "SHORT"):
                for sym in phase.get("symbols") or SYMBOLS:
                    for tf in phase["timeframes"]:
                        p = cells_dir / cell_name(s, d, sym, tf)
                        if p.exists():
                            c = json.loads(p.read_text(encoding="utf-8"))
                            rows.append({k: c["row"].get(k) for k in ROW_KEYS + ["window_id"]})
                            if c.get("sql_violations") or (c.get("db_session") or {}).get("transaction_read_only") != "on":
                                audit_ok = False
                        else:
                            row = base_row(s, d, sym, tf)
                            row.update(status="NOT_RUN", reason="cell file missing: not reached")
                            rows.append({k: row.get(k) for k in ROW_KEYS + ["window_id"]})
    _CTX["window"], _CTX["out"] = None, None
    atomic_write(OUT / args.out / args.rows, rows)
    atomic_write(OUT / args.out / "_meta.json", {
        **git_meta(), "runner_sha1": hashlib.sha1(Path(__file__).read_bytes()).hexdigest(),
        "written_utc": datetime.now(timezone.utc).isoformat(), "rows": len(rows),
        "all_cells_read_only_and_no_sql_violations": audit_ok, "plan": plan})
    cnt: dict[str, int] = {}
    for r in rows:
        k = f"{r['window_id']} {r['status']}"
        cnt[k] = cnt.get(k, 0) + 1
    print(cnt)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("enumerate")
    p = sub.add_parser("probe")
    p.add_argument("--strategies", required=True)
    p.add_argument("--tfs", required=True)
    p.add_argument("--symbols", default="BTCUSDT")
    p.add_argument("--direction", default="LONG")
    p.add_argument("--budget", type=float, default=120.0)
    r = sub.add_parser("run")
    r.add_argument("--plan", required=True)
    r.add_argument("--workers", type=int, default=3)
    r.add_argument("--force", action="store_true")
    c = sub.add_parser("collect")
    c.add_argument("--plan", required=True)
    cw = sub.add_parser("collect_window")
    cw.add_argument("--plan", required=True)
    cw.add_argument("--out", required=True)
    cw.add_argument("--rows", default="rows_oos.json")
    args = ap.parse_args()
    return {"enumerate": cmd_enumerate, "probe": cmd_probe, "run": cmd_run, "collect": cmd_collect,
            "collect_window": cmd_collect_window}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
