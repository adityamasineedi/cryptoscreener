"""Research-only: run S3 forensic diagnostics for Sep 2024 majors.

Does not modify production engines or optimize S3.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Ensure backend root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.research.bos_strategy_comparison.service import (  # noqa: E402
    get_bos_strategy_comparison_service,
)
from app.services.database import db_manager  # noqa: E402


async def main() -> None:
    await db_manager.connect(get_settings())
    svc = get_bos_strategy_comparison_service()
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    out: dict = {"window": {"start": "2024-09-01", "end": "2024-10-01"}, "symbols": {}}
    for sym in symbols:
        print(f"=== {sym} ===", flush=True)
        report = await svc.s3_diagnostics(
            symbol=sym,
            timeframe="15m",
            start_date="2024-09-01",
            end_date="2024-10-01",
            direction="ALL",
            limit=100,
            trace=False,
            htf_trace=True,
        )
        out["symbols"][sym] = report
        f = report.get("funnel") or {}
        print(
            json.dumps(
                {
                    "funnel": f,
                    "htf_survivors": report.get("htf_survivors"),
                    "htf_matrix": report.get("htf_matrix"),
                    "htf_rejection_reasons": report.get("htf_rejection_reasons"),
                    "htf_direction": report.get("htf_direction"),
                    "htf_data_availability": report.get("htf_data_availability"),
                    "htf_root_cause": report.get("htf_root_cause"),
                    "lookahead": report.get("lookahead"),
                    "production_research_htf_invocation": report.get(
                        "production_research_htf_invocation"
                    ),
                },
                indent=2,
            ),
            flush=True,
        )
    dest = Path(__file__).resolve().parent / "_s3_htf_forensic_sep2024.json"
    dest.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {dest}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
