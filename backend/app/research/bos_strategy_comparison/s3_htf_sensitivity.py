"""Research-only S3 HTF sensitivity experiment.

Reuses BOS lifecycle, pullback, retest, S/D, entry/SL/TP/RR/fees.
Only the HTF eligibility gate differs across variants.

Does NOT modify production STRATEGY_3 or signal engines.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from app.config import get_settings
from app.engines.supply_demand.engine import SupplyDemandEngine
from app.research.bos_strategy_comparison.config import (
    DISCLAIMER,
    StrategyResearchConfig,
)
from app.research.bos_strategy_comparison.engine import (
    _clone_signal_config,
    evaluate_strategy_at_bar,
)
from app.research.bos_strategy_comparison.htf import (
    as_of_index_at_or_before,
    htf_trends_for_setup_bar,
)
from app.research.bos_strategy_comparison.htf_forensics import (
    decompose_htf_rejection,
)
from app.research.bos_strategy_comparison.htf_variants import (
    HTF_VARIANTS,
    list_htf_variants,
)
from app.research.bos_strategy_comparison.lifecycle import (
    LifecycleResult,
    first_retest_pass_eval,
)
from app.research.bos_strategy_comparison.metrics import (
    apply_net_r,
    by_symbol_breakdown,
    by_year_breakdown,
    chronological_splits,
    compute_direction_metrics,
    metrics_summary_dict,
)
from app.research.bos_strategy_comparison.runner import (
    _lifecycle_structure_override,
    _manage_open_trade,
    _precompute_lifecycles,
    _trade_from_setup,
)
from app.research.bos_strategy_comparison.schemas import StrategyTrade
from app.research.bos_strategy_comparison.strategies import StrategyDefinition, get_strategy
from app.research.combination_engine import _sd_confluence, detect_zones_as_of
from app.research.config import AMBIGUOUS_EXCLUDE, walk_forward_windows
from app.signals._candle_utils import candle_time, ohlc
from app.signals.config import SignalConfig
from app.signals.signal_engine import SignalEngine

SEP2024_EXPECTED = {
    "BTCUSDT": {"sd_pass": 8, "htf_pass": 0},
    "ETHUSDT": {"sd_pass": 8, "htf_pass": 0},
    "SOLUSDT": {"sd_pass": 9, "htf_pass": 0},
}


def _s3_without_htf_requirement() -> StrategyDefinition:
    """Copy of STRATEGY_3 with HTF gate disabled (applied externally by variants)."""
    base = get_strategy("STRATEGY_3")
    assert base is not None
    return StrategyDefinition(
        strategy_id="STRATEGY_3_HTF_EXTERNAL",
        strategy_name=base.strategy_name + " (HTF external)",
        description="Research copy — HTF applied by sensitivity variant gate.",
        kind="STRATEGY",
        require_bos=base.require_bos,
        require_retest=base.require_retest,
        require_impulse=base.require_impulse,
        require_pullback=base.require_pullback,
        require_sd=base.require_sd,
        require_htf_alignment=False,
        require_entry_ready=base.require_entry_ready,
        conditions=base.conditions,
    )


def _direction_from_bos(bos_direction: str | None) -> str | None:
    if bos_direction == "BULLISH_BOS":
        return "LONG"
    if bos_direction == "BEARISH_BOS":
        return "SHORT"
    return None


def sample_status_label(n: int) -> str:
    if n < 10:
        return "VERY_SMALL_SAMPLE"
    if n < 30:
        return "INSUFFICIENT_SAMPLE"
    return "OK"


def _empty_funnel() -> dict[str, int]:
    return {
        "bos_lifecycles": 0,
        "pullback_pass": 0,
        "retest_pass": 0,
        "sd_pass": 0,
        "htf_pass": 0,
        "entry_ready": 0,
        "rr_pass": 0,
        "trades": 0,
    }


def _enriched_metrics(trades: Sequence[StrategyTrade], rcfg: StrategyResearchConfig) -> dict[str, Any]:
    closed = [t for t in trades if t.exit_reason not in (None, "OPEN")]
    apply_net_r(
        list(closed),
        taker_fee=rcfg.taker_fee,
        maker_fee=rcfg.maker_fee,
        slippage_rate=rcfg.slippage_rate,
    )
    all_m = compute_direction_metrics(
        closed, direction="ALL", bootstrap_samples=rcfg.bootstrap_samples
    )
    long_m = compute_direction_metrics(closed, direction="LONG")
    short_m = compute_direction_metrics(closed, direction="SHORT")
    n = all_m.sample_size
    gross_rs = [float(t.gross_R) for t in closed if t.gross_R is not None]
    net_rs = [float(t.net_R) for t in closed if t.net_R is not None]
    fees_r = None
    slip_r = None
    if gross_rs and net_rs and len(gross_rs) == len(net_rs):
        # Aggregate fee+slippage in R as gross - net
        fees_r = sum(g - n for g, n in zip(gross_rs, net_rs)) / len(gross_rs)

    tp1 = sum(1 for t in closed if t.exit_reason == "TP1")
    sl = sum(1 for t in closed if t.exit_reason == "SL")
    splits = chronological_splits(
        closed,
        train_fraction=rcfg.train_fraction,
        validation_fraction=rcfg.validation_fraction,
        oos_fraction=rcfg.oos_fraction,
    )
    split_metrics = {
        name: metrics_summary_dict(compute_direction_metrics(subset, direction="ALL"))
        for name, subset in splits.items()
    }
    for name, subset in splits.items():
        split_metrics[name]["sample_status"] = sample_status_label(len(subset))

    summary = metrics_summary_dict(all_m)
    summary["sample_status"] = sample_status_label(n)
    summary["trade_count"] = n
    summary["LONG_count"] = long_m.sample_size
    summary["SHORT_count"] = short_m.sample_size
    summary["gross_R_total"] = sum(gross_rs) if gross_rs else None
    summary["net_R_total"] = sum(net_rs) if net_rs else None
    summary["TP1_hits"] = tp1
    summary["SL_hits"] = sl
    summary["fees_slippage_R_avg"] = fees_r
    summary["slippage_rate_config"] = rcfg.slippage_rate
    summary["taker_fee"] = rcfg.taker_fee
    summary["maker_fee"] = rcfg.maker_fee
    return {
        "all": summary,
        "long": metrics_summary_dict(long_m),
        "short": metrics_summary_dict(short_m),
        "by_year": by_year_breakdown(closed),
        "by_symbol": by_symbol_breakdown(closed, min_sample=30),
        "train_val_oos": split_metrics,
    }


def run_s3_htf_sensitivity_symbol(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_5m: Sequence[Mapping[str, Any]] | None = None,
    index_start: int = 0,
    index_end: int | None = None,
    signal_config: SignalConfig | None = None,
    research_config: StrategyResearchConfig | None = None,
    variant_ids: Sequence[str] | None = None,
    period_label: str = "FULL",
) -> dict[str, Any]:
    """Walk series once; evaluate all HTF variants with shared pre-HTF path."""
    t0 = time.perf_counter()
    rcfg = research_config or StrategyResearchConfig()
    scfg = signal_config or SignalConfig()
    local_cfg = _clone_signal_config(scfg, rcfg)
    engine = SignalEngine(local_cfg)
    sd_engine = SupplyDemandEngine(get_settings().indicators_config)
    trend_cache: dict[tuple[str, int], str] = {}
    s3_ext = _s3_without_htf_requirement()

    vids = list(variant_ids) if variant_ids else list(HTF_VARIANTS.keys())
    variants = [HTF_VARIANTS[v] for v in vids if v in HTF_VARIANTS]
    if not variants:
        return {"status": "ERROR", "reason": "No variants", "symbol": symbol}

    series = list(candles)
    start = max(0, int(index_start))
    end = min(len(series), index_end if index_end is not None else len(series))
    loop_start = max(rcfg.min_bars, start)

    lifecycle_results, lifecycle_stats = _precompute_lifecycles(
        symbol=symbol,
        timeframe=timeframe,
        series=series,
        index_start=loop_start,
        local_cfg=local_cfg,
        research_config=rcfg,
        signal_config=scfg,
    )

    # Shared schedule: retest eligibility bars
    schedule: list[tuple[int, LifecycleResult]] = []
    shared_funnel = _empty_funnel()
    shared_funnel["bos_lifecycles"] = int(lifecycle_stats.get("lifecycles") or 0)
    shared_funnel["pullback_pass"] = int(lifecycle_stats.get("pullback_pass") or 0)
    shared_funnel["retest_pass"] = int(lifecycle_stats.get("retest_pass") or 0)

    for lc in lifecycle_results:
        if lc.event.bos_index < loop_start or lc.event.bos_index >= end:
            continue
        rt = first_retest_pass_eval(lc)
        if rt is None or not lc.retest_pass:
            continue
        if rt.as_of_index < loop_start or rt.as_of_index >= end:
            continue
        schedule.append((rt.as_of_index, lc))
    schedule.sort(key=lambda x: (x[0], x[1].event.lifecycle_id))

    # Precompute SD + HTF trends at eligibility (shared across variants)
    cand_cache: dict[str, dict[str, Any]] = {}
    lookahead_violations: list[str] = []
    for eidx, lc in schedule:
        elig_ts = candle_time(series[eidx]) if 0 <= eidx < len(series) else None
        demand_zone, supply_zone = detect_zones_as_of(
            symbol, timeframe, series, eidx, sd_engine=sd_engine
        )
        direction = _direction_from_bos(lc.event.bos_direction)
        last_close = ohlc(series, eidx)[3] if eidx < len(series) else None
        ev = None
        for e in lc.evaluations:
            if e.as_of_index == eidx:
                ev = e
                break
        pullback = ev.pullback if ev else {}
        sd_ok = _sd_confluence(
            pullback=pullback,
            direction=direction,
            demand_zone=demand_zone,
            supply_zone=supply_zone,
            last_close=last_close,
        )
        htf = htf_trends_for_setup_bar(
            symbol=symbol,
            setup_candles=series,
            as_of_index=eidx,
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            candles_5m=candles_5m,
            config=local_cfg,
            trend_cache=trend_cache,
            include_5m=False,
        )
        # No-lookahead asserts
        if elig_ts is not None:
            idx4 = as_of_index_at_or_before(candles_4h or [], elig_ts)
            idx1 = as_of_index_at_or_before(candles_1h or [], elig_ts)
            if idx4 is not None and candles_4h:
                t4 = candle_time(candles_4h[idx4])
                if t4 and t4 > elig_ts:
                    lookahead_violations.append(f"{lc.event.lifecycle_id}:4h")
            if idx1 is not None and candles_1h:
                t1 = candle_time(candles_1h[idx1])
                if t1 and t1 > elig_ts:
                    lookahead_violations.append(f"{lc.event.lifecycle_id}:1h")

        cand_cache[lc.event.lifecycle_id] = {
            "eidx": eidx,
            "direction": direction,
            "sd_ok": sd_ok,
            "demand_zone": demand_zone,
            "supply_zone": supply_zone,
            "htf": htf,
            "pullback": pullback,
            "elig_ts": elig_ts.isoformat() if elig_ts else None,
            "trend_4h": htf.get("trend_4h"),
            "trend_1h": htf.get("trend_1h"),
        }

    sd_pass_n = sum(1 for c in cand_cache.values() if c["sd_ok"])
    shared_funnel["sd_pass"] = sd_pass_n

    # Baseline HTF rejection distribution (among SD survivors)
    baseline_htf_reasons: Counter[str] = Counter()
    for c in cand_cache.values():
        if not c["sd_ok"]:
            continue
        ok, status = HTF_VARIANTS["S3_BASELINE"].gate(
            c["direction"], c["trend_4h"], c["trend_1h"]
        )
        if ok:
            baseline_htf_reasons["ALIGNED"] += 1
        else:
            reason = decompose_htf_rejection(
                direction=c["direction"],
                state_4h=str(c["trend_4h"]),
                state_1h=str(c["trend_1h"]),
                alignment=status,
            )
            baseline_htf_reasons[reason] += 1

    # Per-variant state
    funnels: dict[str, dict[str, int]] = {v.variant_id: _empty_funnel() for v in variants}
    for v in variants:
        funnels[v.variant_id]["bos_lifecycles"] = shared_funnel["bos_lifecycles"]
        funnels[v.variant_id]["pullback_pass"] = shared_funnel["pullback_pass"]
        funnels[v.variant_id]["retest_pass"] = shared_funnel["retest_pass"]
        funnels[v.variant_id]["sd_pass"] = shared_funnel["sd_pass"]

    trades_by: dict[str, list[StrategyTrade]] = {v.variant_id: [] for v in variants}
    open_by: dict[str, StrategyTrade | None] = {v.variant_id: None for v in variants}
    htf_rej: dict[str, Counter[str]] = {v.variant_id: Counter() for v in variants}
    consumed: set[tuple[str, str]] = set()

    schedule_i = 0
    for i in range(loop_start, end):
        c = series[i]
        for vid, ot in list(open_by.items()):
            if ot is None:
                continue
            closed = _manage_open_trade(
                ot, i=i, candle=c, handling=rcfg.ambiguous_handling
            )
            if closed is not None:
                if not (
                    closed.exit_reason == "AMBIGUOUS_INTRABAR"
                    and rcfg.ambiguous_handling == AMBIGUOUS_EXCLUDE
                ):
                    trades_by[vid].append(closed)
                open_by[vid] = None

        while schedule_i < len(schedule) and schedule[schedule_i][0] == i:
            eidx, lc = schedule[schedule_i]
            schedule_i += 1
            meta = cand_cache[lc.event.lifecycle_id]
            if not meta["sd_ok"]:
                continue
            override = _lifecycle_structure_override(lc, entry_index=i, candles=series)
            for variant in variants:
                vid = variant.variant_id
                key = (vid, lc.event.lifecycle_id)
                if key in consumed:
                    continue
                htf_ok, htf_status = variant.gate(
                    meta["direction"], meta["trend_4h"], meta["trend_1h"]
                )
                if not htf_ok:
                    htf_rej[vid][htf_status] += 1
                    consumed.add(key)
                    continue
                # HTF eligibility counted even if a position is already open
                funnels[vid]["htf_pass"] += 1
                if open_by[vid] is not None:
                    consumed.add(key)
                    continue

                setup = evaluate_strategy_at_bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    candles=series,
                    as_of_index=i,
                    strategy=s3_ext,
                    signal_config=scfg,
                    research_config=rcfg,
                    candles_4h=candles_4h,
                    candles_1h=candles_1h,
                    candles_5m=candles_5m,
                    signal_engine=engine,
                    sd_engine=sd_engine,
                    compute_sd=False,
                    trend_cache=trend_cache,
                    local_cfg=local_cfg,
                    htf=meta["htf"],
                    demand_zone=meta["demand_zone"],
                    supply_zone=meta["supply_zone"],
                    structure_override=override,
                )
                consumed.add(key)
                status = str(setup.get("status") or "")
                gates = dict(setup.get("gates") or {})
                if gates.get("sd") is not True:
                    continue
                if status not in ("LONG_ENTRY_CANDIDATE", "SHORT_ENTRY_CANDIDATE"):
                    continue
                funnels[vid]["entry_ready"] += 1
                funnels[vid]["rr_pass"] += 1
                funnels[vid]["trades"] += 1
                trade = _trade_from_setup(
                    {**setup, "htf_alignment": htf_status},
                    strategy_id=vid,
                    symbol=symbol,
                    timeframe=timeframe,
                    entry_index=i,
                    period_label=period_label,
                )
                open_by[vid] = trade

    for vid, ot in open_by.items():
        if ot is not None:
            ot.exit_reason = "OPEN"
            trades_by[vid].append(ot)

    variant_results: dict[str, Any] = {}
    for variant in variants:
        vid = variant.variant_id
        metrics = _enriched_metrics(trades_by[vid], rcfg)
        variant_results[vid] = {
            "variant": {
                "variant_id": variant.variant_id,
                "name": variant.name,
                "description": variant.description,
                "kind": variant.kind,
            },
            "funnel": funnels[vid],
            "htf_rejections": dict(htf_rej[vid]),
            "metrics": metrics,
            "trade_count": metrics["all"]["trade_count"],
            "sample_status": metrics["all"]["sample_status"],
            "trades": [t.to_dict() for t in trades_by[vid] if t.exit_reason != "OPEN"],
        }

    # Pairwise descriptive deltas vs baseline
    base_m = variant_results.get("S3_BASELINE", {}).get("metrics", {}).get("all", {})
    pairwise: dict[str, Any] = {}
    for vid, payload in variant_results.items():
        if vid == "S3_BASELINE":
            continue
        m = payload["metrics"]["all"]
        pairwise[vid] = {
            "vs": "S3_BASELINE",
            "trade_count_delta": (m.get("trade_count") or 0)
            - (base_m.get("trade_count") or 0),
            "expectancy_delta": _delta(m.get("expectancy_R"), base_m.get("expectancy_R")),
            "PF_delta": _delta(m.get("profit_factor"), base_m.get("profit_factor")),
            "DD_delta": _delta(m.get("max_drawdown_R"), base_m.get("max_drawdown_R")),
            "win_rate_delta": _delta(m.get("win_rate"), base_m.get("win_rate")),
            "baseline_expectancy_ci": [
                base_m.get("expectancy_ci_low"),
                base_m.get("expectancy_ci_high"),
            ],
            "variant_expectancy_ci": [
                m.get("expectancy_ci_low"),
                m.get("expectancy_ci_high"),
            ],
            "note": "Descriptive deltas only — not a winner ranking.",
        }

    period_start = period_end = None
    if series and start < len(series):
        ts0 = candle_time(series[start])
        ts1 = candle_time(series[end - 1] if end > 0 else series[-1])
        period_start = ts0.isoformat() if ts0 else None
        period_end = ts1.isoformat() if ts1 else None

    return {
        "status": "OK",
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "period_start": period_start,
        "period_end": period_end,
        "shared_funnel": shared_funnel,
        "lifecycle_stats": lifecycle_stats,
        "baseline_htf_rejection_distribution": dict(baseline_htf_reasons),
        "variants": variant_results,
        "pairwise_vs_baseline": pairwise,
        "lookahead": {
            "pass": len(lookahead_violations) == 0,
            "violations": lookahead_violations,
        },
        "pre_htf_path_identical": True,
        "note": (
            "All variants share BOS→pullback→retest→S/D path; "
            "only HTF eligibility differs. Research only."
        ),
        "disclaimer": DISCLAIMER,
        "elapsed_seconds": time.perf_counter() - t0,
        "live_engines_unchanged": True,
        "htf_logic_production_unchanged": True,
    }


def _delta(a: Any, b: Any) -> float | None:
    if a is None or b is None:
        return None
    try:
        return float(a) - float(b)
    except (TypeError, ValueError):
        return None


def reconcile_sep2024(
    symbol_results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Verify baseline SD/HTF counts match forensic Sep 2024 expectations."""
    rows: dict[str, Any] = {}
    all_ok = True
    for sym, expected in SEP2024_EXPECTED.items():
        payload = symbol_results.get(sym) or {}
        shared = payload.get("shared_funnel") or {}
        base = (payload.get("variants") or {}).get("S3_BASELINE") or {}
        funnel = base.get("funnel") or shared
        sd = int(funnel.get("sd_pass") or shared.get("sd_pass") or 0)
        htf = int(funnel.get("htf_pass") or 0)
        ok = sd == expected["sd_pass"] and htf == expected["htf_pass"]
        if not ok:
            all_ok = False
        rows[sym] = {
            "expected": expected,
            "observed": {"sd_pass": sd, "htf_pass": htf},
            "match": ok,
        }
    return {
        "window": {"start": "2024-09-01", "end": "2024-10-01"},
        "match": all_ok,
        "symbols": rows,
        "action_if_fail": "STOP and investigate — do not trust sensitivity results",
    }


def build_robustness(
    aggregated_variants: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for vid, payload in aggregated_variants.items():
        by_sym = (payload.get("metrics") or {}).get("by_symbol") or {}
        by_year = (payload.get("metrics") or {}).get("by_year") or {}
        all_m = (payload.get("metrics") or {}).get("all") or {}
        long_m = (payload.get("metrics") or {}).get("long") or {}
        short_m = (payload.get("metrics") or {}).get("short") or {}
        oos = ((payload.get("metrics") or {}).get("train_val_oos") or {}).get("oos") or {}

        counts = {s: (v.get("sample_size") or 0) for s, v in by_sym.items()}
        total = sum(counts.values()) or 1
        concentration = {
            s: round(c / total, 3) for s, c in counts.items()
        }
        max_share = max(concentration.values()) if concentration else 0.0
        year_n = {y: (v.get("n") or 0) for y, v in by_year.items()}
        flags: list[str] = []
        if all_m.get("sample_status") in ("INSUFFICIENT_SAMPLE", "VERY_SMALL_SAMPLE"):
            flags.append(str(all_m.get("sample_status")))
        if max_share >= 0.7:
            flags.append("CONCENTRATED_IN_ONE_SYMBOL")
        if len([n for n in year_n.values() if n > 0]) <= 1 and total >= 5:
            flags.append("CONCENTRATED_IN_ONE_YEAR")
        out[vid] = {
            "sample_status": all_m.get("sample_status"),
            "trade_count": all_m.get("trade_count"),
            "symbol_concentration": concentration,
            "year_counts": year_n,
            "long_vs_short": {
                "LONG": long_m.get("sample_size"),
                "SHORT": short_m.get("sample_size"),
                "LONG_expectancy": long_m.get("expectancy_R"),
                "SHORT_expectancy": short_m.get("expectancy_R"),
            },
            "oos": {
                "n": oos.get("sample_size"),
                "expectancy_R": oos.get("expectancy_R"),
                "sample_status": oos.get("sample_status"),
            },
            "flags": flags,
            "persists_across_symbols": len([c for c in counts.values() if c > 0]) >= 2,
            "persists_oos": (oos.get("sample_size") or 0) > 0,
        }
    return out


def aggregate_symbol_runs(
    symbol_runs: Sequence[Mapping[str, Any]],
    *,
    research_config: StrategyResearchConfig | None = None,
) -> dict[str, Any]:
    """Merge per-symbol sensitivity runs into cross-symbol variant aggregates."""
    rcfg = research_config or StrategyResearchConfig()
    by_variant_trades: dict[str, list[StrategyTrade]] = defaultdict(list)
    by_variant_funnel: dict[str, Counter[str]] = defaultdict(Counter)
    by_variant_htf_rej: dict[str, Counter[str]] = defaultdict(Counter)
    baseline_htf_dist: Counter[str] = Counter()
    shared_notes: list[dict[str, Any]] = []

    all_vids: set[str] = set()
    for run in symbol_runs:
        shared_notes.append(
            {
                "symbol": run.get("symbol"),
                "period_start": run.get("period_start"),
                "period_end": run.get("period_end"),
                "shared_funnel": run.get("shared_funnel"),
            }
        )
        for k, v in (run.get("baseline_htf_rejection_distribution") or {}).items():
            baseline_htf_dist[k] += int(v)
        for vid, payload in (run.get("variants") or {}).items():
            all_vids.add(vid)
            for tdict in payload.get("trades") or []:
                by_variant_trades[vid].append(StrategyTrade(**tdict))
            for fk, fv in (payload.get("funnel") or {}).items():
                by_variant_funnel[vid][fk] += int(fv)
            for rk, rv in (payload.get("htf_rejections") or {}).items():
                by_variant_htf_rej[vid][rk] += int(rv)

    variants_out: dict[str, Any] = {}
    for vid in sorted(all_vids) or list(HTF_VARIANTS.keys()):
        trades = by_variant_trades.get(vid, [])
        metrics = _enriched_metrics(trades, rcfg)
        meta = HTF_VARIANTS.get(vid)
        variants_out[vid] = {
            "variant": {
                "variant_id": vid,
                "name": meta.name if meta else vid,
                "description": meta.description if meta else "",
                "kind": meta.kind if meta else "UNKNOWN",
            },
            "funnel": dict(by_variant_funnel[vid]),
            "htf_rejections": dict(by_variant_htf_rej[vid]),
            "metrics": metrics,
            "trade_count": metrics["all"]["trade_count"],
            "sample_status": metrics["all"]["sample_status"],
        }

    # Pairwise on aggregates
    base_m = variants_out.get("S3_BASELINE", {}).get("metrics", {}).get("all", {})
    pairwise: dict[str, Any] = {}
    for vid, payload in variants_out.items():
        if vid == "S3_BASELINE":
            continue
        m = payload["metrics"]["all"]
        pairwise[vid] = {
            "vs": "S3_BASELINE",
            "trade_count_delta": (m.get("trade_count") or 0)
            - (base_m.get("trade_count") or 0),
            "expectancy_delta": _delta(m.get("expectancy_R"), base_m.get("expectancy_R")),
            "PF_delta": _delta(m.get("profit_factor"), base_m.get("profit_factor")),
            "DD_delta": _delta(m.get("max_drawdown_R"), base_m.get("max_drawdown_R")),
            "win_rate_delta": _delta(m.get("win_rate"), base_m.get("win_rate")),
            "note": "Descriptive only — not a ranking or deployment recommendation.",
        }

    return {
        "per_symbol": shared_notes,
        "variants": variants_out,
        "pairwise_vs_baseline": pairwise,
        "baseline_htf_rejection_distribution": dict(baseline_htf_dist),
        "robustness": build_robustness(variants_out),
        "catalog": list_htf_variants(),
        "disclaimer": DISCLAIMER,
        "no_winner_declared": True,
    }


def run_walk_forward_sensitivity(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    research_config: StrategyResearchConfig | None = None,
    index_start: int = 0,
) -> list[dict[str, Any]]:
    """Chronological walk-forward windows; report each window separately."""
    rcfg = research_config or StrategyResearchConfig()
    n = len(candles)
    windows = walk_forward_windows(
        n,
        train_bars=rcfg.walk_forward_train_bars,
        test_bars=rcfg.walk_forward_test_bars,
    )
    if len(windows) < 1:
        # Fallback smaller windows if series short
        windows = walk_forward_windows(n, train_bars=max(200, n // 3), test_bars=max(100, n // 6))
    out: list[dict[str, Any]] = []
    for wi, win in enumerate(windows):
        tr0 = int(win["train_start"])
        tr1 = int(win["train_end"])
        te0 = int(win["test_start"])
        te1 = int(win["test_end"])
        run = run_s3_htf_sensitivity_symbol(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            index_start=max(index_start, te0),
            index_end=te1,
            research_config=rcfg,
            period_label=f"WF_TEST_{wi}",
        )
        compact = {
            "window_index": wi,
            "train_range": [tr0, tr1],
            "test_range": [te0, te1],
            "variants": {
                vid: {
                    "trade_count": p.get("trade_count"),
                    "sample_status": p.get("sample_status"),
                    "expectancy_R": (p.get("metrics") or {}).get("all", {}).get(
                        "expectancy_R"
                    ),
                    "funnel": p.get("funnel"),
                }
                for vid, p in (run.get("variants") or {}).items()
            },
        }
        out.append(compact)
    return out
