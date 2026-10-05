#!/usr/bin/env python3
"""S1 HTF Trend Pullback — isolated sandbox research.

Independent of COMBO_02 / production engines.
Uses sibling S3 sandbox helpers for OHLCV load, validation, closed-4h map only.
All S1 signal logic is local and causal.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SANDBOX = Path(__file__).resolve().parent
REPO = SANDBOX.parents[1]
PRIOR_S3 = REPO / "research_sandbox" / "strategy_discovery_20261005T124726Z"
CFG = json.loads((SANDBOX / "config.json").read_text(encoding="utf-8"))


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


def load_s3_helpers():
    path = PRIOR_S3 / "runner.py"
    spec = importlib.util.spec_from_file_location("prior_s3_helpers_s1", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Missing prior S3 sandbox helpers")
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


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def metrics(trades: list[Any]) -> dict[str, Any]:
    closed = [t for t in trades if t.net_R is not None]
    n = len(closed)
    if n == 0:
        return {
            "candidate_count": 0,
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


@dataclass
class Trade:
    trade_id: str
    direction: str
    partition: str
    decision_time: str
    entry_time: str
    exit_time: str
    entry_price: float
    stop_price: float
    target_price: float
    exit_reason: str
    hold_bars: int
    gross_R: float
    fees_R: float
    net_R: float
    selected_4h_open_time: str
    selected_4h_close_time: str
    selected_4h_is_closed: bool
    lookahead_status: str
    ambiguity: str
    ablation: str
    pullback_low: float
    pullback_high: float


def fee_R(entry: float, stop: float, fee_leg: float) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return float("nan")
    return (2.0 * fee_leg * entry) / risk


def simulate_exit(df, entry_i, direction, entry, stop, target, max_hold):
    n = len(df)
    end = min(n - 1, entry_i + max_hold)
    for j in range(entry_i + 1, end + 1):
        hi = float(df["high"].iloc[j])
        lo = float(df["low"].iloc[j])
        if direction == "LONG":
            hit_stop = lo <= stop
            hit_tgt = hi >= target
            if hit_stop and hit_tgt:
                return j, stop, "STOP_FIRST", "BOTH_TOUCHED_SAME_CANDLE"
            if hit_stop:
                return j, stop, "SL", "NONE"
            if hit_tgt:
                return j, target, "TP", "NONE"
        else:
            hit_stop = hi >= stop
            hit_tgt = lo <= target
            if hit_stop and hit_tgt:
                return j, stop, "STOP_FIRST", "BOTH_TOUCHED_SAME_CANDLE"
            if hit_stop:
                return j, stop, "SL", "NONE"
            if hit_tgt:
                return j, target, "TP", "NONE"
    return end, float(df["close"].iloc[end]), "TIME", "NONE"


def realized_R(direction, entry, stop, exit_px):
    risk = abs(entry - stop)
    if risk <= 0:
        return float("nan")
    if direction == "LONG":
        return (exit_px - entry) / risk
    return (entry - exit_px) / risk


def build_4h_trend_table(df4h: pd.DataFrame, ema_period: int) -> pd.DataFrame:
    """Causal 4h EMA trend on completed 4h bars (indexed by open time)."""
    out = df4h.copy()
    out["ema"] = ema(out["close"], ema_period)
    out["ema_prev"] = out["ema"].shift(1)
    out["close_time"] = out["timestamp"] + pd.Timedelta(hours=4)
    bull = (out["close"] > out["ema"]) & (out["ema"] > out["ema_prev"])
    bear = (out["close"] < out["ema"]) & (out["ema"] < out["ema_prev"])
    out["trend"] = np.where(bull, "BULL", np.where(bear, "BEAR", "NEUTRAL"))
    return out


def run_s1(
    df1h: pd.DataFrame,
    htf_rows: list[dict[str, Any]],
    df4h_trend: pd.DataFrame,
    ema20: pd.Series,
    atr: pd.Series,
    atr_pct: pd.Series | None,
    parts: list[str],
    *,
    pullback_window: int,
    stop_buffer_frac: float,
    target_R: float,
    fee_leg: float,
    max_hold: int,
    cooldown: int,
    ablation: str,
    vol_gate_pct: float | None,
) -> tuple[list[Trade], dict[str, int]]:
    n = len(df1h)
    # Map 4h open -> trend row for quick lookup
    trend_by_open = {
        pd.Timestamp(df4h_trend["timestamp"].iloc[i]): str(df4h_trend["trend"].iloc[i])
        for i in range(len(df4h_trend))
        if not pd.isna(df4h_trend["ema"].iloc[i])
    }

    trades: list[Trade] = []
    counts = {
        "candidate_count": 0,
        "long_candidates": 0,
        "short_candidates": 0,
        "missing_htf": 0,
        "lookahead_fail": 0,
    }
    next_allowed = 0
    tid = 0
    warmup = max(pullback_window + 5, CFG["predeclared"]["setup_ema_period"] + 5)

    for i in range(n):
        if i < next_allowed or i < warmup:
            continue
        htf = htf_rows[i]
        if htf["lookahead_status"] == "MISSING_HTF":
            counts["missing_htf"] += 1
            continue
        if htf["lookahead_status"] != "PASS" or not htf["selected_4h_is_closed"]:
            counts["lookahead_fail"] += 1
            continue

        # Assert closed HTF
        assert htf["selected_4h_close_time"] <= htf["decision_time"]
        assert htf["selected_4h_is_closed"] is True

        open4 = pd.Timestamp(htf["selected_4h_open_time"])
        trend = trend_by_open.get(open4)
        if trend is None or trend == "NEUTRAL":
            continue

        # Optional A1 volatility descriptive gate
        if vol_gate_pct is not None:
            ap = atr_pct.iloc[i] if atr_pct is not None else float("nan")
            if math.isnan(ap) or ap > vol_gate_pct:
                continue

        e20 = float(ema20.iloc[i])
        if math.isnan(e20) or math.isnan(atr.iloc[i]):
            continue

        # Pullback window: completed bars [i-W, i)
        sl = slice(i - pullback_window, i)
        pb_lows = df1h["low"].iloc[sl]
        pb_highs = df1h["high"].iloc[sl]
        ema_win = ema20.iloc[sl]
        if ema_win.isna().any():
            continue
        pullback_low = float(pb_lows.min())
        pullback_high = float(pb_highs.max())
        close = float(df1h["close"].iloc[i])
        buf = stop_buffer_frac * close

        # Touched EMA20 during pullback window (causal per-bar EMA)
        touched_ema_from_above = bool((pb_lows <= ema_win).any())
        touched_ema_from_below = bool((pb_highs >= ema_win).any())

        direction = None
        if trend == "BULL":
            # LONG: pullback toward EMA20 then continuation above pullback high
            if touched_ema_from_above and close > pullback_high and close > e20:
                direction = "LONG"
        elif trend == "BEAR":
            if touched_ema_from_below and close < pullback_low and close < e20:
                direction = "SHORT"

        if direction is None:
            continue

        counts["candidate_count"] += 1
        if direction == "LONG":
            counts["long_candidates"] += 1
        else:
            counts["short_candidates"] += 1

        entry_i = i
        entry = close
        if direction == "LONG":
            stop = pullback_low - buf
            if stop >= entry:
                continue
            risk = entry - stop
            target = entry + target_R * risk
        else:
            stop = pullback_high + buf
            if stop <= entry:
                continue
            risk = stop - entry
            target = entry - target_R * risk
        if risk / entry < 1e-6:
            continue

        exit_i, exit_px, reason, amb = simulate_exit(
            df1h, entry_i, direction, entry, stop, target, max_hold
        )
        gR = realized_R(direction, entry, stop, exit_px)
        fR = fee_R(entry, stop, fee_leg)
        nR = gR - fR
        tid += 1
        trades.append(
            Trade(
                trade_id=f"{ablation}-{direction}-{tid}",
                direction=direction,
                partition=parts[i],
                decision_time=htf["decision_time"].isoformat(),
                entry_time=df1h["timestamp"].iloc[entry_i].isoformat(),
                exit_time=df1h["timestamp"].iloc[exit_i].isoformat(),
                entry_price=entry,
                stop_price=stop,
                target_price=target,
                exit_reason=reason,
                hold_bars=exit_i - entry_i,
                gross_R=gR,
                fees_R=fR,
                net_R=nR,
                selected_4h_open_time=htf["selected_4h_open_time"].isoformat(),
                selected_4h_close_time=htf["selected_4h_close_time"].isoformat(),
                selected_4h_is_closed=True,
                lookahead_status="PASS",
                ambiguity=amb,
                ablation=ablation,
                pullback_low=pullback_low,
                pullback_high=pullback_high,
            )
        )
        next_allowed = exit_i + cooldown

    return trades, counts


def trade_ledger_row(t: Trade) -> dict[str, Any]:
    return {
        "symbol": "BTCUSDT",
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
        "selected_4h_open_time": t.selected_4h_open_time,
        "selected_4h_close_time": t.selected_4h_close_time,
        "selected_4h_is_closed": t.selected_4h_is_closed,
        "lookahead_status": t.lookahead_status,
        "ambiguity": t.ambiguity,
        "ablation": t.ablation,
    }


def main() -> None:
    eng = load_s3_helpers()
    pre = CFG["predeclared"]

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
                "- note: S1 sandbox-only; independent of COMBO_02; no production imports for signals.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    feature_def = {
        "declared_before_oos": True,
        "differs_from_production_structure_logic": True,
        "independent_of_COMBO_02": True,
        "htf_trend": {
            "definition": (
                "On each fully closed 4h candle: EMA(50) of 4h close. "
                "BULL if close>EMA50 and EMA50>EMA50_prev; "
                "BEAR if close<EMA50 and EMA50<EMA50_prev; else NEUTRAL. "
                "Mapped to 1h decision via selected_4h_close_time <= decision_time."
            ),
            "ema_period": pre["htf_ema_period"],
        },
        "setup_pullback": {
            "definition": (
                f"Window of prior {pre['pullback_window_bars']} completed 1h bars. "
                "LONG: 4h BULL AND at least one window low <= EMA(20) at that bar "
                "AND decision close > window max high AND decision close > EMA(20). "
                "SHORT: 4h BEAR AND at least one window high >= EMA(20) "
                "AND decision close < window min low AND decision close < EMA(20)."
            ),
            "setup_ema_period": pre["setup_ema_period"],
            "pullback_window_bars": pre["pullback_window_bars"],
        },
        "continuation": {
            "LONG": "1h close breaks above max high of the completed pullback window",
            "SHORT": "1h close breaks below min low of the completed pullback window",
            "causal": True,
        },
        "entry": {"mode": "E1", "fill": "confirmation bar close"},
        "stop": {
            "LONG": "pullback_window min low - stop_buffer_frac * close",
            "SHORT": "pullback_window max high + stop_buffer_frac * close",
            "stop_buffer_frac": pre["stop_buffer_frac"],
        },
        "target": {"R": pre["target_R"]},
        "fees": {
            "taker_per_leg": pre["fee_taker_per_leg"],
            "legs": 2,
            "conversion": "fees_R = 2 * fee * entry / |entry-stop|",
        },
        "slippage": CFG["slippage"],
        "funding": CFG["funding"],
        "intrabar_ambiguity": pre["intrabar_ambiguity"],
        "ablations": CFG["ablations"],
    }
    (SANDBOX / "feature_definition.json").write_text(json.dumps(feature_def, indent=2), encoding="utf-8")

    df1h = eng.load_ohlcv(CFG["data"]["parquet_1h"])
    df4h = eng.load_ohlcv(CFG["data"]["parquet_4h"])
    q1 = eng.validate_ohlcv(df1h, 3600, "BTCUSDT_1h")
    q4 = eng.validate_ohlcv(df4h, 14400, "BTCUSDT_4h")
    man1 = json.loads((REPO / CFG["data"]["manifest_1h"]).read_text(encoding="utf-8"))
    man4 = json.loads((REPO / CFG["data"]["manifest_4h"]).read_text(encoding="utf-8"))
    overall = "DATA_OK"
    if q1["status"] != "DATA_OK" or q4["status"] != "DATA_OK":
        overall = "DATA_CONTAMINATED" if "CONTAMINATED" in (q1["status"], q4["status"]) else "DATA_WARN"

    inventory = {
        "symbol": "BTCUSDT",
        "timezone": "UTC",
        "overall_quality": overall,
        "datasets": [
            {
                "timeframe": "1h",
                "source_file": CFG["data"]["parquet_1h"],
                "manifest": CFG["data"]["manifest_1h"],
                "first_timestamp": q1["first_timestamp"],
                "last_timestamp": q1["last_timestamp"],
                "row_count": q1["row_count"],
                "missing_bars": q1["gap_count"],
                "duplicate_bars": q1["duplicate_bars"],
                "out_of_order_rows": q1["out_of_order_bars"],
                "invalid_ohlc_rows": q1["invalid_ohlc_rows"],
                "dataset_fingerprint": man1.get("sha256"),
                "quality": q1,
            },
            {
                "timeframe": "4h",
                "source_file": CFG["data"]["parquet_4h"],
                "manifest": CFG["data"]["manifest_4h"],
                "first_timestamp": q4["first_timestamp"],
                "last_timestamp": q4["last_timestamp"],
                "row_count": q4["row_count"],
                "missing_bars": q4["gap_count"],
                "duplicate_bars": q4["duplicate_bars"],
                "out_of_order_rows": q4["out_of_order_bars"],
                "invalid_ohlc_rows": q4["invalid_ohlc_rows"],
                "dataset_fingerprint": man4.get("sha256"),
                "quality": q4,
            },
        ],
    }
    (SANDBOX / "data_inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    (SANDBOX / "data_quality_report.json").write_text(
        json.dumps({"overall": overall, "1h": q1, "4h": q4}, indent=2), encoding="utf-8"
    )
    if overall == "DATA_CONTAMINATED":
        raise RuntimeError("DATA_CONTAMINATED")

    htf_rows, pit = eng.build_closed_4h_map(df1h, df4h)
    unfinished = sum(1 for r in htf_rows if r["lookahead_status"] == "FAIL")
    pit_out = {
        "decision_rows": len(df1h),
        "candidate_rows": pit["candidate_rows"],
        "lookahead_passes": pit["lookahead_pass_rows"],
        "lookahead_failures": pit["lookahead_fail_rows"],
        "future_feature_violations": pit["future_feature_violations"],
        "missing_4h_mappings": pit["missing_htf_rows"],
        "unfinished_4h_mappings": unfinished,
    }
    if (
        pit_out["lookahead_failures"]
        or pit_out["future_feature_violations"]
        or pit_out["unfinished_4h_mappings"]
    ):
        raise RuntimeError(f"STOP — LOOKAHEAD VIOLATION: {pit_out}")

    parts = eng.assign_partitions(
        len(df1h), float(CFG["partitions"]["train_frac"]), float(CFG["partitions"]["validation_frac"])
    )
    df4h_trend = build_4h_trend_table(df4h, int(pre["htf_ema_period"]))
    ema20 = ema(df1h["close"], int(pre["setup_ema_period"]))
    atr = eng.wilder_atr(df1h["high"], df1h["low"], df1h["close"], int(pre["atr_period"]))
    atr_pct = eng.atr_percentile_trailing(atr, 100)

    base_kw = dict(
        pullback_window=int(pre["pullback_window_bars"]),
        stop_buffer_frac=float(pre["stop_buffer_frac"]),
        target_R=float(pre["target_R"]),
        fee_leg=float(pre["fee_taker_per_leg"]),
        max_hold=int(pre["max_hold_bars"]),
        cooldown=int(pre["cooldown_bars_after_exit"]),
    )

    trades_a0, counts_a0 = run_s1(
        df1h,
        htf_rows,
        df4h_trend,
        ema20,
        atr,
        atr_pct,
        parts,
        ablation="A0",
        vol_gate_pct=None,
        **base_kw,
    )
    trades_a1, counts_a1 = run_s1(
        df1h,
        htf_rows,
        df4h_trend,
        ema20,
        atr,
        atr_pct,
        parts,
        ablation="A1",
        vol_gate_pct=50.0,
        **base_kw,
    )

    longs = [t for t in trades_a0 if t.direction == "LONG"]
    shorts = [t for t in trades_a0 if t.direction == "SHORT"]

    # PIT audit from trades
    audit = []
    for t in trades_a0:
        audit.append(
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
                "net_R": t.net_R,
            }
        )
    write_csv(
        SANDBOX / "point_in_time_audit.csv",
        audit,
        list(audit[0].keys()) if audit else ["decision_time"],
    )
    (SANDBOX / "point_in_time_summary.json").write_text(
        json.dumps({**pit_out, "trade_rows": len(trades_a0)}, indent=2), encoding="utf-8"
    )

    ledger_fields = [
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
        "ambiguity",
        "ablation",
    ]
    write_csv(SANDBOX / "s1_long_trade_ledger.csv", [trade_ledger_row(t) for t in longs], ledger_fields)
    write_csv(SANDBOX / "s1_short_trade_ledger.csv", [trade_ledger_row(t) for t in shorts], ledger_fields)

    # Reconciliation
    m_all = metrics(trades_a0)
    m_long = metrics(longs)
    m_short = metrics(shorts)
    recon = {
        "unique_ids": len({t.trade_id for t in trades_a0}) == len(trades_a0),
        "long_plus_short": len(longs) + len(shorts) == len(trades_a0),
        "net_R": abs((m_long["net_R"] + m_short["net_R"]) - m_all["net_R"]) < 1e-9,
        "fees_R": abs((m_long["fees_R"] + m_short["fees_R"]) - m_all["fees_R"]) < 1e-9,
        "wins": m_long["wins"] + m_short["wins"] == m_all["wins"],
        "losses": m_long["losses"] + m_short["losses"] == m_all["losses"],
        "all_lookahead_pass": all(t.lookahead_status == "PASS" for t in trades_a0),
    }
    if not all(recon.values()):
        raise RuntimeError(f"RECONCILIATION FAILED: {recon}")

    # Partition summary
    part_rows = []
    bounds = {}
    for name in ("TRAIN", "VALIDATION", "OOS"):
        idxs = [i for i, p in enumerate(parts) if p == name]
        bounds[name] = {
            "start": df1h["timestamp"].iloc[idxs[0]].isoformat(),
            "end": df1h["timestamp"].iloc[idxs[-1]].isoformat(),
            "1h_rows": len(idxs),
        }
    for direction, subset_all in (
        ("COMBINED", trades_a0),
        ("LONG", longs),
        ("SHORT", shorts),
    ):
        for part in ("TRAIN", "VALIDATION", "OOS"):
            subset = [t for t in subset_all if t.partition == part]
            m = metrics(subset)
            part_rows.append(
                {
                    "variant": f"S1_{direction}",
                    "partition": part,
                    "partition_start": bounds[part]["start"],
                    "partition_end": bounds[part]["end"],
                    "partition_1h_rows": bounds[part]["1h_rows"],
                    "candidate_count": counts_a0["candidate_count"] if direction == "COMBINED" else (
                        counts_a0["long_candidates"] if direction == "LONG" else counts_a0["short_candidates"]
                    ),
                    **{k: m[k] for k in m},
                }
            )
    write_csv(SANDBOX / "partition_summary.csv", part_rows, list(part_rows[0].keys()))

    # Ablation summary
    abl_rows = []
    for name, tr, cnt, note in (
        ("A0", trades_a0, counts_a0, CFG["ablations"]["A0"]),
        ("A1", trades_a1, counts_a1, CFG["ablations"]["A1"]),
        ("A2", [], {}, CFG["ablations"]["A2"]),
    ):
        if name == "A2":
            abl_rows.append(
                {
                    "ablation": "A2",
                    "note": note,
                    "trade_count": None,
                    "average_R": None,
                    "net_R": None,
                    "profit_factor": None,
                    "maximum_drawdown_R": None,
                    "OOS_trade_count": None,
                    "OOS_average_R": None,
                    "OOS_net_R": None,
                    "sample_size_label": "UNAVAILABLE WITHOUT ENGINE CHANGE",
                }
            )
            continue
        m = metrics(tr)
        mo = metrics([t for t in tr if t.partition == "OOS"])
        abl_rows.append(
            {
                "ablation": name,
                "note": note,
                "trade_count": m["trade_count"],
                "average_R": m["average_R"],
                "net_R": m["net_R"],
                "profit_factor": m["profit_factor"],
                "maximum_drawdown_R": m["maximum_drawdown_R"],
                "OOS_trade_count": mo["trade_count"],
                "OOS_average_R": mo["average_R"],
                "OOS_net_R": mo["net_R"],
                "sample_size_label": m["sample_size_label"],
                "candidate_count": cnt.get("candidate_count"),
            }
        )
    write_csv(SANDBOX / "ablation_summary.csv", abl_rows, list(abl_rows[0].keys()))

    # Sensitivity only if OOS trades >= 20
    m_oos = metrics([t for t in trades_a0 if t.partition == "OOS"])
    sens_rows = []
    if m_oos["trade_count"] >= int(CFG["sensitivity"]["min_oos_trades"]):
        for w in CFG["sensitivity"]["pullback_windows"]:
            kw = dict(base_kw)
            kw["pullback_window"] = int(w)
            tr, _ = run_s1(
                df1h, htf_rows, df4h_trend, ema20, atr, atr_pct, parts,
                ablation="SENS", vol_gate_pct=None, **kw
            )
            m = metrics(tr)
            mo = metrics([t for t in tr if t.partition == "OOS"])
            sens_rows.append(
                {
                    "axis": "pullback_window",
                    "value": w,
                    "trade_count": m["trade_count"],
                    "average_R": m["average_R"],
                    "net_R": m["net_R"],
                    "OOS_trade_count": mo["trade_count"],
                    "OOS_average_R": mo["average_R"],
                    "sample_size_label": m["sample_size_label"],
                }
            )
        for b in CFG["sensitivity"]["stop_buffers"]:
            kw = dict(base_kw)
            kw["stop_buffer_frac"] = float(b)
            tr, _ = run_s1(
                df1h, htf_rows, df4h_trend, ema20, atr, atr_pct, parts,
                ablation="SENS", vol_gate_pct=None, **kw
            )
            m = metrics(tr)
            mo = metrics([t for t in tr if t.partition == "OOS"])
            sens_rows.append(
                {
                    "axis": "stop_buffer_frac",
                    "value": b,
                    "trade_count": m["trade_count"],
                    "average_R": m["average_R"],
                    "net_R": m["net_R"],
                    "OOS_trade_count": mo["trade_count"],
                    "OOS_average_R": mo["average_R"],
                    "sample_size_label": m["sample_size_label"],
                }
            )
        for tgt in CFG["sensitivity"]["targets_R"]:
            kw = dict(base_kw)
            kw["target_R"] = float(tgt)
            tr, _ = run_s1(
                df1h, htf_rows, df4h_trend, ema20, atr, atr_pct, parts,
                ablation="SENS", vol_gate_pct=None, **kw
            )
            m = metrics(tr)
            mo = metrics([t for t in tr if t.partition == "OOS"])
            sens_rows.append(
                {
                    "axis": "target_R",
                    "value": tgt,
                    "trade_count": m["trade_count"],
                    "average_R": m["average_R"],
                    "net_R": m["net_R"],
                    "OOS_trade_count": mo["trade_count"],
                    "OOS_average_R": mo["average_R"],
                    "sample_size_label": m["sample_size_label"],
                }
            )
        oos_avgs = [r["OOS_average_R"] for r in sens_rows if r["axis"] == "pullback_window" and r["OOS_average_R"] is not None]
        if len(oos_avgs) >= 2:
            signs = {1 if x > 0 else (-1 if x < 0 else 0) for x in oos_avgs}
            spread = max(oos_avgs) - min(oos_avgs)
            stability = "STABLE" if len(signs) == 1 and spread < 0.5 else ("SENSITIVE" if len(signs) == 1 else "UNSTABLE")
        else:
            stability = "INSUFFICIENT_SAMPLE"
        for r in sens_rows:
            r["stability_class"] = stability
    else:
        sens_rows.append(
            {
                "axis": "skipped",
                "value": None,
                "trade_count": m_all["trade_count"],
                "average_R": m_all["average_R"],
                "net_R": m_all["net_R"],
                "OOS_trade_count": m_oos["trade_count"],
                "OOS_average_R": m_oos["average_R"],
                "sample_size_label": sample_label(m_oos["trade_count"]),
                "stability_class": "INSUFFICIENT_SAMPLE",
            }
        )
    write_csv(SANDBOX / "sensitivity_summary.csv", sens_rows, list(sens_rows[0].keys()))

    # Verdict
    m_oos_l = metrics([t for t in longs if t.partition == "OOS"])
    m_oos_s = metrics([t for t in shorts if t.partition == "OOS"])
    if pit_out["lookahead_failures"] or pit_out["future_feature_violations"]:
        verdict = "LOOKAHEAD RISK"
    elif overall == "DATA_CONTAMINATED":
        verdict = "DATA CONTAMINATED"
    elif m_oos["trade_count"] < 20 and m_all["trade_count"] < 20:
        verdict = "INSUFFICIENT SAMPLE"
    elif m_oos["trade_count"] < 20 or (m_oos["average_R"] is not None and m_oos["average_R"] <= 0):
        verdict = "EXPLORATORY ONLY"
    elif sens_rows[0].get("stability_class") == "UNSTABLE":
        verdict = "UNSTABLE"
    elif sens_rows[0].get("stability_class") == "SENSITIVE":
        verdict = "SENSITIVE"
    elif m_all["trade_count"] >= 50 and m_oos["trade_count"] >= 20 and m_oos["average_R"] > 0:
        verdict = "RESEARCH CANDIDATE"
    else:
        verdict = "EXPLORATORY ONLY"

    summary = {
        "verdict": verdict,
        "pit": pit_out,
        "counts_a0": counts_a0,
        "combined": m_all,
        "long": m_long,
        "short": m_short,
        "oos_combined": m_oos,
        "oos_long": m_oos_l,
        "oos_short": m_oos_s,
        "partitions": bounds,
        "reconciliation": recon,
        "sensitivity_class": sens_rows[0].get("stability_class"),
    }
    (SANDBOX / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    lines = [
        "# S1 HTF Trend Pullback — Research Report",
        "",
        f"Generated: {utc_now()}",
        "Independent of COMBO_02. Sandbox-only. Not a profitability claim.",
        "",
        f"## Verdict: `{verdict}`",
        "",
        f"- Combined n={m_all['trade_count']} avg_R={m_all['average_R']} OOS n={m_oos['trade_count']} OOS avg_R={m_oos['average_R']}",
        f"- LONG n={m_long['trade_count']} OOS n={m_oos_l['trade_count']} OOS avg_R={m_oos_l['average_R']}",
        f"- SHORT n={m_short['trade_count']} OOS n={m_oos_s['trade_count']} OOS avg_R={m_oos_s['average_R']}",
        "",
        "## PIT",
        "",
        json.dumps(pit_out, indent=2),
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
                "verdict": verdict,
                "combined_n": m_all["trade_count"],
                "oos_n": m_oos["trade_count"],
                "oos_avg_R": m_oos["average_R"],
                "long_oos": {"n": m_oos_l["trade_count"], "avg_R": m_oos_l["average_R"]},
                "short_oos": {"n": m_oos_s["trade_count"], "avg_R": m_oos_s["average_R"]},
                "pit": pit_out,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
