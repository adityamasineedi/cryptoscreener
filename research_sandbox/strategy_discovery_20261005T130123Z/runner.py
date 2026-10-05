#!/usr/bin/env python3
"""A3 SHORT-only cross-symbol validation — ETHUSDT / SOLUSDT.

Frozen A3 from prior BTC sandboxes. Analysis-only SHORT filter (same as BTC
direction-split methodology). Sibling-sandbox engine only; no production imports.
Writes only inside this directory.
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
PRIOR_S3 = REPO / "research_sandbox" / "strategy_discovery_20261005T124726Z"
PRIOR_DIR = REPO / "research_sandbox" / "strategy_discovery_20261005T125552Z"
CACHE = REPO / "backend" / "data" / "research_cache" / "ohlcv" / "ohlcv_v1"

# Contiguous DATA_OK shards only (exclude gappy / insufficient Oct-2026 fragments)
SHARDS = {
    "ETHUSDT": {
        "1h": [
            "ETHUSDT_1h_2024-01-01_2025-06-30_ohlcv_v1",
            "ETHUSDT_1h_2025-07-01_2026-09-30_ohlcv_v1",
        ],
        "4h": [
            "ETHUSDT_4h_2024-01-01_2025-06-30_ohlcv_v1",
            "ETHUSDT_4h_2025-07-01_2026-09-30_ohlcv_v1",
        ],
    },
    "SOLUSDT": {
        "1h": [
            "SOLUSDT_1h_2024-01-01_2025-06-30_ohlcv_v1",
            "SOLUSDT_1h_2025-07-01_2026-09-30_ohlcv_v1",
        ],
        "4h": [
            "SOLUSDT_4h_2024-01-01_2025-06-30_ohlcv_v1",
            "SOLUSDT_4h_2025-07-01_2026-09-30_ohlcv_v1",
        ],
    },
}

BTC_REF = {
    "label": "INSUFFICIENT_SAMPLE",
    "note": "Frozen reference from strategy_discovery_20261005T125552Z; not re-run",
    "total_trades": 56,
    "total_average_R": 0.407,
    "total_net_R": 22.82,
    "total_PF": 1.98,
    "total_max_DD": 12.31,
    "oos_trades": 13,
    "oos_average_R": 0.673,
    "oos_net_R": 8.75,
    "oos_PF": 3.83,
    "oos_max_DD": 1.05,
    "calendar": "2022-10-06 → 2026-10-05 UTC",
}


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
    path = PRIOR_S3 / "runner.py"
    if not path.exists():
        raise RuntimeError("A3 REPRODUCTION UNAVAILABLE WITHOUT DEFINITION")
    spec = importlib.util.spec_from_file_location("prior_s3_runner_xs", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("A3 REPRODUCTION UNAVAILABLE WITHOUT DEFINITION")
    mod = importlib.util.module_from_spec(spec)
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
        "average_hold_hours": avg_hold,
        "sample_size_label": sample_label(n),
    }


def load_concat_shards(
    stem_list: list[str], step_s: int, eng: Any
) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    frames = []
    meta = []
    for stem in stem_list:
        pq = CACHE / f"{stem}.parquet"
        man = CACHE / f"{stem}.manifest.json"
        if not pq.exists() or not man.exists():
            raise RuntimeError(f"SYMBOL VALIDATION UNAVAILABLE — missing {stem}")
        m = json.loads(man.read_text(encoding="utf-8"))
        dq = (m.get("validation") or {}).get("data_quality") or {}
        if dq.get("status") != "DATA_OK":
            raise RuntimeError(f"SYMBOL VALIDATION UNAVAILABLE — {stem} status={dq.get('status')}")
        df = pd.read_parquet(pq)
        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)
        frames.append(df)
        meta.append(
            {
                "stem": stem,
                "source_file": str(pq.relative_to(REPO)).replace("\\", "/"),
                "manifest_path": str(man.relative_to(REPO)).replace("\\", "/"),
                "first_timestamp": m.get("first_timestamp"),
                "last_timestamp": m.get("last_timestamp"),
                "row_count": m.get("row_count"),
                "timezone": "UTC",
                "missing_bars": dq.get("missing_candles", 0),
                "duplicate_bars": dq.get("duplicate_candles", 0),
                "out_of_order_rows": 0,
                "invalid_ohlc_rows": dq.get("bad_ohlc", 0),
                "dataset_fingerprint": m.get("sha256"),
                "manifest_status": m.get("validation_status"),
                "data_quality_status": dq.get("status"),
            }
        )
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values("timestamp").reset_index(drop=True)
    before = len(out)
    out = out.drop_duplicates(subset=["timestamp"], keep="first").reset_index(drop=True)
    dropped = before - len(out)
    q = eng.validate_ohlcv(out, step_s, stem_list[0].split("_")[0])
    if q["duplicate_bars"] or not q["ordered"] or q["invalid_ohlc_rows"]:
        raise RuntimeError(f"DATA_CONTAMINATED after concat: {q}")
    if q["gap_count"]:
        raise RuntimeError(f"DATA_CONTAMINATED — gaps after concat: {q}")
    q["dropped_duplicate_timestamps_on_merge"] = dropped
    return out, meta, q


def trade_row(symbol: str, t: Any) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "partition": t.partition,
        "direction": t.direction,
        "trade_id": f"{symbol}-{t.trade_id}",
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
        "selected_4h_open_time": t.selected_4h_open_time,
        "selected_4h_close_time": t.selected_4h_close_time,
        "selected_4h_is_closed": t.selected_4h_is_closed,
        "lookahead_status": t.lookahead_status,
        "decision_time": t.decision_time,
        "ambiguity": t.ambiguity,
    }


def classify(
    eth_oos: dict[str, Any],
    sol_oos: dict[str, Any],
    eth_all: dict[str, Any],
    sol_all: dict[str, Any],
    comparable: str,
) -> list[str]:
    labels = [comparable] if comparable else []
    # Supportive OOS = avg_R > 0 and n>=20
    eth_ok = eth_oos["trade_count"] >= 20 and eth_oos["average_R"] is not None and eth_oos["average_R"] > 0
    sol_ok = sol_oos["trade_count"] >= 20 and sol_oos["average_R"] is not None and sol_oos["average_R"] > 0
    eth_insuf = eth_oos["trade_count"] < 20
    sol_insuf = sol_oos["trade_count"] < 20

    if eth_ok and sol_ok:
        labels.append("CROSS_SYMBOL_EXPLORATORY")
    elif eth_insuf and sol_insuf:
        labels.append("INSUFFICIENT_SAMPLE")
    elif (eth_ok and not sol_ok) or (sol_ok and not eth_ok):
        labels.append("SYMBOL_SPECIFIC")
        if eth_insuf or sol_insuf:
            labels.append("INSUFFICIENT_SAMPLE")
    else:
        # both have samples but not both supportive
        if eth_insuf or sol_insuf:
            labels.append("INSUFFICIENT_SAMPLE")
        eth_neg = eth_oos["average_R"] is not None and eth_oos["average_R"] <= 0 and eth_oos["trade_count"] >= 10
        sol_neg = sol_oos["average_R"] is not None and sol_oos["average_R"] <= 0 and sol_oos["trade_count"] >= 10
        if eth_neg and sol_neg:
            labels.append("REJECTED_FOR_CURRENT_DEFINITION")
        elif (eth_neg and sol_ok) or (sol_neg and eth_ok):
            labels.append("SYMBOL_SPECIFIC")
        else:
            labels.append("INSUFFICIENT_SAMPLE")

    # time-period: sign flip across partitions within a symbol (checked later in main)
    return sorted(set(labels))


def main() -> None:
    eng = load_prior_engine()
    prior_cfg = json.loads((PRIOR_S3 / "config.json").read_text(encoding="utf-8"))
    prior_feat = json.loads((PRIOR_S3 / "feature_definition.json").read_text(encoding="utf-8"))
    cfg = json.loads(json.dumps(prior_cfg))
    cfg["experiment_id"] = "S3_A3_SHORT_CROSS_SYMBOL"
    cfg["parent_sandboxes"] = [PRIOR_S3.name, PRIOR_DIR.name]

    (SANDBOX / "repository_safety_report.md").write_text(
        "\n".join(
            [
                "# Repository safety report",
                "",
                f"- generated_at: {utc_now()}",
                "- branch: main",
                f"- sandbox: {SANDBOX}",
                "- Existing code modified: NO",
                "- Existing configs modified: NO",
                "- Existing DB data modified: NO",
                "- Existing reports overwritten: NO",
                "- Existing strategy modified: NO",
                "- Live trading affected: NO",
                "- Paper trading affected: NO",
                "- Dependencies changed: NO",
                "- Migrations run: NO",
                "- note: Sibling-sandbox engine only; analysis-only SHORT filter.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    (SANDBOX / "configuration_reproduction.json").write_text(
        json.dumps(
            {
                "frozen_from": [
                    "research_sandbox/strategy_discovery_20261005T124726Z/config.json",
                    "research_sandbox/strategy_discovery_20261005T124726Z/feature_definition.json",
                    "research_sandbox/strategy_discovery_20261005T125552Z/ (SHORT analysis method)",
                ],
                "a3_definition": cfg["ablations"]["A3"],
                "predeclared_features": cfg["predeclared_features"],
                "retest": cfg["retest"],
                "entry_mode": "E1",
                "stop": "S1",
                "target_R": 2.0,
                "fees": cfg["fees"],
                "intrabar_ambiguity_rule": cfg["intrabar_ambiguity_rule"],
                "partitions": cfg["partitions"],
                "cooldown_bars_after_exit": cfg["cooldown_bars_after_exit"],
                "max_hold_bars": cfg["max_hold_bars"],
                "feature_definition_snapshot": prior_feat,
                "direction_mode": "A3 combined run then analysis-only SHORT filter (matches BTC direction-split)",
                "no_parameter_retune": True,
                "btc_calendar": "2022-10-06 → 2026-10-05",
                "eth_sol_calendar_policy": "Concatenate contiguous DATA_OK shards 2024-01-01→2026-09-30; symbol-specific 60/20/20",
                "comparability": "NOT_STRICTLY_COMPARABLE_TO_BTC_CALENDAR",
                "btc_reference": BTC_REF,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    inventory: dict[str, Any] = {"symbols": {}, "overall_notes": []}
    quality: dict[str, Any] = {}
    series: dict[str, dict[str, Any]] = {}

    for symbol in ("ETHUSDT", "SOLUSDT"):
        df1h, meta1, q1 = load_concat_shards(SHARDS[symbol]["1h"], 3600, eng)
        df4h, meta4, q4 = load_concat_shards(SHARDS[symbol]["4h"], 14400, eng)
        inventory["symbols"][symbol] = {
            "1h_shards": meta1,
            "4h_shards": meta4,
            "1h_concat": {
                "first_timestamp": q1["first_timestamp"],
                "last_timestamp": q1["last_timestamp"],
                "row_count": q1["row_count"],
                "quality": q1,
            },
            "4h_concat": {
                "first_timestamp": q4["first_timestamp"],
                "last_timestamp": q4["last_timestamp"],
                "row_count": q4["row_count"],
                "quality": q4,
            },
        }
        quality[symbol] = {"1h": q1, "4h": q4, "overall": "DATA_OK"}
        series[symbol] = {"1h": df1h, "4h": df4h}

    inventory["btc_reference_calendar"] = BTC_REF["calendar"]
    inventory["comparability"] = "NOT_STRICTLY_COMPARABLE_TO_BTC_CALENDAR"
    inventory["overall_notes"].append(
        "ETH/SOL usable continuous DATA_OK window is 2024-01-01 → 2026-09-30; BTC was 2022-10-06 → 2026-10-05."
    )
    (SANDBOX / "data_inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    (SANDBOX / "data_quality_report.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")

    feat = cfg["predeclared_features"]
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

    pit_all = {
        "total_decision_rows": 0,
        "candidate_rows": 0,
        "trade_rows": 0,
        "lookahead_pass_rows": 0,
        "lookahead_fail_rows": 0,
        "future_feature_violations": 0,
        "missing_4h_mappings": 0,
        "unfinished_4h_mappings": 0,
    }
    audit_rows: list[dict[str, Any]] = []
    part_summary: list[dict[str, Any]] = []
    symbol_results: dict[str, Any] = {}
    ledgers: dict[str, list[dict[str, Any]]] = {"ETHUSDT": [], "SOLUSDT": []}
    time_period_flags: list[str] = []

    for symbol in ("ETHUSDT", "SOLUSDT"):
        df1h = series[symbol]["1h"]
        df4h = series[symbol]["4h"]
        atr = eng.wilder_atr(df1h["high"], df1h["low"], df1h["close"], int(feat["atr_period"]))
        atr_pct = eng.atr_percentile_trailing(atr, int(feat["atr_percentile_lookback"]))
        htf_rows, pit = eng.build_closed_4h_map(df1h, df4h)
        if pit["lookahead_fail_rows"] != 0 or pit["future_feature_violations"] != 0:
            raise RuntimeError(f"STOP — LOOKAHEAD VIOLATION on {symbol}: {pit}")

        pit_all["total_decision_rows"] += len(df1h)
        pit_all["candidate_rows"] += pit["candidate_rows"]
        pit_all["lookahead_pass_rows"] += pit["lookahead_pass_rows"]
        pit_all["lookahead_fail_rows"] += pit["lookahead_fail_rows"]
        pit_all["future_feature_violations"] += pit["future_feature_violations"]
        pit_all["missing_4h_mappings"] += pit["missing_htf_rows"]

        # unfinished mappings should be zero by construction of closed-only map
        unfinished = sum(1 for r in htf_rows if r["lookahead_status"] == "FAIL")
        pit_all["unfinished_4h_mappings"] += unfinished
        if unfinished:
            raise RuntimeError(f"STOP — LOOKAHEAD VIOLATION unfinished 4h on {symbol}")

        parts = eng.assign_partitions(
            len(df1h),
            float(cfg["partitions"]["train_frac"]),
            float(cfg["partitions"]["validation_frac"]),
        )
        # Partition date bounds
        bounds = {}
        for name in ("TRAIN", "VALIDATION", "OOS"):
            idxs = [i for i, p in enumerate(parts) if p == name]
            bounds[name] = {
                "start": df1h["timestamp"].iloc[idxs[0]].isoformat(),
                "end": df1h["timestamp"].iloc[idxs[-1]].isoformat(),
                "1h_rows": len(idxs),
            }

        trades_all, counts = eng.run_strategy(
            df1h, htf_rows, atr, atr_pct, parts, cfg, ablation="A3", **base_kwargs
        )
        # Analysis-only SHORT filter
        shorts = [t for t in trades_all if t.direction == "SHORT"]
        for t in shorts:
            assert t.selected_4h_close_time  # noqa: S101
            # decision_time stored as iso on trade; compare via htf at decision_i
            assert t.selected_4h_is_closed is True  # noqa: S101
            assert t.lookahead_status == "PASS"  # noqa: S101

        pit_all["trade_rows"] += len(shorts)
        for t in shorts:
            ledgers[symbol].append(trade_row(symbol, t))
            audit_rows.append(
                {
                    "symbol": symbol,
                    "decision_time": t.decision_time,
                    "selected_4h_open_time": t.selected_4h_open_time,
                    "selected_4h_close_time": t.selected_4h_close_time,
                    "selected_4h_is_closed": t.selected_4h_is_closed,
                    "lookahead_status": t.lookahead_status,
                    "partition": t.partition,
                    "trade_id": f"{symbol}-{t.trade_id}",
                    "net_R": t.net_R,
                }
            )

        m_all = metrics(shorts)
        m_oos = metrics([t for t in shorts if t.partition == "OOS"])
        # reconciliation
        ids = [r["trade_id"] for r in ledgers[symbol]]
        recon = {
            "unique": len(set(ids)) == len(ids),
            "net_match": abs(sum(float(r["net_R"]) for r in ledgers[symbol]) - m_all["net_R"]) < 1e-9,
            "fees_match": abs(sum(float(r["fees_R"]) for r in ledgers[symbol]) - m_all["fees_R"]) < 1e-9,
            "wins_match": sum(1 for r in ledgers[symbol] if float(r["net_R"]) > 0) == m_all["wins"],
            "losses_match": sum(1 for r in ledgers[symbol] if float(r["net_R"]) <= 0) == m_all["losses"],
        }
        if not all(recon.values()):
            raise RuntimeError(f"RECONCILIATION FAILED {symbol}: {recon}")

        part_avgs = []
        for part in ("TRAIN", "VALIDATION", "OOS"):
            subset = [t for t in shorts if t.partition == part]
            m = metrics(subset)
            part_avgs.append(m["average_R"])
            part_summary.append(
                {
                    "symbol": symbol,
                    "partition": part,
                    "partition_start": bounds[part]["start"],
                    "partition_end": bounds[part]["end"],
                    "partition_1h_rows": bounds[part]["1h_rows"],
                    "candidate_count": counts.get("breakout_signal_count"),
                    "trade_count": m["trade_count"],
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
                    "comparability": "NOT_STRICTLY_COMPARABLE_TO_BTC_CALENDAR",
                }
            )

        # time-period specific within symbol
        signed = [(a > 0) for a in part_avgs if a is not None]
        if len(signed) == 3 and any(signed) and not all(signed):
            time_period_flags.append(symbol)

        symbol_results[symbol] = {
            "available_start": inventory["symbols"][symbol]["1h_concat"]["first_timestamp"],
            "available_end": inventory["symbols"][symbol]["1h_concat"]["last_timestamp"],
            "usable_start": inventory["symbols"][symbol]["1h_concat"]["first_timestamp"],
            "usable_end": inventory["symbols"][symbol]["1h_concat"]["last_timestamp"],
            "warmup_rows": int(feat["atr_percentile_lookback"]),
            "partitions": bounds,
            "a3_combined_trade_count": len(trades_all),
            "short_trade_count": len(shorts),
            "signal_counts": counts,
            "all": m_all,
            "oos": m_oos,
            "reconciliation": recon,
            "comparability": "NOT_STRICTLY_COMPARABLE_TO_BTC_CALENDAR",
        }

    if pit_all["lookahead_fail_rows"] or pit_all["future_feature_violations"] or pit_all["unfinished_4h_mappings"]:
        raise RuntimeError(f"STOP — LOOKAHEAD VIOLATION aggregate: {pit_all}")

    # Write ledgers
    fields = [
        "symbol",
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
        "selected_4h_open_time",
        "selected_4h_close_time",
        "selected_4h_is_closed",
        "lookahead_status",
        "decision_time",
        "ambiguity",
    ]
    write_csv(SANDBOX / "eth_short_trade_ledger.csv", ledgers["ETHUSDT"], fields)
    write_csv(SANDBOX / "sol_short_trade_ledger.csv", ledgers["SOLUSDT"], fields)
    write_csv(
        SANDBOX / "point_in_time_audit.csv",
        audit_rows,
        [
            "symbol",
            "decision_time",
            "selected_4h_open_time",
            "selected_4h_close_time",
            "selected_4h_is_closed",
            "lookahead_status",
            "partition",
            "trade_id",
            "net_R",
        ],
    )
    write_csv(
        SANDBOX / "symbol_partition_summary.csv",
        part_summary,
        list(part_summary[0].keys()),
    )

    # Pooled ETH+SOL SHORT
    pooled_trades_metrics_input = []
    # rebuild from ledgers for pooled metrics
    class _T:
        pass

    pooled_objs = []
    for sym in ("ETHUSDT", "SOLUSDT"):
        for r in ledgers[sym]:
            o = _T()
            o.net_R = float(r["net_R"])
            o.gross_R = float(r["gross_R"])
            o.fees_R = float(r["fees_R"])
            o.hold_bars = int(r["bars_held"]) if r["bars_held"] is not None else None
            o.partition = r["partition"]
            pooled_objs.append(o)
    m_pool = metrics(pooled_objs)
    m_pool_oos = metrics([t for t in pooled_objs if t.partition == "OOS"])

    labels = classify(
        symbol_results["ETHUSDT"]["oos"],
        symbol_results["SOLUSDT"]["oos"],
        symbol_results["ETHUSDT"]["all"],
        symbol_results["SOLUSDT"]["all"],
        "NOT_STRICTLY_COMPARABLE",
    )
    if time_period_flags:
        labels.append("TIME_PERIOD_SPECIFIC")
        labels = sorted(set(labels))

    cross_rows = [
        {
            "group": "ETHUSDT",
            "trades": symbol_results["ETHUSDT"]["all"]["trade_count"],
            "average_R": symbol_results["ETHUSDT"]["all"]["average_R"],
            "net_R": symbol_results["ETHUSDT"]["all"]["net_R"],
            "profit_factor": symbol_results["ETHUSDT"]["all"]["profit_factor"],
            "maximum_drawdown_R": symbol_results["ETHUSDT"]["all"]["maximum_drawdown_R"],
            "oos_trades": symbol_results["ETHUSDT"]["oos"]["trade_count"],
            "oos_average_R": symbol_results["ETHUSDT"]["oos"]["average_R"],
            "oos_net_R": symbol_results["ETHUSDT"]["oos"]["net_R"],
            "oos_profit_factor": symbol_results["ETHUSDT"]["oos"]["profit_factor"],
            "oos_maximum_drawdown_R": symbol_results["ETHUSDT"]["oos"]["maximum_drawdown_R"],
            "sample_label_all": symbol_results["ETHUSDT"]["all"]["sample_size_label"],
            "sample_label_oos": symbol_results["ETHUSDT"]["oos"]["sample_size_label"],
            "comparability": "NOT_STRICTLY_COMPARABLE_TO_BTC_CALENDAR",
        },
        {
            "group": "SOLUSDT",
            "trades": symbol_results["SOLUSDT"]["all"]["trade_count"],
            "average_R": symbol_results["SOLUSDT"]["all"]["average_R"],
            "net_R": symbol_results["SOLUSDT"]["all"]["net_R"],
            "profit_factor": symbol_results["SOLUSDT"]["all"]["profit_factor"],
            "maximum_drawdown_R": symbol_results["SOLUSDT"]["all"]["maximum_drawdown_R"],
            "oos_trades": symbol_results["SOLUSDT"]["oos"]["trade_count"],
            "oos_average_R": symbol_results["SOLUSDT"]["oos"]["average_R"],
            "oos_net_R": symbol_results["SOLUSDT"]["oos"]["net_R"],
            "oos_profit_factor": symbol_results["SOLUSDT"]["oos"]["profit_factor"],
            "oos_maximum_drawdown_R": symbol_results["SOLUSDT"]["oos"]["maximum_drawdown_R"],
            "sample_label_all": symbol_results["SOLUSDT"]["all"]["sample_size_label"],
            "sample_label_oos": symbol_results["SOLUSDT"]["oos"]["sample_size_label"],
            "comparability": "NOT_STRICTLY_COMPARABLE_TO_BTC_CALENDAR",
        },
        {
            "group": "ETH+SOL_POOLED",
            "trades": m_pool["trade_count"],
            "average_R": m_pool["average_R"],
            "net_R": m_pool["net_R"],
            "profit_factor": m_pool["profit_factor"],
            "maximum_drawdown_R": m_pool["maximum_drawdown_R"],
            "oos_trades": m_pool_oos["trade_count"],
            "oos_average_R": m_pool_oos["average_R"],
            "oos_net_R": m_pool_oos["net_R"],
            "oos_profit_factor": m_pool_oos["profit_factor"],
            "oos_maximum_drawdown_R": m_pool_oos["maximum_drawdown_R"],
            "sample_label_all": m_pool["sample_size_label"],
            "sample_label_oos": m_pool_oos["sample_size_label"],
            "comparability": "NOT_STRICTLY_COMPARABLE_TO_BTC_CALENDAR",
        },
        {
            "group": "BTCUSDT_REFERENCE_FROZEN",
            "trades": BTC_REF["total_trades"],
            "average_R": BTC_REF["total_average_R"],
            "net_R": BTC_REF["total_net_R"],
            "profit_factor": BTC_REF["total_PF"],
            "maximum_drawdown_R": BTC_REF["total_max_DD"],
            "oos_trades": BTC_REF["oos_trades"],
            "oos_average_R": BTC_REF["oos_average_R"],
            "oos_net_R": BTC_REF["oos_net_R"],
            "oos_profit_factor": BTC_REF["oos_PF"],
            "oos_maximum_drawdown_R": BTC_REF["oos_max_DD"],
            "sample_label_all": "PRELIMINARY",
            "sample_label_oos": "INSUFFICIENT_SAMPLE",
            "comparability": "REFERENCE_ONLY_DIFFERENT_CALENDAR",
        },
    ]
    write_csv(SANDBOX / "cross_symbol_summary.csv", cross_rows, list(cross_rows[0].keys()))

    summary = {
        "classifications": labels,
        "point_in_time": pit_all,
        "symbol_results": symbol_results,
        "pooled": {"all": m_pool, "oos": m_pool_oos},
        "btc_reference": BTC_REF,
        "time_period_specific_symbols": time_period_flags,
    }
    (SANDBOX / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (SANDBOX / "point_in_time_summary.json").write_text(json.dumps(pit_all, indent=2), encoding="utf-8")

    # Report
    lines = [
        "# A3 SHORT-only cross-symbol validation",
        "",
        f"Generated: {utc_now()}",
        "Frozen A3 SHORT analysis on ETHUSDT + SOLUSDT. Not a profitability claim.",
        f"Classifications: {labels}",
        "",
        "## Comparability",
        "",
        "NOT_STRICTLY_COMPARABLE_TO_BTC_CALENDAR — ETH/SOL 2024-01-01→2026-09-30 vs BTC 2022-10-06→2026-10-05.",
        "",
        "## PIT",
        "",
        json.dumps(pit_all, indent=2),
        "",
        "## Per symbol OOS SHORT",
        "",
        f"- ETH: n={symbol_results['ETHUSDT']['oos']['trade_count']} avg_R={symbol_results['ETHUSDT']['oos']['average_R']} net_R={symbol_results['ETHUSDT']['oos']['net_R']}",
        f"- SOL: n={symbol_results['SOLUSDT']['oos']['trade_count']} avg_R={symbol_results['SOLUSDT']['oos']['average_R']} net_R={symbol_results['SOLUSDT']['oos']['net_R']}",
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
                "classifications": labels,
                "eth_oos": symbol_results["ETHUSDT"]["oos"],
                "sol_oos": symbol_results["SOLUSDT"]["oos"],
                "pooled_oos_n": m_pool_oos["trade_count"],
                "pit": pit_all,
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
