"""Research-only SHORT policy sensitivity on COMBO_02 diagnostic trade rows.

Does NOT modify live engines, recompute entries, or optimize thresholds.
Scenarios are predefined inclusion filters over historical closed trades.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research.htf_alignment_sensitivity import (
    HTF_ALIGNED,
    HTF_CONFLICT,
    HTF_NEUTRAL_OR_UNAVAILABLE,
    SAMPLE_INSUFFICIENT,
    SAMPLE_THRESHOLD,
    annotate_rows,
    compute_metrics,
    direction_slice,
    research_htf_state,
    sample_size_label,
)

DATASET_LABEL = "BOS Combination Research"
COMBO_ID = "COMBO_02"
SOURCE_ROWS = "trend_regime_trade_rows.json"

SCENARIO_A = "A_CURRENT"
SCENARIO_B = "B_LONG_ONLY"
SCENARIO_C = "C_LONG_PLUS_SHORT_HTF_ALIGNED"
SCENARIO_D = "D_LONG_PLUS_SHORT_15M_ONLY"
SCENARIO_E = "E_LONG_PLUS_SHORT_1H_ONLY"
SCENARIO_F = "F_LONG_PLUS_SHORT_15M_HTF_ALIGNED"

SCENARIO_DEFS = {
    SCENARIO_A: {
        "id": SCENARIO_A,
        "label": "CURRENT",
        "description": "All historical closed trades; no SHORT policy filter.",
    },
    SCENARIO_B: {
        "id": SCENARIO_B,
        "label": "LONG only",
        "description": "Retain LONG trades only; drop all SHORTs.",
    },
    SCENARIO_C: {
        "id": SCENARIO_C,
        "label": "LONG + SHORT HTF_ALIGNED only",
        "description": (
            "Retain all LONGs; retain SHORT only when research HTF state is HTF_ALIGNED."
        ),
    },
    SCENARIO_D: {
        "id": SCENARIO_D,
        "label": "LONG + SHORT 15m only",
        "description": "Retain all LONGs; retain SHORT only on 15m setups.",
    },
    SCENARIO_E: {
        "id": SCENARIO_E,
        "label": "LONG + SHORT 1h only",
        "description": "Retain all LONGs; retain SHORT only on 1h setups.",
    },
    SCENARIO_F: {
        "id": SCENARIO_F,
        "label": "LONG + SHORT 15m HTF_ALIGNED",
        "description": (
            "Retain all LONGs; retain SHORT only on 15m with HTF_ALIGNED."
        ),
    },
}

MULTIPLE_TESTING_RISK = (
    "MULTIPLE_TESTING_RISK: six predefined SHORT-policy scenarios are reported on "
    "the same closed-trade sample. Descriptive only; not a production selection."
)


def scenario_includes(scenario_id: str, row: Mapping[str, Any]) -> bool:
    direction = str(row.get("direction") or "").upper()
    tf = str(row.get("timeframe") or "").lower()
    state = str(row.get("research_htf_state") or research_htf_state(row))

    if scenario_id == SCENARIO_A:
        return True
    if scenario_id == SCENARIO_B:
        return direction == "LONG"
    if scenario_id == SCENARIO_C:
        if direction == "LONG":
            return True
        return direction == "SHORT" and state == HTF_ALIGNED
    if scenario_id == SCENARIO_D:
        if direction == "LONG":
            return True
        return direction == "SHORT" and tf == "15m"
    if scenario_id == SCENARIO_E:
        if direction == "LONG":
            return True
        return direction == "SHORT" and tf == "1h"
    if scenario_id == SCENARIO_F:
        if direction == "LONG":
            return True
        return direction == "SHORT" and tf == "15m" and state == HTF_ALIGNED
    raise ValueError(f"unknown scenario_id={scenario_id}")


def _pct(n: int, d: int) -> float | None:
    if d <= 0:
        return None
    return round(100.0 * n / d, 1)


def evaluate_scenario(
    scenario_id: str,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    original = list(rows)
    retained = [r for r in original if scenario_includes(scenario_id, r)]
    excluded = len(original) - len(retained)
    metrics = compute_metrics(retained)
    short_dropped = sum(
        1
        for r in original
        if str(r.get("direction") or "").upper() == "SHORT"
        and not scenario_includes(scenario_id, r)
    )
    return {
        **SCENARIO_DEFS[scenario_id],
        "orig": len(original),
        "retained_trades": len(retained),
        "excluded_trades": excluded,
        "retention_pct": _pct(len(retained), len(original)),
        "shorts_dropped": short_dropped,
        **metrics,
        "by_direction": {
            "LONG": direction_slice(retained, "LONG"),
            "SHORT": direction_slice(retained, "SHORT"),
        },
        "by_timeframe": {
            tf: compute_metrics([r for r in retained if str(r.get("timeframe") or "").lower() == tf])
            for tf in sorted({str(r.get("timeframe") or "").lower() for r in retained if r.get("timeframe")})
        },
    }


def build_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    annotated = annotate_rows(rows)
    closed = [
        r
        for r in annotated
        if r.get("result") not in (None, "OPEN", "")
    ]
    htf_counts: dict[str, int] = {}
    dir_counts: dict[str, int] = {}
    for r in closed:
        st = str(r.get("research_htf_state") or "")
        htf_counts[st] = htf_counts.get(st, 0) + 1
        d = str(r.get("direction") or "").upper()
        dir_counts[d] = dir_counts.get(d, 0) + 1

    scenarios = {
        sid: evaluate_scenario(sid, closed) for sid in SCENARIO_DEFS
    }

    # Soft recommendation: prefer LONG_ONLY if SHORT mean R < 0 and LONG sample OK
    # and LONG mean R > CURRENT mean R — descriptive gate draft signal only.
    cur = scenarios[SCENARIO_A]
    long_only = scenarios[SCENARIO_B]
    short_slice = cur["by_direction"]["SHORT"]
    long_slice = cur["by_direction"]["LONG"]
    holds = bool(
        short_slice.get("n", 0) >= 10
        and (short_slice.get("mean_R") is not None and short_slice["mean_R"] < 0)
        and (long_slice.get("mean_R") is not None and long_slice["mean_R"] > 0)
        and (long_only.get("mean_R") is not None and cur.get("mean_R") is not None)
        and long_only["mean_R"] > cur["mean_R"]
    )
    gate_draft = {
        "holds_for_gate_draft": holds,
        "suggested_defaults": {
            "research_gate_enabled": False,
            "research_gate_block_shorts": True if holds else False,
            "research_gate_block_htf_conflict": False,
        },
        "rationale": (
            "SHORT mean R negative while LONG mean R positive; LONG_ONLY improves "
            "aggregate mean R vs CURRENT on this sample."
            if holds
            else "Sample does not clearly support enabling a live SHORT block yet."
        ),
        "caveat": (
            f"Requires SHORT n>=10 and descriptive improvement; "
            f"SAMPLE_THRESHOLD={SAMPLE_THRESHOLD}. Not a profitability claim."
        ),
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "label": DATASET_LABEL,
            "combo": COMBO_ID,
            "source": SOURCE_ROWS,
            "n_trades": len(closed),
            "directions": dir_counts,
            "research_htf_state_counts": htf_counts,
            "symbols": sorted({str(r.get("symbol")) for r in closed if r.get("symbol")}),
            "timeframes": sorted({str(r.get("timeframe")) for r in closed if r.get("timeframe")}),
        },
        "scenarios": scenarios,
        "gate_draft": gate_draft,
        "warnings": [
            MULTIPLE_TESTING_RISK,
            "Research only. Live engines unchanged unless research_gate_* flags are explicitly enabled.",
            "Unavailable HTF never treated as bullish/bearish.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    ds = report["dataset"]
    lines = [
        "# SHORT Policy Sensitivity Study (Research Only)",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Dataset: {ds['label']} · Combo: `{ds['combo']}` · Source: `{ds['source']}`",
        "",
        "> Analysis only. Existing trades preserved. Live engines unchanged by default.",
        "",
        "## 1. Dataset",
        "",
        f"- Closed trades: **{ds['n_trades']}**",
        f"- Symbols: `{ds['symbols']}`",
        f"- Timeframes: `{ds['timeframes']}`",
        f"- Directions: `{ds['directions']}`",
        f"- Research HTF states: `{ds['research_htf_state_counts']}`",
        "",
        "## 2. Scenario results",
        "",
        "| Scenario | retained | excl | ret% | WR | mean R | PF | SHORT n/WR/meanR | LONG n/WR/meanR | sample |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for sid, s in report["scenarios"].items():
        sh = s["by_direction"]["SHORT"]
        lg = s["by_direction"]["LONG"]

        def _fmt_side(side: dict[str, Any]) -> str:
            wr = side.get("win_rate")
            wr_s = f"{wr*100:.1f}%" if wr is not None else "—"
            mr = side.get("mean_R")
            mr_s = f"{mr:.3f}" if mr is not None else "—"
            return f"{side.get('n', 0)} / {wr_s} / {mr_s}"

        wr = s.get("win_rate")
        wr_s = f"{wr*100:.1f}%" if wr is not None else "—"
        mr = s.get("mean_R")
        mr_s = f"{mr:.3f}" if mr is not None else "—"
        pf = s.get("profit_factor")
        pf_s = f"{pf:.3f}" if pf is not None else "—"
        lines.append(
            f"| {s['label']} | {s['retained_trades']} | {s['excluded_trades']} | "
            f"{s.get('retention_pct')} | {wr_s} | {mr_s} | {pf_s} | "
            f"{_fmt_side(sh)} | {_fmt_side(lg)} | {s.get('sample_size_flag')} |"
        )

    gd = report["gate_draft"]
    lines.extend(
        [
            "",
            "## 3. Gate draft signal (not auto-enabled)",
            "",
            f"- holds_for_gate_draft: **{gd['holds_for_gate_draft']}**",
            f"- suggested_defaults: `{gd['suggested_defaults']}`",
            f"- rationale: {gd['rationale']}",
            f"- caveat: {gd['caveat']}",
            "",
            "## 4. Warnings",
            "",
        ]
    )
    for w in report.get("warnings") or []:
        lines.append(f"- {w}")
    lines.append("")
    return "\n".join(lines)


def run_from_rows_path(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("trade rows file must be a JSON list")
    return build_report(raw)


def write_outputs(
    report: dict[str, Any],
    *,
    out_json: Path,
    out_md: Path,
    out_summary: Path | None = None,
) -> None:
    out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    out_md.write_text(render_markdown(report), encoding="utf-8")
    if out_summary is not None:
        summary = {
            "generated_at": report["generated_at"],
            "n_trades": report["dataset"]["n_trades"],
            "gate_draft": report["gate_draft"],
            "scenarios": {
                sid: {
                    "label": s["label"],
                    "retained": s["retained_trades"],
                    "mean_R": s.get("mean_R"),
                    "win_rate": s.get("win_rate"),
                    "sample_size_flag": s.get("sample_size_flag"),
                    "short": s["by_direction"]["SHORT"],
                    "long": s["by_direction"]["LONG"],
                }
                for sid, s in report["scenarios"].items()
            },
        }
        out_summary.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
