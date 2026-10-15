"""Honest regime funnel counters. Analytics only.

Each stage is counted from its own flag. A missing flag is not inferred from
another stage. Opportunity rates ignore bars marked ambiguous.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping, Sequence

LOW_SAMPLE_BARS = 30
FILLS_WARNING_BELOW = 100
WILSON_Z = 1.96

LEGACY_OPPORTUNITY_KEYS = (
    "regime",
    "bars_in_regime",
    "accepted_signals",
    "rejected_signals",
    "trades_executed",
    "signal_rate",
    "trade_rate",
)


def wilson_interval(
    successes: int,
    n: int,
    *,
    z: float = WILSON_Z,
) -> tuple[float, float] | None:
    """Wilson score 95% interval. None when n is 0 (rate is undefined)."""
    if n <= 0:
        return None
    phat = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = phat + z2 / (2.0 * n)
    margin = z * math.sqrt((phat * (1.0 - phat) + z2 / (4.0 * n)) / n)
    low = max(0.0, (centre - margin) / denom)
    high = min(1.0, (centre + margin) / denom)
    return (low, high)


def _flag(row: Mapping[str, Any], key: str) -> bool:
    return row.get(key) is True


def _rate(numer: int, denom: int) -> float | None:
    if denom <= 0:
        return None
    return numer / denom


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def parse_legacy_opportunity_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Accept a previously saved regime_opportunity_summary row."""
    missing = [k for k in LEGACY_OPPORTUNITY_KEYS if k not in row]
    if missing:
        raise ValueError(f"legacy opportunity row missing keys: {missing}")
    return {k: row.get(k) for k in LEGACY_OPPORTUNITY_KEYS}


def _empty_bucket() -> dict[str, Any]:
    return {
        "bars": 0,
        "opportunities_long": 0,
        "opportunities_short": 0,
        "opportunities": 0,
        "ambiguous_opportunities": 0,
        "signals_attempted": 0,
        "signals_accepted": 0,
        "orders_placed": 0,
        "fills": 0,
        "opportunity_fills": 0,
        "rejected_by_reason": defaultdict(int),
        "r_values": [],
        "mfe_values": [],
        "mae_values": [],
        "win_flags": [],
    }


def _consume(bucket: dict[str, Any], row: Mapping[str, Any]) -> None:
    bucket["bars"] += 1
    ambiguous = row.get("opportunity_ambiguous") is True
    if ambiguous:
        bucket["ambiguous_opportunities"] += 1
    else:
        if row.get("opportunity_long") is True:
            bucket["opportunities_long"] += 1
            bucket["opportunities"] += 1
        if row.get("opportunity_short") is True:
            bucket["opportunities_short"] += 1
            bucket["opportunities"] += 1
    if _flag(row, "signal_attempted"):
        bucket["signals_attempted"] += 1
    if _flag(row, "signal_rejected"):
        reason = row.get("rejection_reason")
        label = str(reason) if reason else "UNSPECIFIED"
        bucket["rejected_by_reason"][label] += 1
    if _flag(row, "signal_accepted"):
        bucket["signals_accepted"] += 1
    if _flag(row, "order_placed"):
        bucket["orders_placed"] += 1
    filled = _flag(row, "filled")
    if filled:
        bucket["fills"] += 1
        if not ambiguous and (
            row.get("opportunity_long") is True or row.get("opportunity_short") is True
        ):
            bucket["opportunity_fills"] += 1
        r = row.get("r_multiple")
        if isinstance(r, (int, float)):
            bucket["r_values"].append(float(r))
            bucket["win_flags"].append(float(r) > 0)
        mfe = row.get("mfe")
        mae = row.get("mae")
        if isinstance(mfe, (int, float)):
            bucket["mfe_values"].append(float(mfe))
        if isinstance(mae, (int, float)):
            bucket["mae_values"].append(float(mae))


def _finish_row(
    *,
    group: str,
    group_kind: str,
    bucket: dict[str, Any],
    low_sample_bars: int,
) -> dict[str, Any]:
    bars = int(bucket["bars"])
    opps = int(bucket["opportunities"])
    fills = int(bucket["fills"])
    opp_fills = int(bucket["opportunity_fills"])
    capture = _rate(opp_fills, opps)
    precision = _rate(opp_fills, fills)
    opp_rate = _rate(opps, bars)
    wins = bucket["win_flags"]
    return {
        "group_kind": group_kind,
        "group": group,
        "bars": bars,
        "opportunities_long": bucket["opportunities_long"],
        "opportunities_short": bucket["opportunities_short"],
        "opportunities": opps,
        "ambiguous_opportunities": bucket["ambiguous_opportunities"],
        "opportunity_rate": opp_rate,
        "opportunity_rate_wilson95": wilson_interval(opps, bars),
        "signals_attempted": bucket["signals_attempted"],
        "rejected_by_reason": dict(bucket["rejected_by_reason"]),
        "signals_accepted": bucket["signals_accepted"],
        "orders_placed": bucket["orders_placed"],
        "fills": fills,
        "opportunity_fills": opp_fills,
        "capture_rate": capture,
        "capture_rate_wilson95": wilson_interval(opp_fills, opps),
        "precision": precision,
        "precision_wilson95": wilson_interval(opp_fills, fills),
        "mean_R": _mean(bucket["r_values"]),
        "win_rate": _mean([1.0 if w else 0.0 for w in wins]) if wins else None,
        "win_rate_wilson95": wilson_interval(sum(1 for w in wins if w), len(wins)),
        "mean_MFE": _mean(bucket["mfe_values"]),
        "mean_MAE": _mean(bucket["mae_values"]),
        "low_sample": bars < low_sample_bars,
    }


def _group_rows(
    rows: Sequence[Mapping[str, Any]],
    key_fn,
    *,
    group_kind: str,
    low_sample_bars: int,
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(_empty_bucket)
    for row in rows:
        _consume(buckets[key_fn(row)], row)
    out = [
        _finish_row(group=name, group_kind=group_kind, bucket=bucket, low_sample_bars=low_sample_bars)
        for name, bucket in sorted(buckets.items())
    ]
    return out


def funnel_counters(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Stage totals. Rejected is only rows with signal_rejected=True."""
    bucket = _empty_bucket()
    for row in rows:
        _consume(bucket, row)
    return {
        "bars": bucket["bars"],
        "signals_attempted": bucket["signals_attempted"],
        "signals_accepted": bucket["signals_accepted"],
        "orders_placed": bucket["orders_placed"],
        "fills": bucket["fills"],
        "rejected_by_reason": dict(bucket["rejected_by_reason"]),
        "ambiguous_opportunities": bucket["ambiguous_opportunities"],
    }


def assert_funnel_monotonic(counters: Mapping[str, Any]) -> None:
    bars = int(counters["bars"])
    attempted = int(counters["signals_attempted"])
    accepted = int(counters["signals_accepted"])
    orders = int(counters["orders_placed"])
    fills = int(counters["fills"])
    if not (bars >= attempted >= accepted >= orders >= fills):
        raise AssertionError(
            f"funnel not monotonic: bars={bars} attempted={attempted} "
            f"accepted={accepted} orders={orders} fills={fills}"
        )


def build_regime_funnel(
    rows: Sequence[Mapping[str, Any]],
    *,
    low_sample_bars: int = LOW_SAMPLE_BARS,
    fills_warning_below: int = FILLS_WARNING_BELOW,
) -> dict[str, Any]:
    """Per combined-regime funnel plus coin, fear-and-greed, and volume splits."""
    counters = funnel_counters(rows)
    assert_funnel_monotonic(counters)

    def regime_key(row: Mapping[str, Any]) -> str:
        label = row.get("combined_regime")
        return str(label) if label else "UNLABELED"

    def coin_key(row: Mapping[str, Any]) -> str:
        symbol = row.get("symbol")
        return str(symbol) if symbol else "UNKNOWN_SYMBOL"

    def fng_key(row: Mapping[str, Any]) -> str:
        bucket = row.get("fng_bucket")
        return str(bucket) if bucket else "FNG_NA"

    def vol_key(row: Mapping[str, Any]) -> str:
        state = row.get("vol_state")
        return str(state) if state else "VOLUME_NA"

    by_regime = _group_rows(rows, regime_key, group_kind="combined_regime", low_sample_bars=low_sample_bars)
    totals_bucket = _empty_bucket()
    for row in rows:
        _consume(totals_bucket, row)
    totals = _finish_row(
        group="TOTAL",
        group_kind="total",
        bucket=totals_bucket,
        low_sample_bars=low_sample_bars,
    )
    fills = int(counters["fills"])
    warning = None
    if fills < fills_warning_below:
        warning = (
            f"LOW_FILL_SAMPLE: {fills} fills is below {fills_warning_below}. "
            "Regime-level rates are not reliable."
        )
    return {
        "counters": counters,
        "by_regime": by_regime,
        "totals": totals,
        "by_coin": _group_rows(rows, coin_key, group_kind="coin", low_sample_bars=low_sample_bars),
        "by_fng_bucket": _group_rows(rows, fng_key, group_kind="fng", low_sample_bars=low_sample_bars),
        "by_volume_state": _group_rows(
            rows, vol_key, group_kind="volume", low_sample_bars=low_sample_bars
        ),
        "fills_warning": warning,
        "low_sample_bars": low_sample_bars,
        "fills_warning_below": fills_warning_below,
    }


def funnel_csv_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sections = (
        ("by_regime", report.get("by_regime") or []),
        ("totals", [report["totals"]] if report.get("totals") else []),
        ("by_coin", report.get("by_coin") or []),
        ("by_fng_bucket", report.get("by_fng_bucket") or []),
        ("by_volume_state", report.get("by_volume_state") or []),
    )
    for section, items in sections:
        for item in items:
            flat = dict(item)
            flat["section"] = section
            wilson = flat.get("opportunity_rate_wilson95")
            if isinstance(wilson, tuple):
                flat["opportunity_rate_wilson95_low"] = wilson[0]
                flat["opportunity_rate_wilson95_high"] = wilson[1]
            rows.append(flat)
    return rows


def format_funnel_table(report: Mapping[str, Any]) -> str:
    lines = ["combined_regime  bars  opps  attempted  accepted  orders  fills  low_sample"]
    for row in list(report.get("by_regime") or []) + [report.get("totals") or {}]:
        if not row:
            continue
        lines.append(
            f"{row.get('group')}  {row.get('bars')}  {row.get('opportunities')}  "
            f"{row.get('signals_attempted')}  {row.get('signals_accepted')}  "
            f"{row.get('orders_placed')}  {row.get('fills')}  {row.get('low_sample')}"
        )
    if report.get("fills_warning"):
        lines.append(str(report["fills_warning"]))
    return "\n".join(lines)
