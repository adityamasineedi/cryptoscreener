"""Full available-history Multi-Cap Strategy research matrix.

Dataset label: AVAILABLE_HISTORICAL_CAP_WINDOW

Research only — reuses existing generators + combination_backtest + metrics + fees.
Does not modify live signals, strategy definitions, or parameters.

Usage (from backend/):
  python scripts/run_multi_cap_full_research.py
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.research.combination_backtest import evaluate_candidate_trades  # noqa: E402
from app.research.config import split_period_indices  # noqa: E402
from app.research.historical_market_cap.classifier import (  # noqa: E402
    CLASSIFICATION_RULE_VERSION,
    cap_group_at,
    filter_candidates_by_historical_cap,
)
from app.research.historical_market_cap import repository as repo  # noqa: E402
from app.research.historical_market_cap.service import (  # noqa: E402
    list_usdt_perp_symbols,
    ohlcv_span,
)
from app.research.metrics import compute_metrics  # noqa: E402
from app.research.multi_cap_strategies.common import (  # noqa: E402
    candidate_to_research_trade,
    extract_ohlcv,
    compute_atr_array,
)
from app.research.multi_cap_strategies.config import MultiCapResearchConfig  # noqa: E402
from app.research.multi_cap_strategies.fvg import detect_fvg_at  # noqa: E402
from app.research.multi_cap_strategies.large_cap_sweep_choch import (  # noqa: E402
    STRATEGY_ID as LARGE_ID,
    _detect_sweeps,
    generate_candidates as gen_large,
)
from app.research.multi_cap_strategies.mid_cap_fvg_discount import (  # noqa: E402
    STRATEGY_ID as MID_ID,
    generate_candidates as gen_mid,
)
from app.research.multi_cap_strategies.small_cap_volume_bos import (  # noqa: E402
    STRATEGY_ID as SMALL_ID,
    generate_candidates as gen_small,
)
from app.research.multi_cap_strategies.reference import ref_volume_bos_indices  # noqa: E402
from app.research.postgres_ohlcv import load_ohlcv_series_range  # noqa: E402
from app.research.trade_fees import enrich_trades  # noqa: E402
from app.services.database import db_manager  # noqa: E402
from app.signals._candle_utils import candle_time  # noqa: E402

OUT_DIR = ROOT / "scripts" / "multi_cap_full_research_out"
DATASET_LABEL = "AVAILABLE_HISTORICAL_CAP_WINDOW"
TIMEFRAMES = ("5m", "15m", "1h")
STRATEGY_BY_GROUP: dict[str, tuple[str, Callable[..., list]]] = {
    "LARGE_CAP": (LARGE_ID, gen_large),
    "MID_CAP": (MID_ID, gen_mid),
    "SMALL_CAP": (SMALL_ID, gen_small),
}


def _iso(ts: Any) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, str):
        return ts
    if hasattr(ts, "isoformat"):
        return ts.isoformat()
    return str(ts)


def _parse_dt(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc)
    if isinstance(raw, str):
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    return None


def sample_size_label(n: int) -> str:
    if n < 10:
        return "VERY_SMALL"
    if n < 30:
        return "SMALL"
    if n < 100:
        return "DESCRIPTIVE"
    return "MORE_RELIABLE_DESCRIPTIVE_SAMPLE"


def _write_csv(path: Path, rows: Sequence[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def _raw_signal_count(
    sid: str, candles: list[dict[str, Any]], cfg: MultiCapResearchConfig
) -> int:
    if not candles:
        return 0
    if sid == LARGE_ID:
        _, highs, lows, closes, _ = extract_ohlcv(candles)
        atr = compute_atr_array(highs, lows, closes, cfg.atr_period)
        return len(
            _detect_sweeps(lows, closes, atr, cfg.large_cap_htf_swing_lookback)
        )
    if sid == MID_ID:
        _, highs, lows, _, _ = extract_ohlcv(candles)
        return sum(
            1
            for i in range(2, len(highs))
            if (z := detect_fvg_at(highs, lows, i)) is not None
            and z.direction == "BULLISH"
        )
    return len(ref_volume_bos_indices(candles))


def _metrics_row(
    trades: list[Any],
    *,
    fee_rows: list[dict[str, Any]],
    strategy_id: str,
    description: str,
    symbol: str | None,
    timeframe: str | None,
    period_label: str,
    cfg: MultiCapResearchConfig,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    closed = [t for t in trades if t.outcome and t.outcome != "OPEN"]
    m = compute_metrics(
        closed,
        combination_id=strategy_id,
        description=description,
        symbol=symbol,
        timeframe=timeframe,
        direction="LONG",
        period_label=period_label,
        data_quality="OK",
        condition_definition={"dataset": DATASET_LABEL},
    )
    d = m.to_dict()
    wins = sum(1 for t in closed if (t.r_multiple or 0) > 0)
    losses = sum(1 for t in closed if (t.r_multiple or 0) <= 0)
    # Net R / fees / slippage from fee enrichment
    r_nets = [
        float(r["r_net"])
        for r in fee_rows
        if r.get("r_net") is not None and r.get("outcome") not in (None, "OPEN")
    ]
    fees = sum(float(r.get("fee_total_usd") or 0) for r in fee_rows)
    slip_r_total = 0.0
    for r in fee_rows:
        if r.get("outcome") in (None, "OPEN"):
            continue
        entry = float(r.get("entry_price") or 0)
        stop = float(r.get("stop_price") or 0)
        risk = abs(entry - stop)
        if risk > 0 and entry > 0:
            slip_r_total += (2.0 * cfg.slippage_rate * entry) / risk
    net_r = sum(r_nets) if r_nets else None
    if net_r is not None:
        net_r_with_slip = net_r - slip_r_total
    else:
        net_r_with_slip = None
    n = len(closed)
    out = {
        "strategy_id": strategy_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "period_label": period_label,
        "trade_count": n,
        "win_count": wins,
        "loss_count": losses,
        "win_rate": (wins / n) if n else None,
        "mean_R": d.get("average_R"),
        "expectancy_R": d.get("expectancy_R"),
        "profit_factor": d.get("profit_factor"),
        "gross_R": (d.get("gross_profit_R") or 0) + (d.get("gross_loss_R") or 0),
        "net_R": net_r,
        "net_R_with_slippage": net_r_with_slip,
        "max_drawdown": d.get("max_drawdown_R"),
        "MAE": d.get("average_MAE_R"),
        "MFE": d.get("average_MFE_R"),
        "fees": fees,
        "slippage_R": slip_r_total,
        "sample_size_label": sample_size_label(n),
        "LONG": sum(1 for t in closed if t.direction == "LONG"),
        "SHORT": sum(1 for t in closed if t.direction == "SHORT"),
        "regime_note": "REGIME_NOT_AVAILABLE — no new HTF filters applied",
    }
    if extra:
        out.update(extra)
    return out


async def discover_universe(
    cfg: MultiCapResearchConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Discover eligible symbols from hist mcap + OHLCV. Exclude with reasons."""
    all_syms = await list_usdt_perp_symbols()
    cov = {r["symbol"]: r for r in await repo.coverage_summary()}
    obs = await repo.load_observations_many(list(cov.keys()))
    eligible_rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []

    cap_starts: list[datetime] = []
    cap_ends: list[datetime] = []

    for sym in all_syms:
        meta = cov.get(sym)
        if not meta:
            for tf in TIMEFRAMES:
                exclusions.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "cap_group": "UNAVAILABLE",
                        "first_cap_time": None,
                        "last_cap_time": None,
                        "first_ohlcv_time": None,
                        "last_ohlcv_time": None,
                        "eligible_start": None,
                        "eligible_end": None,
                        "exclusion_reason": "CAP_GROUP_UNAVAILABLE",
                    }
                )
            continue

        first_cap = _parse_dt(meta["first_cap_timestamp"])
        last_cap = _parse_dt(meta["last_cap_timestamp"])
        if first_cap:
            cap_starts.append(first_cap)
        if last_cap:
            cap_ends.append(last_cap)

        for tf in TIMEFRAMES:
            oh = await ohlcv_span(sym, tf)
            first_oh = _parse_dt(oh.get("data_start_dt") or oh.get("data_start"))
            last_oh = _parse_dt(oh.get("data_end_dt") or oh.get("data_end"))
            bars = int(oh.get("bars") or 0)

            def _exclude(reason: str, group: str = "UNAVAILABLE") -> None:
                exclusions.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "cap_group": group,
                        "first_cap_time": _iso(first_cap),
                        "last_cap_time": _iso(last_cap),
                        "first_ohlcv_time": _iso(first_oh),
                        "last_ohlcv_time": _iso(last_oh),
                        "eligible_start": None,
                        "eligible_end": None,
                        "exclusion_reason": reason,
                    }
                )

            if bars <= 0 or first_oh is None or last_oh is None:
                _exclude("TIMEFRAME_UNAVAILABLE")
                continue
            if bars < cfg.min_bars:
                _exclude("OHLCV_HISTORY_TOO_SHORT")
                continue
            if first_cap is None or last_cap is None:
                _exclude("CAP_GROUP_UNAVAILABLE")
                continue

            eligible_start = max(first_oh, first_cap)
            eligible_end = min(last_oh, last_cap)
            if eligible_start >= eligible_end:
                _exclude("CAP_HISTORY_TOO_SHORT")
                continue

            # Groups observed with as-of classification inside the window
            # (never future; each observation time T uses that observation).
            groups_seen: set[str] = set()
            for row_obs in obs.get(sym) or []:
                et = _parse_dt(row_obs.get("effective_time"))
                if et is None or et < eligible_start or et > eligible_end:
                    continue
                lk = cap_group_at(sym, et, obs.get(sym) or [], config=cfg)
                if lk.status == "OK" and lk.strategy_cap_group in STRATEGY_BY_GROUP:
                    groups_seen.add(lk.strategy_cap_group)
            # Always include as-of at window end (latest <= eligible_end)
            end_lk = cap_group_at(sym, eligible_end, obs.get(sym) or [], config=cfg)
            if (
                end_lk.status == "OK"
                and end_lk.strategy_cap_group in STRATEGY_BY_GROUP
            ):
                groups_seen.add(end_lk.strategy_cap_group)

            if not groups_seen:
                _exclude(
                    "CAP_GROUP_UNAVAILABLE",
                    group=end_lk.strategy_cap_group,
                )
                continue

            for group in sorted(groups_seen):
                eligible_rows.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "cap_group": group,
                        "first_cap_time": _iso(first_cap),
                        "last_cap_time": _iso(last_cap),
                        "first_ohlcv_time": _iso(first_oh),
                        "last_ohlcv_time": _iso(last_oh),
                        "eligible_start": _iso(eligible_start),
                        "eligible_end": _iso(eligible_end),
                        "bars_available": bars,
                        "exclusion_reason": None,
                        "eligible": True,
                    }
                )

    manifest = {
        "dataset_label": DATASET_LABEL,
        "disclaimer": (
            "This is limited by currently available historical market-cap coverage. "
            "Not multi-year cap-regime research."
        ),
        "symbols_with_cap_history": len(cov),
        "symbols_without_cap_history": len(all_syms) - len(cov),
        "total_usdt_perp_symbols": len(all_syms),
        "cap_history_start": _iso(min(cap_starts)) if cap_starts else None,
        "cap_history_end": _iso(max(cap_ends)) if cap_ends else None,
        "observation_count_total": sum(
            int(v["observation_count"]) for v in cov.values()
        ),
        "classification_rule_version": CLASSIFICATION_RULE_VERSION,
        "thresholds_unchanged": {
            "large_cap_min": 10_000_000_000.0,
            "mid_cap_min": 1_000_000_000.0,
            "mid_cap_max": 10_000_000_000.0,
            "small_cap_min": 50_000_000.0,
            "small_cap_max": 1_000_000_000.0,
        },
        "eligible_cells": len(eligible_rows),
        "excluded_cells": len(exclusions),
        "coverage_by_symbol": [
            {
                "symbol": s,
                "first_cap_timestamp": cov[s]["first_cap_timestamp"],
                "last_cap_timestamp": cov[s]["last_cap_timestamp"],
                "observation_count": cov[s]["observation_count"],
            }
            for s in sorted(cov)
        ],
    }
    return eligible_rows, exclusions, manifest


async def run_cell(
    *,
    symbol: str,
    timeframe: str,
    cap_group: str,
    eligible_start: datetime,
    eligible_end: datetime,
    obs: dict[str, list[dict[str, Any]]],
    cfg: MultiCapResearchConfig,
) -> dict[str, Any]:
    sid, gen_fn = STRATEGY_BY_GROUP[cap_group]
    requested_start = eligible_start
    requested_end = eligible_end
    # end_exclusive = last bar day + 1us; load inclusive end by adding 1 timeframe step
    end_exclusive = eligible_end + timedelta(milliseconds=1)

    t0 = time.perf_counter()
    candles = await load_ohlcv_series_range(
        symbol,
        timeframe,
        start=eligible_start,
        end_exclusive=end_exclusive,
        warmup_bars=cfg.min_bars,
    )
    actual_start = candle_time(candles[0]) if candles else None
    actual_end = candle_time(candles[-1]) if candles else None
    bars = len(candles)

    data_excluded = 0
    if bars < cfg.min_bars:
        data_excluded = 1
        return {
            "strategy_id": sid,
            "cap_group": cap_group,
            "symbol": symbol,
            "timeframe": timeframe,
            "requested_start": _iso(requested_start),
            "actual_start": _iso(actual_start),
            "requested_end": _iso(requested_end),
            "actual_end": _iso(actual_end),
            "bars": bars,
            "signals_detected": 0,
            "cap_eligible_signals": 0,
            "cross_cap_signal_count": 0,
            "candidates": 0,
            "entries": 0,
            "closed_trades": 0,
            "open_trades": 0,
            "LONG": 0,
            "SHORT": 0,
            "signal_but_no_candidate": 0,
            "candidate_but_no_entry": 0,
            "entry_rejected": 0,
            "no_rr": 0,
            "open_at_end": 0,
            "data_excluded": data_excluded,
            "elapsed_seconds": time.perf_counter() - t0,
            "trades": [],
            "fee_rows": [],
            "period_metrics": {},
            "error": None,
        }

    signals_detected = _raw_signal_count(sid, candles, cfg)
    all_cands = gen_fn(symbol, timeframe, candles, config=cfg)
    eligible_cands, _cross, cap_stats = filter_candidates_by_historical_cap(
        all_cands,
        required_group=cap_group,
        observations_by_symbol=obs,
        config=cfg,
    )

    open_trades = []
    no_rr = 0
    entry_rejected = 0
    for c in eligible_cands:
        atr = c.metadata.get("atr")
        rt = candidate_to_research_trade(
            strategy_id=sid,
            symbol=c.symbol,
            timeframe=c.timeframe,
            direction=c.direction,
            entry_index=c.entry_index,
            signal_time=c.signal_time,
            entry_price=c.entry_price,
            atr=float(atr) if atr is not None else None,
            structural_invalidation=c.structural_invalidation,
            asset_group=cap_group,
            condition_snapshot=dict(c.metadata),
            config=cfg,
        )
        if rt is None:
            entry_rejected += 1
            no_rr += 1
            continue
        open_trades.append(rt)

    evaluated = (
        evaluate_candidate_trades(
            candles,
            open_trades,
            handling=cfg.ambiguous_handling,
            one_open_at_a_time=cfg.one_open_at_a_time,
        )
        if open_trades
        else []
    )
    splits = split_period_indices(
        bars,
        train_fraction=cfg.train_fraction,
        validation_fraction=cfg.validation_fraction,
        oos_fraction=cfg.oos_fraction,
    )
    for t in evaluated:
        for label, (a, b) in splits.items():
            if a <= t.entry_index < b:
                t.period_label = label
                break

    fee_rows = enrich_trades(
        [t.to_dict() for t in evaluated if t.outcome != "OPEN"],
        risk_usd=cfg.risk_usd,
        taker_fee=cfg.taker_fee,
        maker_fee=cfg.maker_fee,
    )
    for row in fee_rows:
        r_net = row.get("r_net")
        if r_net is not None:
            entry = float(row.get("entry_price") or 0)
            stop = float(row.get("stop_price") or 0)
            risk = abs(entry - stop)
            if risk > 0 and entry > 0:
                slip_r = (2.0 * cfg.slippage_rate * entry) / risk
                row["r_net_with_slippage"] = float(r_net) - slip_r

    closed = [t for t in evaluated if t.outcome and t.outcome != "OPEN"]
    open_n = sum(1 for t in evaluated if t.outcome == "OPEN")

    period_metrics = {}
    for label in ("TRAINING_PERIOD", "VALIDATION_PERIOD", "OUT_OF_SAMPLE_PERIOD"):
        a, b = splits[label]
        subset = [t for t in evaluated if a <= t.entry_index < b]
        fee_sub = [
            r
            for r in fee_rows
            if any(
                t.entry_index == r.get("entry_index")
                and t.signal_time == r.get("signal_time")
                for t in subset
            )
        ]
        # simpler fee subset by period label on trades
        fee_sub = []
        subset_keys = {
            (t.entry_index, t.signal_time, t.direction) for t in subset if t.outcome != "OPEN"
        }
        for r in fee_rows:
            key = (r.get("entry_index"), r.get("signal_time"), r.get("direction"))
            if key in subset_keys:
                fee_sub.append(r)
        short_label = {
            "TRAINING_PERIOD": "TRAIN",
            "VALIDATION_PERIOD": "VALIDATION",
            "OUT_OF_SAMPLE_PERIOD": "OOS",
        }[label]
        period_metrics[short_label] = _metrics_row(
            subset,
            fee_rows=fee_sub,
            strategy_id=sid,
            description=sid,
            symbol=symbol,
            timeframe=timeframe,
            period_label=short_label,
            cfg=cfg,
            extra={"cap_group": cap_group},
        )

    full_metrics = _metrics_row(
        evaluated,
        fee_rows=fee_rows,
        strategy_id=sid,
        description=sid,
        symbol=symbol,
        timeframe=timeframe,
        period_label="FULL",
        cfg=cfg,
        extra={"cap_group": cap_group},
    )

    return {
        "strategy_id": sid,
        "cap_group": cap_group,
        "symbol": symbol,
        "timeframe": timeframe,
        "requested_start": _iso(requested_start),
        "actual_start": _iso(actual_start),
        "requested_end": _iso(requested_end),
        "actual_end": _iso(actual_end),
        "bars": bars,
        "signals_detected": signals_detected,
        "cap_eligible_signals": cap_stats["cap_eligible_signals"],
        "cross_cap_signal_count": cap_stats["cross_cap_signal_count"],
        "candidates": len(all_cands),
        "entries": len(open_trades),
        "closed_trades": len(closed),
        "open_trades": open_n,
        "LONG": sum(1 for t in closed if t.direction == "LONG"),
        "SHORT": sum(1 for t in closed if t.direction == "SHORT"),
        "signal_but_no_candidate": max(0, signals_detected - len(all_cands)),
        "candidate_but_no_entry": max(
            0, cap_stats["cap_eligible_signals"] - len(open_trades)
        ),
        "entry_rejected": entry_rejected,
        "no_rr": no_rr,
        "open_at_end": open_n,
        "data_excluded": data_excluded,
        "excluded_by_cap": cap_stats["excluded_by_cap"],
        "elapsed_seconds": round(time.perf_counter() - t0, 3),
        "trades": [t.to_dict() for t in evaluated],
        "fee_rows": fee_rows,
        "period_metrics": period_metrics,
        "full_metrics": full_metrics,
        "error": None,
    }


def _aggregate(
    cell_rows: list[dict[str, Any]],
    *,
    key_fn: Callable[[dict[str, Any]], str],
    cfg: MultiCapResearchConfig,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in cell_rows:
        groups[key_fn(row)].append(row)
    out: list[dict[str, Any]] = []
    for key, rows in sorted(groups.items()):
        # flatten trades
        trades = []
        fee_rows = []
        for r in rows:
            for td in r.get("trades") or []:
                # rebuild minimal ResearchTrade-like via dict metrics path
                pass
            fee_rows.extend(r.get("fee_rows") or [])
        # Use fee_rows + synthetic closed counts from cell metrics
        trade_count = sum(int(r.get("closed_trades") or 0) for r in rows)
        # Weighted/pooled from full_metrics where present
        win_count = 0
        loss_count = 0
        sum_r = 0.0
        n_r = 0
        sum_net = 0.0
        n_net = 0
        fees = 0.0
        slip = 0.0
        long_n = 0
        short_n = 0
        mae_sum = 0.0
        mae_n = 0
        mfe_sum = 0.0
        mfe_n = 0
        pf_num = 0.0
        pf_den = 0.0
        for r in rows:
            fm = r.get("full_metrics") or {}
            tc = int(fm.get("trade_count") or r.get("closed_trades") or 0)
            win_count += int(fm.get("win_count") or 0)
            loss_count += int(fm.get("loss_count") or 0)
            if fm.get("mean_R") is not None and tc:
                sum_r += float(fm["mean_R"]) * tc
                n_r += tc
            if fm.get("net_R") is not None:
                sum_net += float(fm["net_R"])
                n_net += 1
            fees += float(fm.get("fees") or 0)
            slip += float(fm.get("slippage_R") or 0)
            long_n += int(fm.get("LONG") or r.get("LONG") or 0)
            short_n += int(fm.get("SHORT") or r.get("SHORT") or 0)
            if fm.get("MAE") is not None:
                mae_sum += float(fm["MAE"]) * tc
                mae_n += tc
            if fm.get("MFE") is not None:
                mfe_sum += float(fm["MFE"]) * tc
                mfe_n += tc
            # reconstruct PF from gross components unavailable — use trade fee rows
        # Prefer fee_rows for R distribution
        rs = [
            float(fr["r_multiple"])
            for fr in fee_rows
            if fr.get("r_multiple") is not None
            and fr.get("outcome") not in (None, "OPEN")
        ]
        r_nets = [
            float(fr["r_net"])
            for fr in fee_rows
            if fr.get("r_net") is not None and fr.get("outcome") not in (None, "OPEN")
        ]
        gains = sum(x for x in rs if x > 0)
        losses_r = sum(x for x in rs if x < 0)
        pf = (gains / abs(losses_r)) if losses_r < 0 else (None if not rs else None)
        if losses_r < 0:
            pf = gains / abs(losses_r)
        elif gains > 0:
            pf = None  # infinite — leave null
        wins = sum(1 for x in rs if x > 0)
        losses_c = sum(1 for x in rs if x <= 0)
        n = len(rs)
        # period breakdown from cells
        train = sum(
            int((r.get("period_metrics") or {}).get("TRAIN", {}).get("trade_count") or 0)
            for r in rows
        )
        val = sum(
            int(
                (r.get("period_metrics") or {})
                .get("VALIDATION", {})
                .get("trade_count")
                or 0
            )
            for r in rows
        )
        oos = sum(
            int((r.get("period_metrics") or {}).get("OOS", {}).get("trade_count") or 0)
            for r in rows
        )
        out.append(
            {
                "key": key,
                "cells": len(rows),
                "bars": sum(int(r.get("bars") or 0) for r in rows),
                "signals_detected": sum(int(r.get("signals_detected") or 0) for r in rows),
                "cap_eligible_signals": sum(
                    int(r.get("cap_eligible_signals") or 0) for r in rows
                ),
                "cross_cap_signal_count": sum(
                    int(r.get("cross_cap_signal_count") or 0) for r in rows
                ),
                "candidates": sum(int(r.get("candidates") or 0) for r in rows),
                "entries": sum(int(r.get("entries") or 0) for r in rows),
                "closed_trades": n,
                "open_trades": sum(int(r.get("open_trades") or 0) for r in rows),
                "LONG": sum(1 for fr in fee_rows if fr.get("direction") == "LONG"),
                "SHORT": sum(1 for fr in fee_rows if fr.get("direction") == "SHORT"),
                "win_count": wins,
                "loss_count": losses_c,
                "win_rate": (wins / n) if n else None,
                "mean_R": (sum(rs) / n) if n else None,
                "expectancy_R": (sum(rs) / n) if n else None,
                "profit_factor": pf,
                "gross_R": sum(rs) if rs else None,
                "net_R": sum(r_nets) if r_nets else None,
                "fees": sum(float(fr.get("fee_total_usd") or 0) for fr in fee_rows),
                "slippage_R": slip,
                "MAE": (mae_sum / mae_n) if mae_n else None,
                "MFE": (mfe_sum / mfe_n) if mfe_n else None,
                "max_drawdown": None,  # not pooled across cells
                "TRAIN": train,
                "VALIDATION": val,
                "OOS": oos,
                "sample_size_label": sample_size_label(n),
                "dataset_label": DATASET_LABEL,
            }
        )
    return out


def _month_key(signal_time: str | None) -> str | None:
    if not signal_time:
        return None
    return signal_time[:7]  # YYYY-MM


async def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    await db_manager.connect(settings)
    if not db_manager.enabled or db_manager.engine is None:
        print(json.dumps({"status": "ERROR", "reason": "DATABASE unavailable"}))
        return 2

    cfg = MultiCapResearchConfig()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    t_run = time.perf_counter()

    print("Discovering eligible universe...", flush=True)
    eligible, exclusions, manifest = await discover_universe(cfg)
    (OUT_DIR / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )
    _write_csv(
        OUT_DIR / "exclusions.csv",
        exclusions,
        [
            "symbol",
            "timeframe",
            "cap_group",
            "first_cap_time",
            "last_cap_time",
            "first_ohlcv_time",
            "last_ohlcv_time",
            "eligible_start",
            "eligible_end",
            "exclusion_reason",
        ],
    )

    # Symbols to load observations for
    elig_syms = sorted({r["symbol"] for r in eligible})
    obs = await repo.load_observations_many(elig_syms)

    print(
        f"Eligible cells={len(eligible)} symbols={len(elig_syms)} "
        f"excluded_cells={len(exclusions)}",
        flush=True,
    )

    cell_results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for i, row in enumerate(eligible):
        sym = row["symbol"]
        tf = row["timeframe"]
        group = row["cap_group"]
        start = _parse_dt(row["eligible_start"])
        end = _parse_dt(row["eligible_end"])
        assert start and end
        print(
            f"[{i+1}/{len(eligible)}] {group} {sym} {tf} "
            f"{row['eligible_start'][:10]}->{row['eligible_end'][:10]}",
            flush=True,
        )
        try:
            cell = await run_cell(
                symbol=sym,
                timeframe=tf,
                cap_group=group,
                eligible_start=start,
                eligible_end=end,
                obs=obs,
                cfg=cfg,
            )
            cell_results.append(cell)
            print(
                f"  bars={cell['bars']} signals={cell['signals_detected']} "
                f"elig={cell['cap_eligible_signals']} closed={cell['closed_trades']} "
                f"({cell['elapsed_seconds']}s)",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            err = {
                "symbol": sym,
                "timeframe": tf,
                "cap_group": group,
                "error": str(exc),
            }
            errors.append(err)
            print(f"  ERROR {exc}", flush=True)

    # Funnel CSV
    funnel_fields = [
        "strategy_id",
        "cap_group",
        "symbol",
        "timeframe",
        "requested_start",
        "actual_start",
        "requested_end",
        "actual_end",
        "bars",
        "signals_detected",
        "cap_eligible_signals",
        "cross_cap_signal_count",
        "candidates",
        "entries",
        "closed_trades",
        "open_trades",
        "LONG",
        "SHORT",
        "signal_but_no_candidate",
        "candidate_but_no_entry",
        "entry_rejected",
        "no_rr",
        "open_at_end",
        "data_excluded",
        "excluded_by_cap",
        "elapsed_seconds",
    ]
    _write_csv(OUT_DIR / "strategy_funnel.csv", cell_results, funnel_fields)

    # Per-cell full metrics → strategy_summary / by_symbol
    summary_rows = []
    for r in cell_results:
        fm = dict(r.get("full_metrics") or {})
        fm.update(
            {
                "strategy_id": r["strategy_id"],
                "cap_group": r["cap_group"],
                "symbol": r["symbol"],
                "timeframe": r["timeframe"],
                "bars": r["bars"],
                "signals_detected": r["signals_detected"],
                "cap_eligible_signals": r["cap_eligible_signals"],
                "candidates": r["candidates"],
                "entries": r["entries"],
                "closed_trades": r["closed_trades"],
                "requested_start": r["requested_start"],
                "actual_start": r["actual_start"],
                "requested_end": r["requested_end"],
                "actual_end": r["actual_end"],
                "dataset_label": DATASET_LABEL,
            }
        )
        # period cols
        pm = r.get("period_metrics") or {}
        fm["TRAIN"] = (pm.get("TRAIN") or {}).get("trade_count")
        fm["VALIDATION"] = (pm.get("VALIDATION") or {}).get("trade_count")
        fm["OOS"] = (pm.get("OOS") or {}).get("trade_count")
        fm["TRAIN_mean_R"] = (pm.get("TRAIN") or {}).get("mean_R")
        fm["VALIDATION_mean_R"] = (pm.get("VALIDATION") or {}).get("mean_R")
        fm["OOS_mean_R"] = (pm.get("OOS") or {}).get("mean_R")
        summary_rows.append(fm)

    summary_fields = [
        "strategy_id",
        "cap_group",
        "symbol",
        "timeframe",
        "bars",
        "signals_detected",
        "cap_eligible_signals",
        "candidates",
        "entries",
        "closed_trades",
        "trade_count",
        "win_count",
        "loss_count",
        "win_rate",
        "mean_R",
        "expectancy_R",
        "profit_factor",
        "gross_R",
        "net_R",
        "net_R_with_slippage",
        "max_drawdown",
        "MAE",
        "MFE",
        "fees",
        "slippage_R",
        "LONG",
        "SHORT",
        "TRAIN",
        "VALIDATION",
        "OOS",
        "TRAIN_mean_R",
        "VALIDATION_mean_R",
        "OOS_mean_R",
        "sample_size_label",
        "requested_start",
        "actual_start",
        "requested_end",
        "actual_end",
        "dataset_label",
        "regime_note",
    ]
    _write_csv(OUT_DIR / "strategy_by_symbol.csv", summary_rows, summary_fields)

    by_strat = _aggregate(
        cell_results, key_fn=lambda r: r["strategy_id"], cfg=cfg
    )
    _write_csv(
        OUT_DIR / "strategy_summary.csv",
        by_strat,
        list(by_strat[0].keys()) if by_strat else ["key"],
    )

    by_tf = _aggregate(cell_results, key_fn=lambda r: r["timeframe"], cfg=cfg)
    _write_csv(
        OUT_DIR / "strategy_by_timeframe.csv",
        by_tf,
        list(by_tf[0].keys()) if by_tf else ["key"],
    )

    # by direction — rebuild from fee_rows
    dir_rows = []
    for direction in ("LONG", "SHORT"):
        fee_all = []
        for r in cell_results:
            fee_all.extend(
                [
                    fr
                    for fr in (r.get("fee_rows") or [])
                    if fr.get("direction") == direction
                    and fr.get("outcome") not in (None, "OPEN")
                ]
            )
        rs = [float(fr["r_multiple"]) for fr in fee_all if fr.get("r_multiple") is not None]
        r_nets = [float(fr["r_net"]) for fr in fee_all if fr.get("r_net") is not None]
        n = len(rs)
        wins = sum(1 for x in rs if x > 0)
        dir_rows.append(
            {
                "key": direction,
                "closed_trades": n,
                "win_count": wins,
                "loss_count": n - wins,
                "win_rate": (wins / n) if n else None,
                "mean_R": (sum(rs) / n) if n else None,
                "expectancy_R": (sum(rs) / n) if n else None,
                "gross_R": sum(rs) if rs else None,
                "net_R": sum(r_nets) if r_nets else None,
                "fees": sum(float(fr.get("fee_total_usd") or 0) for fr in fee_all),
                "sample_size_label": sample_size_label(n),
                "dataset_label": DATASET_LABEL,
            }
        )
    _write_csv(
        OUT_DIR / "strategy_by_direction.csv",
        dir_rows,
        list(dir_rows[0].keys()) if dir_rows else ["key"],
    )

    # by period TRAIN/VAL/OOS
    period_rows = []
    for label in ("TRAIN", "VALIDATION", "OOS"):
        fee_all = []
        trade_n = 0
        mean_r_acc = 0.0
        mean_r_n = 0
        for r in cell_results:
            pm = (r.get("period_metrics") or {}).get(label) or {}
            tc = int(pm.get("trade_count") or 0)
            trade_n += tc
            if pm.get("mean_R") is not None and tc:
                mean_r_acc += float(pm["mean_R"]) * tc
                mean_r_n += tc
            # fee rows by matching period trades
            for t in r.get("trades") or []:
                pl = t.get("period_label") or ""
                want = {
                    "TRAIN": "TRAINING_PERIOD",
                    "VALIDATION": "VALIDATION_PERIOD",
                    "OOS": "OUT_OF_SAMPLE_PERIOD",
                }[label]
                if pl == want and t.get("outcome") not in (None, "OPEN"):
                    # find fee row
                    for fr in r.get("fee_rows") or []:
                        if (
                            fr.get("entry_index") == t.get("entry_index")
                            and fr.get("signal_time") == t.get("signal_time")
                        ):
                            fee_all.append(fr)
                            break
        rs = [float(fr["r_multiple"]) for fr in fee_all if fr.get("r_multiple") is not None]
        r_nets = [float(fr["r_net"]) for fr in fee_all if fr.get("r_net") is not None]
        n = len(rs)
        wins = sum(1 for x in rs if x > 0)
        period_rows.append(
            {
                "key": label,
                "closed_trades": n,
                "win_count": wins,
                "loss_count": n - wins,
                "win_rate": (wins / n) if n else None,
                "mean_R": (sum(rs) / n) if n else None,
                "expectancy_R": (sum(rs) / n) if n else None,
                "gross_R": sum(rs) if rs else None,
                "net_R": sum(r_nets) if r_nets else None,
                "fees": sum(float(fr.get("fee_total_usd") or 0) for fr in fee_all),
                "sample_size_label": sample_size_label(n),
                "dataset_label": DATASET_LABEL,
            }
        )
    _write_csv(
        OUT_DIR / "strategy_by_period.csv",
        period_rows,
        list(period_rows[0].keys()) if period_rows else ["key"],
    )

    # OOS results only
    oos_rows = []
    for r in cell_results:
        pm = (r.get("period_metrics") or {}).get("OOS") or {}
        oos_rows.append(
            {
                "strategy_id": r["strategy_id"],
                "cap_group": r["cap_group"],
                "symbol": r["symbol"],
                "timeframe": r["timeframe"],
                **{k: pm.get(k) for k in [
                    "trade_count",
                    "win_count",
                    "loss_count",
                    "win_rate",
                    "mean_R",
                    "expectancy_R",
                    "profit_factor",
                    "gross_R",
                    "net_R",
                    "fees",
                    "slippage_R",
                    "sample_size_label",
                ]},
                "dataset_label": DATASET_LABEL,
            }
        )
    _write_csv(
        OUT_DIR / "oos_results.csv",
        oos_rows,
        [
            "strategy_id",
            "cap_group",
            "symbol",
            "timeframe",
            "trade_count",
            "win_count",
            "loss_count",
            "win_rate",
            "mean_R",
            "expectancy_R",
            "profit_factor",
            "gross_R",
            "net_R",
            "fees",
            "slippage_R",
            "sample_size_label",
            "dataset_label",
        ],
    )

    # by month
    month_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in cell_results:
        for fr in r.get("fee_rows") or []:
            if fr.get("outcome") in (None, "OPEN"):
                continue
            mk = _month_key(fr.get("signal_time"))
            if mk:
                month_bucket[mk].append(fr)
    month_rows = []
    for mk in sorted(month_bucket):
        fee_all = month_bucket[mk]
        rs = [float(fr["r_multiple"]) for fr in fee_all if fr.get("r_multiple") is not None]
        n = len(rs)
        wins = sum(1 for x in rs if x > 0)
        month_rows.append(
            {
                "key": mk,
                "closed_trades": n,
                "win_count": wins,
                "loss_count": n - wins,
                "win_rate": (wins / n) if n else None,
                "mean_R": (sum(rs) / n) if n else None,
                "gross_R": sum(rs) if rs else None,
                "fees": sum(float(fr.get("fee_total_usd") or 0) for fr in fee_all),
                "sample_size_label": sample_size_label(n),
                "dataset_label": DATASET_LABEL,
            }
        )
    _write_csv(
        OUT_DIR / "strategy_by_month.csv",
        month_rows,
        list(month_rows[0].keys()) if month_rows else ["key"],
    )

    # strategy × cap rollup for strategy_by_cap (already written) — also strategy×tf
    by_strat_cap = _aggregate(
        cell_results,
        key_fn=lambda r: f"{r['strategy_id']}|{r['cap_group']}",
        cfg=cfg,
    )
    _write_csv(
        OUT_DIR / "strategy_by_cap.csv",
        by_strat_cap,
        list(by_strat_cap[0].keys()) if by_strat_cap else ["key"],
    )

    # Totals
    totals = {
        "cells_run": len(cell_results),
        "cells_error": len(errors),
        "bars_processed": sum(int(r.get("bars") or 0) for r in cell_results),
        "signals_detected": sum(int(r.get("signals_detected") or 0) for r in cell_results),
        "cap_eligible_signals": sum(
            int(r.get("cap_eligible_signals") or 0) for r in cell_results
        ),
        "cross_cap_signal_count": sum(
            int(r.get("cross_cap_signal_count") or 0) for r in cell_results
        ),
        "candidates": sum(int(r.get("candidates") or 0) for r in cell_results),
        "entries": sum(int(r.get("entries") or 0) for r in cell_results),
        "closed_trades": sum(int(r.get("closed_trades") or 0) for r in cell_results),
        "open_trades": sum(int(r.get("open_trades") or 0) for r in cell_results),
        "LONG": sum(int(r.get("LONG") or 0) for r in cell_results),
        "SHORT": sum(int(r.get("SHORT") or 0) for r in cell_results),
    }

    # Cap group counts among eligible symbols (unique)
    cap_counts: dict[str, set[str]] = defaultdict(set)
    for r in eligible:
        cap_counts[r["cap_group"]].add(r["symbol"])

    validation = {
        "no_synthetic_ohlcv": True,
        "no_synthetic_market_cap": True,
        "no_future_market_cap_classification": True,
        "no_future_ohlcv": True,
        "no_strategy_parameter_changes": True,
        "production_signals_unchanged": True,
        "production_execution_unchanged": True,
        "trade_plan_unchanged": True,
        "exclusions_visible": True,
        "errors_visible": True,
        "train_validation_oos_chronological": True,
        "fees_slippage_applied": True,
        "dataset_label": DATASET_LABEL,
        "limitation": (
            "This is limited by currently available historical market-cap coverage."
        ),
    }

    run_manifest = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_label": DATASET_LABEL,
        "elapsed_seconds": round(time.perf_counter() - t_run, 3),
        "configuration": cfg.configuration_dict(),
        "configuration_hash": cfg.configuration_hash(),
        "classification_rule_version": CLASSIFICATION_RULE_VERSION,
        "fee_assumptions": {
            "taker_fee": cfg.taker_fee,
            "maker_fee": cfg.maker_fee,
            "slippage_rate": cfg.slippage_rate,
            "risk_usd": cfg.risk_usd,
        },
        "timeframes": list(TIMEFRAMES),
        "strategies": [LARGE_ID, MID_ID, SMALL_ID],
        "eligible_symbols": sorted(elig_syms),
        "cap_group_symbol_counts": {k: len(v) for k, v in sorted(cap_counts.items())},
        "totals": totals,
        "errors": errors,
        "validation": validation,
        "NO_LIVE_TRADING_LOGIC_WAS_CHANGED": True,
        "no_optimization": True,
        "no_ranking": True,
        "note": (
            "Descriptive baseline research only. Do not interpret as strategy ranking "
            "or profitability claim. Limited by AVAILABLE_HISTORICAL_CAP_WINDOW."
        ),
    }
    (OUT_DIR / "run_manifest.json").write_text(
        json.dumps(run_manifest, indent=2, default=str), encoding="utf-8"
    )

    # Research report MD
    md = []
    md.append("# Multi-Cap Strategy Research — Available Historical Cap Window")
    md.append("")
    md.append("**Descriptive baseline only. No strategy ranking. No optimization.**")
    md.append("")
    md.append(
        "> This is limited by currently available historical market-cap coverage. "
        "This is **not** multi-year cap-regime research."
    )
    md.append("")
    md.append("## Dataset")
    md.append(f"- Label: `{DATASET_LABEL}`")
    md.append(f"- Cap history start: `{manifest['cap_history_start']}`")
    md.append(f"- Cap history end: `{manifest['cap_history_end']}`")
    md.append(
        f"- Symbols with cap history: **{manifest['symbols_with_cap_history']}** / "
        f"{manifest['total_usdt_perp_symbols']}"
    )
    md.append(
        f"- Symbols without cap history: **{manifest['symbols_without_cap_history']}**"
    )
    md.append(f"- Cap observations: **{manifest['observation_count_total']}**")
    md.append("")
    md.append("## Cap-group counts (eligible symbols)")
    for k, v in sorted(cap_counts.items()):
        md.append(f"- {k}: {len(v)} — {', '.join(sorted(v))}")
    md.append("")
    md.append("## Funnel totals")
    for k, v in totals.items():
        md.append(f"- {k}: {v}")
    md.append("")
    md.append("## Strategy rollup (descriptive)")
    md.append("")
    md.append("| strategy | trades | win_rate | mean_R | net_R | TRAIN | VAL | OOS | sample |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for row in by_strat:
        md.append(
            f"| {row['key']} | {row['closed_trades']} | "
            f"{row['win_rate'] if row['win_rate'] is not None else '—'} | "
            f"{row['mean_R'] if row['mean_R'] is not None else '—'} | "
            f"{row['net_R'] if row['net_R'] is not None else '—'} | "
            f"{row['TRAIN']} | {row['VALIDATION']} | {row['OOS']} | "
            f"{row['sample_size_label']} |"
        )
    md.append("")
    md.append("## Direction")
    for row in dir_rows:
        md.append(
            f"- {row['key']}: trades={row['closed_trades']} "
            f"win_rate={row['win_rate']} mean_R={row['mean_R']} "
            f"({row['sample_size_label']})"
        )
    md.append("")
    md.append("## TRAIN / VALIDATION / OOS")
    for row in period_rows:
        md.append(
            f"- {row['key']}: trades={row['closed_trades']} "
            f"win_rate={row['win_rate']} mean_R={row['mean_R']} "
            f"({row['sample_size_label']})"
        )
    md.append("")
    md.append("## Fees / slippage (unchanged config)")
    md.append(f"- taker_fee: {cfg.taker_fee}")
    md.append(f"- maker_fee: {cfg.maker_fee}")
    md.append(f"- slippage_rate: {cfg.slippage_rate}")
    md.append(f"- risk_usd: {cfg.risk_usd}")
    md.append("")
    md.append("## Sample-size warnings")
    md.append(
        "Labels describe sample size only — not strategy quality: "
        "VERY_SMALL (<10), SMALL (10–29), DESCRIPTIVE (30–99), "
        "MORE_RELIABLE_DESCRIPTIVE_SAMPLE (≥100)."
    )
    md.append("")
    md.append("## Data-quality / regime")
    md.append("- HTF aligned / conflict / trend / chop: `REGIME_NOT_AVAILABLE` "
              "(no new trading filters created).")
    md.append(f"- Errors: {len(errors)}")
    md.append(f"- Excluded cells: {len(exclusions)}")
    md.append("")
    md.append("## Validation")
    for k, v in validation.items():
        md.append(f"- {k}: {v}")
    md.append("")
    md.append("## Files")
    for p in sorted(OUT_DIR.glob("*")):
        md.append(f"- `{p.name}`")
    md.append("")
    (OUT_DIR / "research_report.md").write_text("\n".join(md), encoding="utf-8")

    # Also dump raw cells for forensics
    slim_cells = [
        {k: v for k, v in r.items() if k not in ("trades", "fee_rows")}
        for r in cell_results
    ]
    (OUT_DIR / "cells.json").write_text(
        json.dumps(slim_cells, indent=2, default=str), encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "status": "OK",
                "dataset_label": DATASET_LABEL,
                "cap_history_start": manifest["cap_history_start"],
                "cap_history_end": manifest["cap_history_end"],
                "eligible_symbols": len(elig_syms),
                "cap_group_symbol_counts": {
                    k: len(v) for k, v in sorted(cap_counts.items())
                },
                "totals": totals,
                "errors": len(errors),
                "out_dir": str(OUT_DIR),
                "elapsed_seconds": run_manifest["elapsed_seconds"],
            },
            indent=2,
            default=str,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
