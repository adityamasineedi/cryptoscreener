#!/usr/bin/env python3
"""Rebuild COMBO_03 validation reports from per-symbol CSV/JSON artifacts."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_combo03_transition_validation import (
    OUT_ROOT,
    REGIMES,
    SYMBOLS,
    _bucket_metrics,
    _build_report,
    _format_block,
    _write_csv,
)

COMBOS = [
    "COMBO_03_TRANSITION",
    "COMBO_03_TRANSITION_B",
    "COMBO_03_TRANSITION_C",
]


def _load_trades(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _coerce_trade(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("r_multiple", "fees", "entry", "stop", "target", "tp1", "tp2", "tp3"):
        if out.get(key) in ("", None):
            out[key] = None
            continue
        try:
            out[key] = float(out[key])
        except (TypeError, ValueError):
            pass
    if out.get("holding_bars") not in ("", None):
        try:
            out["holding_bars"] = float(out["holding_bars"])
        except (TypeError, ValueError):
            pass
    return out


def aggregate_combo(cid: str) -> dict[str, Any] | None:
    out_dir = OUT_ROOT / cid.lower()
    per_symbol: list[dict[str, Any]] = []
    for sym in SYMBOLS:
        result_path = out_dir / f"{sym}_result.json"
        trades_path = out_dir / f"{sym}_trades.csv"
        if not result_path.exists():
            print(f"MISSING {cid}/{sym}", flush=True)
            return None
        meta = json.loads(result_path.read_text(encoding="utf-8"))
        trades = [_coerce_trade(r) for r in _load_trades(trades_path)]
        # Prefer live recompute from trades CSV so metrics stay consistent.
        metrics = _bucket_metrics(trades)
        per_symbol.append(
            {
                "symbol": sym,
                "combination_id": cid,
                "bars_1h": meta.get("bars_1h"),
                "bars_15m": meta.get("bars_15m"),
                "elapsed_seconds": meta.get("elapsed_seconds"),
                "metrics": metrics,
                "trades": trades,
            }
        )
    report = _build_report(cid, per_symbol)
    all_trades: list[dict[str, Any]] = []
    for s in per_symbol:
        all_trades.extend(s.get("trades") or [])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "validation_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    block = _format_block(cid, report)
    (out_dir / "VALIDATION_REPORT.md").write_text(
        f"# {cid} Validation\n\n{report['disclaimer']}\n\n```text\n{block}\n```\n",
        encoding="utf-8",
    )
    _write_csv(out_dir / "all_trades.csv", all_trades)
    return report


def main() -> int:
    comparison: dict[str, Any] = {}
    summary_blocks: list[str] = []
    for cid in COMBOS:
        report = aggregate_combo(cid)
        if report is None:
            print(f"INCOMPLETE {cid}", flush=True)
            continue
        comparison[cid] = report["overall"]
        summary_blocks.append(_format_block(cid, report))

    v2_report_path = Path("reports/combo02_v2_multi_market_validation/validation_report.json")
    if v2_report_path.exists():
        v2rep = json.loads(v2_report_path.read_text(encoding="utf-8"))
        comparison["COMBO_02_V2"] = v2rep.get("overall") or {}
        summary_blocks.insert(
            0,
            "COMBO_02_V2 (prior multi-market validation report):\n"
            + json.dumps(comparison["COMBO_02_V2"], indent=2, default=str),
        )
        # Also attach CHOPPY slice for analytical questions.
        chop = (v2rep.get("by_regime") or {}).get("CHOPPY") or {}
        comparison["COMBO_02_V2_CHOPPY"] = chop

    compare_rows = [
        {"combination_id": cid, **(metrics if isinstance(metrics, dict) else {})}
        for cid, metrics in comparison.items()
    ]
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    _write_csv(OUT_ROOT / "comparison.csv", compare_rows)
    (OUT_ROOT / "COMPARISON_REPORT.md").write_text(
        "# COMBO_03_TRANSITION vs COMBO_02_V2\n\n"
        + f"Generated: {datetime.now(timezone.utc).isoformat()}\n\n"
        + "\n\n".join(f"```text\n{b}\n```" for b in summary_blocks)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({k: v for k, v in comparison.items()}, indent=2, default=str))
    print(f"Wrote aggregate under {OUT_ROOT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
