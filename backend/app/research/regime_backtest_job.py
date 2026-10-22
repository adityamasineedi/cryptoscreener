"""Background regime-book backtest for the Backtest tab.

Uses ``passed_regime_backtest`` (``RegimeClock``, ``run_symbol``,
``build_result``: the same calls ``run_regime_book`` makes, one symbol at a
time). Entry, stop, target, exits, sizing, and fees stay in that module. This
job only chooses the window, loads candles, and keeps one run at a time.

Fill book and sides
    Trades are priced on the next-open fill book (``CAUSAL_NEXT_OPEN``), the
    book the Backtest screen shows for COMBO rows: the sizing step gets each
    symbol's 1h candles (the same series its walk used, warm-up included).
    ``directions`` limits the run to the LONG or the SHORT rows of the passed
    book; the state reports which rows were in scope.

Per-coin allowlist
    ``apply_symbol_allowlist`` (default True) applies
    ``passed_symbol_allowlist``: on a coin with an entry only the sides that
    passed that coin's test open, as on paper. The allowlist is read once at
    start and handed to every worker step, and ``symbol_allowlist`` in the
    state names the per-coin filters the run applied. False runs every side
    on every coin, for the per-coin pass/fail study.

Window
    The book runs on 1h bars only. A request names a timeframe; anything but
    1h is rejected, never reinterpreted. Two modes, as on the screen:
      * dates    start_date / end_date are UTC calendar days (end inclusive).
      * lookback ``limit`` is a count of 1h bars ending at the seal (2026-07-14 23:00,
        app.services.seal), not at the latest stored bar.
    Warm-up bars are loaded in front of the window and are never traded: each
    symbol's walk starts at the loader's ``eval_start``.
    ``MAX_EVAL_BARS`` caps the evaluated 1h bars per symbol. When it applies
    the latest ``MAX_EVAL_BARS`` bars are kept and the payload says so
    (``window.requested`` vs ``window.evaluated``, ``window.cap``).

Execution
    Candles load on the event loop (async DB). Each symbol's walk, and the
    final sizing step, run in a child interpreter (``python -m
    app.research.regime_backtest_job``), so the API loop stays free. Progress
    comes back through a small JSON file; cancel kills the child.
"""

from __future__ import annotations

import asyncio
import json
import os
import pickle
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.research.backtest_ui_config import BACKTEST_TAB_MAKER_FEE_PCT, BACKTEST_TAB_TAKER_FEE_PCT
from app.research.passed_cap_book import PRINCIPAL_USD, cap_symbols
from app.research.passed_regime_backtest import (
    DEFAULT_PNL_MODEL,
    normalize_directions,
    normalize_pnl_model,
    sides_in_scope,
)
from app.services import seal

RUN_TIMEFRAME = "1h"
# Fees the book charges (owner D144 / D202): passed explicitly to the sizing step, so the
# shared passed_regime_backtest defaults (0.04% taker, as scripts/run_passed_cap_book.py) stay.
TAB_TAKER_FEE = BACKTEST_TAB_TAKER_FEE_PCT / 100.0
TAB_MAKER_FEE = BACKTEST_TAB_MAKER_FEE_PCT / 100.0
# Per-symbol cap on evaluated 1h bars. Same as the screen's API bar cap.
MAX_EVAL_BARS = 20_000
DEFAULT_LIMIT = 1440
# Warm-up in front of the window. Never traded.
_WARMUP_1H = 80
# Bars a symbol needs (warm-up included) before its walk can emit anything.
_MIN_BARS = 80
# BTC daily bars loaded in front of the window for the regime clock.
_BTC_DAILY_WARMUP = 500

ProgressFn = Callable[[dict[str, Any]], None]
CancelFn = Callable[[], bool]
Runner = Callable[[dict[str, Any], CancelFn, ProgressFn], Awaitable[dict[str, Any]]]

_lock = asyncio.Lock()
_task: asyncio.Task[None] | None = None
_cancel_flags: dict[str, bool] = {}


def _fixed_config() -> dict[str, Any]:
    """What the book uses regardless of the screen's risk and fee fields."""
    from app.research.passed_regime_backtest import LEVERAGE

    return {
        "principal_usd": PRINCIPAL_USD,
        "taker_fee_pct": round(TAB_TAKER_FEE * 100.0, 10),
        "maker_fee_pct": round(TAB_MAKER_FEE * 100.0, 10),
        "leverage": LEVERAGE,
        "risk_rule": "passed_risk_percent: 2% large / 1% mid of principal, halved when the side is against the BTC daily trend",
        "fill_model": (
            "CAUSAL_NEXT_OPEN: signals from the existing combination backtest (research_config=None), "
            "filled at the next bar's open with the causal model's slippage"
        ),
    }


def _idle_state() -> dict[str, Any]:
    return {
        "job_id": None,
        "status": "idle",
        "phase": "",
        "current": "",
        "symbols": [],
        "timeframe": RUN_TIMEFRAME,
        "limit": 0,
        "window": None,
        "pnl_model": DEFAULT_PNL_MODEL,
        "execution_model_version": None,
        "directions": [],
        "sides_in_scope": [],
        "apply_symbol_allowlist": True,
        "symbol_allowlist": None,
        "total_symbols": 0,
        "done_symbols": 0,
        "walks": 0,
        "started_at": None,
        "finished_at": None,
        "elapsed_seconds": None,
        "summary": None,
        "trades": [],
        "skipped_symbols": [],
        "error": None,
        "_t0": 0.0,
    }


_state: dict[str, Any] = _idle_state()


def _progress_percent(job: dict[str, Any]) -> float:
    status = job.get("status")
    if status == "done":
        return 100.0
    total = int(job.get("total_symbols") or 0)
    if not total:
        return 0.0
    done = int(job.get("done_symbols") or 0)
    intra = {"loading": 0.1, "running": 0.5}.get(str(job.get("phase") or ""), 0.0)
    if job.get("phase") == "sizing":
        return 99.0
    pct = 100.0 * min(1.0, (done + (intra if done < total else 0.0)) / total)
    return round(min(pct, 99.0), 1)


def _public(job: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in job.items() if not k.startswith("_")}
    if job.get("status") == "running" and job.get("_t0"):
        out["elapsed_seconds"] = round(time.perf_counter() - job["_t0"], 3)
    pct = _progress_percent(job)
    out["pct"] = pct
    out["progress_percent"] = pct
    # Same names the single-strategy job uses, so the screen shares its progress view.
    out["total_cells"] = job.get("total_symbols") or 0
    out["done_cells"] = job.get("done_symbols") or 0
    out["timeframes"] = [RUN_TIMEFRAME]
    out["fixed_config"] = _fixed_config()
    return out


def status() -> dict[str, Any]:
    return _public(_state)


def _scope_rows(directions: Any) -> list[dict[str, str]]:
    return [
        {"strategy_id": cid, "timeframe": tf, "direction": side}
        for cid, tf, side in sides_in_scope(directions)
    ]


def run_plan(directions: Any = None) -> dict[str, Any]:
    """What a run with these directions executes, from the same code path the run takes.

    ``sides_in_scope`` is what ``passed_regime_backtest.run_symbol`` walks; ``fixed_config``
    is the sizing and fees the sizing step uses. Read-only.
    """
    scope = normalize_directions(directions)
    return {
        "directions": list(scope),
        "timeframe": RUN_TIMEFRAME,
        "sides_in_scope": _scope_rows(scope),
        "fixed_config": _fixed_config(),
        "sealed_through": seal.SEAL_LAST_DAY,
    }


def _allowlist_view(allowlist: Any, symbols: list[str], scope: Any, applied: bool) -> dict[str, Any]:
    """The per-coin filters a run applies: coins with an entry, sides in scope only."""
    if not applied or allowlist is None:
        return {"applied": False, "symbols": {}}
    view = allowlist.describe(symbols, sides_in_scope(scope))
    return {"applied": True, "symbols": view["symbols"]}


def slim_result(result: dict[str, Any], *, trade_limit: int = 40) -> dict[str, Any]:
    """Summary plus the latest closed trades. Drops the full signal list."""
    summary = dict(result.get("summary") or {})
    by_strategy = summary.get("by_strategy") or {}
    summary["by_strategy"] = sorted(
        by_strategy.values(),
        key=lambda row: float(row.get("pnl_usd_net") or 0.0),
        reverse=True,
    )
    trades = [
        {
            "symbol": t.get("symbol"),
            "strategy_id": t.get("strategy_id"),
            "direction": t.get("direction"),
            "trend_at_entry": t.get("trend_at_entry"),
            "signal_time": t.get("signal_time"),
            "exit_time": t.get("exit_time"),
            "outcome": t.get("outcome"),
            "net_pnl_usd": t.get("net_pnl_usd"),
            "r_net": t.get("r_net"),
        }
        for t in (result.get("trades") or [])
        if t.get("net_pnl_usd") is not None
    ]
    trades.sort(key=lambda row: str(row.get("exit_time") or ""))
    return {
        "summary": summary,
        "trades": trades[-trade_limit:],
        "pnl_model": result.get("pnl_model"),
        "execution_model_version": result.get("execution_model_version"),
        "directions": result.get("directions"),
        "sides_in_scope": result.get("sides_in_scope"),
        "symbol_allowlist": result.get("symbol_allowlist"),
    }


# --- request ---------------------------------------------------------------


class RequestError(ValueError):
    pass


def _day(value: Any, name: str) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise RequestError(f"{name} must be a UTC day YYYY-MM-DD, got {value!r}") from exc


def resolve_window(
    *,
    limit: Any = None,
    timeframe: Any = None,
    start_date: Any = None,
    end_date: Any = None,
) -> dict[str, Any]:
    """The window a request asks for. Raises ``RequestError`` on anything the book cannot run."""
    tf = str(timeframe or RUN_TIMEFRAME).strip().lower()
    if tf != RUN_TIMEFRAME:
        raise RequestError(
            f"The regime book runs on {RUN_TIMEFRAME} bars only; timeframe {tf} is not supported."
        )
    start = _day(start_date, "start_date")
    end = _day(end_date, "end_date")
    if start and end and end < start:
        raise RequestError(f"end_date {end} is before start_date {start}.")
    if start or end:
        return {
            "mode": "dates",
            "timeframe": RUN_TIMEFRAME,
            "start_date": start,
            "end_date": end,
            "limit": None,
            "max_eval_bars": MAX_EVAL_BARS,
        }
    try:
        bars = int(limit if limit not in (None, "") else DEFAULT_LIMIT)
    except (TypeError, ValueError) as exc:
        raise RequestError(f"limit must be a whole number of {RUN_TIMEFRAME} bars, got {limit!r}") from exc
    if bars < 1:
        raise RequestError(f"limit must be at least 1 {RUN_TIMEFRAME} bar, got {bars}.")
    return {
        "mode": "lookback",
        "timeframe": RUN_TIMEFRAME,
        "start_date": None,
        "end_date": None,
        "limit": bars,
        "max_eval_bars": MAX_EVAL_BARS,
    }


# --- loading (event loop, async DB) ----------------------------------------


def _apply_cap(candles: list[dict[str, Any]], eval_start: int) -> tuple[list[dict[str, Any]], int, bool]:
    """Keep the latest MAX_EVAL_BARS evaluated bars and their warm-up."""
    if len(candles) - eval_start <= MAX_EVAL_BARS:
        return candles, eval_start, False
    first = len(candles) - MAX_EVAL_BARS
    keep_from = max(0, first - _WARMUP_1H)
    return candles[keep_from:], first - keep_from, True


def _bar_iso(candle: dict[str, Any] | None) -> str | None:
    if not candle:
        return None
    from app.research.query_utils import candle_timestamp

    ts = candle_timestamp(candle)
    return ts.isoformat() if ts is not None else None


async def _load_book(symbol: str, window: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """1h series plus the HTF/LTF series the walks read, for one symbol and window."""
    from app.research.service import _load_htf_candles_for_combo, _load_research_candles

    dated = window["mode"] == "dates"
    start_date = window["start_date"] if dated else None
    end_date = window["end_date"] if dated else None
    limit = MAX_EVAL_BARS if dated else min(int(window["limit"]), MAX_EVAL_BARS)
    candles, eval_start, meta = await _load_research_candles(
        symbol,
        RUN_TIMEFRAME,
        limit=limit,
        start_date=start_date,
        end_date=end_date,
        warmup_bars=_WARMUP_1H,
        use_research_cache=False,
        tail_warmup=True,
    )
    candles, eval_start, capped = _apply_cap(list(candles), int(eval_start or 0))
    # A lookback longer than the cap was loaded at the cap: that is a cap too.
    capped = capped or (not dated and int(window["limit"]) > MAX_EVAL_BARS)
    evaluated = len(candles) - eval_start
    info = {
        "bars_loaded": len(candles),
        "warmup_bars": eval_start,
        "bars_evaluated": evaluated,
        "period_start": _bar_iso(candles[eval_start]) if evaluated > 0 else None,
        "period_end": _bar_iso(candles[-1]) if evaluated > 0 else None,
        # Run record (owner D202): first and last 1h bar loaded, warm-up included.
        "data_first_bar": _bar_iso(candles[0]) if candles else None,
        "data_last_bar": _bar_iso(candles[-1]) if candles else None,
        "capped": capped,
        "candle_source": meta.get("candle_source"),
    }
    if evaluated <= 0 or len(candles) < _MIN_BARS:
        return None, info
    span = len(candles)
    _, c4h = await _load_htf_candles_for_combo(
        symbol,
        require_htf=True,
        setup_timeframe=RUN_TIMEFRAME,
        limit=span,
        start_date=start_date,
        end_date=end_date,
        warmup_bars=_WARMUP_1H,
        use_research_cache=False,
    )
    c15, _, _ = await _load_research_candles(
        symbol,
        "15m",
        limit=max(span * 4, 400),
        start_date=start_date,
        end_date=end_date,
        warmup_bars=_WARMUP_1H * 4,
        use_research_cache=False,
        tail_warmup=True,
    )
    c1d, _, _ = await _load_research_candles(
        symbol,
        "1d",
        limit=max(span // 24 + 2, 120),
        start_date=start_date,
        end_date=end_date,
        warmup_bars=120 if dated else 20,
        use_research_cache=False,
        tail_warmup=True,
    )
    book = {
        "candles": candles,
        "eval_start": eval_start,
        "meta": meta,
        "c1h": None,
        "c4h": c4h,
        "c15": c15,
        "c1d": c1d,
    }
    return book, info


async def _load_btc_daily(window: dict[str, Any]) -> list[dict[str, Any]]:
    from app.research.service import _load_research_candles

    if window["mode"] == "dates":
        daily, _, _ = await _load_research_candles(
            "BTCUSDT",
            "1d",
            limit=MAX_EVAL_BARS // 24 + _BTC_DAILY_WARMUP,
            start_date=window["start_date"],
            end_date=window["end_date"],
            warmup_bars=_BTC_DAILY_WARMUP,
            use_research_cache=False,
        )
        return list(daily)
    span_days = (min(int(window["limit"]), MAX_EVAL_BARS) + _WARMUP_1H) // 24 + 2
    daily, _, _ = await _load_research_candles(
        "BTCUSDT",
        "1d",
        limit=span_days + _BTC_DAILY_WARMUP,
        warmup_bars=40,
        use_research_cache=False,
        tail_warmup=True,
    )
    return list(daily)


# --- CPU work (child interpreter) ------------------------------------------


def _execute(
    payload: dict[str, Any],
    *,
    should_cancel: CancelFn | None = None,
    progress: ProgressFn | None = None,
    backtest_fn: Callable[..., dict[str, Any]] | None = None,
    postprocess_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One CPU step. Same calls, in the same order, as ``run_regime_book``.

    ``pnl_model``, ``directions``, ``apply_symbol_allowlist`` and
    ``symbol_allowlist`` ride in the payload (defaults: the next-open book,
    both directions, the committed per-coin allowlist applied). The ``finish`` step needs
    ``candles_by_symbol`` (symbol -> the 1h series that symbol's walk used),
    as ``run_regime_book`` passes it, so the next-open fill can be priced.
    """
    from app.research.passed_regime_backtest import RegimeClock, build_result, run_symbol
    from app.signals.config import SignalConfig

    clock = RegimeClock(payload["btc_daily"], SignalConfig())
    model = normalize_pnl_model(payload.get("pnl_model") or DEFAULT_PNL_MODEL)
    directions = normalize_directions(payload.get("directions"))
    apply_allowlist = bool(payload.get("apply_symbol_allowlist", True))
    op = payload["op"]
    if op == "symbol":
        return run_symbol(
            payload["symbol"],
            payload["book"],
            clock,
            signal_config=None,
            research_config=None,
            market_cap=None,
            backtest_fn=backtest_fn,
            should_cancel=should_cancel,
            progress=progress,
            pnl_model=model,
            directions=directions,
            apply_symbol_allowlist=apply_allowlist,
            # The allowlist the parent read at start (None: the committed file).
            symbol_allowlist=payload.get("symbol_allowlist"),
        )
    if op == "finish":
        return build_result(
            payload["symbol_results"],
            clock,
            # D144 costs, passed explicitly (passed_regime_backtest's defaults are not edited).
            taker_fee=float(payload.get("taker_fee", TAB_TAKER_FEE)),
            maker_fee=float(payload.get("maker_fee", TAB_MAKER_FEE)),
            postprocess_fn=postprocess_fn,
            pnl_model=model,
            candles_by_symbol=payload.get("candles_by_symbol"),
            directions=directions,
        )
    raise ValueError(f"unknown op {op!r}")


def _cli(argv: list[str]) -> int:
    """Child entry: pickle payload in, pickle result out, progress as JSON."""
    if len(argv) != 4:
        return 2
    os.environ["RESEARCH_CPU_WORKER"] = "1"
    progress_path = Path(argv[3])
    last = [0.0]

    def progress(event: dict[str, Any]) -> None:
        now = time.perf_counter()
        if now - last[0] < 0.25:
            return
        last[0] = now
        tmp = progress_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(event, default=str), encoding="utf-8")
            os.replace(tmp, progress_path)
        except OSError:
            pass

    try:
        with Path(argv[1]).open("rb") as handle:
            payload = pickle.load(handle)
        result = _execute(payload, progress=progress)
    except Exception as exc:  # noqa: BLE001
        result = {
            "status": "ERROR",
            "reason": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-2000:],
        }
    Path(argv[2]).write_bytes(pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL))
    return 0


async def _run_child(payload: dict[str, Any], should_cancel: CancelFn, on_progress: ProgressFn) -> dict[str, Any]:
    """Run ``_execute(payload)`` in its own interpreter. A cancel kills it."""
    from app.research.backtest_cpu_pool import (
        _child_stderr_file,
        _read_child_stderr,
        _spawn_python_executable,
        _unlink_child_file,
        _write_payload_blob,
    )

    blob_path = await asyncio.to_thread(_write_payload_blob, payload)

    def _run() -> dict[str, Any]:
        exe = _spawn_python_executable() or sys.executable
        fd, result_path = tempfile.mkstemp(prefix="regime_bt_", suffix=".pkl")
        os.close(fd)
        fd, progress_path = tempfile.mkstemp(prefix="regime_bt_", suffix=".json")
        os.close(fd)
        backend = str(Path(__file__).resolve().parents[2])
        env = os.environ.copy()
        env["PYTHONPATH"] = backend + os.pathsep + env.get("PYTHONPATH", "")
        err_fd, stderr_path = _child_stderr_file("regime_bt_")
        try:
            proc = subprocess.Popen(
                [exe, "-m", "app.research.regime_backtest_job", blob_path, result_path, progress_path],
                cwd=backend,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=err_fd,
            )
        except BaseException:
            os.close(err_fd)
            _unlink_child_file(stderr_path)
            raise
        os.close(err_fd)
        seen = 0.0
        try:
            while proc.poll() is None:
                if should_cancel():
                    proc.kill()
                    return {"status": "CANCELLED"}
                try:
                    stamp = os.path.getmtime(progress_path)
                    if stamp != seen:
                        seen = stamp
                        text = Path(progress_path).read_text(encoding="utf-8")
                        if text:
                            on_progress(json.loads(text))
                except (OSError, ValueError):
                    pass
                time.sleep(0.05)
            result_file = Path(result_path)
            if result_file.exists() and result_file.stat().st_size > 0:
                with result_file.open("rb") as handle:
                    return pickle.load(handle)
            stderr = _read_child_stderr(stderr_path)
            raise OSError(0, f"Regime worker exited {proc.returncode} via {exe}. {stderr[-500:]}")
        finally:
            if proc.poll() is None:
                proc.kill()
            for path in (result_path, progress_path, blob_path):
                try:
                    Path(path).unlink()
                except OSError:
                    pass
            # A just-killed child may still hold it briefly.
            _unlink_child_file(stderr_path, retry_seconds=3.0)

    return await asyncio.to_thread(_run)


# Swapped in tests for an in-process runner.
_runner: Runner = _run_child


# --- job -------------------------------------------------------------------


def _finish(job: dict[str, Any], status: str, *, error: str | None = None) -> None:
    job["status"] = status
    job["phase"] = status
    job["current"] = ""
    job["error"] = error
    job["finished_at"] = datetime.now(timezone.utc).isoformat()
    if job.get("_t0"):
        job["elapsed_seconds"] = round(time.perf_counter() - job["_t0"], 3)


def _evaluated(per_symbol: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in per_symbol.values() if row.get("bars_evaluated")]
    starts = [row["period_start"] for row in rows if row.get("period_start")]
    ends = [row["period_end"] for row in rows if row.get("period_end")]
    span = seal.merge_spans(
        [{"first_bar": r.get("data_first_bar"), "last_bar": r.get("data_last_bar")} for r in per_symbol.values()]
    )
    return {
        "period_start": min(starts) if starts else None,
        "period_end": max(ends) if ends else None,
        "data_first_bar": span["first_bar"],
        "data_last_bar": span["last_bar"],
        "sealed_through": seal.SEAL_LAST_DAY,
        "bars_loaded": sum(int(row.get("bars_loaded") or 0) for row in rows),
        "warmup_bars": sum(int(row.get("warmup_bars") or 0) for row in rows),
        "bars_evaluated": sum(int(row.get("bars_evaluated") or 0) for row in rows),
        "symbols": per_symbol,
    }


async def _run(job: dict[str, Any]) -> None:
    """The run inside the seal (owner D202): no load reaches a bar after 2026-07-14."""
    with seal.sealed_scope():
        await _run_sealed(job)


async def _run_sealed(job: dict[str, Any]) -> None:
    job_id = job["job_id"]

    def cancelled() -> bool:
        return bool(_cancel_flags.get(job_id))

    def on_progress(event: dict[str, Any]) -> None:
        job["walks_in_symbol"] = int(event.get("walks") or 0)
        side = " ".join(str(event.get(key) or "") for key in ("strategy_id", "direction")).strip()
        if side:
            job["current"] = f"{event.get('symbol') or ''} {job['done_symbols'] + 1}/{job['total_symbols']} · {side}"

    window = job["window"]
    symbols = list(job["symbols"])
    per_symbol: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    # Each symbol's 1h series, as its walk saw it: the sizing step fills the
    # next-open trades on these bars (run_regime_book passes the same).
    candles_by_symbol: dict[str, list[dict[str, Any]]] = {}
    skipped: list[str] = []
    try:
        job["phase"] = "loading"
        job["current"] = "BTCUSDT 1d regime"
        btc_daily = await _load_btc_daily(window)
        job["window"]["btc_daily"] = {
            "first_bar": _bar_iso(btc_daily[0]) if btc_daily else None,
            "last_bar": _bar_iso(btc_daily[-1]) if btc_daily else None,
        }
        for index, symbol in enumerate(symbols, start=1):
            if cancelled():
                break
            job["phase"] = "loading"
            job["current"] = f"{symbol} {index}/{len(symbols)}"
            book, info = await _load_book(symbol, window)
            per_symbol[symbol] = info
            job["window"]["evaluated"] = _evaluated(per_symbol)
            if info.get("capped"):
                job["window"]["cap"]["applied"] = True
                job["window"]["cap"]["symbols"].append(symbol)
            if book is None:
                skipped.append(symbol)
                job["skipped_symbols"] = list(skipped)
                job["done_symbols"] = index
                continue
            if cancelled():
                break
            job["phase"] = "running"
            job["walks_in_symbol"] = 0
            result = await _runner(
                {
                    "op": "symbol",
                    "symbol": symbol,
                    "book": book,
                    "btc_daily": btc_daily,
                    "pnl_model": job["pnl_model"],
                    "directions": job["directions"],
                    "apply_symbol_allowlist": job["apply_symbol_allowlist"],
                    "symbol_allowlist": job.get("_symbol_allowlist"),
                },
                cancelled,
                on_progress,
            )
            series = list(book.get("candles") or [])
            del book
            status = str(result.get("status") or "")
            if status == "ERROR":
                raise RuntimeError(f"{symbol}: {result.get('reason')}")
            if status == "CANCELLED" or cancelled():
                break
            job["walks"] = int(job.get("walks") or 0) + int(result.get("walks") or 0)
            results.append(result)
            candles_by_symbol[str(symbol).upper()] = series
            job["done_symbols"] = index
        if cancelled():
            _finish(job, "cancelled")
            return
        if not results:
            _finish(job, "error", error=f"No symbol had {RUN_TIMEFRAME} candles in the requested window.")
            return
        job["phase"] = "sizing"
        job["current"] = "regime book"
        final = await _runner(
            {
                "op": "finish",
                "symbol_results": results,
                "btc_daily": btc_daily,
                "taker_fee": TAB_TAKER_FEE,
                "maker_fee": TAB_MAKER_FEE,
                "pnl_model": job["pnl_model"],
                "directions": job["directions"],
                "candles_by_symbol": candles_by_symbol,
            },
            cancelled,
            on_progress,
        )
        if str(final.get("status") or "") == "ERROR":
            raise RuntimeError(str(final.get("reason")))
        if final.get("status") == "CANCELLED" or cancelled():
            _finish(job, "cancelled")
            return
        slim = slim_result(final)
        job["summary"] = slim["summary"]
        job["trades"] = slim["trades"]
        job["pnl_model"] = slim["pnl_model"]
        job["execution_model_version"] = slim["execution_model_version"]
        job["directions"] = slim["directions"]
        job["sides_in_scope"] = slim["sides_in_scope"]
        if slim.get("symbol_allowlist") is not None:
            job["symbol_allowlist"] = slim["symbol_allowlist"]
        _finish(job, "done")
    except Exception as exc:  # noqa: BLE001
        _finish(job, "error", error=f"{type(exc).__name__}: {exc}")
    finally:
        job["skipped_symbols"] = list(skipped)
        _cancel_flags.pop(job_id, None)


async def start(
    symbols: list[str],
    limit: Any = None,
    directions: Any = None,
    *,
    timeframe: Any = None,
    start_date: Any = None,
    end_date: Any = None,
    apply_symbol_allowlist: bool = True,
) -> dict[str, Any]:
    """Start one regime-book run. A run already in progress is left alone.

    ``directions`` is None (every passed row), ``["LONG"]`` or ``["SHORT"]``.
    ``apply_symbol_allowlist`` applies the per-coin allowlist (default True).
    """
    global _task, _state
    allowed = set(cap_symbols())
    chosen: list[str] = []
    for sym in (str(s).strip().upper() for s in symbols):
        if sym in allowed and sym not in chosen:
            chosen.append(sym)
    if not chosen:
        return {"status": "REJECTED", "error": "No large-cap or mid-cap symbols."}
    try:
        window = resolve_window(limit=limit, timeframe=timeframe, start_date=start_date, end_date=end_date)
    except RequestError as exc:
        return {"status": "REJECTED", "error": str(exc)}
    # Seal (owner D202): an explicit day after 2026-07-14 is refused (the route answers 400 first).
    try:
        seal.check_request_dates(window["start_date"], window["end_date"])
    except seal.SealedRangeError as exc:
        return {"status": "REJECTED", "error": str(exc), "error_code": exc.code}
    try:
        scope = normalize_directions(directions)
    except ValueError as exc:
        return {"status": "REJECTED", "error": str(exc)}
    apply_allowlist = bool(apply_symbol_allowlist)
    allowlist = None
    if apply_allowlist:
        from app.research.passed_symbol_allowlist import AllowlistError, get_allowlist

        try:
            allowlist = get_allowlist()
        except AllowlistError as exc:
            return {"status": "REJECTED", "error": f"Per-coin allowlist: {exc}"}
    async with _lock:
        if _task is not None and not _task.done():
            return {"status": "ALREADY_RUNNING", "job": status()}
        job = _idle_state()
        job_id = uuid.uuid4().hex[:12]
        window = {
            **window,
            "requested": {
                "mode": window["mode"],
                "timeframe": window["timeframe"],
                "start_date": window["start_date"],
                "end_date": window["end_date"],
                "limit": window["limit"],
            },
            "evaluated": None,
            "cap": {"max_eval_bars": MAX_EVAL_BARS, "applied": False, "symbols": []},
        }
        job.update(
            {
                "job_id": job_id,
                "status": "running",
                "phase": "starting",
                "symbols": chosen,
                "limit": window["limit"] or 0,
                "window": window,
                "pnl_model": DEFAULT_PNL_MODEL,
                "directions": list(scope),
                "sides_in_scope": _scope_rows(scope),
                "apply_symbol_allowlist": apply_allowlist,
                "symbol_allowlist": _allowlist_view(allowlist, chosen, scope, apply_allowlist),
                "_symbol_allowlist": allowlist,
                "total_symbols": len(chosen),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "_t0": time.perf_counter(),
            }
        )
        _cancel_flags[job_id] = False
        _state = job
        _task = asyncio.create_task(_run(job), name=f"regime_backtest_{job_id}")
    return {"status": "STARTED", "job": status()}


async def cancel(wait_seconds: float = 5.0) -> dict[str, Any]:
    """Stop the running job. Waits briefly so the screen sees it settled."""
    job = _state
    task = _task
    if job.get("status") != "running" or not job.get("job_id"):
        return {"status": "NOT_RUNNING", "job": status()}
    _cancel_flags[job["job_id"]] = True
    job["phase"] = "cancelling"
    if task is not None and not task.done():
        await asyncio.wait({task}, timeout=wait_seconds)
    if job.get("status") == "running":
        _finish(job, "cancelled")
    return {"status": "CANCELLED", "job": status()}


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
