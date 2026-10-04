"""Research-only SHORT entry / stop / TP variant builders.

Never creates paper trades. Never mutates COMBO_02 v1.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from app.research.short_entry_research.constants import (
    DEFAULT_FILTER_THRESHOLDS,
    SAFETY_STAMPS,
    STOP_BOS_HIGH_ATR,
    STOP_CURRENT,
    STOP_FIXED_ATR,
    STOP_SWING_ATR,
    STRATEGY_ID,
    TP_CURRENT,
    TP_FIXED_15R,
    TP_FIXED_1R,
    TP_FIXED_2R,
    TP_SUPPORT,
    VARIANT_BASELINE,
    VARIANT_CHASING_EXCLUSION,
    VARIANT_EXTENSION_FILTER,
    VARIANT_RETEST,
)
from app.research.short_entry_research.entry_classification import (
    classify_entry_quality_exclusive,
    extension_filter_passed,
)
from app.research.short_entry_research.path_metrics import (
    build_stop_price,
    build_tp_price,
    compute_path_geometry,
    resimulate_short_trade,
)
from app.research.short_entry_research.retest_fills import simulate_short_retest_fill
from app.research.short_research_diagnostics.metrics import (
    atr_normalized_distance,
    candle_ohlc,
    entry_delay_bars,
    entry_extension_atr,
)
from app.research.trade_fees import enrich_trade_execution


def variant_fingerprint(
    variant_id: str,
    *,
    window: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    symbols: Sequence[str],
) -> str:
    payload = {
        "strategy_id": STRATEGY_ID,
        "variant_id": variant_id,
        "window": dict(window),
        "thresholds": dict(thresholds),
        "symbols": sorted(str(s).upper() for s in symbols),
        **{k: SAFETY_STAMPS[k] for k in (
            "combo_version", "source", "direction", "parent_run_id", "paper_eligible"
        )},
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _enrich(
    trade: Mapping[str, Any],
    *,
    risk_usd: float = 20.0,
) -> dict[str, Any]:
    row = enrich_trade_execution(trade, risk_usd=risk_usd)
    row["paper_eligible"] = False
    row["production_approved"] = False
    row["telegram_eligible"] = False
    return row


def annotate_entry_quality(
    trade: Mapping[str, Any],
    *,
    atr: float | None,
    bos_level: float | None,
    delay_bars: int | None,
    valid_retest_fill: bool = False,
    thresholds: Mapping[str, float | int] | None = None,
) -> dict[str, Any]:
    ext = entry_extension_atr(
        float(trade["entry_price"]), bos_level, atr
    )
    return classify_entry_quality_exclusive(
        valid_retest_fill=valid_retest_fill,
        extension_atr=ext,
        delay_bars=delay_bars,
        entry_price=float(trade["entry_price"]),
        bos_level=bos_level,
        atr=atr,
        thresholds=thresholds,
    )


def apply_baseline_variant(
    trades: Sequence[Mapping[str, Any]],
    *,
    candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    atr_by_trade: Mapping[str, float | None] | None = None,
    thresholds: Mapping[str, float | int] | None = None,
    risk_usd: float = 20.0,
) -> list[dict[str, Any]]:
    """Variant A — market entry, current stop/TP (pass-through + classification)."""
    out: list[dict[str, Any]] = []
    for i, t in enumerate(trades):
        if str(t.get("outcome") or "") in ("", "OPEN", "None"):
            continue
        sym = str(t.get("symbol") or "").upper()
        candles = candles_by_symbol.get(sym) or []
        entry_idx = t.get("entry_index")
        atr = t.get("atr_at_signal")
        if atr is None and atr_by_trade:
            atr = atr_by_trade.get(f"{sym}:{entry_idx}")
        bos_level = t.get("bos_level") or t.get("broken_level")
        delay = t.get("entry_delay_bars")
        if delay is None:
            delay = 0  # market entry at BOS bar when blotter omits delay
        # Prefer blotter extension when present for stable classification vs parent
        blotter_ext = t.get("entry_extension_atr")
        q = classify_entry_quality_exclusive(
            valid_retest_fill=False,
            extension_atr=float(blotter_ext) if blotter_ext is not None else entry_extension_atr(
                float(t["entry_price"]),
                float(bos_level) if bos_level is not None else None,
                atr,
            ),
            delay_bars=int(delay) if delay is not None else None,
            entry_price=float(t["entry_price"]),
            bos_level=float(bos_level) if bos_level is not None else None,
            atr=atr,
            thresholds=thresholds,
        )
        path = {}
        if entry_idx is not None and candles:
            path = compute_path_geometry(
                entry_price=float(t["entry_price"]),
                stop_price=float(t["stop_price"]),
                tp1=t.get("tp1") or t.get("TP1"),
                candles=candles,
                entry_index=int(entry_idx),
                exit_index=t.get("exit_index"),
                atr=atr,
                support_level=t.get("support_level") or t.get("recent_swing_low"),
            )
        enriched = _enrich(
            {
                **t,
                "entry_type": str(t.get("entry_type") or "MARKET"),
                "direction": "SHORT",
            },
            risk_usd=risk_usd,
        )
        # Prefer blotter fields when present
        for k in ("gross_pnl", "net_pnl", "fees", "r_net", "R"):
            if t.get(k) is not None and enriched.get(k) is None:
                enriched[k] = t.get(k)
        if t.get("gross_pnl") is not None:
            enriched["gross_pnl"] = t["gross_pnl"]
        if t.get("net_pnl") is not None:
            enriched["net_pnl"] = t["net_pnl"]
        if t.get("fees") is not None:
            enriched["fees"] = t["fees"]
            enriched["total_fee"] = t["fees"]
        if t.get("R") is not None:
            enriched["R"] = t["R"]
            enriched["r_net"] = t["R"]
        out.append(
            {
                **enriched,
                **q,
                **path,
                "variant_id": VARIANT_BASELINE,
                "filter_passed": True,
                **SAFETY_STAMPS,
            }
        )
    return out


def apply_chasing_exclusion(
    baseline_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Variant D — drop CHASING primary labels."""
    out = []
    for r in baseline_rows:
        if str(r.get("entry_quality_primary") or "") == "CHASING":
            continue
        row = dict(r)
        row["variant_id"] = VARIANT_CHASING_EXCLUSION
        row["filter_passed"] = True
        row.update(SAFETY_STAMPS)
        out.append(row)
    return out


def apply_extension_filter(
    baseline_rows: Sequence[Mapping[str, Any]],
    *,
    thresholds: Mapping[str, float | int] | None = None,
) -> list[dict[str, Any]]:
    """Variant C — keep only rows with extension/delay within thresholds."""
    th = {**DEFAULT_FILTER_THRESHOLDS, **dict(thresholds or {})}
    out = []
    for r in baseline_rows:
        passed = extension_filter_passed(
            extension_atr=r.get("extension_atr") or r.get("entry_extension_atr"),
            delay_bars=r.get("delay_bars") if r.get("delay_bars") is not None else r.get("entry_delay_bars"),
            thresholds=th,
        )
        if not passed:
            continue
        row = dict(r)
        row["variant_id"] = VARIANT_EXTENSION_FILTER
        row["filter_passed"] = True
        row["filter_thresholds"] = {
            "extension_filter_max_atr": th["extension_filter_max_atr"],
            "extension_filter_max_delay_bars": th["extension_filter_max_delay_bars"],
        }
        row.update(SAFETY_STAMPS)
        out.append(row)
    return out


def apply_retest_variant(
    baseline_trades: Sequence[Mapping[str, Any]],
    *,
    candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    atr_lookup: Mapping[str, float | None] | None = None,
    thresholds: Mapping[str, float | int] | None = None,
    risk_usd: float = 20.0,
) -> list[dict[str, Any]]:
    """Variant B — limit retest after BOS; no retroactive BOS fill."""
    th = {**DEFAULT_FILTER_THRESHOLDS, **dict(thresholds or {})}
    out: list[dict[str, Any]] = []
    for t in baseline_trades:
        sym = str(t.get("symbol") or "").upper()
        candles = candles_by_symbol.get(sym) or []
        bos_idx = t.get("entry_index")
        if bos_idx is None or not candles:
            continue
        bos_idx = int(bos_idx)
        bos_level = t.get("bos_level") or t.get("broken_level")
        if bos_level is None:
            # Proxy: use entry as near-BOS close; broken level slightly above for SHORT
            # Prefer structural stop context swing — recent swing low approx
            bos_level = t.get("recent_swing_low") or t.get("support_level")
        if bos_level is None:
            # Last resort: use entry price as broken-level proxy (MARKET filled at close below)
            bos_level = float(t["entry_price"])
        atr = None
        if atr_lookup:
            atr = atr_lookup.get(f"{sym}:{bos_idx}")
        fill = simulate_short_retest_fill(
            candles,
            bos_index=bos_idx,
            bos_level=float(bos_level),
            atr=atr,
            expiry_bars=int(th["retest_expiry_bars"]),
            tolerance_atr=float(th["retest_tolerance_atr"]),
            allow_bos_candle_fill=False,
        )
        if not fill.get("filled"):
            continue
        fill_idx = int(fill["fill_index"])
        entry_px = float(fill["fill_price"])
        # Keep structural stop relative to original risk geometry when possible
        stop_px = float(t["stop_price"])
        if stop_px <= entry_px:
            # Ensure valid SHORT geometry
            risk = abs(float(t["entry_price"]) - float(t["stop_price"]))
            stop_px = entry_px + max(risk, 1e-9)
        tp1 = t.get("tp1") or t.get("TP1")
        if tp1 is not None and float(tp1) >= entry_px:
            risk = stop_px - entry_px
            tp1 = entry_px - 2.0 * risk
        sim = resimulate_short_trade(
            symbol=sym,
            candles=candles,
            entry_index=fill_idx,
            entry_price=entry_px,
            stop_price=stop_px,
            tp1=float(tp1) if tp1 is not None else None,
            tp2=t.get("tp2") or t.get("TP2"),
            tp3=t.get("tp3") or t.get("TP3"),
            signal_time=fill.get("fill_time"),
        )
        if str(sim.get("outcome") or "") in ("", "OPEN", "None"):
            continue
        delay = entry_delay_bars(fill.get("fill_time"), fill.get("bos_time"))
        if delay is None:
            delay = fill_idx - bos_idx
        q = classify_entry_quality_exclusive(
            valid_retest_fill=True,
            extension_atr=entry_extension_atr(entry_px, float(bos_level), atr),
            delay_bars=delay,
            entry_price=entry_px,
            bos_level=float(bos_level),
            atr=atr,
            thresholds=th,
        )
        path = compute_path_geometry(
            entry_price=entry_px,
            stop_price=stop_px,
            tp1=float(tp1) if tp1 is not None else None,
            candles=candles,
            entry_index=fill_idx,
            exit_index=sim.get("exit_index"),
            atr=atr,
            support_level=t.get("support_level") or t.get("recent_swing_low"),
        )
        trade_row = {
            **sim,
            "symbol": sym,
            "direction": "SHORT",
            "entry_type": "LIMIT_RETEST",
            "entry_price": entry_px,
            "stop_price": stop_px,
            "tp1": tp1,
            "entry_index": fill_idx,
            "bos_level": float(bos_level),
            **fill,
        }
        enriched = _enrich(trade_row, risk_usd=risk_usd)
        out.append(
            {
                **enriched,
                **q,
                **path,
                "variant_id": VARIANT_RETEST,
                "filter_passed": True,
                **SAFETY_STAMPS,
            }
        )
    return out


def apply_stop_variant(
    baseline_trades: Sequence[Mapping[str, Any]],
    *,
    stop_variant: str,
    candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    atr_lookup: Mapping[str, float | None] | None = None,
    thresholds: Mapping[str, float | int] | None = None,
    risk_usd: float = 20.0,
) -> list[dict[str, Any]]:
    th = {**DEFAULT_FILTER_THRESHOLDS, **dict(thresholds or {})}
    out: list[dict[str, Any]] = []
    for t in baseline_trades:
        if str(t.get("outcome") or "") in ("", "OPEN", "None"):
            continue
        sym = str(t.get("symbol") or "").upper()
        candles = candles_by_symbol.get(sym) or []
        entry_idx = t.get("entry_index")
        if entry_idx is None or not candles:
            continue
        entry_idx = int(entry_idx)
        atr = atr_lookup.get(f"{sym}:{entry_idx}") if atr_lookup else None
        bos_high = None
        if entry_idx < len(candles):
            _, bos_high, _, _ = candle_ohlc(candles[entry_idx])
        stop_px = build_stop_price(
            variant=stop_variant,
            entry_price=float(t["entry_price"]),
            current_stop=float(t["stop_price"]),
            atr=atr,
            bos_candle_high=bos_high,
            swing_high=t.get("recent_swing_high"),
            thresholds=th,
        )
        if stop_px <= float(t["entry_price"]):
            continue
        sim = resimulate_short_trade(
            symbol=sym,
            candles=candles,
            entry_index=entry_idx,
            entry_price=float(t["entry_price"]),
            stop_price=stop_px,
            tp1=t.get("tp1") or t.get("TP1"),
            tp2=t.get("tp2") or t.get("TP2"),
            tp3=t.get("tp3") or t.get("TP3"),
            signal_time=t.get("entry_time") or t.get("signal_time"),
        )
        if str(sim.get("outcome") or "") in ("", "OPEN", "None"):
            continue
        path = compute_path_geometry(
            entry_price=float(t["entry_price"]),
            stop_price=stop_px,
            tp1=t.get("tp1") or t.get("TP1"),
            candles=candles,
            entry_index=entry_idx,
            exit_index=sim.get("exit_index"),
            atr=atr,
        )
        enriched = _enrich(
            {**sim, "symbol": sym, "direction": "SHORT", "entry_type": t.get("entry_type") or "MARKET"},
            risk_usd=risk_usd,
        )
        out.append(
            {
                **enriched,
                **path,
                "variant_id": stop_variant,
                "stop_variant": stop_variant,
                **SAFETY_STAMPS,
            }
        )
    return out


def apply_tp_variant(
    baseline_trades: Sequence[Mapping[str, Any]],
    *,
    tp_variant: str,
    candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    atr_lookup: Mapping[str, float | None] | None = None,
    risk_usd: float = 20.0,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in baseline_trades:
        if str(t.get("outcome") or "") in ("", "OPEN", "None"):
            continue
        sym = str(t.get("symbol") or "").upper()
        candles = candles_by_symbol.get(sym) or []
        entry_idx = t.get("entry_index")
        if entry_idx is None or not candles:
            continue
        entry_idx = int(entry_idx)
        atr = atr_lookup.get(f"{sym}:{entry_idx}") if atr_lookup else None
        tp1 = build_tp_price(
            variant=tp_variant,
            entry_price=float(t["entry_price"]),
            stop_price=float(t["stop_price"]),
            current_tp=t.get("tp1") or t.get("TP1"),
            support_level=t.get("support_level") or t.get("recent_swing_low"),
        )
        if tp1 is None or float(tp1) >= float(t["entry_price"]):
            continue
        sim = resimulate_short_trade(
            symbol=sym,
            candles=candles,
            entry_index=entry_idx,
            entry_price=float(t["entry_price"]),
            stop_price=float(t["stop_price"]),
            tp1=float(tp1),
            tp2=None,
            tp3=None,
            signal_time=t.get("entry_time") or t.get("signal_time"),
        )
        if str(sim.get("outcome") or "") in ("", "OPEN", "None"):
            continue
        path = compute_path_geometry(
            entry_price=float(t["entry_price"]),
            stop_price=float(t["stop_price"]),
            tp1=float(tp1),
            candles=candles,
            entry_index=entry_idx,
            exit_index=sim.get("exit_index"),
            atr=atr,
            support_level=t.get("support_level") or t.get("recent_swing_low"),
        )
        enriched = _enrich(
            {**sim, "symbol": sym, "direction": "SHORT", "entry_type": t.get("entry_type") or "MARKET", "tp1": tp1},
            risk_usd=risk_usd,
        )
        out.append(
            {
                **enriched,
                **path,
                "variant_id": tp_variant,
                "tp_variant": tp_variant,
                **SAFETY_STAMPS,
            }
        )
    return out


ENTRY_VARIANT_IDS = (
    VARIANT_BASELINE,
    VARIANT_RETEST,
    VARIANT_EXTENSION_FILTER,
    VARIANT_CHASING_EXCLUSION,
)

STOP_VARIANT_IDS = (
    STOP_CURRENT,
    STOP_SWING_ATR,
    STOP_BOS_HIGH_ATR,
    STOP_FIXED_ATR,
)

TP_VARIANT_IDS = (
    TP_CURRENT,
    TP_SUPPORT,
    TP_FIXED_1R,
    TP_FIXED_15R,
    TP_FIXED_2R,
)
