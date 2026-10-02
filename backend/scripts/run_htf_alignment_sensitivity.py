"""Run research-only HTF alignment sensitivity on existing diagnostic trade rows.

Does not modify live signals, production thresholds, or recompute entries.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research.htf_alignment_sensitivity import run_from_rows_path, write_outputs

ROWS = Path(__file__).resolve().parent / "trend_regime_trade_rows.json"
OUT_JSON = Path(__file__).resolve().parent / "htf_alignment_sensitivity.json"
OUT_MD = Path(__file__).resolve().parent / "htf_alignment_sensitivity.md"
OUT_SUMMARY = Path(__file__).resolve().parent / "htf_alignment_sensitivity_summary.json"


def main() -> None:
    if not ROWS.is_file():
        raise SystemExit(f"missing trade rows: {ROWS}")
    report = run_from_rows_path(ROWS)
    write_outputs(report, out_json=OUT_JSON, out_md=OUT_MD, out_summary=OUT_SUMMARY)
    print(f"WROTE {OUT_JSON}")
    print(f"WROTE {OUT_MD}")
    print(f"WROTE {OUT_SUMMARY}")
    print(f"n_trades {report['dataset']['n_trades']}")
    print(f"htf_states {report['dataset']['research_htf_state_counts']}")
    for sid, s in report["scenarios"].items():
        print(
            f"{sid}: retained={s['retained_trades']} "
            f"retention_pct={s.get('retention_pct')} "
            f"mean_R={s.get('mean_R')} "
            f"flag={s.get('sample_size_flag')}"
        )


if __name__ == "__main__":
    main()
