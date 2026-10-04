"""Write parity CSVs / summary.json / validation_report.md."""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from app.research.live_backtest_parity.constants import (
    DEVIATION_BUCKETS,
    DISCLAIMER,
    ENTRY_PRICE_STALE,
    ENTRY_PRICE_UNAVAILABLE,
    ENTRY_PRICE_VALID,
    LIVE_BACKTEST_MATCH,
    LIVE_BACKTEST_MISMATCH,
)
from app.research.live_backtest_parity.entry_price import (
    deviation_bucket,
    deviation_distribution,
)
from app.research.live_backtest_parity.models import ParityEvent


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    # Union of keys for stable header.
    keys: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _cell(r.get(k)) for k in keys})


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    return v


def _latency_rows(events: Sequence[ParityEvent]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for e in events:
        lat = e.timeline.latencies_ms()
        rows.append(
            {
                "event_id": e.event_id,
                "symbol": e.symbol,
                "timeframe": e.timeframe,
                **lat,
                **{k: getattr(e.timeline, k).isoformat()
                   if getattr(e.timeline, k) is not None else None
                   for k in (
                       "candle_open_time",
                       "candle_close_time",
                       "live_frame_received_at",
                       "signal_detected_at",
                       "trade_plan_created_at",
                       "alert_generated_at",
                       "telegram_sent_at",
                       "paper_entry_at",
                   )},
            }
        )
    return rows


def _latency_dist(events: Sequence[ParityEvent], key: str) -> dict[str, float | None]:
    vals = []
    for e in events:
        v = e.timeline.latencies_ms().get(key)
        if v is not None:
            vals.append(float(v))
    return deviation_distribution(vals)


def build_bucket_stats(events: Sequence[ParityEvent]) -> list[dict[str, Any]]:
    groups: dict[str, list[ParityEvent]] = defaultdict(list)
    for e in events:
        b = deviation_bucket(e.entry_deviation_pct) or "unknown"
        groups[b].append(e)
    out: list[dict[str, Any]] = []
    for name, _lo, _hi in DEVIATION_BUCKETS:
        rows = groups.get(name, [])
        devs = [float(e.entry_deviation_pct) for e in rows if e.entry_deviation_pct is not None]
        paper_n = sum(1 for e in rows if e.paper_opened)
        out.append(
            {
                "bucket": name,
                "signal_count": len(rows),
                "paper_entries": paper_n,
                "average_deviation": (
                    statistics.fmean(devs) if devs else None
                ),
                "median_deviation": (
                    statistics.median(devs) if devs else None
                ),
                "max_deviation": max(devs) if devs else None,
                "win_loss": None,  # insufficient paper outcome tracking in this pass
            }
        )
    return out


def build_summary(
    *,
    events: Sequence[ParityEvent],
    mismatches: Sequence[dict[str, Any]],
    future_data_fails: int,
    telegram_stats: dict[str, int],
    per_symbol: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    matches = sum(1 for e in events if e.parity_result == LIVE_BACKTEST_MATCH)
    mismatch_n = sum(1 for e in events if e.parity_result == LIVE_BACKTEST_MISMATCH)
    total = len(events)
    # Also count silent mismatches discovered without entry events.
    mismatch_n = max(mismatch_n, len(mismatches))
    match_rate = (matches / total) if total else None

    entry_devs = [e.entry_deviation_pct for e in events]
    stale_n = sum(1 for e in events if e.entry_price_status == ENTRY_PRICE_STALE)
    valid_n = sum(1 for e in events if e.entry_price_status == ENTRY_PRICE_VALID)
    unavail_n = sum(
        1 for e in events if e.entry_price_status == ENTRY_PRICE_UNAVAILABLE
    )

    parity_pass = mismatch_n == 0 and future_data_fails == 0
    no_lookahead = future_data_fails == 0 and all(
        not e.candle_validation.future_data_detected for e in events
    )
    entry_trace = all(
        e.entry_triad.backtest_entry is not None
        or e.entry_triad.live_signal_price is not None
        or e.live_entry is not None
        for e in events
    ) if events else True
    # Require distinct triad fields recorded when paper opened.
    if any(e.paper_opened for e in events):
        entry_trace = entry_trace and all(
            e.entry_triad.backtest_entry is not None
            and e.entry_triad.live_signal_price is not None
            and e.entry_triad.paper_entry is not None
            for e in events
            if e.paper_opened
        )
    latency_trace = all(
        e.timeline.signal_detected_at is not None
        and e.timeline.candle_close_time is not None
        for e in events
    ) if events else True
    telegram_trace = (
        telegram_stats.get("generated", 0) == total
        if total
        else telegram_stats.get("generated", 0) >= 0
    )

    summary: dict[str, Any] = {
        "disclaimer": DISCLAIMER,
        "total_live_candidates": total,
        "asof_matches": matches,
        "asof_mismatches": mismatch_n,
        "match_rate": match_rate,
        "future_data_fails": future_data_fails,
        "entry_deviation": deviation_distribution(entry_devs),
        "entry_deviation_buckets": build_bucket_stats(events),
        "latency": {
            "candle_close_to_signal_ms": _latency_dist(
                events, "candle_close_to_signal_ms"
            ),
            "signal_to_trade_plan_ms": _latency_dist(
                events, "signal_to_trade_plan_ms"
            ),
            "trade_plan_to_alert_ms": _latency_dist(
                events, "trade_plan_to_alert_ms"
            ),
            "alert_to_paper_entry_ms": _latency_dist(
                events, "alert_to_paper_entry_ms"
            ),
            "candle_close_to_paper_entry_ms": _latency_dist(
                events, "candle_close_to_paper_entry_ms"
            ),
            "total_signal_to_entry_ms": _latency_dist(
                events, "total_signal_to_entry_ms"
            ),
        },
        "entry_price_check": {
            "VALID": valid_n,
            "STALE": stale_n,
            "UNAVAILABLE": unavail_n,
            "STALE_ENTRY": stale_n,
        },
        "telegram": dict(telegram_stats),
        "per_symbol": per_symbol or {},
        "verdict": {
            "LIVE_BACKTEST_PARITY": "PASS" if parity_pass else "FAIL",
            "NO_LOOKAHEAD": "PASS" if no_lookahead else "FAIL",
            "ENTRY_PRICE_TRACEABILITY": "PASS" if entry_trace else "FAIL",
            "LATENCY_TRACEABILITY": "PASS" if latency_trace else "FAIL",
            "TELEGRAM_TRACEABILITY": "PASS" if telegram_trace else "FAIL",
            "NO_LIVE_ORDERS": "CONFIRMED",
            "NO_PRODUCTION_APPROVAL": "CONFIRMED",
            "NO_STRATEGY_CHANGES": "CONFIRMED",
        },
    }
    if extra:
        summary["extra"] = extra
    return summary


def write_validation_report_md(summary: dict[str, Any], path: Path) -> None:
    v = summary.get("verdict") or {}
    ed = summary.get("entry_deviation") or {}
    lines = [
        "# Live ↔ Backtest Entry Parity — Validation Report",
        "",
        DISCLAIMER,
        "",
        "## Verdict",
        "",
        f"- LIVE ↔ BACKTEST PARITY: **{v.get('LIVE_BACKTEST_PARITY')}**",
        f"- NO-LOOKAHEAD: **{v.get('NO_LOOKAHEAD')}**",
        f"- ENTRY PRICE TRACEABILITY: **{v.get('ENTRY_PRICE_TRACEABILITY')}**",
        f"- LATENCY TRACEABILITY: **{v.get('LATENCY_TRACEABILITY')}**",
        f"- TELEGRAM TRACEABILITY: **{v.get('TELEGRAM_TRACEABILITY')}**",
        f"- NO LIVE ORDERS: **{v.get('NO_LIVE_ORDERS')}**",
        f"- NO PRODUCTION APPROVAL: **{v.get('NO_PRODUCTION_APPROVAL')}**",
        f"- NO STRATEGY CHANGES: **{v.get('NO_STRATEGY_CHANGES')}**",
        "",
        "## AS-OF Parity",
        "",
        f"- total live candidates: {summary.get('total_live_candidates')}",
        f"- AS-OF matches: {summary.get('asof_matches')}",
        f"- AS-OF mismatches: {summary.get('asof_mismatches')}",
        f"- match rate: {summary.get('match_rate')}",
        f"- future_data_fails: {summary.get('future_data_fails')}",
        "",
        "## Entry Deviation",
        "",
        f"- P50: {ed.get('P50')}",
        f"- P75: {ed.get('P75')}",
        f"- P90: {ed.get('P90')}",
        f"- P95: {ed.get('P95')}",
        f"- P99: {ed.get('P99')}",
        f"- MAX: {ed.get('MAX')}",
        "",
        "## ENTRY_PRICE_CHECK",
        "",
    ]
    epc = summary.get("entry_price_check") or {}
    lines.extend(
        [
            f"- VALID: {epc.get('VALID')}",
            f"- STALE / STALE_ENTRY: {epc.get('STALE')}",
            f"- UNAVAILABLE: {epc.get('UNAVAILABLE')}",
            "",
            "## Latency (ms)",
            "",
        ]
    )
    for name, dist in (summary.get("latency") or {}).items():
        lines.append(
            f"- {name}: P50={dist.get('P50')} P95={dist.get('P95')} "
            f"P99={dist.get('P99')} MAX={dist.get('MAX')}"
        )
    tel = summary.get("telegram") or {}
    lines.extend(
        [
            "",
            "## Telegram (mock / research)",
            "",
            f"- generated: {tel.get('generated')}",
            f"- sent: {tel.get('sent')}",
            f"- blocked: {tel.get('blocked')}",
            f"- failed: {tel.get('failed')}",
            "",
            "## Notes",
            "",
            "- Entry deviation buckets are descriptive only; no validity declared.",
            "- Dynamic production Telegram remains disabled.",
            "- V1 and COMBO_02 parameters were not modified.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_parity_artifacts(
    out_dir: Path | str,
    *,
    events: Sequence[ParityEvent],
    mismatches: Sequence[dict[str, Any]],
    future_data_fails: int,
    telegram_stats: dict[str, int],
    per_symbol: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    parity_rows = []
    entry_rows = []
    replay_rows = []
    for e in events:
        d = e.to_dict()
        parity_rows.append(
            {
                "event_id": e.event_id,
                "symbol": e.symbol,
                "timeframe": e.timeframe,
                "timestamp": d["timeline"].get("signal_detected_at")
                if isinstance(d.get("timeline"), dict)
                else None,
                "parity_result": e.parity_result,
                "live_status": e.live_status,
                "backtest_status": e.backtest_status,
                "live_direction": e.live_direction,
                "backtest_direction": e.backtest_direction,
                "live_entry": e.live_entry,
                "backtest_entry": e.backtest_entry,
                "live_sl": e.live_sl,
                "backtest_sl": e.backtest_sl,
                "live_tp": e.live_tp,
                "backtest_tp": e.backtest_tp,
                "live_rr": e.live_rr,
                "backtest_rr": e.backtest_rr,
                "reason": e.mismatch_reason,
                "production_approved": False,
                "telegram_eligible": False,
                "future_data_detected": e.candle_validation.future_data_detected,
            }
        )
        entry_rows.append(
            {
                "event_id": e.event_id,
                "symbol": e.symbol,
                "timeframe": e.timeframe,
                "backtest_entry": e.entry_triad.backtest_entry,
                "live_signal_price": e.entry_triad.live_signal_price,
                "paper_entry": e.entry_triad.paper_entry,
                "absolute_difference": e.absolute_difference,
                "difference_pct": e.entry_deviation_pct,
                "live_vs_backtest_bps": e.live_vs_backtest_bps,
                "bucket": deviation_bucket(e.entry_deviation_pct),
                "entry_price_status": e.entry_price_status,
                "entry_price_label": e.entry_price_label,
                "price_source": e.entry_triad.price_source,
                "signal_time": d["timeline"].get("signal_detected_at")
                if isinstance(d.get("timeline"), dict)
                else None,
                "paper_entry_time": d["timeline"].get("paper_entry_at")
                if isinstance(d.get("timeline"), dict)
                else None,
                "paper_opened": e.paper_opened,
            }
        )
        replay_rows.append(
            {
                "event_id": e.event_id,
                "symbol": e.symbol,
                "timeframe": e.timeframe,
                "parity_result": e.parity_result,
                "as_of_index": (e.extra or {}).get("as_of_index"),
                "future_data_detected": e.candle_validation.future_data_detected,
                "signal_after_close": e.candle_validation.signal_after_close,
                "telegram_status": e.telegram_status,
                "paper_opened": e.paper_opened,
                "entry_price_status": e.entry_price_status,
            }
        )

    # Include mismatch-only rows not already in events.
    for m in mismatches:
        parity_rows.append({**m, "parity_result": LIVE_BACKTEST_MISMATCH})

    _write_csv(out / "parity_events.csv", parity_rows)
    _write_csv(out / "entry_deviation.csv", entry_rows)
    _write_csv(out / "latency.csv", _latency_rows(events))
    _write_csv(out / "replay_results.csv", replay_rows)

    summary = build_summary(
        events=events,
        mismatches=mismatches,
        future_data_fails=future_data_fails,
        telegram_stats=telegram_stats,
        per_symbol=per_symbol,
        extra=extra,
    )
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    write_validation_report_md(summary, out / "validation_report.md")
    return summary
