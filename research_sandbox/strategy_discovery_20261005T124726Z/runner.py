#!/usr/bin/env python3
"""S3 Compression Breakout — isolated sandbox research runner.

Independent of COMBO_02 / production engines.
Reads existing Parquet only. Writes only inside this sandbox directory.
Does not create paper/live trades or modify any existing repo files.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SANDBOX = Path(__file__).resolve().parent
REPO = SANDBOX.parents[1]
CFG_PATH = SANDBOX / "config.json"


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


def load_cfg() -> dict[str, Any]:
    return json.loads(CFG_PATH.read_text(encoding="utf-8"))


def load_ohlcv(rel: str) -> pd.DataFrame:
    path = REPO / rel
    df = pd.read_parquet(path)
    if "timestamp" not in df.columns:
        raise RuntimeError(f"missing timestamp column: {path}")
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def validate_ohlcv(df: pd.DataFrame, step_s: int, name: str) -> dict[str, Any]:
    ts = df["timestamp"]
    dups = int(ts.duplicated().sum())
    ordered = bool(ts.is_monotonic_increasing)
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    bad_ohlc = int(((h < l) | (h < o) | (h < c) | (l > o) | (l > c) | o.isna() | h.isna() | l.isna() | c.isna()).sum())
    deltas = ts.diff().dt.total_seconds().iloc[1:]
    gap_mask = deltas != float(step_s)
    gap_count = int(gap_mask.sum())
    max_gap_bars = 0
    if gap_count:
        # gaps measured in missing steps
        max_gap_bars = int(((deltas[gap_mask] / step_s) - 1).max()) if len(deltas[gap_mask]) else 0
    tz_utc = bool(getattr(ts.dt, "tz", None) is not None)
    status = "DATA_OK"
    if dups or bad_ohlc or not ordered:
        status = "DATA_CONTAMINATED"
    elif gap_count:
        status = "DATA_WARN" if gap_count < 5 else "DATA_CONTAMINATED"
    return {
        "name": name,
        "row_count": int(len(df)),
        "first_timestamp": ts.iloc[0].isoformat() if len(df) else None,
        "last_timestamp": ts.iloc[-1].isoformat() if len(df) else None,
        "timezone": "UTC",
        "tz_aware_utc": tz_utc,
        "ordered": ordered,
        "duplicate_bars": dups,
        "out_of_order_bars": 0 if ordered else 1,
        "invalid_ohlc_rows": bad_ohlc,
        "expected_step_seconds": step_s,
        "gap_count": gap_count,
        "max_gap_bars": max_gap_bars,
        "status": status,
    }


def wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return atr


def atr_percentile_trailing(atr: pd.Series, lookback: int) -> pd.Series:
    # Point-in-time: percentile of current ATR among trailing window ending at i.
    out = pd.Series(index=atr.index, dtype="float64")
    vals = atr.to_numpy()
    for i in range(len(vals)):
        if i + 1 < lookback or math.isnan(vals[i]):
            out.iloc[i] = float("nan")
            continue
        window = vals[i + 1 - lookback : i + 1]
        if any(math.isnan(x) for x in window):
            out.iloc[i] = float("nan")
            continue
        # percentile rank of last value in window
        last = window[-1]
        rank = sum(1 for x in window if x <= last)
        out.iloc[i] = 100.0 * rank / len(window)
    return out


def build_closed_4h_map(df1h: pd.DataFrame, df4h: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """For each 1h bar, map latest fully closed 4h candle.

    decision_time = 1h open + 1h = 1h close time
    selected_4h_close_time <= decision_time
    """
    open_1h = df1h["timestamp"]
    decision = open_1h + pd.Timedelta(hours=1)
    open_4h = df4h["timestamp"]
    close_4h = open_4h + pd.Timedelta(hours=4)

    # searchsorted on close times
    close_arr = close_4h.to_numpy()
    rows: list[dict[str, Any]] = []
    stats = {
        "candidate_rows": 0,
        "lookahead_pass_rows": 0,
        "lookahead_fail_rows": 0,
        "missing_htf_rows": 0,
        "future_feature_violations": 0,
    }
    for i in range(len(df1h)):
        dtime = decision.iloc[i]
        # rightmost close_4h <= dtime
        idx = close_4h.searchsorted(dtime, side="right") - 1
        stats["candidate_rows"] += 1
        if idx < 0:
            stats["missing_htf_rows"] += 1
            rows.append(
                {
                    "i": i,
                    "decision_time": dtime,
                    "selected_4h_open_time": None,
                    "selected_4h_close_time": None,
                    "selected_4h_is_closed": False,
                    "selected_4h_open": None,
                    "selected_4h_close": None,
                    "lookahead_status": "MISSING_HTF",
                }
            )
            continue
        sel_open = open_4h.iloc[idx]
        sel_close = close_4h.iloc[idx]
        is_closed = bool(sel_close <= dtime)
        lookahead = "PASS" if is_closed else "FAIL"
        if lookahead == "PASS":
            stats["lookahead_pass_rows"] += 1
        else:
            stats["lookahead_fail_rows"] += 1
            stats["future_feature_violations"] += 1
        rows.append(
            {
                "i": i,
                "decision_time": dtime,
                "selected_4h_open_time": sel_open,
                "selected_4h_close_time": sel_close,
                "selected_4h_is_closed": is_closed,
                "selected_4h_open": float(df4h["open"].iloc[idx]),
                "selected_4h_close": float(df4h["close"].iloc[idx]),
                "lookahead_status": lookahead,
            }
        )
    return rows, stats


@dataclass
class Trade:
    trade_id: str
    direction: str
    decision_i: int
    decision_time: str
    entry_mode: str
    entry_i: int
    entry_time: str
    entry_price: float
    stop_price: float
    target_price: float
    stop_kind: str
    target_R: float
    ablation: str
    compression_state: str
    range_high: float
    range_low: float
    breakout_level: float
    selected_4h_open_time: str
    selected_4h_close_time: str
    selected_4h_is_closed: bool
    lookahead_status: str
    exit_i: int | None = None
    exit_time: str | None = None
    exit_reason: str | None = None
    exit_price: float | None = None
    gross_R: float | None = None
    fees_R: float | None = None
    net_R: float | None = None
    hold_bars: int | None = None
    ambiguity: str = "NONE"
    partition: str = ""


def fee_R(entry: float, stop: float, fee_rate_leg: float) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return float("nan")
    # two legs taker on notional; express as fraction of R: (2 * fee_rate * entry) / risk
    return (2.0 * fee_rate_leg * entry) / risk


def simulate_exit(
    df: pd.DataFrame,
    entry_i: int,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    max_hold: int,
) -> tuple[int, float, str, str]:
    """Return exit_i, exit_price, exit_reason, ambiguity."""
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
    # time exit at last close
    px = float(df["close"].iloc[end])
    return end, px, "TIME", "NONE"


def realized_R(direction: str, entry: float, stop: float, exit_px: float) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return float("nan")
    if direction == "LONG":
        return (exit_px - entry) / risk
    return (entry - exit_px) / risk


def metrics_from_trades(trades: list[Trade]) -> dict[str, Any]:
    closed = [t for t in trades if t.net_R is not None]
    n = len(closed)
    if n == 0:
        return {
            "trade_count": 0,
            "long_trade_count": 0,
            "short_trade_count": 0,
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
    # drawdown on cumulative net R
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    max_streak = 0
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
    med = float(pd.Series(nets).median())
    return {
        "trade_count": n,
        "long_trade_count": sum(1 for t in closed if t.direction == "LONG"),
        "short_trade_count": sum(1 for t in closed if t.direction == "SHORT"),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / n if n else None,
        "gross_R": float(sum(gross)),
        "fees_R": float(sum(fees)),
        "net_R": float(sum(nets)),
        "average_R": float(sum(nets) / n),
        "median_R": med,
        "profit_factor": None if pf is None else (None if math.isinf(pf) else float(pf)),
        "maximum_drawdown_R": float(max_dd),
        "longest_losing_streak": int(max_streak),
        "average_hold_bars": float(sum(holds) / len(holds)) if holds else None,
        "sample_size_label": sample_label(n),
    }


def assign_partitions(n: int, train_frac: float, val_frac: float) -> list[str]:
    i_train = int(n * train_frac)
    i_val = int(n * (train_frac + val_frac))
    parts = []
    for i in range(n):
        if i < i_train:
            parts.append("TRAIN")
        elif i < i_val:
            parts.append("VALIDATION")
        else:
            parts.append("OOS")
    return parts


def find_retest(
    df: pd.DataFrame,
    break_i: int,
    direction: str,
    level: float,
    max_bars: int,
) -> int | None:
    """Return bar index where retest confirmation occurs, or None.

    After breakout bar, within max_bars: a bar that touches level, then a later
    (or same) bar that closes back in breakout direction. Uses only bars after breakout.
    """
    n = len(df)
    end = min(n - 1, break_i + max_bars)
    touched = False
    for j in range(break_i + 1, end + 1):
        hi = float(df["high"].iloc[j])
        lo = float(df["low"].iloc[j])
        cl = float(df["close"].iloc[j])
        if direction == "LONG":
            if lo <= level:
                touched = True
            if touched and cl > level:
                return j
        else:
            if hi >= level:
                touched = True
            if touched and cl < level:
                return j
    return None


def run_strategy(
    df1h: pd.DataFrame,
    htf_rows: list[dict[str, Any]],
    atr: pd.Series,
    atr_pct: pd.Series,
    parts: list[str],
    cfg: dict[str, Any],
    *,
    compression_threshold: float,
    range_lookback: int,
    breakout_buffer_frac: float,
    entry_mode: str,
    stop_kind: str,
    target_R: float,
    ablation: str,
    atr_stop_mult: float,
    fee_leg: float,
    max_hold: int,
    cooldown: int,
    max_retest_bars: int,
) -> tuple[list[Trade], dict[str, int]]:
    n = len(df1h)
    trades: list[Trade] = []
    counts = {
        "compression_signal_count": 0,
        "breakout_signal_count": 0,
        "retest_signal_count": 0,
        "missing_htf_skipped": 0,
        "lookahead_fail_skipped": 0,
    }
    next_allowed = 0
    tid = 0

    for i in range(n):
        if i < next_allowed:
            continue
        # Need range lookback completed bars before decision bar
        if i < range_lookback:
            continue
        if i < cfg["predeclared_features"]["atr_percentile_lookback"]:
            continue
        ap = atr_pct.iloc[i]
        if math.isnan(ap) or math.isnan(atr.iloc[i]):
            continue
        compressed = ap <= compression_threshold
        if not compressed:
            continue
        counts["compression_signal_count"] += 1

        htf = htf_rows[i]
        if htf["lookahead_status"] == "MISSING_HTF":
            counts["missing_htf_skipped"] += 1
            continue
        if htf["lookahead_status"] != "PASS" or not htf["selected_4h_is_closed"]:
            counts["lookahead_fail_skipped"] += 1
            # Hard stop later if any FAIL rows used; we never use FAIL rows.
            continue

        # Range from completed bars [i-range_lookback, i) — excludes decision bar
        sl = slice(i - range_lookback, i)
        range_high = float(df1h["high"].iloc[sl].max())
        range_low = float(df1h["low"].iloc[sl].min())
        close = float(df1h["close"].iloc[i])
        buf = breakout_buffer_frac * close
        long_bo = close > range_high + buf
        short_bo = close < range_low - buf
        if not long_bo and not short_bo:
            continue
        direction = "LONG" if long_bo else "SHORT"
        # If both (rare), skip ambiguous
        if long_bo and short_bo:
            continue
        counts["breakout_signal_count"] += 1
        breakout_level = range_high + buf if direction == "LONG" else range_low - buf

        # Ablation A1: closed 4h directional context
        if ablation == "A1":
            o4 = htf["selected_4h_open"]
            c4 = htf["selected_4h_close"]
            if o4 is None or c4 is None:
                continue
            if direction == "LONG" and not (c4 > o4):
                continue
            if direction == "SHORT" and not (c4 < o4):
                continue

        signal_i = i
        if ablation == "A3":
            rt = find_retest(df1h, i, direction, breakout_level, max_retest_bars)
            if rt is None:
                continue
            counts["retest_signal_count"] += 1
            signal_i = rt

        # Entry
        if entry_mode == "E1":
            entry_i = signal_i
            entry_price = float(df1h["close"].iloc[entry_i])
        elif entry_mode == "E2":
            if signal_i + 1 >= n:
                continue
            entry_i = signal_i + 1
            entry_price = float(df1h["open"].iloc[entry_i])
        else:
            raise RuntimeError(f"unknown entry_mode {entry_mode}")

        # Stops / targets at entry (using signal-time range / ATR at signal bar)
        atr_sig = float(atr.iloc[signal_i])
        if stop_kind == "S1":
            stop_price = range_low if direction == "LONG" else range_high
        elif stop_kind == "S2":
            stop_price = (
                entry_price - atr_stop_mult * atr_sig
                if direction == "LONG"
                else entry_price + atr_stop_mult * atr_sig
            )
        else:
            raise RuntimeError(f"unknown stop {stop_kind}")

        risk = abs(entry_price - stop_price)
        if risk <= 0 or risk / entry_price < 1e-6:
            continue
        if direction == "LONG":
            target_price = entry_price + target_R * risk
            if stop_price >= entry_price:
                continue
        else:
            target_price = entry_price - target_R * risk
            if stop_price <= entry_price:
                continue

        # Point-in-time assertions for this candidate
        assert htf["selected_4h_close_time"] <= htf["decision_time"]
        assert htf["selected_4h_is_closed"] is True
        assert htf["lookahead_status"] == "PASS"

        exit_i, exit_px, reason, amb = simulate_exit(
            df1h, entry_i, direction, entry_price, stop_price, target_price, max_hold
        )
        gR = realized_R(direction, entry_price, stop_price, exit_px)
        fR = fee_R(entry_price, stop_price, fee_leg)
        nR = gR - fR
        tid += 1
        t = Trade(
            trade_id=f"{ablation}-{entry_mode}-{stop_kind}-T{target_R}-{tid}",
            direction=direction,
            decision_i=signal_i,
            decision_time=htf_rows[signal_i]["decision_time"].isoformat(),
            entry_mode=entry_mode,
            entry_i=entry_i,
            entry_time=(df1h["timestamp"].iloc[entry_i] + pd.Timedelta(hours=0)).isoformat(),
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            stop_kind=stop_kind,
            target_R=target_R,
            ablation=ablation,
            compression_state="COMPRESSED",
            range_high=range_high,
            range_low=range_low,
            breakout_level=breakout_level,
            selected_4h_open_time=htf["selected_4h_open_time"].isoformat(),
            selected_4h_close_time=htf["selected_4h_close_time"].isoformat(),
            selected_4h_is_closed=True,
            lookahead_status="PASS",
            exit_i=exit_i,
            exit_time=(df1h["timestamp"].iloc[exit_i]).isoformat(),
            exit_reason=reason,
            exit_price=exit_px,
            gross_R=gR,
            fees_R=fR,
            net_R=nR,
            hold_bars=exit_i - entry_i,
            ambiguity=amb,
            partition=parts[signal_i],
        )
        # For E1 entry_time should reflect decision close; store open timestamp of entry bar
        t.entry_time = df1h["timestamp"].iloc[entry_i].isoformat()
        trades.append(t)
        next_allowed = exit_i + cooldown

    return trades, counts


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fieldnames})


def partition_stats(
    df1h: pd.DataFrame,
    parts: list[str],
    atr_pct: pd.Series,
    compression_threshold: float,
    trades: list[Trade],
    htf_rows: list[dict[str, Any]],
    df4h: pd.DataFrame,
) -> list[dict[str, Any]]:
    out = []
    for name in ("TRAIN", "VALIDATION", "OOS"):
        idxs = [i for i, p in enumerate(parts) if p == name]
        if not idxs:
            continue
        start_i, end_i = idxs[0], idxs[-1]
        start = df1h["timestamp"].iloc[start_i]
        end = df1h["timestamp"].iloc[end_i]
        # 4h rows overlapping [start, end+1h]
        end_dec = end + pd.Timedelta(hours=1)
        n4 = int(((df4h["timestamp"] >= start) & (df4h["timestamp"] <= end)).sum())
        comp = 0
        for i in idxs:
            ap = atr_pct.iloc[i]
            if not math.isnan(ap) and ap <= compression_threshold:
                # only count when HTF available/pass for fairness
                if htf_rows[i]["lookahead_status"] == "PASS":
                    comp += 1
        pt = [t for t in trades if t.partition == name]
        m = metrics_from_trades(pt)
        bo = len(pt)  # executed breakouts as trades; signals subset
        out.append(
            {
                "partition": name,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "1h_rows": len(idxs),
                "4h_rows": n4,
                "compression_bars": comp,
                "breakout_candidates": m["trade_count"],
                "long_trades": m["long_trade_count"],
                "short_trades": m["short_trade_count"],
                "wins": m["wins"],
                "losses": m["losses"],
                "net_R": m["net_R"],
                "average_R": m["average_R"],
                "profit_factor": m["profit_factor"],
                "maximum_drawdown_R": m["maximum_drawdown_R"],
                "longest_losing_streak": m["longest_losing_streak"],
                "sample_size_label": m["sample_size_label"],
                "gross_R": m["gross_R"],
                "fees_R": m["fees_R"],
                "win_rate": m["win_rate"],
                "median_R": m["median_R"],
                "average_hold_bars": m["average_hold_bars"],
            }
        )
    return out


def main() -> None:
    cfg = load_cfg()
    feat = cfg["predeclared_features"]
    fee_leg = float(cfg["fees"]["taker_fee_rate_per_leg"])

    # --- safety report ---
    safety = {
        "generated_at": utc_now(),
        "branch": "main",
        "sandbox": str(SANDBOX),
        "Existing code modified": "NO",
        "Existing configs modified": "NO",
        "Existing DB data modified": "NO",
        "Existing strategy modified": "NO",
        "Existing reports overwritten": "NO",
        "Live trading affected": "NO",
        "Paper trading affected": "NO",
        "Dependencies changed": "NO",
        "Migrations run": "NO",
        "note": "Sandbox-only runner. No production imports. Dirty git tree treated as protected.",
    }
    (SANDBOX / "repository_safety_report.md").write_text(
        "# Repository safety report\n\n"
        + "\n".join(f"- **{k}:** {v}" for k, v in safety.items())
        + "\n",
        encoding="utf-8",
    )

    # --- load data ---
    df1h = load_ohlcv(cfg["data"]["parquet_1h"])
    df4h = load_ohlcv(cfg["data"]["parquet_4h"])
    q1 = validate_ohlcv(df1h, 3600, "BTCUSDT_1h")
    q4 = validate_ohlcv(df4h, 14400, "BTCUSDT_4h")
    overlap = bool(df1h["timestamp"].iloc[0] <= df4h["timestamp"].iloc[-1] and df4h["timestamp"].iloc[0] <= df1h["timestamp"].iloc[-1])
    overall = "DATA_OK"
    if q1["status"] == "DATA_CONTAMINATED" or q4["status"] == "DATA_CONTAMINATED" or not overlap:
        overall = "DATA_CONTAMINATED"
    elif q1["status"] == "DATA_WARN" or q4["status"] == "DATA_WARN":
        overall = "DATA_WARN"

    man1 = json.loads((REPO / cfg["data"]["manifest_1h"]).read_text(encoding="utf-8"))
    man4 = json.loads((REPO / cfg["data"]["manifest_4h"]).read_text(encoding="utf-8"))

    inventory = {
        "symbol": "BTCUSDT",
        "timezone": "UTC",
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
                "out_of_order_bars": q1["out_of_order_bars"],
                "invalid_ohlc_rows": q1["invalid_ohlc_rows"],
                "dataset_fingerprint": man1.get("sha256"),
                "manifest_validation": man1.get("validation_status"),
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
                "out_of_order_bars": q4["out_of_order_bars"],
                "invalid_ohlc_rows": q4["invalid_ohlc_rows"],
                "dataset_fingerprint": man4.get("sha256"),
                "manifest_validation": man4.get("validation_status"),
                "quality": q4,
            },
        ],
        "windows_overlap": overlap,
        "overall_quality": overall,
    }
    (SANDBOX / "data_inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    (SANDBOX / "data_quality_report.json").write_text(
        json.dumps({"overall": overall, "1h": q1, "4h": q4, "overlap": overlap}, indent=2),
        encoding="utf-8",
    )
    (SANDBOX / "feature_definition.json").write_text(
        json.dumps(
            {
                "compression": feat,
                "range": {
                    "definition": "max(high)/min(low) over prior completed range_lookback bars excluding decision bar",
                    "range_lookback_bars": feat["range_lookback_bars"],
                },
                "breakout": {
                    "long": "close > range_high + breakout_buffer_frac * close",
                    "short": "close < range_low - breakout_buffer_frac * close",
                    "breakout_buffer_frac": feat["breakout_buffer_frac"],
                },
                "htf": {
                    "rule": "selected_4h_close_time <= decision_time (1h open + 1h)",
                    "forming_forbidden": True,
                },
                "entry": cfg["entry_modes"],
                "stops": cfg["stops"],
                "targets_R": cfg["targets_R"],
                "fees": cfg["fees"],
                "intrabar_ambiguity_rule": cfg["intrabar_ambiguity_rule"],
                "declared_before_oos": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if overall == "DATA_CONTAMINATED":
        (SANDBOX / "research_report.md").write_text(
            "# S3 research report\n\nDATA_CONTAMINATED — performance interpretation stopped.\n",
            encoding="utf-8",
        )
        print("DATA_CONTAMINATED — stop")
        return

    # Features
    atr = wilder_atr(df1h["high"], df1h["low"], df1h["close"], int(feat["atr_period"]))
    atr_pct = atr_percentile_trailing(atr, int(feat["atr_percentile_lookback"]))
    htf_rows, pit_stats = build_closed_4h_map(df1h, df4h)

    if pit_stats["lookahead_fail_rows"] != 0 or pit_stats["future_feature_violations"] != 0:
        raise RuntimeError("STOP — LOOKAHEAD VIOLATION in HTF mapping construction")

    parts = assign_partitions(
        len(df1h),
        float(cfg["partitions"]["train_frac"]),
        float(cfg["partitions"]["validation_frac"]),
    )

    primary = cfg["primary_reporting"]
    base_kwargs = dict(
        compression_threshold=float(feat["compression_threshold"]),
        range_lookback=int(feat["range_lookback_bars"]),
        breakout_buffer_frac=float(feat["breakout_buffer_frac"]),
        entry_mode=primary["entry_mode"],
        stop_kind="S1",
        target_R=2.0,
        atr_stop_mult=float(cfg["stops"]["atr_stop_multiple"]),
        fee_leg=fee_leg,
        max_hold=int(cfg["max_hold_bars"]),
        cooldown=int(cfg["cooldown_bars_after_exit"]),
        max_retest_bars=int(cfg["retest"]["max_retest_bars"]),
    )

    # A0 primary
    trades_a0, counts_a0 = run_strategy(
        df1h, htf_rows, atr, atr_pct, parts, cfg, ablation="A0", **base_kwargs
    )
    # A1
    trades_a1, counts_a1 = run_strategy(
        df1h, htf_rows, atr, atr_pct, parts, cfg, ablation="A1", **base_kwargs
    )
    # A3 retest
    trades_a3, counts_a3 = run_strategy(
        df1h, htf_rows, atr, atr_pct, parts, cfg, ablation="A3", **base_kwargs
    )

    # E2 separate (base A0)
    kw_e2 = dict(base_kwargs)
    kw_e2["entry_mode"] = "E2"
    trades_e2, counts_e2 = run_strategy(
        df1h, htf_rows, atr, atr_pct, parts, cfg, ablation="A0", **kw_e2
    )

    # Point-in-time audit from primary A0 trades + mapping summary
    audit_rows = []
    for t in trades_a0:
        audit_rows.append(
            {
                "decision_time": t.decision_time,
                "direction": t.direction,
                "compression_state": t.compression_state,
                "range_high": t.range_high,
                "range_low": t.range_low,
                "breakout_level": t.breakout_level,
                "selected_4h_open_time": t.selected_4h_open_time,
                "selected_4h_close_time": t.selected_4h_close_time,
                "selected_4h_is_closed": t.selected_4h_is_closed,
                "entry_time": t.entry_time,
                "stop_time_or_price": t.stop_price,
                "target_price": t.target_price,
                "exit_time": t.exit_time,
                "exit_reason": t.exit_reason,
                "lookahead_status": t.lookahead_status,
                "partition": t.partition,
                "trade_id": t.trade_id,
            }
        )
    # Also store compact mapping audit counts
    write_csv(
        SANDBOX / "point_in_time_audit.csv",
        audit_rows,
        [
            "decision_time",
            "direction",
            "compression_state",
            "range_high",
            "range_low",
            "breakout_level",
            "selected_4h_open_time",
            "selected_4h_close_time",
            "selected_4h_is_closed",
            "entry_time",
            "stop_time_or_price",
            "target_price",
            "exit_time",
            "exit_reason",
            "lookahead_status",
            "partition",
            "trade_id",
        ],
    )

    # Ledger
    ledger_rows = []
    for t in trades_a0 + trades_a1 + trades_a3 + trades_e2:
        ledger_rows.append(
            {
                "trade_id": t.trade_id,
                "ablation": t.ablation,
                "entry_mode": t.entry_mode,
                "partition": t.partition,
                "direction": t.direction,
                "decision_time": t.decision_time,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "entry_price": t.entry_price,
                "stop_price": t.stop_price,
                "target_price": t.target_price,
                "stop_kind": t.stop_kind,
                "target_R": t.target_R,
                "exit_reason": t.exit_reason,
                "gross_R": t.gross_R,
                "fees_R": t.fees_R,
                "net_R": t.net_R,
                "hold_bars": t.hold_bars,
                "ambiguity": t.ambiguity,
                "lookahead_status": t.lookahead_status,
                "selected_4h_close_time": t.selected_4h_close_time,
                "range_high": t.range_high,
                "range_low": t.range_low,
                "breakout_level": t.breakout_level,
            }
        )
    write_csv(
        SANDBOX / "candidate_ledger.csv",
        ledger_rows,
        list(ledger_rows[0].keys()) if ledger_rows else ["trade_id"],
    )

    part_rows = partition_stats(
        df1h,
        parts,
        atr_pct,
        float(feat["compression_threshold"]),
        trades_a0,
        htf_rows,
        df4h,
    )
    # attach signal counts globally for A0
    for r in part_rows:
        r["compression_signal_count_global_A0"] = counts_a0["compression_signal_count"]
        r["breakout_signal_count_global_A0"] = counts_a0["breakout_signal_count"]
        r["retest_signal_count_global_A0"] = counts_a0["retest_signal_count"]
    write_csv(
        SANDBOX / "partition_summary.csv",
        part_rows,
        list(part_rows[0].keys()) if part_rows else ["partition"],
    )

    # Regime summary proxy: compressed vs not (sandbox-only)
    regime_rows = []
    for name, mask_label, pred in (
        ("COMPRESSED", "atr_pct<=threshold", lambda i: (not math.isnan(atr_pct.iloc[i])) and atr_pct.iloc[i] <= float(feat["compression_threshold"])),
        ("NOT_COMPRESSED", "atr_pct>threshold", lambda i: (not math.isnan(atr_pct.iloc[i])) and atr_pct.iloc[i] > float(feat["compression_threshold"])),
    ):
        idxs = [i for i in range(len(df1h)) if pred(i)]
        regime_rows.append(
            {
                "regime": name,
                "definition": mask_label,
                "bar_count": len(idxs),
                "percentage": 100.0 * len(idxs) / len(df1h) if len(df1h) else 0.0,
                "symbol": "BTCUSDT",
                "note": "Sandbox compression proxy — not production market_structure regime",
            }
        )
    write_csv(
        SANDBOX / "regime_summary.csv",
        regime_rows,
        ["regime", "definition", "bar_count", "percentage", "symbol", "note"],
    )

    # Ablation summary (full-sample + OOS)
    ablation_rows = []
    for name, tr, cnt, note in (
        ("A0", trades_a0, counts_a0, cfg["ablations"]["A0"]),
        ("A1", trades_a1, counts_a1, cfg["ablations"]["A1"]),
        ("A2", [], {}, cfg["ablations"]["A2"]),
        ("A3", trades_a3, counts_a3, cfg["ablations"]["A3"]),
        ("A0_E2", trades_e2, counts_e2, "A0 with entry E2 next open"),
    ):
        if name == "A2":
            ablation_rows.append(
                {
                    "ablation": "A2",
                    "note": note,
                    "trade_count": None,
                    "average_R": None,
                    "median_R": None,
                    "profit_factor": None,
                    "maximum_drawdown_R": None,
                    "longest_losing_streak": None,
                    "OOS_trade_count": None,
                    "OOS_average_R": None,
                    "OOS_net_R": None,
                    "sample_size_label": "UNAVAILABLE WITHOUT ENGINE CHANGE",
                    "lookahead_status": "N/A",
                    "compression_signal_count": None,
                    "breakout_signal_count": None,
                    "retest_signal_count": None,
                }
            )
            continue
        m_all = metrics_from_trades(tr)
        m_oos = metrics_from_trades([t for t in tr if t.partition == "OOS"])
        ablation_rows.append(
            {
                "ablation": name,
                "note": note,
                "trade_count": m_all["trade_count"],
                "average_R": m_all["average_R"],
                "median_R": m_all["median_R"],
                "profit_factor": m_all["profit_factor"],
                "maximum_drawdown_R": m_all["maximum_drawdown_R"],
                "longest_losing_streak": m_all["longest_losing_streak"],
                "OOS_trade_count": m_oos["trade_count"],
                "OOS_average_R": m_oos["average_R"],
                "OOS_net_R": m_oos["net_R"],
                "sample_size_label": m_all["sample_size_label"],
                "lookahead_status": "PASS",
                "compression_signal_count": cnt.get("compression_signal_count"),
                "breakout_signal_count": cnt.get("breakout_signal_count"),
                "retest_signal_count": cnt.get("retest_signal_count"),
            }
        )
    write_csv(
        SANDBOX / "ablation_summary.csv",
        ablation_rows,
        list(ablation_rows[0].keys()),
    )

    # Sensitivity — only if >= 20 trades and lookahead pass
    sens_rows = []
    total_n = len(trades_a0)
    if total_n >= int(cfg["sensitivity"]["min_trades_to_run"]) and pit_stats["lookahead_fail_rows"] == 0:
        # nearby compression
        for thr in cfg["sensitivity"]["compression_thresholds"]:
            kw = dict(base_kwargs)
            kw["compression_threshold"] = float(thr)
            tr, _ = run_strategy(df1h, htf_rows, atr, atr_pct, parts, cfg, ablation="A0", **kw)
            m = metrics_from_trades(tr)
            mo = metrics_from_trades([t for t in tr if t.partition == "OOS"])
            sens_rows.append(
                {
                    "axis": "compression_threshold",
                    "value": thr,
                    "trade_count": m["trade_count"],
                    "average_R": m["average_R"],
                    "net_R": m["net_R"],
                    "profit_factor": m["profit_factor"],
                    "maximum_drawdown_R": m["maximum_drawdown_R"],
                    "OOS_trade_count": mo["trade_count"],
                    "OOS_average_R": mo["average_R"],
                    "sample_size_label": m["sample_size_label"],
                }
            )
        for lb in cfg["sensitivity"]["range_lookbacks"]:
            kw = dict(base_kwargs)
            kw["range_lookback"] = int(lb)
            tr, _ = run_strategy(df1h, htf_rows, atr, atr_pct, parts, cfg, ablation="A0", **kw)
            m = metrics_from_trades(tr)
            mo = metrics_from_trades([t for t in tr if t.partition == "OOS"])
            sens_rows.append(
                {
                    "axis": "range_lookback",
                    "value": lb,
                    "trade_count": m["trade_count"],
                    "average_R": m["average_R"],
                    "net_R": m["net_R"],
                    "profit_factor": m["profit_factor"],
                    "maximum_drawdown_R": m["maximum_drawdown_R"],
                    "OOS_trade_count": mo["trade_count"],
                    "OOS_average_R": mo["average_R"],
                    "sample_size_label": m["sample_size_label"],
                }
            )
        for bf in cfg["sensitivity"]["breakout_buffers"]:
            kw = dict(base_kwargs)
            kw["breakout_buffer_frac"] = float(bf)
            tr, _ = run_strategy(df1h, htf_rows, atr, atr_pct, parts, cfg, ablation="A0", **kw)
            m = metrics_from_trades(tr)
            mo = metrics_from_trades([t for t in tr if t.partition == "OOS"])
            sens_rows.append(
                {
                    "axis": "breakout_buffer_frac",
                    "value": bf,
                    "trade_count": m["trade_count"],
                    "average_R": m["average_R"],
                    "net_R": m["net_R"],
                    "profit_factor": m["profit_factor"],
                    "maximum_drawdown_R": m["maximum_drawdown_R"],
                    "OOS_trade_count": mo["trade_count"],
                    "OOS_average_R": mo["average_R"],
                    "sample_size_label": m["sample_size_label"],
                }
            )
        for tgt in cfg["targets_R"]:
            kw = dict(base_kwargs)
            kw["target_R"] = float(tgt)
            tr, _ = run_strategy(df1h, htf_rows, atr, atr_pct, parts, cfg, ablation="A0", **kw)
            m = metrics_from_trades(tr)
            mo = metrics_from_trades([t for t in tr if t.partition == "OOS"])
            sens_rows.append(
                {
                    "axis": "target_R",
                    "value": tgt,
                    "trade_count": m["trade_count"],
                    "average_R": m["average_R"],
                    "net_R": m["net_R"],
                    "profit_factor": m["profit_factor"],
                    "maximum_drawdown_R": m["maximum_drawdown_R"],
                    "OOS_trade_count": mo["trade_count"],
                    "OOS_average_R": mo["average_R"],
                    "sample_size_label": m["sample_size_label"],
                }
            )
        for sk in ("S1", "S2"):
            kw = dict(base_kwargs)
            kw["stop_kind"] = sk
            tr, _ = run_strategy(df1h, htf_rows, atr, atr_pct, parts, cfg, ablation="A0", **kw)
            m = metrics_from_trades(tr)
            mo = metrics_from_trades([t for t in tr if t.partition == "OOS"])
            sens_rows.append(
                {
                    "axis": "stop_kind",
                    "value": sk,
                    "trade_count": m["trade_count"],
                    "average_R": m["average_R"],
                    "net_R": m["net_R"],
                    "profit_factor": m["profit_factor"],
                    "maximum_drawdown_R": m["maximum_drawdown_R"],
                    "OOS_trade_count": mo["trade_count"],
                    "OOS_average_R": mo["average_R"],
                    "sample_size_label": m["sample_size_label"],
                }
            )
        # classify stability from compression axis OOS avg_R sign consistency
        oos_avgs = [r["OOS_average_R"] for r in sens_rows if r["axis"] == "compression_threshold"]
        oos_avgs = [x for x in oos_avgs if x is not None]
        if len(oos_avgs) < 2:
            stability = "INSUFFICIENT_SAMPLE"
        else:
            signs = {1 if x > 0 else (-1 if x < 0 else 0) for x in oos_avgs}
            spreads = max(oos_avgs) - min(oos_avgs)
            if len(signs) == 1 and spreads < 0.5:
                stability = "STABLE"
            elif len(signs) == 1:
                stability = "SENSITIVE"
            else:
                stability = "UNSTABLE"
        for r in sens_rows:
            r["stability_class"] = stability
    else:
        sens_rows.append(
            {
                "axis": "skipped",
                "value": None,
                "trade_count": total_n,
                "average_R": None,
                "net_R": None,
                "profit_factor": None,
                "maximum_drawdown_R": None,
                "OOS_trade_count": None,
                "OOS_average_R": None,
                "sample_size_label": sample_label(total_n),
                "stability_class": "INSUFFICIENT_SAMPLE",
            }
        )
    write_csv(
        SANDBOX / "sensitivity_summary.csv",
        sens_rows,
        list(sens_rows[0].keys()),
    )

    # symbol summary
    m_all = metrics_from_trades(trades_a0)
    m_oos = metrics_from_trades([t for t in trades_a0 if t.partition == "OOS"])
    symbol_rows = [
        {
            "symbol": "BTCUSDT",
            "ablation": "A0",
            "entry_mode": "E1",
            "stop": "S1",
            "target_R": 2.0,
            **{f"all_{k}": v for k, v in m_all.items()},
            **{f"oos_{k}": v for k, v in m_oos.items()},
            "cross_symbol": "UNAVAILABLE IN THIS EXPERIMENT",
            "slippage": cfg["fees"]["slippage"],
            "funding": cfg["fees"]["funding"],
            "intrabar_ambiguity_rule": cfg["intrabar_ambiguity_rule"],
            "lookahead_mapping": pit_stats,
            "signal_counts": counts_a0,
        }
    ]
    # flatten nested for csv
    flat = []
    for r in symbol_rows:
        flat.append(
            {
                "symbol": r["symbol"],
                "ablation": r["ablation"],
                "entry_mode": r["entry_mode"],
                "stop": r["stop"],
                "target_R": r["target_R"],
                "all_trade_count": r["all_trade_count"],
                "all_average_R": r["all_average_R"],
                "all_net_R": r["all_net_R"],
                "all_profit_factor": r["all_profit_factor"],
                "all_maximum_drawdown_R": r["all_maximum_drawdown_R"],
                "all_sample_size_label": r["all_sample_size_label"],
                "oos_trade_count": r["oos_trade_count"],
                "oos_average_R": r["oos_average_R"],
                "oos_net_R": r["oos_net_R"],
                "oos_profit_factor": r["oos_profit_factor"],
                "oos_maximum_drawdown_R": r["oos_maximum_drawdown_R"],
                "oos_sample_size_label": r["oos_sample_size_label"],
                "cross_symbol": r["cross_symbol"],
                "slippage": r["slippage"],
                "funding": r["funding"],
            }
        )
    write_csv(SANDBOX / "symbol_summary.csv", flat, list(flat[0].keys()))

    # PIT summary sidecar
    (SANDBOX / "point_in_time_summary.json").write_text(json.dumps(pit_stats, indent=2), encoding="utf-8")

    # Verdict
    oos_n = m_oos["trade_count"]
    verdict = "INSUFFICIENT SAMPLE"
    if pit_stats["lookahead_fail_rows"] != 0:
        verdict = "LOOKAHEAD RISK"
    elif overall == "DATA_CONTAMINATED":
        verdict = "DATA CONTAMINATED"
    elif oos_n < 20 and m_all["trade_count"] < 20:
        verdict = "INSUFFICIENT SAMPLE"
    elif oos_n < 20:
        verdict = "EXPLORATORY ONLY"
    else:
        # do not promote without stability
        stab = sens_rows[0].get("stability_class", "SENSITIVE")
        if stab == "UNSTABLE":
            verdict = "UNSTABLE"
        elif stab == "SENSITIVE":
            verdict = "SENSITIVE"
        elif m_all["trade_count"] >= 50 and oos_n >= 20:
            verdict = "RESEARCH CANDIDATE"
        else:
            verdict = "EXPLORATORY ONLY"

    # Research report
    lines = [
        "# S3 Compression Breakout — Research Report",
        "",
        f"Generated: {utc_now()}",
        "Independent of COMBO_02. Sandbox-only. Not a profitability claim.",
        "",
        "## Safety",
        "",
        "```text",
        "Existing code modified: NO",
        "Existing configs modified: NO",
        "Existing DB data modified: NO",
        "Existing reports overwritten: NO",
        "Existing strategy modified: NO",
        "Live trading affected: NO",
        "Paper trading affected: NO",
        "Dependencies changed: NO",
        "Migrations run: NO",
        "```",
        "",
        "## Data",
        "",
        f"- Quality: **{overall}**",
        f"- 1h rows: {q1['row_count']} ({q1['first_timestamp']} → {q1['last_timestamp']})",
        f"- 4h rows: {q4['row_count']} ({q4['first_timestamp']} → {q4['last_timestamp']})",
        f"- Fingerprints: 1h `{man1.get('sha256')}` / 4h `{man4.get('sha256')}`",
        "",
        "## Point-in-time",
        "",
        f"- mapping rows: {pit_stats['candidate_rows']}",
        f"- lookahead pass: {pit_stats['lookahead_pass_rows']}",
        f"- lookahead fail: {pit_stats['lookahead_fail_rows']}",
        f"- missing HTF: {pit_stats['missing_htf_rows']}",
        f"- future feature violations: {pit_stats['future_feature_violations']}",
        f"- trade audit rows (A0): {len(audit_rows)} all PASS",
        "",
        "## Primary config (A0 / E1 / S1 / 2R)",
        "",
        f"- compression: ATR(14) percentile <= {feat['compression_threshold']} over {feat['atr_percentile_lookback']}",
        f"- range lookback: {feat['range_lookback_bars']} completed bars",
        f"- breakout buffer: {feat['breakout_buffer_frac']}",
        "",
        "## Partitions (A0)",
        "",
    ]
    for r in part_rows:
        lines.append(
            f"- **{r['partition']}**: {r['start']} → {r['end']} | trades={r['long_trades']}+{r['short_trades']}={r['breakout_candidates']} | "
            f"avg_R={r['average_R']} net_R={r['net_R']} PF={r['profit_factor']} DD={r['maximum_drawdown_R']} | {r['sample_size_label']}"
        )
    lines += [
        "",
        "## Ablation",
        "",
    ]
    for r in ablation_rows:
        lines.append(
            f"- **{r['ablation']}**: trades={r['trade_count']} avg_R={r['average_R']} OOS_n={r['OOS_trade_count']} "
            f"OOS_avg_R={r['OOS_average_R']} | {r['sample_size_label']} | {r['note']}"
        )
    lines += [
        "",
        f"## Verdict: `{verdict}`",
        "",
        "CROSS_SYMBOL VALIDATION: UNAVAILABLE IN THIS EXPERIMENT",
        "",
        "Slippage/funding: UNAVAILABLE WITHOUT ENGINE CHANGE",
        "",
        "## Final safety",
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

    # Emit machine summary for console
    summary = {
        "verdict": verdict,
        "overall_quality": overall,
        "pit_stats": pit_stats,
        "A0_metrics": m_all,
        "A0_oos": m_oos,
        "counts_a0": counts_a0,
        "partitions": part_rows,
        "ablations": ablation_rows,
        "sensitivity_class": sens_rows[0].get("stability_class"),
    }
    (SANDBOX / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"ok": True, "verdict": verdict, "trades": m_all["trade_count"], "oos": m_oos["trade_count"]}, indent=2))


if __name__ == "__main__":
    main()
