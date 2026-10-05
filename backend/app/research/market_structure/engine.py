"""Market-structure analytics engine — post-backtest, read-only, PIT-safe.

Never modifies strategy signals, entries, exits, sizing, fees, or fingerprints.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.research.bos_strategy_comparison.htf import (
    build_htf_as_of_index_map_fully_closed,
    decision_timestamp_for_setup_bar,
)
from app.research.market_structure.attribution import (
    attribute_bar,
    build_index_maps,
    classify_15m_status,
    normalize_ledger_trades,
)
from app.research.market_structure.config import (
    ANALYTICS_VERSION,
    MarketStructureFeatureConfig,
    assert_regime_filtering_safe,
    default_feature_config,
)
from app.research.market_structure.mtf import classify_mtf_alignment
from app.research.market_structure.research_labels import suggest_research_opportunity
from app.research.market_structure.structure import (
    StructureSnapshot,
    _missing_snapshot,
    build_timeframe_feature_table,
    snapshot_at_index,
)
from app.signals._candle_utils import candle_time


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _parse_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _aware(raw)
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return _aware(datetime.fromisoformat(s))
    except ValueError:
        return None


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def compute_market_structure_analytics(
    *,
    symbol: str,
    setup_timeframe: str,
    setup_candles: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]] | None,
    candles_1h: Sequence[Mapping[str, Any]] | None,
    candles_15m: Sequence[Mapping[str, Any]] | None,
    trades: Sequence[Mapping[str, Any]] | None = None,
    index_start: int | None = None,
    index_end: int | None = None,
    combination_id: str | None = None,
    strategy_id: str | None = None,
    closed_htf_policy: bool = True,
    config: MarketStructureFeatureConfig | None = None,
    enable_regime_filtering: bool = False,
    research_only: bool = True,
    run_id: str | None = None,
    dataset_fingerprint: str | None = None,
    configuration_fingerprint: str | None = None,
    requested_range: Mapping[str, Any] | None = None,
    actual_range: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute bar-level structure/regime analytics for a completed backtest cell.

    ``closed_htf_policy`` controls analytics 4h mapping only (always prefers
    fully-closed 4h candles for feature labels). Strategy forming-HTF path is
    never altered here.
    """
    assert_regime_filtering_safe(
        enable_regime_filtering=enable_regime_filtering,
        research_only=research_only,
    )
    if enable_regime_filtering:
        raise ValueError(
            "ENABLE_REGIME_FILTERING is False for this implementation; "
            "refusing to apply regime filters even in research_only mode"
        )

    t0 = time.perf_counter()
    cfg = config or default_feature_config()
    series = list(setup_candles)
    n = len(series)
    start = max(0, index_start if index_start is not None else 0)
    end = min(n, index_end if index_end is not None else n)

    # Prefer dedicated 1h series; if setup is 1h, reuse setup candles.
    tf_l = (setup_timeframe or "").lower()
    series_1h = list(candles_1h) if candles_1h else (series if tf_l == "1h" else [])
    series_4h = list(candles_4h) if candles_4h else (series if tf_l == "4h" else [])
    series_15m = list(candles_15m) if candles_15m else []
    source_15m_available = bool(series_15m)
    status_15m_series = "OK" if source_15m_available else "UNAVAILABLE"

    table_1h = (
        build_timeframe_feature_table(series_1h, "1h", symbol=symbol, config=cfg)
        if series_1h
        else None
    )
    table_4h = (
        build_timeframe_feature_table(series_4h, "4h", symbol=symbol, config=cfg)
        if series_4h
        else None
    )
    table_15m = (
        build_timeframe_feature_table(series_15m, "15m", symbol=symbol, config=cfg)
        if series_15m
        else None
    )

    # Always use fully-closed as-of maps for analytics features.
    idx_4h_map: list[int | None] = [None] * n
    idx_1h_map: list[int | None] = [None] * n
    idx_15m_map: list[int | None] = [None] * n
    if series_4h:
        idx_4h_map = build_htf_as_of_index_map_fully_closed(
            series,
            series_4h,
            setup_timeframe=setup_timeframe,
            htf_timeframe="4h",
        )
    if series_1h:
        if tf_l == "1h":
            # Decision on closed 1h bar i uses features of bar i (same candle).
            idx_1h_map = list(range(n))
        else:
            idx_1h_map = build_htf_as_of_index_map_fully_closed(
                series,
                series_1h,
                setup_timeframe=setup_timeframe,
                htf_timeframe="1h",
            )
    if series_15m:
        idx_15m_map = build_htf_as_of_index_map_fully_closed(
            series,
            series_15m,
            setup_timeframe=setup_timeframe,
            htf_timeframe="15m",
        )

    # Authoritative ledger maps — entry_index only (never decision_iso join).
    ledger = normalize_ledger_trades(trades)
    by_entry, active_by_bar, by_exit = build_index_maps(ledger)

    by_bar: list[dict[str, Any]] = []
    quality = {
        "total_1h_decision_bars": 0,
        "bars_with_4h_features": 0,
        "bars_with_1h_features": 0,
        "bars_with_15m_features": 0,
        "bars_with_future_feature_violation": 0,
        "bars_with_missing_feature_data": 0,
        "bars_with_unknown_regime": 0,
        "bars_with_timestamp_mismatch": 0,
        "bars_with_duplicate_feature_rows": 0,
        "15m_source_available_rows": 0,
        "15m_feature_available_rows": 0,
        "15m_unknown_rows": 0,
        "15m_status_distribution": {},
        "duplicate_execution_entries_per_trade": 0,
        "trades_without_execution_row": 0,
        "execution_rows_without_trade_id": 0,
        "signal_rows_without_trade_id": 0,
        "offending_rows": [],
    }
    seen_keys: set[str] = set()
    execution_counts: dict[str, int] = {}
    pit_failed = False
    pit_error: str | None = None

    decision_indices = list(range(start, end))
    for i in decision_indices:
        quality["total_1h_decision_bars"] += 1
        decision_ts = decision_timestamp_for_setup_bar(
            series, i, setup_timeframe=setup_timeframe
        )
        decision_iso = _iso(decision_ts)
        open_ts = _aware(candle_time(series[i]))
        row_key = f"{decision_iso}|{i}"
        if row_key in seen_keys:
            quality["bars_with_duplicate_feature_rows"] += 1
        seen_keys.add(row_key)

        snap_4h: StructureSnapshot
        snap_1h: StructureSnapshot
        snap_15m: StructureSnapshot
        try:
            i4 = idx_4h_map[i] if i < len(idx_4h_map) else None
            if table_4h is None or i4 is None:
                snap_4h = _missing_snapshot(
                    "4h",
                    decision_iso,
                    quality="UNKNOWN_DATA_MISSING",
                    reason="4H_DATA_MISSING" if table_4h is None else "4H_NO_CLOSED_CANDLE",
                )
                quality["bars_with_missing_feature_data"] += 1
            else:
                snap_4h = snapshot_at_index(table_4h, i4, decision_time=decision_ts)
                quality["bars_with_4h_features"] += 1
                if snap_4h.last_closed_candle_time and decision_ts:
                    feat_ts = _parse_ts(snap_4h.last_closed_candle_time)
                    if feat_ts and feat_ts > decision_ts:
                        quality["bars_with_future_feature_violation"] += 1
                        quality["offending_rows"].append(
                            {
                                "bar_index": i,
                                "decision_time": decision_iso,
                                "feature_time": snap_4h.last_closed_candle_time,
                                "timeframe": "4h",
                            }
                        )
                        pit_failed = True
                        pit_error = (
                            f"4h feature close {feat_ts} > decision {decision_ts} at bar {i}"
                        )
                        break

            i1 = idx_1h_map[i] if i < len(idx_1h_map) else None
            if table_1h is None or i1 is None:
                snap_1h = _missing_snapshot(
                    "1h",
                    decision_iso,
                    quality="UNKNOWN_DATA_MISSING",
                    reason="1H_DATA_MISSING",
                )
                quality["bars_with_missing_feature_data"] += 1
            else:
                snap_1h = snapshot_at_index(table_1h, i1, decision_time=decision_ts)
                quality["bars_with_1h_features"] += 1

            i15 = idx_15m_map[i] if i < len(idx_15m_map) else None
            if table_15m is None or i15 is None:
                snap_15m = _missing_snapshot(
                    "15m",
                    decision_iso,
                    quality="UNKNOWN_DATA_MISSING",
                    reason=(
                        "15M_SOURCE_UNAVAILABLE"
                        if not source_15m_available
                        else "15M_NO_CLOSED"
                    ),
                )
                if source_15m_available:
                    quality["bars_with_missing_feature_data"] += 1
            else:
                snap_15m = snapshot_at_index(table_15m, i15, decision_time=decision_ts)
                quality["bars_with_15m_features"] += 1
        except AssertionError as exc:
            quality["bars_with_future_feature_violation"] += 1
            quality["offending_rows"].append(
                {"bar_index": i, "decision_time": decision_iso, "error": str(exc)}
            )
            pit_failed = True
            pit_error = str(exc)
            break

        mtf = classify_mtf_alignment(snap_4h, snap_1h, snap_15m)
        research = suggest_research_opportunity(
            structure_4h=snap_4h,
            structure_1h=snap_1h,
            structure_15m=snap_15m,
            mtf_alignment=mtf.mtf_alignment,
        )

        status_15 = classify_15m_status(
            source_available=source_15m_available,
            snap_quality=snap_15m.data_quality_state,
            last_closed_candle_time=snap_15m.last_closed_candle_time,
            decision_time=decision_ts,
            missing_reason=(snap_15m.reason_codes[0] if snap_15m.reason_codes else None),
        )
        if status_15["15m_source_available"]:
            quality["15m_source_available_rows"] += 1
        if status_15["15m_feature_available"]:
            quality["15m_feature_available_rows"] += 1
        else:
            if source_15m_available:
                quality["15m_unknown_rows"] += 1
        dist = quality["15m_status_distribution"]
        dist[status_15["15m_status"]] = int(dist.get(status_15["15m_status"], 0)) + 1

        attrib = attribute_bar(
            i, by_entry=by_entry, active=active_by_bar, by_exit=by_exit
        )
        trade = attrib["trade"]
        trade_id = attrib["trade_id"]
        entry_flag = attrib["entry"]
        entry_attr = attrib["entry_attribution_type"]
        row_role = attrib["row_role"]

        if entry_attr == "EXECUTION_BAR":
            if not trade_id:
                quality["execution_rows_without_trade_id"] += 1
            else:
                execution_counts[str(trade_id)] = execution_counts.get(str(trade_id), 0) + 1
        if attrib["signal_bar"] and not trade_id:
            quality["signal_rows_without_trade_id"] += 1

        if trade is not None and entry_attr == "EXECUTION_BAR":
            trade_status = str(trade.get("outcome") or "OPEN")
            direction = str(trade.get("direction") or snap_1h.direction)
            signal_stage = "accepted_signal"
            final_decision = "ACCEPTED"
            rejection_reason = None
            primary_stage = None
            rejection_detail_status = "AVAILABLE"
            r_mult = trade.get("r_multiple")
            if r_mult is None:
                r_mult = trade.get("r_net")
            win_loss = None
            try:
                if r_mult is not None:
                    rv = float(r_mult)
                    win_loss = "WIN" if rv > 0 else ("LOSS" if rv < 0 else "FLAT")
            except (TypeError, ValueError):
                win_loss = None
            gross_pnl = trade.get("gross_pnl_usd")
            fees = trade.get("fee_total_usd")
            net_pnl = trade.get("net_pnl_usd")
        elif entry_attr == "POSITION_ACTIVE":
            trade_status = str((trade or {}).get("outcome") or "OPEN")
            direction = str((trade or {}).get("direction") or snap_1h.direction)
            signal_stage = "position_active"
            final_decision = "NO_ENTRY"
            rejection_reason = "POSITION_ACTIVE_NOT_NEW_ENTRY"
            primary_stage = "POSITION_STATE"
            rejection_detail_status = "AVAILABLE"
            r_mult = None
            win_loss = None
            gross_pnl = None
            fees = None
            net_pnl = None
        else:
            trade_status = "NO_TRADE"
            direction = snap_1h.direction
            signal_stage = "no_entry"
            final_decision = "NO_ENTRY"
            # Stage-level gate reasons are not exported on the hot path.
            rejection_reason = "NO_STRATEGY_ENTRY_AT_BAR"
            primary_stage = "UNKNOWN_NOT_EXPORTED"
            rejection_detail_status = "NOT_EXPORTED"
            r_mult = None
            win_loss = None
            gross_pnl = None
            fees = None
            net_pnl = None

        if snap_1h.market_regime == "UNKNOWN" and snap_4h.market_regime == "UNKNOWN":
            quality["bars_with_unknown_regime"] += 1

        if snap_1h.feature_timestamp and decision_ts:
            ft = _parse_ts(snap_1h.feature_timestamp)
            if ft and ft > decision_ts:
                quality["bars_with_timestamp_mismatch"] += 1

        by_bar.append(
            {
                "bar_index": i,
                "decision_time": decision_iso,
                "setup_open_time": _iso(open_ts),
                "signal_time": attrib.get("signal_time"),
                "entry_time": attrib.get("entry_time"),
                "exit_time": attrib.get("exit_time"),
                "signal_bar": attrib["signal_bar"],
                "execution_bar": attrib["execution_bar"],
                "entry_attribution_type": entry_attr,
                "row_role": row_role,
                "trade_id": trade_id,
                "trade_status": trade_status,
                "entry": entry_flag,
                "direction": direction,
                "win_loss": win_loss,
                "r_multiple": r_mult,
                "gross_pnl": gross_pnl,
                "fees": fees,
                "net_pnl": net_pnl,
                "structure_4h": snap_4h.to_dict(),
                "structure_1h": snap_1h.to_dict(),
                "structure_15m": snap_15m.to_dict(),
                "regime_4h": snap_4h.market_regime,
                "trend_4h": snap_4h.trend_state,
                "structure_state_4h": snap_4h.structure_state,
                "bos_4h": snap_4h.bos_state,
                "regime_1h": snap_1h.market_regime,
                "trend_1h": snap_1h.trend_state,
                "structure_state_1h": snap_1h.structure_state,
                "bos_1h": snap_1h.bos_state,
                "regime_15m": snap_15m.market_regime,
                "trend_15m": snap_15m.trend_state,
                "structure_state_15m": snap_15m.structure_state,
                "bos_15m": snap_15m.bos_state,
                "market_regime": snap_1h.market_regime,
                "mtf_alignment": mtf.mtf_alignment,
                "mtf_score": mtf.mtf_score,
                "mtf_confidence": mtf.mtf_confidence,
                "mtf_conflict_count": mtf.mtf_conflict_count,
                "volatility_state": snap_1h.volatility_state,
                "choppiness_state": (
                    "HIGH"
                    if (snap_1h.choppiness_index or 0) >= cfg.choppiness_high_threshold
                    else (
                        "LOW"
                        if snap_1h.choppiness_index is not None
                        and snap_1h.choppiness_index <= cfg.choppiness_low_threshold
                        else "MID"
                        if snap_1h.choppiness_index is not None
                        else "UNKNOWN"
                    )
                ),
                "signal_stage": signal_stage,
                "final_strategy_decision": final_decision,
                "rejection_reason": rejection_reason,
                "primary_rejection_stage": primary_stage,
                "primary_rejection_reason": rejection_reason,
                "rejection_detail_status": rejection_detail_status,
                "regime_confidence": snap_1h.confidence_score,
                "regime_reason_codes": snap_1h.reason_codes,
                "research_opportunity": research,
                "research_label": research.get("primary_research_label"),
                "accepted_signal": bool(attrib["accepted_signal"]),
                "15m_source_available": status_15["15m_source_available"],
                "15m_feature_available": status_15["15m_feature_available"],
                "15m_status": status_15["15m_status"],
                "15m_last_closed_candle_time": status_15["15m_last_closed_candle_time"],
                "15m_missing_reason": status_15["15m_missing_reason"],
            }
        )

    # Attribution quality invariants
    for tid, cnt in execution_counts.items():
        if cnt > 1:
            quality["duplicate_execution_entries_per_trade"] += cnt - 1
            quality["offending_rows"].append(
                {"trade_id": tid, "execution_rows": cnt, "issue": "DUPLICATE_EXECUTION"}
            )
    for lt in ledger:
        if execution_counts.get(lt.trade_id, 0) == 0:
            # Only count if entry_index is inside the evaluated window
            if (
                lt.entry_index is not None
                and start <= lt.entry_index < end
            ):
                quality["trades_without_execution_row"] += 1
                quality["offending_rows"].append(
                    {
                        "trade_id": lt.trade_id,
                        "entry_index": lt.entry_index,
                        "issue": "TRADE_WITHOUT_EXECUTION_ROW",
                    }
                )

    analytics_runtime = time.perf_counter() - t0

    if pit_failed or quality["bars_with_future_feature_violation"] > 0:
        return {
            "status": "PIT_VIOLATION",
            "error": pit_error or "future feature violation",
            "analytics_enabled": True,
            "analytics_version": ANALYTICS_VERSION,
            "analytics_runtime_seconds": round(analytics_runtime, 6),
            "quality_report": quality,
            "by_bar": by_bar,
            "feature_config": cfg.to_dict(),
            "feature_config_fingerprint": cfg.fingerprint(),
        }

    # Closed-HTF invariant for analytics
    if closed_htf_policy and series_4h:
        for row in by_bar:
            s4 = row.get("structure_4h") or {}
            if s4.get("data_quality_state", "").startswith("UNKNOWN"):
                continue
            feat = _parse_ts(s4.get("last_closed_candle_time"))
            dec = _parse_ts(row.get("decision_time"))
            if feat and dec and feat > dec:
                quality["bars_with_future_feature_violation"] += 1
                return {
                    "status": "PIT_VIOLATION",
                    "error": f"every_selected_4h_close <= decision_close failed: {feat} > {dec}",
                    "analytics_enabled": True,
                    "analytics_version": ANALYTICS_VERSION,
                    "analytics_runtime_seconds": round(analytics_runtime, 6),
                    "quality_report": quality,
                    "by_bar": by_bar,
                    "feature_config": cfg.to_dict(),
                    "feature_config_fingerprint": cfg.fingerprint(),
                }

    from app.research.market_structure.summaries import (
        build_mtf_alignment_summary,
        build_opportunity_summary,
        build_trade_regime_summary,
    )

    trade_context, unmatched, ambiguous = build_trade_context(by_bar, ledger)
    quality["unmatched_trade_context_rows"] = len(unmatched)
    quality["ambiguous_trade_context_rows"] = len(ambiguous)
    quality["unique_executed_trades"] = sum(
        1 for r in by_bar if r.get("entry_attribution_type") == "EXECUTION_BAR"
    )
    quality["raw_decision_rows"] = len(by_bar)
    quality["signal_row_count"] = sum(1 for r in by_bar if r.get("signal_bar"))
    quality["execution_row_count"] = sum(1 for r in by_bar if r.get("execution_bar"))
    quality["position_active_row_count"] = sum(
        1 for r in by_bar if r.get("entry_attribution_type") == "POSITION_ACTIVE"
    )
    quality["no_trade_row_count"] = sum(
        1 for r in by_bar if r.get("entry_attribution_type") == "NO_ENTRY"
    )
    quality["unknown_row_count"] = sum(
        1 for r in by_bar if r.get("entry_attribution_type") == "UNKNOWN"
    )
    quality["missing_4h_feature_rows"] = sum(
        1
        for r in by_bar
        if str((r.get("structure_4h") or {}).get("data_quality_state") or "").startswith(
            "UNKNOWN"
        )
    )
    quality["missing_1h_feature_rows"] = sum(
        1
        for r in by_bar
        if str((r.get("structure_1h") or {}).get("data_quality_state") or "").startswith(
            "UNKNOWN"
        )
    )
    quality["unknown_15m_feature_rows"] = quality["15m_unknown_rows"]
    quality["ledger_trade_count"] = len(ledger)
    quality["trade_context_count"] = len(trade_context)
    quality["trade_context_reconciles_to_ledger"] = len(trade_context) == len(ledger)

    trade_regime = build_trade_regime_summary(by_bar, trades or [])
    opportunity = build_opportunity_summary(by_bar)
    mtf_summary = build_mtf_alignment_summary(by_bar)

    return {
        "status": "OK",
        "analytics_enabled": True,
        "analytics_version": ANALYTICS_VERSION,
        "analytics_runtime_seconds": round(analytics_runtime, 6),
        "feature_config": cfg.to_dict(),
        "feature_config_fingerprint": cfg.fingerprint(),
        "15m_status": status_15m_series,
        "15m_source_available": source_15m_available,
        "15m_source_available_rows": quality["15m_source_available_rows"],
        "15m_feature_available_rows": quality["15m_feature_available_rows"],
        "15m_unknown_rows": quality["15m_unknown_rows"],
        "15m_status_distribution": quality["15m_status_distribution"],
        "closed_candle_policy": "fully_closed_asof_for_analytics",
        "row_counts": {
            "raw_decision_rows": quality["raw_decision_rows"],
            "unique_trade_count": quality["unique_executed_trades"],
            "unique_executed_trades": quality["unique_executed_trades"],
            "signal_row_count": quality["signal_row_count"],
            "execution_row_count": quality["execution_row_count"],
            "position_active_row_count": quality["position_active_row_count"],
            "no_trade_row_count": quality["no_trade_row_count"],
            "unknown_row_count": quality["unknown_row_count"],
            "ledger_trade_count": quality["ledger_trade_count"],
        },
        "metadata": {
            "strategy_id": strategy_id,
            "combo_id": combination_id,
            "symbol": symbol.upper(),
            "setup_timeframe": setup_timeframe,
            "dataset_fingerprint": dataset_fingerprint,
            "configuration_fingerprint": configuration_fingerprint,
            "feature_config_fingerprint": cfg.fingerprint(),
            "requested_range": dict(requested_range or {}),
            "actual_range": dict(actual_range or {}),
            "timeframes_used": {
                "4h": bool(series_4h),
                "1h": bool(series_1h),
                "15m": bool(series_15m),
            },
            "data_sources": {
                "4h": "ohlcv" if series_4h else None,
                "1h": "ohlcv" if series_1h else None,
                "15m": "ohlcv" if series_15m else None,
            },
            "closed_candle_policy": "4h_close_time <= setup_candle_close_time",
            "analytics_version": ANALYTICS_VERSION,
            "run_id": run_id,
            "disclaimer": "Analytics only — does not affect strategy decisions",
        },
        "by_bar": by_bar,
        "trade_context": trade_context,
        "unmatched_trade_context": unmatched,
        "ambiguous_trade_context": ambiguous,
        "trade_regime_summary": trade_regime,
        "regime_opportunity_summary": opportunity,
        "mtf_alignment_summary": mtf_summary,
        "quality_report": quality,
        "table_rows": [_table_row(r) for r in by_bar],
    }


def build_trade_context(
    by_bar: Sequence[Mapping[str, Any]],
    ledger: Sequence[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """One authoritative trade-context row per ledger trade."""
    exec_rows = [
        r for r in by_bar if r.get("entry_attribution_type") == "EXECUTION_BAR"
    ]
    by_tid: dict[str, list[Mapping[str, Any]]] = {}
    for r in exec_rows:
        tid = str(r.get("trade_id") or "")
        by_tid.setdefault(tid, []).append(r)

    context: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []

    for lt in ledger:
        tid = lt.trade_id
        rows = by_tid.get(tid) or []
        t = lt.trade
        if len(rows) == 0:
            unmatched.append(
                {
                    "trade_id": tid,
                    "entry_time": lt.entry_time.isoformat() if lt.entry_time else None,
                    "candidate_decision_times": [],
                    "match_status": "UNMATCHED",
                    "reason": "NO_EXECUTION_BAR_IN_ANALYTICS_WINDOW",
                }
            )
            context.append(
                {
                    "trade_id": tid,
                    "entry_time": lt.entry_time.isoformat() if lt.entry_time else t.get("signal_time"),
                    "exit_time": lt.exit_time.isoformat() if lt.exit_time else t.get("exit_time"),
                    "direction": t.get("direction"),
                    "entry_regime_4h": None,
                    "entry_regime_1h": None,
                    "entry_regime_15m": None,
                    "entry_market_regime": None,
                    "entry_mtf_alignment": None,
                    "entry_mtf_score": None,
                    "entry_volatility_state": None,
                    "entry_choppiness_state": None,
                    "entry_bos_4h": None,
                    "entry_bos_1h": None,
                    "entry_bos_15m": None,
                    "entry_research_label": None,
                    "win_loss": None,
                    "r_multiple": t.get("r_multiple") if t.get("r_multiple") is not None else t.get("r_net"),
                    "gross_pnl": t.get("gross_pnl_usd"),
                    "fees": t.get("fee_total_usd"),
                    "net_pnl": t.get("net_pnl_usd"),
                    "context_match_status": "UNMATCHED",
                }
            )
            continue
        if len(rows) > 1:
            ambiguous.append(
                {
                    "trade_id": tid,
                    "entry_time": lt.entry_time.isoformat() if lt.entry_time else None,
                    "candidate_decision_times": [r.get("decision_time") for r in rows],
                    "match_status": "AMBIGUOUS",
                    "reason": "MULTIPLE_EXECUTION_BARS",
                }
            )
            match_status = "AMBIGUOUS"
            row = rows[0]
        else:
            row = rows[0]
            unknown_feats = (
                str(row.get("regime_1h") or "").startswith("UNKNOWN")
                or str(row.get("15m_status") or "")
                in {"UNKNOWN_DATA_MISSING", "INSUFFICIENT_HISTORY", "UNAVAILABLE"}
            )
            match_status = (
                "MATCHED_WITH_UNKNOWN_FEATURES" if unknown_feats else "MATCHED_UNIQUE"
            )

        r_mult = row.get("r_multiple")
        if r_mult is None:
            r_mult = t.get("r_multiple") if t.get("r_multiple") is not None else t.get("r_net")
        context.append(
            {
                "trade_id": tid,
                "entry_time": row.get("entry_time")
                or (lt.entry_time.isoformat() if lt.entry_time else t.get("signal_time")),
                "exit_time": row.get("exit_time")
                or (lt.exit_time.isoformat() if lt.exit_time else t.get("exit_time")),
                "direction": row.get("direction") or t.get("direction"),
                "entry_regime_4h": row.get("regime_4h"),
                "entry_regime_1h": row.get("regime_1h"),
                "entry_regime_15m": row.get("regime_15m"),
                "entry_market_regime": row.get("market_regime"),
                "entry_mtf_alignment": row.get("mtf_alignment"),
                "entry_mtf_score": row.get("mtf_score"),
                "entry_volatility_state": row.get("volatility_state"),
                "entry_choppiness_state": row.get("choppiness_state"),
                "entry_bos_4h": row.get("bos_4h"),
                "entry_bos_1h": row.get("bos_1h"),
                "entry_bos_15m": row.get("bos_15m"),
                "entry_research_label": row.get("research_label"),
                "win_loss": row.get("win_loss"),
                "r_multiple": r_mult,
                "gross_pnl": row.get("gross_pnl")
                if row.get("gross_pnl") is not None
                else t.get("gross_pnl_usd"),
                "fees": row.get("fees") if row.get("fees") is not None else t.get("fee_total_usd"),
                "net_pnl": row.get("net_pnl")
                if row.get("net_pnl") is not None
                else t.get("net_pnl_usd"),
                "context_match_status": match_status,
            }
        )
    return context, unmatched, ambiguous


def _table_row(r: Mapping[str, Any]) -> dict[str, Any]:
    """Flattened UI/export row matching the required table columns."""
    return {
        "decision_time": r.get("decision_time"),
        "signal_time": r.get("signal_time"),
        "entry_time": r.get("entry_time"),
        "trade_id": r.get("trade_id"),
        "trade_status": r.get("trade_status"),
        "entry": r.get("entry"),
        "signal_bar": r.get("signal_bar"),
        "execution_bar": r.get("execution_bar"),
        "entry_attribution_type": r.get("entry_attribution_type"),
        "row_role": r.get("row_role"),
        "direction": r.get("direction"),
        "regime_4h": r.get("regime_4h"),
        "trend_4h": r.get("trend_4h"),
        "structure_4h": r.get("structure_state_4h"),
        "bos_4h": r.get("bos_4h"),
        "regime_1h": r.get("regime_1h"),
        "trend_1h": r.get("trend_1h"),
        "structure_1h": r.get("structure_state_1h"),
        "bos_1h": r.get("bos_1h"),
        "regime_15m": r.get("regime_15m"),
        "trend_15m": r.get("trend_15m"),
        "structure_15m": r.get("structure_state_15m"),
        "bos_15m": r.get("bos_15m"),
        "mtf_alignment": r.get("mtf_alignment"),
        "mtf_score": r.get("mtf_score"),
        "volatility_state": r.get("volatility_state"),
        "choppiness_state": r.get("choppiness_state"),
        "signal_stage": r.get("signal_stage"),
        "final_strategy_decision": r.get("final_strategy_decision"),
        "rejection_reason": r.get("rejection_reason"),
        "primary_rejection_stage": r.get("primary_rejection_stage"),
        "primary_rejection_reason": r.get("primary_rejection_reason"),
        "rejection_detail_status": r.get("rejection_detail_status"),
        "win_loss": r.get("win_loss"),
        "r_multiple": r.get("r_multiple"),
        "market_regime": r.get("market_regime"),
        "regime_confidence": r.get("regime_confidence"),
        "research_label": r.get("research_label")
        or (r.get("research_opportunity") or {}).get("primary_research_label"),
        "15m_source_available": r.get("15m_source_available"),
        "15m_feature_available": r.get("15m_feature_available"),
        "15m_status": r.get("15m_status"),
        "15m_last_closed_candle_time": r.get("15m_last_closed_candle_time"),
        "15m_missing_reason": r.get("15m_missing_reason"),
    }
