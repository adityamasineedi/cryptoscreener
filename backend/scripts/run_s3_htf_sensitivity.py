"""Research-only: S3 HTF sensitivity across majors.

Writes backend/scripts/_s3_htf_sensitivity_report.json
Does not modify production engines.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.research.bos_strategy_comparison.service import (  # noqa: E402
    get_bos_strategy_comparison_service,
)
from app.services.database import db_manager  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--start",
        default="2020-10-01",
        help="Full-period start date (YYYY-MM-DD)",
    )
    parser.add_argument("--end", default=None)
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        help="Include walk-forward windows (slow)",
    )
    parser.add_argument(
        "--sep-only",
        action="store_true",
        help="Only Sep 2024 window (reconcile + sensitivity)",
    )
    args = parser.parse_args()

    await db_manager.connect(get_settings())
    svc = get_bos_strategy_comparison_service()

    if args.sep_only:
        print("Mode: Sep 2024 only", flush=True)
        report = await svc.s3_htf_sensitivity(
            symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            timeframe="15m",
            start_date="2024-09-01",
            end_date="2024-10-01",
            include_walk_forward=False,
            reconcile_sep2024_window=True,
        )
    else:
        print(
            f"Mode: full history start={args.start} walk_forward={args.walk_forward}",
            flush=True,
        )
        report = await svc.s3_htf_sensitivity(
            symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            timeframe="15m",
            start_date=args.start,
            end_date=args.end,
            include_walk_forward=bool(args.walk_forward),
            reconcile_sep2024_window=True,
        )

    dest = Path(__file__).resolve().parent / "_s3_htf_sensitivity_report.json"
    compact = dict(report)
    for sym, run in (compact.get("per_symbol") or {}).items():
        for vid, payload in (run.get("variants") or {}).items():
            trades = payload.get("trades") or []
            payload["trades"] = trades[:50]
            payload["trades_truncated"] = len(trades) > 50
            payload["trades_total"] = len(trades)
    dest.write_text(json.dumps(compact, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report.get("status"),
                "reconciliation": report.get("reconciliation"),
                "warnings": report.get("warnings"),
                "dataset": {
                    "start": (report.get("dataset") or {}).get("start"),
                    "end": (report.get("dataset") or {}).get("end"),
                    "symbols": [
                        {
                            "symbol": r.get("symbol"),
                            "candles_15m": r.get("candles_15m"),
                            "candles_1h": r.get("candles_1h"),
                            "candles_4h": r.get("candles_4h"),
                        }
                        for r in ((report.get("dataset") or {}).get("symbols") or [])
                    ],
                },
                "variants": {
                    vid: {
                        "trade_count": p.get("trade_count"),
                        "sample_status": p.get("sample_status"),
                        "funnel": p.get("funnel"),
                        "expectancy_R": (p.get("metrics") or {})
                        .get("all", {})
                        .get("expectancy_R"),
                        "profit_factor": (p.get("metrics") or {})
                        .get("all", {})
                        .get("profit_factor"),
                        "max_drawdown_R": (p.get("metrics") or {})
                        .get("all", {})
                        .get("max_drawdown_R"),
                        "win_rate": (p.get("metrics") or {})
                        .get("all", {})
                        .get("win_rate"),
                    }
                    for vid, p in (report.get("variants") or {}).items()
                },
                "pairwise": report.get("pairwise_vs_baseline"),
                "baseline_htf_rejection_distribution": report.get(
                    "baseline_htf_rejection_distribution"
                ),
                "robustness_flags": {
                    vid: (r.get("flags") or [])
                    for vid, r in (report.get("robustness") or {}).items()
                },
                "wrote": str(dest),
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
