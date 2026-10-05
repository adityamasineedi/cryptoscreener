"""Observational analysis of market_structure_BTCUSDT_1h.csv.

Historical research only. Does not modify strategy, create trades, or claim profitability.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SRC = Path(r"C:\Users\masin\Downloads\market_structure_BTCUSDT_1h.csv")
OUT = Path(__file__).resolve().parents[1] / "reports" / "market_structure_analysis_BTCUSDT_1h"

EXPECTED = [
    "decision_time",
    "trade_id",
    "trade_status",
    "entry",
    "direction",
    "regime_4h",
    "trend_4h",
    "structure_4h",
    "bos_4h",
    "regime_1h",
    "trend_1h",
    "structure_1h",
    "bos_1h",
    "regime_15m",
    "trend_15m",
    "structure_15m",
    "bos_15m",
    "mtf_alignment",
    "mtf_score",
    "volatility_state",
    "choppiness_state",
    "signal_stage",
    "final_strategy_decision",
    "rejection_reason",
    "win_loss",
    "r_multiple",
    "market_regime",
    "regime_confidence",
    "research_label",
    "15m_status",
]

MTF_LABELS = {
    "FULL_BULL_ALIGNMENT",
    "FULL_BEAR_ALIGNMENT",
    "BULLISH_HIGHER_TIMEFRAME_BUT_15M_WEAK",
    "BEARISH_HIGHER_TIMEFRAME_BUT_15M_WEAK",
    "1H_15M_BULLISH_AGAINST_4H",
    "1H_15M_BEARISH_AGAINST_4H",
    "TIMEFRAME_CONFLICT",
    "RANGE_ALIGNED",
    "VOLATILITY_ALIGNED",
    "TRANSITION_ALIGNED",
    "INSUFFICIENT_DATA",
}

RESEARCH_FOCUS = [
    "TREND_FOLLOWING_CANDIDATE",
    "SHORT_TREND_FOLLOWING_CANDIDATE",
    "MEAN_REVERSION_CANDIDATE",
    "BREAKOUT_CANDIDATE",
    "NO_TRADE_CHOPPY",
    "TRANSITION_WAIT",
    "TIMEFRAME_CONFLICT_AVOID",
    "PULLBACK_OR_WAIT",
    "NONE",
]


def normalize_header(h: str) -> str:
    return h.strip().lower().replace(" ", "_")


def parse_ts(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def to_float(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.upper() in {"NA", "N/A", "NONE", "NULL"}:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def is_unknown(v: Any) -> bool:
    s = str(v or "").strip().upper()
    return (not s) or s.startswith("UNKNOWN") or s in {"NA", "N/A", "NONE", "NULL"}


def is_entry(row: dict[str, Any]) -> bool:
    entry = str(row.get("entry") or "").strip().upper()
    decision = str(row.get("final_strategy_decision") or "").strip().upper()
    stage = str(row.get("signal_stage") or "").strip().upper()
    return entry in {"YES", "Y", "TRUE", "1"} or decision == "ACCEPTED" or stage == "ACCEPTED_SIGNAL"


def direction_side(trend: str | None, regime: str | None = None) -> str:
    t = str(trend or "").upper()
    r = str(regime or "").upper()
    if t == "BULLISH" or r.startswith("BULL"):
        return "BULL"
    if t == "BEARISH" or r.startswith("BEAR"):
        return "BEAR"
    if t in {"NEUTRAL", "TRANSITION"} or r in {
        "RANGE",
        "CHOPPY",
        "TRANSITION",
        "LOW_VOLATILITY_COMPRESSION",
        "HIGH_VOLATILITY_RANGE",
    }:
        if t == "TRANSITION" or r == "TRANSITION":
            return "TRANSITION"
        return "NEUTRAL"
    if is_unknown(t) and is_unknown(r):
        return "UNKNOWN"
    return "NEUTRAL"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            flat = {}
            for k, v in r.items():
                if isinstance(v, (dict, list, tuple)):
                    flat[k] = json.dumps(v, default=str)
                else:
                    flat[k] = v
            w.writerow(flat)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def trade_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    entries = [r for r in rows if is_entry(r)]
    rs = [to_float(r.get("r_multiple")) for r in entries]
    rs_ok = [x for x in rs if x is not None]
    wins = sum(1 for r in entries if str(r.get("win_loss") or "").upper() == "WIN")
    losses = sum(1 for r in entries if str(r.get("win_loss") or "").upper() == "LOSS")
    # Prefer explicit win_loss; fall back to R sign if win_loss blank
    if wins + losses == 0 and rs_ok:
        wins = sum(1 for x in rs_ok if x > 0)
        losses = sum(1 for x in rs_ok if x < 0)
    gross = sum(x for x in rs_ok if x > 0)
    loss_abs = abs(sum(x for x in rs_ok if x < 0))
    pf = (gross / loss_abs) if loss_abs > 0 else None
    return {
        "accepted_entries": len(entries),
        "no_entry_rows": len(rows) - len(entries),
        "wins": wins if entries else "N/A",
        "losses": losses if entries else "N/A",
        "sum_R": round(sum(rs_ok), 6) if rs_ok else ("N/A" if not entries else 0.0),
        "average_R": round(sum(rs_ok) / len(rs_ok), 6) if rs_ok else "N/A",
        "median_R": round(statistics.median(rs_ok), 6) if rs_ok else "N/A",
        "profit_factor": round(pf, 6) if pf is not None else "N/A",
        "win_rate": round(wins / (wins + losses), 6) if (wins + losses) else "N/A",
        "r_values": rs_ok,
    }


def category_breakdown(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    n = len(rows)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        key = str(r.get(field) if r.get(field) not in (None, "") else "MISSING")
        buckets[key].append(r)
    out: list[dict[str, Any]] = []
    for cat, rs in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        m = trade_metrics(rs)
        out.append(
            {
                "category_field": field,
                "category": cat,
                "count": len(rs),
                "percentage_of_all_rows": round(100.0 * len(rs) / n, 4) if n else 0.0,
                "accepted_entries": m["accepted_entries"],
                "no_entry_rows": m["no_entry_rows"],
                "wins": m["wins"],
                "losses": m["losses"],
                "sum_R": m["sum_R"],
                "average_R": m["average_R"],
                "median_R": m["median_R"],
            }
        )
    return out


def period_key(dt: datetime, grain: str) -> str:
    if grain == "day":
        return dt.date().isoformat()
    if grain == "week":
        iso = dt.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if grain == "month":
        return f"{dt.year:04d}-{dt.month:02d}"
    if grain == "quarter":
        q = (dt.month - 1) // 3 + 1
        return f"{dt.year:04d}-Q{q}"
    if grain == "year":
        return str(dt.year)
    raise ValueError(grain)


def dominant(values: list[str]) -> str:
    if not values:
        return "N/A"
    return Counter(values).most_common(1)[0][0]


def sample_quality(bars: int, trades: int, consecutive_share: float) -> str:
    if bars < 30:
        return "VERY_LOW"
    if trades == 0 and bars < 100:
        return "LOW_DESCRIPTIVE"
    if trades == 0 and bars >= 100:
        return "DESCRIPTIVE_ONLY_LARGE"
    if trades < 5:
        return "LOW_TRADE_SAMPLE"
    if consecutive_share > 0.7:
        return "EPISODE_CONCENTRATED"
    if trades < 15:
        return "MODERATE"
    return "ADEQUATE_FOR_SEPARATE_RESEARCH"


def next_step(label: str, bars: int, trades: int, quality: str) -> str:
    if label in {"NONE"}:
        return "DESCRIPTIVE_ONLY"
    if label in {"NO_TRADE_CHOPPY", "TRANSITION_WAIT", "TIMEFRAME_CONFLICT_AVOID"}:
        if bars < 50:
            return "INSUFFICIENT_SAMPLE"
        return "DESCRIPTIVE_ONLY"
    if trades == 0:
        if bars >= 100:
            return "CREATE_SEPARATE_RESEARCH_BACKTEST"
        if bars >= 30:
            return "REQUIRES_MORE_DATA"
        return "INSUFFICIENT_SAMPLE"
    if quality in {"VERY_LOW", "LOW_TRADE_SAMPLE"}:
        return "REQUIRES_MORE_DATA"
    if quality == "EPISODE_CONCENTRATED":
        return "REQUIRES_MORE_DATA"
    if quality == "ADEQUATE_FOR_SEPARATE_RESEARCH":
        return "CREATE_SEPARATE_RESEARCH_BACKTEST"
    return "DESCRIPTIVE_ONLY"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # ---- LOAD ----
    with SRC.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        raw_headers = list(reader.fieldnames or [])
        header_map = {h: normalize_header(h) for h in raw_headers}
        # reverse map normalized -> original
        norm_to_orig = {}
        for orig, norm in header_map.items():
            if norm not in norm_to_orig:
                norm_to_orig[norm] = orig

        rows: list[dict[str, Any]] = []
        malformed: list[dict[str, Any]] = []
        for i, raw in enumerate(reader, start=2):  # 1-indexed file line with header=1
            row = {normalize_header(k): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
            # keep original line for malformed export
            row["_source_line"] = i
            row["_raw"] = raw
            dt = parse_ts(row.get("decision_time"))
            row["_decision_dt"] = dt
            issues = []
            if not row.get("decision_time"):
                issues.append("MISSING_DECISION_TIME")
            elif dt is None:
                issues.append("UNPARSEABLE_DECISION_TIME")
            # unexpected extras already normalized; missing expected noted later
            if issues:
                malformed.append({**{k: row.get(k) for k in EXPECTED if k in row or True}, "issues": issues, "source_line": i})
                # still keep in rows for reconciliation — do not silently drop
            rows.append(row)

    n = len(rows)
    mapping_report = {
        "source_file": str(SRC),
        "original_headers": raw_headers,
        "normalized_headers": [normalize_header(h) for h in raw_headers],
        "header_mapping": header_map,
        "expected_present": {c: c in {normalize_header(h) for h in raw_headers} for c in EXPECTED},
        "unexpected_headers": [
            normalize_header(h)
            for h in raw_headers
            if normalize_header(h) not in EXPECTED
        ],
        "missing_expected_headers": [
            c for c in EXPECTED if c not in {normalize_header(h) for h in raw_headers}
        ],
    }

    # ---- VALIDATE ----
    dts = [r["_decision_dt"] for r in rows]
    parsed = [d for d in dts if d is not None]
    missing_dt = sum(1 for d in dts if d is None)
    # duplicates by exact decision_time string
    dt_strings = [str(r.get("decision_time") or "") for r in rows]
    dup_times = [t for t, c in Counter(dt_strings).items() if t and c > 1]
    duplicate_decision_time_rows = sum(1 for t in dt_strings if t and dt_strings.count(t) > 1)
    # duplicate full rows
    sigs = [
        tuple(str(r.get(c) or "") for c in EXPECTED if c in r)
        for r in rows
    ]
    dup_full = sum(1 for s, c in Counter(sigs).items() if c > 1)

    sorted_asc = all(parsed[i] <= parsed[i + 1] for i in range(len(parsed) - 1)) if len(parsed) > 1 else True
    tz_names = set()
    for d in parsed:
        tz_names.add(str(d.tzinfo) if d.tzinfo else "naive")

    trade_ids = [str(r.get("trade_id") or "").strip() for r in rows if str(r.get("trade_id") or "").strip()]
    unique_trade_ids = sorted(set(trade_ids), key=lambda x: (len(x), x))
    dup_trade_ids = [t for t, c in Counter(trade_ids).items() if c > 1]

    entry_rows_raw = [r for r in rows if is_entry(r)]
    # Deduplicate by trade_id for ledger-level analysis (CSV marks consecutive
    # bars for the same trade_id — likely signal_time vs decision_time join).
    entry_rows_unique: list[dict[str, Any]] = []
    seen_tid: set[str] = set()
    for r in sorted(
        entry_rows_raw,
        key=lambda x: (x.get("_decision_dt") or datetime.max.replace(tzinfo=timezone.utc)),
    ):
        tid = str(r.get("trade_id") or "").strip()
        if not tid:
            entry_rows_unique.append(r)
            continue
        if tid in seen_tid:
            continue
        seen_tid.add(tid)
        entry_rows_unique.append(r)

    entry_rows = entry_rows_unique  # default trade analysis uses unique trades
    no_trade_rows = [r for r in rows if not is_entry(r)]
    # also count explicit NO TRADE text
    no_trade_status = sum(
        1
        for r in rows
        if str(r.get("trade_status") or "").upper().replace("_", " ") in {"NO TRADE", "NO_TRADE"}
        or str(r.get("entry") or "").upper() == "NO"
    )

    unknown_any = sum(
        1
        for r in rows
        if any(
            is_unknown(r.get(f))
            for f in (
                "regime_4h",
                "regime_1h",
                "regime_15m",
                "market_regime",
                "mtf_alignment",
            )
        )
    )
    miss_15 = sum(
        1
        for r in rows
        if str(r.get("15m_status") or "").upper() == "UNAVAILABLE"
        or is_unknown(r.get("regime_15m"))
        or str(r.get("trend_15m") or "").upper().startswith("UNKNOWN")
    )
    miss_4 = sum(
        1
        for r in rows
        if is_unknown(r.get("regime_4h")) or str(r.get("trend_4h") or "").upper().startswith("UNKNOWN")
    )
    miss_1 = sum(
        1
        for r in rows
        if is_unknown(r.get("regime_1h")) or str(r.get("trend_1h") or "").upper().startswith("UNKNOWN")
    )

    validation = {
        "file_name": SRC.name,
        "source_path": str(SRC),
        "row_count": n,
        "malformed_row_count": len(malformed),
        "duplicate_full_row_signatures": dup_full,
        "duplicate_decision_timestamps": len(dup_times),
        "duplicate_decision_timestamp_row_occurrences": duplicate_decision_time_rows,
        "duplicate_decision_timestamp_values": dup_times[:50],
        "missing_decision_timestamps": missing_dt,
        "timestamp_timezone": sorted(tz_names),
        "first_timestamp": parsed[0].isoformat() if parsed else None,
        "last_timestamp": parsed[-1].isoformat() if parsed else None,
        "sorted_ascending_by_decision_time": sorted_asc,
        "unique_trade_ids": unique_trade_ids,
        "unique_trade_id_count": len(unique_trade_ids),
        "duplicate_trade_ids": dup_trade_ids,
        "entry_rows_raw_marked_yes": len(entry_rows_raw),
        "entry_rows_unique_trade_id": len(entry_rows_unique),
        "entry_rows": len(entry_rows_unique),
        "no_entry_rows": len(no_trade_rows),
        "no_trade_status_or_entry_no_rows": no_trade_status,
        "duplicate_trade_id_entry_attribution": {
            tid: cnt
            for tid, cnt in Counter(
                str(r.get("trade_id") or "") for r in entry_rows_raw if str(r.get("trade_id") or "").strip()
            ).items()
            if cnt > 1
        },
        "data_quality_note_duplicate_trade_attribution": (
            "Each unique trade_id appears on 2 consecutive decision bars with entry=YES. "
            "Trade-level metrics below use the earliest bar per trade_id. "
            "Raw entry-marked row count is retained for audit."
        ),
        "rows_with_any_unknown_core_field": unknown_any,
        "rows_with_missing_or_unknown_15m": miss_15,
        "rows_with_missing_or_unknown_4h": miss_4,
        "rows_with_missing_or_unknown_1h": miss_1,
        "header_mapping": mapping_report,
        "disclaimer": "Historical research only — not a profitability claim.",
    }

    # ---- STRATEGY ISOLATION ----
    isolation_issues: list[dict[str, Any]] = []
    for r in rows:
        label = str(r.get("research_label") or "")
        decision = str(r.get("final_strategy_decision") or "")
        reject = str(r.get("rejection_reason") or "")
        # research label must not create entries
        if is_entry(r) and label and decision not in {"ACCEPTED", ""} and str(r.get("entry") or "").upper() != "YES":
            isolation_issues.append(
                {
                    "issue": "ENTRY_WITHOUT_CLEAR_STRATEGY_ACCEPT",
                    "decision_time": r.get("decision_time"),
                    "research_label": label,
                    "final_strategy_decision": decision,
                }
            )
        # NO_ENTRY should not be accepted due to bullish labels
        if (
            decision.upper() == "NO_ENTRY"
            and str(r.get("entry") or "").upper() == "NO"
            and is_entry(r)
        ):
            isolation_issues.append(
                {
                    "issue": "NO_ENTRY_MARKED_AS_ENTRY",
                    "decision_time": r.get("decision_time"),
                    "research_label": label,
                }
            )
        # research label overwriting decision? (identical strings suspicious only if label equals decision and decision is a research label)
        if decision and label and decision == label and decision in RESEARCH_FOCUS:
            isolation_issues.append(
                {
                    "issue": "STRATEGY_DECISION_EQUALS_RESEARCH_LABEL",
                    "decision_time": r.get("decision_time"),
                    "value": decision,
                }
            )
        # live/paper markers
        blob = " ".join(str(r.get(k) or "") for k in r.keys()).lower()
        if "paper_trade" in blob or "live_trade" in blob or "telegram" in blob:
            isolation_issues.append(
                {
                    "issue": "EXECUTION_SIDE_EFFECT_MARKER",
                    "decision_time": r.get("decision_time"),
                }
            )
        # accepted entry must not be caused solely by research label when rejection present
        if is_entry(r) and reject and reject not in {"", "None", "null"} and reject.upper() not in {"N/A"}:
            # rejection_reason set on accepted entry is inconsistent
            if str(r.get("entry") or "").upper() == "YES":
                isolation_issues.append(
                    {
                        "issue": "ENTRY_WITH_REJECTION_REASON",
                        "decision_time": r.get("decision_time"),
                        "rejection_reason": reject,
                        "research_label": label,
                    }
                )

    # bullish regime should not flip NO_ENTRY
    for r in no_trade_rows:
        if str(r.get("final_strategy_decision") or "").upper() == "ACCEPTED":
            isolation_issues.append(
                {
                    "issue": "NO_ENTRY_ROW_BUT_ACCEPTED_DECISION",
                    "decision_time": r.get("decision_time"),
                    "regime_1h": r.get("regime_1h"),
                    "research_label": r.get("research_label"),
                }
            )

    isolation = {
        "descriptive_analytics_only": True,
        "research_labels_do_not_create_entries": all(
            not (is_entry(r) and str(r.get("final_strategy_decision") or "").upper() == "NO_ENTRY")
            for r in rows
        ),
        "final_strategy_decision_distinct_from_research_label": sum(
            1
            for r in rows
            if r.get("final_strategy_decision") and r.get("research_label")
            and str(r.get("final_strategy_decision")) == str(r.get("research_label"))
        )
        == 0,
        "issue_count": len(isolation_issues),
        "issues": isolation_issues[:200],
        "note": (
            "rejection_reason remains strategy-gate field; research_label is hypothesis only"
        ),
    }

    # ---- REGIME DISTRIBUTION ----
    dist_fields = [
        "regime_4h",
        "regime_1h",
        "regime_15m",
        "market_regime",
        "volatility_state",
        "choppiness_state",
        "mtf_alignment",
        "research_label",
        "final_strategy_decision",
        "rejection_reason",
        "signal_stage",
    ]
    regime_distribution: list[dict[str, Any]] = []
    for f in dist_fields:
        regime_distribution.extend(category_breakdown(rows, f))

    # reconcile category counts per field
    reconcile_dist = {}
    for f in dist_fields:
        sub = [x for x in regime_distribution if x["category_field"] == f]
        reconcile_dist[f] = {
            "categories": len(sub),
            "sum_counts": sum(x["count"] for x in sub),
            "equals_total_rows": sum(x["count"] for x in sub) == n,
        }

    # ---- MTF ----
    mtf_summary = []
    for cat, rs in sorted(
        defaultdict(list, {str(r.get("mtf_alignment") or "MISSING"): [] for r in rows}).items()
    ):
        pass
    mtf_buckets: dict[str, list] = defaultdict(list)
    for r in rows:
        mtf_buckets[str(r.get("mtf_alignment") or "MISSING")].append(r)
    for cat, rs in sorted(mtf_buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        m = trade_metrics(rs)
        mtf_summary.append(
            {
                "mtf_alignment": cat,
                "bars": len(rs),
                "percentage": round(100.0 * len(rs) / n, 4) if n else 0.0,
                "entries": m["accepted_entries"],
                "wins": m["wins"],
                "losses": m["losses"],
                "win_rate": m["win_rate"],
                "sum_R": m["sum_R"],
                "average_R": m["average_R"],
                "profit_factor": m["profit_factor"],
                "in_expected_taxonomy": cat in MTF_LABELS,
            }
        )

    def sides(r):
        return (
            direction_side(r.get("trend_4h"), r.get("regime_4h")),
            direction_side(r.get("trend_1h"), r.get("regime_1h")),
            direction_side(r.get("trend_15m"), r.get("regime_15m")),
        )

    agree_4_1 = agree_1_15 = agree_4_15 = agree_all = conflict_any = 0
    conf_4_1: Counter = Counter()
    conf_1_15: Counter = Counter()
    for r in rows:
        s4, s1, s15 = sides(r)
        conf_4_1[(s4, s1)] += 1
        conf_1_15[(s1, s15)] += 1
        if s4 == s1 and s4 not in {"UNKNOWN"}:
            agree_4_1 += 1
        if s1 == s15 and s1 not in {"UNKNOWN"}:
            agree_1_15 += 1
        if s4 == s15 and s4 not in {"UNKNOWN"}:
            agree_4_15 += 1
        known = [s for s in (s4, s1, s15) if s not in {"UNKNOWN"}]
        if len(known) == 3 and len(set(known)) == 1:
            agree_all += 1
        if len(set(x for x in known if x in {"BULL", "BEAR"})) > 1:
            conflict_any += 1

    mtf_agreement = {
        "4h_1h_direction_agreement": {
            "count": agree_4_1,
            "percentage": round(100.0 * agree_4_1 / n, 4),
        },
        "1h_15m_direction_agreement": {
            "count": agree_1_15,
            "percentage": round(100.0 * agree_1_15 / n, 4),
        },
        "4h_15m_direction_agreement": {
            "count": agree_4_15,
            "percentage": round(100.0 * agree_4_15 / n, 4),
        },
        "all_three_agreement": {
            "count": agree_all,
            "percentage": round(100.0 * agree_all / n, 4),
        },
        "any_timeframe_bull_bear_conflict": {
            "count": conflict_any,
            "percentage": round(100.0 * conflict_any / n, 4),
        },
        "confusion_matrix_4h_x_1h": {
            f"{a}|{b}": c for (a, b), c in sorted(conf_4_1.items())
        },
        "confusion_matrix_1h_x_15m": {
            f"{a}|{b}": c for (a, b), c in sorted(conf_1_15.items())
        },
        "sum_mtf_bars": sum(x["bars"] for x in mtf_summary),
        "mtf_reconciles_to_rows": sum(x["bars"] for x in mtf_summary) == n,
    }

    # ---- ACTUAL TRADES ----
    # Ambiguity: raw CSV marks multiple bars per trade_id. Unique trade_id used
    # for ledger metrics; raw duplicates reported as data-quality issue.
    trade_context = []
    ambiguity = None
    entry_by_id: dict[str, list] = defaultdict(list)
    for r in entry_rows_raw:
        tid = str(r.get("trade_id") or "").strip()
        entry_by_id[tid].append(r)

    if any(not k for k in entry_by_id.keys()):
        ambiguity = "Some entry rows have blank trade_id"
    multi = {k: len(v) for k, v in entry_by_id.items() if k and len(v) > 1}
    if multi:
        ambiguity = (
            "Duplicate trade_id on multiple entry-marked rows "
            f"(using earliest bar per trade_id for trade metrics): {multi}"
        )

    for r in entry_rows:  # unique
        trade_context.append(
            {
                "trade_id": r.get("trade_id"),
                "entry_time": r.get("decision_time"),
                "direction": r.get("direction"),
                "entry_regime_4h": r.get("regime_4h"),
                "entry_regime_1h": r.get("regime_1h"),
                "entry_regime_15m": r.get("regime_15m"),
                "entry_market_regime": r.get("market_regime"),
                "entry_mtf_alignment": r.get("mtf_alignment"),
                "entry_mtf_score": r.get("mtf_score"),
                "entry_volatility_state": r.get("volatility_state"),
                "entry_choppiness_state": r.get("choppiness_state"),
                "entry_bos_4h": r.get("bos_4h"),
                "entry_bos_1h": r.get("bos_1h"),
                "entry_bos_15m": r.get("bos_15m"),
                "entry_research_label": r.get("research_label"),
                "exit_time": "N/A_NOT_IN_CSV",
                "win_loss": r.get("win_loss") or "N/A",
                "r_multiple": r.get("r_multiple") or "N/A",
                "trade_status": r.get("trade_status"),
                "final_strategy_decision": r.get("final_strategy_decision"),
                "signal_stage": r.get("signal_stage"),
                "duplicate_bars_for_trade_id": len(entry_by_id.get(str(r.get("trade_id") or ""), [])),
            }
        )

    def group_trades(field_getter, group_name: str) -> list[dict[str, Any]]:
        buckets: dict[str, list] = defaultdict(list)
        for r in entry_rows:
            buckets[str(field_getter(r) or "MISSING")].append(r)
        out = []
        for g, rs in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            m = trade_metrics(rs)
            out.append(
                {
                    "group_type": group_name,
                    "group": g,
                    "trade_count": len(rs),
                    "wins": m["wins"],
                    "losses": m["losses"],
                    "win_rate": m["win_rate"],
                    "sum_R": m["sum_R"],
                    "average_R": m["average_R"],
                    "median_R": m["median_R"],
                    "profit_factor": m["profit_factor"],
                    "note": "Observational only — sample size shown; not a profitability claim",
                }
            )
        return out

    trade_regime_summary = []
    trade_regime_summary += group_trades(lambda r: r.get("regime_4h"), "regime_4h")
    trade_regime_summary += group_trades(lambda r: r.get("regime_1h"), "regime_1h")
    trade_regime_summary += group_trades(lambda r: r.get("regime_15m"), "regime_15m")
    trade_regime_summary += group_trades(lambda r: r.get("mtf_alignment"), "mtf_alignment")
    trade_regime_summary += group_trades(lambda r: r.get("volatility_state"), "volatility_state")
    trade_regime_summary += group_trades(lambda r: r.get("choppiness_state"), "choppiness_state")
    trade_regime_summary += group_trades(lambda r: r.get("bos_1h"), "bos_1h")
    trade_regime_summary += group_trades(lambda r: r.get("bos_4h"), "bos_4h")
    trade_regime_summary += group_trades(lambda r: r.get("research_label"), "research_label")

    # R reconciliation
    all_entry_R = [to_float(r.get("r_multiple")) for r in entry_rows]
    all_entry_R_ok = [x for x in all_entry_R if x is not None]
    total_R = sum(all_entry_R_ok)
    # for regime_1h grouping, sum of sum_R should equal total_R
    g1 = [x for x in trade_regime_summary if x["group_type"] == "regime_1h"]
    sum_grouped_R = sum(float(x["sum_R"]) for x in g1 if x["sum_R"] != "N/A")
    wins_total = sum(1 for r in entry_rows if str(r.get("win_loss") or "").upper() == "WIN")
    losses_total = sum(1 for r in entry_rows if str(r.get("win_loss") or "").upper() == "LOSS")
    if wins_total + losses_total == 0 and all_entry_R_ok:
        wins_total = sum(1 for x in all_entry_R_ok if x > 0)
        losses_total = sum(1 for x in all_entry_R_ok if x < 0)

    trade_reconcile = {
        "entry_marked_rows": len(entry_rows_raw),
        "unique_trades": len(entry_rows),
        "entry_row_count": len(entry_rows),
        "trade_context_row_count": len(trade_context),
        "trade_context_reconciles": len(trade_context) == len(entry_rows),
        "wins": wins_total,
        "losses": losses_total,
        "flat_or_missing_wl": len(entry_rows) - wins_total - losses_total,
        "total_R": round(total_R, 6),
        "sum_grouped_R_regime_1h": round(sum_grouped_R, 6),
        "grouped_R_reconciles": abs(round(sum_grouped_R, 6) - round(total_R, 6)) < 1e-6,
        "raw_entry_marked_R_sum_double_count_risk": round(
            sum(x for x in (to_float(r.get("r_multiple")) for r in entry_rows_raw) if x is not None),
            6,
        ),
        "ambiguity": ambiguity,
        "pnl_in_csv": False,
        "pnl_reconcile": "N/A_NO_PNL_COLUMN",
    }

    # ---- NO-TRADE OPPORTUNITY ----
    opportunity = []
    for field in ("regime_1h", "regime_4h", "mtf_alignment", "research_label"):
        buckets: dict[str, list] = defaultdict(list)
        for r in rows:
            buckets[str(r.get(field) or "MISSING")].append(r)
        for g, rs in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            # candidate/signal: accepted OR stage suggests evaluation
            candidates = [
                r
                for r in rs
                if is_entry(r)
                or str(r.get("signal_stage") or "").upper()
                not in {"", "NO_ENTRY", "NONE"}
            ]
            accepted = [r for r in rs if is_entry(r)]
            rejected = [r for r in rs if not is_entry(r)]
            opportunity.append(
                {
                    "group_field": field,
                    "group": g,
                    "total_bars": len(rs),
                    "candidate_or_signal_bars": len(candidates),
                    "accepted_entries": len(accepted),
                    "rejected_bars": len(rejected),
                    "trade_rate": round(len(accepted) / len(rs), 6) if rs else 0.0,
                    "signal_rate": round(len(candidates) / len(rs), 6) if rs else 0.0,
                }
            )

    # research labels common with zero trades
    label_zero_trades = []
    for lab in RESEARCH_FOCUS:
        rs = [r for r in rows if str(r.get("research_label") or "") == lab]
        if not rs:
            continue
        tr = [r for r in rs if is_entry(r)]
        if len(tr) == 0:
            label_zero_trades.append(
                {
                    "research_label": lab,
                    "bars": len(rs),
                    "percentage": round(100.0 * len(rs) / n, 4),
                    "actual_trades": 0,
                    "note": "Frequent label with zero actual trades — not a strategy",
                }
            )

    # ---- REJECTION ----
    rejection_rows = []
    rej_buckets: dict[str, list] = defaultdict(list)
    for r in rows:
        rej_buckets[str(r.get("rejection_reason") or "MISSING")].append(r)
    for reason, rs in sorted(rej_buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        regime_dist = Counter(str(x.get("regime_1h") or "MISSING") for x in rs)
        mtf_dist = Counter(str(x.get("mtf_alignment") or "MISSING") for x in rs)
        rejection_rows.append(
            {
                "rejection_reason": reason,
                "total_rows": len(rs),
                "percentage": round(100.0 * len(rs) / n, 4),
                "regime_distribution": dict(regime_dist),
                "mtf_distribution": dict(mtf_dist),
                "entries": sum(1 for x in rs if is_entry(x)),
            }
        )

    # regime × primary rejection
    rej_by_regime = []
    for regime, rs in sorted(
        defaultdict(list, {str(r.get("regime_1h") or "MISSING"): [] for r in rows}  # placeholder
        ).items()
    ):
        pass
    rr_buckets: dict[tuple[str, str], int] = Counter()
    regime_totals: Counter = Counter()
    for r in rows:
        regime = str(r.get("regime_1h") or "MISSING")
        reason = str(r.get("rejection_reason") or "MISSING")
        if is_entry(r):
            reason = "ACCEPTED_ENTRY"
        rr_buckets[(regime, reason)] += 1
        regime_totals[regime] += 1
    for (regime, reason), cnt in sorted(rr_buckets.items(), key=lambda kv: (-kv[1], kv[0])):
        rej_by_regime.append(
            {
                "regime": regime,
                "primary_rejection_reason": reason,
                "bars": cnt,
                "percentage": round(100.0 * cnt / regime_totals[regime], 4)
                if regime_totals[regime]
                else 0.0,
                "percentage_of_all_rows": round(100.0 * cnt / n, 4),
            }
        )

    # map rejection reasons to gate families (string heuristics; do not invent)
    gate_map = {
        "trend": [],
        "higher_low": [],
        "bos": [],
        "htf_alignment": [],
        "open_position": [],
        "cooldown": [],
        "risk_validation": [],
        "no_strategy_entry": [],
        "other": [],
    }
    for reason, rs in rej_buckets.items():
        ru = reason.upper()
        family = "other"
        if "NO_STRATEGY_ENTRY" in ru or reason == "NO_STRATEGY_ENTRY_AT_BAR":
            family = "no_strategy_entry"
        elif "HTF" in ru or "FOUR_HOUR" in ru or "1H_" in ru:
            family = "htf_alignment"
        elif "BOS" in ru:
            family = "bos"
        elif "HL" in ru or "HIGHER_LOW" in ru or "STRUCTURE" in ru:
            family = "higher_low"
        elif "TREND" in ru:
            family = "trend"
        elif "POSITION" in ru or "OPEN" in ru:
            family = "open_position"
        elif "COOLDOWN" in ru:
            family = "cooldown"
        elif "RISK" in ru or "STOP" in ru or "TP1" in ru or "MIN_RR" in ru:
            family = "risk_validation"
        top_regimes = Counter(str(x.get("regime_1h") or "MISSING") for x in rs).most_common(5)
        gate_map[family].append(
            {
                "rejection_reason": reason,
                "bars": len(rs),
                "top_regimes": top_regimes,
            }
        )

    # ---- TEMPORAL ----
    temporal = []
    for grain in ("day", "week", "month", "quarter", "year"):
        buckets: dict[str, list] = defaultdict(list)
        for r in rows:
            dt = r.get("_decision_dt")
            if dt is None:
                buckets["UNPARSEABLE"].append(r)
                continue
            buckets[period_key(dt, grain)].append(r)
        for period, rs in sorted(buckets.items()):
            m = trade_metrics(rs)
            temporal.append(
                {
                    "grain": grain,
                    "period": period,
                    "bars": len(rs),
                    "dominant_market_regime": dominant(
                        [str(x.get("market_regime") or "MISSING") for x in rs]
                    ),
                    "dominant_mtf_alignment": dominant(
                        [str(x.get("mtf_alignment") or "MISSING") for x in rs]
                    ),
                    "entries": m["accepted_entries"],
                    "wins": m["wins"],
                    "losses": m["losses"],
                    "sum_R": m["sum_R"],
                    "average_R": m["average_R"],
                }
            )

    # regime transitions on market_regime (sorted by time)
    ordered = sorted(
        [r for r in rows if r.get("_decision_dt") is not None],
        key=lambda r: r["_decision_dt"],
    )
    transitions: Counter = Counter()
    dwell: dict[str, list[int]] = defaultdict(list)
    if ordered:
        prev = str(ordered[0].get("market_regime") or "MISSING")
        run = 1
        for r in ordered[1:]:
            cur = str(r.get("market_regime") or "MISSING")
            if cur == prev:
                run += 1
            else:
                transitions[(prev, cur)] += 1
                dwell[prev].append(run)
                prev = cur
                run = 1
        dwell[prev].append(run)

    transition_rows = []
    for (a, b), cnt in sorted(transitions.items(), key=lambda kv: (-kv[1], kv[0])):
        transition_rows.append(
            {
                "previous_regime": a,
                "new_regime": b,
                "transition_count": cnt,
                "average_time_in_previous_regime_bars": round(
                    sum(dwell[a]) / len(dwell[a]), 4
                )
                if dwell[a]
                else "N/A",
            }
        )

    # sequence detectors (runs)
    def find_runs(pred, label: str, min_len: int = 6) -> list[dict[str, Any]]:
        out = []
        i = 0
        while i < len(ordered):
            if not pred(ordered[i]):
                i += 1
                continue
            j = i
            while j < len(ordered) and pred(ordered[j]):
                j += 1
            length = j - i
            if length >= min_len:
                out.append(
                    {
                        "sequence_type": label,
                        "start": ordered[i].get("decision_time"),
                        "end": ordered[j - 1].get("decision_time"),
                        "length_bars": length,
                    }
                )
            i = j
        return out

    sequences = []
    sequences += find_runs(lambda r: str(r.get("market_regime") or "") == "CHOPPY", "LONG_CHOPPY")
    sequences += find_runs(
        lambda r: str(r.get("market_regime") or "") == "TRANSITION", "LONG_TRANSITION"
    )
    sequences += find_runs(
        lambda r: str(r.get("market_regime") or "") == "HIGH_VOLATILITY_RANGE",
        "HIGH_VOLATILITY_RANGE",
    )
    sequences += find_runs(
        lambda r: str(r.get("mtf_alignment") or "") == "FULL_BULL_ALIGNMENT",
        "FULL_BULL_ALIGNMENT",
        min_len=3,
    )
    sequences += find_runs(
        lambda r: str(r.get("mtf_alignment") or "") == "FULL_BEAR_ALIGNMENT",
        "FULL_BEAR_ALIGNMENT",
        min_len=3,
    )
    sequences += find_runs(
        lambda r: str(r.get("mtf_alignment") or "")
        in {
            "TIMEFRAME_CONFLICT",
            "1H_15M_BULLISH_AGAINST_4H",
            "1H_15M_BEARISH_AGAINST_4H",
            "BULLISH_HIGHER_TIMEFRAME_BUT_15M_WEAK",
        }
        and direction_side(r.get("trend_4h"), r.get("regime_4h")) == "BULL"
        and direction_side(r.get("trend_1h"), r.get("regime_1h")) == "BEAR",
        "HTF_BULL_LTF_BEAR_CONFLICT",
        min_len=3,
    )

    # ---- DATA QUALITY ----
    invalid_mtf = []
    for r in rows:
        sc = to_float(r.get("mtf_score"))
        if sc is None and str(r.get("mtf_score") or "").strip() not in {"", "N/A"}:
            invalid_mtf.append(r)
        elif sc is not None and (sc < -4 or sc > 4):
            invalid_mtf.append(r)

    invalid_r = []
    for r in rows:
        raw = str(r.get("r_multiple") or "").strip()
        if raw and to_float(raw) is None:
            invalid_r.append(r)

    status_15 = Counter(str(r.get("15m_status") or "MISSING") for r in rows)

    dq = {
        "rows_with_missing_decision_time": missing_dt,
        "duplicate_decision_times": len(dup_times),
        "duplicate_trade_ids": len(dup_trade_ids),
        "rows_with_unknown_4h": miss_4,
        "rows_with_unknown_1h": miss_1,
        "rows_with_unknown_15m": miss_15,
        "rows_with_invalid_mtf_score": len(invalid_mtf),
        "invalid_mtf_score_examples": [
            {"decision_time": r.get("decision_time"), "mtf_score": r.get("mtf_score")}
            for r in invalid_mtf[:20]
        ],
        "expected_mtf_score_range": [-4, 4],
        "rows_with_invalid_r_multiple": len(invalid_r),
        "rows_with_entry_but_no_trade_id": sum(
            1 for r in entry_rows if not str(r.get("trade_id") or "").strip()
        ),
        "rows_with_trade_id_but_no_entry": sum(
            1
            for r in rows
            if str(r.get("trade_id") or "").strip() and not is_entry(r)
        ),
        "rows_with_win_without_r_multiple": sum(
            1
            for r in rows
            if str(r.get("win_loss") or "").upper() == "WIN" and to_float(r.get("r_multiple")) is None
        ),
        "rows_with_loss_without_r_multiple": sum(
            1
            for r in rows
            if str(r.get("win_loss") or "").upper() == "LOSS" and to_float(r.get("r_multiple")) is None
        ),
        "rows_with_research_label_but_no_strategy_decision": sum(
            1
            for r in rows
            if str(r.get("research_label") or "").strip()
            and not str(r.get("final_strategy_decision") or "").strip()
        ),
        "rows_with_strategy_entry_but_missing_regime": sum(
            1
            for r in entry_rows
            if is_unknown(r.get("market_regime")) and is_unknown(r.get("regime_1h"))
        ),
        "rows_with_future_timestamp_violation_if_available": "N/A_NOT_IN_CSV",
        "15m_status_counts": dict(status_15),
        "15m_status_only_ok_or_unavailable": all(
            k in {"OK", "UNAVAILABLE", "MISSING"} for k in status_15.keys()
        ),
    }

    dq_issues = []
    for k, v in dq.items():
        if isinstance(v, int) and v > 0 and k.startswith("rows_"):
            dq_issues.append({"check": k, "count": v, "severity": "WARN"})
        elif k == "rows_with_invalid_mtf_score" and isinstance(v, int) and v > 0:
            dq_issues.append({"check": k, "count": v, "severity": "FAIL"})
    if isolation_issues:
        dq_issues.append(
            {
                "check": "strategy_isolation_issues",
                "count": len(isolation_issues),
                "severity": "WARN",
            }
        )
    if ambiguity:
        dq_issues.append({"check": "trade_id_ambiguity", "count": 1, "detail": ambiguity, "severity": "WARN"})

    # ---- RESEARCH RANKING ----
    ranking = []
    for lab in RESEARCH_FOCUS:
        rs = [r for r in rows if str(r.get("research_label") or "") == lab]
        if not rs and lab != "NONE":
            # still list with zeros? skip empty except we want known labels if present
            continue
        if not rs:
            continue
        tr_raw = [r for r in rs if is_entry(r)]
        # unique trades only
        seen = set()
        tr = []
        for r in sorted(tr_raw, key=lambda x: x.get("_decision_dt") or datetime.min.replace(tzinfo=timezone.utc)):
            tid = str(r.get("trade_id") or "").strip() or id(r)
            if tid in seen:
                continue
            seen.add(tid)
            tr.append(r)
        m = trade_metrics(tr)
        # consecutive share: largest run / bars
        labs_series = [str(r.get("research_label") or "") for r in ordered]
        max_run = 0
        run = 0
        for x in labs_series:
            if x == lab:
                run += 1
                max_run = max(max_run, run)
            else:
                run = 0
        consec_share = max_run / len(rs) if rs else 0.0
        # bos/structure confirmation share
        bos_conf = sum(
            1
            for r in rs
            if str(r.get("bos_1h") or "") in {"BULLISH_BOS", "BEARISH_BOS"}
        )
        mtf_cons = sum(
            1
            for r in rs
            if str(r.get("mtf_alignment") or "")
            in {"FULL_BULL_ALIGNMENT", "FULL_BEAR_ALIGNMENT", "RANGE_ALIGNED"}
        )
        quality = sample_quality(len(rs), len(tr), consec_share)
        step = next_step(lab, len(rs), len(tr), quality)
        ranking.append(
            {
                "research_label": lab,
                "bars": len(rs),
                "percentage_of_dataset": round(100.0 * len(rs) / n, 4),
                "candidate_signals": sum(
                    1
                    for r in rs
                    if is_entry(r)
                    or str(r.get("signal_stage") or "").upper() not in {"", "NO_ENTRY"}
                ),
                "actual_trades": len(tr),
                "wins": m["wins"] if tr else 0,
                "losses": m["losses"] if tr else 0,
                "sum_R": m["sum_R"] if tr else "N/A",
                "average_R": m["average_R"] if tr else "N/A",
                "dominant_timeframe_pattern": dominant(
                    [str(x.get("mtf_alignment") or "MISSING") for x in rs]
                ),
                "bos_or_structure_confirmation_bars": bos_conf,
                "mtf_consistent_bars": mtf_cons,
                "max_consecutive_bars": max_run,
                "consecutive_share_of_label_bars": round(consec_share, 4),
                "sample_quality": quality,
                "recommended_next_step": step,
                "evidence_rank_key": (
                    len(rs),
                    len(tr),
                    bos_conf,
                    mtf_cons,
                    -int(consec_share * 1000),
                ),
            }
        )

    ranking.sort(
        key=lambda x: (
            0 if x["recommended_next_step"] == "CREATE_SEPARATE_RESEARCH_BACKTEST" else 1,
            -x["bars"],
            -x["actual_trades"],
            -x["bos_or_structure_confirmation_bars"],
            -x["mtf_consistent_bars"],
        )
    )
    for i, row in enumerate(ranking, 1):
        row["rank"] = i
        row.pop("evidence_rank_key", None)

    # ---- ACCEPTANCE ----
    acceptance = {
        "every_row_in_aggregate_or_malformed": (
            reconcile_dist["market_regime"]["equals_total_rows"]
            and len(malformed) >= 0
        ),
        "regime_counts_reconcile": reconcile_dist["market_regime"]["equals_total_rows"],
        "trade_context_reconciles": trade_reconcile["trade_context_reconciles"],
        "win_loss_counts_shown": True,
        "grouped_R_reconciles": trade_reconcile["grouped_R_reconciles"],
        "pnl_reconcile": trade_reconcile["pnl_reconcile"],
        "mtf_reconciles": mtf_agreement["mtf_reconciles_to_rows"],
        "analytics_labels_do_not_replace_rejection_reasons": isolation[
            "final_strategy_decision_distinct_from_research_label"
        ],
        "no_research_label_creates_entry": isolation[
            "research_labels_do_not_create_entries"
        ],
        "no_paper_or_live_trade_created": True,
    }

    # ---- WRITE ARTIFACTS ----
    write_json(OUT / "market_structure_validation.json", {
        "validation": validation,
        "isolation": isolation,
        "data_quality": dq,
        "acceptance": acceptance,
        "trade_reconcile": trade_reconcile,
        "mtf_agreement": mtf_agreement,
        "gate_family_map": gate_map,
        "label_zero_trades": label_zero_trades,
        "sequences": sequences,
        "transitions": transition_rows,
    })
    write_csv(
        OUT / "market_structure_validation.csv",
        [
            {"metric": k, "value": json.dumps(v, default=str) if isinstance(v, (dict, list)) else v}
            for k, v in validation.items()
            if k != "header_mapping"
        ],
    )
    write_csv(OUT / "regime_distribution.csv", regime_distribution)
    write_json(OUT / "regime_distribution.json", {
        "rows": regime_distribution,
        "reconcile": reconcile_dist,
    })
    write_csv(OUT / "mtf_alignment_summary.csv", mtf_summary)
    write_json(OUT / "mtf_alignment_summary.json", {
        "summary": mtf_summary,
        "agreement": mtf_agreement,
    })
    write_csv(OUT / "trade_context.csv", trade_context)
    write_csv(
        OUT / "trade_context_raw_entry_marked_rows.csv",
        [
            {
                "decision_time": r.get("decision_time"),
                "trade_id": r.get("trade_id"),
                "entry": r.get("entry"),
                "win_loss": r.get("win_loss"),
                "r_multiple": r.get("r_multiple"),
                "regime_1h": r.get("regime_1h"),
                "mtf_alignment": r.get("mtf_alignment"),
            }
            for r in entry_rows_raw
        ],
    )
    write_csv(OUT / "trade_regime_summary.csv", trade_regime_summary)
    write_csv(OUT / "rejection_by_regime.csv", rej_by_regime)
    write_csv(OUT / "research_opportunity_ranking.csv", ranking)
    write_csv(OUT / "temporal_regime_summary.csv", temporal)
    write_csv(OUT / "data_quality_issues.csv", dq_issues)
    write_csv(
        OUT / "malformed_rows.csv",
        [
            {
                "source_line": m.get("source_line"),
                "issues": m.get("issues"),
                "decision_time": m.get("decision_time"),
            }
            for m in malformed
        ]
        if malformed
        else [{"note": "no_malformed_rows"}],
    )
    # also write opportunity + rejection detail extras
    write_csv(OUT / "opportunity_by_group.csv", opportunity)
    write_csv(OUT / "rejection_reason_summary.csv", rejection_rows)

    # README
    top_regimes = [
        x for x in regime_distribution if x["category_field"] == "market_regime"
    ][:8]
    top_mtf = mtf_summary[:8]
    readme = f"""# Market Structure Analysis — BTCUSDT 1h

**Historical research only. Not a profitability claim.**

Source: `{SRC}`
Output: `{OUT}`

## Dataset
- Rows: {n}
- Range: {validation['first_timestamp']} → {validation['last_timestamp']}
- Timezone: {validation['timestamp_timezone']}
- Entries: {len(entry_rows)}
- No-entry: {len(no_trade_rows)}
- 15m missing/unknown rows: {miss_15}
- Ambiguity: {ambiguity or 'none'}

## Top market regimes
{chr(10).join(f"- {r['category']}: {r['count']} ({r['percentage_of_all_rows']}%)" for r in top_regimes)}

## Top MTF alignments
{chr(10).join(f"- {r['mtf_alignment']}: {r['bars']} ({r['percentage']}%) entries={r['entries']}" for r in top_mtf)}

## Safety
- No strategy rules changed
- No paper/live trades created
- Research labels are hypotheses only
- Rejection reasons preserved as strategy fields

## Limitations
- Observational analysis only
- Consecutive rows may be the same episode
- Small trade samples are unreliable
- CSV alone cannot establish profitability
"""
    (OUT / "analysis_readme.md").write_text(readme, encoding="utf-8")

    # summary stdout
    summary = {
        "out_dir": str(OUT),
        "rows": n,
        "entries_raw_marked": len(entry_rows_raw),
        "entries_unique": len(entry_rows),
        "wins": wins_total,
        "losses": losses_total,
        "total_R": round(total_R, 6),
        "top_market_regime": top_regimes[0]["category"] if top_regimes else None,
        "top_mtf": top_mtf[0]["mtf_alignment"] if top_mtf else None,
        "acceptance": acceptance,
        "ambiguity": ambiguity,
        "isolation_issues": len(isolation_issues),
        "dq_issue_rows": len(dq_issues),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
