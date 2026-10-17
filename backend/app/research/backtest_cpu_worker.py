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
            candles_5m=data.get("candles_5m"),
            candles_1d=data.get("candles_1d"),
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


CAUSAL_NET_SUMMARY_KEYS = (
    "average_R_usd_basis_gross",
    "average_R_usd_basis_net",
    "profit_factor_net",
    "win_rate_net",
    "max_drawdown_usd_net",
    "equity_curve_usd_net",
)


def apply_causal_net_summary(row: dict[str, Any], *, risk_usd: float) -> None:
    """Recompute the net curve fields from the causal trades on the row.

    Only ``CAUSAL_NET_SUMMARY_KEYS`` are replaced. ``average_R_net`` and the
    other fields set by ``apply_causal_primary_display`` are left alone.
    """
    from app.research.trade_fees import summarize_net_trades

    # ``trades`` is empty when the caller did not ask for the blotter.
    causal = row.get("trades") or row.get("causal_trades") or []
    summary = summarize_net_trades(causal, risk_usd=risk_usd)
    for key in CAUSAL_NET_SUMMARY_KEYS:
        row[key] = summary[key]


def build_matrix_row_postprocess(data: dict[str, Any]) -> dict[str, Any]:
    """Fee enrich + market-structure attach (same process or worker).

    Strategy trade math is unchanged; this only enriches/observes.
    """
    from app.research.trade_fees import enrich_trades, summarize_net_trades
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

    from app.research.sd_structure.variants import SD_STRUCTURE_FAMILY

    is_sd = combination_id in SD_STRUCTURE_FAMILY
    slip = 0.0
    if is_sd:
        from app.research.sd_structure.review import SD_SLIPPAGE_RATE

        slip = SD_SLIPPAGE_RATE
    enriched = enrich_trades(
        out.get("trades") or [],
        risk_usd=risk_usd,
        taker_fee=taker_fee,
        maker_fee=maker_fee,
        leverage=lev,
        account_equity=principal,
        closed_only=True,
        slippage_rate=slip,
        running_equity=True,
    )
    net_summary = summarize_net_trades(enriched, risk_usd=risk_usd)
    warmup_bars = int(data.get("index_start") or 0)
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
    fee_total = sum(float(t.get("fee_total_usd") or 0) for t in enriched)
    structure = (
        "HL (bullish HH+HL)"
        if direction_u == "LONG"
        else "HL+LH (both sides)"
        if direction_u == "BOTH"
        else "LH (bearish LH+LL)"
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
        "average_R_net": net_summary["average_R_net"],
        "average_R_usd_basis_gross": net_summary["average_R_usd_basis_gross"],
        "average_R_usd_basis_net": net_summary["average_R_usd_basis_net"],
        "profit_factor_net": net_summary["profit_factor_net"],
        "win_rate_net": net_summary["win_rate_net"],
        "max_drawdown_usd_net": net_summary["max_drawdown_usd_net"],
        "equity_curve_usd_net": net_summary["equity_curve_usd_net"],
        "r_definition": net_summary["r_definition"],
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
        "warmup_bars": warmup_bars,
        "bars_evaluated": max(0, len(candles) - warmup_bars),
        "candle_source": load_meta.get("candle_source"),
        "trades": trades,
        # Observability side-channel from backtest walk (does not affect trades).
        "entry_diagnostics": out.get("entry_diagnostics") or {},
        "status": out.get("status") or ("OK" if n else "SUCCESS_EMPTY"),
        "execution_model": out.get("execution_model"),
        "htf_fully_closed": out.get("htf_fully_closed"),
    }
    if str(out.get("execution_model") or "") == "DUAL_LEGACY_AND_CAUSAL" and candles:
        from app.research.causal_execution import (
            apply_causal_primary_display,
            build_execution_run_record,
            persist_execution_run_isolated,
        )
        from app.research.config import ResearchConfig

        run_cfg = ResearchConfig()
        run_cfg.execution_model = "DUAL_LEGACY_AND_CAUSAL"
        run_cfg.htf_fully_closed = bool(out.get("htf_fully_closed"))
        run_cfg.risk_usd = risk_usd
        run_cfg.taker_fee_rate = taker_fee
        run_cfg.maker_fee_rate = maker_fee
        run_cfg.leverage = lev
        symbol = str(load_meta.get("symbol") or "")
        timeframe = str(load_meta.get("timeframe") or "")
        record = build_execution_run_record(
            run_id=f"{data.get('job_id') or 'matrix_worker'}:{symbol}:{timeframe}",
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            trades=out.get("trades") or [],
            config=run_cfg,
            account_equity=principal,
        )
        row_payload["execution_comparison"] = record["reconciliation"]
        row_payload["causal_trades"] = record["causal_trades"]
        apply_causal_primary_display(
            row_payload,
            record,
            include_trades=include_trades,
        )
        # Curve, drawdown and profit factor must describe the same book as the trades.
        apply_causal_net_summary(row_payload, risk_usd=risk_usd)
        row_payload["execution_run"] = {
            key: record[key]
            for key in (
                "run_id",
                "dataset_fingerprint",
                "configuration_hash",
                "execution_configuration_hash",
                "signal_engine_version",
                "research_engine_version",
                "execution_model_version",
                "execution_model",
                "htf_fully_closed",
                "legacy_summary",
                "causal_summary",
            )
        }
        try:
            persist_execution_run_isolated(record)
        except FileExistsError:
            row_payload["execution_run"]["persist_status"] = "ALREADY_RECORDED"
    if is_sd:
        from app.research.sd_structure.review import review_closed_trades

        row_payload["sd_review"] = review_closed_trades(enriched)

    analytics_on = bool(data.get("analytics_on"))
    c1h = data.get("candles_1h")
    c4h = data.get("candles_4h")
    c15m = data.get("candles_15m")
    tf_u = str(load_meta.get("timeframe") or "")
    idx0 = data.get("index_start")
    job_id = data.get("job_id")

    # Display-only labels on each trade (trend, volume, zone at the signal bar).
    try:
        from app.research.trade_context import attach_trade_context

        attach_trade_context(
            row_payload.get("trades") or [],
            candles,
            timeframe=tf_u,
            symbol=str(load_meta.get("symbol") or ""),
        )
    except Exception:  # noqa: BLE001
        pass

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


def _cli(argv: list[str]) -> int:
    """Subprocess entry. Avoids the Windows process-pool semaphore."""
    if len(argv) != 3:
        return 2
    payload_path, result_path = argv[1], argv[2]
    try:
        result = run_combination_backtest_worker({"payload_path": payload_path})
    except Exception as exc:  # noqa: BLE001
        result = {
            "status": "ERROR",
            "reason": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-2000:],
        }
    Path(result_path).write_bytes(pickle.dumps(result))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_cli(sys.argv))