#!/usr/bin/env python3
"""Run live ↔ backtest entry parity validation (research / paper only).

Usage (from backend/):
  python scripts/live_backtest_parity/run_parity_validation.py
  python scripts/live_backtest_parity/run_parity_validation.py --synthetic
  python scripts/live_backtest_parity/run_parity_validation.py --symbols BTCUSDT,ETHUSDT,SOLUSDT

Never enables live orders or dynamic production Telegram.
Never modifies COMBO_02 parameters or V1.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.research.live_backtest_parity.constants import (  # noqa: E402
    DEFAULT_SYMBOLS,
    DISCLAIMER,
)
from app.research.live_backtest_parity.runner import (  # noqa: E402
    default_output_dir,
    run_parity_validation,
)
from app.services.database import db_manager  # noqa: E402


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _main_async(args: argparse.Namespace) -> int:
    symbols = (
        [s.strip().upper() for s in str(args.symbols).split(",") if s.strip()]
        if args.symbols
        else list(DEFAULT_SYMBOLS)
    )
    out_dir = Path(args.out_dir) if args.out_dir else default_output_dir()
    use_db = not bool(args.synthetic)

    if use_db:
        settings = get_settings()
        try:
            await db_manager.connect(settings)
        except Exception as exc:  # noqa: BLE001
            print(f"DB connect failed ({exc}); falling back to synthetic.", file=sys.stderr)
            use_db = False

    summary = await run_parity_validation(
        symbols=symbols,
        out_dir=out_dir,
        use_db=use_db,
        start=_parse_dt(args.start),
        end=_parse_dt(args.end),
        timeframe=str(args.timeframe or "15m"),
        max_bars=int(args.max_bars) if args.max_bars else None,
    )
    verdict = summary.get("verdict") or {}
    print(json.dumps(
        {
            "disclaimer": DISCLAIMER,
            "output_dir": summary.get("output_dir"),
            "data_source": summary.get("data_source"),
            "total_live_candidates": summary.get("total_live_candidates"),
            "asof_matches": summary.get("asof_matches"),
            "asof_mismatches": summary.get("asof_mismatches"),
            "match_rate": summary.get("match_rate"),
            "verdict": verdict,
            "production_approved": False,
            "telegram_eligible": False,
            "live_orders": False,
        },
        indent=2,
        default=str,
    ))
    # STOP on AS-OF mismatch per acceptance criteria.
    if str(verdict.get("LIVE_BACKTEST_PARITY")) == "FAIL":
        print(
            "STOP: AS-OF live/research mismatch detected. "
            "Do not optimize strategy — investigate mismatch first.",
            file=sys.stderr,
        )
        return 2
    if str(verdict.get("NO_LOOKAHEAD")) == "FAIL":
        print("STOP: future candle access detected.", file=sys.stderr)
        return 3
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Live ↔ backtest entry parity validation (research-only)"
    )
    p.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated symbols (default: BTCUSDT,ETHUSDT,SOLUSDT)",
    )
    p.add_argument("--timeframe", default="15m", help="Setup timeframe (default 15m)")
    p.add_argument("--start", default=None, help="Optional ISO start (UTC)")
    p.add_argument("--end", default=None, help="Optional ISO end exclusive (UTC)")
    p.add_argument(
        "--synthetic",
        action="store_true",
        help="Force synthetic OHLCV (no DB)",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: scripts/live_backtest_parity/)",
    )
    p.add_argument(
        "--max-bars",
        default=None,
        help="Optional cap on setup bars replayed per symbol (after warmup)",
    )
    args = p.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
