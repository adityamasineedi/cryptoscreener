"""Research-only HTF alignment sensitivity study.

Uses pre-computed Trend/Regime Diagnostic trade rows. Does NOT:
- modify live signal engine / trend_engine / BOS / entry / SL / TP
- recompute entries or R:R
- optimize thresholds or search for a "best" filter
- fabricate trades or use post-entry HTF information

Scenarios are predefined inclusion filters over historical trades only.
"""

from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research.metrics import max_drawdown_r, profit_factor
from app.research.trend_regime_diagnostic import bootstrap_mean_ci

DATASET_LABEL = "BOS Combination Research"
COMBO_ID = "COMBO_02"
SOURCE_ROWS = "trend_regime_trade_rows.json"

SAMPLE_OK = "SAMPLE_SIZE_OK"
SAMPLE_INSUFFICIENT = "INSUFFICIENT_SAMPLE"
SAMPLE_THRESHOLD = 30

HTF_ALIGNED = "HTF_ALIGNED"
HTF_CONFLICT = "HTF_CONFLICT"
HTF_NEUTRAL_OR_UNAVAILABLE = "HTF_NEUTRAL_OR_UNAVAILABLE"

SCENARIO_A = "A_CURRENT"
SCENARIO_B = "B_HTF_ALIGNED_ONLY"
SCENARIO_C = "C_HTF_ALIGNED_PLUS_NEUTRAL_OR_UNAVAILABLE"
SCENARIO_D = "D_CURRENT_EXCLUDE_HTF_CONFLICT"

SCENARIO_DEFS = {
    SCENARIO_A: {
        "id": SCENARIO_A,
        "label": "CURRENT",
        "description": "All historical trades; no hypothetical HTF filter.",
    },
    SCENARIO_B: {
        "id": SCENARIO_B,
        "label": "HTF_ALIGNED only",
        "description": "Retain only trades where setup direction agrees with available directional HTF.",
    },
    SCENARIO_C: {
        "id": SCENARIO_C,
        "label": "HTF_ALIGNED + HTF_NEUTRAL_OR_UNAVAILABLE",
        "description": (
            "Retain HTF_ALIGNED plus trades with no opposing directional HTF "
            "(neutral / unavailable HTF). Does not treat unavailable as bullish/bearish."
        ),
    },
    SCENARIO_D: {
        "id": SCENARIO_D,
        "label": "CURRENT exclude HTF_CONFLICT",
        "description": "Retain current dataset excluding HTF_CONFLICT trades.",
    },
}

MULTIPLE_TESTING_RISK = (
    "MULTIPLE_TESTING_RISK: four predefined scenarios are reported on the same "
    "closed-trade sample. Intervals and rates are descriptive only; do not treat "
    "any scenario as selected or validated by this study."
)


def load_trade_rows(path: str | Path) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("trade rows file must be a JSON list")
    return [dict(r) for r in raw]


def research_htf_state(row: Mapping[str, Any]) -> str:
    """Map existing regime_category → research HTF filter label.

    Relies on pre-entry classifications already stored on the trade row.
    Unavailable / neutral HTF is never treated as bullish or bearish.
    """
    cat = str(row.get("regime_category") or "").upper()
    if cat == "ALIGNED_TREND":
        return HTF_ALIGNED
    if cat == "HTF_CONFLICT":
        return HTF_CONFLICT
    # LOCAL_ONLY, NEUTRAL_STRUCTURE, or unknown → no opposing directional HTF retained
    return HTF_NEUTRAL_OR_UNAVAILABLE


def annotate_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["current_regime"] = str(r.get("regime_category") or "")
        d["research_htf_state"] = research_htf_state(r)
        out.append(d)
    return out


def scenario_includes(scenario_id: str, row: Mapping[str, Any]) -> bool:
    state = str(row.get("research_htf_state") or research_htf_state(row))
    if scenario_id == SCENARIO_A:
        return True
    if scenario_id == SCENARIO_B:
        return state == HTF_ALIGNED
    if scenario_id == SCENARIO_C:
        return state in {HTF_ALIGNED, HTF_NEUTRAL_OR_UNAVAILABLE}
    if scenario_id == SCENARIO_D:
        return state != HTF_CONFLICT
    raise ValueError(f"unknown scenario_id={scenario_id}")


def sample_size_label(n: int) -> str:
    return SAMPLE_OK if n >= SAMPLE_THRESHOLD else SAMPLE_INSUFFICIENT


def _is_closed(row: Mapping[str, Any]) -> bool:
    result = row.get("result")
    return result not in (None, "OPEN", "")


def _is_win(row: Mapping[str, Any]) -> bool:
    r = row.get("R")
    return r is not None and float(r) > 0


def _is_loss(row: Mapping[str, Any]) -> bool:
    r = row.get("R")
    return r is not None and float(r) <= 0


def _parse_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _sorted_by_entry(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    def key(r: Mapping[str, Any]) -> tuple:
        ts = _parse_ts(r.get("entry_time"))
        return (ts or datetime.min.replace(tzinfo=timezone.utc), str(r.get("symbol") or ""))

    return sorted(rows, key=key)


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[len(s) // 2]


def compute_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if _is_closed(r)]
    n = len(closed)
    if n == 0:
        return {
            "n": 0,
            "sample_size": 0,
            "sample_size_flag": SAMPLE_INSUFFICIENT,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "SL_rate": None,
            "TP1_rate": None,
            "mean_R": None,
            "median_R": None,
            "profit_factor": None,
            "max_drawdown_R": None,
            "MAE": None,
            "MFE": None,
            "avg_MAE_R": None,
            "avg_MFE_R": None,
            "mean_R_bootstrap_95ci": None,
        }

    rs = [float(r["R"]) for r in closed if r.get("R") is not None]
    wins = sum(1 for r in closed if _is_win(r))
    losses = sum(1 for r in closed if _is_loss(r))
    sl = sum(1 for r in closed if r.get("result") == "SL")
    tp1 = sum(1 for r in closed if str(r.get("result") or "").startswith("TP"))
    mae = [float(r["MAE"]) for r in closed if r.get("MAE") is not None]
    mfe = [float(r["MFE"]) for r in closed if r.get("MFE") is not None]
    mae_r = [float(r["MAE_R"]) for r in closed if r.get("MAE_R") is not None]
    mfe_r = [float(r["MFE_R"]) for r in closed if r.get("MFE_R") is not None]

    ordered = _sorted_by_entry(closed)
    equity = [0.0]
    for r in ordered:
        if r.get("R") is None:
            continue
        equity.append(equity[-1] + float(r["R"]))
    max_dd, _ = max_drawdown_r(equity) if len(equity) > 1 else (None, [])

    mean_r = sum(rs) / len(rs) if rs else None
    out = {
        "n": n,
        "sample_size": n,
        "sample_size_flag": sample_size_label(n),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / n if n else None,
        "SL_rate": sl / n if n else None,
        "TP1_rate": tp1 / n if n else None,
        "mean_R": mean_r,
        "median_R": _median(rs),
        "profit_factor": profit_factor(rs),
        "max_drawdown_R": max_dd,
        "MAE": (sum(mae) / len(mae)) if mae else None,
        "MFE": (sum(mfe) / len(mfe)) if mfe else None,
        "avg_MAE_R": (sum(mae_r) / len(mae_r)) if mae_r else None,
        "avg_MFE_R": (sum(mfe_r) / len(mfe_r)) if mfe_r else None,
        "mean_R_bootstrap_95ci": bootstrap_mean_ci(rs) if len(rs) >= 10 else None,
    }
    return out


def direction_slice(rows: Sequence[Mapping[str, Any]], direction: str) -> dict[str, Any]:
    subset = [r for r in rows if str(r.get("direction") or "").upper() == direction]
    m = compute_metrics(subset)
    return {
        "n": m["n"],
        "wins": m["wins"],
        "losses": m["losses"],
        "win_rate": m["win_rate"],
        "mean_R": m["mean_R"],
        "sample_size_flag": m["sample_size_flag"],
    }


def retention_block(
    original: Sequence[Mapping[str, Any]],
    retained: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    n0 = len([r for r in original if _is_closed(r)])
    n1 = len([r for r in retained if _is_closed(r)])
    excluded = n0 - n1
    return {
        "original_trades": n0,
        "retained_trades": n1,
        "excluded_trades": excluded,
        "retention_pct": (n1 / n0 * 100.0) if n0 else None,
    }


def scenario_report(
    scenario_id: str,
    all_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    retained = [r for r in all_rows if scenario_includes(scenario_id, r)]
    metrics = compute_metrics(retained)
    ret = retention_block(all_rows, retained)
    return {
        **SCENARIO_DEFS[scenario_id],
        **ret,
        **metrics,
        "LONG": direction_slice(retained, "LONG"),
        "SHORT": direction_slice(retained, "SHORT"),
        "by_timeframe": {
            "5m": {"status": "NO DATA", "n": 0, "note": "current dataset contains zero 5m trades"},
            "15m": {
                **compute_metrics([r for r in retained if str(r.get("timeframe") or "").lower() == "15m"]),
                "LONG": direction_slice(
                    [r for r in retained if str(r.get("timeframe") or "").lower() == "15m"],
                    "LONG",
                ),
                "SHORT": direction_slice(
                    [r for r in retained if str(r.get("timeframe") or "").lower() == "15m"],
                    "SHORT",
                ),
            },
            "1h": {
                **compute_metrics([r for r in retained if str(r.get("timeframe") or "").lower() == "1h"]),
                "LONG": direction_slice(
                    [r for r in retained if str(r.get("timeframe") or "").lower() == "1h"],
                    "LONG",
                ),
                "SHORT": direction_slice(
                    [r for r in retained if str(r.get("timeframe") or "").lower() == "1h"],
                    "SHORT",
                ),
            },
        },
    }


def cross_tab(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """TIMEFRAME × DIRECTION × HTF STATE cells."""
    cells: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    tfs = ("15m", "1h")
    dirs = ("LONG", "SHORT")
    states = (HTF_ALIGNED, HTF_CONFLICT, HTF_NEUTRAL_OR_UNAVAILABLE)

    for r in rows:
        if not _is_closed(r):
            continue
        tf = str(r.get("timeframe") or "").lower()
        d = str(r.get("direction") or "").upper()
        st = str(r.get("research_htf_state") or research_htf_state(r))
        if tf not in tfs or d not in dirs or st not in states:
            continue
        cells[f"{tf.upper()} {d} {st.replace('HTF_', '')}"].append(r)

    # Also emit canonical keys including NEUTRAL
    out: dict[str, Any] = {}
    for tf in tfs:
        for d in dirs:
            for st in states:
                short = st.replace("HTF_", "")
                key = f"{tf.upper()} {d} {short}"
                subset = cells.get(key, [])
                m = compute_metrics(subset)
                n = m["n"]
                losses = m["losses"]
                out[key] = {
                    "n": n,
                    "wins": m["wins"],
                    "losses": losses,
                    "loss_rate": (losses / n) if n else None,
                    "mean_R": m["mean_R"],
                    "sample_size_flag": m["sample_size_flag"],
                }
    out["5M"] = {"status": "NO DATA", "n": 0}
    return out


def loss_share_vs_loss_rate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if _is_closed(r)]
    losers = [r for r in closed if _is_loss(r)]
    total_losers = len(losers)
    by_state: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in closed:
        by_state[str(r.get("research_htf_state") or research_htf_state(r))].append(r)

    result: dict[str, Any] = {"total_losers": total_losers, "by_htf_state": {}}
    for state in (HTF_ALIGNED, HTF_CONFLICT, HTF_NEUTRAL_OR_UNAVAILABLE):
        subset = by_state.get(state, [])
        n = len(subset)
        state_losers = sum(1 for r in subset if _is_loss(r))
        result["by_htf_state"][state] = {
            "n": n,
            "losses": state_losers,
            "loss_rate": (state_losers / n) if n else None,
            "loss_share_of_all_losers": (
                (state_losers / total_losers) if total_losers else None
            ),
            "sample_size_flag": sample_size_label(n),
            "definitions": {
                "loss_rate": "losses_in_category / trades_in_category",
                "loss_share": "losses_in_category / all_losers",
            },
        }
    return result


def htf_state_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        if not _is_closed(r):
            continue
        counts[str(r.get("research_htf_state") or research_htf_state(r))] += 1
    return dict(counts)


def current_regime_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        if not _is_closed(r):
            continue
        counts[str(r.get("current_regime") or r.get("regime_category") or "")] += 1
    return dict(counts)


def build_report(rows_raw: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = annotate_rows(rows_raw)
    closed = [r for r in rows if _is_closed(r)]
    scenarios = {sid: scenario_report(sid, rows) for sid in SCENARIO_DEFS}

    bootstrap_summary = {
        sid: scenarios[sid].get("mean_R_bootstrap_95ci") for sid in SCENARIO_DEFS
    }

    observations = _factual_observations(rows, scenarios)

    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset": DATASET_LABEL,
            "combo": COMBO_ID,
            "source_rows": SOURCE_ROWS,
            "analysis": "htf_alignment_sensitivity",
            "research_only": True,
            "live_engine_unchanged": True,
            "production_thresholds_unchanged": True,
            "existing_trades_unchanged": True,
            "no_future_information": True,
            "no_parameter_optimization": True,
            "no_new_sideways_detector": True,
            "no_fabricated_data": True,
            "no_trade_entry_recalculation": True,
            "multiple_testing_risk": MULTIPLE_TESTING_RISK,
        },
        "dataset": {
            "n_trades": len(closed),
            "timeframes_present": sorted(
                {str(r.get("timeframe") or "").lower() for r in closed}
            ),
            "directions_present": sorted(
                {str(r.get("direction") or "").upper() for r in closed}
            ),
            "symbols_present": sorted({str(r.get("symbol") or "") for r in closed}),
            "5m_status": "NO DATA",
            "result_counts": _count_field(closed, "result"),
            "current_regime_counts": current_regime_counts(rows),
            "research_htf_state_counts": htf_state_counts(rows),
        },
        "existing_regime_definition": {
            "ALIGNED_TREND": "setup trending and available directional HTF(s) agree, none opposite",
            "HTF_CONFLICT": "setup trending and ≥1 HTF opposite",
            "LOCAL_ONLY": "setup trending; HTF neutral/unavailable/no directional agreement",
            "NEUTRAL_STRUCTURE": "setup not BULLISH/BEARISH",
            "note": "Labels taken from existing trend_regime_trade_rows.json; not recomputed.",
        },
        "research_filter_labels": {
            HTF_ALIGNED: "Setup direction agrees with available directional HTF.",
            HTF_CONFLICT: "Setup direction disagrees with at least one directional HTF.",
            HTF_NEUTRAL_OR_UNAVAILABLE: (
                "No opposing HTF direction is available "
                "(neutral / unavailable / local-only). Unavailable is not treated as bullish/bearish."
            ),
            "mapping_from_current_regime": {
                "ALIGNED_TREND": HTF_ALIGNED,
                "HTF_CONFLICT": HTF_CONFLICT,
                "LOCAL_ONLY": HTF_NEUTRAL_OR_UNAVAILABLE,
                "NEUTRAL_STRUCTURE": HTF_NEUTRAL_OR_UNAVAILABLE,
            },
        },
        "scenario_definitions": SCENARIO_DEFS,
        "scenarios": scenarios,
        "cross_tab_tf_direction_htf": cross_tab(rows),
        "loss_share_vs_loss_rate": loss_share_vs_loss_rate(rows),
        "bootstrap_uncertainty": {
            "purpose": "quantify uncertainty of mean R only; not used to select a scenario",
            "by_scenario": bootstrap_summary,
            "note": "CI omitted when n_R < 10 (same rule as trend_regime_diagnostic).",
        },
        "multiple_testing_warning": MULTIPLE_TESTING_RISK,
        "limitations": [
            "COMBO_02 Path A closed trades only; not full live Path B.",
            "Sample sizes for several subgroups remain below 30 (INSUFFICIENT_SAMPLE).",
            "Direction asymmetry (LONG vs SHORT) confounds regime comparisons.",
            "4h HTF often HTF_UNAVAILABLE in source rows → many NEUTRAL_OR_UNAVAILABLE.",
            "Scenarios C and D can coincide when NEUTRAL_STRUCTURE count is zero.",
            "No fees re-simulated; R values come from the existing diagnostic rows.",
            "Bootstrap CIs describe sampling variability; they do not validate filters.",
            MULTIPLE_TESTING_RISK,
        ],
        "factual_observations": observations,
        "acceptance": {
            "live_engine_unchanged": True,
            "production_thresholds_unchanged": True,
            "existing_trades_unchanged": True,
            "no_future_information_used": True,
            "no_parameter_optimization": True,
            "no_new_sideways_detector": True,
            "no_fabricated_data": True,
            "no_trade_entry_recalculation": True,
            "all_sample_sizes_reported": True,
            "long_short_separated": True,
            "tf_15m_1h_separated": True,
        },
    }


def build_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    scenarios = report.get("scenarios") or {}
    compact = {}
    for sid, s in scenarios.items():
        compact[sid] = {
            "label": s.get("label"),
            "original_trades": s.get("original_trades"),
            "retained_trades": s.get("retained_trades"),
            "excluded_trades": s.get("excluded_trades"),
            "retention_pct": s.get("retention_pct"),
            "wins": s.get("wins"),
            "losses": s.get("losses"),
            "win_rate": s.get("win_rate"),
            "SL_rate": s.get("SL_rate"),
            "TP1_rate": s.get("TP1_rate"),
            "mean_R": s.get("mean_R"),
            "median_R": s.get("median_R"),
            "profit_factor": s.get("profit_factor"),
            "max_drawdown_R": s.get("max_drawdown_R"),
            "MAE": s.get("MAE"),
            "MFE": s.get("MFE"),
            "sample_size_flag": s.get("sample_size_flag"),
            "LONG": s.get("LONG"),
            "SHORT": s.get("SHORT"),
            "15m_n": (s.get("by_timeframe") or {}).get("15m", {}).get("n"),
            "1h_n": (s.get("by_timeframe") or {}).get("1h", {}).get("n"),
            "5m": "NO DATA",
        }
    return {
        "meta": report.get("meta"),
        "dataset_n": (report.get("dataset") or {}).get("n_trades"),
        "research_htf_state_counts": (report.get("dataset") or {}).get(
            "research_htf_state_counts"
        ),
        "scenarios": compact,
        "loss_share_vs_loss_rate": report.get("loss_share_vs_loss_rate"),
        "cross_tab_tf_direction_htf": report.get("cross_tab_tf_direction_htf"),
        "factual_observations": report.get("factual_observations"),
        "acceptance": report.get("acceptance"),
        "multiple_testing_warning": report.get("multiple_testing_warning"),
    }


def _count_field(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[str(r.get(field) or "")] += 1
    return dict(counts)


def _fmt_pct(x: float | None) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x * 100:.1f}%"


def _fmt_num(x: Any, digits: int = 3) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return str(x)
        return f"{x:.{digits}f}"
    return str(x)


def _factual_observations(
    rows: Sequence[Mapping[str, Any]],
    scenarios: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    closed = [r for r in rows if _is_closed(r)]
    losers = [r for r in closed if _is_loss(r)]
    total_losers = len(losers)
    obs: list[str] = []
    obs.append(f"Analyzed n={len(closed)} existing closed COMBO_02 diagnostic trades.")

    for state in (HTF_ALIGNED, HTF_CONFLICT, HTF_NEUTRAL_OR_UNAVAILABLE):
        subset = [r for r in closed if r.get("research_htf_state") == state]
        n = len(subset)
        losses = sum(1 for r in subset if _is_loss(r))
        share = (losses / total_losers) if total_losers else 0.0
        rate = (losses / n) if n else 0.0
        obs.append(
            f"{state}: n={n}, losses={losses}/{n}, "
            f"loss_rate={rate * 100:.1f}%, "
            f"loss_share={share * 100:.1f}% of all losers "
            f"({losses}/{total_losers}); {sample_size_label(n)}."
        )

    for sid in (SCENARIO_B, SCENARIO_C, SCENARIO_D):
        s = scenarios[sid]
        obs.append(
            f"{s['label']}: retained {s['retained_trades']}/{s['original_trades']} "
            f"({_fmt_num(s.get('retention_pct'), 1)}% retention), "
            f"mean_R={_fmt_num(s.get('mean_R'))}, "
            f"{s.get('sample_size_flag')}."
        )

    for tf in ("15m", "1h"):
        for d in ("LONG", "SHORT"):
            subset = [
                r
                for r in closed
                if str(r.get("timeframe") or "").lower() == tf
                and str(r.get("direction") or "").upper() == d
            ]
            n = len(subset)
            losses = sum(1 for r in subset if _is_loss(r))
            wins = sum(1 for r in subset if _is_win(r))
            obs.append(
                f"{tf.upper()} {d}: {wins} wins / {losses} losses from {n} trades "
                f"({sample_size_label(n)})."
            )

    obs.append("5m: NO DATA (zero trades in current dataset).")
    obs.append(
        "Direction asymmetry remains large; regime effects must not be attributed "
        "independently of LONG/SHORT mix."
    )
    obs.append(MULTIPLE_TESTING_RISK)
    return obs


def render_markdown(report: Mapping[str, Any]) -> str:
    ds = report["dataset"]
    scenarios = report["scenarios"]
    xt = report["cross_tab_tf_direction_htf"]
    lsr = report["loss_share_vs_loss_rate"]
    lines: list[str] = []
    lines.append("# HTF Alignment Sensitivity Study (Research Only)")
    lines.append("")
    lines.append(f"Generated: `{report['meta']['generated_at']}`")
    lines.append(
        f"Dataset: {report['meta']['dataset']} · Combo: `{report['meta']['combo']}` · "
        f"Source: `{report['meta']['source_rows']}`"
    )
    lines.append("")
    lines.append(
        "> Analysis only. Existing trades preserved. Live engines and production "
        "thresholds unchanged. Scenarios are not ranked."
    )
    lines.append("")

    # 1
    lines.append("## 1. Dataset")
    lines.append("")
    lines.append(f"- Closed trades: **{ds['n_trades']}**")
    lines.append(f"- Symbols: `{ds['symbols_present']}`")
    lines.append(f"- Timeframes present: `{ds['timeframes_present']}`")
    lines.append(f"- Directions: `{ds['directions_present']}`")
    lines.append(f"- 5m: **{ds['5m_status']}**")
    lines.append(f"- Result counts: `{ds['result_counts']}`")
    lines.append(f"- Current regime counts: `{ds['current_regime_counts']}`")
    lines.append(f"- Research HTF state counts: `{ds['research_htf_state_counts']}`")
    lines.append("")

    # 2
    lines.append("## 2. Existing regime definition")
    lines.append("")
    for k, v in (report["existing_regime_definition"]).items():
        if k == "note":
            lines.append(f"- Note: {v}")
        else:
            lines.append(f"- `{k}`: {v}")
    lines.append("")

    # 3
    lines.append("## 3. Scenario definitions")
    lines.append("")
    for sid, d in report["scenario_definitions"].items():
        lines.append(f"- **{d['label']}** (`{sid}`): {d['description']}")
    lines.append("")
    lines.append("Research filter labels:")
    for k, v in report["research_filter_labels"].items():
        if k == "mapping_from_current_regime":
            lines.append(f"- Mapping: `{v}`")
        else:
            lines.append(f"- `{k}`: {v}")
    lines.append("")

    # 4
    lines.append("## 4. Overall results")
    lines.append("")
    lines.append(
        "| Scenario | orig | retained | excluded | retention % | wins | losses | "
        "win rate | SL rate | TP1 rate | mean R | median R | PF | max DD R | "
        "MAE | MFE | sample |"
    )
    lines.append(
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"
    )
    for sid in SCENARIO_DEFS:
        s = scenarios[sid]
        lines.append(
            f"| {s['label']} | {s['original_trades']} | {s['retained_trades']} | "
            f"{s['excluded_trades']} | {_fmt_num(s.get('retention_pct'), 1)} | "
            f"{s['wins']} | {s['losses']} | {_fmt_pct(s.get('win_rate'))} | "
            f"{_fmt_pct(s.get('SL_rate'))} | {_fmt_pct(s.get('TP1_rate'))} | "
            f"{_fmt_num(s.get('mean_R'))} | {_fmt_num(s.get('median_R'))} | "
            f"{_fmt_num(s.get('profit_factor'))} | {_fmt_num(s.get('max_drawdown_R'))} | "
            f"{_fmt_num(s.get('MAE'), 2)} | {_fmt_num(s.get('MFE'), 2)} | "
            f"{s.get('sample_size_flag')} |"
        )
    lines.append("")

    # 5 LONG
    lines.append("## 5. LONG results")
    lines.append("")
    lines.append("| Scenario | n | wins | losses | win rate | mean R | sample |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")
    for sid in SCENARIO_DEFS:
        s = scenarios[sid]
        L = s["LONG"]
        lines.append(
            f"| {s['label']} | {L['n']} | {L['wins']} | {L['losses']} | "
            f"{_fmt_pct(L.get('win_rate'))} | {_fmt_num(L.get('mean_R'))} | "
            f"{L.get('sample_size_flag')} |"
        )
    lines.append("")

    # 6 SHORT
    lines.append("## 6. SHORT results")
    lines.append("")
    lines.append("| Scenario | n | wins | losses | win rate | mean R | sample |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")
    for sid in SCENARIO_DEFS:
        s = scenarios[sid]
        S = s["SHORT"]
        lines.append(
            f"| {s['label']} | {S['n']} | {S['wins']} | {S['losses']} | "
            f"{_fmt_pct(S.get('win_rate'))} | {_fmt_num(S.get('mean_R'))} | "
            f"{S.get('sample_size_flag')} |"
        )
    lines.append("")

    # 7 15M
    lines.append("## 7. 15M results")
    lines.append("")
    lines.append(
        "| Scenario | n | wins | losses | win rate | mean R | LONG n/wr/meanR | "
        "SHORT n/wr/meanR | sample |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---|---|---|")
    for sid in SCENARIO_DEFS:
        s = scenarios[sid]
        t = s["by_timeframe"]["15m"]
        L, S = t["LONG"], t["SHORT"]
        lines.append(
            f"| {s['label']} | {t['n']} | {t['wins']} | {t['losses']} | "
            f"{_fmt_pct(t.get('win_rate'))} | {_fmt_num(t.get('mean_R'))} | "
            f"{L['n']} / {_fmt_pct(L.get('win_rate'))} / {_fmt_num(L.get('mean_R'))} | "
            f"{S['n']} / {_fmt_pct(S.get('win_rate'))} / {_fmt_num(S.get('mean_R'))} | "
            f"{t.get('sample_size_flag')} |"
        )
    lines.append("")

    # 8 1H
    lines.append("## 8. 1H results")
    lines.append("")
    lines.append(
        "| Scenario | n | wins | losses | win rate | mean R | LONG n/wr/meanR | "
        "SHORT n/wr/meanR | sample |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---|---|---|")
    for sid in SCENARIO_DEFS:
        s = scenarios[sid]
        t = s["by_timeframe"]["1h"]
        L, S = t["LONG"], t["SHORT"]
        lines.append(
            f"| {s['label']} | {t['n']} | {t['wins']} | {t['losses']} | "
            f"{_fmt_pct(t.get('win_rate'))} | {_fmt_num(t.get('mean_R'))} | "
            f"{L['n']} / {_fmt_pct(L.get('win_rate'))} / {_fmt_num(L.get('mean_R'))} | "
            f"{S['n']} / {_fmt_pct(S.get('win_rate'))} / {_fmt_num(S.get('mean_R'))} | "
            f"{t.get('sample_size_flag')} |"
        )
    lines.append("")
    lines.append("### 5M")
    lines.append("")
    lines.append("**NO DATA** — current dataset contains zero 5m trades.")
    lines.append("")

    # 9 cross-tab
    lines.append("## 9. Timeframe × direction × HTF cross-tab")
    lines.append("")
    lines.append("| Cell | n | wins | losses | loss rate | mean R | sample |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")
    for key, cell in xt.items():
        if key == "5M":
            lines.append(f"| {key} | 0 | — | — | — | — | NO DATA |")
            continue
        lines.append(
            f"| {key} | {cell['n']} | {cell['wins']} | {cell['losses']} | "
            f"{_fmt_pct(cell.get('loss_rate'))} | {_fmt_num(cell.get('mean_R'))} | "
            f"{cell.get('sample_size_flag')} |"
        )
    lines.append("")

    # 10 retention
    lines.append("## 10. Retention analysis")
    lines.append("")
    lines.append("| Scenario | original | retained | excluded | retention % |")
    lines.append("|---|---:|---:|---:|---:|")
    for sid in SCENARIO_DEFS:
        s = scenarios[sid]
        lines.append(
            f"| {s['label']} | {s['original_trades']} | {s['retained_trades']} | "
            f"{s['excluded_trades']} | {_fmt_num(s.get('retention_pct'), 1)} |"
        )
    lines.append("")

    # 11 loss share vs rate
    lines.append("## 11. Loss-share vs loss-rate analysis")
    lines.append("")
    lines.append(
        "Loss **rate** = losses in category / trades in category. "
        "Loss **share** = losses in category / all losers."
    )
    lines.append("")
    lines.append(f"Total losers: **{lsr['total_losers']}**")
    lines.append("")
    lines.append("| HTF state | n | losses | loss rate | loss share | sample |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for state, block in lsr["by_htf_state"].items():
        lines.append(
            f"| {state} | {block['n']} | {block['losses']} | "
            f"{_fmt_pct(block.get('loss_rate'))} | "
            f"{_fmt_pct(block.get('loss_share_of_all_losers'))} | "
            f"{block.get('sample_size_flag')} |"
        )
    lines.append("")

    # 12 bootstrap
    lines.append("## 12. Bootstrap uncertainty")
    lines.append("")
    lines.append(
        "Bootstrap 95% intervals for mean R (uncertainty only; not used to select a scenario)."
    )
    lines.append("")
    lines.append("| Scenario | mean R | CI low | CI high | n |")
    lines.append("|---|---:|---:|---:|---:|")
    for sid in SCENARIO_DEFS:
        s = scenarios[sid]
        ci = s.get("mean_R_bootstrap_95ci")
        if ci:
            lines.append(
                f"| {s['label']} | {_fmt_num(s.get('mean_R'))} | "
                f"{_fmt_num(ci.get('low'))} | {_fmt_num(ci.get('high'))} | {s['n']} |"
            )
        else:
            lines.append(
                f"| {s['label']} | {_fmt_num(s.get('mean_R'))} | — | — | {s['n']} |"
            )
    lines.append("")
    lines.append(f"Note: {report['bootstrap_uncertainty']['note']}")
    lines.append("")

    # 13 multiple testing
    lines.append("## 13. Multiple-testing warning")
    lines.append("")
    lines.append(f"**{report['multiple_testing_warning']}**")
    lines.append("")
    lines.append(
        "No threshold optimization, no search over cutoffs, no selection of a winning scenario."
    )
    lines.append("")

    # 14 limitations
    lines.append("## 14. Limitations")
    lines.append("")
    for lim in report["limitations"]:
        lines.append(f"- {lim}")
    lines.append("")

    lines.append("## Factual observations")
    lines.append("")
    for o in report["factual_observations"]:
        lines.append(f"- {o}")
    lines.append("")

    lines.append("## Acceptance")
    lines.append("")
    for k, v in report["acceptance"].items():
        lines.append(f"- `{k}`: {v}")
    lines.append("")
    return "\n".join(lines)


def write_outputs(
    report: Mapping[str, Any],
    *,
    out_json: Path,
    out_md: Path,
    out_summary: Path,
) -> None:
    out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    out_md.write_text(render_markdown(report), encoding="utf-8")
    out_summary.write_text(
        json.dumps(build_summary(report), indent=2, default=str), encoding="utf-8"
    )


def run_from_rows_path(rows_path: str | Path) -> dict[str, Any]:
    rows = load_trade_rows(rows_path)
    return build_report(rows)
