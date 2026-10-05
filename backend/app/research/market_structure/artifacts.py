"""Persist market-structure analytics under reports/<run_id>/market_structure/."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research.market_structure.config import ANALYTICS_VERSION

ROOT = Path(__file__).resolve().parents[3]  # backend/
DEFAULT_REPORTS = ROOT / "reports"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = list(rows[0].keys())
    for r in rows[1:]:
        for k in r.keys():
            if k not in fieldnames:
                fieldnames.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            flat = {}
            for k, v in r.items():
                if isinstance(v, (dict, list, tuple)):
                    flat[k] = json.dumps(v, default=str)
                else:
                    flat[k] = v
            writer.writerow(flat)


def market_structure_dir(run_id: str, reports_root: Path | None = None) -> Path:
    root = reports_root or DEFAULT_REPORTS
    return root / str(run_id) / "market_structure"


def write_market_structure_artifacts(
    analytics: Mapping[str, Any],
    *,
    run_id: str,
    reports_root: Path | None = None,
) -> dict[str, str]:
    """Write required CSV/JSON artifacts. Returns path map."""
    out_dir = market_structure_dir(run_id, reports_root)
    out_dir.mkdir(parents=True, exist_ok=True)

    by_bar = list(analytics.get("by_bar") or [])
    table_rows = list(analytics.get("table_rows") or [])
    trade_context = list(analytics.get("trade_context") or [])
    unmatched = list(analytics.get("unmatched_trade_context") or [])
    ambiguous = list(analytics.get("ambiguous_trade_context") or [])
    trade_summary = list(analytics.get("trade_regime_summary") or [])
    opportunity = list(analytics.get("regime_opportunity_summary") or [])
    mtf_summary = list(analytics.get("mtf_alignment_summary") or [])
    quality = dict(analytics.get("quality_report") or {})
    meta = dict(analytics.get("metadata") or {})
    row_counts = dict(analytics.get("row_counts") or {})

    raw_entry_marked = [
        {
            "decision_time": r.get("decision_time"),
            "trade_id": r.get("trade_id"),
            "entry": r.get("entry"),
            "entry_attribution_type": r.get("entry_attribution_type"),
            "row_role": r.get("row_role"),
            "signal_bar": r.get("signal_bar"),
            "execution_bar": r.get("execution_bar"),
        }
        for r in by_bar
        if r.get("entry") == "YES" or r.get("entry_attribution_type") == "EXECUTION_BAR"
    ]

    feature_cfg = {
        "feature_config": analytics.get("feature_config"),
        "feature_config_fingerprint": analytics.get("feature_config_fingerprint"),
        "analytics_version": analytics.get("analytics_version") or ANALYTICS_VERSION,
        "metadata": meta,
        "closed_candle_policy": analytics.get("closed_candle_policy"),
        "15m_status": analytics.get("15m_status"),
        "15m_source_available": analytics.get("15m_source_available"),
        "15m_source_available_rows": analytics.get("15m_source_available_rows"),
        "15m_feature_available_rows": analytics.get("15m_feature_available_rows"),
        "15m_unknown_rows": analytics.get("15m_unknown_rows"),
        "15m_status_distribution": analytics.get("15m_status_distribution"),
        "row_counts": row_counts,
        "disclaimer": "Analytics only — does not affect strategy decisions",
    }

    paths = {
        "market_structure_by_bar.csv": out_dir / "market_structure_by_bar.csv",
        "market_structure_by_bar.json": out_dir / "market_structure_by_bar.json",
        "trade_context.csv": out_dir / "trade_context.csv",
        "trade_context_raw_entry_marked_rows.csv": out_dir
        / "trade_context_raw_entry_marked_rows.csv",
        "unmatched_trade_context.csv": out_dir / "unmatched_trade_context.csv",
        "ambiguous_trade_context.csv": out_dir / "ambiguous_trade_context.csv",
        "trade_regime_summary.csv": out_dir / "trade_regime_summary.csv",
        "trade_regime_summary.json": out_dir / "trade_regime_summary.json",
        "regime_opportunity_summary.csv": out_dir / "regime_opportunity_summary.csv",
        "mtf_alignment_summary.csv": out_dir / "mtf_alignment_summary.csv",
        "market_structure_config.json": out_dir / "market_structure_config.json",
        "market_structure_quality_report.json": out_dir
        / "market_structure_quality_report.json",
    }

    _write_csv(paths["market_structure_by_bar.csv"], table_rows or by_bar)
    _write_json(
        paths["market_structure_by_bar.json"],
        {
            "rows": by_bar,
            "table_rows": table_rows,
            "trade_context": trade_context,
            "row_counts": row_counts,
            "metadata": meta,
        },
    )
    _write_csv(paths["trade_context.csv"], trade_context)
    _write_csv(paths["trade_context_raw_entry_marked_rows.csv"], raw_entry_marked)
    _write_csv(
        paths["unmatched_trade_context.csv"],
        unmatched
        or [{"note": "no_unmatched_trades", "match_status": "NONE"}],
    )
    _write_csv(
        paths["ambiguous_trade_context.csv"],
        ambiguous
        or [{"note": "no_ambiguous_trades", "match_status": "NONE"}],
    )
    _write_csv(paths["trade_regime_summary.csv"], trade_summary)
    _write_json(paths["trade_regime_summary.json"], trade_summary)
    _write_csv(paths["regime_opportunity_summary.csv"], opportunity)
    _write_csv(paths["mtf_alignment_summary.csv"], mtf_summary)
    _write_json(paths["market_structure_config.json"], feature_cfg)
    _write_json(
        paths["market_structure_quality_report.json"],
        {
            **quality,
            "invariant_bars_with_future_feature_violation_eq_0": quality.get(
                "bars_with_future_feature_violation", 1
            )
            == 0,
            "invariant_duplicate_execution_entries_eq_0": quality.get(
                "duplicate_execution_entries_per_trade", 1
            )
            == 0,
            "invariant_execution_rows_without_trade_id_eq_0": quality.get(
                "execution_rows_without_trade_id", 1
            )
            == 0,
            "analytics_version": ANALYTICS_VERSION,
        },
    )

    return {k: str(v) for k, v in paths.items()}
