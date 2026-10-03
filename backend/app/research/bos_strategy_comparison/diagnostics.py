"""Retest / stage-funnel diagnostics for BOS strategy research.

Diagnostic only — uses production SignalEngine / detect_retest.
Does NOT modify retest logic or live signal engines.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any, Mapping, Sequence

from app.research.bos_strategy_comparison.config import (
    DISCLAIMER,
    StrategyResearchConfig,
)
from app.research.bos_strategy_comparison.engine import (
    _bos_confirmed,
    _clone_signal_config,
    _retest_confirmed,
    evaluate_strategy_at_bar,
)
from app.research.bos_strategy_comparison.htf import (
    HTF_ALIGNED,
    classify_htf_alignment,
    htf_trends_for_setup_bar,
)
from app.research.bos_strategy_comparison.strategies import STRATEGIES, get_strategy
from app.research.combination_engine import _impulse_confirmed, _pullback_confirmed
from app.signals._candle_utils import candle_time
from app.signals.config import SignalConfig
from app.signals.signal_engine import SignalEngine


def categorize_retest_rejection(
    *,
    retest: Mapping[str, Any] | None,
    bos: Mapping[str, Any] | None,
    pullback: Mapping[str, Any] | None,
    candles_len: int,
    as_of_index: int,
) -> str:
    """Map production retest/pullback outputs to research rejection categories.

    Categories are observational labels over existing engine reasons —
    not a second retest calculator.
    """
    if as_of_index < 0 or candles_len < 10:
        return "insufficient_candles"
    if not bos or bos.get("state") != "CONFIRMED":
        return "other_existing_engine_reason"
    if bos.get("broken_level") is None:
        return "other_existing_engine_reason"

    pb_state = str((pullback or {}).get("pullback_state") or "")
    if pb_state in ("WAITING", "NONE", ""):
        return "waiting_for_pullback"

    if pullback and pullback.get("structure_intact") is False:
        return "structure_invalidated"

    reason = str((retest or {}).get("reason") or "").lower()
    state = str((retest or {}).get("state") or "").upper()

    if "no broken level" in reason:
        return "other_existing_engine_reason"
    if "waiting for pullback" in reason:
        return "waiting_for_pullback"
    if "invalidat" in reason:
        return "invalidation_occurred"
    if "structure" in reason and ("invalid" in reason or "broke" in reason):
        return "structure_invalidated"
    if "fail" in reason or state == "FAIL":
        return "engine_returned_fail"
    if "no bullish retest" in reason or "no bearish retest" in reason:
        # Production reason when price did not hold near broken level
        distance = (retest or {}).get("distance")
        tol = (retest or {}).get("tolerance")
        if (
            distance is not None
            and tol is not None
            and float(distance) > float(tol) * 1.01
        ):
            return "price_did_not_revisit_bos_level"
        return "price_did_not_revisit_bos_level"
    if "expired" in reason or "window" in reason:
        return "retest_window_expired"
    if reason:
        return "other_existing_engine_reason"
    return "other_existing_engine_reason"


def run_stage_funnel_diagnostic(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_5m: Sequence[Mapping[str, Any]] | None = None,
    index_start: int = 0,
    signal_config: SignalConfig | None = None,
    research_config: StrategyResearchConfig | None = None,
    strategy_ids: Sequence[str] | None = None,
    max_rejection_samples: int = 40,
) -> dict[str, Any]:
    """Walk bars; count funnel stages using production engines only."""
    rcfg = research_config or StrategyResearchConfig()
    scfg = signal_config or SignalConfig()
    local_cfg = _clone_signal_config(scfg, rcfg)
    engine = SignalEngine(local_cfg)
    trend_cache: dict[tuple[str, int], str] = {}

    ids = list(strategy_ids) if strategy_ids else [
        "STRATEGY_1",
        "STRATEGY_2",
        "STRATEGY_3",
        "CONTROL_C",
        "CONTROL_D",
    ]
    strategies = [get_strategy(i) for i in ids]
    strategies = [s for s in strategies if s is not None]

    bos_count = 0
    htf_aligned_count = 0
    impulse_count = 0
    pullback_count = 0
    retest_count = 0
    entry_count = 0
    rejection_counter: Counter[str] = Counter()
    rejection_samples: list[dict[str, Any]] = []
    per_strategy: dict[str, dict[str, int]] = {
        s.strategy_id: {
            "bos_count": 0,
            "htf_aligned_count": 0,
            "impulse_count": 0,
            "pullback_count": 0,
            "retest_count": 0,
            "entry_count": 0,
            "setup_pass": 0,
        }
        for s in strategies
    }

    t0 = time.perf_counter()
    n = len(candles)
    start = max(0, int(index_start))

    for i in range(start, n):
        tf_analysis = engine.analyze_timeframe(
            symbol,
            timeframe,
            candles,
            as_of_index=i,
        )
        bos = tf_analysis.get("bos")
        if not _bos_confirmed(bos):
            continue
        bos_count += 1

        impulse = tf_analysis.get("impulse")
        pullback = tf_analysis.get("pullback")
        retest = tf_analysis.get("retest")
        trend = tf_analysis.get("trend") or {}

        impulse_ok = _impulse_confirmed(impulse)
        pullback_ok = _pullback_confirmed(pullback)
        retest_ok = _retest_confirmed(retest)
        if impulse_ok:
            impulse_count += 1
        if pullback_ok:
            pullback_count += 1
        if retest_ok:
            retest_count += 1
        else:
            cat = categorize_retest_rejection(
                retest=retest,
                bos=bos,
                pullback=pullback,
                candles_len=n,
                as_of_index=i,
            )
            rejection_counter[cat] += 1
            if len(rejection_samples) < max_rejection_samples:
                ts = candle_time(candles[i])
                rejection_samples.append(
                    {
                        "as_of_index": i,
                        "signal_time": ts.isoformat() if ts else None,
                        "category": cat,
                        "engine_reason": (retest or {}).get("reason"),
                        "retest_state": (retest or {}).get("state"),
                        "pullback_state": (pullback or {}).get("pullback_state"),
                        "bos_direction": (bos or {}).get("direction"),
                        "broken_level": (bos or {}).get("broken_level"),
                        "distance": (retest or {}).get("distance"),
                        "tolerance": (retest or {}).get("tolerance"),
                        "structure_intact": (pullback or {}).get("structure_intact"),
                    }
                )

        htf = htf_trends_for_setup_bar(
            symbol=symbol,
            setup_candles=candles,
            as_of_index=i,
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            candles_5m=candles_5m,
            config=local_cfg,
            trend_15m=str(trend.get("trend") or "INSUFFICIENT_DATA"),
            trend_cache=trend_cache,
            include_5m=False,
        )
        htf_alignment = classify_htf_alignment(
            bos_direction=(bos or {}).get("direction"),
            trend_4h=htf.get("trend_4h"),
            trend_1h=htf.get("trend_1h"),
        )
        htf_ok = htf_alignment == HTF_ALIGNED
        if htf_ok:
            htf_aligned_count += 1

        bar_entry = False
        for strat in strategies:
            sid = strat.strategy_id
            bucket = per_strategy[sid]
            bucket["bos_count"] += 1
            if htf_ok:
                bucket["htf_aligned_count"] += 1
            if impulse_ok:
                bucket["impulse_count"] += 1
            if pullback_ok:
                bucket["pullback_count"] += 1
            if retest_ok:
                bucket["retest_count"] += 1

            setup = evaluate_strategy_at_bar(
                symbol=symbol,
                timeframe=timeframe,
                candles=candles,
                as_of_index=i,
                strategy=strat,
                signal_config=scfg,
                research_config=rcfg,
                candles_4h=candles_4h,
                candles_1h=candles_1h,
                candles_5m=candles_5m,
                signal_engine=engine,
                local_cfg=local_cfg,
                tf_analysis=tf_analysis,
                htf=htf,
                trend_cache=trend_cache,
                compute_sd=strat.require_sd,
            )
            status = str(setup.get("status") or "")
            if status in ("LONG_ENTRY_CANDIDATE", "SHORT_ENTRY_CANDIDATE"):
                bucket["entry_count"] += 1
                bucket["setup_pass"] += 1
                bar_entry = True
        if bar_entry:
            entry_count += 1

    elapsed = time.perf_counter() - t0
    return {
        "status": "OK",
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "bars_scanned": max(0, n - start),
        "index_start": start,
        "bos_count": bos_count,
        "htf_aligned_count": htf_aligned_count,
        "impulse_count": impulse_count,
        "pullback_count": pullback_count,
        "retest_count": retest_count,
        "entry_count": entry_count,
        "funnel": {
            "BOS": bos_count,
            "HTF aligned": htf_aligned_count,
            "Impulse": impulse_count,
            "Pullback": pullback_count,
            "Retest": retest_count,
            "Entry": entry_count,
        },
        "retest_rejection_reasons": dict(rejection_counter),
        "retest_rejection_samples": rejection_samples,
        "per_strategy": per_strategy,
        "elapsed_seconds": elapsed,
        "note": (
            "Diagnostic only — production retest engine used as-is. "
            "Retest requires pullback state not WAITING/NONE in detect_retest."
        ),
        "disclaimer": DISCLAIMER,
        "live_engines_unchanged": True,
        "retest_logic_unchanged": True,
    }
