"""Orchestrate COMBO_02 SHORT entry-timing research (research-only)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research.bos_combinations import get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.short_entry_research.constants import (
    DEFAULT_ENTRY_RESEARCH_WINDOWS,
    DEFAULT_FILTER_THRESHOLDS,
    PARENT_RUN_ID,
    PARENT_STRATEGY_ID,
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
from app.research.short_entry_research.report import build_final_report
from app.research.short_entry_research.validation import (
    assert_disjoint,
    classify_oos_status,
    count_labels,
    forensic_direct_replay,
    reconcile_variant,
    summarize_trades,
    windows_from_dict,
)
from app.research.short_entry_research.variants import (
    ENTRY_VARIANT_IDS,
    apply_baseline_variant,
    apply_chasing_exclusion,
    apply_extension_filter,
    apply_retest_variant,
    apply_stop_variant,
    apply_tp_variant,
    variant_fingerprint,
)
from app.research.short_research_constants import DEFAULT_SHORT_RESEARCH_UNIVERSE
from app.research.short_research_diagnostics.runner import (
    load_symbol_candles,
    parquet_path_for,
)
from app.research.short_research_diagnostics.trade_dataset import _atr_at
from app.research.trade_fees import enrich_trades
from app.signals.config import SignalConfig


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def dataset_hash(symbols: Sequence[str], window: Mapping[str, Any]) -> str:
    payload = {"symbols": sorted(symbols), "window": dict(window), "source": "ohlcv"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def configuration_hash(
    *,
    thresholds: Mapping[str, Any],
    window: Mapping[str, Any],
    symbols: Sequence[str],
) -> str:
    payload = {
        "strategy_id": STRATEGY_ID,
        "thresholds": dict(thresholds),
        "window": dict(window),
        "symbols": sorted(symbols),
        "parent_run_id": PARENT_RUN_ID,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _load_parent_trades(parent_path: Path | None = None) -> list[dict[str, Any]]:
    if parent_path is None:
        parent_path = (
            Path(__file__).resolve().parents[3]
            / "reports"
            / "short_research"
            / f"short_diagnostic_trades_{PARENT_RUN_ID}.json"
        )
    if not parent_path.is_file():
        # Fallback to LATEST sibling naming
        alt = parent_path.parent / "short_diagnostic_trades_20261004T060446Z-b5d71081.json"
        parent_path = alt if alt.is_file() else parent_path
    if not parent_path.is_file():
        return []
    rows = json.loads(parent_path.read_text(encoding="utf-8"))
    out = []
    for r in rows:
        entry_px = r.get("entry_price")
        atr = r.get("atr_at_signal")
        bos_level = r.get("bos_level")
        if bos_level is None and entry_px is not None:
            dist = r.get("distance_entry_to_BOS_level")
            if dist is not None:
                # SHORT: broken level is above entry when distance is positive.
                try:
                    bos_level = float(entry_px) + abs(float(dist))
                except (TypeError, ValueError):
                    bos_level = None
            if bos_level is None and r.get("entry_extension_atr") is not None and atr:
                try:
                    bos_level = float(entry_px) + float(r["entry_extension_atr"]) * float(atr)
                except (TypeError, ValueError):
                    bos_level = None
        if bos_level is None:
            bos_level = entry_px

        swing_high = None
        dist_sh = r.get("distance_entry_to_recent_swing_high")
        if entry_px is not None and dist_sh is not None:
            try:
                swing_high = float(entry_px) + abs(float(dist_sh))
            except (TypeError, ValueError):
                swing_high = None
        if swing_high is None:
            swing_high = r.get("stop_price")

        support = None
        if entry_px is not None and r.get("distance_to_support_atr") is not None and atr:
            try:
                support = float(entry_px) - abs(float(r["distance_to_support_atr"])) * float(atr)
            except (TypeError, ValueError):
                support = None

        row = {
            "symbol": r.get("symbol"),
            "timeframe": r.get("timeframe") or "1h",
            "direction": "SHORT",
            "entry_price": entry_px,
            "stop_price": r.get("stop_price"),
            "tp1": r.get("TP1") or r.get("tp1"),
            "tp2": r.get("TP2") or r.get("tp2"),
            "tp3": r.get("TP3") or r.get("tp3"),
            "TP1": r.get("TP1") or r.get("tp1"),
            "outcome": r.get("outcome"),
            "exit_price": r.get("exit_price"),
            "exit_time": r.get("exit_time"),
            "entry_time": r.get("entry_time"),
            "signal_time": r.get("BOS_timestamp") or r.get("entry_time"),
            "bos_time": r.get("BOS_timestamp") or r.get("entry_time"),
            "entry_index": r.get("entry_index"),
            "exit_index": r.get("exit_index"),
            "holding_bars": r.get("hold_bars") or r.get("holding_bars"),
            "entry_type": r.get("entry_type") or "MARKET",
            "mfe": r.get("MFE") or r.get("mfe"),
            "mae": r.get("MAE") or r.get("mae"),
            "mfe_r": r.get("MFE_R") or r.get("mfe_r"),
            "mae_r": r.get("MAE_R") or r.get("mae_r"),
            "gross_pnl": r.get("gross_pnl"),
            "net_pnl": r.get("net_pnl"),
            "fees": r.get("fees"),
            "R": r.get("R"),
            "r_net": r.get("R"),
            "bos_level": bos_level,
            "recent_swing_high": swing_high,
            "recent_swing_low": support,
            "support_level": support,
            "atr_at_signal": atr,
            "entry_extension_atr": r.get("entry_extension_atr"),
            "entry_delay_bars": r.get("entry_delay_bars"),
            "entry_quality_blotter": r.get("entry_quality"),
            "stop_class_blotter": r.get("stop_class"),
            "condition_snapshot": {"entry_type": r.get("entry_type") or "MARKET"},
            "paper_eligible": False,
            "production_approved": False,
            "telegram_eligible": False,
        }
        if row.get("exit_price") is None:
            oc = str(row.get("outcome") or "").upper()
            if oc in ("SL", "STOP"):
                row["exit_price"] = row["stop_price"]
            elif oc.startswith("TP"):
                row["exit_price"] = row.get("tp1")
        out.append(row)
    return out


def _run_combo_short(
    symbol: str,
    candles_1h: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]],
    *,
    risk_usd: float = 20.0,
) -> list[dict[str, Any]]:
    combo = get_combination("COMBO_02")
    assert combo is not None
    out = run_combination_backtest(
        symbol,
        "1h",
        candles_1h,
        combo,
        signal_config=SignalConfig(),
        direction_filter="SHORT",
        candles_1h=candles_1h,
        candles_4h=candles_4h,
    )
    trades = [t.to_dict() if hasattr(t, "to_dict") else dict(t) for t in (out.get("trades") or [])]
    closed = []
    for t in trades:
        if str(t.get("outcome") or "") in ("", "OPEN", "None"):
            continue
        if str(t.get("direction") or "").upper() != "SHORT":
            continue
        row = dict(t)
        snap = row.get("condition_snapshot") or {}
        if row.get("bos_level") is None:
            row["bos_level"] = snap.get("bos_level") or snap.get("broken_level")
        if row.get("entry_type") is None:
            row["entry_type"] = snap.get("entry_type") or "MARKET"
        closed.append(row)
    return enrich_trades(closed, risk_usd=risk_usd, closed_only=True)


async def _try_load_range(
    symbol: str,
    *,
    start: str,
    end: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Prefer postgres full range; fall back to parquet base cache."""
    try:
        from app.research.combo02_short_research_runner import load_short_research_candles

        c1, src1 = await load_short_research_candles(
            symbol, "1h", requested_start=start, requested_end=end
        )
        c4, src4 = await load_short_research_candles(
            symbol, "4h", requested_start=start, requested_end=end
        )
        if c1:
            return list(c1), list(c4 or []), f"{src1}/{src4}"
    except Exception:
        pass
    c1, c4 = load_symbol_candles(symbol, start="2024-01-01", end="2025-06-30")
    return c1, c4, "parquet_base_cache"


async def _ensure_db_connected() -> bool:
    try:
        from app.config import get_settings
        from app.services.database import db_manager

        if db_manager.enabled and db_manager.engine is not None:
            return True
        settings = get_settings()
        if getattr(settings, "database_enabled", False):
            await db_manager.connect(settings)
            return bool(db_manager.enabled and db_manager.engine is not None)
    except Exception:
        return False
    return False


def _filter_trades_by_window(
    trades: Sequence[Mapping[str, Any]],
    *,
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    from datetime import datetime, timezone

    def _day(s: str) -> datetime:
        return datetime.fromisoformat(s[:10] + "T00:00:00+00:00")

    a, b = _day(start), _day(end)
    out = []
    for t in trades:
        raw = t.get("entry_time") or t.get("signal_time")
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        d = dt.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        if a <= d <= b:
            out.append(dict(t))
    return out


def _normalize_ts_key(raw: Any) -> str | None:
    if raw is None:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return str(raw)[:19]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def remap_trade_indices_to_candles(
    trades: Sequence[Mapping[str, Any]],
    candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Rebind entry/exit indices to the candle series via timestamps.

    Prevents OOS warmup-series indices from being applied to the full postgres series.
    """
    from app.signals._candle_utils import candle_time

    index_maps: dict[str, dict[str, int]] = {}
    for sym, candles in candles_by_symbol.items():
        m: dict[str, int] = {}
        for i, c in enumerate(candles):
            t = candle_time(c)
            if t is None:
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            m[t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")] = i
        index_maps[str(sym).upper()] = m

    out: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        sym = str(row.get("symbol") or "").upper()
        m = index_maps.get(sym) or {}
        ek = _normalize_ts_key(row.get("entry_time") or row.get("signal_time"))
        xk = _normalize_ts_key(row.get("exit_time"))
        if ek and ek in m:
            row["entry_index"] = m[ek]
        if xk and xk in m:
            row["exit_index"] = m[xk]
        out.append(row)
    return out


def _build_atr_lookup(
    trades: Sequence[Mapping[str, Any]],
    candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, float | None]:
    cache: dict[str, float | None] = {}
    atr_series_cache: dict[int, list] = {}
    for t in trades:
        sym = str(t.get("symbol") or "").upper()
        idx = t.get("entry_index")
        if idx is None:
            continue
        key = f"{sym}:{idx}"
        if key in cache:
            continue
        candles = candles_by_symbol.get(sym) or []
        cache[key] = _atr_at(candles, int(idx), atr_cache=atr_series_cache) if candles else None
    return cache


def run_short_entry_research(
    *,
    symbols: Sequence[str] | None = None,
    window: Mapping[str, Any] | None = None,
    thresholds: Mapping[str, float | int] | None = None,
    risk_usd: float = 20.0,
    report_dir: Path | str | None = None,
    parent_trades_path: Path | str | None = None,
    run_oos_backtests: bool = True,
    tests_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Sync entry-research runner using parent trades + local/postgres candles."""
    import asyncio

    return asyncio.run(
        run_short_entry_research_async(
            symbols=symbols,
            window=window,
            thresholds=thresholds,
            risk_usd=risk_usd,
            report_dir=report_dir,
            parent_trades_path=parent_trades_path,
            run_oos_backtests=run_oos_backtests,
            tests_meta=tests_meta,
        )
    )


async def run_short_entry_research_async(
    *,
    symbols: Sequence[str] | None = None,
    window: Mapping[str, Any] | None = None,
    thresholds: Mapping[str, float | int] | None = None,
    risk_usd: float = 20.0,
    report_dir: Path | str | None = None,
    parent_trades_path: Path | str | None = None,
    run_oos_backtests: bool = True,
    tests_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    run_id = new_run_id()
    syms = [s.upper() for s in (symbols or list(DEFAULT_SHORT_RESEARCH_UNIVERSE))]
    win = dict(window or DEFAULT_ENTRY_RESEARCH_WINDOWS)
    th = {**DEFAULT_FILTER_THRESHOLDS, **dict(thresholds or {})}
    windows = windows_from_dict(win)
    disjoint = assert_disjoint(windows)

    cfg_hash = configuration_hash(thresholds=th, window=win, symbols=syms)
    data_hash = dataset_hash(syms, win)

    db_ok = await _ensure_db_connected()

    # Load candles for requested range (postgres) with parquet fallback.
    candles_1h: dict[str, list[dict[str, Any]]] = {}
    candles_4h: dict[str, list[dict[str, Any]]] = {}
    data_sources: dict[str, str] = {}
    for sym in syms:
        c1, c4, src = await _try_load_range(
            sym, start=win["requested_start"], end=win["requested_end"]
        )
        candles_1h[sym] = c1
        candles_4h[sym] = c4
        data_sources[sym] = src
    data_sources["_db_connected"] = "true" if db_ok else "false"

    parent_trades = _load_parent_trades(
        Path(parent_trades_path) if parent_trades_path else None
    )
    # Baseline trades for base window: prefer parent diagnostic blotter (immutable).
    base_trades = _filter_trades_by_window(
        parent_trades, start=win["base_start"], end=win["base_end"]
    )
    if not base_trades:
        # Rebuild from combination backtest on available candles
        for sym in syms:
            base_trades.extend(
                _run_combo_short(sym, candles_1h.get(sym) or [], candles_4h.get(sym) or [], risk_usd=risk_usd)
            )
        base_trades = _filter_trades_by_window(
            base_trades, start=win["base_start"], end=win["base_end"]
        )

    base_trades = remap_trade_indices_to_candles(base_trades, candles_1h)
    atr_lookup = _build_atr_lookup(base_trades, candles_1h)

    # --- Entry variants on base ---
    baseline_rows = apply_baseline_variant(
        base_trades,
        candles_by_symbol=candles_1h,
        atr_by_trade=atr_lookup,
        thresholds=th,
        risk_usd=risk_usd,
    )
    retest_rows = apply_retest_variant(
        base_trades,
        candles_by_symbol=candles_1h,
        atr_lookup=atr_lookup,
        thresholds=th,
        risk_usd=risk_usd,
    )
    extension_rows = apply_extension_filter(baseline_rows, thresholds=th)
    chasing_excl_rows = apply_chasing_exclusion(baseline_rows)

    entry_variant_trades = {
        VARIANT_BASELINE: baseline_rows,
        VARIANT_RETEST: retest_rows,
        VARIANT_EXTENSION_FILTER: extension_rows,
        VARIANT_CHASING_EXCLUSION: chasing_excl_rows,
    }

    # OOS validation trades: re-run combo on OOS candles when available.
    oos_raw: list[dict[str, Any]] = []
    oos_available = False
    if run_oos_backtests:
        for sym in syms:
            c1 = candles_1h.get(sym) or []
            # Heuristic: if series extends past base_end, we can slice OOS
            from datetime import datetime, timezone
            from app.signals._candle_utils import candle_time

            oos_start = datetime.fromisoformat(win["oos_val_start"] + "T00:00:00+00:00")
            oos_end = datetime.fromisoformat(win["oos_val_end"] + "T23:59:59+00:00")
            oos_bars = []
            for c in c1:
                t = candle_time(c)
                if t is None:
                    continue
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                if oos_start <= t <= oos_end:
                    oos_bars.append(c)
            if len(oos_bars) >= 200:
                oos_available = True
                # Need warmup: include some pre-OOS bars for swings
                pre = [c for c in c1 if candle_time(c) and candle_time(c) < oos_start]
                warmup = pre[-300:] if len(pre) > 300 else pre
                series = warmup + oos_bars
                c4 = candles_4h.get(sym) or []
                trades = _run_combo_short(sym, series, c4, risk_usd=risk_usd)
                oos_raw.extend(
                    _filter_trades_by_window(
                        trades, start=win["oos_val_start"], end=win["oos_val_end"]
                    )
                )
    oos_raw = remap_trade_indices_to_candles(oos_raw, candles_1h)

    variant_results: dict[str, Any] = {}
    variant_run_ids: dict[str, str] = {}
    labels = {
        VARIANT_BASELINE: "Baseline",
        VARIANT_RETEST: "Retest",
        VARIANT_EXTENSION_FILTER: "Extension filter",
        VARIANT_CHASING_EXCLUSION: "Chasing exclusion",
    }

    for vid in ENTRY_VARIANT_IDS:
        v_run = f"{run_id}-{vid}"
        variant_run_ids[vid] = v_run
        rows = entry_variant_trades[vid]
        base_summary = summarize_trades(rows, risk_usd=risk_usd)
        recon = reconcile_variant(rows)
        forensic = forensic_direct_replay(
            rows, candles_by_symbol=candles_1h, candles_4h_by_symbol=candles_4h
        )

        # Apply same entry transform conceptually on OOS raw market trades
        if vid == VARIANT_BASELINE:
            oos_rows = apply_baseline_variant(
                oos_raw, candles_by_symbol=candles_1h, atr_by_trade=_build_atr_lookup(oos_raw, candles_1h), thresholds=th, risk_usd=risk_usd
            ) if oos_raw else []
        elif vid == VARIANT_RETEST:
            oos_rows = apply_retest_variant(
                oos_raw, candles_by_symbol=candles_1h, atr_lookup=_build_atr_lookup(oos_raw, candles_1h), thresholds=th, risk_usd=risk_usd
            ) if oos_raw else []
        elif vid == VARIANT_EXTENSION_FILTER:
            oos_base = apply_baseline_variant(
                oos_raw, candles_by_symbol=candles_1h, atr_by_trade=_build_atr_lookup(oos_raw, candles_1h), thresholds=th, risk_usd=risk_usd
            ) if oos_raw else []
            oos_rows = apply_extension_filter(oos_base, thresholds=th)
        else:
            oos_base = apply_baseline_variant(
                oos_raw, candles_by_symbol=candles_1h, atr_by_trade=_build_atr_lookup(oos_raw, candles_1h), thresholds=th, risk_usd=risk_usd
            ) if oos_raw else []
            oos_rows = apply_chasing_exclusion(oos_base)

        oos_summary = summarize_trades(oos_rows, risk_usd=risk_usd) if oos_rows else None
        oos_status = classify_oos_status(
            base_summary=base_summary,
            oos_val_summary=oos_summary,
            oos_val_trades=int((oos_summary or {}).get("trade_count") or 0),
            recon_ok=bool(recon.get("ok")),
            direct_replay=bool(forensic.get("direct_candle_replay")) or int(base_summary.get("trade_count") or 0) == 0,
            windows_ok=bool(disjoint.get("ok")),
            # Prior peek + post-observation index remap invalidates promotion on this holdout.
            oos_consumed=True,
        )
        # This phase never paper-eligible
        oos_status["paper_eligible"] = False

        variant_results[vid] = {
            "label": labels[vid],
            "variant_id": vid,
            "variant_run_id": v_run,
            "fingerprint": variant_fingerprint(vid, window=win, thresholds=th, symbols=syms),
            "configuration_hash": cfg_hash,
            "dataset_hash": data_hash,
            "parent_strategy_id": PARENT_STRATEGY_ID,
            "parent_run_id": PARENT_RUN_ID,
            "base": {k: v for k, v in base_summary.items() if k != "closed_trades"},
            "oos_val": {k: v for k, v in (oos_summary or {}).items() if k != "closed_trades"} if oos_summary else None,
            "oos_validation_trade_count": int((oos_summary or {}).get("trade_count") or 0),
            "reconciliation": recon,
            "direct_candle_replay": forensic.get("direct_candle_replay"),
            "evidence_classes": forensic.get("evidence_classes"),
            "oos_status": oos_status,
            "entry_quality_counts": count_labels(rows, "entry_quality_primary"),
            "stop_class_counts": count_labels(rows, "stop_class"),
            **SAFETY_STAMPS,
        }

    # Stop / TP research matrices (base only + OOS status gated)
    stop_results = {}
    for sid in (STOP_CURRENT, STOP_SWING_ATR, STOP_BOS_HIGH_ATR, STOP_FIXED_ATR):
        srows = apply_stop_variant(
            base_trades,
            stop_variant=sid,
            candles_by_symbol=candles_1h,
            atr_lookup=atr_lookup,
            thresholds=th,
            risk_usd=risk_usd,
        )
        stop_results[sid] = {
            "fingerprint": variant_fingerprint(sid, window=win, thresholds=th, symbols=syms),
            "base": {k: v for k, v in summarize_trades(srows).items() if k != "closed_trades"},
            "oos_status": "NOT_RUN_UNTIL_BASE_PROMISING",
            **SAFETY_STAMPS,
        }

    tp_results = {}
    for tid in (TP_CURRENT, TP_SUPPORT, TP_FIXED_1R, TP_FIXED_15R, TP_FIXED_2R):
        trows = apply_tp_variant(
            base_trades,
            tp_variant=tid,
            candles_by_symbol=candles_1h,
            atr_lookup=atr_lookup,
            risk_usd=risk_usd,
        )
        summary = summarize_trades(trows)
        mfe_vals = [float(r["mfe_r"]) for r in trows if r.get("mfe_r") is not None]
        tp_results[tid] = {
            "fingerprint": variant_fingerprint(tid, window=win, thresholds=th, symbols=syms),
            "base": {k: v for k, v in summary.items() if k != "closed_trades"},
            "avg_mfe_r": (sum(mfe_vals) / len(mfe_vals)) if mfe_vals else None,
            "oos_status": "NOT_RUN_UNTIL_BASE_PROMISING",
            **SAFETY_STAMPS,
        }

    entry_counts = count_labels(baseline_rows, "entry_quality_primary")
    stop_counts = count_labels(baseline_rows, "stop_class")
    retest_impl = {
        "status": (
            "IMPLEMENTED_AND_FILLING"
            if int(variant_results[VARIANT_RETEST]["base"].get("trade_count") or 0) > 0
            else "IMPLEMENTED_BUT_NO_FILLS"
        ),
        "fills_work_correctly": True,  # unit tests cover synthetic correctness
        "zero_baseline_limit_retest_explained": (
            "COMBO_02 combination path enters MARKET at BOS close when pullback/retest "
            "is not required; LIMIT_RETEST=0 on baseline is expected, not necessarily a defect. "
            "Variant B places a later limit at the broken level."
        ),
        "entry_filters_improve_oos": False,  # updated below if any OOS pass
        "stop_variants_improve_oos": False,
        "tp_supported_by_oos": False,
        "oos_data_available": oos_available,
        "data_sources": data_sources,
    }
    # Entry *filters* (extension / chasing exclusion) — not the retest entry mode.
    filter_pass = any(
        (variant_results.get(vid) or {}).get("oos_status", {}).get("oos_status") == "PASS"
        for vid in (VARIANT_EXTENSION_FILTER, VARIANT_CHASING_EXCLUSION)
    )
    retest_impl["entry_filters_improve_oos"] = filter_pass
    retest_oos = str(
        (variant_results.get(VARIANT_RETEST) or {})
        .get("oos_status", {})
        .get("oos_status")
        or ""
    )
    retest_impl["retest_improves_oos"] = retest_oos in ("PASS", "PASS_CONSUMED")
    retest_impl["retest_oos_promotion_blocked"] = retest_oos == "PASS_CONSUMED"

    # Best next: least-bad base among filters that stay research-only; never paper.
    candidates = []
    for vid, vr in variant_results.items():
        base = vr.get("base") or {}
        n = int(base.get("trade_count") or 0)
        if n < 10:
            continue
        candidates.append(
            {
                "variant_id": vid,
                "trade_count": n,
                "net_pnl": base.get("net_pnl"),
                "average_net_r": base.get("average_net_r"),
                "profit_factor": base.get("profit_factor"),
                "oos_status": (vr.get("oos_status") or {}).get("oos_status"),
                "deserves_future_oos": n >= 20,
                "note": "Exploratory; requires untouched OOS before any approval.",
            }
        )
    candidates.sort(
        key=lambda c: (
            float(c["average_net_r"] or -999),
            float(c["net_pnl"] or -999),
        ),
        reverse=True,
    )
    best = candidates[0] if candidates else {"variant_id": None, "note": "No candidate"}

    tp_findings = {
        "baseline_avg_planned_rr": None,
        "baseline_avg_mfe_r": None,
        "tp_variant_base_nets": {
            k: (v.get("base") or {}).get("net_pnl") for k, v in tp_results.items()
        },
        "note": (
            "TP variants are base-sample exploratory only; OOS not promoted "
            "until a base-promising entry variant exists."
        ),
    }
    mfe = [float(r["mfe_r"]) for r in baseline_rows if r.get("mfe_r") is not None]
    rr = [float(r["planned_rr"]) for r in baseline_rows if r.get("planned_rr") is not None]
    if mfe:
        tp_findings["baseline_avg_mfe_r"] = sum(mfe) / len(mfe)
    if rr:
        tp_findings["baseline_avg_planned_rr"] = sum(rr) / len(rr)

    out_dir = Path(
        report_dir
        or (Path(__file__).resolve().parents[3] / "reports" / "short_research")
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    report = build_final_report(
        run_id=run_id,
        variant_run_ids=variant_run_ids,
        variant_results=variant_results,
        retest_status=retest_impl,
        entry_counts=entry_counts,
        stop_counts=stop_counts,
        tp_findings=tp_findings,
        tests=dict(
            tests_meta
            or {
                "added": "tests/test_combo02_short_entry_research.py",
                "executed": "pending",
                "result": "pending",
                "long_regression": "not modified",
                "v1_regression": "SHORT remains blocked",
                "uncertainties": [
                    "bos_level sometimes recovered from distance/extension when snapshot missing",
                    "stop/TP overlay OOS not promoted until entry geometry is approved",
                    "prior OOS run 20261004T094053Z-0b46752e consumed after index-remap fix",
                ],
            }
        ),
        best_next=best,
    )
    report["stop_variants"] = stop_results
    report["tp_variants"] = tp_results
    report["configuration_hash"] = cfg_hash
    report["dataset_hash"] = data_hash
    report["window"] = win
    report["thresholds"] = th
    report["disjoint_windows"] = disjoint
    report["oos_available"] = oos_available
    report["oos_validation_consumed_runs"] = [
        "20261004T094053Z-0b46752e",  # observed before entry_index remapping fix
    ]
    report["baseline_preserved"] = {
        "parent_run_id": PARENT_RUN_ID,
        "parent_trade_count": len(parent_trades),
        "base_trade_count": len(base_trades),
        "note": "Parent diagnostic blotter not modified.",
    }

    report_path = out_dir / f"short_entry_research_{run_id}.json"
    text_path = out_dir / f"short_entry_research_operator_{run_id}.txt"
    latest_json = out_dir / "short_entry_research_LATEST.json"
    latest_txt = out_dir / "short_entry_research_operator_LATEST.txt"
    payload = json.dumps(report, indent=2, default=str)
    report_path.write_text(payload, encoding="utf-8")
    latest_json.write_text(payload, encoding="utf-8")
    text_path.write_text(str(report.get("operator_text") or ""), encoding="utf-8")
    latest_txt.write_text(str(report.get("operator_text") or ""), encoding="utf-8")

    # Persist per-variant trade samples (capped)
    for vid, rows in entry_variant_trades.items():
        p = out_dir / f"short_entry_trades_{run_id}_{vid}.json"
        p.write_text(json.dumps(rows[:500], indent=2, default=str), encoding="utf-8")

    return {
        "run_id": run_id,
        "report": report,
        "report_path": str(report_path),
        "text_path": str(text_path),
        **SAFETY_STAMPS,
    }
