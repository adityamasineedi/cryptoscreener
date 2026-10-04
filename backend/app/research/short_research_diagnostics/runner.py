"""Orchestrate SHORT research diagnostics (research-only, no paper/Telegram/v1)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research.bos_combinations import get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.combo02_candidate_research import compute_trade_metrics
from app.research.data_cache.memory_loader import candles_from_parquet
from app.research.short_research_diagnostics.ablation import build_ablation_matrix
from app.research.short_research_diagnostics.classification import classify_short_diagnostics
from app.research.short_research_diagnostics.classifiers import summarize_by_category
from app.research.short_research_diagnostics.constants import (
    DEFAULT_UNIVERSE,
    SAFETY_STAMPS,
    STOP_TOO_TIGHT,
    STOP_TOO_WIDE,
)
from app.research.short_research_diagnostics.fee_sensitivity import fee_sensitivity_matrix
from app.research.short_research_diagnostics.implementation_verify import (
    run_implementation_verification,
)
from app.research.short_research_diagnostics.report import build_final_report
from app.research.short_research_diagnostics.trade_dataset import build_diagnostic_dataset
from app.research.short_research_forensics import (
    apply_independent_replay_to_trade,
    enrich_trade_forensic_fields,
)
from app.research.short_research_windows import DEFAULT_SHORT_RESEARCH_WINDOWS
from app.research.trade_fees import enrich_trades
from app.signals.config import SignalConfig
from app.signals.signal_engine import SignalEngine
from app.signals._candle_utils import candle_time


def new_diagnostic_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def default_parquet_root() -> Path:
    # runner.py → short_research_diagnostics → research → app → backend
    return (
        Path(__file__).resolve().parents[3]
        / "data"
        / "research_cache"
        / "ohlcv"
        / "ohlcv_v1"
    )


def parquet_path_for(
    symbol: str,
    timeframe: str,
    *,
    start: str = "2024-01-01",
    end: str = "2025-06-30",
    root: Path | None = None,
) -> Path:
    base = root or default_parquet_root()
    return base / f"{symbol.upper()}_{timeframe}_{start}_{end}_ohlcv_v1.parquet"


def load_symbol_candles(
    symbol: str,
    *,
    start: str = "2024-01-01",
    end: str = "2025-06-30",
    root: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    p1 = parquet_path_for(symbol, "1h", start=start, end=end, root=root)
    p4 = parquet_path_for(symbol, "4h", start=start, end=end, root=root)
    c1 = candles_from_parquet(p1) if p1.is_file() else []
    c4 = candles_from_parquet(p4) if p4.is_file() else []
    return c1, c4


def run_baseline_short_backtest(
    symbol: str,
    candles_1h: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]],
    *,
    risk_usd: float = 20.0,
) -> list[dict[str, Any]]:
    combo = get_combination("COMBO_02")
    assert combo is not None
    out = run_combination_backtest(
        symbol,
        "1h",
        candles_1h,
        combo,
        signal_config=SignalConfig(),
        direction_filter="SHORT",
        candles_1h=candles_1h,
        candles_4h=candles_4h,
    )
    trades = [t.to_dict() if hasattr(t, "to_dict") else dict(t) for t in (out.get("trades") or [])]
    closed = [
        t
        for t in trades
        if str(t.get("outcome") or "") not in ("", "OPEN", "None")
        and str(t.get("direction") or "").upper() == "SHORT"
    ]
    enriched = enrich_trades(closed, risk_usd=risk_usd, closed_only=True)
    # Direct replay stamps for ablation requirement
    replayed = []
    for t in enriched:
        row = enrich_trade_forensic_fields({**t, "direction": "SHORT"})
        try:
            row = apply_independent_replay_to_trade(
                row, candles_1h=list(candles_1h), candles_4h=list(candles_4h)
            )
        except Exception:
            pass
        replayed.append(row)
    return replayed


def _btc_trend_map(btc_candles: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Coarse pre-entry BTC trend via local swing structure (no lookahead beyond bar i)."""
    if len(btc_candles) < 80:
        return {}
    from app.research.short_research_diagnostics.trade_dataset import _recent_swing_levels

    filled: dict[str, str] = {}
    cur = "NEUTRAL"
    # Sample daily to bound cost; forward-fill hourly keys.
    for i in range(60, len(btc_candles), 24):
        meta = _recent_swing_levels(btc_candles, i, lookback=80)
        cur = str(meta.get("trend_state") or cur) or cur
        t = candle_time(btc_candles[i])
        if t is not None:
            filled[t.isoformat()[:13]] = cur
    # Forward-fill across all bars
    out: dict[str, str] = {}
    last = "NEUTRAL"
    sample_keys = sorted(filled)
    si = 0
    for c in btc_candles:
        t = candle_time(c)
        if t is None:
            continue
        key = t.isoformat()[:13]
        while si < len(sample_keys) and sample_keys[si] <= key:
            last = filled[sample_keys[si]]
            si += 1
        out[key] = last
    return out


def _entry_findings(by_entry: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    late_n = int((by_entry.get("LATE") or {}).get("trade_count") or 0)
    chase_n = int((by_entry.get("CHASING") or {}).get("trade_count") or 0)
    retest = by_entry.get("RETEST") or {}
    imm = by_entry.get("IMMEDIATE_BREAK") or {}
    late_or_poor = (late_n + chase_n) / n >= 0.3 if n else False
    retest_better = (
        retest.get("average_net_r") is not None
        and imm.get("average_net_r") is not None
        and float(retest["average_net_r"]) > float(imm["average_net_r"]) + 0.05
    )
    return {
        "by_category": by_entry,
        "late_or_chase_share": (late_n + chase_n) / n if n else None,
        "retest_outperforms_immediate_break": retest_better,
        "late_or_poor": late_or_poor or retest_better,
        "summary": (
            f"late+chase={late_n + chase_n}/{n}; "
            f"retest_avg_r={retest.get('average_net_r')}; "
            f"immediate_avg_r={imm.get('average_net_r')}"
        ),
    }


def _stop_findings(by_stop: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    tight = int((by_stop.get(STOP_TOO_TIGHT) or {}).get("trade_count") or 0)
    wide = int((by_stop.get(STOP_TOO_WIDE) or {}).get("trade_count") or 0)
    label = "mixed"
    if n and tight / n >= 0.25 and tight >= wide:
        label = "too_tight"
    elif n and wide / n >= 0.25 and wide > tight:
        label = "too_wide"
    elif tight == 0 and wide == 0:
        label = "mostly_good"
    return {
        "by_category": by_stop,
        "tight_or_wide": label,
        "summary": f"TOO_TIGHT={tight} TOO_WIDE={wide} of {n}",
    }


def _tp_findings(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "summary": "no trades",
            "economically_viable": False,
            "poor_rr_after_fees": True,
            "mfe_exceeds_tp_frequently": False,
        }
    rrs = [float(r["reward_to_risk_ratio"]) for r in rows if r.get("reward_to_risk_ratio") is not None]
    mfe_rs = [float(r["MFE_R"]) for r in rows if r.get("MFE_R") is not None]
    realized = [float(r["R"]) for r in rows if r.get("R") is not None]
    avg_rr = sum(rrs) / len(rrs) if rrs else None
    avg_mfe_r = sum(mfe_rs) / len(mfe_rs) if mfe_rs else None
    avg_r = sum(realized) / len(realized) if realized else None
    mfe_gt_tp = 0
    for r in rows:
        if r.get("MFE_R") is not None and r.get("reward_to_risk_ratio") is not None:
            if float(r["MFE_R"]) > float(r["reward_to_risk_ratio"]) * 0.9:
                mfe_gt_tp += 1
    poor_rr = (avg_rr is not None and avg_rr < 1.2) or (avg_r is not None and avg_r < 0)
    mfe_freq = (mfe_gt_tp / len(rows)) >= 0.35 if rows else False
    viable = (avg_rr is not None and avg_rr >= 1.5 and avg_r is not None and avg_r > 0)
    return {
        "avg_reward_to_risk": avg_rr,
        "avg_mfe_r": avg_mfe_r,
        "avg_realized_r": avg_r,
        "mfe_near_or_beyond_tp_share": mfe_gt_tp / len(rows) if rows else None,
        "poor_rr_after_fees": poor_rr,
        "mfe_exceeds_tp_frequently": mfe_freq,
        "economically_viable": viable,
        "summary": (
            f"avg_RR={avg_rr}; avg_MFE_R={avg_mfe_r}; avg_realized_R={avg_r}; "
            f"viable={viable}"
        ),
    }


def _regime_findings(by_regime: Mapping[str, Any]) -> dict[str, Any]:
    sb = by_regime.get("STRONG_BEAR") or {}
    side = by_regime.get("SIDEWAYS") or {}
    bull = by_regime.get("STRONG_BULL") or {}
    concentrated = False
    if sb.get("average_net_r") is not None:
        others = [
            x.get("average_net_r")
            for x in (side, bull)
            if x.get("average_net_r") is not None
        ]
        if others and float(sb["average_net_r"]) > max(float(o) for o in others) + 0.15:
            concentrated = True
        if float(sb["average_net_r"]) > 0 and all(
            (o is None or float(o) <= 0) for o in (side.get("average_net_r"), bull.get("average_net_r"))
        ):
            concentrated = True
    return {
        "by_regime": by_regime,
        "concentrated": concentrated,
        "strong_bear": sb,
        "sideways": side,
        "strong_bull": bull,
        "summary": (
            f"STRONG_BEAR n={sb.get('trade_count')} avg_r={sb.get('average_net_r')}; "
            f"SIDEWAYS n={side.get('trade_count')} avg_r={side.get('average_net_r')}; "
            f"STRONG_BULL n={bull.get('trade_count')} avg_r={bull.get('average_net_r')}"
        ),
    }


def run_short_diagnostics(
    *,
    symbols: Sequence[str] | None = None,
    base_start: str = "2024-01-01",
    base_end: str = "2025-06-30",
    risk_usd: float = 20.0,
    report_dir: Path | str | None = None,
    enrich_context: bool = True,
    run_no_htf: bool = True,
    tests_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Full diagnostic pipeline. Never enables paper/production/Telegram."""
    run_id = new_diagnostic_run_id()
    syms = [s.upper() for s in (symbols or list(DEFAULT_UNIVERSE))]
    windows = DEFAULT_SHORT_RESEARCH_WINDOWS
    window = {
        "base_start": base_start,
        "base_end": base_end,
        "oos_dev_start": windows.oos_dev_start,
        "oos_dev_end": windows.oos_dev_end,
        "oos_val_start": windows.oos_val_start,
        "oos_val_end": windows.oos_val_end,
        "policy": "POLICY_A_STRICT_DISJOINT",
    }

    implementation = run_implementation_verification()
    if not implementation["ok"]:
        report = build_final_report(
            run_id=run_id,
            implementation=implementation,
            trade_dataset=[],
            entry_findings={"summary": "skipped — implementation defect", "late_or_poor": None},
            stop_findings={"summary": "skipped", "tight_or_wide": None},
            tp_findings={"summary": "skipped", "economically_viable": None},
            regime_findings={"summary": "skipped", "concentrated": None},
            fee_sensitivity={},
            ablation={},
            classification={
                "labels": ["IMPLEMENTATION_DEFECT"],
                "primary": "IMPLEMENTATION_DEFECT",
            },
            tests=dict(tests_meta or {}),
        )
        return {"run_id": run_id, "report": report, **SAFETY_STAMPS}

    candles_1h: dict[str, list[dict[str, Any]]] = {}
    candles_4h: dict[str, list[dict[str, Any]]] = {}
    baseline_trades: list[dict[str, Any]] = []
    for sym in syms:
        c1, c4 = load_symbol_candles(sym, start=base_start, end=base_end)
        candles_1h[sym] = c1
        candles_4h[sym] = c4
        if not c1:
            continue
        trades = run_baseline_short_backtest(sym, c1, c4, risk_usd=risk_usd)
        baseline_trades.extend(trades)

    btc_map = _btc_trend_map(candles_1h.get("BTCUSDT") or [])
    diag_rows = build_diagnostic_dataset(
        baseline_trades,
        candles_by_symbol=candles_1h,
        candles_4h_by_symbol=candles_4h,
        risk_usd=risk_usd,
        btc_trend_by_time=btc_map,
        enrich_context=enrich_context,
    )

    by_entry = summarize_by_category(diag_rows, "entry_quality")
    by_stop = summarize_by_category(diag_rows, "stop_class")
    # Expand regimes (multi-label) into per-regime rows for summary
    regime_expanded = []
    for r in diag_rows:
        regs = r.get("regimes") or ([r.get("primary_regime")] if r.get("primary_regime") else ["UNKNOWN"])
        for lab in regs:
            regime_expanded.append({**r, "regime_label": lab})
    by_regime = summarize_by_category(regime_expanded, "regime_label")

    entry_findings = _entry_findings(by_entry, diag_rows)
    stop_findings = _stop_findings(by_stop, diag_rows)
    tp_findings = _tp_findings(diag_rows)
    regime_findings = _regime_findings(by_regime)
    fees = fee_sensitivity_matrix(baseline_trades, risk_usd=risk_usd)

    ablation = build_ablation_matrix(
        baseline_trades=baseline_trades,
        diagnostic_rows=diag_rows,
        candles_by_symbol=candles_1h,
        candles_4h_by_symbol=candles_4h,
        window=window,
        symbols=syms,
        risk_usd=risk_usd,
        run_no_htf=run_no_htf,
    )

    base_metrics = compute_trade_metrics(baseline_trades)
    classification = classify_short_diagnostics(
        implementation=implementation,
        diagnostic_rows=diag_rows,
        entry_summary=by_entry,
        stop_summary=by_stop,
        tp_findings=tp_findings,
        regime_summary=by_regime,
        fee_sensitivity=fees,
        baseline_metrics=base_metrics,
    )

    out_dir = Path(
        report_dir
        or (Path(__file__).resolve().parents[3] / "reports" / "short_research")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = out_dir / f"short_diagnostic_trades_{run_id}.json"
    report_path = out_dir / f"short_diagnostic_report_{run_id}.json"
    text_path = out_dir / f"short_diagnostic_operator_{run_id}.txt"

    dataset_path.write_text(json.dumps(diag_rows, indent=2, default=str), encoding="utf-8")
    report = build_final_report(
        run_id=run_id,
        implementation=implementation,
        trade_dataset=diag_rows,
        entry_findings=entry_findings,
        stop_findings=stop_findings,
        tp_findings=tp_findings,
        regime_findings=regime_findings,
        fee_sensitivity=fees,
        ablation=ablation,
        classification=classification,
        tests=dict(
            tests_meta
            or {
                "added": "test_combo02_short_diagnostics.py",
                "executed": "pending",
                "result": "pending",
                "long_regression": "not run in this function",
                "v1_regression": "not run in this function",
                "uncertainties": [
                    "OOS windows not evaluated because base remains RESEARCH_REJECTED",
                    "BTC dominance / breadth proxies unavailable",
                    "ADX not computed — regime strength uses trend agreement counts",
                ],
            }
        ),
        dataset_path=str(dataset_path),
    )
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    text_path.write_text(str(report.get("operator_text") or ""), encoding="utf-8")

    return {
        "run_id": run_id,
        "report": report,
        "dataset_path": str(dataset_path),
        "report_path": str(report_path),
        "text_path": str(text_path),
        "baseline_trade_count": len(baseline_trades),
        "diagnostic_trade_count": len(diag_rows),
        "base_metrics": {
            k: v for k, v in base_metrics.items() if k not in ("closed_trades", "r_series")
        },
        **SAFETY_STAMPS,
    }
