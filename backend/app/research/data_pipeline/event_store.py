"""BOS event store — precompute sparse events via production SignalEngine.

Strategy research then evaluates declarative masks over events instead of
re-scanning every candle × strategy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.research.bos_strategy_comparison.config import StrategyResearchConfig
from app.research.bos_strategy_comparison.engine import (
    _bos_confirmed,
    _clone_signal_config,
    _retest_confirmed,
)
from app.research.bos_strategy_comparison.htf import (
    HTF_ALIGNED,
    classify_htf_alignment,
    htf_trends_for_setup_bar,
)
from app.research.bos_strategy_comparison.lifecycle import (
    freeze_bos_impulse_event,
    run_lifecycle,
)
from app.research.combination_engine import (
    _impulse_confirmed,
    _pullback_confirmed,
    _sd_confluence,
    detect_zones_as_of,
)
from app.research.data_pipeline.config import (
    ALIGNED_BEARISH,
    ALIGNED_BULLISH,
    CONFLICT,
    FEATURE_VERSION,
    MIXED,
    NEUTRAL,
    UNAVAILABLE,
    PipelineConfig,
)
from app.signals._candle_utils import candle_time, ohlc
from app.signals.config import SignalConfig
from app.signals.signal_engine import SignalEngine


def _direction_from_bos(bos: Mapping[str, Any] | None) -> str | None:
    if not bos:
        return None
    d = str(bos.get("direction") or "")
    if d == "BULLISH_BOS":
        return "LONG"
    if d == "BEARISH_BOS":
        return "SHORT"
    return None


def mtf_state_from_trends(
    *,
    direction: str | None,
    trend_4h: str | None,
    trend_1h: str | None,
    trend_15m: str | None,
    trend_5m: str | None,
) -> str:
    labels = [
        t
        for t in (trend_4h, trend_1h, trend_15m, trend_5m)
        if t in ("BULLISH", "BEARISH", "NEUTRAL")
    ]
    if not labels:
        return UNAVAILABLE
    if direction == "LONG" and all(t == "BULLISH" for t in labels):
        return ALIGNED_BULLISH
    if direction == "SHORT" and all(t == "BEARISH" for t in labels):
        return ALIGNED_BEARISH
    if "BULLISH" in labels and "BEARISH" in labels:
        return CONFLICT
    if all(t == "NEUTRAL" for t in labels):
        return NEUTRAL
    return MIXED


def build_bos_events_for_symbol(
    *,
    symbol: str,
    candles_by_tf: Mapping[str, Sequence[Mapping[str, Any]]],
    config: PipelineConfig,
    dataset_version: str,
    research_cfg: StrategyResearchConfig,
    signal_config: SignalConfig | None = None,
) -> list[dict[str, Any]]:
    """Scan setup TF bars; emit one event per confirmed BOS (production engines)."""
    setup_tf = config.setup_timeframe
    series = list(candles_by_tf.get(setup_tf) or [])
    if len(series) < research_cfg.min_bars:
        return []

    scfg = signal_config or SignalConfig()
    local_cfg = _clone_signal_config(scfg, research_cfg)
    engine = SignalEngine(local_cfg)
    trend_cache: dict[tuple[str, int], str] = {}
    candles_4h = list(candles_by_tf.get("4h") or [])
    candles_1h = list(candles_by_tf.get("1h") or [])
    candles_5m = list(candles_by_tf.get(config.entry_timeframe) or [])

    events: list[dict[str, Any]] = []
    last_bos_key: tuple[str, str] | None = None
    loop_start = max(research_cfg.min_bars, 50)

    for i in range(loop_start, len(series)):
        tf_analysis = engine.analyze_timeframe(
            symbol, setup_tf, series, as_of_index=i
        )
        bos = tf_analysis.get("bos")
        if not _bos_confirmed(bos if isinstance(bos, Mapping) else None):
            continue

        direction = _direction_from_bos(bos if isinstance(bos, Mapping) else None)
        if direction is None:
            continue

        sig_time = candle_time(series[i])
        if sig_time is None:
            continue
        if sig_time.tzinfo is None:
            sig_time = sig_time.replace(tzinfo=timezone.utc)

        # Deduplicate consecutive identical BOS confirmations
        bos_ts = str((bos or {}).get("timestamp") or sig_time.isoformat())
        key = (bos_ts, direction)
        if key == last_bos_key:
            continue
        last_bos_key = key

        trend = tf_analysis.get("trend") or {}
        impulse = tf_analysis.get("impulse")
        pullback = tf_analysis.get("pullback")
        retest = tf_analysis.get("retest")

        # Research lifecycle: freeze impulse and re-evaluate pullback/retest
        # on subsequent candles (production engines unchanged).
        impulse_ok = _impulse_confirmed(
            impulse if isinstance(impulse, Mapping) else None
        )
        lifecycle_pullback_ok = False
        lifecycle_retest_ok = False
        lifecycle_meta: dict[str, Any] | None = None
        if impulse_ok and isinstance(bos, Mapping) and isinstance(impulse, Mapping):
            frozen = freeze_bos_impulse_event(
                symbol=symbol,
                timeframe=setup_tf,
                bos_index=i,
                bos=bos,
                impulse=impulse,
                candles=series,
            )
            lc = run_lifecycle(
                candles=series,
                event=frozen,
                signal_config=local_cfg,
                max_follow_bars=int(
                    getattr(research_cfg, "research_max_lifecycle_bars", 40) or 40
                ),
            )
            lifecycle_pullback_ok = bool(lc.pullback_pass)
            lifecycle_retest_ok = bool(lc.retest_pass)
            lifecycle_meta = {
                "lifecycle_id": frozen.lifecycle_id,
                "terminal_pullback_state": lc.terminal_pullback_state,
                "terminal_retest_state": lc.terminal_retest_state,
                "termination": lc.termination,
                "pullback_pass": lc.pullback_pass,
                "retest_pass": lc.retest_pass,
            }
            # Prefer lifecycle terminal pullback/retest for downstream masks
            if lc.evaluations:
                last_ev = lc.evaluations[-1]
                pullback = last_ev.pullback
                retest = last_ev.retest
            # If pullback passed earlier than last bar, use first pass evaluation
            for ev in lc.evaluations:
                if ev.pullback_state in ("ACTIVE", "CONFIRMED"):
                    pullback = ev.pullback
                    break
            for ev in lc.evaluations:
                if ev.retest.get("retest"):
                    retest = ev.retest
                    break

        htf = htf_trends_for_setup_bar(
            symbol=symbol,
            setup_candles=series,
            as_of_index=i,
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            candles_5m=candles_5m,
            config=local_cfg,
            trend_15m=str(trend.get("trend") or ""),
            trend_cache=trend_cache,
            include_5m=True,
        )
        htf_alignment = classify_htf_alignment(
            bos_direction=(bos or {}).get("direction"),
            trend_4h=htf.get("trend_4h"),
            trend_1h=htf.get("trend_1h"),
        )
        mtf_state = mtf_state_from_trends(
            direction=direction,
            trend_4h=htf.get("trend_4h"),
            trend_1h=htf.get("trend_1h"),
            trend_15m=htf.get("trend_15m"),
            trend_5m=htf.get("trend_5m"),
        )

        demand_zone, supply_zone = detect_zones_as_of(
            symbol, setup_tf, series, i
        )
        last_close = ohlc(series, i)[3]
        sd_ok = _sd_confluence(
            pullback=pullback if isinstance(pullback, Mapping) else None,
            direction=direction,
            demand_zone=demand_zone,
            supply_zone=supply_zone,
            last_close=last_close,
        )
        sd_state = "INTERACT" if sd_ok else "NONE"

        swing_price = (bos or {}).get("level") or (bos or {}).get("broken_level")
        bos_price = (bos or {}).get("break_price") or last_close
        atr = (impulse or {}).get("atr") if isinstance(impulse, Mapping) else None
        if atr is None and isinstance(bos, Mapping):
            atr = bos.get("atr")
        rvol = (impulse or {}).get("rvol") if isinstance(impulse, Mapping) else None

        events.append(
            {
                "dataset_version": dataset_version,
                "feature_version": FEATURE_VERSION,
                "symbol": symbol.upper(),
                "timestamp": sig_time,
                "timeframe": setup_tf,
                "direction": direction,
                "swing_price": float(swing_price) if swing_price is not None else None,
                "bos_price": float(bos_price) if bos_price is not None else None,
                "bos_distance": (
                    abs(float(bos_price) - float(swing_price))
                    if bos_price is not None and swing_price is not None
                    else None
                ),
                "atr": float(atr) if atr is not None else None,
                "trend": str(trend.get("trend") or UNAVAILABLE),
                "htf_alignment": htf_alignment,
                "mtf_state": mtf_state,
                "impulse": impulse_ok,
                "pullback": (
                    lifecycle_pullback_ok
                    if lifecycle_meta is not None
                    else _pullback_confirmed(
                        pullback if isinstance(pullback, Mapping) else None
                    )
                ),
                "retest": (
                    lifecycle_retest_ok
                    if lifecycle_meta is not None
                    else _retest_confirmed(
                        retest if isinstance(retest, Mapping) else None
                    )
                ),
                "sd_state": sd_state,
                "rvol": float(rvol) if rvol is not None else None,
                "bar_index": i,
                "payload": {
                    "htf": {
                        "trend_4h": htf.get("trend_4h"),
                        "trend_1h": htf.get("trend_1h"),
                        "trend_15m": htf.get("trend_15m"),
                        "trend_5m": htf.get("trend_5m"),
                    },
                    "htf_aligned": htf_alignment == HTF_ALIGNED,
                    "bos_direction": (bos or {}).get("direction"),
                    "lifecycle": lifecycle_meta,
                },
            }
        )
    return events


def strategy_event_mask(
    events: Sequence[Mapping[str, Any]],
    *,
    require_htf: bool = False,
    require_impulse: bool = False,
    require_pullback: bool = False,
    require_retest: bool = False,
    require_sd: bool = False,
) -> list[Mapping[str, Any]]:
    """Vector-friendly filter over precomputed events (no feature recalculation)."""
    out: list[Mapping[str, Any]] = []
    for ev in events:
        if require_htf and not (
            ev.get("htf_alignment") == HTF_ALIGNED
            or (ev.get("payload") or {}).get("htf_aligned")
        ):
            continue
        if require_impulse and not ev.get("impulse"):
            continue
        if require_pullback and not ev.get("pullback"):
            continue
        if require_retest and not ev.get("retest"):
            continue
        if require_sd and not str(ev.get("sd_state") or "").startswith("INTERACT"):
            continue
        out.append(ev)
    return out


# Declarative strategy specs (S1–S3, C1–C4) — structural conditions only
STRATEGY_SPECS: dict[str, dict[str, Any]] = {
    "S1": {
        "name": "BOS + HTF Alignment + Retest",
        "require_htf": True,
        "require_retest": True,
    },
    "S2": {
        "name": "BOS + HTF Alignment + Impulse + Pullback + Retest",
        "require_htf": True,
        "require_impulse": True,
        "require_pullback": True,
        "require_retest": True,
    },
    "S3": {
        "name": "BOS + HTF Alignment + Pullback + Supply/Demand + Retest",
        "require_htf": True,
        "require_pullback": True,
        "require_sd": True,
        "require_retest": True,
    },
    "C1": {"name": "BOS only"},
    "C2": {"name": "BOS + HTF Alignment", "require_htf": True},
    "C3": {"name": "BOS + Retest", "require_retest": True},
    "C4": {
        "name": "BOS + Pullback + Retest",
        "require_pullback": True,
        "require_retest": True,
    },
}


def evaluate_strategy_specs(
    events: Sequence[Mapping[str, Any]],
    *,
    min_sample: int = 30,
) -> dict[str, Any]:
    """Apply declarative masks; mark insufficient samples without optimizing on P&L."""
    report: dict[str, Any] = {}
    for sid, spec in STRATEGY_SPECS.items():
        matched = strategy_event_mask(
            events,
            require_htf=bool(spec.get("require_htf")),
            require_impulse=bool(spec.get("require_impulse")),
            require_pullback=bool(spec.get("require_pullback")),
            require_retest=bool(spec.get("require_retest")),
            require_sd=bool(spec.get("require_sd")),
        )
        long_n = sum(1 for e in matched if e.get("direction") == "LONG")
        short_n = sum(1 for e in matched if e.get("direction") == "SHORT")
        status = "OK" if len(matched) >= min_sample else "INSUFFICIENT_SAMPLE"
        report[sid] = {
            "strategy_id": sid,
            "name": spec["name"],
            "n": len(matched),
            "n_long": long_n,
            "n_short": short_n,
            "status": status,
            "note": "Event counts only — not a profitability ranking.",
        }
    return report
