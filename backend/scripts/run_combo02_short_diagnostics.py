#!/usr/bin/env python3
"""COMBO_02 SHORT research diagnostics (no paper / live / Telegram / v1 changes).

Usage (from backend/):
  python scripts/run_combo02_short_diagnostics.py
  python scripts/run_combo02_short_diagnostics.py --symbols BTCUSDT,ETHUSDT --fast-context
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research.short_research_constants import DEFAULT_SHORT_RESEARCH_UNIVERSE  # noqa: E402
from app.research.short_research_diagnostics.runner import run_short_diagnostics  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="COMBO_02 SHORT research diagnostics")
    p.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SHORT_RESEARCH_UNIVERSE),
        help="Comma-separated symbols",
    )
    p.add_argument("--base-start", default="2024-01-01")
    p.add_argument("--base-end", default="2025-06-30")
    p.add_argument("--risk-usd", type=float, default=20.0)
    p.add_argument(
        "--report-dir",
        default=str(ROOT / "reports" / "short_research"),
    )
    p.add_argument(
        "--fast-context",
        action="store_true",
        help="Skip heavy per-trade SignalEngine context rebuild",
    )
    p.add_argument(
        "--skip-no-htf",
        action="store_true",
        help="Skip COMBO_02_LOCAL rebacktest ablation",
    )
    args = p.parse_args()
    symbols = [s.strip().upper() for s in str(args.symbols).split(",") if s.strip()]
    result = run_short_diagnostics(
        symbols=symbols,
        base_start=args.base_start,
        base_end=args.base_end,
        risk_usd=float(args.risk_usd),
        report_dir=Path(args.report_dir),
        enrich_context=not args.fast_context,
        run_no_htf=not args.skip_no_htf,
    )
    report = result.get("report") or {}
    print(report.get("operator_text") or json.dumps(result, indent=2, default=str))
    print()
    print(f"Report JSON: {result.get('report_path')}")
    print(f"Trade dataset: {result.get('dataset_path')}")
    print("paper_eligible=false production_approved=false telegram_eligible=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
