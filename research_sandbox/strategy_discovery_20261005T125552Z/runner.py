#!/usr/bin/env python3
"""S3-A3 direction split — isolated sandbox follow-up.

Reuses the prior sandbox runner implementation via importlib (sibling sandbox only).
Does not import production app code. Writes only inside this directory.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SANDBOX = Path(__file__).resolve().parent
REPO = SANDBOX.parents[1]
PRIOR = REPO / "research_sandbox" / "strategy_discovery_20261005T124726Z"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sample_label(n: int) -> str:
    if n < 20:
        return "INSUFFICIENT_SAMPLE"
    if n < 50:
        return "EXPLORATORY_ONLY"
    if n < 100:
        return "PRELIMINARY"
    return "STRONGER_RESEARCH_EVIDENCE"


def load_prior_engine():
    path = PRIOR / "runner.py"
    if not path.exists():
        raise RuntimeError("A3 REPRODUCTION UNAVAILABLE WITHOUT DEFINITION — prior runner missing")
    spec = importlib.util.spec_from_file_location("prior_s3_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("A3 REPRODUCTION UNAVAILABLE WITHOUT DEFINITION")
    mod = importlib.util.module_from_spec(spec)
    # Required so @dataclass on Trade resolves sys.modules[cls.__module__]
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fieldnames})


def metrics(trades: list[Any]) -> dict[str, Any]:
    closed = [t for t in trades if t.net_R is not None]
    n = len(closed)
    if n == 0:
        return {
            "trade_count": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "gross_R": 0.0,
            "fees_R": 0.0,
            "net_R": 0.0,
            "average_R": None,
            "median_R": None,
            "profit_factor": None,
            "maximum_drawdown_R": 0.0,
            "longest_losing_streak": 0,
            "average_hold_bars": None,
            "average_hold_hours": None,
            "sample_size_label": sample_label(0),
        }
    nets = [float(t.net_R) for t in closed]
    gross = [float(t.gross_R) for t in closed]
    fees = [float(t.fees_R) for t in closed]
    wins = sum(1 for x in nets if x > 0)
    losses = sum(1 for x in nets if x <= 0)
    gp = sum(x for x in nets if x > 0)
    gl = -sum(x for x in nets if x < 0)
    pf = (gp / gl) if gl > 0 else (None if gp == 0 else float("inf"))
    eq = peak = max_dd = 0.0
    streak = max_streak = 0
    for x in nets:
        eq += x
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
        if x <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    holds = [t.hold_bars for t in closed if t.hold_bars is not None]
    avg_hold = float(sum(holds) / len(holds)) if holds else None
    return {
        "trade_count": n,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / n,
        "gross_R": float(sum(gross)),
        "fees_R": float(sum(fees)),
        "net_R": float(sum(nets)),
        "average_R": float(sum(nets) / n),
        "median_R": float(pd.Series(nets).median()),
        "profit_factor": None if pf is None or math.isinf(pf) else float(pf),
        "maximum_drawdown_R": float(max_dd),
        "longest_losing_streak": int(max_streak),
        "average_hold_bars": avg_hold,
        "average_hold_hours": avg_hold,  # 1h bars
        "sample_size_label": sample_label(n),
    }


def trade_to_row(t: Any, variant: str) -> dict[str, Any]:
    return {
        "variant": variant,
        "partition": t.partition,
        "direction": t.direction,
        "trade_id": t.trade_id,
        "signal_time": t.decision_time,
        "entry_time": t.entry_time,
        "exit_time": t.exit_time,
        "entry_price": t.entry_price,
        "stop_price": t.stop_price,
        "target_price": t.target_price,
        "exit_reason": t.exit_reason,
        "bars_held": t.hold_bars,
        "gross_R": t.gross_R,
        "fees_R": t.fees_R,
        "net_R": t.net_R,
        "lookahead_status": t.lookahead_status,
        "selected_4h_open_time": t.selected_4h_open_time,
        "selected_4h_close_time": t.selected_4h_close_time,
        "selected_4h_is_closed": t.selected_4h_is_closed,
        "decision_time": t.decision_time,
        "ambiguity": t.ambiguity,
    }


def classify_direction(
    m_all: dict[str, Any],
    m_long: dict[str, Any],
    m_short: dict[str, Any],
    m_oos: dict[str, Any],
    m_oos_l: dict[str, Any],
    m_oos_s: dict[str, Any],
    part_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Direction attribution without using OOS to choose a filter (report-only)."""
    labels: list[str] = []

    # Partition sensitivity of combined A3 avg_R sign
    part_avgs = {}
    for name in ("TRAIN", "VALIDATION", "OOS"):
        rows = [r for r in part_rows if r["partition"] == name and r["direction"] == "COMBINED"]
        if rows and rows[0]["average_R"] is not None:
            part_avgs[name] = float(rows[0]["average_R"])
    signs = {k: (1 if v > 0 else (-1 if v < 0 else 0)) for k, v in part_avgs.items()}
    if len(set(signs.values())) > 1:
        labels.append("PARTITION-SENSITIVE")

    # OOS direction samples
    oos_l_n = m_oos_l["trade_count"]
    oos_s_n = m_oos_s["trade_count"]
    oos_l_avg = m_oos_l["average_R"]
    oos_s_avg = m_oos_s["average_R"]

    if oos_l_n < 20 and oos_s_n < 20:
        primary = "INSUFFICIENT_SAMPLE"
    else:
        l_pos = oos_l_avg is not None and oos_l_avg > 0 and oos_l_n >= 10
        s_pos = oos_s_avg is not None and oos_s_avg > 0 and oos_s_n >= 10
        l_neg = oos_l_avg is not None and oos_l_avg <= 0
        s_neg = oos_s_avg is not None and oos_s_avg <= 0

        if oos_l_n >= 20 and oos_s_n >= 20 and l_pos and s_pos:
            primary = "BOTH_DIRECTIONS_SUPPORTIVE"
        elif oos_l_n >= 20 and l_pos and (oos_s_n < 20 or s_neg):
            primary = "LONG_ONLY_EXPLORATORY"
        elif oos_s_n >= 20 and s_pos and (oos_l_n < 20 or l_neg):
            primary = "SHORT_ONLY_EXPLORATORY"
        elif (l_pos and s_neg) or (s_pos and l_neg):
            primary = "MIXED_DIRECTION_ARTIFACT"
        elif (oos_l_avg is not None and oos_l_avg <= 0) and (oos_s_avg is not None and oos_s_avg <= 0):
            primary = "NO_DIRECTIONAL_EDGE"
        else:
            # one side sparse / near-zero
            if oos_l_n < 20 or oos_s_n < 20:
                primary = "INSUFFICIENT_SAMPLE"
            else:
                primary = "NO_DIRECTIONAL_EDGE"

    # Time-period-specific: one direction positive only in one partition
    for direction in ("LONG", "SHORT"):
        avgs = []
        for name in ("TRAIN", "VALIDATION", "OOS"):
            rows = [r for r in part_rows if r["partition"] == name and r["direction"] == direction]
            if rows and rows[0]["average_R"] is not None and int(rows[0]["trades"]) >= 5:
                avgs.append((name, float(rows[0]["average_R"])))
        pos = [n for n, a in avgs if a > 0]
        if len(pos) == 1 and len(avgs) >= 2:
            labels.append("TIME-PERIOD-SPECIFIC")
            break

    return {
        "primary_verdict": primary,
        "secondary_labels": sorted(set(labels)),
        "partition_average_R": part_avgs,
    }


def main() -> None:
    eng = load_prior_engine()
    prior_cfg = json.loads((PRIOR / "config.json").read_text(encoding="utf-8"))
    prior_inv = json.loads((PRIOR / "data_inventory.json").read_text(encoding="utf-8"))
    prior_a3 = json.loads((PRIOR / "run_summary.json").read_text(encoding="utf-8"))
    # recover prior A3 metrics from ablation file
    prior_abl = list(csv.DictReader((PRIOR / "ablation_summary.csv").open(encoding="utf-8")))
    prior_a3_row = next(r for r in prior_abl if r["ablation"] == "A3")

    cfg = json.loads(json.dumps(prior_cfg))  # deep copy frozen
    cfg["experiment_id"] = "S3_A3_DIRECTION_SPLIT"
    cfg["parent_sandbox"] = str(PRIOR.name)
    cfg["sandbox_dir"] = str(SANDBOX.relative_to(REPO)).replace("\\", "/")
    (SANDBOX / "configuration_reproduction.json").write_text(
        json.dumps(
            {
                "parent_sandbox": PRIOR.name,
                "frozen_from": "research_sandbox/strategy_discovery_20261005T124726Z/config.json",
                "a3_definition": cfg["ablations"]["A3"],
                "retest": cfg["retest"],
                "predeclared_features": cfg["predeclared_features"],
                "entry_mode": "E1",
                "stop": "S1",
                "target_R": 2.0,
                "fees": cfg["fees"],
                "intrabar_ambiguity_rule": cfg["intrabar_ambiguity_rule"],
                "partitions": cfg["partitions"],
                "cooldown_bars_after_exit": cfg["cooldown_bars_after_exit"],
                "max_hold_bars": cfg["max_hold_bars"],
                "data": cfg["data"],
                "prior_a3_reference": {
                    "trade_count": prior_a3_row.get("trade_count"),
                    "average_R": prior_a3_row.get("average_R"),
                    "OOS_trade_count": prior_a3_row.get("OOS_trade_count"),
                    "OOS_average_R": prior_a3_row.get("OOS_average_R"),
                    "OOS_net_R": prior_a3_row.get("OOS_net_R"),
                },
                "direction_filter_mode": "analysis_only_post_hoc_split_of_combined_A3_ledger",
                "no_parameter_changes": True,
                "oos_not_used_to_select_direction": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    (SANDBOX / "repository_safety_report.md").write_text(
        "\n".join(
            [
                "# Repository safety report",
                "",
                f"- generated_at: {utc_now()}",
                f"- branch: main",
                f"- sandbox: {SANDBOX}",
                f"- parent_sandbox: {PRIOR}",
                "- Existing code modified: NO",
                "- Existing configs modified: NO",
                "- Existing DB data modified: NO",
                "- Existing DB data changed: NO",
                "- Existing reports overwritten: NO",
                "- Existing strategies modified: NO",
                "- Live trading affected: NO",
                "- Paper trading affected: NO",
                "- Dependencies changed: NO",
                "- Migrations run: NO",
                "- note: Sibling-sandbox engine reuse only; no production imports.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    # Load + validate data (same paths)
    df1h = eng.load_ohlcv(cfg["data"]["parquet_1h"])
    df4h = eng.load_ohlcv(cfg["data"]["parquet_4h"])
    q1 = eng.validate_ohlcv(df1h, 3600, "BTCUSDT_1h")
    q4 = eng.validate_ohlcv(df4h, 14400, "BTCUSDT_4h")
    man1 = json.loads((REPO / cfg["data"]["manifest_1h"]).read_text(encoding="utf-8"))
    man4 = json.loads((REPO / cfg["data"]["manifest_4h"]).read_text(encoding="utf-8"))

    fp1 = man1.get("sha256")
    fp4 = man4.get("sha256")
    prior_fp1 = prior_inv["datasets"][0]["dataset_fingerprint"]
    prior_fp4 = prior_inv["datasets"][1]["dataset_fingerprint"]
    if fp1 != prior_fp1 or fp4 != prior_fp4:
        raise RuntimeError(
            f"Fingerprint mismatch vs prior S3 run: 1h {fp1} vs {prior_fp1}; 4h {fp4} vs {prior_fp4}"
        )

    overall = "DATA_OK"
    if q1["status"] != "DATA_OK" or q4["status"] != "DATA_OK":
        overall = "DATA_CONTAMINATED" if "CONTAMINATED" in (q1["status"], q4["status"]) else "DATA_WARN"

    inventory = {
        "symbol": "BTCUSDT",
        "timezone": "UTC",
        "same_as_prior_sandbox": PRIOR.name,
        "fingerprint_assert_identical": True,
        "prior_fingerprints": {"1h": prior_fp1, "4h": prior_fp4},
        "current_fingerprints": {"1h": fp1, "4h": fp4},
        "datasets": [
            {
                "timeframe": "1h",
                "source_file": cfg["data"]["parquet_1h"],
                "manifest": cfg["data"]["manifest_1h"],
                "first_timestamp": q1["first_timestamp"],
                "last_timestamp": q1["last_timestamp"],
                "row_count": q1["row_count"],
                "missing_bars": q1["gap_count"],
                "duplicate_bars": q1["duplicate_bars"],
                "dataset_fingerprint": fp1,
                "quality": q1,
            },
            {
                "timeframe": "4h",
                "source_file": cfg["data"]["parquet_4h"],
                "manifest": cfg["data"]["manifest_4h"],
                "first_timestamp": q4["first_timestamp"],
                "last_timestamp": q4["last_timestamp"],
                "row_count": q4["row_count"],
                "missing_bars": q4["gap_count"],
                "duplicate_bars": q4["duplicate_bars"],
                "dataset_fingerprint": fp4,
                "quality": q4,
            },
        ],
        "overall_quality": overall,
    }
    (SANDBOX / "data_inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    (SANDBOX / "data_quality_report.json").write_text(
        json.dumps({"overall": overall, "1h": q1, "4h": q4}, indent=2), encoding="utf-8"
    )
    if overall == "DATA_CONTAMINATED":
        raise RuntimeError("DATA_CONTAMINATED — stop")

    feat = cfg["predeclared_features"]
    atr = eng.wilder_atr(df1h["high"], df1h["low"], df1h["close"], int(feat["atr_period"]))
    atr_pct = eng.atr_percentile_trailing(atr, int(feat["atr_percentile_lookback"]))
    htf_rows, pit_stats = eng.build_closed_4h_map(df1h, df4h)
    if pit_stats["lookahead_fail_rows"] != 0 or pit_stats["future_feature_violations"] != 0:
        raise RuntimeError("STOP — LOOKAHEAD VIOLATION")

    parts = eng.assign_partitions(
        len(df1h),
        float(cfg["partitions"]["train_frac"]),
        float(cfg["partitions"]["validation_frac"]),
    )

    base_kwargs = dict(
        compression_threshold=float(feat["compression_threshold"]),
        range_lookback=int(feat["range_lookback_bars"]),
        breakout_buffer_frac=float(feat["breakout_buffer_frac"]),
        entry_mode="E1",
        stop_kind="S1",
        target_R=2.0,
        atr_stop_mult=float(cfg["stops"]["atr_stop_multiple"]),
        fee_leg=float(cfg["fees"]["taker_fee_rate_per_leg"]),
        max_hold=int(cfg["max_hold_bars"]),
        cooldown=int(cfg["cooldown_bars_after_exit"]),
        max_retest_bars=int(cfg["retest"]["max_retest_bars"]),
    )

    trades, counts = eng.run_strategy(
        df1h, htf_rows, atr, atr_pct, parts, cfg, ablation="A3", **base_kwargs
    )

    # Reproduction check vs prior A3
    m_all = metrics(trades)
    m_oos = metrics([t for t in trades if t.partition == "OOS"])
    prior_n = int(float(prior_a3_row["trade_count"]))
    prior_oos_n = int(float(prior_a3_row["OOS_trade_count"]))
    if m_all["trade_count"] != prior_n or m_oos["trade_count"] != prior_oos_n:
        raise RuntimeError(
            "A3 REPRODUCTION MISMATCH: "
            f"n={m_all['trade_count']} (prior {prior_n}), "
            f"oos={m_oos['trade_count']} (prior {prior_oos_n})"
        )
    # net R tolerance
    prior_oos_net = float(prior_a3_row["OOS_net_R"])
    if abs(m_oos["net_R"] - prior_oos_net) > 1e-6:
        raise RuntimeError(
            f"A3 REPRODUCTION MISMATCH OOS net_R {m_oos['net_R']} vs prior {prior_oos_net}"
        )

    longs = [t for t in trades if t.direction == "LONG"]
    shorts = [t for t in trades if t.direction == "SHORT"]
    m_long = metrics(longs)
    m_short = metrics(shorts)
    m_oos_l = metrics([t for t in longs if t.partition == "OOS"])
    m_oos_s = metrics([t for t in shorts if t.partition == "OOS"])

    # Ledgers
    ledger_fields = [
        "variant",
        "partition",
        "direction",
        "trade_id",
        "signal_time",
        "entry_time",
        "exit_time",
        "entry_price",
        "stop_price",
        "target_price",
        "exit_reason",
        "bars_held",
        "gross_R",
        "fees_R",
        "net_R",
        "lookahead_status",
        "selected_4h_open_time",
        "selected_4h_close_time",
        "selected_4h_is_closed",
        "decision_time",
        "ambiguity",
    ]
    comb_rows = [trade_to_row(t, "A3_COMBINED") for t in trades]
    long_rows = [trade_to_row(t, "A3_LONG_ONLY") for t in longs]
    short_rows = [trade_to_row(t, "A3_SHORT_ONLY") for t in shorts]
    write_csv(SANDBOX / "a3_combined_trade_ledger.csv", comb_rows, ledger_fields)
    write_csv(SANDBOX / "a3_long_trade_ledger.csv", long_rows, ledger_fields)
    write_csv(SANDBOX / "a3_short_trade_ledger.csv", short_rows, ledger_fields)

    # PIT audit from trades
    audit_rows = []
    for t in trades:
        audit_rows.append(
            {
                "decision_time": t.decision_time,
                "direction": t.direction,
                "partition": t.partition,
                "trade_id": t.trade_id,
                "selected_4h_open_time": t.selected_4h_open_time,
                "selected_4h_close_time": t.selected_4h_close_time,
                "selected_4h_is_closed": t.selected_4h_is_closed,
                "lookahead_status": t.lookahead_status,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "exit_reason": t.exit_reason,
                "net_R": t.net_R,
            }
        )
    write_csv(
        SANDBOX / "point_in_time_audit.csv",
        audit_rows,
        list(audit_rows[0].keys()) if audit_rows else ["decision_time"],
    )
    (SANDBOX / "point_in_time_summary.json").write_text(json.dumps(pit_stats, indent=2), encoding="utf-8")

    # Reconciliation
    ids = [t.trade_id for t in trades]
    recon = {
        "unique_trade_ids": len(set(ids)) == len(ids),
        "trade_count": len(trades),
        "unique_count": len(set(ids)),
        "duplicate_count": len(ids) - len(set(ids)),
        "long_count": len(longs),
        "short_count": len(shorts),
        "long_plus_short_equals_combined": len(longs) + len(shorts) == len(trades),
        "net_R_long_plus_short": m_long["net_R"] + m_short["net_R"],
        "net_R_combined": m_all["net_R"],
        "net_R_reconciles": abs((m_long["net_R"] + m_short["net_R"]) - m_all["net_R"]) < 1e-9,
        "fees_reconciles": abs((m_long["fees_R"] + m_short["fees_R"]) - m_all["fees_R"]) < 1e-9,
        "wins_reconciles": (m_long["wins"] + m_short["wins"]) == m_all["wins"],
        "losses_reconciles": (m_long["losses"] + m_short["losses"]) == m_all["losses"],
        "all_lookahead_pass": all(t.lookahead_status == "PASS" for t in trades),
        "all_4h_closed": all(t.selected_4h_is_closed for t in trades),
    }
    if not all(
        [
            recon["unique_trade_ids"],
            recon["long_plus_short_equals_combined"],
            recon["net_R_reconciles"],
            recon["fees_reconciles"],
            recon["wins_reconciles"],
            recon["losses_reconciles"],
            recon["all_lookahead_pass"],
            recon["all_4h_closed"],
        ]
    ):
        raise RuntimeError(f"TRADE RECONCILIATION FAILED: {recon}")

    # Partition x direction summary
    part_rows: list[dict[str, Any]] = []
    for part in ("TRAIN", "VALIDATION", "OOS"):
        for direction, subset in (
            ("COMBINED", [t for t in trades if t.partition == part]),
            ("LONG", [t for t in longs if t.partition == part]),
            ("SHORT", [t for t in shorts if t.partition == part]),
        ):
            m = metrics(subset)
            part_rows.append(
                {
                    "variant": "A3",
                    "partition": part,
                    "direction": direction,
                    "trades": m["trade_count"],
                    "wins": m["wins"],
                    "losses": m["losses"],
                    "win_rate": m["win_rate"],
                    "gross_R": m["gross_R"],
                    "fees_R": m["fees_R"],
                    "net_R": m["net_R"],
                    "average_R": m["average_R"],
                    "median_R": m["median_R"],
                    "profit_factor": m["profit_factor"],
                    "maximum_drawdown_R": m["maximum_drawdown_R"],
                    "longest_losing_streak": m["longest_losing_streak"],
                    "average_hold_bars": m["average_hold_bars"],
                    "average_hold_hours": m["average_hold_hours"],
                    "sample_size_label": m["sample_size_label"],
                }
            )
    write_csv(
        SANDBOX / "direction_partition_summary.csv",
        part_rows,
        list(part_rows[0].keys()),
    )

    # Attribution
    total_net = m_all["net_R"]
    oos_n = m_oos["trade_count"]
    attrib = {
        "A3_combined": m_all,
        "A3_long_only": m_long,
        "A3_short_only": m_short,
        "A3_oos_combined": m_oos,
        "A3_oos_long": m_oos_l,
        "A3_oos_short": m_oos_s,
        "pct_total_net_R_from_long": (100.0 * m_long["net_R"] / total_net) if total_net != 0 else None,
        "pct_total_net_R_from_short": (100.0 * m_short["net_R"] / total_net) if total_net != 0 else None,
        "pct_oos_trades_from_long": (100.0 * m_oos_l["trade_count"] / oos_n) if oos_n else None,
        "pct_oos_trades_from_short": (100.0 * m_oos_s["trade_count"] / oos_n) if oos_n else None,
        "signal_counts": counts,
        "reconciliation": recon,
        "reproduction": {
            "prior_trade_count": prior_n,
            "reproduced_trade_count": m_all["trade_count"],
            "prior_oos_trade_count": prior_oos_n,
            "reproduced_oos_trade_count": m_oos["trade_count"],
            "prior_oos_net_R": prior_oos_net,
            "reproduced_oos_net_R": m_oos["net_R"],
            "match": True,
        },
        "chronological_integrity": {
            "train_not_selected_using_val_or_oos": True,
            "validation_not_selected_using_oos": True,
            "oos_not_used_to_choose_direction": True,
            "no_threshold_changed_after_oos": True,
            "no_manual_trade_removal": True,
            "no_direction_disabled_before_reporting": True,
            "direction_filter_is_analysis_only": True,
        },
    }
    verdict = classify_direction(m_all, m_long, m_short, m_oos, m_oos_l, m_oos_s, part_rows)
    attrib["direction_verdict"] = verdict

    (SANDBOX / "direction_comparison.json").write_text(
        json.dumps(attrib, indent=2, default=str), encoding="utf-8"
    )

    # Research report
    lines = [
        "# S3-A3 Direction Split — Research Report",
        "",
        f"Generated: {utc_now()}",
        f"Parent: `{PRIOR.name}`",
        "Independent of COMBO_02. Analysis-only direction split of frozen A3.",
        "Not a profitability claim.",
        "",
        "## Reproduction",
        "",
        f"- Prior A3 trades: {prior_n} → reproduced: {m_all['trade_count']}",
        f"- Prior OOS trades: {prior_oos_n} → reproduced: {m_oos['trade_count']}",
        f"- Prior OOS net_R: {prior_oos_net} → reproduced: {m_oos['net_R']}",
        f"- Fingerprints identical: YES ({fp1[:16]}… / {fp4[:16]}…)",
        "",
        "## Point-in-time",
        "",
        f"- candidate rows: {pit_stats['candidate_rows']}",
        f"- lookahead pass: {pit_stats['lookahead_pass_rows']}",
        f"- lookahead fail: {pit_stats['lookahead_fail_rows']}",
        f"- future violations: {pit_stats['future_feature_violations']}",
        f"- missing HTF: {pit_stats['missing_htf_rows']}",
        "",
        "## Direction comparison (full sample)",
        "",
        f"- COMBINED: n={m_all['trade_count']} avg_R={m_all['average_R']} net_R={m_all['net_R']} PF={m_all['profit_factor']} DD={m_all['maximum_drawdown_R']}",
        f"- LONG: n={m_long['trade_count']} avg_R={m_long['average_R']} net_R={m_long['net_R']} PF={m_long['profit_factor']} DD={m_long['maximum_drawdown_R']}",
        f"- SHORT: n={m_short['trade_count']} avg_R={m_short['average_R']} net_R={m_short['net_R']} PF={m_short['profit_factor']} DD={m_short['maximum_drawdown_R']}",
        "",
        "## OOS",
        "",
        f"- COMBINED: n={m_oos['trade_count']} avg_R={m_oos['average_R']} net_R={m_oos['net_R']} PF={m_oos['profit_factor']}",
        f"- LONG: n={m_oos_l['trade_count']} avg_R={m_oos_l['average_R']} net_R={m_oos_l['net_R']} PF={m_oos_l['profit_factor']}",
        f"- SHORT: n={m_oos_s['trade_count']} avg_R={m_oos_s['average_R']} net_R={m_oos_s['net_R']} PF={m_oos_s['profit_factor']}",
        "",
        f"## Verdict: `{verdict['primary_verdict']}`",
        f"Secondary: {verdict['secondary_labels']}",
        "",
        "## Safety",
        "",
        "```text",
        "READ ONLY",
        "NO EXISTING CODE MODIFIED",
        "NO EXISTING REPORTS OVERWRITTEN",
        "NO DATABASE DATA MODIFIED",
        "NO STRATEGY MODIFIED",
        "NO LIVE/PAPER EXECUTION",
        "NO DEPLOYMENT",
        "```",
        "",
    ]
    (SANDBOX / "research_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "sandbox": str(SANDBOX),
                "verdict": verdict["primary_verdict"],
                "secondary": verdict["secondary_labels"],
                "combined_n": m_all["trade_count"],
                "oos_long": {"n": m_oos_l["trade_count"], "avg_R": m_oos_l["average_R"]},
                "oos_short": {"n": m_oos_s["trade_count"], "avg_R": m_oos_s["average_R"]},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
