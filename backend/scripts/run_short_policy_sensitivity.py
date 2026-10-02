"""Run research-only SHORT policy sensitivity on diagnostic trade rows."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research.short_policy_sensitivity import run_from_rows_path, write_outputs

ROWS = Path(__file__).resolve().parent / "trend_regime_trade_rows.json"
OUT_JSON = Path(__file__).resolve().parent / "short_policy_sensitivity.json"
OUT_MD = Path(__file__).resolve().parent / "short_policy_sensitivity.md"
OUT_SUMMARY = Path(__file__).resolve().parent / "short_policy_sensitivity_summary.json"


def main() -> None:
    if not ROWS.is_file():
        raise SystemExit(f"missing trade rows: {ROWS}")
    report = run_from_rows_path(ROWS)
    write_outputs(report, out_json=OUT_JSON, out_md=OUT_MD, out_summary=OUT_SUMMARY)
    print(f"WROTE {OUT_JSON}")
    print(f"WROTE {OUT_MD}")
    print(f"WROTE {OUT_SUMMARY}")
    print(f"n_trades {report['dataset']['n_trades']}")
    gd = report["gate_draft"]
    print(
        f"gate_draft holds={gd.get('holds_for_gate_draft')} "
        f"suggested={gd.get('suggested_defaults')}"
    )
    for sid, s in report["scenarios"].items():
        print(
            f"{sid}: retained={s['retained_trades']} mean_R={s.get('mean_R')} "
            f"flag={s.get('sample_size_flag')} "
            f"short_n={s['by_direction']['SHORT'].get('n')}"
        )


if __name__ == "__main__":
    main()
