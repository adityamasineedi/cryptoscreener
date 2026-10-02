"""Analyze completed Candle-12 V2 research results (analysis-only).

Does not rerun the backtest, change live engines, optimize parameters,
rank symbols/timeframes, or declare winners.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULT_PATH = ROOT / "scripts" / "candle12_v2_research_result_full.json"
SUMMARY_PATH = ROOT / "scripts" / "candle12_v2_research_summary_full.txt"
OUT_JSON = ROOT / "scripts" / "candle12_v2_analysis_report.json"
OUT_MD = ROOT / "scripts" / "candle12_v2_analysis_report.md"

# Documented reporting threshold for INSUFFICIENT_SAMPLE labeling in this report.
# Matches the experiment's min_sample_size_warning (not a universal statistical law).
REPORTING_SAMPLE_THRESHOLD = 30

DEPTH_BUCKETS = [
    ("lt_100", 0, 99),
    ("100_299", 100, 299),
    ("300_999", 300, 999),
    ("1000_4999", 1000, 4999),
    ("5000_plus", 5000, 10**12),
]

DEPTH_VS_RESULTS_BUCKETS = [
    ("lt_300", 0, 299),
    ("300_999", 300, 999),
    ("1000_4999", 1000, 4999),
    ("5000_plus", 5000, 10**12),
]

SAMPLE_BUCKETS = [
    ("0", 0, 0),
    ("1_9", 1, 9),
    ("10_29", 10, 29),
    ("30_49", 30, 49),
    ("50_99", 50, 99),
    ("100_249", 100, 249),
    ("250_plus", 250, 10**12),
]

MAJORS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return x  # keep inf for profit_factor reporting as string later
        return x
    except (TypeError, ValueError):
        return None


def _jsonable(v: Any) -> Any:
    if isinstance(v, float):
        if math.isnan(v):
            return None
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
    return v


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _calendar_days(start: str | None, end: str | None) -> float | None:
    a, b = _parse_ts(start), _parse_ts(end)
    if a is None or b is None:
        return None
    return max(0.0, (b - a).total_seconds() / 86400.0)


def _bucket_label(n: int, buckets: list[tuple[str, int, int]]) -> str:
    for name, lo, hi in buckets:
        if lo <= n <= hi:
            return name
    return "unknown"


def _metric_block(m: dict[str, Any] | None) -> dict[str, Any]:
    m = m or {}
    sample = int(m.get("sample_size") or 0)
    out = {
        "sample_size": sample,
        "LONG": m.get("long_count"),
        "SHORT": m.get("short_count"),
        "TP1_rate": m.get("tp1_rate"),
        "TP2_rate": m.get("tp2_rate"),
        "TP3_rate": m.get("tp3_rate"),
        "SL_rate": m.get("sl_rate"),
        "expectancy_R": m.get("expectancy_R"),
        "average_R": m.get("average_R"),
        "median_R": m.get("median_R"),
        "profit_factor": _jsonable(m.get("profit_factor")),
        "max_drawdown_R": m.get("max_drawdown_R"),
        "MAE_R": m.get("average_MAE_R"),
        "MFE_R": m.get("average_MFE_R"),
        "average_holding_bars": m.get("average_holding_time"),
        "net_R": m.get("net_R"),
        "gross_R": m.get("gross_R"),
    }
    if sample < REPORTING_SAMPLE_THRESHOLD:
        out["sample_label"] = "INSUFFICIENT_SAMPLE"
        out["reporting_threshold"] = REPORTING_SAMPLE_THRESHOLD
    else:
        out["sample_label"] = None
        out["reporting_threshold"] = REPORTING_SAMPLE_THRESHOLD
    return out


def _weighted_mean(pairs: list[tuple[float, float]]) -> float | None:
    """pairs of (value, weight)."""
    num = 0.0
    den = 0.0
    for v, w in pairs:
        if w <= 0:
            continue
        num += v * w
        den += w
    if den <= 0:
        return None
    return num / den


def _aggregate_series_metrics(series_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Trade-weighted aggregate of per-series FULL-period metrics."""
    closed = 0
    long_c = 0
    short_c = 0
    tp1 = tp2 = tp3 = sl = 0.0
    exp_pairs: list[tuple[float, float]] = []
    avg_pairs: list[tuple[float, float]] = []
    med_pairs: list[tuple[float, float]] = []
    mae_pairs: list[tuple[float, float]] = []
    mfe_pairs: list[tuple[float, float]] = []
    hold_pairs: list[tuple[float, float]] = []
    gross_pairs: list[tuple[float, float]] = []
    net_pairs: list[tuple[float, float]] = []
    # profit factor / max DD cannot be correctly recomputed without trade lists;
    # report as unavailable for reconstructed buckets.
    for s in series_list:
        m = s.get("metrics") or {}
        n = int(m.get("sample_size") or s.get("sample_size") or 0)
        if n <= 0:
            continue
        closed += n
        long_c += int(m.get("long_count") or 0)
        short_c += int(m.get("short_count") or 0)
        # rates are fractions of sample; convert to expected counts
        for key, acc_name in (
            ("tp1_rate", "tp1"),
            ("tp2_rate", "tp2"),
            ("tp3_rate", "tp3"),
            ("sl_rate", "sl"),
        ):
            r = _safe_float(m.get(key))
            if r is None:
                continue
            if acc_name == "tp1":
                tp1 += r * n
            elif acc_name == "tp2":
                tp2 += r * n
            elif acc_name == "tp3":
                tp3 += r * n
            else:
                sl += r * n
        for key, store in (
            ("expectancy_R", exp_pairs),
            ("average_R", avg_pairs),
            ("median_R", med_pairs),
            ("average_MAE_R", mae_pairs),
            ("average_MFE_R", mfe_pairs),
            ("average_holding_time", hold_pairs),
            ("gross_R", gross_pairs),
            ("net_R", net_pairs),
        ):
            v = _safe_float(m.get(key))
            if v is not None and not math.isinf(v):
                store.append((v, float(n)))

    def rate(count: float) -> float | None:
        return (count / closed) if closed else None

    return {
        "sample_size": closed,
        "series_count": len(series_list),
        "series_with_trades": sum(
            1
            for s in series_list
            if int((s.get("metrics") or {}).get("sample_size") or s.get("sample_size") or 0)
            > 0
        ),
        "LONG": long_c,
        "SHORT": short_c,
        "TP1_rate": rate(tp1),
        "TP2_rate": rate(tp2),
        "TP3_rate": rate(tp3),
        "SL_rate": rate(sl),
        "expectancy_R": _weighted_mean(exp_pairs),
        "average_R": _weighted_mean(avg_pairs),
        "median_R_trade_weighted_of_series_medians": _weighted_mean(med_pairs),
        "profit_factor": None,
        "profit_factor_note": (
            "Not recomputed for depth buckets: requires trade-level R list "
            "(not persisted in result JSON)."
        ),
        "max_drawdown_R": None,
        "max_drawdown_note": (
            "Not recomputed for depth buckets: requires chronological trade equity "
            "(not persisted in result JSON)."
        ),
        "MAE_R": _weighted_mean(mae_pairs),
        "MFE_R": _weighted_mean(mfe_pairs),
        "average_holding_bars": _weighted_mean(hold_pairs),
        "gross_R": _weighted_mean(gross_pairs),
        "net_R": _weighted_mean(net_pairs),
        "aggregation": "trade_weighted_mean_of_series_FULL_metrics",
        "sample_label": (
            "INSUFFICIENT_SAMPLE" if closed < REPORTING_SAMPLE_THRESHOLD else None
        ),
        "reporting_threshold": REPORTING_SAMPLE_THRESHOLD,
    }


def validate_integrity(d: dict[str, Any]) -> dict[str, Any]:
    totals = d.get("totals") or {}
    universe = d.get("universe") or {}
    series = d.get("series") or []
    overall = d.get("overall") or {}
    by_split = d.get("by_split") or {}
    issues: list[str] = []

    symbols_processed = int(totals.get("symbols_processed") or 0)
    series_processed = int(totals.get("series_processed") or 0)
    candles = int(totals.get("candles_processed") or 0)
    setups = int(totals.get("setups_evaluated") or 0)
    trades_gen = int(totals.get("trades_generated") or 0)
    eligible = int(universe.get("eligible_series") or 0)
    excluded = universe.get("excluded_series") or []
    excluded_count = int(universe.get("excluded_count") or len(excluded))
    requested = universe.get("requested_symbols") or []
    sym_list = universe.get("symbols_processed") or []

    closed_overall = int(overall.get("sample_size") or 0)
    train = int((by_split.get("TRAINING_PERIOD") or {}).get("sample_size") or 0)
    val = int((by_split.get("VALIDATION_PERIOD") or {}).get("sample_size") or 0)
    oos = int((by_split.get("OUT_OF_SAMPLE_PERIOD") or {}).get("sample_size") or 0)
    split_sum = train + val + oos

    series_sample_sum = sum(int(s.get("sample_size") or 0) for s in series)
    series_trades_gen_sum = sum(
        int((s.get("instrumentation") or {}).get("trades_generated") or 0) for s in series
    )
    series_setups_sum = sum(
        int((s.get("instrumentation") or {}).get("setups_evaluated") or 0) for s in series
    )
    series_candles_sum = sum(
        int((s.get("data_quality") or {}).get("candle_count") or 0) for s in series
    )
    # instrumentation candles_processed may be < candle_count (warmup/end trim)
    instr_candles_sum = sum(
        int((s.get("instrumentation") or {}).get("candles_processed") or 0) for s in series
    )

    gap_total = sum(int((s.get("data_quality") or {}).get("gap_count") or 0) for s in series)
    dup_total = sum(
        int((s.get("data_quality") or {}).get("duplicate_count") or 0) for s in series
    )

    if len(series) != series_processed:
        issues.append(
            f"series list length {len(series)} != totals.series_processed {series_processed}"
        )
    if eligible != series_processed:
        issues.append(
            f"universe.eligible_series {eligible} != totals.series_processed {series_processed}"
        )
    if excluded_count != len(excluded):
        issues.append(
            f"excluded_count {excluded_count} != len(excluded_series) {len(excluded)}"
        )
    if symbols_processed != len(sym_list):
        issues.append(
            f"symbols_processed {symbols_processed} != len(symbols_processed list) {len(sym_list)}"
        )
    if len(requested) and symbols_processed + len({e['symbol'] for e in excluded}) < len(
        set(requested)
    ):
        # soft check: requested may include excluded-only symbols
        pass
    if series_sample_sum != closed_overall:
        issues.append(
            f"sum(series.sample_size)={series_sample_sum} != overall.sample_size={closed_overall}"
        )
    if series_trades_gen_sum != trades_gen:
        issues.append(
            f"sum(series.trades_generated)={series_trades_gen_sum} != totals.trades_generated={trades_gen}"
        )
    if series_setups_sum != setups:
        issues.append(
            f"sum(series.setups_evaluated)={series_setups_sum} != totals.setups_evaluated={setups}"
        )
    excluded_candle_sum = sum(int(e.get("candle_count") or 0) for e in excluded)
    explained: list[str] = []
    if series_candles_sum != candles:
        if series_candles_sum + excluded_candle_sum == candles:
            explained.append(
                f"candles_processed total ({candles}) = sum(eligible series candle_count) "
                f"({series_candles_sum}) + excluded series candle_count ({excluded_candle_sum}). "
                "Runner counted loaded candles before INSUFFICIENT_DATA exclusion."
            )
        else:
            issues.append(
                f"sum(series.candle_count)={series_candles_sum} != totals.candles_processed={candles} "
                f"(excluded candle_count sum={excluded_candle_sum}; unexplained residual "
                f"{candles - series_candles_sum - excluded_candle_sum})"
            )
    if split_sum != closed_overall:
        issues.append(
            f"TRAIN+VAL+OOS sample_size sum={split_sum} != overall.sample_size={closed_overall}"
        )
    if trades_gen < closed_overall:
        issues.append(
            f"trades_generated {trades_gen} < closed sample_size {closed_overall}"
        )
    open_trades = trades_gen - closed_overall
    if open_trades < 0:
        issues.append(f"negative open-trade implied count: {open_trades}")

    # direction consistency overall
    long_c = int(overall.get("long_count") or 0)
    short_c = int(overall.get("short_count") or 0)
    if long_c + short_c != closed_overall:
        issues.append(
            f"overall LONG+SHORT={long_c + short_c} != sample_size={closed_overall}"
        )

    by_tf = d.get("by_timeframe") or {}
    tf_sum = sum(int((by_tf.get(tf) or {}).get("sample_size") or 0) for tf in ("5m", "15m", "1h"))
    if tf_sum != closed_overall:
        issues.append(f"sum(by_timeframe.sample_size)={tf_sum} != overall={closed_overall}")

    by_dir = d.get("by_direction") or {}
    dir_sum = sum(
        int((by_dir.get(x) or {}).get("sample_size") or 0) for x in ("LONG", "SHORT")
    )
    if dir_sum != closed_overall:
        issues.append(f"sum(by_direction.sample_size)={dir_sum} != overall={closed_overall}")

    return {
        "totals": {
            "requested_symbols": len(set(requested)),
            "symbols_processed": symbols_processed,
            "series_eligible": eligible,
            "series_in_list": len(series),
            "series_excluded": excluded_count,
            "excluded_series": excluded,
            "candles_processed_total": candles,
            "sum_series_candle_count": series_candles_sum,
            "sum_series_candles_processed_instrumentation": instr_candles_sum,
            "setups_evaluated": setups,
            "sum_series_setups_evaluated": series_setups_sum,
            "trades_generated": trades_gen,
            "sum_series_trades_generated": series_trades_gen_sum,
            "closed_trades_overall_sample_size": closed_overall,
            "sum_series_sample_size": series_sample_sum,
            "open_trades_implied": open_trades,
            "TRAIN_sample_size": train,
            "VALIDATION_sample_size": val,
            "OOS_sample_size": oos,
            "TRAIN_VAL_OOS_sum": split_sum,
            "gap_count_sum": gap_total,
            "duplicate_count_sum": dup_total,
            "excluded_candle_count_sum": excluded_candle_sum,
        },
        "consistent": len(issues) == 0,
        "issues": issues,
        "explained_differences": explained,
        "notes": [
            "totals.candles_processed includes candles loaded for series later marked "
            "INSUFFICIENT_DATA/excluded; eligible series[] candle_count sum is lower by "
            "that excluded amount when the runner increments total_candles before the skip.",
            "totals.candles_processed is loaded OHLCV row counts "
            "(not instrumentation candles_processed, which excludes warmup/end trim).",
            "trades_generated may exceed closed sample_size when OPEN trades remain.",
            "Per-series metrics are FULL-period; TRAIN/VAL/OOS are global chronological "
            "buckets across all trades in the saved aggregates.",
        ],
    }


def build_series_rows(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for s in series:
        dq = s.get("data_quality") or {}
        m = s.get("metrics") or {}
        instr = s.get("instrumentation") or {}
        candle_count = int(dq.get("candle_count") or 0)
        closed = int(s.get("sample_size") or m.get("sample_size") or 0)
        start = s.get("calendar_start") or dq.get("calendar_start")
        end = s.get("calendar_end") or dq.get("calendar_end")
        rows.append(
            {
                "symbol": s.get("symbol"),
                "timeframe": s.get("timeframe"),
                "candle_count": candle_count,
                "calendar_start": start,
                "calendar_end": end,
                "calendar_days": _calendar_days(start, end),
                "setups": int(instr.get("setups_evaluated") or 0),
                "closed_trades": closed,
                "trades_generated": int(instr.get("trades_generated") or 0),
                "depth_bucket": _bucket_label(candle_count, DEPTH_BUCKETS),
                "sample_bucket": _bucket_label(closed, SAMPLE_BUCKETS),
                "insufficient_sample": closed < REPORTING_SAMPLE_THRESHOLD,
                "gap_count": int(dq.get("gap_count") or 0),
                "duplicate_count": int(dq.get("duplicate_count") or 0),
                "data_quality": dq.get("data_quality"),
            }
        )
    return rows


def depth_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_trades = sum(r["closed_trades"] for r in rows)
    out: dict[str, Any] = {"buckets": {}, "total_closed_trades": total_trades}
    for name, lo, hi in DEPTH_BUCKETS:
        subset = [r for r in rows if r["depth_bucket"] == name]
        trades = sum(r["closed_trades"] for r in subset)
        out["buckets"][name] = {
            "candle_range": [lo, hi if hi < 10**12 else None],
            "series_count": len(subset),
            "trade_count": trades,
            "pct_of_total_trades": (trades / total_trades) if total_trades else None,
        }
    return out


def sample_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "reporting_threshold": REPORTING_SAMPLE_THRESHOLD,
        "reporting_threshold_definition": (
            "INSUFFICIENT_SAMPLE when closed_trades < 30, matching the experiment "
            "min_sample_size_warning. This is a reporting threshold for cautious "
            "interpretation, not a universal statistical proof threshold."
        ),
        "buckets": {},
    }
    for name, lo, hi in SAMPLE_BUCKETS:
        subset = [r for r in rows if r["sample_bucket"] == name]
        out["buckets"][name] = {
            "closed_trade_range": [lo, hi if hi < 10**12 else None],
            "series_count": len(subset),
            "labeled_insufficient_sample": all(
                r["insufficient_sample"] for r in subset
            )
            if subset
            else None,
        }
    insuff = [r for r in rows if r["insufficient_sample"]]
    out["insufficient_sample_series_count"] = len(insuff)
    out["insufficient_sample_series_pct"] = (
        len(insuff) / len(rows) if rows else None
    )
    out["sufficient_sample_series_count"] = len(rows) - len(insuff)
    return out


def depth_vs_results(series: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, lo, hi in DEPTH_VS_RESULTS_BUCKETS:
        subset = [
            s
            for s in series
            if lo <= int((s.get("data_quality") or {}).get("candle_count") or 0) <= hi
        ]
        block = _aggregate_series_metrics(subset)
        block["candle_range"] = [lo, hi if hi < 10**12 else None]
        out[name] = block
    return out


def concentration(
    d: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    overall = d.get("overall") or {}
    closed = int(overall.get("sample_size") or 0) or 1
    by_tf = d.get("by_timeframe") or {}
    by_dir = d.get("by_direction") or {}
    by_sym = d.get("by_symbol") or {}

    # symbol concentration from by_symbol closed samples
    sym_counts = [
        (sym, int((m or {}).get("sample_size") or 0)) for sym, m in by_sym.items()
    ]
    sym_counts.sort(key=lambda x: x[1], reverse=True)
    top5 = sym_counts[:5]
    top10 = sym_counts[:10]
    top5_trades = sum(n for _, n in top5)
    top10_trades = sum(n for _, n in top10)

    major_trades = sum(int((by_sym.get(s) or {}).get("sample_size") or 0) for s in MAJORS)
    other_trades = closed - major_trades

    # long-history share
    long_hist_trades = sum(
        r["closed_trades"] for r in rows if r["candle_count"] >= 5000
    )
    short_hist_trades = sum(
        r["closed_trades"] for r in rows if r["candle_count"] < 300
    )

    # Herfindahl on symbol trade shares
    hhi = 0.0
    for _, n in sym_counts:
        if closed > 0:
            share = n / closed
            hhi += share * share

    return {
        "closed_trades_denominator": int(overall.get("sample_size") or 0),
        "by_timeframe_share": {
            tf: {
                "sample_size": int((by_tf.get(tf) or {}).get("sample_size") or 0),
                "pct": (
                    int((by_tf.get(tf) or {}).get("sample_size") or 0) / closed
                    if closed
                    else None
                ),
            }
            for tf in ("5m", "15m", "1h")
        },
        "by_direction_share": {
            dirc: {
                "sample_size": int((by_dir.get(dirc) or {}).get("sample_size") or 0),
                "pct": (
                    int((by_dir.get(dirc) or {}).get("sample_size") or 0) / closed
                    if closed
                    else None
                ),
            }
            for dirc in ("LONG", "SHORT")
        },
        "btc_eth_sol": {
            "sample_size": major_trades,
            "pct": major_trades / closed if closed else None,
            "per_symbol": {
                s: int((by_sym.get(s) or {}).get("sample_size") or 0) for s in MAJORS
            },
        },
        "other_symbols_aggregate": {
            "sample_size": other_trades,
            "pct": other_trades / closed if closed else None,
            "symbol_count_with_any_closed_trade": sum(
                1 for sym, n in sym_counts if sym not in MAJORS and n > 0
            ),
        },
        "top5_symbols_share": {
            "sample_size": top5_trades,
            "pct": top5_trades / closed if closed else None,
            "symbols": [{"symbol": s, "sample_size": n} for s, n in top5],
            "note": "Listed for concentration measurement only; not a ranking.",
        },
        "top10_symbols_share": {
            "sample_size": top10_trades,
            "pct": top10_trades / closed if closed else None,
        },
        "herfindahl_hirschman_index_symbols": hhi,
        "long_history_5000plus_trade_share": {
            "sample_size": long_hist_trades,
            "pct": long_hist_trades / closed if closed else None,
        },
        "short_history_lt300_trade_share": {
            "sample_size": short_hist_trades,
            "pct": short_hist_trades / closed if closed else None,
        },
    }


def oos_stability(by_split: dict[str, Any]) -> dict[str, Any]:
    """Compare TRAIN / VALIDATION / OOS aggregates (overall only in saved artifact)."""
    labels = ("TRAINING_PERIOD", "VALIDATION_PERIOD", "OUT_OF_SAMPLE_PERIOD")
    blocks = {lab: _metric_block(by_split.get(lab)) for lab in labels}

    def sign(x: float | None) -> str | None:
        if x is None:
            return None
        if x > 0:
            return "positive"
        if x < 0:
            return "negative"
        return "zero"

    observations: list[str] = []
    exp = {lab: blocks[lab].get("expectancy_R") for lab in labels}
    signs = {lab: sign(_safe_float(exp[lab])) for lab in labels}
    if len(set(signs.values())) > 1:
        observations.append(
            f"Expectancy_R sign differs across periods: "
            f"TRAIN={signs['TRAINING_PERIOD']} ({exp['TRAINING_PERIOD']}), "
            f"VALIDATION={signs['VALIDATION_PERIOD']} ({exp['VALIDATION_PERIOD']}), "
            f"OOS={signs['OUT_OF_SAMPLE_PERIOD']} ({exp['OUT_OF_SAMPLE_PERIOD']})."
        )
    else:
        observations.append(
            f"Expectancy_R sign is {signs['TRAINING_PERIOD']} in TRAIN, VALIDATION, and OOS "
            f"(values: TRAIN={exp['TRAINING_PERIOD']}, "
            f"VALIDATION={exp['VALIDATION_PERIOD']}, "
            f"OOS={exp['OUT_OF_SAMPLE_PERIOD']})."
        )

    pf = {lab: blocks[lab].get("profit_factor") for lab in labels}
    observations.append(
        f"Profit factor by period: TRAIN={pf['TRAINING_PERIOD']}, "
        f"VALIDATION={pf['VALIDATION_PERIOD']}, OOS={pf['OUT_OF_SAMPLE_PERIOD']}."
    )

    for metric, label in (
        ("TP1_rate", "TP1 rate"),
        ("SL_rate", "SL rate"),
        ("MAE_R", "MAE_R"),
        ("MFE_R", "MFE_R"),
    ):
        vals = {lab: blocks[lab].get(metric) for lab in labels}
        observations.append(
            f"{label} by period: TRAIN={vals['TRAINING_PERIOD']}, "
            f"VALIDATION={vals['VALIDATION_PERIOD']}, "
            f"OOS={vals['OUT_OF_SAMPLE_PERIOD']}."
        )

    # trade frequency proxy: sample_size
    ss = {lab: blocks[lab].get("sample_size") for lab in labels}
    observations.append(
        f"Closed-trade counts by period: TRAIN={ss['TRAINING_PERIOD']}, "
        f"VALIDATION={ss['VALIDATION_PERIOD']}, OOS={ss['OUT_OF_SAMPLE_PERIOD']} "
        f"(chronological 60/20/20 split; counts are not normalized per calendar day)."
    )

    return {
        "scope": "overall_aggregates_only",
        "missing_matrices": [
            "TRAIN/VALIDATION/OOS x timeframe",
            "TRAIN/VALIDATION/OOS x direction x timeframe",
        ],
        "missing_matrices_note": (
            "The saved result JSON does not persist trade-level rows or "
            "period×timeframe×direction aggregates. Stability observations below "
            "use overall by_split only. No backtest rerun was performed."
        ),
        "periods": {
            "TRAIN": blocks["TRAINING_PERIOD"],
            "VALIDATION": blocks["VALIDATION_PERIOD"],
            "OOS": blocks["OUT_OF_SAMPLE_PERIOD"],
        },
        "observations": observations,
    }


def fees_impact(d: dict[str, Any]) -> dict[str, Any]:
    overall = d.get("overall") or {}
    cfg = d.get("v2_config") or {}
    gross = _safe_float(overall.get("gross_R"))
    net = _safe_float(overall.get("net_R"))
    diff = None
    if gross is not None and net is not None:
        diff = gross - net
    closed = int(overall.get("sample_size") or 0)
    # Approximate total cost in R units: sum over trades of (gross_R - net_R)
    # Using mean difference * sample_size when per-trade costs unavailable.
    total_cost_R_approx = (diff * closed) if diff is not None else None

    by_split = {}
    for lab, key in (
        ("TRAIN", "TRAINING_PERIOD"),
        ("VALIDATION", "VALIDATION_PERIOD"),
        ("OOS", "OUT_OF_SAMPLE_PERIOD"),
    ):
        m = d.get("by_split", {}).get(key) or {}
        g, n = _safe_float(m.get("gross_R")), _safe_float(m.get("net_R"))
        by_split[lab] = {
            "gross_R": g,
            "net_R": n,
            "gross_minus_net_R": (g - n) if g is not None and n is not None else None,
            "sample_size": m.get("sample_size"),
        }

    return {
        "configured_trading_fee": cfg.get("trading_fee"),
        "configured_entry_slippage": cfg.get("entry_slippage"),
        "configured_exit_slippage": cfg.get("exit_slippage"),
        "overall": {
            "gross_expectancy_R": gross,
            "net_expectancy_R": net,
            "difference_gross_minus_net_R": diff,
            "closed_trades": closed,
            "approx_total_cost_contribution_R": total_cost_R_approx,
            "approx_note": (
                "approx_total_cost_contribution_R = "
                "(mean gross_R - mean net_R) * closed_trades. "
                "Exact per-trade fee/slippage totals were not persisted."
            ),
        },
        "by_split": by_split,
    }


def data_quality_section(rows: list[dict[str, Any]], integrity: dict[str, Any]) -> dict[str, Any]:
    lt300 = [r for r in rows if r["candle_count"] < 300]
    # "recent history" heuristic: calendar_days < 14
    recent = [r for r in rows if (r["calendar_days"] is not None and r["calendar_days"] < 14)]
    return {
        "gap_count_sum": integrity["totals"]["gap_count_sum"],
        "duplicate_count_sum": integrity["totals"]["duplicate_count_sum"],
        "insufficient_series_excluded": integrity["totals"]["excluded_series"],
        "series_with_lt_300_candles": len(lt300),
        "series_with_lt_300_candles_pct": len(lt300) / len(rows) if rows else None,
        "series_with_calendar_span_lt_14_days": len(recent),
        "series_with_calendar_span_lt_14_days_pct": len(recent) / len(rows) if rows else None,
        "total_candles_note": (
            f"totals.candles_processed={integrity['totals']['candles_processed_total']} "
            "is the sum of per-series candle_count values. It does NOT imply equal "
            "historical depth across symbols/timeframes."
        ),
    }


def fmt_pct(x: Any) -> str:
    if x is None:
        return "—"
    try:
        return f"{100 * float(x):.2f}%"
    except (TypeError, ValueError):
        return str(x)


def fmt_num(x: Any, nd: int = 4) -> str:
    if x is None:
        return "—"
    if x == "inf" or x == float("inf"):
        return "inf"
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return str(x)


def md_metrics_table(title: str, block: dict[str, Any]) -> list[str]:
    lines = [f"### {title}", ""]
    if block.get("sample_label") == "INSUFFICIENT_SAMPLE":
        lines.append(
            f"**INSUFFICIENT_SAMPLE** (closed sample_size={block.get('sample_size')} "
            f"< reporting threshold {block.get('reporting_threshold')})."
        )
        lines.append("")
    rows = [
        ("sample_size", block.get("sample_size")),
        ("LONG", block.get("LONG")),
        ("SHORT", block.get("SHORT")),
        ("TP1_rate", fmt_pct(block.get("TP1_rate"))),
        ("TP2_rate", fmt_pct(block.get("TP2_rate"))),
        ("TP3_rate", fmt_pct(block.get("TP3_rate"))),
        ("SL_rate", fmt_pct(block.get("SL_rate"))),
        ("expectancy_R", fmt_num(block.get("expectancy_R"))),
        ("average_R", fmt_num(block.get("average_R"))),
        ("median_R", fmt_num(block.get("median_R"))),
        ("profit_factor", fmt_num(block.get("profit_factor"))),
        ("max_drawdown_R", fmt_num(block.get("max_drawdown_R"))),
        ("MAE_R", fmt_num(block.get("MAE_R"))),
        ("MFE_R", fmt_num(block.get("MFE_R"))),
        ("average_holding_bars", fmt_num(block.get("average_holding_bars"))),
        ("net_R", fmt_num(block.get("net_R"))),
        ("gross_R", fmt_num(block.get("gross_R"))),
    ]
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    for k, v in rows:
        lines.append(f"| {k} | {v} |")
    lines.append("")
    return lines


def write_markdown(report: dict[str, Any]) -> str:
    integ = report["data_integrity"]
    t = integ["totals"]
    lines: list[str] = []
    lines.append("# Candle-1 / Candle-2 Research V2 — Analysis Report")
    lines.append("")
    lines.append(f"- Experiment: `{report['experiment_id']}`")
    lines.append(f"- Source: `{report['source_result']}`")
    lines.append(f"- Analysis created (UTC): `{report['created_at']}`")
    lines.append(f"- Reporting sample threshold: **{REPORTING_SAMPLE_THRESHOLD}** closed trades")
    lines.append("")
    lines.append(
        "This document evaluates an existing historical research experiment only. "
        "It does not change live signal logic, production thresholds, or research rules."
    )
    lines.append("")

    # 1
    lines.append("## 1. Data integrity")
    lines.append("")
    lines.append(f"- Consistency validation: **{'PASS' if integ['consistent'] else 'FAIL'}**")
    if integ["issues"]:
        lines.append("- Unresolved issues:")
        for issue in integ["issues"]:
            lines.append(f"  - {issue}")
    else:
        lines.append("- No unresolved internal consistency issues among persisted totals.")
    if integ.get("explained_differences"):
        lines.append("- Explained differences (not silently corrected):")
        for item in integ["explained_differences"]:
            lines.append(f"  - {item}")
    lines.append("")
    lines.append("| Check | Value |")
    lines.append("|---|---|")
    for k in (
        "requested_symbols",
        "symbols_processed",
        "series_eligible",
        "series_in_list",
        "series_excluded",
        "candles_processed_total",
        "sum_series_candle_count",
        "excluded_candle_count_sum",
        "setups_evaluated",
        "trades_generated",
        "closed_trades_overall_sample_size",
        "open_trades_implied",
        "TRAIN_sample_size",
        "VALIDATION_sample_size",
        "OOS_sample_size",
        "TRAIN_VAL_OOS_sum",
        "gap_count_sum",
        "duplicate_count_sum",
    ):
        lines.append(f"| {k} | {t[k]} |")
    lines.append("")
    lines.append("Excluded series:")
    lines.append("")
    if t["excluded_series"]:
        for e in t["excluded_series"]:
            lines.append(
                f"- `{e.get('symbol')}` `{e.get('timeframe')}`: {e.get('reason')} "
                f"(status={e.get('status')}, candle_count={e.get('candle_count')})"
            )
    else:
        lines.append("- none")
    lines.append("")
    for n in integ.get("notes") or []:
        lines.append(f"- Note: {n}")
    lines.append("")

    # 2
    lines.append("## 2. Historical-depth distribution")
    lines.append("")
    lines.append(
        "Series do **not** share equal historical coverage. "
        f"Total candles = {t['candles_processed_total']} is a sum across unequal depths."
    )
    lines.append("")
    dd = report["historical_depth_distribution"]
    lines.append("| Depth bucket | Series | Closed trades | % of trades |")
    lines.append("|---|---:|---:|---:|")
    labels = {
        "lt_100": "<100",
        "100_299": "100–299",
        "300_999": "300–999",
        "1000_4999": "1000–4999",
        "5000_plus": "5000+",
    }
    for key, label in labels.items():
        b = dd["buckets"][key]
        lines.append(
            f"| {label} | {b['series_count']} | {b['trade_count']} | "
            f"{fmt_pct(b['pct_of_total_trades'])} |"
        )
    lines.append("")

    # 3 overall
    lines.append("## 3. Overall results (FULL period aggregate)")
    lines.append("")
    lines.extend(md_metrics_table("FULL / overall", report["overall"]))

    # 4-6 periods
    lines.append("## 4. TRAIN results")
    lines.append("")
    lines.extend(md_metrics_table("TRAINING_PERIOD", report["by_split"]["TRAIN"]))

    lines.append("## 5. Validation results")
    lines.append("")
    lines.extend(md_metrics_table("VALIDATION_PERIOD", report["by_split"]["VALIDATION"]))

    lines.append("## 6. OOS results")
    lines.append("")
    lines.append(
        "OOS is evaluation-only. Parameters were fixed before OOS inspection "
        "in the original experiment."
    )
    lines.append("")
    lines.extend(md_metrics_table("OUT_OF_SAMPLE_PERIOD", report["by_split"]["OOS"]))

    # 7 timeframe
    lines.append("## 7. Timeframe breakdown")
    lines.append("")
    lines.append(
        "Saved artifact provides FULL-period aggregates by timeframe. "
        "TRAIN/VALIDATION/OOS × timeframe matrices were **not persisted** "
        "and are marked unavailable (no backtest repair rerun)."
    )
    lines.append("")
    for tf in ("5m", "15m", "1h"):
        lines.extend(md_metrics_table(f"Timeframe {tf} (FULL period)", report["by_timeframe"][tf]))
        lines.append(f"#### {tf} × TRAIN / VALIDATION / OOS")
        lines.append("")
        lines.append("| Period | Status |")
        lines.append("|---|---|")
        lines.append("| TRAIN | UNAVAILABLE_IN_SAVED_ARTIFACT |")
        lines.append("| VALIDATION | UNAVAILABLE_IN_SAVED_ARTIFACT |")
        lines.append("| OOS | UNAVAILABLE_IN_SAVED_ARTIFACT |")
        lines.append("")

    # 8 direction
    lines.append("## 8. Direction breakdown")
    lines.append("")
    lines.append(
        "FULL-period LONG/SHORT aggregates are available. "
        "Direction × timeframe × period matrices were not persisted."
    )
    lines.append("")
    for dirc in ("LONG", "SHORT"):
        lines.extend(md_metrics_table(f"Direction {dirc} (FULL period)", report["by_direction"][dirc]))
    lines.append("### Direction × timeframe (FULL) — unavailable")
    lines.append("")
    lines.append(
        "Not present in `candle12_v2_research_result_full.json`. "
        "No inference substituted."
    )
    lines.append("")
    lines.append("### Direction × TRAIN/VALIDATION/OOS — unavailable")
    lines.append("")
    lines.append(
        "Not present in the saved artifact beyond overall period aggregates "
        "(section 4–6 include LONG/SHORT counts inside each period block)."
    )
    lines.append("")
    # Include LONG/SHORT counts from period blocks
    lines.append("Period-level direction counts (from saved by_split):")
    lines.append("")
    lines.append("| Period | LONG | SHORT | sample_size |")
    lines.append("|---|---:|---:|---:|")
    for name, key in (
        ("TRAIN", "TRAIN"),
        ("VALIDATION", "VALIDATION"),
        ("OOS", "OOS"),
    ):
        b = report["by_split"][key]
        lines.append(
            f"| {name} | {b.get('LONG')} | {b.get('SHORT')} | {b.get('sample_size')} |"
        )
    lines.append("")

    # 9 majors
    lines.append("## 9. BTC / ETH / SOL breakdown")
    lines.append("")
    for sym in MAJORS:
        lines.extend(
            md_metrics_table(f"{sym} (FULL, all timeframes combined)", report["majors"][sym])
        )
        # per-tf series rows
        lines.append(f"#### {sym} by timeframe (series FULL metrics)")
        lines.append("")
        lines.append(
            "| TF | candles | calendar_days | closed_trades | expectancy_R | net_R | sample_label |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---|")
        for row in report["majors"]["per_series"][sym]:
            lab = "INSUFFICIENT_SAMPLE" if row["insufficient_sample"] else "—"
            lines.append(
                f"| {row['timeframe']} | {row['candle_count']} | "
                f"{fmt_num(row['calendar_days'], 2)} | {row['closed_trades']} | "
                f"{fmt_num(row['expectancy_R'])} | {fmt_num(row['net_R'])} | {lab} |"
            )
        lines.append("")
    lines.extend(
        md_metrics_table(
            "All other eligible symbols (aggregate FULL)",
            report["majors"]["other_aggregate"],
        )
    )

    # 10 sample size
    lines.append("## 10. Sample-size distribution")
    lines.append("")
    lines.append(report["sample_size_distribution"]["reporting_threshold_definition"])
    lines.append("")
    lines.append("| Closed-trade bucket | Series count |")
    lines.append("|---|---:|")
    sb_labels = {
        "0": "0",
        "1_9": "1–9",
        "10_29": "10–29",
        "30_49": "30–49",
        "50_99": "50–99",
        "100_249": "100–249",
        "250_plus": "250+",
    }
    for key, label in sb_labels.items():
        lines.append(
            f"| {label} | {report['sample_size_distribution']['buckets'][key]['series_count']} |"
        )
    lines.append("")
    lines.append(
        f"- Series labeled INSUFFICIENT_SAMPLE (<{REPORTING_SAMPLE_THRESHOLD}): "
        f"**{report['sample_size_distribution']['insufficient_sample_series_count']}** "
        f"({fmt_pct(report['sample_size_distribution']['insufficient_sample_series_pct'])})"
    )
    lines.append(
        f"- Series at/above reporting threshold: "
        f"**{report['sample_size_distribution']['sufficient_sample_series_count']}**"
    )
    lines.append("")

    # 11 depth analysis
    lines.append("## 11. Historical-depth analysis")
    lines.append("")
    lines.append(
        "Objective metrics below are trade-weighted means of per-series FULL metrics "
        "within each candle-count bucket. Profit factor and max drawdown are not "
        "recomputed without trade lists. This section measures whether aggregates are "
        "dominated by short-history series; it does not compare groups as better/worse."
    )
    lines.append("")
    dvr = report["historical_depth_vs_results"]
    dvr_labels = {
        "lt_300": "<300 candles",
        "300_999": "300–999",
        "1000_4999": "1000–4999",
        "5000_plus": "5000+",
    }
    for key, label in dvr_labels.items():
        lines.extend(md_metrics_table(f"Depth {label}", dvr[key]))

    # 12 OOS stability
    lines.append("## 12. OOS stability observations")
    lines.append("")
    stab = report["oos_stability"]
    lines.append(stab["missing_matrices_note"])
    lines.append("")
    for obs in stab["observations"]:
        lines.append(f"- {obs}")
    lines.append("")
    lines.append(
        "No TRAIN/VALIDATION/OOS × timeframe or × direction×timeframe matrix is "
        "available in the saved artifact; those comparisons are omitted rather than invented."
    )
    lines.append("")

    # 13 fees
    lines.append("## 13. Fees / slippage impact")
    lines.append("")
    fi = report["fees_slippage_impact"]
    lines.append(
        f"- Configured trading_fee={fi['configured_trading_fee']}, "
        f"entry_slippage={fi['configured_entry_slippage']}, "
        f"exit_slippage={fi['configured_exit_slippage']}"
    )
    lines.append(
        f"- Overall gross expectancy_R={fmt_num(fi['overall']['gross_expectancy_R'])}"
    )
    lines.append(
        f"- Overall net expectancy_R={fmt_num(fi['overall']['net_expectancy_R'])}"
    )
    lines.append(
        f"- Difference (gross − net)={fmt_num(fi['overall']['difference_gross_minus_net_R'])}"
    )
    lines.append(
        f"- Approx total cost contribution (R units)="
        f"{fmt_num(fi['overall']['approx_total_cost_contribution_R'])}"
    )
    lines.append(f"- {fi['overall']['approx_note']}")
    lines.append("")
    lines.append("| Period | gross_R | net_R | gross−net |")
    lines.append("|---|---:|---:|---:|")
    for lab in ("TRAIN", "VALIDATION", "OOS"):
        b = fi["by_split"][lab]
        lines.append(
            f"| {lab} | {fmt_num(b['gross_R'])} | {fmt_num(b['net_R'])} | "
            f"{fmt_num(b['gross_minus_net_R'])} |"
        )
    lines.append("")

    # 14 concentration
    lines.append("## 14. Concentration analysis")
    lines.append("")
    conc = report["concentration"]
    lines.append(
        "Figures below measure concentration of closed-trade sample mass. "
        "They are not rankings and do not select winners."
    )
    lines.append("")
    lines.append("| Slice | sample_size | pct of closed trades |")
    lines.append("|---|---:|---:|")
    for tf in ("5m", "15m", "1h"):
        b = conc["by_timeframe_share"][tf]
        lines.append(f"| Timeframe {tf} | {b['sample_size']} | {fmt_pct(b['pct'])} |")
    for dirc in ("LONG", "SHORT"):
        b = conc["by_direction_share"][dirc]
        lines.append(f"| Direction {dirc} | {b['sample_size']} | {fmt_pct(b['pct'])} |")
    lines.append(
        f"| BTC+ETH+SOL combined | {conc['btc_eth_sol']['sample_size']} | "
        f"{fmt_pct(conc['btc_eth_sol']['pct'])} |"
    )
    lines.append(
        f"| All other symbols | {conc['other_symbols_aggregate']['sample_size']} | "
        f"{fmt_pct(conc['other_symbols_aggregate']['pct'])} |"
    )
    lines.append(
        f"| Top-5 symbols (concentration only) | {conc['top5_symbols_share']['sample_size']} | "
        f"{fmt_pct(conc['top5_symbols_share']['pct'])} |"
    )
    lines.append(
        f"| Top-10 symbols (concentration only) | {conc['top10_symbols_share']['sample_size']} | "
        f"{fmt_pct(conc['top10_symbols_share']['pct'])} |"
    )
    lines.append(
        f"| Depth 5000+ candles | {conc['long_history_5000plus_trade_share']['sample_size']} | "
        f"{fmt_pct(conc['long_history_5000plus_trade_share']['pct'])} |"
    )
    lines.append(
        f"| Depth <300 candles | {conc['short_history_lt300_trade_share']['sample_size']} | "
        f"{fmt_pct(conc['short_history_lt300_trade_share']['pct'])} |"
    )
    lines.append("")
    lines.append(
        f"- Symbol HHI (Herfindahl–Hirschman on closed-trade shares): "
        f"{fmt_num(conc['herfindahl_hirschman_index_symbols'])}"
    )
    lines.append(
        f"- Other-symbol count with ≥1 closed trade: "
        f"{conc['other_symbols_aggregate']['symbol_count_with_any_closed_trade']}"
    )
    lines.append("")

    # 15 limitations
    lines.append("## 15. Limitations")
    lines.append("")
    for lim in report["limitations"]:
        lines.append(f"- {lim}")
    lines.append("")

    # Research observations
    lines.append("## Research observations")
    lines.append("")
    for obs in report["research_observations"]:
        lines.append(f"- {obs}")
    lines.append("")
    lines.append("### Explicit statements")
    lines.append("")
    for s in report["explicit_statements"]:
        lines.append(f"- {s}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    if not RESULT_PATH.exists():
        raise SystemExit(f"Missing result file: {RESULT_PATH}")
    d = json.loads(RESULT_PATH.read_text(encoding="utf-8"))

    integrity = validate_integrity(d)
    series = d.get("series") or []
    rows = build_series_rows(series)

    # Majors per-series + aggregate others
    majors_out: dict[str, Any] = {"per_series": {}}
    by_sym = d.get("by_symbol") or {}
    for sym in MAJORS:
        majors_out[sym] = _metric_block(by_sym.get(sym))
        majors_out["per_series"][sym] = []
        for s in series:
            if s.get("symbol") != sym:
                continue
            m = s.get("metrics") or {}
            row = next(
                r
                for r in rows
                if r["symbol"] == sym and r["timeframe"] == s.get("timeframe")
            )
            majors_out["per_series"][sym].append(
                {
                    **row,
                    "expectancy_R": m.get("expectancy_R"),
                    "net_R": m.get("net_R"),
                    "gross_R": m.get("gross_R"),
                }
            )

    other_series = [s for s in series if s.get("symbol") not in MAJORS]
    majors_out["other_aggregate"] = _aggregate_series_metrics(other_series)
    # Prefer saved by_symbol rollup for majors if present; for others use weighted series
    # Also compute other from by_symbol for sample_size cross-check
    other_sym_sample = sum(
        int((m or {}).get("sample_size") or 0)
        for sym, m in by_sym.items()
        if sym not in MAJORS
    )
    majors_out["other_aggregate"]["sample_size_crosscheck_by_symbol"] = other_sym_sample

    report: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": d.get("experiment_id"),
        "source_result": str(RESULT_PATH.as_posix()),
        "source_summary": str(SUMMARY_PATH.as_posix()) if SUMMARY_PATH.exists() else None,
        "analysis_only": True,
        "reporting_sample_threshold": REPORTING_SAMPLE_THRESHOLD,
        "configuration_snapshot": {
            "structure_lookback_bars": (d.get("v2_config") or {}).get(
                "structure_lookback_bars"
            ),
            "min_bars_warmup": (d.get("v2_config") or {}).get("min_bars"),
            "trading_fee": (d.get("v2_config") or {}).get("trading_fee"),
            "entry_slippage": (d.get("v2_config") or {}).get("entry_slippage"),
            "exit_slippage": (d.get("v2_config") or {}).get("exit_slippage"),
            "train_fraction": (d.get("v2_config") or {}).get("train_fraction"),
            "validation_fraction": (d.get("v2_config") or {}).get("validation_fraction"),
            "oos_fraction": (d.get("v2_config") or {}).get("oos_fraction"),
            "min_sample_size_warning": (d.get("v2_config") or {}).get(
                "min_sample_size_warning"
            ),
            "unchanged_note": (
                "LOOKBACK=300, warmup=50, C1/C2/BOS/SL/TP/RR/fees/slippage were not modified "
                "by this analysis."
            ),
        },
        "data_integrity": integrity,
        "historical_depth_distribution": depth_distribution(rows),
        "overall": _metric_block(d.get("overall")),
        "by_split": {
            "TRAIN": _metric_block((d.get("by_split") or {}).get("TRAINING_PERIOD")),
            "VALIDATION": _metric_block(
                (d.get("by_split") or {}).get("VALIDATION_PERIOD")
            ),
            "OOS": _metric_block(
                (d.get("by_split") or {}).get("OUT_OF_SAMPLE_PERIOD")
            ),
        },
        "by_timeframe": {
            tf: _metric_block((d.get("by_timeframe") or {}).get(tf))
            for tf in ("5m", "15m", "1h")
        },
        "by_timeframe_by_split": {
            "status": "UNAVAILABLE_IN_SAVED_ARTIFACT",
            "reason": (
                "candle12_v2_research_result_full.json does not contain "
                "period×timeframe aggregates or trade rows."
            ),
        },
        "by_direction": {
            dirc: _metric_block((d.get("by_direction") or {}).get(dirc))
            for dirc in ("LONG", "SHORT")
        },
        "by_direction_by_timeframe_by_split": {
            "status": "UNAVAILABLE_IN_SAVED_ARTIFACT",
            "reason": (
                "Direction×timeframe×period matrices were not persisted in the "
                "experiment output."
            ),
        },
        "majors": majors_out,
        "sample_size_distribution": sample_distribution(rows),
        "historical_depth_vs_results": depth_vs_results(series),
        "oos_stability": oos_stability(d.get("by_split") or {}),
        "fees_slippage_impact": fees_impact(d),
        "data_quality": data_quality_section(rows, integrity),
        "concentration": concentration(d, rows),
        "series_rows_count": len(rows),
        "confirmations": d.get("confirmations") or {},
    }

    # Limitations
    report["limitations"] = [
        "Trade-level rows were not saved in the experiment JSON; some cross-tabs cannot be rebuilt.",
        "TRAIN/VALIDATION/OOS × timeframe matrices are unavailable in the saved artifact.",
        "Direction × timeframe × period matrices are unavailable in the saved artifact.",
        "Depth-bucket profit_factor and max_drawdown_R are not recomputed without trade lists.",
        "Median_R in depth buckets is a trade-weighted mean of per-series medians, not a pooled median.",
        "Most series have short calendar spans; total candle count is dominated by unequal depths.",
        "Series metrics are FULL-period; period metrics are global chronological buckets.",
        "INSUFFICIENT_SAMPLE uses reporting threshold 30 (experiment warning), not a universal test.",
        "This analysis does not establish future profitability.",
    ]

    # Factual research observations only
    t = integrity["totals"]
    dd = report["historical_depth_distribution"]["buckets"]
    conc = report["concentration"]
    fi = report["fees_slippage_impact"]["overall"]
    obs: list[str] = []
    obs.append(
        f"Integrity check {'passed' if integrity['consistent'] else 'failed'} on persisted totals "
        f"(symbols_processed={t['symbols_processed']}, series={t['series_eligible']}, "
        f"candles={t['candles_processed_total']}, setups={t['setups_evaluated']}, "
        f"trades_generated={t['trades_generated']}, closed={t['closed_trades_overall_sample_size']})."
    )
    obs.append(
        f"TRAIN/VALIDATION/OOS closed-trade counts are "
        f"{t['TRAIN_sample_size']}/{t['VALIDATION_sample_size']}/{t['OOS_sample_size']} "
        f"(sum {t['TRAIN_VAL_OOS_sum']})."
    )
    obs.append(
        f"Gap_count sum={t['gap_count_sum']}; duplicate_count sum={t['duplicate_count_sum']}; "
        f"excluded series={t['series_excluded']}."
    )
    obs.append(
        f"Depth <300 candles: {dd['lt_100']['series_count'] + dd['100_299']['series_count']} series "
        f"and {dd['lt_100']['trade_count'] + dd['100_299']['trade_count']} closed trades "
        f"({fmt_pct((dd['lt_100']['trade_count'] + dd['100_299']['trade_count']) / max(1, report['historical_depth_distribution']['total_closed_trades']))} of trades)."
    )
    obs.append(
        f"Depth 5000+: {dd['5000_plus']['series_count']} series and "
        f"{dd['5000_plus']['trade_count']} closed trades "
        f"({fmt_pct(dd['5000_plus']['pct_of_total_trades'])} of trades)."
    )
    obs.append(
        f"FULL overall expectancy_R={report['overall'].get('expectancy_R')}, "
        f"net_R={report['overall'].get('net_R')}, "
        f"gross_R={report['overall'].get('gross_R')}."
    )
    obs.append(
        f"Period expectancy_R: TRAIN={report['by_split']['TRAIN'].get('expectancy_R')}, "
        f"VALIDATION={report['by_split']['VALIDATION'].get('expectancy_R')}, "
        f"OOS={report['by_split']['OOS'].get('expectancy_R')}."
    )
    obs.append(
        f"FULL timeframe sample sizes: 5m={report['by_timeframe']['5m'].get('sample_size')}, "
        f"15m={report['by_timeframe']['15m'].get('sample_size')}, "
        f"1h={report['by_timeframe']['1h'].get('sample_size')}."
    )
    obs.append(
        f"FULL direction sample sizes: LONG={report['by_direction']['LONG'].get('sample_size')}, "
        f"SHORT={report['by_direction']['SHORT'].get('sample_size')}."
    )
    obs.append(
        f"BTC+ETH+SOL combined closed trades={conc['btc_eth_sol']['sample_size']} "
        f"({fmt_pct(conc['btc_eth_sol']['pct'])}); "
        f"all other symbols={conc['other_symbols_aggregate']['sample_size']} "
        f"({fmt_pct(conc['other_symbols_aggregate']['pct'])})."
    )
    obs.append(
        f"Series with closed_trades < {REPORTING_SAMPLE_THRESHOLD} (INSUFFICIENT_SAMPLE label): "
        f"{report['sample_size_distribution']['insufficient_sample_series_count']} / {len(rows)}."
    )
    obs.append(
        f"Cost impact (overall mean): gross_R − net_R = {fi.get('difference_gross_minus_net_R')}; "
        f"approx total cost contribution_R = {fi.get('approx_total_cost_contribution_R')}."
    )
    obs.append(
        f"Top-5 symbols account for {fmt_pct(conc['top5_symbols_share']['pct'])} of closed trades "
        f"(concentration statistic only)."
    )
    obs.append(
        "TRAIN/VALIDATION/OOS × timeframe and direction×timeframe×period matrices are absent "
        "from the saved experiment output; this analysis does not invent those cells."
    )
    report["research_observations"] = obs

    report["explicit_statements"] = [
        "This is historical research.",
        "OOS is evaluation-only.",
        "The experiment does not establish future profitability.",
        "Live signal logic was not changed.",
        "Production thresholds were not changed.",
        "No parameter optimization was performed.",
        "LOOKBACK=300, warmup=50, and Candle-1/Candle-2 rules were not changed by this analysis.",
    ]

    # Final consistency validation before save
    if not integrity["consistent"]:
        report["save_gate"] = {
            "saved": True,
            "warning": "Integrity issues were found and are reported in data_integrity.issues",
        }
    else:
        report["save_gate"] = {"saved": True, "warning": None}

    # Serialize JSON with inf handling
    def default(o: Any) -> Any:
        if isinstance(o, float) and (math.isinf(o) or math.isnan(o)):
            return _jsonable(o)
        raise TypeError(type(o))

    OUT_JSON.write_text(
        json.dumps(report, indent=2, default=default), encoding="utf-8"
    )
    OUT_MD.write_text(write_markdown(report), encoding="utf-8")
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_MD}")
    print(f"integrity_consistent={integrity['consistent']}")
    if integrity["issues"]:
        print("issues:")
        for i in integrity["issues"]:
            print(" -", i)


if __name__ == "__main__":
    main()
