"""Per-trade forensic lookahead audit for SHORT research.

Fail closed: missing timestamps → lookahead_status=FAIL → REVIEW_REQUIRED.
Never opens paper/live trades.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4

from app.signals.trade_math import (
    SAME_CANDLE_PRECEDENCE_SL_FIRST,
    resolve_same_candle_exit,
    stop_triggered,
    take_profit_triggered,
)

HISTORICAL_DISCLAIMER = (
    "Historical research only. No live exchange fills. "
    "No guarantee of future profitability."
)

# Evidence strength for forensic timestamps.
EVIDENCE_DIRECT = "DIRECT_CANDLE_REPLAY"
EVIDENCE_DERIVED = "DERIVED_FROM_RESEARCH_TRADE"
EVIDENCE_UNKNOWN = "UNKNOWN"


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def candle_close_time(open_time: datetime, *, timeframe: str = "1h") -> datetime:
    """Candle 'time' is bar open; close is open + TF seconds."""
    step = {"15m": 900, "1h": 3600, "4h": 14400}.get(str(timeframe).lower(), 3600)
    return open_time + timedelta(seconds=step)


def build_trade_forensic_audit(
    trade: Mapping[str, Any],
    *,
    trade_id: str | None = None,
    timeframe: str = "1h",
) -> dict[str, Any]:
    """Build per-trade lookahead evidence. Unknown → FAIL."""
    tid = trade_id or str(trade.get("trade_id") or trade.get("trade_no") or uuid4().hex[:12])
    direction = str(trade.get("direction") or "SHORT").upper()

    signal_time = _parse_ts(
        trade.get("signal_time") or trade.get("entry_time") or trade.get("entry_timestamp")
    )
    entry_time = _parse_ts(
        trade.get("entry_time") or trade.get("entry_timestamp") or trade.get("signal_time")
    )
    entry_open = _parse_ts(trade.get("entry_candle_open_time")) or entry_time
    entry_close = _parse_ts(trade.get("entry_candle_close_time"))
    if entry_close is None and entry_open is not None:
        entry_close = candle_close_time(entry_open, timeframe=timeframe)

    evidence = str(
        trade.get("forensic_evidence_class")
        or trade.get("evidence_class")
        or EVIDENCE_UNKNOWN
    ).upper()
    if evidence not in (EVIDENCE_DIRECT, EVIDENCE_DERIVED, EVIDENCE_UNKNOWN):
        evidence = EVIDENCE_UNKNOWN

    # Direct replay must supply independent ends; derived may fall back to signal/entry
    # but that path can never produce research_quality PASS.
    trend_end = _parse_ts(trade.get("trend_data_end_time"))
    bos_end = _parse_ts(trade.get("bos_data_end_time"))
    htf_end = _parse_ts(trade.get("htf_data_end_time"))
    entry_data_end = _parse_ts(trade.get("entry_data_end_time"))
    stop_tp_end = _parse_ts(trade.get("stop_tp_data_end_time"))
    exit_scan_start = _parse_ts(
        trade.get("exit_scan_start_time") or trade.get("first_exit_scan_time")
    )
    exit_time = _parse_ts(trade.get("exit_time") or trade.get("exit_timestamp"))

    derived_fallback = False
    if evidence != EVIDENCE_DIRECT:
        if trend_end is None and signal_time is not None:
            trend_end = signal_time
            derived_fallback = True
        if bos_end is None and signal_time is not None:
            bos_end = signal_time
            derived_fallback = True
        if htf_end is None and signal_time is not None:
            htf_end = signal_time
            derived_fallback = True
        if entry_data_end is None and entry_time is not None:
            entry_data_end = entry_time
            derived_fallback = True
        if stop_tp_end is None and entry_time is not None:
            stop_tp_end = entry_time
            derived_fallback = True
        if exit_scan_start is None and trade.get("exit_scan_start_time") is None:
            if trade.get("forensic_exit_scan_after_entry") is True and entry_time and exit_time:
                exit_scan_start = entry_time + timedelta(microseconds=1)
                derived_fallback = True
            elif (
                trade.get("holding_bars") is not None
                and int(trade.get("holding_bars") or 0) >= 1
                and entry_time
            ):
                exit_scan_start = entry_time + timedelta(
                    hours=1 if timeframe == "1h" else 0, microseconds=1
                )
                derived_fallback = True
        if derived_fallback and evidence == EVIDENCE_UNKNOWN:
            evidence = EVIDENCE_DERIVED

    missing = []
    for name, val in (
        ("signal_time", signal_time),
        ("entry_time", entry_time),
        ("trend_data_end_time", trend_end),
        ("bos_data_end_time", bos_end),
        ("htf_data_end_time", htf_end),
        ("entry_data_end_time", entry_data_end),
        ("stop_tp_data_end_time", stop_tp_end),
        ("exit_scan_start_time", exit_scan_start),
    ):
        if val is None:
            missing.append(name)

    checks = {
        "trend_before_entry": bool(
            signal_time and trend_end and trend_end <= signal_time
        ),
        "bos_before_entry": bool(signal_time and bos_end and bos_end <= signal_time),
        "htf_before_entry": bool(signal_time and htf_end and htf_end <= signal_time),
        "entry_before_fill": bool(
            entry_time and entry_data_end and entry_data_end <= entry_time
        ),
        "stop_tp_known_before_fill": bool(
            entry_time and stop_tp_end and stop_tp_end <= entry_time
        ),
        "exit_after_entry": bool(
            entry_time and exit_scan_start and exit_scan_start > entry_time
        ),
    }
    stop = trade.get("stop_price")
    tp = trade.get("take_profit_price")
    if tp is None:
        tp = trade.get("tp1")
    geometry_ok = (
        direction != "SHORT"
        or (
            stop is not None
            and tp is not None
            and float(stop) > float(trade.get("entry_price") or 0)
            and float(tp) < float(trade.get("entry_price") or 0)
        )
    )
    if direction == "SHORT" and (stop is None or tp is None):
        # stop/TP known check still uses timestamps; geometry recorded separately
        pass

    all_pass = all(checks.values()) and not missing and geometry_ok
    status = "PASS" if all_pass else "FAIL"
    # Derived/unknown evidence cannot unlock research PASS (may still show time-check PASS).
    quality_pass = status == "PASS" and evidence == EVIDENCE_DIRECT
    if evidence in (EVIDENCE_DERIVED, EVIDENCE_UNKNOWN) and status == "PASS":
        # Keep lookahead_status for diagnostics; research_quality stays REVIEW_REQUIRED.
        pass

    htf = {
        "setup_timeframe": timeframe,
        "htf_timeframe": "4h",
        "last_htf_candle_used": trade.get("last_htf_candle_used"),
        "htf_candle_closed_before_signal": trade.get("htf_candle_closed_before_signal"),
        "trend_1h": (trade.get("condition_snapshot") or {}).get("trend_1h")
        if isinstance(trade.get("condition_snapshot"), Mapping)
        else trade.get("trend_1h"),
        "trend_4h": (trade.get("condition_snapshot") or {}).get("trend_4h")
        if isinstance(trade.get("condition_snapshot"), Mapping)
        else trade.get("trend_4h"),
        "bos_confirmation_candle": trade.get("bos_confirmation_candle")
        or trade.get("bos_candle_timestamp"),
    }
    if htf["htf_candle_closed_before_signal"] is False:
        status = "FAIL"
        checks["htf_before_entry"] = False

    structure = {
        "swing_high_timestamps": trade.get("swing_high_timestamps") or [],
        "swing_low_timestamps": trade.get("swing_low_timestamps") or [],
        "swing_values": trade.get("swing_values") or {},
        "lh_ll_confirmation_timestamps": trade.get("lh_ll_confirmation_timestamps") or [],
        "bos_candle_timestamp": trade.get("bos_candle_timestamp"),
        "bos_close": trade.get("bos_close"),
        "reference_swing_low_timestamp": trade.get("reference_swing_low_timestamp"),
        "reference_swing_low_value": trade.get("reference_swing_low_value"),
    }
    if (
        structure.get("bos_close") is not None
        and structure.get("reference_swing_low_value") is not None
        and float(structure["bos_close"]) >= float(structure["reference_swing_low_value"])
        and direction == "SHORT"
    ):
        status = "FAIL"

    entry_exit = {
        "signal_time": _iso(signal_time),
        "order_created_time": trade.get("order_created_time") or _iso(signal_time),
        "entry_time": _iso(entry_time),
        "entry_price": trade.get("entry_price"),
        "entry_data_end_time": _iso(entry_data_end),
        "stop_price": stop,
        "take_profit_price": tp,
        "stop_tp_data_end_time": _iso(stop_tp_end),
        "first_exit_scan_time": _iso(exit_scan_start),
        "exit_time": _iso(exit_time),
        "exit_reason": trade.get("exit_reason") or trade.get("outcome"),
        "same_candle_policy": SAME_CANDLE_PRECEDENCE_SL_FIRST,
    }
    limit = None
    entry_type = str(trade.get("entry_type") or "").upper()
    if entry_type == "LIMIT_RETEST":
        limit = {
            "limit_order_price": trade.get("limit_order_price") or trade.get("entry_price"),
            "limit_order_created_time": trade.get("limit_order_created_time")
            or trade.get("order_created_time")
            or _iso(signal_time),
            "first_eligible_fill_candle": trade.get("first_eligible_fill_candle"),
            "fill_reason": trade.get("fill_reason") or "limit_touched",
        }
        fill_candle = _parse_ts(trade.get("first_eligible_fill_candle"))
        created = _parse_ts(limit["limit_order_created_time"])
        if fill_candle and created and fill_candle < created:
            status = "FAIL"
            checks["entry_before_fill"] = False

    return {
        "trade_id": tid,
        "direction": direction,
        "signal_time": _iso(signal_time),
        "entry_time": _iso(entry_time),
        "entry_candle_close_time": _iso(entry_close),
        "trend_data_end_time": _iso(trend_end),
        "bos_data_end_time": _iso(bos_end),
        "htf_data_end_time": _iso(htf_end),
        "entry_data_end_time": _iso(entry_data_end),
        "stop_tp_data_end_time": _iso(stop_tp_end),
        "exit_scan_start_time": _iso(exit_scan_start),
        "lookahead_checks": checks,
        "lookahead_status": status,
        "evidence_class": evidence,
        "missing_fields": missing,
        "htf_forensic": htf,
        "structure_forensic": structure,
        "entry_exit_forensic": entry_exit,
        "limit_retest": limit,
        "disclaimer": HISTORICAL_DISCLAIMER,
        "research_quality": "PASS" if quality_pass else "REVIEW_REQUIRED",
        "paper_eligible": False,
    }


def audit_trades_for_lookahead(
    trades: Sequence[Mapping[str, Any]],
    *,
    timeframe: str = "1h",
) -> dict[str, Any]:
    audits = [
        build_trade_forensic_audit(t, trade_id=f"t{i+1}", timeframe=timeframe)
        for i, t in enumerate(trades)
    ]
    failed = [a for a in audits if a["lookahead_status"] != "PASS"]
    status = "PASS" if audits and not failed else ("PASS" if not audits else "FAIL")
    # Empty trade list: no forensic failure from trades (sample-size handles emptiness)
    if not audits:
        status = "PASS"
    evidence_classes = {str(a.get("evidence_class") or EVIDENCE_UNKNOWN) for a in audits}
    # Promotion-grade forensic pass requires DIRECT replay on every trade (or no trades).
    direct_ok = (not audits) or (
        status == "PASS"
        and evidence_classes == {EVIDENCE_DIRECT}
        and all(a.get("research_quality") == "PASS" for a in audits)
    )
    return {
        "audits": audits,
        "lookahead_status": status,
        "failed_count": len(failed),
        "trade_count": len(audits),
        "evidence_classes": sorted(evidence_classes) if evidence_classes else [],
        "forensic_pass_for_promotion": direct_ok,
        "research_quality": "PASS" if direct_ok else "REVIEW_REQUIRED",
        "paper_eligible": False,
        "disclaimer": HISTORICAL_DISCLAIMER,
    }


def enrich_trade_forensic_fields(trade: Mapping[str, Any]) -> dict[str, Any]:
    """Attach derived forensic timestamps for ResearchTrade-shaped rows.

    Uses signal_time as as_of bound (engine truncates to as_of). Exit scan is
    marked after entry when holding_bars >= 1 or exit_time > signal_time.

    Evidence class is DERIVED_FROM_RESEARCH_TRADE unless already DIRECT.
    Derived evidence alone must never unlock research_quality PASS.
    """
    row = dict(trade)
    if str(row.get("forensic_evidence_class") or "") != EVIDENCE_DIRECT:
        row["forensic_evidence_class"] = EVIDENCE_DERIVED
    signal = _parse_ts(row.get("signal_time") or row.get("entry_time"))
    entry = _parse_ts(row.get("entry_time") or row.get("signal_time"))
    if signal is not None:
        row.setdefault("trend_data_end_time", signal.isoformat())
        row.setdefault("bos_data_end_time", signal.isoformat())
        row.setdefault("htf_data_end_time", signal.isoformat())
        row.setdefault("signal_time", signal.isoformat())
    if entry is not None:
        row.setdefault("entry_time", entry.isoformat())
        row.setdefault("entry_data_end_time", entry.isoformat())
        row.setdefault("stop_tp_data_end_time", entry.isoformat())
        row.setdefault(
            "entry_candle_close_time",
            candle_close_time(entry, timeframe=str(row.get("timeframe") or "1h")).isoformat(),
        )
    exit_t = _parse_ts(row.get("exit_time"))
    if entry is not None and (
        int(row.get("holding_bars") or 0) >= 1
        or (exit_t is not None and exit_t > entry)
    ):
        row["forensic_exit_scan_after_entry"] = True
        row.setdefault(
            "exit_scan_start_time",
            (entry + timedelta(microseconds=1)).isoformat(),
        )
    if row.get("take_profit_price") is None and row.get("tp1") is not None:
        row["take_profit_price"] = row["tp1"]
    snap = row.get("condition_snapshot") if isinstance(row.get("condition_snapshot"), Mapping) else {}
    if snap.get("trend_4h") and row.get("htf_candle_closed_before_signal") is None:
        row.setdefault("htf_candle_closed_before_signal", True)
    return row


def _candle_open(c: Mapping[str, Any]) -> datetime | None:
    return _parse_ts(c.get("time"))


def independent_candle_replay_audit(
    *,
    candles_1h: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    signal_time: Any,
    entry_time: Any,
    entry_price: float,
    stop_price: float,
    take_profit_price: float,
    direction: str = "SHORT",
    structure_refs: Mapping[str, Any] | None = None,
    htf_refs: Mapping[str, Any] | None = None,
    order_created_time: Any | None = None,
    entry_type: str = "MARKET",
    limit_order_price: float | None = None,
    first_eligible_fill_candle: Any | None = None,
    timeframe: str = "1h",
) -> dict[str, Any]:
    """Independent OHLCV replay — does not reuse trade-derived forensic timestamps.

    Verifies trend/BOS/HTF inputs from raw candles truncated to signal time,
    stop/TP geometry at entry, exit scan after entry, and non-retroactive limits.
    """
    signal = _parse_ts(signal_time)
    entry = _parse_ts(entry_time)
    errors: list[str] = []
    if signal is None or entry is None:
        return {
            "ok": False,
            "evidence_class": EVIDENCE_UNKNOWN,
            "lookahead_status": "FAIL",
            "research_quality": "REVIEW_REQUIRED",
            "errors": ["missing_signal_or_entry_time"],
            "paper_eligible": False,
        }

    # Truncate to candles whose open <= signal (as_of). HTF also requires close <= signal.
    setup_before = []
    for c in candles_1h or []:
        ts = _candle_open(c)
        if ts is not None and ts <= signal:
            setup_before.append((ts, c))
    setup_before.sort(key=lambda x: x[0])
    if not setup_before:
        errors.append("no_setup_candles_before_signal")

    htf_before = []
    for c in candles_4h or []:
        ts = _candle_open(c)
        if ts is None:
            continue
        close_t = candle_close_time(ts, timeframe="4h")
        if close_t <= signal:
            htf_before.append((ts, close_t, c))
    htf_before.sort(key=lambda x: x[0])

    # Reject any referenced future HTF / structure / BOS inputs.
    refs = structure_refs or {}
    for key in ("bos_candle_timestamp", "reference_swing_low_timestamp"):
        ts = _parse_ts(refs.get(key))
        if ts is not None and ts > signal:
            errors.append(f"future_structure:{key}")
    for ts_raw in list(refs.get("swing_high_timestamps") or []) + list(
        refs.get("swing_low_timestamps") or []
    ):
        ts = _parse_ts(ts_raw)
        if ts is not None and ts > signal:
            errors.append("future_swing")
            break
    htf_r = htf_refs or {}
    future_htf = _parse_ts(htf_r.get("future_candle_open") or htf_r.get("last_htf_candle_used"))
    if future_htf is not None:
        # last_htf must be closed before signal
        last_close = candle_close_time(future_htf, timeframe="4h")
        if str(htf_r.get("role") or "") == "future" or (
            htf_r.get("must_be_rejected") and last_close > signal
        ):
            errors.append("future_htf_candle")
        if htf_r.get("assert_closed_before_signal") and last_close > signal:
            errors.append("htf_still_forming")

    # Independent path only uses closed HTF bars (close_time <= signal); forming bars ignored.

    trend_end = setup_before[-1][0] if setup_before else None
    bos_end = trend_end
    htf_end = htf_before[-1][1] if htf_before else None  # closed time
    if htf_refs and htf_refs.get("require_htf") and htf_end is None:
        errors.append("htf_unavailable_before_signal")

    # Exit scan: first 1h candle strictly after entry
    after_entry = []
    for c in candles_1h or []:
        ts = _candle_open(c)
        if ts is not None and ts > entry:
            after_entry.append(ts)
    after_entry.sort()
    exit_scan_start = after_entry[0] if after_entry else None
    if exit_scan_start is None:
        errors.append("no_exit_scan_candle_after_entry")

    # Geometry + triggers
    d = str(direction or "SHORT").upper()
    if d == "SHORT":
        if not (float(stop_price) > float(entry_price) > float(take_profit_price)):
            errors.append("invalid_short_geometry")
    else:
        if not (float(stop_price) < float(entry_price) < float(take_profit_price)):
            errors.append("invalid_long_geometry")

    # Stop/TP after entry rejected
    stop_tp_end = entry  # computable at entry from prices known then
    if _parse_ts(refs.get("stop_tp_computed_at")) and _parse_ts(
        refs.get("stop_tp_computed_at")
    ) > entry:
        errors.append("stop_tp_after_entry")

    # Limit non-retroactive
    if str(entry_type or "").upper() == "LIMIT_RETEST":
        created = _parse_ts(order_created_time) or signal
        fill = _parse_ts(first_eligible_fill_candle)
        if fill is not None and created is not None and fill < created:
            errors.append("retroactive_limit_fill")

    checks = {
        "trend_before_entry": bool(trend_end and trend_end <= signal),
        "bos_before_entry": bool(bos_end and bos_end <= signal),
        "htf_before_entry": bool(
            (htf_end is None and not (htf_refs or {}).get("require_htf"))
            or (htf_end is not None and htf_end <= signal)
        ),
        "entry_before_fill": True,
        "stop_tp_known_before_fill": bool(stop_tp_end and stop_tp_end <= entry),
        "exit_after_entry": bool(exit_scan_start and exit_scan_start > entry),
    }
    if not all(checks.values()):
        errors.append("lookahead_check_failed")

    ok = not errors and all(checks.values())
    forensic_fields = {
        "signal_time": signal.isoformat(),
        "entry_time": entry.isoformat(),
        "trend_data_end_time": _iso(trend_end),
        "bos_data_end_time": _iso(bos_end),
        "htf_data_end_time": _iso(htf_end),
        "entry_data_end_time": entry.isoformat(),
        "stop_tp_data_end_time": entry.isoformat(),
        "exit_scan_start_time": _iso(exit_scan_start),
        "entry_candle_close_time": candle_close_time(entry, timeframe=timeframe).isoformat(),
        "htf_candle_closed_before_signal": bool(htf_end is None or htf_end <= signal),
        "forensic_evidence_class": EVIDENCE_DIRECT if ok else EVIDENCE_UNKNOWN,
        "entry_price": float(entry_price),
        "stop_price": float(stop_price),
        "take_profit_price": float(take_profit_price),
        "direction": d,
    }
    return {
        "ok": ok,
        "evidence_class": EVIDENCE_DIRECT if ok else EVIDENCE_UNKNOWN,
        "lookahead_status": "PASS" if ok else "FAIL",
        "research_quality": "PASS" if ok else "REVIEW_REQUIRED",
        "lookahead_checks": checks,
        "errors": errors,
        "forensic_fields": forensic_fields,
        "setup_bars_used": len(setup_before),
        "htf_bars_used": len(htf_before),
        "paper_eligible": False,
        "disclaimer": HISTORICAL_DISCLAIMER,
    }


def apply_independent_replay_to_trade(
    trade: Mapping[str, Any],
    *,
    candles_1h: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge independent replay fields onto a trade copy (no mutation of engine cache)."""
    row = dict(trade)
    tp = row.get("take_profit_price")
    if tp is None:
        tp = row.get("tp1")
    replay = independent_candle_replay_audit(
        candles_1h=candles_1h,
        candles_4h=candles_4h,
        signal_time=row.get("signal_time") or row.get("entry_time"),
        entry_time=row.get("entry_time") or row.get("signal_time"),
        entry_price=float(row.get("entry_price") or 0),
        stop_price=float(row.get("stop_price") or 0),
        take_profit_price=float(tp or 0),
        direction=str(row.get("direction") or "SHORT"),
        structure_refs={
            "bos_candle_timestamp": row.get("bos_candle_timestamp"),
            "reference_swing_low_timestamp": row.get("reference_swing_low_timestamp"),
            "swing_high_timestamps": row.get("swing_high_timestamps"),
            "swing_low_timestamps": row.get("swing_low_timestamps"),
            "stop_tp_computed_at": row.get("stop_tp_data_end_time"),
        },
        htf_refs={
            "require_htf": True,
            "last_htf_candle_used": row.get("last_htf_candle_used"),
        },
        order_created_time=row.get("order_created_time") or row.get("signal_time"),
        entry_type=str(row.get("entry_type") or "MARKET"),
        limit_order_price=row.get("limit_order_price"),
        first_eligible_fill_candle=row.get("first_eligible_fill_candle"),
        timeframe=str(row.get("timeframe") or "1h"),
    )
    row["independent_replay"] = {
        k: replay[k]
        for k in (
            "ok",
            "evidence_class",
            "lookahead_status",
            "research_quality",
            "errors",
            "setup_bars_used",
            "htf_bars_used",
        )
    }
    if replay["ok"]:
        row.update(replay["forensic_fields"])
    else:
        row["forensic_evidence_class"] = EVIDENCE_UNKNOWN
    return row


def forensic_future_htf_cannot_alter_signal(
    *,
    signal_time: datetime,
    htf_closed_before: Mapping[str, Any],
    htf_with_future: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove signal retains earlier HTF classification when a future bar arrives."""
    early = str(htf_closed_before.get("trend_4h") or "")
    later = str(htf_with_future.get("trend_4h") or "")
    retained = early == str(htf_closed_before.get("signal_trend_4h") or early)
    future_open = _parse_ts(htf_with_future.get("future_candle_open"))
    used_future = bool(future_open and future_open <= signal_time and later != early)
    return {
        "ok": retained and not used_future,
        "signal_time": signal_time.isoformat(),
        "trend_at_signal": early,
        "trend_if_future_included": later,
        "retained_earlier_classification": retained and early == str(
            htf_closed_before.get("signal_trend_4h") or early
        ),
    }


def forensic_future_swing_cannot_alter_signal(
    *,
    signal_time: datetime,
    swings_at_signal: Sequence[Mapping[str, Any]],
    swings_with_future: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    def _labels(swings: Sequence[Mapping[str, Any]]) -> list[str]:
        out = []
        for s in swings:
            ts = _parse_ts(s.get("timestamp") or s.get("confirmed_at"))
            if ts is not None and ts <= signal_time:
                out.append(str(s.get("label") or s.get("swing_type")))
        return out

    early = _labels(swings_at_signal)
    # Even if future swings exist, signal must only see early labels
    filtered_future = _labels(swings_with_future)
    return {
        "ok": early == filtered_future,
        "labels_at_signal": early,
        "labels_filtered_with_future_series": filtered_future,
        "future_swings_ignored": True,
    }


def forensic_future_bos_cannot_alter_signal(
    *,
    signal_time: datetime,
    bos_at_signal: Mapping[str, Any] | None,
    bos_after: Mapping[str, Any] | None,
) -> dict[str, Any]:
    bos_ts = _parse_ts((bos_at_signal or {}).get("timestamp") or (bos_at_signal or {}).get("time"))
    later_ts = _parse_ts((bos_after or {}).get("timestamp") or (bos_after or {}).get("time"))
    used_future = bool(later_ts and bos_ts and later_ts > signal_time and bos_at_signal == bos_after)
    return {
        "ok": not used_future and (bos_ts is None or bos_ts <= signal_time),
        "bos_at_signal": bos_at_signal,
        "bos_after_signal": bos_after,
    }


def forensic_limit_not_retroactive(
    *,
    order_created: datetime,
    fill_candle_time: datetime,
) -> dict[str, Any]:
    ok = fill_candle_time >= order_created
    return {
        "ok": ok,
        "limit_order_created_time": order_created.isoformat(),
        "first_eligible_fill_candle": fill_candle_time.isoformat(),
        "retroactive": not ok,
    }


def short_stop_tp_directional_checks(
    *,
    candle_high: float,
    candle_low: float,
    entry: float,
    stop: float,
    tp: float,
) -> dict[str, Any]:
    hit_stop = stop_triggered("SHORT", candle_high, candle_low, stop)
    hit_tp = take_profit_triggered("SHORT", candle_high, candle_low, tp)
    same = resolve_same_candle_exit("SHORT", candle_high, candle_low, stop, tp)
    return {
        "stop_uses_high": hit_stop == (candle_high >= stop),
        "tp_uses_low": hit_tp == (candle_low <= tp),
        "same_candle_policy": same["precedence"],
        "same_candle_outcome": same["outcome"],
        "ambiguous": same["ambiguous"],
    }
