"""Three-label COMBO_02_V1 vs COMBO_02_V1_CLOSED_HTF audit.

Labels (required):
  original  — lim_4h=limit//16+100 + forming HTF (COMBO_02 / COMBO_02_V1)
  corrected — lim_4h=limit//4+100  + forming HTF (COMBO_02 / COMBO_02_V1)
  final     — lim_4h=limit//4+100  + closed HTF  (COMBO_02_CLOSED_HTF)

Research-only. No Telegram / paper / live. Does not mutate frozen COMBO_02_V1.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
import traceback
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engines.mtf.indicators import atr_series  # noqa: E402
from app.research.backtest_ui_config import configuration_fingerprint  # noqa: E402
from app.research.bos_combinations import get_combination  # noqa: E402
from app.research.bos_strategy_comparison.htf import (  # noqa: E402
    HTF_ALIGNED,
    build_htf_as_of_index_map,
    build_htf_as_of_index_map_fully_closed,
    classify_htf_alignment,
    precompute_htf_trend_cache,
    timeframe_seconds,
)
from app.research.combination_backtest import (  # noqa: E402
    _bos_break_possible,
    resolve_intrabar_outcome,
    run_combination_backtest,
)
from app.research.combination_engine import (  # noqa: E402
    _clone_signal_config,
    evaluate_combination_at_bar,
)
from app.research.config import ResearchConfig  # noqa: E402
from app.research.metrics import max_drawdown_r, profit_factor  # noqa: E402
from app.research.query_utils import DATASET_ID  # noqa: E402
from app.research.schemas import ResearchTrade  # noqa: E402
from app.research.trade_fees import enrich_trades  # noqa: E402
from app.signals._candle_utils import candle_time, ohlc, series_ohlcv  # noqa: E402
from app.signals.bos_engine import detect_bos  # noqa: E402
from app.signals.config import SignalConfig  # noqa: E402
from app.signals.signal_engine import SignalEngine  # noqa: E402
from app.signals.swing_detector import detect_swings, extend_swings  # noqa: E402
from app.signals.trend_engine import infer_trend  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[1] / "reports" / "combo02_v1_closed_htf_compare"
CACHE_DIR = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "research_cache"
    / "ohlcv"
    / "ohlcv_v1"
)
PARQUET_1H = CACHE_DIR / "BTCUSDT_1h_2022-10-06_2026-10-05_ohlcv_v1.parquet"
PARQUET_4H = CACHE_DIR / "BTCUSDT_4h_2022-10-06_2026-10-05_ohlcv_v1.parquet"

LIMIT = 8640
LIM_4H_ORIGINAL = max(int(LIMIT) // 16 + 100, 200)
LIM_4H_CORRECTED = max(int(LIMIT) // 4 + 100, 200)

RISK_USD = 20.0
PRINCIPAL = 1000.0
LEVERAGE = 2.0
TAKER = 0.0004
MAKER = 0.0002


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def load_parquet_candles(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    df = pl.read_parquet(path)
    colmap = {c.lower(): c for c in df.columns}
    tcol = colmap.get("open_time") or colmap.get("timestamp") or colmap.get("time")
    if tcol is None:
        raise RuntimeError(f"No time column in {path}")
    df = df.sort(tcol)
    if limit is not None and limit > 0:
        df = df.tail(int(limit))
    out: list[dict[str, Any]] = []
    for row in df.iter_rows(named=True):
        ts = row[tcol]
        if not isinstance(ts, datetime):
            ts = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        ts = _aware(ts)
        assert ts is not None and ts.tzinfo is not None
        out.append(
            {
                "time": ts,
                "open_time": ts,
                "open": float(row[colmap["open"]]),
                "high": float(row[colmap["high"]]),
                "low": float(row[colmap["low"]]),
                "close": float(row[colmap["close"]]),
                "volume": float(row[colmap.get("volume", colmap["open"])]),
            }
        )
    return out


def dataset_fingerprint(candles_by_tf: Mapping[str, Sequence[Mapping[str, Any]]]) -> str:
    parts: list[str] = []
    for tf in sorted(candles_by_tf.keys()):
        for c in candles_by_tf[tf] or []:
            ts = _aware(candle_time(c))
            stamp = ts.isoformat() if ts else str(c.get("time"))
            parts.append(
                f"{tf}|{stamp}|{c.get('open')}|{c.get('high')}|{c.get('low')}|"
                f"{c.get('close')}|{c.get('volume')}"
            )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def config_fp(*, strategy_id: str, combination_id: str, closed_htf: bool) -> str:
    # Canonical COMBO_02_V1 production-comparable fingerprint payload
    # (matches UI/backtest_ui_config → e3e4c05faf4454ec).
    if not closed_htf and strategy_id == "COMBO_02_V1" and combination_id == "COMBO_02":
        payload = {
            "strategy_id": "COMBO_02_V1",
            "combo_version": "v1",
            "direction": "LONG",
            "symbols": ["BTCUSDT"],
            "timeframes": ["1h"],
            "setup_timeframe": "1h",
            "risk_mode": "V1_PRODUCTION_PROFILE",
            "configured_risk_percent": 0.02,
            "principal_usd": PRINCIPAL,
            "leverage": LEVERAGE,
            "taker_fee_pct": 0.04,
            "maker_fee_pct": 0.02,
            "start_date": None,
            "end_date": None,
            "cells": [
                {
                    "symbol": "BTCUSDT",
                    "timeframe": "1h",
                    "effective_risk_percent": 0.02,
                    "risk_source": "V1_PRODUCTION_PROFILE",
                }
            ],
        }
        return configuration_fingerprint(payload)
    # Research variant — expected different fingerprint
    payload = {
        "strategy_id": strategy_id,
        "combination_id": combination_id,
        "combo_version": "v1-combo02-long-htf-closed",
        "direction": "LONG",
        "symbols": ["BTCUSDT"],
        "timeframes": ["1h"],
        "setup_timeframe": "1h",
        "risk_mode": "V1_PRODUCTION_PROFILE",
        "configured_risk_percent": 0.02,
        "principal_usd": PRINCIPAL,
        "leverage": LEVERAGE,
        "taker_fee_pct": 0.04,
        "maker_fee_pct": 0.02,
        "htf_require_fully_closed": True,
        "start_date": None,
        "end_date": None,
        "cells": [
            {
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "effective_risk_percent": 0.02,
                "risk_source": "V1_PRODUCTION_PROFILE",
            }
        ],
    }
    return configuration_fingerprint(payload)


def _trade_dicts(raw: Sequence[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in raw:
        if isinstance(t, dict):
            out.append(dict(t))
        elif hasattr(t, "to_dict"):
            out.append(t.to_dict())
        else:
            out.append(dict(t.__dict__))
    return out


def max_consecutive_losses(rs: Sequence[float]) -> int:
    best = 0
    cur = 0
    for r in rs:
        if r < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k) for k in fieldnames})


def run_labeled_walk(
    *,
    label: str,
    strategy_id: str,
    combination_id: str,
    candles_1h: list[dict[str, Any]],
    candles_4h: list[dict[str, Any]],
    lim_4h: int,
    coverage_formula: str,
    htf_rule: str,
    closed_htf: bool,
) -> dict[str, Any]:
    combo = get_combination(combination_id)
    assert combo is not None
    assert bool(combo.htf_require_fully_closed) is closed_htf

    # Production walk for trade blotter (authoritative fills)
    bt = run_combination_backtest(
        "BTCUSDT",
        "1h",
        candles_1h,
        combo,
        direction_filter="LONG",
        candles_1h=candles_1h,
        candles_4h=candles_4h,
    )
    trades = _trade_dicts(bt.get("trades") or [])

    rcfg = ResearchConfig()
    scfg = SignalConfig()
    local_cfg = _clone_signal_config(
        scfg, rcfg, require_htf_alignment=bool(combo.require_htf_alignment)
    )
    engine = SignalEngine(local_cfg)
    swing_cfg = local_cfg.swing_for("1h")
    _, highs, lows, closes, vols = series_ohlcv(candles_1h)
    atr_arr = atr_series(highs, lows, closes, local_cfg.atr_period)

    cache: dict[tuple[str, int], str] = {}
    precompute_htf_trend_cache(
        candles_1h, timeframe="1h", symbol="BTCUSDT", config=local_cfg, cache=cache
    )
    precompute_htf_trend_cache(
        candles_4h, timeframe="4h", symbol="BTCUSDT", config=local_cfg, cache=cache
    )
    if closed_htf:
        idx_1h = build_htf_as_of_index_map_fully_closed(
            candles_1h, candles_1h, setup_timeframe="1h", htf_timeframe="1h"
        )
        idx_4h = build_htf_as_of_index_map_fully_closed(
            candles_1h, candles_4h, setup_timeframe="1h", htf_timeframe="4h"
        )
        mode = "fully_closed"
    else:
        idx_1h = build_htf_as_of_index_map(candles_1h, candles_1h)
        idx_4h = build_htf_as_of_index_map(candles_1h, candles_4h)
        mode = "forming_open_le_setup_open"

    setup_dur = timeframe_seconds("1h")
    htf4_dur = timeframe_seconds("4h")
    loop_start = max(rcfg.min_bars, 0)
    end = len(candles_1h)

    swings = (
        detect_swings(
            candles_1h,
            left=swing_cfg.swing_left_bars,
            right=swing_cfg.swing_right_bars,
            symbol="BTCUSDT",
            timeframe="1h",
            atr_period=local_cfg.atr_period,
            minimum_swing_distance_atr=swing_cfg.minimum_swing_distance_atr,
            as_of_index=max(loop_start - 1, 0),
            highs=highs,
            lows=lows,
            closes=closes,
        )
        if loop_start > 0
        else []
    )
    prev_break = False
    prev_swing_n = len(swings)
    open_trade: ResearchTrade | None = None

    reject_rows: list[dict[str, Any]] = []
    htf_audit: list[dict[str, Any]] = []
    reject_counts = Counter()
    missing_htf = 0
    bars_evaluated = 0
    validity_failures: list[dict[str, Any]] = []

    for i in range(loop_start, end):
        bars_evaluated += 1
        setup_open = _aware(candle_time(candles_1h[i]))
        assert setup_open is not None
        setup_close = setup_open + timedelta(seconds=setup_dur)
        i4 = idx_4h[i] if i < len(idx_4h) else None
        i1 = idx_1h[i] if i < len(idx_1h) else None
        if i4 is None:
            missing_htf += 1

        sel_open = _aware(candle_time(candles_4h[i4])) if i4 is not None else None
        sel_close = (
            (sel_open + timedelta(seconds=htf4_dur)) if sel_open is not None else None
        )
        t1 = cache.get(("1h", i1), "HTF_UNAVAILABLE") if i1 is not None else "HTF_UNAVAILABLE"
        t4 = cache.get(("4h", i4), "HTF_UNAVAILABLE") if i4 is not None else "HTF_UNAVAILABLE"
        align_long = classify_htf_alignment(
            bos_direction="BULLISH_BOS", trend_4h=t4, trend_1h=t1
        )
        is_fully_closed = bool(
            sel_close is not None and sel_close <= setup_close
        )
        if closed_htf and i4 is not None and not is_fully_closed:
            validity_failures.append(
                {
                    "bar_index": i,
                    "setup_open_time": setup_open.isoformat(),
                    "setup_close_time": setup_close.isoformat(),
                    "selected_4h_open_time": sel_open.isoformat() if sel_open else None,
                    "selected_4h_close_time": sel_close.isoformat() if sel_close else None,
                }
            )

        htf_audit.append(
            {
                "setup_open_time": setup_open.isoformat(),
                "setup_close_time": setup_close.isoformat(),
                "selected_4h_open_time": sel_open.isoformat() if sel_open else None,
                "selected_4h_close_time": sel_close.isoformat() if sel_close else None,
                "selected_4h_index": i4,
                "htf_selection_mode": mode,
                "htf_1h_state": t1,
                "htf_4h_state": t4,
                "htf_alignment": align_long,
                "is_fully_closed": is_fully_closed,
                "rejection_reason": None,
                "bar_index": i,
            }
        )

        swings = extend_swings(
            swings,
            candles_1h,
            left=swing_cfg.swing_left_bars,
            right=swing_cfg.swing_right_bars,
            symbol="BTCUSDT",
            timeframe="1h",
            atr_period=local_cfg.atr_period,
            minimum_swing_distance_atr=swing_cfg.minimum_swing_distance_atr,
            as_of_index=i,
            highs=highs,
            lows=lows,
            closes=closes,
        )
        swing_grew = len(swings) != prev_swing_n
        prev_swing_n = len(swings)

        if open_trade is not None:
            c = candles_1h[i]
            high = float(c["high"])
            low = float(c["low"])
            targets = [t for t in (open_trade.tp1, open_trade.tp2, open_trade.tp3) if t is not None]
            brk = _bos_break_possible(candles_1h, i, swings, direction_filter="LONG")
            if brk and not (prev_break and not swing_grew):
                reject_counts["position_state"] += 1
                reject_rows.append(
                    {
                        "timestamp": setup_open.isoformat(),
                        "rejection_stage": "position_state",
                        "rejection_reason": "POSITION_OPEN_BLOCKS_NEW_ENTRY",
                        "htf_1h_state": t1,
                        "htf_4h_state": t4,
                        "htf_alignment": align_long,
                    }
                )
                htf_audit[-1]["rejection_reason"] = "POSITION_OPEN_BLOCKS_NEW_ENTRY"
            outcome, exit_px, ambiguous = resolve_intrabar_outcome(
                direction=open_trade.direction,
                high=high,
                low=low,
                stop=open_trade.stop_price,
                targets=targets,
                handling=rcfg.ambiguous_handling,
            )
            if outcome:
                open_trade = None
                prev_break = False
            continue

        if combo.require_bos:
            brk = _bos_break_possible(candles_1h, i, swings, direction_filter="LONG")
            if not brk:
                reject_counts["bos"] += 1
                trend = infer_trend(list(swings))
                reason = f"BOS_PREFILTER_TREND_{trend.get('trend') or 'NONE'}"
                reject_rows.append(
                    {
                        "timestamp": setup_open.isoformat(),
                        "rejection_stage": "bos",
                        "rejection_reason": reason,
                        "htf_1h_state": t1,
                        "htf_4h_state": t4,
                        "htf_alignment": align_long,
                    }
                )
                htf_audit[-1]["rejection_reason"] = reason
                prev_break = False
                continue
            if prev_break and not swing_grew:
                reject_counts["bos"] += 1
                reason = "STICKY_SAME_BOS_SKIP"
                reject_rows.append(
                    {
                        "timestamp": setup_open.isoformat(),
                        "rejection_stage": "bos",
                        "rejection_reason": reason,
                        "htf_1h_state": t1,
                        "htf_4h_state": t4,
                        "htf_alignment": align_long,
                    }
                )
                htf_audit[-1]["rejection_reason"] = reason
                continue
            prev_break = True

        setup = evaluate_combination_at_bar(
            symbol="BTCUSDT",
            timeframe="1h",
            candles=candles_1h,
            as_of_index=i,
            combination=combo,
            signal_config=scfg,
            research_config=rcfg,
            signal_engine=engine,
            compute_sd=False,
            swings=swings,
            atr_value=atr_arr[i],
            volumes=vols,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
            htf_trend_cache=cache,
            htf_idx_1h_map=idx_1h,
            htf_idx_4h_map=idx_4h,
        )
        gates = setup.get("gates") or {}
        htf_meta = setup.get("htf") or {}
        status = setup.get("status")
        if status == "LONG_ENTRY_CANDIDATE":
            open_trade = ResearchTrade(
                symbol="BTCUSDT",
                timeframe="1h",
                combination_id=combo.combination_id,
                entry_index=i,
                signal_time=setup.get("signal_time"),
                direction="LONG",
                entry_price=float(setup["entry_price"]),
                stop_price=float(setup["stop_price"]),
                tp1=setup.get("tp1"),
                tp2=setup.get("tp2"),
                tp3=setup.get("tp3"),
                rr=setup.get("rr"),
            )
            htf_audit[-1]["rejection_reason"] = "ACCEPTED"
            continue

        reason = str(setup.get("reason") or "NO_SETUP")
        stage = "other"
        if not gates.get("bos"):
            stage = "bos"
            reject_counts["bos"] += 1
        elif not gates.get("trend"):
            stage = "trend"
            reject_counts["trend"] += 1
        elif gates.get("hl_intact") is False:
            stage = "higher_low"
            reject_counts["higher_low"] += 1
        elif combo.require_htf_alignment and not gates.get("htf"):
            stage = "htf"
            reject_counts["htf"] += 1
            reason = str(htf_meta.get("reason") or reason)
        elif "TP1_R" in reason or "STOP" in reason.upper() or "target" in reason.lower():
            stage = "risk_validation"
            reject_counts["risk_validation"] += 1
        else:
            reject_counts["other"] += 1
        reject_rows.append(
            {
                "timestamp": setup_open.isoformat(),
                "rejection_stage": stage,
                "rejection_reason": reason,
                "htf_1h_state": htf_meta.get("trend_1h", t1),
                "htf_4h_state": htf_meta.get("trend_4h", t4),
                "htf_alignment": htf_meta.get("htf_alignment", align_long),
            }
        )
        htf_audit[-1]["rejection_reason"] = reason

    if closed_htf and validity_failures:
        raise AssertionError(
            f"final closed-HTF validity failed on {len(validity_failures)} bars; "
            f"first={validity_failures[0]}"
        )

    closed = [t for t in trades if str(t.get("outcome") or "") not in ("", "OPEN", "None")]
    open_n = len(trades) - len(closed)
    # Ensure entry_type for fee enrich
    for t in closed:
        snap = t.get("condition_snapshot") or {}
        if "entry_type" not in snap and t.get("entry_type") is None:
            t["entry_type"] = "MARKET"
    enriched = enrich_trades(
        closed,
        risk_usd=RISK_USD,
        taker_fee=TAKER,
        maker_fee=MAKER,
        leverage=LEVERAGE,
        account_equity=PRINCIPAL,
    )
    rs = [float(t["r_multiple"]) for t in closed if t.get("r_multiple") is not None]
    wins = sum(1 for r in rs if r > 0)
    losses = sum(1 for r in rs if r < 0)
    holds = [int(t["holding_bars"]) for t in closed if t.get("holding_bars") is not None]
    eq = [0.0]
    for r in rs:
        eq.append(eq[-1] + r)
    max_dd, _ = max_drawdown_r(eq) if rs else (None, [])
    gross = sum(float(x.get("gross_pnl_usd") or x.get("gross_pnl") or 0) for x in enriched)
    fees = sum(float(x.get("fee_total_usd") or x.get("total_fee") or 0) for x in enriched)
    net = sum(float(x.get("net_pnl_usd") or x.get("net_pnl") or 0) for x in enriched)

    # Attach enrich fields onto trade blotter by signal_time
    by_sig = {x.get("signal_time"): x for x in enriched}
    blotter = []
    for n, t in enumerate(trades, start=1):
        e = by_sig.get(t.get("signal_time")) or {}
        snap = t.get("condition_snapshot") or {}
        blotter.append(
            {
                "label": label,
                "trade_id": f"{label}-t{n}",
                "entry_time": t.get("signal_time"),
                "exit_time": t.get("exit_time"),
                "direction": t.get("direction"),
                "entry_price": t.get("entry_price"),
                "stop_price": t.get("stop_price"),
                "target_price": t.get("tp1"),
                "exit_reason": t.get("outcome"),
                "bars_held": t.get("holding_bars"),
                "realized_R": t.get("r_multiple"),
                "gross_pnl": e.get("gross_pnl_usd", e.get("gross_pnl")),
                "fees": e.get("fee_total_usd", e.get("total_fee")),
                "net_pnl": e.get("net_pnl_usd", e.get("net_pnl")),
                "htf_alignment": snap.get("htf_alignment"),
                "trend_4h": snap.get("trend_4h"),
                "trend_1h": snap.get("trend_1h"),
                "htf_require_fully_closed": snap.get("htf_require_fully_closed"),
            }
        )

    ds_fp = dataset_fingerprint({"1h": candles_1h, "4h": candles_4h})
    cfg = config_fp(
        strategy_id=strategy_id,
        combination_id=combination_id,
        closed_htf=closed_htf,
    )

    return {
        "label": label,
        "strategy_id": strategy_id,
        "combo_id": combination_id,
        "htf_coverage_formula": coverage_formula,
        "htf_as_of_rule": htf_rule,
        "dataset_id": DATASET_ID,
        "dataset_fingerprint": ds_fp,
        "configuration_fingerprint": cfg,
        "requested_bar_count": LIMIT,
        "actual_bar_count": len(candles_1h),
        "start_timestamp": _aware(candle_time(candles_1h[0])).isoformat() if candles_1h else None,
        "end_timestamp": _aware(candle_time(candles_1h[-1])).isoformat() if candles_1h else None,
        "1h_bars_evaluated": bars_evaluated,
        "4h_bars_loaded": len(candles_4h),
        "lim_4h": lim_4h,
        "bars_with_no_usable_htf": missing_htf,
        "total_entries": len(trades),
        "closed_trades": len(closed),
        "open_trades": open_n,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / len(rs)) if rs else None,
        "gross_pnl": gross,
        "fees": fees,
        "net_pnl": net,
        "average_R": (sum(rs) / len(rs)) if rs else None,
        "profit_factor": profit_factor(rs),
        "maximum_drawdown_R": max_dd,
        "maximum_consecutive_losses": max_consecutive_losses(rs),
        "average_holding_bars": (sum(holds) / len(holds)) if holds else None,
        "htf_rejections": int(reject_counts["htf"]),
        "bos_rejections": int(reject_counts["bos"]),
        "trend_rejections": int(reject_counts["trend"]),
        "higher_low_rejections": int(reject_counts["higher_low"]),
        "position_state_rejections": int(reject_counts["position_state"]),
        "risk_validation_rejections": int(reject_counts["risk_validation"]),
        "reject_counts": dict(reject_counts),
        "trades": blotter,
        "reject_rows": reject_rows,
        "htf_audit": htf_audit,
        "signal_timestamps": [t.get("entry_time") for t in blotter],
        "backtest_setups": bt.get("setups_processed"),
    }


def classify_attribution(
    original: dict[str, Any],
    corrected: dict[str, Any],
    final: dict[str, Any],
) -> list[dict[str, Any]]:
    o = {t["entry_time"]: t for t in original["trades"] if t.get("entry_time")}
    c = {t["entry_time"]: t for t in corrected["trades"] if t.get("entry_time")}
    f = {t["entry_time"]: t for t in final["trades"] if t.get("entry_time")}
    all_ts = sorted(set(o) | set(c) | set(f))
    rows = []
    for ts in all_ts:
        in_o, in_c, in_f = ts in o, ts in c, ts in f
        if in_o and in_c and in_f:
            cls = "unchanged"
            note = "Present in original, corrected, and final"
        elif (not in_o) and in_c and in_f:
            cls = "new_because_of_complete_4h_coverage"
            note = "Absent from original; present after full 4h coverage"
        elif (not in_o) and (not in_c) and in_f:
            cls = "changed_because_htf_state_changed"
            note = "Appeared only under closed-HTF (forming HTF blocked or state differed)"
        elif in_o and in_c and (not in_f):
            cls = "removed_by_closed_htf_mapping"
            note = "Present with forming HTF; removed when requiring fully closed 4h"
        elif in_c and (not in_f) and (not in_o):
            cls = "removed_by_closed_htf_mapping"
            note = "Coverage-new trade removed by closed-HTF"
        elif in_o and (not in_c):
            cls = "changed_because_htf_state_changed"
            note = "Dropped when coverage fixed (unexpected) or remapped"
        else:
            cls = "changed_because_htf_state_changed"
            note = f"pattern o={in_o} c={in_c} f={in_f}"
        src = f.get(ts) or c.get(ts) or o.get(ts) or {}
        rows.append(
            {
                **src,
                "attribution": cls,
                "attribution_note": note,
                "in_original": in_o,
                "in_corrected": in_c,
                "in_final": in_f,
            }
        )
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    c1 = load_parquet_candles(PARQUET_1H, limit=LIMIT)
    c4_orig = load_parquet_candles(PARQUET_4H, limit=LIM_4H_ORIGINAL)
    c4_corr = load_parquet_candles(PARQUET_4H, limit=LIM_4H_CORRECTED)

    print("Running original...")
    original = run_labeled_walk(
        label="original",
        strategy_id="COMBO_02_V1",
        combination_id="COMBO_02",
        candles_1h=c1,
        candles_4h=c4_orig,
        lim_4h=LIM_4H_ORIGINAL,
        coverage_formula="limit//16 + 100",
        htf_rule="forming: largest 4h with open_time <= setup_open_time",
        closed_htf=False,
    )
    print("Running corrected...")
    corrected = run_labeled_walk(
        label="corrected",
        strategy_id="COMBO_02_V1",
        combination_id="COMBO_02",
        candles_1h=c1,
        candles_4h=c4_corr,
        lim_4h=LIM_4H_CORRECTED,
        coverage_formula="limit//4 + 100",
        htf_rule="forming: largest 4h with open_time <= setup_open_time",
        closed_htf=False,
    )
    print("Running final (closed-HTF)...")
    final = run_labeled_walk(
        label="final",
        strategy_id="COMBO_02_V1_CLOSED_HTF",
        combination_id="COMBO_02_CLOSED_HTF",
        candles_1h=c1,
        candles_4h=c4_corr,
        lim_4h=LIM_4H_CORRECTED,
        coverage_formula="limit//4 + 100",
        htf_rule="closed: latest 4h with close_time <= setup_close_time",
        closed_htf=True,
    )

    # Final trade HTF validity: every final trade's setup bar must have fully closed 4h
    audit_by_open = {r["setup_open_time"]: r for r in final["htf_audit"]}
    final_trade_validity = []
    for t in final["trades"]:
        row = audit_by_open.get(t["entry_time"])
        ok = bool(row and row.get("is_fully_closed") and row.get("selected_4h_index") is not None)
        final_trade_validity.append(
            {
                "trade_id": t["trade_id"],
                "entry_time": t["entry_time"],
                "fully_closed_4h": ok,
                "selected_4h_open_time": row.get("selected_4h_open_time") if row else None,
                "selected_4h_close_time": row.get("selected_4h_close_time") if row else None,
                "setup_close_time": row.get("setup_close_time") if row else None,
            }
        )
        if not ok:
            raise AssertionError(f"Final trade without fully closed 4h: {t['entry_time']}")

    attribution = classify_attribution(original, corrected, final)

    metric_keys = [
        ("4h coverage", "htf_coverage_formula"),
        ("HTF rule", "htf_as_of_rule"),
        ("1h bars evaluated", "1h_bars_evaluated"),
        ("4h bars loaded", "4h_bars_loaded"),
        ("Missing HTF mappings", "bars_with_no_usable_htf"),
        ("Total entries", "total_entries"),
        ("Closed trades", "closed_trades"),
        ("Open trades", "open_trades"),
        ("Wins", "wins"),
        ("Losses", "losses"),
        ("Win rate", "win_rate"),
        ("Net PnL", "net_pnl"),
        ("Average R", "average_R"),
        ("Profit factor", "profit_factor"),
        ("Maximum drawdown", "maximum_drawdown_R"),
        ("HTF rejections", "htf_rejections"),
        ("Dataset fingerprint", "dataset_fingerprint"),
        ("Configuration fingerprint", "configuration_fingerprint"),
    ]
    comparison_rows = []
    for display, key in metric_keys:
        comparison_rows.append(
            {
                "Metric": display,
                "original": original.get(key),
                "corrected": corrected.get(key),
                "final": final.get(key),
            }
        )

    summary = {
        "title": "COMBO_02_V1_CLOSED_HTF three-label comparison",
        "research_only": True,
        "live_approved": False,
        "warning": (
            "Do not compare original directly to final without corrected. "
            "final is a research variant, not approved for live trading. "
            "No profitability claim."
        ),
        "labels": {
            "original": original,
            "corrected": corrected,
            "final": final,
        },
        "comparison_table": comparison_rows,
        "attribution_summary": {
            "unchanged": sum(1 for a in attribution if a["attribution"] == "unchanged"),
            "new_because_of_complete_4h_coverage": sum(
                1
                for a in attribution
                if a["attribution"] == "new_because_of_complete_4h_coverage"
            ),
            "removed_by_closed_htf_mapping": sum(
                1
                for a in attribution
                if a["attribution"] == "removed_by_closed_htf_mapping"
            ),
            "changed_because_htf_state_changed": sum(
                1
                for a in attribution
                if a["attribution"] == "changed_because_htf_state_changed"
            ),
        },
        "final_trade_fully_closed_check": {
            "all_ok": all(x["fully_closed_4h"] for x in final_trade_validity),
            "trades": final_trade_validity,
        },
        "safety": {
            "telegram_sent": False,
            "paper_trade_created": False,
            "live_trade_created": False,
            "frozen_combo02_v1_mutated": False,
        },
    }

    # Strip bulky arrays from nested labels in summary json (keep metrics)
    slim_labels = {}
    for lab, payload in (("original", original), ("corrected", corrected), ("final", final)):
        slim = {k: v for k, v in payload.items() if k not in ("reject_rows", "htf_audit")}
        slim_labels[lab] = slim
    summary["labels"] = slim_labels

    (OUT_DIR / "comparison_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    write_csv(
        OUT_DIR / "comparison_summary.csv",
        comparison_rows,
        ["Metric", "original", "corrected", "final"],
    )

    trade_fields = [
        "label",
        "trade_id",
        "entry_time",
        "exit_time",
        "direction",
        "entry_price",
        "stop_price",
        "target_price",
        "exit_reason",
        "bars_held",
        "realized_R",
        "gross_pnl",
        "fees",
        "net_pnl",
        "attribution",
        "attribution_note",
        "in_original",
        "in_corrected",
        "in_final",
    ]
    # Prefer attribution rows (covers all labels' union) + per-label blotters
    write_csv(OUT_DIR / "trade_comparison.csv", attribution, trade_fields)

    htf_fields = [
        "setup_open_time",
        "setup_close_time",
        "selected_4h_open_time",
        "selected_4h_close_time",
        "selected_4h_index",
        "htf_selection_mode",
        "htf_1h_state",
        "htf_4h_state",
        "htf_alignment",
        "is_fully_closed",
        "rejection_reason",
    ]
    # Full audit for final (required)
    write_csv(OUT_DIR / "htf_mapping_audit.csv", final["htf_audit"], htf_fields)

    rej_fields = [
        "timestamp",
        "rejection_stage",
        "rejection_reason",
        "htf_1h_state",
        "htf_4h_state",
        "htf_alignment",
    ]
    write_csv(
        OUT_DIR / "rejected_candidates_original.csv", original["reject_rows"], rej_fields
    )
    write_csv(
        OUT_DIR / "rejected_candidates_corrected.csv",
        corrected["reject_rows"],
        rej_fields,
    )
    write_csv(
        OUT_DIR / "rejected_candidates_final.csv", final["reject_rows"], rej_fields
    )

    print(
        json.dumps(
            {
                "comparison_table": comparison_rows,
                "attribution_summary": summary["attribution_summary"],
                "final_fully_closed_ok": summary["final_trade_fully_closed_check"]["all_ok"],
                "out_dir": str(OUT_DIR),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
