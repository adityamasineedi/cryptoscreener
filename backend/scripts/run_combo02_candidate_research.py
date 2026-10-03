#!/usr/bin/env python3
"""Frozen COMBO_02 v1 LONG candidate research runner (research only).

For every candidate in a saved manifest, runs exact COMBO_02 LONG 1h with
HTF_ALIGNED fail-closed via the standard research engine. Does NOT modify
v1_production, paper watcher universe, or Telegram eligibility.

Usage (from backend/):
  python scripts/run_combo02_candidate_research.py
  python scripts/run_combo02_candidate_research.py --manifest reports/combo02_candidates_<ts>.json
  python scripts/run_combo02_candidate_research.py --top-n 10 --skip-oos
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
from app.research.combo02_candidate_research import (  # noqa: E402
    persist_research_artifacts,
    run_candidate_research,
    utc_stamp,
)
from app.research.combo02_candidate_selector import select_candidates  # noqa: E402
from app.research.combo02_candidate_thresholds import CandidateSelectorConfig  # noqa: E402
from app.services.database import db_manager  # noqa: E402


async def _main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    await db_manager.connect(settings)
    stamp = utc_stamp()
    reports = ROOT / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    manifest_path: Path | None = None

    try:
        if args.manifest:
            raw = str(args.manifest).strip()
            if "<" in raw or ">" in raw:
                print(
                    f"ERROR: manifest path looks like a placeholder: {raw}\n"
                    "Replace <UTC> with the real timestamp from select_combo02_candidates.py output.\n"
                    "Example:\n"
                    "  .\\.venv\\Scripts\\python.exe scripts/run_combo02_candidate_research.py "
                    "--manifest reports/combo02_candidates_20261003T195721Z.json",
                    file=sys.stderr,
                )
                return 2
            manifest_path = Path(raw)
            if not manifest_path.is_file():
                # Helpful listing of available manifests
                available = sorted(reports.glob("combo02_candidates_*.json"), reverse=True)
                print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
                if available:
                    print("Available manifests:", file=sys.stderr)
                    for p in available[:10]:
                        print(f"  {p}", file=sys.stderr)
                return 2
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            cfg = CandidateSelectorConfig(top_n=args.top_n)
            if args.min_history_days is not None:
                cfg = CandidateSelectorConfig(
                    top_n=args.top_n,
                    min_history_days=args.min_history_days,
                )
            manifest = await select_candidates(config=cfg)
            manifest_path = reports / f"combo02_candidates_{stamp}.json"
            manifest_path.write_text(
                json.dumps(manifest, indent=2, default=str), encoding="utf-8"
            )
            print(f"Wrote manifest {manifest_path}", file=sys.stderr)

        # Freeze symbols for this run — do not re-query screener mid-batch.
        frozen_symbols = [
            str(s.get("symbol")).upper()
            for s in (manifest.get("symbols") or manifest.get("candidates") or [])
            if s.get("symbol")
        ]
        if not frozen_symbols:
            excl = manifest.get("excluded") or []
            hist_excl = sum(
                1
                for e in excl
                if str(e.get("reason") or "").startswith("insufficient_history")
            )
            print(
                "WARNING: candidate list is empty — nothing to backtest besides v1 refs.\n"
                f"  Manifest: {manifest_path}\n"
                f"  Excluded rows: {len(excl)} (insufficient_history≈{hist_excl})\n"
                "  Default min_history_days=540; your DB may only have ~500d.\n"
                "  Retry selector with a lower floor, e.g.:\n"
                "    .\\.venv\\Scripts\\python.exe scripts/select_combo02_candidates.py "
                "--min-history-days 500 --top-n 10\n"
                "  Then pass the printed path to --manifest.",
                file=sys.stderr,
            )
            if not args.allow_empty:
                print(
                    "Aborting (pass --allow-empty to still write v1-reference-only reports).",
                    file=sys.stderr,
                )
                return 3

        print(
            f"Running frozen COMBO_02 on {len(frozen_symbols)} candidates: "
            f"{', '.join(frozen_symbols) or '(none)'}",
            file=sys.stderr,
        )

        payload = await run_candidate_research(
            manifest=manifest,
            run_oos=not args.skip_oos,
        )
    finally:
        await db_manager.close()

    paths = persist_research_artifacts(
        payload,
        reports_dir=reports,
        stamp=stamp,
        manifest=manifest,
        manifest_path=manifest_path,
    )
    for kind, path in paths.items():
        print(f"Wrote {kind}: {path}", file=sys.stderr)

    finals = payload.get("final_lists") or {}
    print(
        json.dumps(
            {
                "candidates": len(payload.get("results") or []),
                "V2_PAPER_CANDIDATE": finals.get("V2_PAPER_CANDIDATE"),
                "PROMISING_NEEDS_MORE_EVIDENCE": finals.get(
                    "PROMISING_NEEDS_MORE_EVIDENCE"
                ),
                "WATCHLIST": finals.get("WATCHLIST"),
                "REJECT": finals.get("REJECT"),
                "INSUFFICIENT_DATA": finals.get("INSUFFICIENT_DATA"),
                "disclaimer": payload.get("disclaimer"),
                "v1_unchanged": True,
            },
            indent=2,
        )
    )
    print(
        "Frozen v1 remains BTC/ETH/SOL only.\n"
        "No screener candidate was automatically promoted, paper-traded,\n"
        "or made Telegram eligible.",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--manifest",
        type=str,
        default=None,
        help=(
            "Immutable candidate manifest JSON from select_combo02_candidates.py. "
            "Use the real filename printed by the selector "
            "(not the literal placeholder combo02_candidates_<UTC>.json)."
        ),
    )
    ap.add_argument("--top-n", type=int, default=30)
    ap.add_argument(
        "--min-history-days",
        type=float,
        default=None,
        help="Override selector history floor when auto-building a manifest (default 540)",
    )
    ap.add_argument("--skip-oos", action="store_true")
    ap.add_argument(
        "--allow-empty",
        action="store_true",
        help="Allow running when the candidate list is empty (writes v1-reference-only reports)",
    )
    args = ap.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
