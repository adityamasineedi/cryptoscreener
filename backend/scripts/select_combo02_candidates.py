#!/usr/bin/env python3
"""Reproducible COMBO_02 candidate selector (research only).

Persists an immutable candidate manifest — not the visual Top-100 screen alone.

Usage (from backend/):
  python scripts/select_combo02_candidates.py
  python scripts/select_combo02_candidates.py --top-n 30 --out reports/combo02_candidates_manual.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.research.combo02_candidate_selector import select_candidates  # noqa: E402
from app.research.combo02_candidate_thresholds import CandidateSelectorConfig  # noqa: E402
from app.services.database import db_manager  # noqa: E402


async def _main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    await db_manager.connect(settings)
    try:
        cfg = CandidateSelectorConfig(
            top_n=args.top_n,
            min_history_days=args.min_history_days,
            min_ohlcv_completeness=args.min_completeness,
            min_avg_24h_quote_volume_usd=args.min_quote_volume,
            exclude_v1_universe=not args.include_v1,
        )
        payload = await select_candidates(config=cfg)
    finally:
        await db_manager.close()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else (ROOT / "reports" / f"combo02_candidates_{stamp}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    # Immutable snapshot — strip nothing; list must not change mid-backtest.
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out} ({payload.get('candidate_count', 0)} candidates)", file=sys.stderr)
    print(
        json.dumps(
            {
                "out": str(out),
                "count": payload.get("candidate_count"),
                "symbols": [s.get("symbol") for s in payload.get("symbols") or []],
            },
            indent=2,
        )
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top-n", type=int, default=30)
    ap.add_argument("--min-history-days", type=float, default=540.0)
    ap.add_argument("--min-quote-volume", type=float, default=5_000_000.0)
    ap.add_argument("--min-completeness", type=float, default=0.99)
    ap.add_argument("--include-v1", action="store_true", help="Do not exclude BTC/ETH/SOL")
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
