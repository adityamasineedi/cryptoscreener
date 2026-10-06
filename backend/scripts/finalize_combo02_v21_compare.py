#!/usr/bin/env python3
"""Aggregate BASE + V2.1-A/B checkpoints into COMPARISON_REPORT.md."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.run_combo02_v21_variant_compare import (
    OUT_DIR,
    PRIOR_BASE_TRADES,
    SYMBOLS,
    VARIANTS,
    _aggregate,
    _format_md,
    _load_prior_base,
    _recommend,
    _write_csv,
)


def _load_variant(label: str, combo_id: str) -> dict:
    summary = OUT_DIR / f"{label}_summary.json"
    if summary.exists():
        return json.loads(summary.read_text(encoding="utf-8"))
    per = []
    all_trades = []
    for sym in SYMBOLS:
        path = OUT_DIR / f"{label}_{sym}.json"
        if not path.exists():
            raise FileNotFoundError(f"missing checkpoint {path}")
        one = json.loads(path.read_text(encoding="utf-8"))
        per.append(one)
        all_trades.extend(one["trades"])
    agg = _aggregate(label, combo_id, per)
    _write_csv(OUT_DIR / f"{label}_all_trades.csv", all_trades)
    summary.write_text(json.dumps(agg, indent=2, default=str), encoding="utf-8")
    return agg


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = {"BASE_V2": _load_prior_base()}
    for label, combo_id in VARIANTS:
        if label == "BASE_V2":
            continue
        results[label] = _load_variant(label, combo_id)

    decision = _recommend(results)
    payload = {
        "data_start": "2022-01-01",
        "data_end": "2026-10-06",
        "symbols": list(SYMBOLS),
        "variants": results,
        "decision": decision,
        "base_source": str(PRIOR_BASE_TRADES),
        "disclaimer": (
            "Research comparison only. Not a profitability claim. "
            "Frozen COMBO_02_V2 default not promoted."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (OUT_DIR / "comparison.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    md = _format_md(results, decision)
    (OUT_DIR / "COMPARISON_REPORT.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"\nRecommendation: {decision['recommendation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
