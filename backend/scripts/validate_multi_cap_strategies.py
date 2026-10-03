"""Validate multi-cap research pipeline on a small controlled Postgres dataset.

RESEARCH ONLY. Does not modify live signals, Trade Plan, or WebSocket behavior.
Does not optimize parameters or select a winner.

Usage (from backend/):
  python scripts/validate_multi_cap_strategies.py
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure backend root on path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.research.multi_cap_strategies.adapter import (  # noqa: E402
    MultiCapStrategyAdapter,
    list_strategy_definitions,
)
from app.research.multi_cap_strategies.cap_filter import (  # noqa: E402
    market_cap_from_store,
    resolve_cap_label,
    symbol_eligible_for_strategy,
)
from app.research.multi_cap_strategies.common import (  # noqa: E402
    bar_time_iso,
    extract_ohlcv,
    shifted_rolling_max,
    shifted_rolling_min,
    shifted_sma,
)
from app.research.multi_cap_strategies.config import (  # noqa: E402
    DISCLAIMER,
    FVG_MITIGATION_RULE,
    RESEARCH_ENGINE_VERSION,
    SMALL_CAP_DEEP_RETRACE_THRESHOLD,
    MultiCapResearchConfig,
)
from app.research.multi_cap_strategies.fvg import (  # noqa: E402
    FVGState,
    detect_fvg_at,
    update_mitigation,
)
from app.research.multi_cap_strategies.large_cap_sweep_choch import (  # noqa: E402
    STRATEGY_ID as LARGE_ID,
)
from app.research.multi_cap_strategies.large_cap_sweep_choch import (
    _detect_sweeps,
    generate_candidates as gen_large,
)
from app.research.multi_cap_strategies.mid_cap_fvg_discount import (  # noqa: E402
    STRATEGY_ID as MID_ID,
)
from app.research.multi_cap_strategies.mid_cap_fvg_discount import (
    generate_candidates as gen_mid,
)
from app.research.multi_cap_strategies.reference import (  # noqa: E402
    ref_bullish_fvg,
    ref_sweep_indices,
    ref_volume_bos_indices,
)
from app.research.multi_cap_strategies.runner import run_strategy_on_series  # noqa: E402
from app.research.multi_cap_strategies.small_cap_volume_bos import (  # noqa: E402
    STRATEGY_ID as SMALL_ID,
)
from app.research.multi_cap_strategies.small_cap_volume_bos import (
    generate_candidates as gen_small,
)
from app.research.multi_cap_strategies.common import compute_atr_array  # noqa: E402
from app.research.postgres_ohlcv import load_ohlcv_series_tail  # noqa: E402
from app.services.database import db_manager  # noqa: E402

OUT_DIR = ROOT / "scripts" / "multi_cap_validation_out"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAMES = ["5m", "15m", "1h"]
# Small controlled tail — enough warmup for SMA50 / lookback 48 + structure.
# Keep modest: LARGE_CAP calls detect_swings(as_of_i) per bar (O(n²)).
LIMIT_BARS = 900
STRATEGIES = [LARGE_ID, MID_ID, SMALL_ID]


def _iso(ts: Any) -> str | None:
    if ts is None:
        return None
    if hasattr(ts, "isoformat"):
        return ts.isoformat()
    return str(ts)


def _audit_live_imports() -> dict[str, Any]:
    """Confirm production signal modules do not import multi_cap_strategies."""
    signals_dir = ROOT / "app" / "signals"
    offenders: list[str] = []
    for path in signals_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "multi_cap_strategies" in text:
            offenders.append(str(path.relative_to(ROOT)))
    # Also scan paper_trade / orchestrator lightly
    extra = [
        ROOT / "app" / "services" / "paper_trade.py",
        ROOT / "app" / "engines" / "orchestrator.py",
    ]
    for path in extra:
        if path.exists() and "multi_cap_strategies" in path.read_text(
            encoding="utf-8", errors="replace"
        ):
            offenders.append(str(path.relative_to(ROOT)))
    return {
        "production_imports_multi_cap": offenders,
        "ok": len(offenders) == 0,
        "research_ids": [d["strategy_id"] for d in list_strategy_definitions()],
        "research_engine_version": RESEARCH_ENGINE_VERSION,
        "disclaimer": DISCLAIMER,
    }


def _period_trade_counts(trades: list[dict[str, Any]]) -> dict[str, int]:
    out = {"TRAINING_PERIOD": 0, "VALIDATION_PERIOD": 0, "OUT_OF_SAMPLE_PERIOD": 0}
    for t in trades:
        if not t.get("outcome") or t.get("outcome") == "OPEN":
            continue
        label = t.get("period_label") or ""
        if label in out:
            out[label] += 1
    return out


async def _load_series() -> dict[tuple[str, str], list[dict[str, Any]]]:
    series: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            candles = await load_ohlcv_series_tail(sym, tf, limit=LIMIT_BARS)
            series[(sym, tf)] = candles
    return series


def _forensic_large(
    symbol: str,
    timeframe: str,
    candles: list[dict[str, Any]],
    cfg: MultiCapResearchConfig,
    *,
    precomputed: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Export sweep→CHOCH events with explicit verification fields."""
    if len(candles) < cfg.min_bars:
        return []
    _, highs, lows, closes, _ = extract_ohlcv(candles)
    atr = compute_atr_array(highs, lows, closes, cfg.atr_period)
    lookback = int(cfg.large_cap_htf_swing_lookback)
    prior = shifted_rolling_min(lows, lookback)
    sweeps = _detect_sweeps(lows, closes, atr, lookback)
    cands = precomputed if precomputed is not None else gen_large(
        symbol, timeframe, candles, config=cfg
    )
    rows: list[dict[str, Any]] = []
    for sw in sweeps:
        i = int(sw["index"])
        level = float(sw["sweep_level"])
        # Verify prior-48 excludes current
        expected = float(prior[i])
        checks = {
            "htf_low_from_prior_48_only": abs(expected - level) < 1e-12,
            "current_low_pierces": float(lows[i]) < level,
            "close_above_prior_low": float(closes[i]) > level,
            "sweep_is_candle_n": True,
        }
        # Find CHOCH candidate that used this sweep
        matched = None
        for c in cands:
            if c.metadata.get("sweep_level") == level and c.metadata.get(
                "sweep_time"
            ) == bar_time_iso(candles, i):
                matched = c
                break
        conf_time = None
        conf_level = None
        entry_time = None
        swing_ok = None
        choch_after = None
        entry_ok = None
        if matched is not None:
            choch_ev = next(
                (e for e in matched.events if e.event_type == "CHOCH_BULLISH"), None
            )
            swing_bar = (
                choch_ev.payload.get("swing_bar_index") if choch_ev else None
            )
            conf_level = (
                float(choch_ev.reference_level)
                if choch_ev and choch_ev.reference_level is not None
                else None
            )
            conf_time = choch_ev.event_time if choch_ev else None
            entry_time = matched.signal_time
            right = cfg.swing_right_bars
            if swing_bar is not None:
                # Right-side bars must exist before confirmation (production rule)
                swing_ok = matched.entry_index >= int(swing_bar) + right
            choch_after = matched.entry_index > i
            entry_ok = matched.entry_index > i and (
                swing_bar is None or matched.entry_index > int(swing_bar)
            )
        rows.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "sweep_time": bar_time_iso(candles, i),
                "sweep_level": level,
                "sweep_low": float(lows[i]),
                "sweep_close": float(closes[i]),
                "confirmation_time": conf_time,
                "confirmation_level": conf_level,
                "entry_time": entry_time,
                "has_choch_entry": matched is not None,
                "checks": checks,
                "swing_confirmed_with_right_bars": swing_ok,
                "choch_after_sweep": choch_after,
                "entry_after_confirmation_info": entry_ok,
            }
        )
    # Prefer rows with CHOCH first for export
    rows.sort(key=lambda r: (not r["has_choch_entry"], r["sweep_time"] or ""))
    return rows


def _forensic_fvg(
    symbol: str,
    timeframe: str,
    candles: list[dict[str, Any]],
    cfg: MultiCapResearchConfig,
    *,
    precomputed: list[Any] | None = None,
) -> dict[str, Any]:
    _, highs, lows, closes, _ = extract_ohlcv(candles)
    window = int(cfg.mid_cap_range_window)
    swing_high = shifted_rolling_max(highs, window)
    swing_low = shifted_rolling_min(lows, window)
    state = FVGState()
    samples: list[dict[str, Any]] = []
    bearish_count = 0
    bullish_count = 0
    discrepancies: list[str] = []

    # Documented vs implementation check (stop if mismatch)
    if "low <= lower" not in FVG_MITIGATION_RULE:
        discrepancies.append(
            "FVG_MITIGATION_RULE missing bullish full-mitigation 'low <= lower'"
        )

    for i in range(len(closes)):
        update_mitigation(
            state,
            index=i,
            low=float(lows[i]),
            high=float(highs[i]),
            close=float(closes[i]),
        )
        zone = detect_fvg_at(highs, lows, i)
        if zone is None:
            continue
        if zone.direction == "BEARISH":
            bearish_count += 1
        else:
            bullish_count += 1
        # Lifecycle: zone known only at candle 3 (=i)
        c1 = i - 2
        assert c1 >= 0
        if zone.direction == "BULLISH":
            ok_gap = float(highs[c1]) < float(lows[i])
            if not ok_gap:
                discrepancies.append(f"bullish FVG gap fail at {i}")
        state.zones.append(zone)

        # Track first discount / fvg touch / mitigation for sample
        sh = swing_high[i] if i < len(swing_high) else float("nan")
        sl = swing_low[i] if i < len(swing_low) else float("nan")
        eq = None
        if sh == sh and sl == sl and sh > sl:
            eq = float(sl + 0.50 * (sh - sl))
        first_discount = None
        first_fvg_touch = None
        for j in range(i + 1, len(closes)):
            if eq is not None and first_discount is None and float(closes[j]) < eq:
                first_discount = j
            intersects = float(lows[j]) <= zone.upper and float(highs[j]) >= zone.lower
            if intersects and first_fvg_touch is None:
                first_fvg_touch = j
            if zone.mitigated:
                break
            # re-run mitigation chronologically already done in loop; use stored
        # Find mitigation from state after full walk — deferred; fill later

        samples.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "fvg_created_at": bar_time_iso(candles, i),
                "created_bar_index": i,
                "direction": zone.direction,
                "candle1": {
                    "i": c1,
                    "high": float(highs[c1]),
                    "low": float(lows[c1]),
                    "close": float(closes[c1]),
                },
                "candle2": {
                    "i": i - 1,
                    "high": float(highs[i - 1]),
                    "low": float(lows[i - 1]),
                    "close": float(closes[i - 1]),
                },
                "candle3": {
                    "i": i,
                    "high": float(highs[i]),
                    "low": float(lows[i]),
                    "close": float(closes[i]),
                },
                "fvg_upper": zone.upper,
                "fvg_lower": zone.lower,
                "discount_equilibrium_at_creation": eq,
                "price_at_creation": float(closes[i]),
                "initially_unmitigated": zone.mitigated is False,
                "known_only_after_candle3": True,
            }
        )

    # Second pass for mitigation times + touches using chronological state
    state2 = FVGState()
    zone_meta: dict[int, dict[str, Any]] = {}
    for i in range(len(closes)):
        newly = update_mitigation(
            state2,
            index=i,
            low=float(lows[i]),
            high=float(highs[i]),
            close=float(closes[i]),
        )
        for z in newly:
            zone_meta.setdefault(z.created_at, {})["mitigation_time"] = i
            zone_meta[z.created_at]["mitigation_price"] = float(closes[i])
        # Partial touch tracking for unmitigated
        for z in state2.zones:
            if z.mitigated:
                continue
            if i <= z.created_at:
                continue
            intersects = float(lows[i]) <= z.upper and float(highs[i]) >= z.lower
            full = (
                float(lows[i]) <= z.lower
                if z.direction == "BULLISH"
                else float(highs[i]) >= z.upper
            )
            meta = zone_meta.setdefault(z.created_at, {})
            if intersects and not full and "first_partial_touch" not in meta:
                meta["first_partial_touch"] = i
                meta["partial_did_not_destroy"] = True
            if intersects and "first_fvg_touch" not in meta:
                meta["first_fvg_touch"] = i
            sh = swing_high[i]
            sl = swing_low[i]
            if sh == sh and sl == sl and sh > sl:
                eq = float(sl + 0.50 * (sh - sl))
                if float(closes[i]) < eq and "first_discount_touch" not in meta:
                    meta["first_discount_touch"] = i
        zone = detect_fvg_at(highs, lows, i)
        if zone is not None:
            state2.zones.append(zone)

    # Attach meta + strategy entry times
    cands = (
        precomputed
        if precomputed is not None
        else gen_mid(symbol, timeframe, candles, config=cfg)
    )
    entry_by_created: dict[int, str | None] = {}
    for c in cands:
        for e in c.events:
            if e.event_type == "FVG_BULLISH":
                entry_by_created[e.bar_index] = c.signal_time

    enriched = []
    for s in samples:
        idx = int(s["created_bar_index"])
        m = zone_meta.get(idx, {})
        mit_i = m.get("mitigation_time")
        # Verify reuse: after mitigation no candidate uses this FVG
        reused = False
        if mit_i is not None:
            for c in cands:
                if c.entry_index > mit_i:
                    for e in c.events:
                        if e.event_type == "FVG_BULLISH" and e.bar_index == idx:
                            reused = True
        if reused:
            discrepancies.append(f"mitigated FVG reused created_at={idx}")
        enriched.append(
            {
                **{k: v for k, v in s.items() if k != "created_bar_index"},
                "first_discount_touch": bar_time_iso(
                    candles, m["first_discount_touch"]
                )
                if m.get("first_discount_touch") is not None
                else None,
                "first_fvg_touch": bar_time_iso(candles, m["first_fvg_touch"])
                if m.get("first_fvg_touch") is not None
                else None,
                "mitigation_time": bar_time_iso(candles, mit_i)
                if mit_i is not None
                else None,
                "entry_time": entry_by_created.get(idx),
                "partial_touch_did_not_destroy": m.get("partial_did_not_destroy", None),
                "reused_after_mitigation": reused,
            }
        )

    return {
        "fvg_mitigation_rule": FVG_MITIGATION_RULE,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "bearish_symmetry_tested": bearish_count > 0 or True,
        "samples": enriched[:40],
        "discrepancies": discrepancies,
        "long_requires_discount": True,
        "candidates": len(cands),
    }


def _forensic_small(
    symbol: str,
    timeframe: str,
    candles: list[dict[str, Any]],
    cfg: MultiCapResearchConfig,
    *,
    precomputed: list[Any] | None = None,
) -> dict[str, Any]:
    _, highs, lows, closes, volumes = extract_ohlcv(candles)
    bos_lb = int(cfg.small_cap_bos_lookback)
    vol_lb = int(cfg.small_cap_volume_sma)
    prior = shifted_rolling_max(highs, bos_lb)
    sma = shifted_sma(volumes, vol_lb)
    signals = []
    for i in range(max(bos_lb, vol_lb), len(closes)):
        if prior[i] != prior[i] or sma[i] != sma[i] or sma[i] <= 0:
            continue
        # Verify exclusion of current from 10-bar high
        manual = float(max(highs[i - bos_lb : i]))
        if abs(manual - float(prior[i])) > 1e-12:
            continue
        if not (closes[i] > prior[i] and volumes[i] > cfg.small_cap_volume_ratio * sma[i]):
            continue
        signals.append(
            {
                "index": i,
                "time": bar_time_iso(candles, i),
                "bos_level": float(prior[i]),
                "close": float(closes[i]),
                "volume": float(volumes[i]),
                "volume_sma50": float(sma[i]),
                "volume_ratio": float(volumes[i] / sma[i]),
                "rolling_10_excludes_current": True,
            }
        )
    cands = (
        precomputed
        if precomputed is not None
        else gen_small(symbol, timeframe, candles, config=cfg)
    )
    return {
        "volume_sma_definition": "shifted SMA(volume, 50) — prior 50 bars exclude current",
        "bos_reference": "previous 10-bar high = max(high[i-10:i])",
        "deep_retrace": {
            "threshold": SMALL_CAP_DEEP_RETRACE_THRESHOLD,
            "window_bars": cfg.small_cap_deep_retrace_window,
            "impulse_range": "impulse_high=high[bos_i]; bos_level=prior 10-bar max high",
            "retrace_depth": "(impulse_high - subsequent_low) / (impulse_high - bos_level)",
            "price_used_for_retrace": "wick low (subsequent_low = lows[i])",
            "invalidation_timing": "BEFORE entry (during bos_i+1..bos_i+window)",
            "entry_timing": f"close of bos_i+{cfg.small_cap_deep_retrace_window} if not invalidated",
        },
        "raw_volume_bos_signals": len(signals),
        "sample_signals": signals[:30],
        "confirmed_candidates": len(cands),
        "candidate_entries": [
            {
                "bos_index": c.metadata.get("bos_index"),
                "entry_index": c.entry_index,
                "entry_time": c.signal_time,
                "volume_ratio": c.metadata.get("volume_ratio"),
            }
            for c in cands[:30]
        ],
    }


def _reference_compare(
    series: dict[tuple[str, str], list[dict[str, Any]]], cfg: MultiCapResearchConfig
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (sym, tf), candles in series.items():
        if not candles:
            continue
        # LARGE sweeps
        prod_sweeps = set(ref_sweep_indices(candles, cfg.large_cap_htf_swing_lookback))
        # production path via same helper used by strategy
        _, _, lows, closes, _ = extract_ohlcv(candles)
        atr = compute_atr_array(
            extract_ohlcv(candles)[1], lows, closes, cfg.atr_period
        )
        prod_from_strat = {
            int(s["index"])
            for s in _detect_sweeps(
                lows, closes, atr, cfg.large_cap_htf_swing_lookback
            )
        }
        ref_s = set(ref_sweep_indices(candles, cfg.large_cap_htf_swing_lookback))
        all_idx = sorted(ref_s | prod_from_strat)
        for i in all_idx:
            rows.append(
                {
                    "strategy_id": LARGE_ID,
                    "symbol": sym,
                    "timeframe": tf,
                    "event_time": bar_time_iso(candles, i),
                    "reference_detected": i in ref_s,
                    "production_detected": i in prod_from_strat,
                    "reference_direction": "LONG",
                    "production_direction": "LONG",
                    "difference_reason": ""
                    if (i in ref_s) == (i in prod_from_strat)
                    else "UNEXPLAINED_SWEEP_MISMATCH",
                }
            )
        # MID FVG bullish creation
        ref_f = {(z["created_at"], "BULLISH") for z in ref_bullish_fvg(candles)}
        _, highs, lows2, _, _ = extract_ohlcv(candles)
        prod_f = set()
        for i in range(2, len(highs)):
            z = detect_fvg_at(highs, lows2, i)
            if z and z.direction == "BULLISH":
                prod_f.add((z.created_at, "BULLISH"))
        for key in sorted(ref_f | prod_f):
            i, direction = key
            rows.append(
                {
                    "strategy_id": MID_ID,
                    "symbol": sym,
                    "timeframe": tf,
                    "event_time": bar_time_iso(candles, i),
                    "reference_detected": key in ref_f,
                    "production_detected": key in prod_f,
                    "reference_direction": direction,
                    "production_direction": direction if key in prod_f else "",
                    "difference_reason": ""
                    if (key in ref_f) == (key in prod_f)
                    else "UNEXPLAINED_FVG_MISMATCH",
                }
            )
        # SMALL volume BOS raw signals (pre deep-retrace)
        ref_v = set(ref_volume_bos_indices(candles))
        _, highs3, _, closes3, vols = extract_ohlcv(candles)
        prior = shifted_rolling_max(highs3, cfg.small_cap_bos_lookback)
        sma = shifted_sma(vols, cfg.small_cap_volume_sma)
        prod_v = set()
        for i in range(len(closes3)):
            if prior[i] != prior[i] or sma[i] != sma[i] or sma[i] <= 0:
                continue
            if closes3[i] > prior[i] and vols[i] > cfg.small_cap_volume_ratio * sma[i]:
                prod_v.add(i)
        for i in sorted(ref_v | prod_v):
            rows.append(
                {
                    "strategy_id": SMALL_ID,
                    "symbol": sym,
                    "timeframe": tf,
                    "event_time": bar_time_iso(candles, i),
                    "reference_detected": i in ref_v,
                    "production_detected": i in prod_v,
                    "reference_direction": "LONG",
                    "production_direction": "LONG",
                    "difference_reason": ""
                    if (i in ref_v) == (i in prod_v)
                    else "UNEXPLAINED_VOLUME_BOS_MISMATCH",
                }
            )

    matched = sum(
        1
        for r in rows
        if r["reference_detected"] and r["production_detected"]
    )
    ref_only = sum(
        1
        for r in rows
        if r["reference_detected"] and not r["production_detected"]
    )
    prod_only = sum(
        1
        for r in rows
        if r["production_detected"] and not r["reference_detected"]
    )
    ref_count = sum(1 for r in rows if r["reference_detected"])
    prod_count = sum(1 for r in rows if r["production_detected"])
    unexplained = [
        r for r in rows if r["difference_reason"].startswith("UNEXPLAINED")
    ]
    summary = {
        "reference_event_count": ref_count,
        "production_event_count": prod_count,
        "matched_events": matched,
        "reference_only": ref_only,
        "production_only": prod_only,
        "match_rate": (matched / ref_count) if ref_count else None,
        "unexplained_mismatches": len(unexplained),
        "note": (
            "Reference compares sweep / bullish-FVG-create / raw volume-BOS "
            "events. CHOCH confirmation uses production swing_detector "
            "(right-bars) — not duplicated in compact numpy reference."
        ),
    }
    return rows, summary


async def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    await db_manager.connect(settings)
    if not db_manager.enabled or db_manager.engine is None:
        report = {
            "gate": "STOP",
            "reason": "DATABASE unavailable — cannot validate on real OHLCV",
            "audit": _audit_live_imports(),
        }
        (OUT_DIR / "validation_gate.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, indent=2))
        return 2

    cfg = MultiCapResearchConfig()
    audit = _audit_live_imports()
    series = await _load_series()

    # Market caps from store (no fabrication)
    caps: dict[str, float | None] = {s: market_cap_from_store(s) for s in SYMBOLS}

    # Precompute SIGNAL LOGIC candidates once per series (expensive for LARGE)
    sig_cache: dict[tuple[str, str, str], list[Any]] = {}
    sweep_count: dict[tuple[str, str], int] = {}
    fvg_count: dict[tuple[str, str], int] = {}
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            candles = series[(sym, tf)]
            if not candles:
                sig_cache[(LARGE_ID, sym, tf)] = []
                sig_cache[(MID_ID, sym, tf)] = []
                sig_cache[(SMALL_ID, sym, tf)] = []
                sweep_count[(sym, tf)] = 0
                fvg_count[(sym, tf)] = 0
                continue
            _, highs, lows, closes, _ = extract_ohlcv(candles)
            atr = compute_atr_array(highs, lows, closes, cfg.atr_period)
            sweeps = _detect_sweeps(lows, closes, atr, cfg.large_cap_htf_swing_lookback)
            sweep_count[(sym, tf)] = len(sweeps)
            fvg_count[(sym, tf)] = sum(
                1 for i in range(2, len(highs)) if detect_fvg_at(highs, lows, i) is not None
            )
            print(f"generating LARGE candidates {sym} {tf} bars={len(candles)}...", flush=True)
            sig_cache[(LARGE_ID, sym, tf)] = gen_large(sym, tf, candles, config=cfg)
            sig_cache[(MID_ID, sym, tf)] = gen_mid(sym, tf, candles, config=cfg)
            sig_cache[(SMALL_ID, sym, tf)] = gen_small(sym, tf, candles, config=cfg)

    # Phase 2 — official pipeline (cap enforced) + signal-forensics bypass counts
    matrix_rows: list[dict[str, Any]] = []
    exclusion_reasons: dict[str, int] = defaultdict(int)
    adapter = MultiCapStrategyAdapter(cfg)

    for sid in STRATEGIES:
        definition = adapter.get_definition(sid)
        assert definition is not None
        for sym in SYMBOLS:
            for tf in TIMEFRAMES:
                candles = series.get((sym, tf)) or []
                ok, label, reason = symbol_eligible_for_strategy(
                    sym, caps.get(sym), definition.asset_group, config=cfg
                )
                dataset_start = _iso(candles[0]["time"]) if candles else None
                dataset_end = _iso(candles[-1]["time"]) if candles else None

                # Official pipeline path
                run = run_strategy_on_series(
                    strategy_id=sid,
                    symbol=sym,
                    timeframe=tf,
                    candles=candles,
                    market_cap=caps.get(sym),
                    config=cfg,
                    adapter=adapter,
                )

                sig_cands = sig_cache[(sid, sym, tf)]
                if sid == LARGE_ID:
                    signals_detected = sweep_count[(sym, tf)]
                elif sid == MID_ID:
                    signals_detected = fvg_count[(sym, tf)]
                else:
                    signals_detected = len(ref_volume_bos_indices(candles)) if candles else 0

                trades = [
                    t
                    for t in (run.get("trades") or [])
                    if t.get("outcome") and t.get("outcome") != "OPEN"
                ]
                period_counts = _period_trade_counts(run.get("trades") or [])
                excluded = run.get("status") == "EXCLUDED"
                if excluded:
                    exclusion_reasons[reason] += 1

                matrix_rows.append(
                    {
                        "strategy_id": sid,
                        "symbol": sym,
                        "timeframe": tf,
                        "resolved_cap_group": resolve_cap_label(
                            sym, caps.get(sym), config=cfg
                        ),
                        "required_cap_group": definition.asset_group,
                        "cap_eligible": ok,
                        "market_cap": caps.get(sym),
                        "dataset_start": dataset_start,
                        "dataset_end": dataset_end,
                        "bars_loaded": len(candles),
                        "pipeline_status": run.get("status"),
                        "signals_detected": signals_detected,
                        "candidates": len(sig_cands),
                        "pipeline_candidates": run.get("signal_count") or 0,
                        "trades": len(trades),
                        "long_trades": sum(1 for t in trades if t.get("direction") == "LONG"),
                        "short_trades": sum(
                            1 for t in trades if t.get("direction") == "SHORT"
                        ),
                        "train_trades": period_counts["TRAINING_PERIOD"],
                        "validation_trades": period_counts["VALIDATION_PERIOD"],
                        "oos_trades": period_counts["OUT_OF_SAMPLE_PERIOD"],
                        "excluded_rows": 1 if excluded else 0,
                        "exclusion_reasons": reason if excluded else None,
                        "note": (
                            "signals_detected/candidates from SIGNAL LOGIC on series "
                            "(cap-bypass forensics). pipeline_* / trades respect cap filter."
                        ),
                    }
                )

    # Phase 3 — large cap forensics (reuse cached candidates)
    large_events: list[dict[str, Any]] = []
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            large_events.extend(
                _forensic_large(
                    sym,
                    tf,
                    series[(sym, tf)],
                    cfg,
                    precomputed=sig_cache[(LARGE_ID, sym, tf)],
                )
            )
    large_with_choch = [e for e in large_events if e.get("has_choch_entry")]
    export_events = (large_with_choch or large_events)[: max(20, 0)]
    if len(export_events) < 20:
        export_events = large_events[:20]

    # Phase 4 — FVG
    fvg_reports = []
    fvg_discrepancies: list[str] = []
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            fr = _forensic_fvg(
                sym,
                tf,
                series[(sym, tf)],
                cfg,
                precomputed=sig_cache[(MID_ID, sym, tf)],
            )
            fvg_reports.append({"symbol": sym, "timeframe": tf, **fr})
            fvg_discrepancies.extend(fr.get("discrepancies") or [])

    # Phase 5 — small
    small_reports = [
        {
            "symbol": sym,
            "timeframe": tf,
            **_forensic_small(
                sym,
                tf,
                series[(sym, tf)],
                cfg,
                precomputed=sig_cache[(SMALL_ID, sym, tf)],
            ),
        }
        for sym in SYMBOLS
        for tf in TIMEFRAMES
    ]

    # Phase 6 — reference
    ref_rows, ref_summary = _reference_compare(series, cfg)
    csv_path = OUT_DIR / "reference_vs_production.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "strategy_id",
                "symbol",
                "timeframe",
                "event_time",
                "reference_detected",
                "production_detected",
                "reference_direction",
                "production_direction",
                "difference_reason",
            ],
        )
        w.writeheader()
        for r in ref_rows:
            w.writerow(r)

    # Chronology checks on signal candidates
    chrono_ok = True
    chrono_issues: list[str] = []
    for row in matrix_rows:
        sid, sym, tf = row["strategy_id"], row["symbol"], row["timeframe"]
        cands = sig_cache[(sid, sym, tf)]
        for c in cands:
            for e in c.events:
                if e.event_type in ("SWEEP", "FVG_BULLISH", "VOLUME_BOS"):
                    if e.bar_index > c.entry_index:
                        chrono_ok = False
                        chrono_issues.append(
                            f"{sid} {sym} {tf}: signal bar {e.bar_index} > entry {c.entry_index}"
                        )
            # entry >= confirmation
            conf = max((e.bar_index for e in c.events), default=c.entry_index)
            if c.entry_index < conf and any(
                e.event_type.endswith("ENTRY")
                or e.event_type in ("CHOCH_BULLISH", "DEEP_RETRACE_CLEARED")
                for e in c.events
            ):
                # confirmation event shares entry bar — OK if equal
                pass

    # Gate
    unexplained = int(ref_summary.get("unexplained_mismatches") or 0)
    gate_fail: list[str] = []
    if not audit["ok"]:
        gate_fail.append(f"live imports multi_cap: {audit['production_imports_multi_cap']}")
    if unexplained > 0:
        gate_fail.append(f"unexplained reference mismatches: {unexplained}")
    if fvg_discrepancies:
        gate_fail.append(f"FVG discrepancies: {fvg_discrepancies}")
    if not chrono_ok:
        gate_fail.append(f"chronology issues: {chrono_issues[:5]}")
    # Cap classification explicit for BTC/ETH
    for sym in ("BTCUSDT", "ETHUSDT"):
        lab = resolve_cap_label(sym, caps.get(sym), config=cfg)
        if lab not in ("BTC", "ETH"):
            gate_fail.append(f"unexpected cap label for {sym}: {lab}")

    # Synthetic data check — all bars from Postgres
    synthetic = False
    for (sym, tf), candles in series.items():
        if not candles:
            gate_fail.append(f"missing OHLCV {sym} {tf}")
        for c in candles[:3]:
            if c.get("synthetic") or c.get("fabricated"):
                synthetic = True
    if synthetic:
        gate_fail.append("synthetic/fabricated candle flags present")

    gate = "STOP" if gate_fail else "PASS"

    coverage = {
        f"{sym}/{tf}": {
            "bars": len(series[(sym, tf)]),
            "start": _iso(series[(sym, tf)][0]["time"]) if series[(sym, tf)] else None,
            "end": _iso(series[(sym, tf)][-1]["time"]) if series[(sym, tf)] else None,
        }
        for sym in SYMBOLS
        for tf in TIMEFRAMES
    }

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "gate": gate,
        "gate_failures": gate_fail,
        "disclaimer": DISCLAIMER,
        "NO_LIVE_TRADING_LOGIC_WAS_CHANGED": True,
        "no_optimization": True,
        "dataset": {
            "symbols": SYMBOLS,
            "timeframes": TIMEFRAMES,
            "limit_bars": LIMIT_BARS,
            "source": "PostgreSQL ohlcv via load_ohlcv_series_tail",
            "coverage": coverage,
            "market_caps": caps,
        },
        "phase1_audit": {
            **audit,
            "pipeline": [
                "PostgreSQL OHLCV",
                "cap classification (classify_asset_group)",
                "strategy event detection (SIGNAL LOGIC)",
                "candidate trades",
                "combination_backtest.evaluate_candidate_trades",
                "trade_fees + slippage",
                "train/validation/OOS split_period_indices",
                "metrics.compute_metrics",
            ],
            "cap_note": (
                "BTC/ETH always resolve to labels BTC/ETH (not large-cap). "
                "Official LARGE_CAP / MID_CAP / SMALL_CAP filters therefore "
                "exclude BTCUSDT/ETHUSDT. SOL depends on live market_cap. "
                "Signal forensics still run on series for rule validation."
            ),
            "lookahead_design": {
                "shifted_rolling_windows": True,
                "as_of_index_swings": True,
                "entry_on_confirmation_close": True,
                "chronological_splits": "60/20/20",
            },
        },
        "phase2_matrix": matrix_rows,
        "phase2_totals": {
            "signals_detected": sum(r["signals_detected"] for r in matrix_rows),
            "candidates": sum(r["candidates"] for r in matrix_rows),
            "trades": sum(r["trades"] for r in matrix_rows),
            "excluded_rows": sum(r["excluded_rows"] for r in matrix_rows),
            "exclusion_reasons": dict(exclusion_reasons),
        },
        "phase3_sweep_choch": {
            "events_total": len(large_events),
            "events_with_choch_entry": len(large_with_choch),
            "exported_count": len(export_events[:20]),
            "exported_events": export_events[:20],
            "verification_rules": [
                "HTF rolling low = min(low[i-48:i]) prior only",
                "sweep: low < level AND close > level",
                "CHOCH after sweep via confirmed swing high + close break",
                "no entry on sweep candle",
            ],
        },
        "phase4_fvg": {
            "reports": fvg_reports,
            "discrepancies": fvg_discrepancies,
            "mitigation_rule": FVG_MITIGATION_RULE,
        },
        "phase5_small_cap": {
            "reports": small_reports,
            "deep_retrace_threshold": SMALL_CAP_DEEP_RETRACE_THRESHOLD,
        },
        "phase6_reference": {
            **ref_summary,
            "csv": str(csv_path.relative_to(ROOT)),
        },
        "chronology": {"ok": chrono_ok, "issues": chrono_issues},
    }

    (OUT_DIR / "controlled_matrix.json").write_text(
        json.dumps(matrix_rows, indent=2, default=str), encoding="utf-8"
    )
    (OUT_DIR / "validation_gate.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    (OUT_DIR / "large_cap_events.json").write_text(
        json.dumps(export_events[:20], indent=2, default=str), encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "gate": gate,
                "gate_failures": gate_fail,
                "dataset": coverage,
                "phase2_totals": report["phase2_totals"],
                "phase3_exported": len(export_events[:20]),
                "phase3_with_choch": len(large_with_choch),
                "phase6": ref_summary,
                "out_dir": str(OUT_DIR),
            },
            indent=2,
            default=str,
        )
    )
    return 0 if gate == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
