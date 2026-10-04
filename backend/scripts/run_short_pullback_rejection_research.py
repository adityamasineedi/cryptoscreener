#!/usr/bin/env python3
"""Run SHORT_PULLBACK_REJECTION_RESEARCH (research-only; never paper/Telegram/v1)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure backend/ is on path when run as a script.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.research.short_pullback_rejection.runner import (  # noqa: E402
    run_short_pullback_rejection_research,
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Optional symbol list (default: research universe)",
    )
    p.add_argument("--no-variant-grid", action="store_true")
    p.add_argument(
        "--stop-variant",
        default="stop_buffer_0_25",
        choices=["stop_buffer_0_0", "stop_buffer_0_25", "stop_buffer_0_50"],
    )
    p.add_argument(
        "--tp-variant",
        default="nearest_support",
        choices=["nearest_support", "1.0R", "1.5R", "2.0R"],
    )
    args = p.parse_args()
    report = run_short_pullback_rejection_research(
        symbols=args.symbols,
        stop_variant=args.stop_variant,
        tp_variant=args.tp_variant,
        run_variant_grid=not args.no_variant_grid,
    )
    print(report.get("operator_text") or json.dumps(report, indent=2, default=str))
    print(f"\nReport: {report.get('report_path')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
