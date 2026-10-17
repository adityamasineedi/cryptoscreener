"""Backtest-screen adapter for the ranked strategies outside the COMBO family.

CANDLE12_V2, the multi-cap strategies and the OHLCV structure experiment each
have their own engine. This module calls those engines as they are, the same
way ``scripts/strategy_rank/multicap/run_multicap_rank.py`` does, and shapes
the result as a Strategy Backtest row. It does not change a signal, a stop, a
target, a fill or a fee rule. Research only: no paper or live path.
"""

from __future__ import annotations

import asyncio
import os
import pickle
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

_OSE_VARIANTS = ("A_corrected_baseline", "B_acceptance", "C_retest", "D_faster_confirmation")
_MULTICAP_MODULES = {
    "LARGE_CAP_SWEEP_CHOCH": "large_cap_sweep_choch",
    "MID_CAP_FVG_DISCOUNT": "mid_cap_fvg_discount",
    "SMALL_CAP_VOLUME_BOS": "small_cap_volume_bos",
}

_EXEC_MULTICAP = (
    "MARKET entry at signal-bar close; exits from next bar; ATR(14)x1.5 or structural "
    "stop (farther), TP1 at 2R (first target touched exits full size); same-bar SL+TP "
    "-> SL (CONSERVATIVE); one open trade at a time; taker both sides on executed "
    "notional; no slippage in USD"
)
_EXEC_CANDLE12 = (
    "Candle-1 BOS setup, Candle-2 stop-entry at C1 high/low trigger with 0.02% adverse "
    "entry slippage; structural stop, production targets; exit fill with 0.02% adverse "
    "slippage (engine _exit_fill); same-bar SL+TP -> SL; one open trade at a time across "
    "both directions; taker both sides"
)
_EXEC_CANDLE12_V3 = (
    "CANDLE12_V3_EXEC_FIX: CANDLE12_V2 Candle-1 setup, BOS once per broken level; Candle-2 "
    "stop entry, fill = C1 high/low with 0.02% adverse slippage (inclusive), or C2 open with "
    "slippage when C2 opens beyond the trigger; structural stop; full exit at the nearest "
    "target (nearest_target_full_exit); a bar opening beyond the stop exits at its open, "
    "beyond a target at the target; same-bar SL+TP -> SL; one open trade per side (LONG-only "
    "or SHORT-only book); qty floored to the exchange step, min qty enforced; taker both "
    "sides; no funding"
)
_EXEC_OSE = (
    "first close through a confirmed swing; entry next bar open with 0.02% adverse "
    "slippage; stop = opposite confirmed swing; target 2R; 20-bar hold then close exit "
    "with slippage; gap-through-stop exits at open; same-bar -> stop; one position per "
    "symbol; taker both sides"
)

# Warm-up bars are the ones the ranking run asked for, per engine.
ENGINE_STRATEGIES: dict[str, dict[str, Any]] = {
    "CANDLE12_V2": {
        "family": "candle12",
        "name": "CANDLE12 V2",
        "directions": ("LONG", "SHORT"),
        "timeframes": ("5m", "15m", "1h"),
        "warmup_bars": 1000,
        "execution_model": _EXEC_CANDLE12,
    },
    "CANDLE12_V3_EXEC_FIX": {
        "family": "candle12_v3",
        "name": "CANDLE12 V3 EXEC FIX",
        "directions": ("LONG", "SHORT"),
        "timeframes": ("5m", "15m", "1h"),
        "warmup_bars": 1000,
        "execution_model": _EXEC_CANDLE12_V3,
    },
    **{
        sid: {
            "family": "multicap",
            "name": sid.replace("_", " "),
            "directions": ("LONG",),
            "timeframes": ("5m", "15m", "1h"),
            "warmup_bars": 1000,
            "execution_model": _EXEC_MULTICAP,
        }
        for sid in _MULTICAP_MODULES
    },
    **{
        f"OSE_{variant}": {
            "family": "ohlcv_structure_experiment",
            "name": f"OSE {variant.replace('_', ' ')}",
            "variant": variant,
            "directions": ("LONG", "SHORT"),
            "timeframes": ("15m", "1h"),
            "warmup_bars": 500,
            "execution_model": _EXEC_OSE,
        }
        for variant in _OSE_VARIANTS
    },
}
_BY_UPPER = {sid.upper(): sid for sid in ENGINE_STRATEGIES}


def engine_strategy_id(value: str | None) -> str | None:
    """Canonical id for a ranked engine strategy, or None for a COMBO id."""
    return _BY_UPPER.get(str(value or "").strip().upper())


def engine_request_error(strategy_id: str, direction: str, timeframes: Sequence[str]) -> str | None:
    """Why this engine cannot run the request, or None when it can."""
    spec = ENGINE_STRATEGIES[strategy_id]
    if direction.upper() not in spec["directions"]:
        return f"{strategy_id} runs {' or '.join(spec['directions'])} only"
    bad = [tf for tf in timeframes if tf not in spec["timeframes"]]
    if bad:
        return (
            f"{strategy_id} supports timeframes {', '.join(spec['timeframes'])}; "
            f"got {', '.join(bad)}"
        )
    return None


def _iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        ts = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    return str(value)


def _run_multicap(data: Mapping[str, Any], sizing: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    import importlib

    from app.research.combination_backtest import evaluate_candidate_trades
    from app.research.multi_cap_strategies.adapter import MultiCapStrategyAdapter
    from app.research.multi_cap_strategies.cap_filter import symbol_eligible_for_strategy
    from app.research.multi_cap_strategies.config import MultiCapResearchConfig
    from app.research.trade_fees import enrich_trades

    sid = str(data["strategy_id"])
    mod = importlib.import_module(f"app.research.multi_cap_strategies.{_MULTICAP_MODULES[sid]}")
    cfg = MultiCapResearchConfig()  # engine defaults untouched (signal + SL/TP params)
    definition = mod.DEFINITION
    candles = list(data["candles"])
    try:
        _ok, label, _why = symbol_eligible_for_strategy(
            str(data["symbol"]), data.get("market_cap"), definition.asset_group, config=cfg
        )
    except Exception:  # noqa: BLE001
        label = definition.asset_group
    cands = mod.generate_candidates(
        str(data["symbol"]),
        str(data["timeframe"]),
        candles,
        config=cfg,
        index_start=int(data.get("index_start") or 0),
    )
    trades = MultiCapStrategyAdapter(cfg).candidates_to_trades(
        cands, asset_group=label, period_label="FULL"
    )
    evaluated = evaluate_candidate_trades(
        candles,
        trades,
        handling=cfg.ambiguous_handling,
        one_open_at_a_time=cfg.one_open_at_a_time,
    )
    closed = [t.to_dict() for t in evaluated if t.outcome and t.outcome != "OPEN"]
    opened = sum(1 for t in evaluated if t.outcome == "OPEN")
    enriched = enrich_trades(closed, **sizing)
    for t in enriched:
        t["entry_time"] = t.get("signal_time")
    return enriched, opened


def _run_candle12(data: Mapping[str, Any], sizing: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    from app.research import candle12_v2 as c12
    from app.research.trade_fees import enrich_trade_execution

    direction = str(data["direction"]).upper()
    res = c12.run_candle12_v2(
        str(data["symbol"]),
        str(data["timeframe"]),
        list(data["candles"]),
        index_start=int(data.get("index_start") or 0),
        progress_every=0,
    )
    if res.get("status") not in (None, "OK") and not res.get("trades"):
        raise RuntimeError(f"candle12_v2 engine status {res.get('status')}")
    v2 = (
        c12.Candle12V2Config(
            **{k: res["v2_config"][k] for k in ("trading_fee", "entry_slippage", "exit_slippage")}
        )
        if res.get("v2_config")
        else None
    )
    # One both-direction walk (shared position lock); this row keeps one side.
    side = [t for t in res.get("trades") or [] if t.get("direction") == direction]
    closed = [
        t for t in side if t.get("outcome") not in (None, "OPEN") and t.get("exit_price") is not None
    ]
    enriched: list[dict[str, Any]] = []
    for t in closed:
        fill = c12._exit_fill(direction, float(t["exit_price"]), v2)
        snap = dict(t.get("condition_snapshot") or {})
        e = enrich_trade_execution(
            {
                **t,
                "exit_price": fill,
                "raw_exit_price": t["exit_price"],
                "entry_time": t.get("signal_time"),
                "condition_snapshot": {"entry_type": snap.get("entry_type") or "MARKET"},
            },
            **sizing,
        )
        e["engine_net_R"] = t.get("r_multiple")
        enriched.append(e)
    return enriched, len(side) - len(closed)


def _run_candle12_v3(data: Mapping[str, Any], sizing: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    from app.research.candle12_v3 import Candle12V3Config, run_candle12_v3

    # The row's direction is the whole book: per-side lock, no hidden other side.
    cfg = Candle12V3Config(
        book_side=str(data["direction"]).upper(),
        taker_fee=sizing["taker_fee"],
        risk_usd=sizing["risk_usd"],
        account_equity=sizing["account_equity"],
        leverage=sizing["leverage"],
    )
    res = run_candle12_v3(
        str(data["symbol"]),
        str(data["timeframe"]),
        list(data["candles"]),
        config=cfg,
        index_start=int(data.get("index_start") or 0),
    )
    trades = res["trades"]
    closed = [t for t in trades if t["outcome"] != "OPEN"]
    return closed, len(trades) - len(closed)


def _run_ose(data: Mapping[str, Any], sizing: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    from app.research.ohlcv_structure_experiment.engine import breaks_for_variant, run_variant
    from app.research.trade_fees import enrich_trade_execution

    direction = str(data["direction"]).upper()
    variant = ENGINE_STRATEGIES[str(data["strategy_id"])]["variant"]
    candles = list(data["candles"])
    first = int(data.get("index_start") or 0)
    events = breaks_for_variant(candles, symbol=str(data["symbol"]), variant=variant)
    events = [e for e in events if e.break_index >= first]
    records = run_variant(candles, events, variant)
    closed: list[dict[str, Any]] = []
    opened = 0
    for r in records:
        if str(r.event.direction).upper()[:1] != direction[:1]:
            continue
        if r.status == "entry_qualified":
            opened += 1  # qualified but window incomplete -> never filled
            continue
        if r.status != "filled" or r.exit_price is None or r.entry_price is None:
            continue
        entry_time = _iso(candles[r.entry_index]["time"])
        closed.append(
            enrich_trade_execution(
                {
                    "direction": direction,
                    "entry_price": r.entry_price,
                    "stop_price": r.stop_price,
                    "exit_price": r.exit_price,
                    "outcome": r.outcome,
                    "entry_type": "MARKET",
                    "signal_time": entry_time,
                    "entry_time": entry_time,
                    "exit_time": _iso(candles[r.exit_index]["time"]),
                    "entry_index": r.entry_index,
                    "exit_index": r.exit_index,
                    "holding_bars": r.exit_index - r.entry_index,
                    "engine_net_r": r.net_r,
                    "event_id": r.event.event_id,
                    "target_price": r.target_price,
                    "tp1": r.target_price,
                    "ambiguous": r.ambiguous,
                },
                **sizing,
            )
        )
    return closed, opened


_RUNNERS: dict[str, Callable[[Mapping[str, Any], dict[str, Any]], tuple[list[dict[str, Any]], int]]] = {
    "multicap": _run_multicap,
    "candle12": _run_candle12,
    "candle12_v3": _run_candle12_v3,
    "ohlcv_structure_experiment": _run_ose,
}


def run_engine_cell(data: Mapping[str, Any]) -> dict[str, Any]:
    """Run one strategy/direction/symbol/timeframe cell. Pure: candles in, trades out.

    Sizing matches the ranking run: fixed risk, leverage cap against the constant
    account equity, taker and maker rates from the request.
    """
    t0 = time.perf_counter()
    sid = str(data["strategy_id"])
    spec = ENGINE_STRATEGIES[sid]
    sizing = {
        "risk_usd": float(data["risk_usd"]),
        "taker_fee": float(data["taker_fee"]),
        "maker_fee": float(data["maker_fee"]),
        "leverage": float(data["leverage"]),
        "account_equity": float(data["principal"]),
    }
    trades, opened = _RUNNERS[spec["family"]](data, sizing)
    trades = [t for t in trades if t.get("net_pnl_usd") is not None]
    trades.sort(key=lambda t: (str(t.get("exit_time") or ""), str(t.get("entry_time") or "")))
    for i, t in enumerate(trades, 1):
        t["trade_no"] = i
        if t.get("trade_id") is None:
            t["trade_id"] = f"t{i}"
        for key in ("signal_time", "entry_time", "exit_time"):
            if key in t:
                t[key] = _iso(t[key])
    try:
        from app.research.trade_context import attach_trade_context

        attach_trade_context(
            trades,
            list(data["candles"]),
            timeframe=str(data["timeframe"]),
            symbol=str(data["symbol"]),
            # OSE and CANDLE12 V3 stamp the entry bar's open, so the bar before it
            # (the closed signal bar) is the decision bar.
            decision_offset=-1 if spec["family"] in ("ohlcv_structure_experiment", "candle12_v3") else 0,
        )
    except Exception:  # noqa: BLE001
        pass  # display-only labels never fail a backtest
    return {
        "status": "OK" if trades else "SUCCESS_EMPTY",
        "trades": trades,
        "open_trades_at_end": opened,
        "execution_model": spec["execution_model"],
        "elapsed_seconds": time.perf_counter() - t0,
    }


def build_engine_row(
    out: Mapping[str, Any],
    *,
    strategy_id: str,
    direction: str,
    symbol: str,
    timeframe: str,
    risk_usd: float,
    principal: float,
    leverage: float,
    bars_loaded: int,
    warmup_bars: int,
    candle_source: str | None,
    period_start: str | None,
    period_end: str | None,
    include_trades: bool = True,
) -> dict[str, Any]:
    """Strategy Backtest row with the same keys the COMBO rows carry."""
    from app.research.trade_fees import summarize_net_trades

    trades = list(out.get("trades") or [])
    net = summarize_net_trades(trades, risk_usd=risk_usd)
    nets = [float(t["net_pnl_usd"]) for t in trades]
    r_gross = [float(t["r_gross"]) for t in trades if t.get("r_gross") is not None]
    r_net = [float(t["r_net"]) for t in trades if t.get("r_net") is not None]
    curve_r: list[float] = []
    cum = peak = dd_r = 0.0
    for r in r_net:
        cum += r
        peak = max(peak, cum)
        dd_r = max(dd_r, peak - cum)
        curve_r.append(round(cum, 6))
    n = len(trades)
    # Display counts only. OSE outcomes are "target" / "stop" / "timeout"; a timeout is neither.
    tp_hits = sum(
        1
        for t in trades
        if str(t.get("outcome") or "").upper().startswith("TP")
        or str(t.get("outcome") or "").upper() == "TARGET"
    )
    sl_hits = sum(
        1 for t in trades if str(t.get("outcome") or "").upper() in ("SL", "STOP", "STOP_LOSS")
    )
    return {
        "combination_id": strategy_id,
        "name": ENGINE_STRATEGIES[strategy_id]["name"],
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "structure": "engine-defined",
        "sample_size": n,
        "average_R": (sum(r_gross) / len(r_gross)) if r_gross else None,
        "average_R_net": net["average_R_net"],
        "average_R_usd_basis_gross": net["average_R_usd_basis_gross"],
        "average_R_usd_basis_net": net["average_R_usd_basis_net"],
        "profit_factor_net": net["profit_factor_net"],
        "win_rate_net": net["win_rate_net"],
        "max_drawdown_usd_net": net["max_drawdown_usd_net"],
        "equity_curve_usd_net": net["equity_curve_usd_net"],
        "r_definition": net["r_definition"],
        "expectancy_R": net["average_R_net"],
        "tp1_hit_rate": (tp_hits / n) if n else None,
        "sl_rate": (sl_hits / n) if n else None,
        "profit_factor": net["profit_factor_net"],
        "max_drawdown_R": dd_r if r_net else None,
        "tp1_hits": tp_hits,
        "sl_hits": sl_hits,
        "period_start": period_start,
        "period_end": period_end,
        "r_values": r_net,
        "equity_curve_r": curve_r,
        "pnl_usd": sum(float(t["gross_pnl_usd"]) for t in trades) if trades else None,
        "pnl_usd_net": sum(nets) if trades else None,
        "fees_usd": sum(float(t.get("fee_total_usd") or 0) for t in trades),
        "risk_usd": risk_usd,
        "principal_usd": principal,
        "leverage": leverage,
        "bars_loaded": bars_loaded,
        "warmup_bars": warmup_bars,
        "bars_evaluated": max(0, bars_loaded - warmup_bars),
        "candle_source": candle_source,
        "trades": trades if include_trades else [],
        "entry_diagnostics": {},
        "open_trades_at_end": int(out.get("open_trades_at_end") or 0),
        "status": out.get("status") or ("OK" if n else "SUCCESS_EMPTY"),
        "execution_model": out.get("execution_model"),
        "engine_family": ENGINE_STRATEGIES[strategy_id]["family"],
        "engine_fill_model": out.get("execution_model"),
        "market_structure": {"analytics_enabled": False},
    }


def _cli(argv: list[str]) -> int:
    """Subprocess entry: pickle payload in, pickle result out."""
    if len(argv) != 3:
        return 2
    os.environ["RESEARCH_CPU_WORKER"] = "1"
    try:
        with Path(argv[1]).open("rb") as f:
            result = run_engine_cell(pickle.load(f))
    except Exception as exc:  # noqa: BLE001
        result = {
            "status": "ERROR",
            "reason": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-2000:],
        }
    Path(argv[2]).write_bytes(pickle.dumps(result))
    return 0


async def run_engine_cell_isolated(
    payload: dict[str, Any],
    *,
    should_cancel: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run ``run_engine_cell`` in its own interpreter so the API loop stays free.

    The engines take no cancel hook, so a cancel stops the child process.
    """
    from app.research.backtest_cpu_pool import _spawn_python_executable, _write_payload_blob

    blob_path = await asyncio.to_thread(_write_payload_blob, payload)

    def _run() -> dict[str, Any]:
        exe = _spawn_python_executable() or sys.executable
        fd, result_path = tempfile.mkstemp(prefix="bt_engine_", suffix=".pkl")
        os.close(fd)
        backend = str(Path(__file__).resolve().parents[2])
        env = os.environ.copy()
        env["PYTHONPATH"] = backend + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.Popen(
            [exe, "-m", "app.research.engine_backtest", blob_path, result_path],
            cwd=backend,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        last_beat = 0.0
        try:
            while proc.poll() is None:
                if should_cancel is not None and should_cancel():
                    proc.kill()
                    return {"status": "CANCELLED"}
                # Keeps the job watchdog fed; the engines report no bar progress.
                if progress_callback is not None and time.perf_counter() - last_beat >= 5.0:
                    try:
                        progress_callback({"phase": "ENGINE_WALK"})
                    except Exception:  # noqa: BLE001
                        pass
                    last_beat = time.perf_counter()
                time.sleep(0.05)
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            result_file = Path(result_path)
            if result_file.exists() and result_file.stat().st_size > 0:
                with result_file.open("rb") as handle:
                    return pickle.load(handle)
            raise OSError(0, f"Engine worker exited {proc.returncode} via {exe}. {stderr[-500:]}")
        finally:
            if proc.poll() is None:
                proc.kill()
            for p in (result_path, blob_path):
                try:
                    Path(p).unlink()
                except OSError:
                    pass

    return await asyncio.to_thread(_run)


async def engine_strategy_matrix(
    *,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    direction: str,
    limit: int,
    risk_usd: float,
    principal_usd: float,
    start_date: str | None,
    end_date: str | None,
    taker_fee: float,
    maker_fee: float,
    leverage: float,
    include_trades: bool = True,
    should_cancel: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """One Backtest-screen cell for a ranked engine strategy. Same payload shape
    as ``BosResearchService.strategy_matrix``."""
    from app.research.service import _load_research_candles, _market_cap

    spec = ENGINE_STRATEGIES[strategy_id]
    direction_u = direction.upper().strip()
    t0 = time.perf_counter()
    candles, eval_start, load_meta = await _load_research_candles(
        symbol,
        timeframe,
        limit=limit,
        start_date=start_date,
        end_date=end_date,
        warmup_bars=int(spec["warmup_bars"]),
        use_research_cache=False,
        tail_warmup=True,
    )
    if progress_callback is not None:
        progress_callback({"phase": "RAW_ROWS_LOADED", "rows_loaded": len(candles)})
    sym_u = str(load_meta.get("symbol") or symbol)
    tf_u = str(load_meta.get("timeframe") or timeframe)
    out = await run_engine_cell_isolated(
        {
            "strategy_id": strategy_id,
            "direction": direction_u,
            "symbol": sym_u,
            "timeframe": tf_u,
            "candles": [dict(c) for c in candles],
            "index_start": int(eval_start or 0),
            "risk_usd": float(risk_usd),
            "taker_fee": float(taker_fee),
            "maker_fee": float(maker_fee),
            "leverage": float(leverage),
            "principal": float(principal_usd),
            "market_cap": _market_cap(sym_u),
        },
        should_cancel=should_cancel,
        progress_callback=progress_callback,
    )
    base = {
        "combination_id": strategy_id,
        "combination_name": spec["name"],
        "playbook": f"{spec['name']} ({spec['family']} engine)",
        "label": "RESEARCH ONLY",
        "disclaimer": (
            "Research only. Runs the strategy's own engine with its own fill model. "
            "No paper, live or Telegram path."
        ),
        "timing": {"engine_seconds": round(time.perf_counter() - t0, 3)},
    }
    if out.get("status") in ("CANCELLED", "ERROR"):
        return {**base, "status": out["status"], "reason": out.get("reason"), "rows": []}
    first_eval = candles[int(eval_start or 0)] if len(candles) > int(eval_start or 0) else None
    row = build_engine_row(
        out,
        strategy_id=strategy_id,
        direction=direction_u,
        symbol=sym_u,
        timeframe=tf_u,
        risk_usd=float(risk_usd),
        principal=float(principal_usd),
        leverage=float(leverage),
        bars_loaded=len(candles),
        warmup_bars=int(eval_start or 0),
        candle_source=load_meta.get("candle_source"),
        period_start=_iso(first_eval.get("time")) if first_eval else None,
        period_end=_iso(candles[-1].get("time")) if candles else None,
        include_trades=include_trades,
    )
    return {**base, "status": "OK", "rows": [row]}


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
