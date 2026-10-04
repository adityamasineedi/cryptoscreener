#!/usr/bin/env python3
"""COMBO_02 SHORT entry-timing research runner (research-only).

Never enables paper, live, production, Telegram, or COMBO_02 v1 SHORT.

Usage (from backend/):
  python scripts/run_combo02_short_entry_research.py
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
from app.research.short_entry_research.constants import (  # noqa: E402
    DEFAULT_ENTRY_RESEARCH_WINDOWS,
    DISCLAIMER,
    PARENT_RUN_ID,
    SAFETY_STAMPS,
    STRATEGY_ID,
)
from app.research.short_entry_research.runner import (  # noqa: E402
    run_short_entry_research_async,
)
from app.research.short_research_constants import DEFAULT_SHORT_RESEARCH_UNIVERSE  # noqa: E402
from app.services.database import db_manager  # noqa: E402


async def _main_async(args: argparse.Namespace) -> dict:
    settings = get_settings()
    if getattr(settings, "database_enabled", False):
        await db_manager.connect(settings)
    symbols = [s.strip().upper() for s in str(args.symbols).split(",") if s.strip()]
    return await run_short_entry_research_async(
        symbols=symbols,
        window=DEFAULT_ENTRY_RESEARCH_WINDOWS,
        report_dir=Path(args.report_dir),
        parent_trades_path=Path(args.parent_trades),
        run_oos_backtests=not args.skip_oos,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=DISCLAIMER)
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SHORT_RESEARCH_UNIVERSE),
        help="Comma-separated symbols",
    )
    parser.add_argument(
        "--report-dir",
        default=str(ROOT / "reports" / "short_research"),
    )
    parser.add_argument(
        "--parent-trades",
        default=str(
            ROOT
            / "reports"
            / "short_research"
            / f"short_diagnostic_trades_{PARENT_RUN_ID}.json"
        ),
    )
    parser.add_argument("--skip-oos", action="store_true")
    args = parser.parse_args()

    print(DISCLAIMER)
    print(f"strategy_id={STRATEGY_ID} parent_run={PARENT_RUN_ID}")
    print(f"paper_eligible={SAFETY_STAMPS['paper_eligible']}")

    result = asyncio.run(_main_async(args))
    report = result.get("report") or {}
    print(report.get("operator_text") or "")
    print(f"\nWrote: {result.get('report_path')}")
    print(f"Wrote: {result.get('text_path')}")
    # Compact JSON status
    print(
        json.dumps(
            {
                "run_id": result.get("run_id"),
                "paper_eligible": False,
                "telegram_eligible": False,
                "production_approved": False,
                "oos_status_by_variant": report.get("oos_status_by_variant"),
                "retest_fills": report.get("retest_fills"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
