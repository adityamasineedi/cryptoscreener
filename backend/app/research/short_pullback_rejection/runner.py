"""Orchestrate SHORT pullback-rejection research (research-only)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research.short_pullback_rejection.constants import (
    DEFAULT_PULLBACK_REJECTION_WINDOWS,
    DEFAULT_THRESHOLDS,
    DEFAULT_UNIVERSE,
    SAFETY_STAMPS,
    STOP_BUFFER_025,
    STOP_BUFFER_BY_VARIANT,
    STRATEGY_FINGERPRINT_TEXT,
    STRATEGY_ID,
    TP_NEAREST_SUPPORT,
    TP_R_BY_VARIANT,
)
from app.research.short_pullback_rejection.report import build_final_report
from app.research.short_pullback_rejection.signals import generate_signals_for_symbol
from app.research.short_pullback_rejection.simulation import simulate_signals
from app.research.short_pullback_rejection.validation import (
    assert_disjoint,
    by_regime,
    by_rejection_type,
    classify_research,
    distribution,
    filter_trades_to_window,
    forensic_direct_replay,
    reconcile_trades,
    rejection_type_counts,
    summarize_trades,
    windows_from_dict,
)
from app.research.short_pullback_rejection.variants import (
    STOP_VARIANT_IDS,
    TP_VARIANT_IDS,
)
from app.research.short_research_windows import filter_candles_to_window as filter_ohlcv


def _load_symbol_candles(
    symbol: str,
    *,
    start: str,
    end: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Lazy import avoids pulling database/logging stack into unit tests.
    from app.research.short_research_diagnostics.runner import load_symbol_candles

    return load_symbol_candles(symbol, start=start, end=end)


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def dataset_hash(symbols: Sequence[str], window: Mapping[str, Any]) -> str:
    payload = {
        "symbols": sorted(symbols),
        "window": dict(window),
        "strategy_id": STRATEGY_ID,
        "source": "ohlcv_v1",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def configuration_hash(
    *,
    thresholds: Mapping[str, Any],
    window: Mapping[str, Any],
    symbols: Sequence[str],
    stop_variant: str,
    tp_variant: str,
) -> str:
    payload = {
        "strategy_id": STRATEGY_ID,
        "thresholds": dict(thresholds),
        "window": dict(window),
        "symbols": sorted(symbols),
        "stop_variant": stop_variant,
        "tp_variant": tp_variant,
        "fingerprint": STRATEGY_FINGERPRINT_TEXT,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def default_report_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "reports" / "short_research"


def _slim_variant_summary(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    s = summarize_trades(trades)
    return {k: v for k, v in s.items() if k != "closed_trades"}


def run_short_pullback_rejection_research(
    *,
    symbols: Sequence[str] | None = None,
    windows: Mapping[str, Any] | None = None,
    thresholds: Mapping[str, float | int] | None = None,
    stop_variant: str = STOP_BUFFER_025,
    tp_variant: str = TP_NEAREST_SUPPORT,
    run_variant_grid: bool = True,
    data_start: str = "2024-01-01",
    data_end: str = "2025-06-30",
    report_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute research-only pullback-rejection batch. Never paper / Telegram / v1."""
    syms = [s.upper() for s in (symbols or DEFAULT_UNIVERSE)]
    th = {**DEFAULT_THRESHOLDS, **dict(thresholds or {})}
    win = windows_from_dict(windows or DEFAULT_PULLBACK_REJECTION_WINDOWS)
    win_check = assert_disjoint(win)
    run_id = new_run_id()
    risk_usd = float(th["risk_usd"])

    stop_buf = float(STOP_BUFFER_BY_VARIANT.get(stop_variant, 0.25))
    tp_r = TP_R_BY_VARIANT.get(tp_variant)
    tp_mode = "nearest_support" if tp_variant == TP_NEAREST_SUPPORT else "fixed_r"

    symbols_data: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    candles_1h_by: dict[str, list[dict[str, Any]]] = {}
    candles_4h_by: dict[str, list[dict[str, Any]]] = {}

    for sym in syms:
        c1, c4 = _load_symbol_candles(sym, start=data_start, end=data_end)
        # Restrict to requested experiment range (inclusive).
        c1 = filter_ohlcv(c1, start=win.requested_start, end=win.requested_end)
        c4 = filter_ohlcv(c4, start=win.requested_start, end=win.requested_end)
        symbols_data[sym] = (c1, c4)
        candles_1h_by[sym] = c1
        candles_4h_by[sym] = c4

    all_signals: list[dict[str, Any]] = []
    for sym, (c1, c4) in symbols_data.items():
        sym_signals = generate_signals_for_symbol(
            sym,
            c1,
            c4,
            thresholds=th,
            stop_buffer_atr=stop_buf,
            tp_mode=tp_mode,
            tp_r=tp_r,
        )
        for s in sym_signals:
            s["stop_variant"] = stop_variant
            s["tp_variant"] = tp_variant
        all_signals.extend(sym_signals)

    trades = simulate_signals(all_signals, candles_1h_by, risk_usd=risk_usd)

    base_trades = filter_trades_to_window(
        trades, start=win.base_start, end=win.base_end
    )
    oos_dev_trades = filter_trades_to_window(
        trades, start=win.oos_dev_start, end=win.oos_dev_end
    )
    oos_val_trades = filter_trades_to_window(
        trades, start=win.oos_val_start, end=win.oos_val_end
    )

    base_metrics = summarize_trades(base_trades)
    oos_dev_metrics = summarize_trades(oos_dev_trades)
    oos_val_metrics = summarize_trades(oos_val_trades)

    recon = reconcile_trades(trades)
    replay = forensic_direct_replay(
        trades,
        candles_by_symbol=candles_1h_by,
        candles_4h_by_symbol=candles_4h_by,
    )
    classification = classify_research(
        base_summary=base_metrics,
        oos_dev_summary=oos_dev_metrics,
        oos_val_summary=oos_val_metrics,
        oos_val_trades=len(oos_val_trades),
        recon_ok=bool(recon.get("ok")),
        direct_replay=bool(replay.get("direct_candle_replay")),
        windows_ok=bool(win_check.get("ok")),
    )

    stop_variant_results: dict[str, Any] = {}
    tp_variant_results: dict[str, Any] = {}
    if run_variant_grid:
        from app.research.short_pullback_rejection.variants import apply_stop_buffer_to_signal

        # One scan already done; rebuild stop/TP from stored rejection geometry.
        # Do not select a winner on base alone.
        for sv in STOP_VARIANT_IDS:
            buf = float(STOP_BUFFER_BY_VARIANT[sv])
            rebuilt: list[dict[str, Any]] = []
            for s in all_signals:
                try:
                    row = apply_stop_buffer_to_signal(
                        s,
                        stop_buffer_atr=buf,
                        tp_mode=tp_mode,
                        tp_r=tp_r,
                    )
                    row["stop_variant"] = sv
                    row["tp_variant"] = tp_variant
                    rebuilt.append(row)
                except Exception:
                    continue
            tr = simulate_signals(rebuilt, candles_1h_by, risk_usd=risk_usd)
            base_only = filter_trades_to_window(
                tr, start=win.base_start, end=win.base_end
            )
            stop_variant_results[sv] = {
                **_slim_variant_summary(base_only),
                "partition": "base_exploratory_only",
                "note": "Do not select winner on base alone",
            }
        for tv in TP_VARIANT_IDS:
            tv_r = TP_R_BY_VARIANT.get(tv)
            tv_mode = "nearest_support" if tv == TP_NEAREST_SUPPORT else "fixed_r"
            rebuilt = []
            for s in all_signals:
                try:
                    row = apply_stop_buffer_to_signal(
                        s,
                        stop_buffer_atr=stop_buf,
                        tp_mode=tv_mode,
                        tp_r=tv_r,
                    )
                    row["stop_variant"] = stop_variant
                    row["tp_variant"] = tv
                    rebuilt.append(row)
                except Exception:
                    continue
            tr = simulate_signals(rebuilt, candles_1h_by, risk_usd=risk_usd)
            base_only = filter_trades_to_window(
                tr, start=win.base_start, end=win.base_end
            )
            tp_variant_results[tv] = {
                **_slim_variant_summary(base_only),
                "partition": "base_exploratory_only",
                "note": "Do not select winner on base alone",
            }

    cfg_hash = configuration_hash(
        thresholds=th,
        window=win.to_dict(),
        symbols=syms,
        stop_variant=stop_variant,
        tp_variant=tp_variant,
    )
    ds_hash = dataset_hash(syms, win.to_dict())

    report = build_final_report(
        run_id=run_id,
        configuration_hash=cfg_hash,
        dataset_hash=ds_hash,
        strategy_fingerprint=STRATEGY_FINGERPRINT_TEXT,
        windows=win.to_dict(),
        signals=all_signals,
        trades=trades,
        base_metrics=base_metrics,
        oos_dev_metrics=oos_dev_metrics,
        oos_val_metrics=oos_val_metrics,
        trades_by_rejection_type=by_rejection_type(trades),
        trades_by_rejection_type_counts=rejection_type_counts(trades),
        regime_results=by_regime(trades),
        stop_variant_results=stop_variant_results,
        tp_variant_results=tp_variant_results,
        support_distance_distribution=distribution(trades, "support_distance"),
        entry_extension_distribution=distribution(trades, "entry_extension_atr"),
        classification=classification,
        reconciliation=recon,
        direct_replay=replay,
    )
    report["window_validation"] = win_check
    report["primary_stop_variant"] = stop_variant
    report["primary_tp_variant"] = tp_variant
    report.update(SAFETY_STAMPS)

    out_dir = report_dir or default_report_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"short_pullback_rejection_{run_id}.json"
    trades_path = out_dir / f"short_pullback_rejection_trades_{run_id}.json"
    operator_path = out_dir / f"short_pullback_rejection_operator_{run_id}.txt"
    latest_json = out_dir / "short_pullback_rejection_LATEST.json"
    latest_op = out_dir / "short_pullback_rejection_operator_LATEST.txt"

    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    trades_path.write_text(json.dumps(trades, indent=2, default=str), encoding="utf-8")
    operator_path.write_text(str(report.get("operator_text") or ""), encoding="utf-8")
    latest_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    latest_op.write_text(str(report.get("operator_text") or ""), encoding="utf-8")

    report["report_path"] = str(report_path)
    report["trades_path"] = str(trades_path)
    report["operator_path"] = str(operator_path)
    return report


async def run_short_pullback_rejection_research_async(**kwargs: Any) -> dict[str, Any]:
    return run_short_pullback_rejection_research(**kwargs)
