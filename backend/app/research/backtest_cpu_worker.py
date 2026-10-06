"""Picklable CPU worker for research combination backtests.

Payload is a pickle blob path so candle dtypes/timestamps match in-process runs.
"""

from __future__ import annotations

import json
import os
import pickle
import traceback
from pathlib import Path
from typing import Any


def _assert_research_only_worker() -> None:
    if os.environ.get("CRYPTOSCREENER_FORCE_LIVE_IN_RESEARCH_WORKER") == "1":
        raise RuntimeError(
            "Research CPU worker refused: live/paper execution flag is set"
        )


def _load_payload(payload: dict[str, Any]) -> dict[str, Any]:
    path = payload.get("payload_path")
    if path:
        with Path(path).open("rb") as f:
            return pickle.load(f)
    return payload


def run_combination_backtest_worker(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ["RESEARCH_CPU_WORKER"] = "1"
    _assert_research_only_worker()

    from app.research.combination_backtest import run_combination_backtest

    data = _load_payload(payload)
    cancel_path = data.get("cancel_path")
    progress_path = data.get("progress_path")

    def should_cancel() -> bool:
        return bool(cancel_path and Path(cancel_path).exists())

    def progress_callback(progress: dict[str, Any]) -> None:
        if not progress_path:
            return
        try:
            Path(progress_path).write_text(
                json.dumps(progress, default=str), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            return

    try:
        out = run_combination_backtest(
            str(data["symbol"]),
            str(data["timeframe"]),
            list(data.get("candles") or []),
            str(data["combination_id"]),
            signal_config=data.get("signal_config"),
            research_config=data.get("research_config"),
            market_cap=data.get("market_cap"),
            direction_filter=data.get("direction_filter"),
            index_start=data.get("index_start"),
            should_cancel=should_cancel,
            candles_1h=data.get("candles_1h"),
            candles_4h=data.get("candles_4h"),
            candles_15m=data.get("candles_15m"),
            progress_callback=progress_callback,
            job_id=data.get("job_id"),
        )
        if out is None:
            raise RuntimeError("run_combination_backtest returned None")
        return out
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"research_cpu_worker_failed: {type(exc).__name__}: {exc}\n"
            f"{traceback.format_exc()}"
        ) from exc


def build_matrix_row_postprocess(data: dict[str, Any]) -> dict[str, Any]:
    """Fee enrich + market-structure attach (same process or worker).

    Strategy trade math is unchanged; this only enriches/observes.
    """
    from app.research.trade_fees import enrich_trades
    from app.research.market_structure import attach_market_structure_to_row

    out = data["backtest_out"]
    r = out.get("result") or {}
    n = int(out.get("sample_size") or r.get("sample_size") or 0)
    risk_usd = float(data["risk_usd"])
    taker_fee = float(data["taker_fee"])
    maker_fee = float(data["maker_fee"])
    lev = float(data["leverage"])
    principal = float(data["principal"])
    include_trades = bool(data.get("include_trades", True))
    direction_u = str(data.get("direction") or "LONG")
    combination_id = str(data["combination_id"])
    combo_name = str(data.get("combination_name") or combination_id)
    load_meta = data.get("load_meta") or {}
    candles = list(data.get("candles") or [])

    enriched = enrich_trades(
        out.get("trades") or [],
        risk_usd=risk_usd,
        taker_fee=taker_fee,
        maker_fee=maker_fee,
        leverage=lev,
        account_equity=principal,
        closed_only=True,
    )
    trades = enriched if include_trades else []
    net_pnls = [
        float(t["net_pnl_usd"])
        for t in enriched
        if t.get("net_pnl_usd") is not None
    ]
    gross_pnls = [
        float(t["gross_pnl_usd"])
        for t in enriched
        if t.get("gross_pnl_usd") is not None
    ]
    pnl_gross = sum(gross_pnls) if gross_pnls else (
        float(r["average_R"]) * n * float(risk_usd)
        if r.get("average_R") is not None and n
        else None
    )
    pnl_net = sum(net_pnls) if net_pnls else None
    avg_r_net = (
        (
            sum(float(t["r_net"]) for t in enriched if t.get("r_net") is not None)
            / n
        )
        if n and enriched
        else None
    )
    fee_total = sum(float(t.get("fee_total_usd") or 0) for t in enriched)
    structure = (
        "HL (bullish HH+HL)" if direction_u == "LONG" else "LH (bearish LH+LL)"
    )
    row_payload: dict[str, Any] = {
        "combination_id": combination_id,
        "name": combo_name,
        "symbol": load_meta.get("symbol"),
        "timeframe": load_meta.get("timeframe"),
        "direction": direction_u,
        "structure": structure,
        "sample_size": n,
        "average_R": r.get("average_R"),
        "average_R_net": avg_r_net,
        "expectancy_R": r.get("expectancy_R"),
        "tp1_hit_rate": r.get("tp1_hit_rate"),
        "sl_rate": r.get("sl_rate"),
        "profit_factor": r.get("profit_factor"),
        "max_drawdown_R": r.get("max_drawdown_R"),
        "tp1_hits": r.get("tp1_hits"),
        "sl_hits": r.get("sl_hits"),
        "period_start": r.get("period_start"),
        "period_end": r.get("period_end"),
        "r_values": r.get("r_values") or [],
        "equity_curve_r": r.get("equity_curve_r") or [],
        "pnl_usd": pnl_gross,
        "pnl_usd_net": pnl_net,
        "fees_usd": fee_total,
        "risk_usd": risk_usd,
        "principal_usd": principal,
        "leverage": lev,
        "bars_loaded": len(candles),
        "candle_source": load_meta.get("candle_source"),
        "trades": trades,
        # Observability side-channel from backtest walk (does not affect trades).
        "entry_diagnostics": out.get("entry_diagnostics") or {},
        "status": out.get("status") or ("OK" if n else "SUCCESS_EMPTY"),
    }

    analytics_on = bool(data.get("analytics_on"))
    c1h = data.get("candles_1h")
    c4h = data.get("candles_4h")
    c15m = data.get("candles_15m")
    tf_u = str(load_meta.get("timeframe") or "")
    idx0 = data.get("index_start")
    job_id = data.get("job_id")

    if analytics_on:
        return attach_market_structure_to_row(
            row_payload,
            setup_candles=candles,
            candles_4h=c4h,
            candles_1h=c1h if c1h else (candles if tf_u.lower() == "1h" else None),
            candles_15m=c15m,
            index_start=idx0,
            strategy_runtime_seconds=float(out.get("elapsed_seconds") or 0),
            combination_id=combination_id,
            strategy_id=None,
            run_id=job_id or "matrix_worker",
            write_artifacts=bool(data.get("write_artifacts", True)),
            analytics_enabled=True,
            enable_regime_filtering=False,
            research_only=True,
            closed_htf_policy=True,
        )
    return attach_market_structure_to_row(
        row_payload,
        setup_candles=candles,
        candles_4h=c4h,
        candles_1h=c1h,
        candles_15m=None,
        strategy_runtime_seconds=float(out.get("elapsed_seconds") or 0),
        analytics_enabled=False,
        enable_regime_filtering=False,
        research_only=True,
        closed_htf_policy=True,
        write_artifacts=False,
    )


def run_matrix_postprocess_worker(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ["RESEARCH_CPU_WORKER"] = "1"
    _assert_research_only_worker()
    data = _load_payload(payload)
    return build_matrix_row_postprocess(data)