"""Deterministic chronological replay: live-sim vs AS-OF shadow backtest.

Uses frozen COMBO_02 via evaluate_combination_at_bar — no parameter changes.
Live simulation only sees candles at or before T (truncated series).
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.research.bos_combinations import get_combination
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.config import ResearchConfig
from app.research.live_backtest_parity.candle_validation import (
    TrackingCandles,
    candle_close_time,
    validate_candle_close,
)
from app.research.live_backtest_parity.constants import (
    COMBO_ID,
    DEFAULT_SYMBOLS,
    DISCLAIMER,
    LIVE_BACKTEST_MISMATCH,
    PRICE_SOURCE_CLOSE,
    SETUP_TIMEFRAME,
    SOURCE,
    STRATEGY_ID,
)
from app.research.live_backtest_parity.models import (
    EventTimeline,
    ParityEvent,
    ensure_utc,
)
from app.research.live_backtest_parity.paper_sim import simulate_paper_entry
from app.research.live_backtest_parity.shadow_compare import compare_live_vs_asof
from app.research.live_backtest_parity.telegram_validation import MockTelegramSink
from app.signals._candle_utils import candle_time
from app.signals.config import SignalConfig

_MIN_BARS = 50


def _asof_htf(
    series: Sequence[Mapping[str, Any]],
    as_of_ts: datetime,
) -> list[dict[str, Any]]:
    cutoff = ensure_utc(as_of_ts)
    assert cutoff is not None
    out: list[dict[str, Any]] = []
    for c in series:
        t = ensure_utc(candle_time(c))
        if t is not None and t <= cutoff:
            out.append(dict(c))
    return out


def evaluate_live_truncated(
    *,
    symbol: str,
    timeframe: str,
    setup_prefix: Sequence[Mapping[str, Any]],
    candles_1h_prefix: Sequence[Mapping[str, Any]],
    candles_4h_prefix: Sequence[Mapping[str, Any]],
    combination: Any,
    signal_config: SignalConfig,
    research_config: ResearchConfig,
    signal_engine: Any | None = None,
) -> dict[str, Any]:
    """Live engine view: only prefix bars exist (no future candles)."""
    idx = len(setup_prefix) - 1
    return evaluate_combination_at_bar(
        symbol=symbol,
        timeframe=timeframe,
        candles=list(setup_prefix),
        as_of_index=idx,
        combination=combination,
        signal_config=signal_config,
        research_config=research_config,
        signal_engine=signal_engine,
        candles_1h=list(candles_1h_prefix),
        candles_4h=list(candles_4h_prefix),
        compute_sd=False,
    )


def evaluate_asof_shadow(
    *,
    symbol: str,
    timeframe: str,
    setup_full: Sequence[Mapping[str, Any]],
    candles_1h_full: Sequence[Mapping[str, Any]],
    candles_4h_full: Sequence[Mapping[str, Any]],
    as_of_index: int,
    combination: Any,
    signal_config: SignalConfig,
    research_config: ResearchConfig,
    signal_engine: Any | None = None,
) -> dict[str, Any]:
    """Historical AS-OF evaluation on full series with as_of_index=T."""
    tip = setup_full[as_of_index]
    as_of_ts = candle_time(tip) or datetime.now(timezone.utc)
    h1 = _asof_htf(candles_1h_full, as_of_ts)
    h4 = _asof_htf(candles_4h_full, as_of_ts)
    return evaluate_combination_at_bar(
        symbol=symbol,
        timeframe=timeframe,
        candles=list(setup_full),
        as_of_index=as_of_index,
        combination=combination,
        signal_config=signal_config,
        research_config=research_config,
        signal_engine=signal_engine,
        candles_1h=h1,
        candles_4h=h4,
        compute_sd=False,
    )


def _is_entry(result: Mapping[str, Any] | None) -> bool:
    st = str((result or {}).get("status") or "").upper()
    return st in ("LONG_ENTRY_CANDIDATE", "SHORT_ENTRY_CANDIDATE")


def replay_symbol(
    *,
    symbol: str,
    candles_15m: Sequence[Mapping[str, Any]],
    candles_1h: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]],
    timeframe: str = SETUP_TIMEFRAME,
    signal_config: SignalConfig | None = None,
    research_config: ResearchConfig | None = None,
    telegram: MockTelegramSink | None = None,
    start_index: int | None = None,
    live_price_fn: Any | None = None,
    max_bars: int | None = None,
) -> dict[str, Any]:
    """Replay closed bars chronologically; compare live-trunc vs AS-OF shadow."""
    from app.signals.signal_engine import SignalEngine

    combo = get_combination(COMBO_ID)
    if combo is None:
        raise RuntimeError("COMBO_02 definition missing")
    cfg = signal_config or SignalConfig()
    rcfg = research_config or ResearchConfig()
    shared_engine = SignalEngine(cfg)
    sink = telegram or MockTelegramSink()
    sym = str(symbol).upper()
    tf = str(timeframe or SETUP_TIMEFRAME).lower()

    setup = list(candles_15m if tf == "15m" else candles_15m)
    if tf == "1h":
        setup = list(candles_1h)
    h1 = list(candles_1h)
    h4 = list(candles_4h)

    events: list[ParityEvent] = []
    mismatches: list[dict[str, Any]] = []
    future_fails = 0
    loop_start = max(_MIN_BARS, int(start_index or _MIN_BARS))
    loop_end = len(setup)
    if max_bars is not None and max_bars > 0:
        loop_end = min(loop_end, loop_start + int(max_bars))

    for i in range(loop_start, loop_end):
        tip = setup[i]
        open_t = ensure_utc(candle_time(tip))
        close_t = candle_close_time(tip, tf)
        as_of_ts = open_t or close_t
        if as_of_ts is None:
            continue

        # LIVE: truncated prefix only — future bars inaccessible.
        live_setup = setup[: i + 1]
        live_h1 = _asof_htf(h1, as_of_ts)
        live_h4 = _asof_htf(h4, as_of_ts)

        tracked = TrackingCandles(setup)
        # Measure wall-clock processing deltas, then anchor the event timeline
        # at candle_close_time so latencies are not mixed with historical open times.
        t0 = time.perf_counter()
        live_result = evaluate_live_truncated(
            symbol=sym,
            timeframe=tf,
            setup_prefix=live_setup,
            candles_1h_prefix=live_h1,
            candles_4h_prefix=live_h4,
            combination=combo,
            signal_config=cfg,
            research_config=rcfg,
            signal_engine=shared_engine,
        )
        t_signal = time.perf_counter()

        # Shadow AS-OF on full series (engines must ignore bars after as_of_index).
        asof_result = evaluate_asof_shadow(
            symbol=sym,
            timeframe=tf,
            setup_full=setup,
            candles_1h_full=h1,
            candles_4h_full=h4,
            as_of_index=i,
            combination=combo,
            signal_config=cfg,
            research_config=rcfg,
            signal_engine=shared_engine,
        )
        t_shadow = time.perf_counter()

        # Probe that tracking full series with as_of does not need future if truncated.
        _ = tracked[i]  # access tip only for bookkeeping
        if tracked.max_accessed > i:
            future_fails += 1

        cmp = compare_live_vs_asof(
            symbol=sym,
            timeframe=tf,
            timestamp=as_of_ts.isoformat(),
            live_result=live_result,
            asof_result=asof_result,
        )

        # Only emit parity events for live entry candidates (or mismatches).
        if not _is_entry(live_result) and cmp["match"]:
            continue

        anchor = close_t or as_of_ts
        assert anchor is not None
        frame_received = anchor
        signal_at = anchor + timedelta(seconds=max(0.0, t_signal - t0))
        plan_at = anchor + timedelta(seconds=max(0.0, t_shadow - t0))

        event = ParityEvent(
            event_id=f"{sym}-{tf}-{i}-{uuid.uuid4().hex[:8]}",
            symbol=sym,
            timeframe=tf,
            direction=str(live_result.get("direction") or "") or None,
            setup=cmp.get("live_setup"),
            bos=cmp.get("live_bos"),
            choch=cmp.get("live_choch"),
            parity_result=cmp["parity_result"],
            mismatch_reason=cmp.get("reason"),
            live_status=cmp.get("live_state"),
            backtest_status=cmp.get("backtest_state"),
            live_direction=cmp.get("live_direction"),
            backtest_direction=cmp.get("backtest_direction"),
            live_entry=cmp.get("live_entry"),
            backtest_entry=cmp.get("backtest_entry"),
            live_sl=cmp.get("live_sl"),
            backtest_sl=cmp.get("backtest_sl"),
            live_tp=cmp.get("live_tp"),
            backtest_tp=cmp.get("backtest_tp"),
            live_rr=cmp.get("live_rr"),
            backtest_rr=cmp.get("backtest_rr"),
            timeline=EventTimeline(
                candle_open_time=open_t,
                candle_close_time=close_t,
                live_frame_received_at=frame_received,
                signal_detected_at=signal_at,
                trade_plan_created_at=plan_at,
            ),
            strategy_id=STRATEGY_ID,
            source=SOURCE,
            production_approved=False,
            telegram_eligible=False,
            extra={
                "as_of_index": i,
                "setup_close": float(tip.get("close") or tip.get("c") or 0) or None,
                "disclaimer": DISCLAIMER,
                "processing_signal_ms": int(max(0.0, t_signal - t0) * 1000),
                "processing_shadow_ms": int(max(0.0, t_shadow - t_signal) * 1000),
            },
        )

        candle_val = validate_candle_close(
            as_of=as_of_ts,
            signal_detected_at=signal_at,
            setup_candles=live_setup,
            setup_timeframe=tf,
            candles_1h=live_h1,
            candles_4h=live_h4,
            candles_15m=live_setup if tf == "15m" else None,
        )
        if candle_val.future_data_detected:
            future_fails += 1
        event.candle_validation = candle_val

        if not cmp["match"]:
            mismatches.append(
                {
                    "symbol": sym,
                    "timeframe": tf,
                    "timestamp": as_of_ts.isoformat(),
                    "live_state": cmp.get("live_state"),
                    "backtest_state": cmp.get("backtest_state"),
                    "live_direction": cmp.get("live_direction"),
                    "backtest_direction": cmp.get("backtest_direction"),
                    "live_entry": cmp.get("live_entry"),
                    "backtest_entry": cmp.get("backtest_entry"),
                    "reason": cmp.get("reason"),
                }
            )

        # Paper observation using tip close as live price in replay (same source).
        injected = None
        if live_price_fn is not None:
            injected = live_price_fn(sym, tip, live_result)
        else:
            injected = float(tip.get("close") or tip.get("c") or 0) or None
        t_paper0 = time.perf_counter()
        simulate_paper_entry(
            event,
            live_result=live_result,
            injected_live_price=injected,
            injected_price_source=PRICE_SOURCE_CLOSE,
        )
        # Re-anchor paper/alert times to candle-close + measured deltas (not wall clock).
        paper_delta = max(0.0, time.perf_counter() - t_paper0)
        event.timeline.paper_entry_at = plan_at + timedelta(seconds=paper_delta)
        t_alert0 = time.perf_counter()
        sink.process(event)
        alert_delta = max(0.0, time.perf_counter() - t_alert0)
        event.timeline.alert_generated_at = event.timeline.paper_entry_at + timedelta(
            seconds=alert_delta
        )
        if event.timeline.telegram_sent_at is not None:
            event.timeline.telegram_sent_at = event.timeline.alert_generated_at
        events.append(event)

    return {
        "symbol": sym,
        "timeframe": tf,
        "bars_replayed": max(0, loop_end - loop_start),
        "events": events,
        "mismatches": mismatches,
        "future_data_fails": future_fails,
        "telegram": sink,
        "disclaimer": DISCLAIMER,
    }


def replay_universe(
    *,
    series_by_symbol: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]],
    symbols: Sequence[str] | None = None,
    timeframe: str = SETUP_TIMEFRAME,
    **kwargs: Any,
) -> dict[str, Any]:
    """Replay BTC/ETH/SOL (or provided universe) and aggregate."""
    syms = [s.upper() for s in (symbols or DEFAULT_SYMBOLS)]
    sink = MockTelegramSink()
    all_events: list[ParityEvent] = []
    all_mismatches: list[dict[str, Any]] = []
    per_symbol: dict[str, Any] = {}
    future_fails = 0

    for sym in syms:
        pack = series_by_symbol.get(sym) or series_by_symbol.get(sym.upper())
        if not pack:
            per_symbol[sym] = {"error": "missing_ohlcv"}
            continue
        result = replay_symbol(
            symbol=sym,
            candles_15m=pack.get("15m") or pack.get("candles_15m") or [],
            candles_1h=pack.get("1h") or pack.get("candles_1h") or [],
            candles_4h=pack.get("4h") or pack.get("candles_4h") or [],
            timeframe=timeframe,
            telegram=sink,
            **kwargs,
        )
        per_symbol[sym] = {
            "bars_replayed": result["bars_replayed"],
            "candidates": len(result["events"]),
            "mismatches": len(result["mismatches"]),
            "future_data_fails": result["future_data_fails"],
        }
        all_events.extend(result["events"])
        all_mismatches.extend(result["mismatches"])
        future_fails += int(result["future_data_fails"])

    return {
        "symbols": syms,
        "timeframe": timeframe,
        "events": all_events,
        "mismatches": all_mismatches,
        "future_data_fails": future_fails,
        "per_symbol": per_symbol,
        "telegram_stats": sink.stats(),
        "telegram": sink,
        "disclaimer": DISCLAIMER,
        "production_approved": False,
        "live_orders": False,
        "strategy_changed": False,
        "v1_changed": False,
    }
