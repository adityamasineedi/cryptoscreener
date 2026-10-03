"""JSON + Markdown forensic reports (research-only)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research.trade_plan_forensics.thresholds import (
    SAFETY_NO_PROD_CHANGE,
    SAFETY_NO_STRATEGY_SELECT,
)


def build_markdown(report: Mapping[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Trade Plan Forensics Report")
    lines.append("")
    lines.append(f"Generated: {report.get('generated_at')}")
    lines.append("")
    lines.append("## 1. Dataset")
    lines.append(f"- Source: `{report.get('ingest', {}).get('resolved_source')}`")
    lines.append(f"- Raw count: {report.get('ingest', {}).get('raw_count')}")
    lines.append(f"- Filtered count: {report.get('ingest', {}).get('filtered_count')}")
    lines.append(f"- Reconstructed OK: {report.get('population', {}).get('ok')}")
    lines.append(f"- Stopped (missing data): {report.get('population', {}).get('stopped')}")
    lines.append("")
    lines.append("## 2. Trade population")
    pop = report.get("population_metrics") or {}
    for k, v in pop.items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")
    lines.append("## 3. Data quality")
    dq = report.get("data_quality") or {}
    for k, v in dq.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## 4–18. Cross-tabs (summary)")
    tabs = report.get("cross_tabs") or {}
    for name in (
        "by_timeframe",
        "by_htf_state",
        "by_entry_timing",
        "by_regime_primary",
        "by_session_bin",
        "by_direction",
    ):
        lines.append(f"### {name}")
        block = tabs.get(name) or {}
        for key, metrics in block.items():
            lines.append(
                f"- `{key}`: n={metrics.get('n')} win_rate={metrics.get('win_rate')} "
                f"mean_R={metrics.get('mean_R')} sample={metrics.get('sample_status')}"
            )
        lines.append("")
    lines.append("## Winners vs losers")
    wl = report.get("winners_vs_losers") or {}
    lines.append(f"- winners_n={wl.get('winners_n')} losers_n={wl.get('losers_n')}")
    for note in report.get("losing_cluster_notes") or []:
        lines.append(f"- Observation: {note}")
    lines.append("")
    lines.append("## 19. Failure hypotheses")
    lines.append("")
    lines.append("| Potential issue | Evidence | Sample size | Status |")
    lines.append("|---|---|---|---|")
    for row in report.get("issue_table") or []:
        ev = str(row.get("evidence") or "").replace("|", "/")
        lines.append(
            f"| {row.get('potential_issue')} | {ev} | {row.get('sample_size')} | {row.get('status')} |"
        )
    lines.append("")
    lines.append("## 20. Missing data")
    for m in report.get("missing_data") or []:
        lines.append(f"- {m}")
    lines.append("")
    lines.append("## 21. Sample-size warnings")
    for w in report.get("sample_warnings") or []:
        lines.append(f"- {w}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(SAFETY_NO_PROD_CHANGE)
    lines.append("")
    lines.append(SAFETY_NO_STRATEGY_SELECT)
    lines.append("")
    return "\n".join(lines)


def write_reports(report: Mapping[str, Any], out_dir: Path | None = None) -> dict[str, str]:
    out_dir = out_dir or (
        Path(__file__).resolve().parents[3] / "scripts" / "trade_plan_forensics_out"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"trade_plan_forensics_{ts}.json"
    md_path = out_dir / f"trade_plan_forensics_{ts}.md"
    import json

    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md_path.write_text(build_markdown(report), encoding="utf-8")
    # also latest pointers
    (out_dir / "latest.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "latest.md").write_text(build_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}
