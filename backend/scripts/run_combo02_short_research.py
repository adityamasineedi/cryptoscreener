#!/usr/bin/env python3
"""COMBO_02 SHORT research-only historical batch runner (real-symbol reports).

Never modifies v1 universe, paper watchers, or Telegram eligibility.
Never sets paper_eligible / production_approved / telegram_eligible true.

Usage (from backend/):
  python scripts/run_combo02_short_research.py
  python scripts/run_combo02_short_research.py --symbols BTCUSDT,ETHUSDT \\
    --base-start 2024-01-01 --base-end 2025-06-30 \\
    --oos-dev-start 2025-07-01 --oos-dev-end 2025-12-31 \\
    --oos-val-start 2026-01-01 --oos-val-end 2026-09-30 \\
    --evidence-mode DIRECT_CANDLE_REPLAY \\
    --report-dir reports/short_research
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.research.combo02_short_research_runner import (  # noqa: E402
    default_reports_dir,
    run_short_research_batch,
)
from app.research.short_research_constants import (  # noqa: E402
    DEFAULT_SHORT_RESEARCH_UNIVERSE,
    DISCLAIMER,
    SETUP_TIMEFRAME,
)
from app.research.short_research_hard_gates import (  # noqa: E402
    EVIDENCE_MODE_DIRECT,
    print_batch_console_summary,
)
from app.research.short_research_windows import (  # noqa: E402
    DEFAULT_SHORT_RESEARCH_WINDOWS,
    ResearchWindows,
    short_research_window_config,
    validate_research_windows,
)
from app.services.database import db_manager  # noqa: E402


def _build_windows(args: argparse.Namespace) -> ResearchWindows:
    """Build explicit ResearchWindows from CLI or defaults."""
    fields = (
        args.base_start,
        args.base_end,
        args.oos_dev_start,
        args.oos_dev_end,
        args.oos_val_start,
        args.oos_val_end,
    )
    if any(fields) and not all(fields):
        raise SystemExit(
            "When setting research windows, provide all of: "
            "--base-start --base-end --oos-dev-start --oos-dev-end "
            "--oos-val-start --oos-val-end"
        )
    if all(fields):
        return ResearchWindows(
            requested_start=str(args.base_start),
            requested_end=str(args.oos_val_end),
            base_start=str(args.base_start),
            base_end=str(args.base_end),
            oos_dev_start=str(args.oos_dev_start),
            oos_dev_end=str(args.oos_dev_end),
            oos_val_start=str(args.oos_val_start),
            oos_val_end=str(args.oos_val_end),
        )
    return DEFAULT_SHORT_RESEARCH_WINDOWS


async def _main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    await db_manager.connect(settings)
    symbols = (
        [s.strip().upper() for s in str(args.symbols).split(",") if s.strip()]
        if args.symbols
        else list(DEFAULT_SHORT_RESEARCH_UNIVERSE)
    )
    tf = str(args.timeframe or SETUP_TIMEFRAME).lower()
    if tf != SETUP_TIMEFRAME:
        raise SystemExit(f"SHORT research setup timeframe must be {SETUP_TIMEFRAME}")

    windows = _build_windows(args)
    win_check = validate_research_windows(windows)
    if not win_check["ok"]:
        print(
            json.dumps(
                {
                    "status": "INVALID_OVERLAP",
                    "window_status": win_check["window_status"],
                    "errors": win_check["errors"],
                    "research_quality": "REVIEW_REQUIRED",
                    "paper_eligible": False,
                    "production_approved": False,
                    "telegram_eligible": False,
                },
                indent=2,
            )
        )
        return 2

    window_cfg = short_research_window_config(windows)

    evidence_mode = str(args.evidence_mode or EVIDENCE_MODE_DIRECT).upper()
    report_dir = Path(args.report_dir) if args.report_dir else default_reports_dir()

    report = await run_short_research_batch(
        symbols=symbols,
        run_id=args.run_id,
        window=window_cfg,
        research_windows=windows,
        run_oos=not args.skip_oos,
        persist=True,
        reports_dir=report_dir,
        evidence_mode=evidence_mode,
    )
    summary = report.get("batch_summary") or {}
    print(print_batch_console_summary(summary))
    print("-")
    print(
        json.dumps(
            {
                "run_id": report.get("run_id"),
                "source_file": report.get("source_file"),
                "summary_file": report.get("summary_file"),
                "evidence_mode": evidence_mode,
                "research_windows": windows.to_dict(),
                "window_status": report.get("window_status"),
                "batch_summary": {
                    k: summary.get(k)
                    for k in (
                        "total_symbols",
                        "healthy_symbols",
                        "review_ready",
                        "review_blocked",
                        "insufficient_oos_sample",
                        "data_failures",
                        "forensic_failures",
                        "paper_eligible",
                        "production_approved",
                        "telegram_eligible",
                    )
                },
                "disclaimer": DISCLAIMER,
                "paper_eligible": False,
                "production_approved": False,
                "telegram_eligible": False,
            },
            indent=2,
            default=str,
        )
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="COMBO_02 SHORT research-only real-symbol report runner"
    )
    p.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated research symbols (default: SHORT research universe)",
    )
    p.add_argument("--timeframe", default=SETUP_TIMEFRAME, help="Setup TF (must be 1h)")
    p.add_argument("--run-id", default=None, help="Optional stable run id (idempotent)")
    p.add_argument("--skip-oos", action="store_true", help="Skip OOS windows")
    p.add_argument("--base-start", default=None)
    p.add_argument("--base-end", default=None)
    p.add_argument("--oos-dev-start", default=None)
    p.add_argument("--oos-dev-end", default=None)
    p.add_argument("--oos-val-start", default=None)
    p.add_argument("--oos-val-end", default=None)
    p.add_argument(
        "--evidence-mode",
        default=EVIDENCE_MODE_DIRECT,
        help="Forensic evidence mode (default: DIRECT_CANDLE_REPLAY)",
    )
    p.add_argument(
        "--report-dir",
        default=None,
        help="Output directory for JSON reports (default: backend/app/reports)",
    )
    args = p.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
