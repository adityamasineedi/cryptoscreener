"""Phase 4 SHORT research quality: coverage, fingerprints, lookahead, reconciliation.

Research-only. Never opens paper/live trades.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.research.config import RESEARCH_ENGINE_VERSION
from app.research.short_research_constants import (
    COMBO_VERSION,
    DIRECTION,
    SOURCE,
    STRATEGY_FINGERPRINT_TEXT,
    STRATEGY_ID,
)
from app.research.trade_fees import DEFAULT_LEVERAGE, DEFAULT_MAKER_FEE, DEFAULT_TAKER_FEE
from app.signals.trade_math import SAME_CANDLE_PRECEDENCE_SL_FIRST

TF_SECONDS: dict[str, int] = {
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}

HISTORICAL_DISCLAIMER = (
    "Historical research only. No live exchange fills. "
    "No guarantee of future profitability."
)

EXECUTION_MODEL: dict[str, Any] = {
    "entry_order_type": "MARKET or LIMIT_RETEST",
    "exit_order_type": "MARKET",
    "entry_model": "MARKET or LIMIT_RETEST",
    "entry_fee_type": "TAKER or MAKER",
    "exit_fee_type": "TAKER",
    "same_candle_policy": SAME_CANDLE_PRECEDENCE_SL_FIRST,
    "fee_model": "TAKER_MAKER",
    "taker_fee": DEFAULT_TAKER_FEE,
    "maker_fee": DEFAULT_MAKER_FEE,
    "fee_currency": "QUOTE",
    "fee_sign": "NEGATIVE_COST",
    "fee_basis": "EXECUTED_NOTIONAL",
    "slippage": 0,
    "leverage": DEFAULT_LEVERAGE,
    "profit_factor_basis": "NET",
    "partial_exits": False,
    "tp_ladder": "TP1_FIRST",
}

FEE_RECON_ABS_TOLERANCE = 0.01
FEE_RECON_REL_TOLERANCE = 1e-9

SAMPLE_BUCKETS = (
    (0, 0, "0 trades"),
    (1, 9, "1–9 trades"),
    (10, 29, "10–29 trades"),
    (30, None, "30+ trades"),
)


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return None  # require explicit timezone (UTC)
    return dt.astimezone(timezone.utc)


def validate_timeframe(timeframe: str) -> dict[str, Any]:
    tf = str(timeframe or "").lower().strip()
    if tf not in TF_SECONDS:
        return {
            "ok": False,
            "timeframe": tf,
            "error": f"unsupported_timeframe:{tf}",
            "expected_seconds": None,
        }
    return {
        "ok": True,
        "timeframe": tf,
        "error": None,
        "expected_seconds": TF_SECONDS[tf],
    }


def inspect_ohlcv_series(
    candles: Sequence[Mapping[str, Any]],
    *,
    timeframe: str,
    symbol: str = "",
    requested_start: str | None = None,
    requested_end: str | None = None,
    data_source: str = "ohlcv_store",
) -> dict[str, Any]:
    """Coverage + integrity report for one TF series. Fail-closed statuses."""
    tf_check = validate_timeframe(timeframe)
    if not tf_check["ok"]:
        return {
            "symbol": str(symbol).upper(),
            "timeframe": timeframe,
            "requested_start": requested_start,
            "requested_end": requested_end,
            "actual_first_candle": None,
            "actual_last_candle": None,
            "bars_requested": None,
            "bars_loaded": 0,
            "bars_used": 0,
            "expected_bar_count": None,
            "missing_bars": 0,
            "duplicate_bars": 0,
            "out_of_order_bars": 0,
            "future_timestamps": 0,
            "non_utc_timestamps": 0,
            "timezone": "UTC",
            "data_source": data_source,
            "data_health_status": "EMPTY",
            "requested_range_available": False,
            "ok": False,
            "errors": [tf_check["error"]],
        }

    step = int(tf_check["expected_seconds"])
    now = datetime.now(timezone.utc)
    times: list[datetime] = []
    non_utc = 0
    future = 0
    for c in candles:
        raw = c.get("time") if isinstance(c, Mapping) else None
        if raw is not None and isinstance(raw, datetime) and raw.tzinfo is None:
            non_utc += 1
            continue
        ts = _parse_ts(raw)
        if ts is None:
            # string/datetime without tz counted as non-UTC/malformed
            if raw is not None:
                non_utc += 1
            continue
        if ts > now:
            future += 1
        times.append(ts)

    bars_loaded = len(times)
    if bars_loaded == 0:
        status = "EMPTY"
        return {
            "symbol": str(symbol).upper(),
            "timeframe": timeframe,
            "requested_start": requested_start,
            "requested_end": requested_end,
            "actual_first_candle": None,
            "actual_last_candle": None,
            "bars_requested": None,
            "bars_loaded": 0,
            "bars_used": 0,
            "expected_bar_count": None,
            "missing_bars": 0,
            "duplicate_bars": 0,
            "out_of_order_bars": 0,
            "future_timestamps": future,
            "non_utc_timestamps": non_utc,
            "timezone": "UTC",
            "data_source": data_source,
            "data_health_status": status,
            "requested_range_available": False,
            "ok": False,
            "errors": ["empty_series"],
        }

    out_of_order = 0
    for i in range(1, len(times)):
        if times[i] < times[i - 1]:
            out_of_order += 1

    # Sort for gap/duplicate analysis (report unsorted count separately)
    ordered = sorted(times)
    seen: set[float] = set()
    duplicates = 0
    gaps = 0
    for t in ordered:
        key = t.timestamp()
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    unique = sorted(seen)
    for i in range(1, len(unique)):
        delta = unique[i] - unique[i - 1]
        if delta > step * 1.5:
            # approximate missing bars between gaps
            gaps += max(0, int(round(delta / step)) - 1)

    first = ordered[0]
    last = ordered[-1]
    span_expected = int((last.timestamp() - first.timestamp()) / step) + 1
    missing = max(0, gaps)

    req_start = _parse_ts(requested_start) if requested_start else None
    req_end = _parse_ts(requested_end) if requested_end else None
    # Date-only strings → start/end of day UTC
    if requested_start and req_start is None:
        try:
            req_start = datetime.fromisoformat(requested_start + "T00:00:00+00:00")
        except ValueError:
            req_start = None
    if requested_end and req_end is None:
        try:
            req_end = datetime.fromisoformat(requested_end + "T23:59:59+00:00")
        except ValueError:
            req_end = None

    expected_bar_count = None
    bars_requested = None
    range_available = True
    if req_start is not None and req_end is not None and req_end >= req_start:
        bars_requested = int((req_end.timestamp() - req_start.timestamp()) / step) + 1
        expected_bar_count = bars_requested
        # Available if first candle <= requested start+1 step and last >= requested end-1 step
        range_available = first <= (req_start + timedelta(seconds=step)) and last >= (
            req_end - timedelta(seconds=step)
        )
        if not range_available:
            # still usable locally, but not the requested calendar interval
            pass
    else:
        expected_bar_count = span_expected

    errors: list[str] = []
    if non_utc:
        errors.append("non_utc_timestamps")
    if future:
        errors.append("future_timestamps")
    if out_of_order:
        errors.append("out_of_order")
    if duplicates:
        errors.append("duplicates")
    if missing:
        errors.append("gaps")

    if bars_loaded == 0:
        status = "EMPTY"
    elif non_utc or future:
        status = "OUT_OF_ORDER" if out_of_order else "INCOMPLETE"
    elif out_of_order:
        status = "OUT_OF_ORDER"
    elif duplicates:
        status = "DUPLICATES_DETECTED"
    elif missing:
        status = "GAPS_DETECTED"
    elif not range_available and req_start is not None:
        status = "INCOMPLETE"
    elif bars_loaded < 50:
        status = "INSUFFICIENT_HISTORY"
    else:
        status = "HEALTHY"

    ok = status == "HEALTHY" and not errors
    return {
        "symbol": str(symbol).upper(),
        "timeframe": timeframe,
        "requested_start": requested_start,
        "requested_end": requested_end,
        "actual_first_candle": first.isoformat(),
        "actual_last_candle": last.isoformat(),
        "bars_requested": bars_requested,
        "bars_loaded": bars_loaded,
        "bars_used": bars_loaded - duplicates,
        "expected_bar_count": expected_bar_count,
        "missing_bars": missing,
        "duplicate_bars": duplicates,
        "out_of_order_bars": out_of_order,
        "future_timestamps": future,
        "non_utc_timestamps": non_utc,
        "timezone": "UTC",
        "data_source": data_source,
        "data_health_status": status,
        "requested_range_available": bool(range_available) if req_start else None,
        "ok": ok,
        "errors": errors,
    }


def dataset_fingerprint(
    candles_by_tf: Mapping[str, Sequence[Mapping[str, Any]]],
) -> str:
    """Stable hash of closed OHLCV used for a research run."""
    parts: list[str] = []
    for tf in sorted(candles_by_tf.keys()):
        series = candles_by_tf[tf] or []
        for c in series:
            if not isinstance(c, Mapping):
                continue
            ts = _parse_ts(c.get("time"))
            stamp = ts.isoformat() if ts else str(c.get("time"))
            parts.append(
                f"{tf}|{stamp}|{c.get('open')}|{c.get('high')}|{c.get('low')}|{c.get('close')}|{c.get('volume')}"
            )
    raw = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def configuration_fingerprint(
    *,
    window: Mapping[str, Any],
    thresholds: Mapping[str, Any] | None = None,
    symbols: Sequence[str] | None = None,
    run_oos: bool = True,
    execution_model: Mapping[str, Any] | None = None,
) -> str:
    payload = {
        "strategy_id": STRATEGY_ID,
        "combo_version": COMBO_VERSION,
        "source": SOURCE,
        "direction": DIRECTION,
        "strategy_fingerprint": STRATEGY_FINGERPRINT_TEXT,
        "engine_version": RESEARCH_ENGINE_VERSION,
        "window": dict(window),
        "thresholds": dict(thresholds or {}),
        "symbols": sorted(str(s).upper() for s in (symbols or [])),
        "run_oos": bool(run_oos),
        "execution_model": dict(execution_model or EXECUTION_MODEL),
    }
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def sample_size_assessment(trade_count: int) -> dict[str, Any]:
    n = int(trade_count or 0)
    bucket = "0 trades"
    for lo, hi, label in SAMPLE_BUCKETS:
        if hi is None and n >= lo:
            bucket = label
            break
        if hi is not None and lo <= n <= hi:
            bucket = label
            break
    if n <= 0:
        sample_class = "NO_TRADES"
    elif n <= 9:
        sample_class = "VERY_SMALL"
    elif n <= 29:
        sample_class = "SMALL"
    else:
        sample_class = "USABLE_FOR_REVIEW"
    warning = {
        "rule": "minimum_sample_size",
        "actual": n,
        "required": ">= 30",
        "passed": n >= 30,
        "severity": "WARNING" if n < 30 else "INFO",
        "bucket": bucket,
        "sample_class": sample_class,
        "overridable": False,
    }
    if n == 0:
        quality = "INSUFFICIENT_SAMPLE"
    elif n < 10:
        quality = "INSUFFICIENT_SAMPLE"
    elif n < 30:
        quality = "PROMISING_BUT_LOW_SAMPLE"
    else:
        quality = "RESEARCH_ONLY"
    return {
        "trade_count": n,
        "bucket": bucket,
        "sample_class": sample_class,
        "research_quality_hint": quality,
        "warnings": [warning],
    }


def validate_base_oos_split(
    *,
    base_start: str,
    base_end: str,
    oos_start: str,
    oos_end: str,
    base_trades: Sequence[Mapping[str, Any]] | None = None,
    oos_trades: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Require disjoint base/OOS calendar windows and trade sets."""

    def _day(s: str) -> datetime | None:
        try:
            return datetime.fromisoformat(str(s)[:10] + "T00:00:00+00:00")
        except ValueError:
            return None

    b0, b1 = _day(base_start), _day(base_end)
    o0, o1 = _day(oos_start), _day(oos_end)
    overlap_bars = 0
    if b0 and b1 and o0 and o1:
        latest_start = max(b0, o0)
        earliest_end = min(b1, o1)
        if latest_start <= earliest_end:
            overlap_bars = int((earliest_end - latest_start).total_seconds() // 86400) + 1

    def _entry_day(t: Mapping[str, Any]) -> datetime | None:
        raw = t.get("entry_time") or t.get("entry_timestamp")
        if raw is None:
            return None
        try:
            return datetime.fromisoformat(str(raw)[:10] + "T00:00:00+00:00")
        except ValueError:
            return None

    def _in_window(
        t: Mapping[str, Any], start: datetime | None, end: datetime | None
    ) -> bool:
        if start is None or end is None:
            return True
        day = _entry_day(t)
        if day is None:
            return True
        return start <= day <= end

    def _keys(
        trades: Sequence[Mapping[str, Any]] | None,
        start: datetime | None,
        end: datetime | None,
    ) -> set[str]:
        out: set[str] = set()
        for t in trades or []:
            if not _in_window(t, start, end):
                continue
            key = (
                f"{t.get('entry_time') or t.get('entry_timestamp')}"
                f"|{t.get('exit_time') or t.get('exit_timestamp')}"
                f"|{t.get('entry_price')}|{t.get('exit_price')}"
            )
            out.add(key)
        return out

    # Contiguous splits (base_end < oos_start) are valid; overlapping calendars are not.
    if b1 and o0 and b1 < o0:
        overlap_bars = 0

    base_keys = _keys(base_trades, b0, b1)
    oos_keys = _keys(oos_trades, o0, o1)
    overlap_trades = len(base_keys & oos_keys)
    invalid = overlap_bars > 0 or overlap_trades > 0

    return {
        "base_start": base_start,
        "base_end": base_end,
        "oos_start": oos_start,
        "oos_end": oos_end,
        "overlap_bars": overlap_bars,
        "overlap_trades": overlap_trades,
        "valid": not invalid,
        "oos_status": "INVALID_SPLIT" if invalid else "OK",
        "research_quality": "REVIEW_REQUIRED" if invalid else None,
    }


def fee_reconciliation_tolerance(reported_fees: float | None) -> float:
    """Documented absolute tolerance for fee aggregate comparison."""
    base = abs(float(reported_fees or 0.0)) * FEE_RECON_REL_TOLERANCE
    return max(FEE_RECON_ABS_TOLERANCE, base)


def _trade_fee_value(trade: Mapping[str, Any]) -> float:
    fee = trade.get("fees")
    if fee is None:
        fee = trade.get("total_fee")
    if fee is None:
        fee = trade.get("fee_total_usd")
    if fee is None:
        entry = trade.get("entry_fee")
        exit_ = trade.get("exit_fee")
        if entry is not None or exit_ is not None:
            fee = float(entry or 0.0) + float(exit_ or 0.0)
    return float(fee or 0.0)


def _detect_fee_sign(fee_sum: float, trades: Sequence[Mapping[str, Any]]) -> str:
    for t in trades:
        explicit = str(t.get("fee_sign") or "").upper()
        if explicit in ("NEGATIVE_COST", "POSITIVE_COST"):
            return explicit
    if fee_sum < 0:
        return "NEGATIVE_COST"
    if fee_sum > 0:
        return "POSITIVE_COST"
    return str(EXECUTION_MODEL.get("fee_sign") or "NEGATIVE_COST")


def reconcile_research_blotter(
    trades: Sequence[Mapping[str, Any]],
    *,
    starting_equity: float = 1000.0,
    reported_net_pnl: float | None = None,
    reported_fees: float | None = None,
    reported_avg_r: float | None = None,
    reported_profit_factor: float | None = None,
    profit_factor_basis: str = "NET",
) -> dict[str, Any]:
    """Reconcile trade-level PnL/fees/equity/R/PF/DD/streak."""
    ordered = list(trades)
    net_series: list[float] = []
    fee_sum = 0.0
    gross_wins = 0.0
    gross_losses = 0.0
    net_wins = 0.0
    net_losses = 0.0
    r_vals: list[float] = []
    failed_trade_ids: list[str] = []

    for t in ordered:
        net = t.get("net_pnl")
        if net is None:
            net = t.get("net_pnl_usd")
        if net is None:
            net = t.get("r_net")
        net_f = float(net or 0.0)
        net_series.append(net_f)
        fee_f = _trade_fee_value(t)
        fee_sum += fee_f
        gross = t.get("gross_pnl")
        if gross is None:
            gross = t.get("gross_pnl_usd")
        if gross is None:
            gross = net_f
        g = float(gross or 0.0)
        if g > 0:
            gross_wins += g
        else:
            gross_losses += abs(g)
        if net_f > 0:
            net_wins += net_f
        else:
            net_losses += abs(net_f)
        r = t.get("r_net")
        if r is None:
            r = t.get("r_multiple")
        if r is not None:
            r_vals.append(float(r))

    fee_sign = _detect_fee_sign(fee_sum, ordered)
    # Per-trade identity: gross ± fees = net (sign-aware).
    for idx, t in enumerate(ordered):
        gross = t.get("gross_pnl")
        if gross is None:
            gross = t.get("gross_pnl_usd")
        net = t.get("net_pnl")
        if net is None:
            net = t.get("net_pnl_usd")
        if gross is None or net is None:
            continue
        fee_f = _trade_fee_value(t)
        if fee_sign == "POSITIVE_COST":
            expected = float(gross) - fee_f
        else:
            expected = float(gross) + fee_f
        if abs(expected - float(net)) > fee_reconciliation_tolerance(fee_f):
            tid = str(
                t.get("trade_id")
                or t.get("id")
                or t.get("trade_no")
                or f"idx:{idx}"
            )
            failed_trade_ids.append(tid)

    sum_net = sum(net_series)
    equity = float(starting_equity)
    peak = equity
    max_dd = 0.0
    curve = [equity]
    for n in net_series:
        equity += n
        curve.append(equity)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    streak = cur = 0
    for n in net_series:
        if n < 0:
            cur += 1
            streak = max(streak, cur)
        else:
            cur = 0

    avg_r = (sum(r_vals) / len(r_vals)) if r_vals else None
    if profit_factor_basis.upper() == "GROSS":
        pf = (gross_wins / gross_losses) if gross_losses > 0 else None
    else:
        pf = (net_wins / net_losses) if net_losses > 0 else None

    def _close(a: float | None, b: float | None, tol: float = 1e-6) -> bool:
        if a is None or b is None:
            return a is None and b is None
        return abs(float(a) - float(b)) <= tol

    fee_tol = fee_reconciliation_tolerance(reported_fees)
    fee_diff = (
        None
        if reported_fees is None
        else float(reported_fees) - fee_sum
    )
    fees_match = (
        True
        if reported_fees is None
        else abs(float(fee_diff or 0.0)) <= fee_tol
    )
    # Aggregate identity under the detected sign convention.
    sum_gross = sum(
        float(
            (t.get("gross_pnl") if t.get("gross_pnl") is not None else t.get("gross_pnl_usd"))
            or 0.0
        )
        for t in ordered
    )
    if fee_sign == "POSITIVE_COST":
        identity_net = sum_gross - fee_sum
    else:
        identity_net = sum_gross + fee_sum
    identity_ok = _close(identity_net, sum_net, fee_tol) if ordered else True

    checks = {
        "net_pnl_matches": (
            True
            if reported_net_pnl is None
            else _close(sum_net, float(reported_net_pnl), fee_tol)
        ),
        "fees_match": fees_match and identity_ok and not failed_trade_ids,
        "equity_matches": _close(
            starting_equity + sum_net,
            curve[-1] if curve else starting_equity,
            fee_tol,
        ),
        "avg_r_matches": (
            True if reported_avg_r is None or avg_r is None else _close(avg_r, float(reported_avg_r), 1e-4)
        ),
        "profit_factor_matches": (
            True
            if reported_profit_factor is None or pf is None
            else _close(pf, float(reported_profit_factor), 1e-4)
        ),
        "gross_fees_net_identity": identity_ok,
    }
    # Chronological trade order: entry_time non-decreasing when present
    prev_entry = None
    order_ok = True
    for t in ordered:
        et = t.get("entry_time") or t.get("entry_timestamp") or t.get("signal_time")
        if et is None:
            continue
        if prev_entry is not None and str(et) < str(prev_entry):
            order_ok = False
            break
        prev_entry = et
    checks["trade_order_ok"] = order_ok

    fee_status = "PASS" if checks["fees_match"] else "FAIL"
    fee_diagnostics = {
        "status": fee_status,
        "reported_fees": reported_fees,
        "sum_trade_fees": fee_sum,
        "difference": fee_diff,
        "tolerance": fee_tol,
        "currency": "QUOTE",
        "basis": "EXECUTED_NOTIONAL",
        "fee_sign": fee_sign,
        "failed_trade_ids": failed_trade_ids,
        "gross_plus_fees_equals_net": identity_ok,
    }
    ok = all(checks.values())
    return {
        "sum_net_pnl": sum_net,
        "sum_gross_pnl": sum_gross,
        "sum_fees": fee_sum,
        "starting_equity": starting_equity,
        "ending_equity": curve[-1] if curve else starting_equity,
        "equity_curve": curve,
        "avg_r": avg_r,
        "profit_factor": pf,
        "profit_factor_basis": profit_factor_basis.upper(),
        "max_drawdown": max_dd,
        "max_losing_streak": streak,
        "checks": checks,
        "equity_reconciliation": "PASS" if checks["equity_matches"] else "FAIL",
        "fee_reconciliation": fee_status,
        "fee_reconciliation_diagnostics": fee_diagnostics,
        "trade_order_reconciliation": "PASS" if order_ok else "FAIL",
        "ok": ok,
    }


def lookahead_audit_checklist(
    *,
    engine_uses_closed_pre_as_of: bool = True,
    htf_uses_closed_bars: bool = True,
    bos_requires_close: bool = True,
    stop_tp_known_at_entry: bool = True,
    exits_scan_later_bars: bool = True,
) -> dict[str, Any]:
    """Document lookahead safety. Any unknown → REVIEW_REQUIRED."""
    checks = [
        {
            "rule": "trend_pre_entry_only",
            "passed": engine_uses_closed_pre_as_of,
            "detail": "SignalEngine as_of truncates candles",
        },
        {
            "rule": "bos_completed_candle",
            "passed": bos_requires_close,
            "detail": "detect_bos requires close beyond swing",
        },
        {
            "rule": "htf_completed_bars",
            "passed": htf_uses_closed_bars,
            "detail": "htf_trends_for_setup_bar uses closed HTF only",
        },
        {
            "rule": "stop_tp_known_at_entry",
            "passed": stop_tp_known_at_entry,
            "detail": "stop/targets computed at entry candidate",
        },
        {
            "rule": "exits_later_candles_only",
            "passed": exits_scan_later_bars,
            "detail": "backtest manages open trade on subsequent bars",
        },
    ]
    all_known = all(c["passed"] is True for c in checks)
    return {
        "checks": checks,
        "lookahead_safe": all_known,
        "research_quality": "RESEARCH_ONLY" if all_known else "REVIEW_REQUIRED",
        "disclaimer": HISTORICAL_DISCLAIMER,
    }


def classify_research_quality(
    *,
    trade_count: int,
    health_ok: bool,
    oos_split_valid: bool,
    lookahead_safe: bool,
    state: str,
    windows_ok: bool = True,
    forensic_pass: bool = True,
) -> str:
    if (
        not health_ok
        or not oos_split_valid
        or not lookahead_safe
        or not windows_ok
        or not forensic_pass
    ):
        return "REVIEW_REQUIRED"
    if str(state).upper() == "RESEARCH_REJECTED":
        return "RESEARCH_REJECTED"
    if str(state).upper() == "OOS_FAILED":
        return "OOS_FAILED"
    sample = sample_size_assessment(trade_count)
    if str(state).upper() == "SHORT_RESEARCH_CANDIDATE":
        if trade_count < 10:
            return "INSUFFICIENT_SAMPLE"
        if trade_count < 30:
            return "PROMISING_BUT_LOW_SAMPLE"
        return "SHORT_RESEARCH_CANDIDATE"
    return sample["research_quality_hint"]
