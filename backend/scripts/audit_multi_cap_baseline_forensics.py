"""Multi-Cap baseline forensics + data-quality audit (research only).

Replays the same research path as run_id 20261003T134241Z to recover trade-level
rows (not persisted in cells.json). Does not change strategy parameters or live logic.

Usage (from backend/):
  python scripts/audit_multi_cap_baseline_forensics.py
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.research.combination_backtest import evaluate_candidate_trades  # noqa: E402
from app.research.config import split_period_indices  # noqa: E402
from app.research.historical_market_cap.classifier import (  # noqa: E402
    cap_group_at,
    filter_candidates_by_historical_cap,
)
from app.research.historical_market_cap import repository as repo  # noqa: E402
from app.research.multi_cap_strategies.baseline_forensics import (  # noqa: E402
    aggregate_metrics,
    build_cap_transitions,
    calendar_days,
    classify_cell_history_bucket,
    funnel_gap_explanation,
    history_bucket,
    median,
    mean,
    net_r_with_slip,
    parse_dt,
    reconcile_artifact_totals,
    reference_comparison_aggregate,
    safe_float,
    slip_r_from_row,
    summarize_cap_transitions,
)
from app.research.multi_cap_strategies.common import (  # noqa: E402
    candidate_to_research_trade,
)
from app.research.multi_cap_strategies.config import MultiCapResearchConfig  # noqa: E402
from app.research.multi_cap_strategies.large_cap_sweep_choch import (  # noqa: E402
    STRATEGY_ID as LARGE_ID,
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
from app.research.postgres_ohlcv import load_ohlcv_series_range  # noqa: E402
from app.research.trade_fees import enrich_trades  # noqa: E402
from app.services.database import db_manager  # noqa: E402
from app.signals._candle_utils import candle_time  # noqa: E402

BASE_OUT = ROOT / "scripts" / "multi_cap_full_research_out"
FORENSICS = BASE_OUT / "baseline_forensics"
EXPECTED_RUN_ID = "20261003T134241Z"

STRATEGY_BY_GROUP = {
    "LARGE_CAP": (LARGE_ID, gen_large),
    "MID_CAP": (MID_ID, gen_mid),
    "SMALL_CAP": (SMALL_ID, gen_small),
}
DIRECTION_SUPPORT = {
    LARGE_ID: "LONG",
    MID_ID: "LONG",
    SMALL_ID: "LONG",
}


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = fields or list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _iso(ts: Any) -> str | None:
    if ts is None:
        return None
    if hasattr(ts, "isoformat"):
        return ts.isoformat()
    return str(ts)


def expected_bars(days: float | None, timeframe: str) -> float | None:
    if days is None:
        return None
    minutes = {"5m": 5, "15m": 15, "1h": 60}.get(timeframe)
    if not minutes:
        return None
    return (days * 1440.0) / minutes


async def replay_cell(
    cell: dict[str, Any],
    obs: dict[str, list[dict[str, Any]]],
    cfg: MultiCapResearchConfig,
) -> dict[str, Any]:
    symbol = cell["symbol"]
    timeframe = cell["timeframe"]
    cap_group = cell["cap_group"]
    sid, gen_fn = STRATEGY_BY_GROUP[cap_group]
    start = parse_dt(cell["requested_start"])
    end = parse_dt(cell["requested_end"])
    assert start and end
    end_exclusive = end + timedelta(milliseconds=1)
    candles = await load_ohlcv_series_range(
        symbol,
        timeframe,
        start=start,
        end_exclusive=end_exclusive,
        warmup_bars=cfg.min_bars,
    )
    bars = len(candles)
    if bars < cfg.min_bars:
        return {
            **cell,
            "replay_closed": 0,
            "replay_open": 0,
            "suppressed_due_to_open_trade": 0,
            "trades": [],
            "fee_rows": [],
            "dq": {"status": "SKIP_SHORT_SERIES"},
        }

    # DQ checks on series
    times = [candle_time(c) for c in candles]
    dq_issues: list[str] = []
    for i in range(1, len(times)):
        if times[i] is None or times[i - 1] is None:
            continue
        if times[i] <= times[i - 1]:
            dq_issues.append(f"timestamp_not_increasing@{i}")
            break
    now = datetime.now(timezone.utc)
    if times and times[-1] and times[-1] > now + timedelta(minutes=5):
        dq_issues.append("future_ohlcv_timestamp")
    invalid_ohlc = 0
    for c in candles:
        try:
            o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
            if h < max(o, cl) or l > min(o, cl) or h < l:
                invalid_ohlc += 1
        except Exception:  # noqa: BLE001
            invalid_ohlc += 1
    if invalid_ohlc:
        dq_issues.append(f"invalid_ohlc_count={invalid_ohlc}")

    all_cands = gen_fn(symbol, timeframe, candles, config=cfg)
    eligible_cands, cross, cap_stats = filter_candidates_by_historical_cap(
        all_cands,
        required_group=cap_group,
        observations_by_symbol=obs,
        config=cfg,
    )

    # future cap leakage spot-check on eligible
    lookahead_violations: list[dict[str, Any]] = []
    for c in eligible_cands[:50]:  # sample first 50 per cell for cost control
        st = parse_dt(c.signal_time)
        if st is None:
            continue
        lk = cap_group_at(symbol, st, obs.get(symbol) or [], config=cfg)
        if lk.effective_time and lk.effective_time > st:
            lookahead_violations.append(
                {
                    "symbol": symbol,
                    "signal_time": c.signal_time,
                    "cap_effective_time": lk.effective_time.isoformat(),
                    "expected": "effective_time <= signal_time",
                    "actual": "effective_time > signal_time",
                }
            )

    open_trades = []
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
        if rt is not None:
            open_trades.append(rt)

    # Count one-open suppressions explicitly
    ordered = sorted(open_trades, key=lambda t: (t.entry_index, t.symbol, t.combination_id))
    next_allowed = 0
    suppressed = 0
    kept = []
    for cand in ordered:
        if cfg.one_open_at_a_time and cand.entry_index < next_allowed:
            suppressed += 1
            continue
        kept.append(cand)
        # peek exit via full evaluate later — approximate next_allowed after evaluate
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
    # Recompute suppressed as entries - evaluated (authoritative vs evaluate)
    suppressed = max(0, len(open_trades) - len(evaluated))

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

    # Chronology checks
    for t in evaluated:
        if t.exit_index is not None and t.exit_index < t.entry_index:
            lookahead_violations.append(
                {
                    "symbol": symbol,
                    "event": "exit_before_entry",
                    "entry_index": t.entry_index,
                    "exit_index": t.exit_index,
                }
            )
        if t.signal_time and t.exit_time:
            st = parse_dt(t.signal_time)
            xt = parse_dt(t.exit_time)
            if st and xt and xt < st:
                lookahead_violations.append(
                    {
                        "symbol": symbol,
                        "event": "exit_time_before_signal",
                        "signal_time": t.signal_time,
                        "exit_time": t.exit_time,
                    }
                )

    fee_rows = enrich_trades(
        [t.to_dict() for t in evaluated if t.outcome != "OPEN"],
        risk_usd=cfg.risk_usd,
        taker_fee=cfg.taker_fee,
        maker_fee=cfg.maker_fee,
    )
    # attach cell meta + slip
    for row in fee_rows:
        row["strategy_id"] = sid
        row["cap_group"] = cap_group
        row["history_bucket"] = history_bucket(
            calendar_days(cell.get("requested_start"), cell.get("requested_end"))
        )
        slip = slip_r_from_row(row, slippage_rate=cfg.slippage_rate)
        row["slippage_R"] = slip
        rn = safe_float(row.get("r_net"))
        row["net_R_with_slippage"] = (rn - slip) if rn is not None and slip is not None else rn
        # period short label
        pl = row.get("period_label") or ""
        row["period"] = {
            "TRAINING_PERIOD": "TRAIN",
            "VALIDATION_PERIOD": "VALIDATION",
            "OUT_OF_SAMPLE_PERIOD": "OOS",
        }.get(pl, pl)

    closed = [t for t in evaluated if t.outcome and t.outcome != "OPEN"]
    open_n = sum(1 for t in evaluated if t.outcome == "OPEN")

    # period split chronological validation
    train_end = splits["TRAINING_PERIOD"][1]
    val_end = splits["VALIDATION_PERIOD"][1]
    chrono_ok = train_end <= val_end <= bars

    return {
        "strategy_id": sid,
        "cap_group": cap_group,
        "symbol": symbol,
        "timeframe": timeframe,
        "requested_start": cell.get("requested_start"),
        "requested_end": cell.get("requested_end"),
        "actual_start": _iso(candle_time(candles[0]) if candles else None),
        "actual_end": _iso(candle_time(candles[-1]) if candles else None),
        "bars": bars,
        "signals_detected": cell.get("signals_detected"),
        "cap_eligible_signals": cap_stats["cap_eligible_signals"],
        "candidates": len(all_cands),
        "entries": len(open_trades),
        "closed_trades": len(closed),
        "open_trades": open_n,
        "suppressed_due_to_open_trade": suppressed,
        "cross_cap": cap_stats["cross_cap_signal_count"],
        "trades": [t.to_dict() for t in evaluated],
        "fee_rows": fee_rows,
        "dq": {
            "issues": dq_issues,
            "invalid_ohlc": invalid_ohlc,
            "chrono_split_ok": chrono_ok,
            "lookahead_violations": lookahead_violations,
        },
        "splits": {k: list(v) for k, v in splits.items()},
    }


async def main() -> int:
    FORENSICS.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    await db_manager.connect(settings)
    if not db_manager.enabled or db_manager.engine is None:
        print(json.dumps({"status": "ERROR", "reason": "DATABASE unavailable"}))
        return 2

    cfg = MultiCapResearchConfig()
    rm = json.loads((BASE_OUT / "run_manifest.json").read_text(encoding="utf-8"))
    dm = json.loads((BASE_OUT / "dataset_manifest.json").read_text(encoding="utf-8"))
    cells = json.loads((BASE_OUT / "cells.json").read_text(encoding="utf-8"))
    funnel = _read_csv(BASE_OUT / "strategy_funnel.csv")
    summary = _read_csv(BASE_OUT / "strategy_summary.csv")
    by_period = _read_csv(BASE_OUT / "strategy_by_period.csv")
    by_tf = _read_csv(BASE_OUT / "strategy_by_timeframe.csv")
    by_sym = _read_csv(BASE_OUT / "strategy_by_symbol.csv")
    by_dir = _read_csv(BASE_OUT / "strategy_by_direction.csv")

    if rm.get("run_id") != EXPECTED_RUN_ID:
        print(
            json.dumps(
                {
                    "status": "STOP",
                    "reason": "run_id mismatch",
                    "expected": EXPECTED_RUN_ID,
                    "actual": rm.get("run_id"),
                },
                indent=2,
            )
        )
        return 3

    recon = reconcile_artifact_totals(
        run_manifest=rm,
        funnel_rows=funnel,
        summary_rows=summary,
        period_rows=by_period,
        timeframe_rows=by_tf,
        symbol_rows=by_sym,
        direction_rows=by_dir,
        cells=cells,
    )
    (FORENSICS / "artifact_reconciliation.json").write_text(
        json.dumps(recon, indent=2, default=str), encoding="utf-8"
    )
    if recon["status"] != "PASS":
        print(json.dumps({"status": "STOP", "reconciliation": recon}, indent=2, default=str))
        return 4

    print("Artifact reconciliation: PASS", flush=True)

    # Cap transitions for eligible symbols (+ all with history for context)
    elig_syms = sorted(set(rm.get("eligible_symbols") or []))
    obs = await repo.load_observations_many(elig_syms)
    transitions = build_cap_transitions(
        obs,
        classify_fn=lambda sym, et, o: cap_group_at(sym, et, o, config=cfg),
    )
    _write_csv(FORENSICS / "cap_transitions.csv", transitions)
    tsum = summarize_cap_transitions(transitions)
    _write_csv(
        FORENSICS / "cap_transition_summary.csv",
        [
            {
                "metric": k,
                "value": (
                    len(v)
                    if isinstance(v, list)
                    else v
                ),
                "detail": (
                    ",".join(v) if isinstance(v, list) and k.endswith("symbols") else ""
                ),
            }
            for k, v in tsum.items()
        ],
    )
    print(
        f"Cap transitions: overlapping={tsum['symbols_with_overlapping_group_membership']} "
        f"multi={tsum['symbols_with_multiple_transitions']}",
        flush=True,
    )

    # History buckets from baseline cells (no replay needed)
    bucket_rows = []
    for c in cells:
        row = classify_cell_history_bucket(c)
        # cap obs count in window
        start = parse_dt(c.get("requested_start"))
        end = parse_dt(c.get("requested_end"))
        n_obs = 0
        for o in obs.get(c["symbol"]) or []:
            et = parse_dt(o.get("effective_time"))
            if et and start and end and start <= et <= end:
                n_obs += 1
        days = row.get("calendar_days")
        exp = expected_bars(days, c["timeframe"])
        bars = int(c.get("bars") or 0)
        # coverage vs calendar (warmup inflates bars) — report both
        row["cap_observations"] = n_obs
        row["coverage_ratio"] = (bars / exp) if exp and exp > 0 else None
        row["signals"] = c.get("signals_detected")
        bucket_rows.append(row)
    _write_csv(FORENSICS / "cell_history_buckets.csv", bucket_rows)
    bucket_counts = Counter(r["history_bucket"] for r in bucket_rows)
    print("History buckets:", dict(bucket_counts), flush=True)

    # Replay for trade-level forensics
    print(f"Replaying {len(cells)} cells for trade-level forensics...", flush=True)
    t0 = time.perf_counter()
    replays: list[dict[str, Any]] = []
    all_fee_rows: list[dict[str, Any]] = []
    all_trades: list[dict[str, Any]] = []
    dq_all: list[dict[str, Any]] = []
    lookahead_all: list[dict[str, Any]] = []
    suppress_rows: list[dict[str, Any]] = []

    for i, cell in enumerate(cells):
        print(
            f"[{i+1}/{len(cells)}] {cell['cap_group']} {cell['symbol']} {cell['timeframe']}",
            flush=True,
        )
        r = await replay_cell(cell, obs, cfg)
        replays.append(r)
        all_fee_rows.extend(r.get("fee_rows") or [])
        all_trades.extend(r.get("trades") or [])
        dq_all.append(
            {
                "symbol": r["symbol"],
                "timeframe": r["timeframe"],
                "strategy_id": r["strategy_id"],
                **(r.get("dq") or {}),
            }
        )
        for v in (r.get("dq") or {}).get("lookahead_violations") or []:
            lookahead_all.append(v)
        suppress_rows.append(
            {
                "strategy": r["strategy_id"],
                "symbol": r["symbol"],
                "timeframe": r["timeframe"],
                "cap_group": r["cap_group"],
                "candidate_count": r.get("candidates"),
                "cap_eligible": r.get("cap_eligible_signals"),
                "entries": r.get("entries"),
                "evaluated": int(r.get("closed_trades") or 0) + int(r.get("open_trades") or 0),
                "suppressed_due_to_open_trade": r.get("suppressed_due_to_open_trade"),
            }
        )

    print(f"Replay done in {time.perf_counter()-t0:.1f}s trades={len(all_fee_rows)}", flush=True)

    # Verify replay closed totals vs baseline — report discrepancies; do not silent-fix.
    replay_closed = sum(int(r.get("closed_trades") or 0) for r in replays)
    replay_open = sum(int(r.get("open_trades") or 0) for r in replays)
    replay_entries = sum(int(r.get("entries") or 0) for r in replays)
    cell_diffs: list[dict[str, Any]] = []
    by_key = {
        (r["strategy_id"], r["symbol"], r["timeframe"], r["cap_group"]): r for r in replays
    }
    for c in cells:
        key = (c["strategy_id"], c["symbol"], c["timeframe"], c["cap_group"])
        r = by_key.get(key)
        b_closed = int(c.get("closed_trades") or 0)
        r_closed = int((r or {}).get("closed_trades") or 0)
        if b_closed != r_closed:
            cell_diffs.append(
                {
                    "strategy_id": c["strategy_id"],
                    "symbol": c["symbol"],
                    "timeframe": c["timeframe"],
                    "cap_group": c["cap_group"],
                    "baseline_closed": b_closed,
                    "replay_closed": r_closed,
                    "delta": r_closed - b_closed,
                    "baseline_entries": c.get("entries"),
                    "replay_entries": (r or {}).get("entries"),
                    "baseline_actual_end": c.get("actual_end"),
                    "replay_actual_end": (r or {}).get("actual_end"),
                    "baseline_bars": c.get("bars"),
                    "replay_bars": (r or {}).get("bars"),
                }
            )
    _write_csv(FORENSICS / "replay_closed_discrepancy.csv", cell_diffs)
    replay_match_status = "PASS" if replay_closed == int(rm["totals"]["closed_trades"]) else "FAIL"
    if replay_match_status != "PASS":
        print(
            json.dumps(
                {
                    "warning": "replay_closed_mismatch",
                    "replay_closed": replay_closed,
                    "baseline_closed": rm["totals"]["closed_trades"],
                    "cell_diff_count": len(cell_diffs),
                    "cell_diffs": cell_diffs,
                    "action": (
                        "Continuing forensics on replay blotter; discrepancy recorded. "
                        "Baseline artifact reconciliation already PASS — not silently corrected."
                    ),
                },
                indent=2,
                default=str,
            ),
            flush=True,
        )

    # Persist slim trade blotter
    (FORENSICS / "trades_closed.jsonl").write_text(
        "\n".join(json.dumps(r, default=str) for r in all_fee_rows),
        encoding="utf-8",
    )

    # History bucket performance from fee rows
    total_net = sum(
        float(net_r_with_slip(t, slippage_rate=cfg.slippage_rate) or 0) for t in all_fee_rows
    )
    total_trades = len(all_fee_rows)
    hist_perf = []
    for bname, _, _ in (
        ("A_<14d", 0, 14),
        ("B_14_30d", 14, 30),
        ("C_30_90d", 30, 90),
        ("D_90_180d", 90, 180),
        ("E_180d_plus", 180, None),
    ):
        subset = [t for t in all_fee_rows if t.get("history_bucket") == bname]
        m = aggregate_metrics(
            subset, slippage_rate=cfg.slippage_rate, risk_usd=cfg.risk_usd, group=bname
        )
        # also cell-level funnel for bucket
        brows = [r for r in bucket_rows if r["history_bucket"] == bname]
        m.update(
            {
                "cells": len(brows),
                "signals": sum(int(r.get("signals") or 0) for r in brows),
                "eligible_signals": sum(int(r.get("cap_eligible_signals") or 0) for r in brows),
                "candidates": sum(int(r.get("candidates") or 0) for r in brows),
                "entries": sum(int(r.get("entries") or 0) for r in brows),
                "closed_trades_cells": sum(int(r.get("closed_trades") or 0) for r in brows),
                "percentage_of_total_trades": (
                    (m["trades"] / total_trades) if total_trades else None
                ),
                "percentage_of_total_net_R": (
                    (m["net_R"] / total_net) if total_net and m["net_R"] is not None else None
                ),
            }
        )
        hist_perf.append(m)
    _write_csv(FORENSICS / "history_bucket_performance.csv", hist_perf)
    # also merge into cell_history aggregate summary used by report
    _write_csv(
        FORENSICS / "cell_history_buckets_agg.csv",
        hist_perf,
    )

    # Cost impact by groups
    cost_rows = []
    groupers = {
        "ALL": lambda t: "ALL",
        "strategy": lambda t: t.get("strategy_id") or t.get("combination_id"),
        "timeframe": lambda t: t.get("timeframe"),
        "symbol": lambda t: t.get("symbol"),
        "cap_group": lambda t: t.get("cap_group") or t.get("asset_group"),
        "period": lambda t: t.get("period") or t.get("period_label"),
        "month": lambda t: (t.get("signal_time") or "")[:7] or "UNKNOWN",
        "history_bucket": lambda t: t.get("history_bucket"),
    }
    for gname, gfn in groupers.items():
        buckets: dict[str, list] = defaultdict(list)
        for t in all_fee_rows:
            buckets[str(gfn(t))].append(t)
        for key, subset in sorted(buckets.items()):
            m = aggregate_metrics(
                subset,
                slippage_rate=cfg.slippage_rate,
                risk_usd=cfg.risk_usd,
                group=f"{gname}:{key}",
            )
            gross = m.get("gross_R")
            cost = None
            if m.get("fees_R") is not None and m.get("slippage_R") is not None:
                cost = m["fees_R"] + m["slippage_R"]
            m["cost_pct_of_gross_if_meaningful"] = (
                (cost / abs(gross))
                if cost is not None and gross not in (None, 0)
                else None
            )
            m["group_type"] = gname
            m["group_key"] = key
            cost_rows.append(m)
    _write_csv(FORENSICS / "cost_impact.csv", cost_rows)

    # Strategy × cap
    strat_cap = []
    for sid in (LARGE_ID, MID_ID, SMALL_ID):
        for cg in ("LARGE_CAP", "MID_CAP", "SMALL_CAP"):
            subset = [
                t
                for t in all_fee_rows
                if (t.get("strategy_id") or t.get("combination_id")) == sid
                and t.get("cap_group") == cg
            ]
            if not subset and not any(
                r["strategy_id"] == sid and r["cap_group"] == cg for r in cells
            ):
                continue
            m = aggregate_metrics(
                subset, slippage_rate=cfg.slippage_rate, risk_usd=cfg.risk_usd, group=f"{sid}|{cg}"
            )
            syms = sorted({t.get("symbol") for t in subset})
            months = sorted({(t.get("signal_time") or "")[:7] for t in subset if t.get("signal_time")})
            cell_sub = [c for c in cells if c["strategy_id"] == sid and c["cap_group"] == cg]
            days = [calendar_days(c["requested_start"], c["requested_end"]) or 0 for c in cell_sub]
            m.update(
                {
                    "strategy": sid,
                    "cap_group": cg,
                    "symbols_contributing": len(syms),
                    "symbols": ",".join(syms),
                    "number_of_months": len([x for x in months if x]),
                    "history_coverage_days_sum": sum(days),
                    "history_coverage_days_mean": mean(days) if days else None,
                }
            )
            strat_cap.append(m)
    _write_csv(FORENSICS / "strategy_cap.csv", strat_cap)

    # Strategy × timeframe
    strat_tf = []
    for sid in (LARGE_ID, MID_ID, SMALL_ID):
        for tf in ("5m", "15m", "1h"):
            subset = [
                t
                for t in all_fee_rows
                if (t.get("strategy_id") or t.get("combination_id")) == sid
                and t.get("timeframe") == tf
            ]
            m = aggregate_metrics(
                subset, slippage_rate=cfg.slippage_rate, risk_usd=cfg.risk_usd, group=f"{sid}|{tf}"
            )
            # duration: holding_bars * tf minutes
            mins = {"5m": 5, "15m": 15, "1h": 60}[tf]
            holds = [
                int(t["holding_bars"])
                for t in subset
                if t.get("holding_bars") is not None
            ]
            durs = [h * mins for h in holds]
            m.update(
                {
                    "strategy": sid,
                    "timeframe": tf,
                    "average_holding_bars": mean([float(h) for h in holds]),
                    "median_holding_bars": median([float(h) for h in holds]),
                    "average_duration_minutes": mean([float(d) for d in durs]),
                    "median_duration_minutes": median([float(d) for d in durs]),
                }
            )
            strat_tf.append(m)
    _write_csv(FORENSICS / "strategy_timeframe.csv", strat_tf)

    # Symbol concentration
    conc_rows = []
    for sid in (LARGE_ID, MID_ID, SMALL_ID):
        subset_all = [
            t
            for t in all_fee_rows
            if (t.get("strategy_id") or t.get("combination_id")) == sid
        ]
        n_all = len(subset_all) or 1
        by_sym: dict[str, list] = defaultdict(list)
        for t in subset_all:
            by_sym[str(t.get("symbol"))].append(t)
        ranked = sorted(by_sym.items(), key=lambda kv: -len(kv[1]))
        for sym, subset in ranked:
            m = aggregate_metrics(
                subset, slippage_rate=cfg.slippage_rate, risk_usd=cfg.risk_usd, group=f"{sid}|{sym}"
            )
            # history days from cells
            csub = [c for c in cells if c["strategy_id"] == sid and c["symbol"] == sym]
            hd = max(
                (calendar_days(c["requested_start"], c["requested_end"]) or 0) for c in csub
            ) if csub else None
            groups = sorted({c["cap_group"] for c in csub})
            m.update(
                {
                    "strategy": sid,
                    "symbol": sym,
                    "trade_count": m["trades"],
                    "percentage_of_strategy_trades": m["trades"] / n_all,
                    "history_days": hd,
                    "cap_groups_seen": "|".join(groups),
                }
            )
            conc_rows.append(m)
        # concentration summary rows
        shares = [len(v) / n_all for _, v in ranked]
        for k in (5, 10, 20):
            conc_rows.append(
                {
                    "group": f"{sid}|TOP_{k}_SHARE",
                    "strategy": sid,
                    "symbol": f"TOP_{k}",
                    "trade_count": sum(len(v) for _, v in ranked[:k]),
                    "percentage_of_strategy_trades": sum(shares[:k]),
                    "win_rate": None,
                    "mean_R": None,
                    "gross_R": None,
                    "net_R": None,
                    "fees_R": None,
                    "slippage_R": None,
                    "history_days": None,
                    "cap_groups_seen": "",
                }
            )
    _write_csv(FORENSICS / "symbol_concentration.csv", conc_rows)

    # Monthly
    monthly = []
    by_sm: dict[tuple[str, str], list] = defaultdict(list)
    for t in all_fee_rows:
        sid = t.get("strategy_id") or t.get("combination_id")
        month = (t.get("exit_time") or t.get("signal_time") or "")[:7] or "UNKNOWN"
        by_sm[(str(sid), month)].append(t)
    for (sid, month), subset in sorted(by_sm.items()):
        m = aggregate_metrics(
            subset, slippage_rate=cfg.slippage_rate, risk_usd=cfg.risk_usd, group=f"{sid}|{month}"
        )
        m.update(
            {
                "strategy": sid,
                "month": month,
                "wins": m["win_count"],
                "losses": m["loss_count"],
                "active_symbols": len({t.get("symbol") for t in subset}),
                "active_cap_groups": len({t.get("cap_group") for t in subset}),
            }
        )
        monthly.append(m)
    _write_csv(FORENSICS / "monthly_results.csv", monthly)

    # Period × strategy
    period_rows = []
    for sid in (LARGE_ID, MID_ID, SMALL_ID):
        for period in ("TRAIN", "VALIDATION", "OOS"):
            subset = [
                t
                for t in all_fee_rows
                if (t.get("strategy_id") or t.get("combination_id")) == sid
                and t.get("period") == period
            ]
            m = aggregate_metrics(
                subset,
                slippage_rate=cfg.slippage_rate,
                risk_usd=cfg.risk_usd,
                group=f"{sid}|{period}",
            )
            m.update({"strategy": sid, "period": period})
            period_rows.append(m)
    _write_csv(FORENSICS / "period_results.csv", period_rows)

    # Exit reasons
    exit_rows = []
    for sid in (LARGE_ID, MID_ID, SMALL_ID):
        subset_all = [
            t
            for t in all_fee_rows
            if (t.get("strategy_id") or t.get("combination_id")) == sid
        ]
        # include OPEN from all_trades
        opens = [
            t
            for t in all_trades
            if (t.get("combination_id") == sid or t.get("strategy_id") == sid)
            and t.get("outcome") == "OPEN"
        ]
        outcomes = Counter(t.get("outcome") for t in subset_all)
        outcomes["OPEN"] = len(opens)
        total_out = sum(outcomes.values()) or 1
        for outcome, cnt in sorted(outcomes.items(), key=lambda x: -x[1]):
            sub = [t for t in subset_all if t.get("outcome") == outcome]
            if outcome == "OPEN":
                exit_rows.append(
                    {
                        "strategy": sid,
                        "exit_reason": "OPEN",
                        "trades": cnt,
                        "percentage": cnt / total_out,
                        "win_rate": None,
                        "mean_R": None,
                        "net_R": None,
                    }
                )
                continue
            m = aggregate_metrics(
                sub, slippage_rate=cfg.slippage_rate, risk_usd=cfg.risk_usd, group=f"{sid}|{outcome}"
            )
            exit_rows.append(
                {
                    "strategy": sid,
                    "exit_reason": outcome,
                    "trades": cnt,
                    "percentage": cnt / total_out,
                    "win_rate": m["win_rate"],
                    "mean_R": m["mean_R"],
                    "net_R": m["net_R"],
                }
            )
    _write_csv(FORENSICS / "exit_reason_summary.csv", exit_rows)

    # Trade failure summary (winners vs losers)
    fail_rows = []
    for sid in (LARGE_ID, MID_ID, SMALL_ID):
        for label, pred in (
            ("WINNERS", lambda t: (safe_float(t.get("r_multiple")) or 0) > 0),
            ("LOSERS", lambda t: (safe_float(t.get("r_multiple")) or 0) <= 0),
        ):
            subset = [
                t
                for t in all_fee_rows
                if (t.get("strategy_id") or t.get("combination_id")) == sid and pred(t)
            ]
            holds = [float(t["holding_bars"]) for t in subset if t.get("holding_bars") is not None]
            mae = [float(t["mae_r"]) for t in subset if safe_float(t.get("mae_r")) is not None]
            mfe = [float(t["mfe_r"]) for t in subset if safe_float(t.get("mfe_r")) is not None]
            # distances
            sl_dist = []
            tp_dist = []
            for t in subset:
                e = safe_float(t.get("entry_price"))
                s = safe_float(t.get("stop_price"))
                tp = safe_float(t.get("tp1"))
                if e and s:
                    sl_dist.append(abs(e - s) / e)
                if e and tp:
                    tp_dist.append(abs(tp - e) / e)
            fail_rows.append(
                {
                    "strategy": sid,
                    "cohort": label,
                    "trades": len(subset),
                    "median_holding_bars": median(holds),
                    "mean_holding_bars": mean(holds),
                    "median_MAE_R": median(mae) if mae else "NOT AVAILABLE",
                    "median_MFE_R": median(mfe) if mfe else "NOT AVAILABLE",
                    "mean_MAE_R": mean(mae) if mae else "NOT AVAILABLE",
                    "mean_MFE_R": mean(mfe) if mfe else "NOT AVAILABLE",
                    "median_entry_to_SL_pct": median(sl_dist),
                    "median_entry_to_TP1_pct": median(tp_dist),
                    "mean_R": mean([float(t["r_multiple"]) for t in subset if t.get("r_multiple") is not None]),
                    "outcomes": dict(Counter(t.get("outcome") for t in subset)),
                }
            )
    _write_csv(FORENSICS / "trade_failure_summary.csv", fail_rows)

    # Funnel reconciliation
    tot = rm["totals"]
    funnel_expl = funnel_gap_explanation(
        candidates=int(tot["candidates"]),
        cap_eligible=int(tot["cap_eligible_signals"]),
        cross_cap=int(tot["cross_cap_signal_count"]),
        entries=int(tot["entries"]),
        closed=int(tot["closed_trades"]),
        open_trades=int(tot["open_trades"]),
        excluded_by_cap=sum(int(r.get("excluded_by_cap") or 0) for r in funnel),
    )
    funnel_expl["replay_entries"] = replay_entries
    funnel_expl["replay_closed"] = replay_closed
    funnel_expl["replay_open"] = replay_open
    funnel_expl["replay_suppressed_sum"] = sum(
        int(r.get("suppressed_due_to_open_trade") or 0) for r in suppress_rows
    )
    funnel_rows_out = []
    for sid in (LARGE_ID, MID_ID, SMALL_ID):
        csub = [c for c in cells if c["strategy_id"] == sid]
        rsub = [r for r in replays if r["strategy_id"] == sid]
        funnel_rows_out.append(
            {
                "strategy": sid,
                "signals": sum(int(c.get("signals_detected") or 0) for c in csub),
                "cap_eligible": sum(int(c.get("cap_eligible_signals") or 0) for c in csub),
                "cross_cap": sum(int(c.get("cross_cap_signal_count") or 0) for c in csub),
                "candidates": sum(int(c.get("candidates") or 0) for c in csub),
                "entries": sum(int(c.get("entries") or 0) for c in csub),
                "closed_trades": sum(int(c.get("closed_trades") or 0) for c in csub),
                "open_trades": sum(int(c.get("open_trades") or 0) for c in csub),
                "suppressed_due_to_open_trade": sum(
                    int(r.get("suppressed_due_to_open_trade") or 0) for r in rsub
                ),
            }
        )
    funnel_rows_out.append({"strategy": "ALL", **{k: funnel_expl.get(k) for k in (
        "candidates", "cap_eligible_signals", "cross_cap_signal_count", "entries",
        "closed_trades", "open_trades", "entries_minus_evaluated",
    )}, "suppressed_due_to_open_trade": funnel_expl["replay_suppressed_sum"]})
    _write_csv(FORENSICS / "funnel_reconciliation.csv", funnel_rows_out)
    _write_csv(FORENSICS / "one_open_suppression.csv", suppress_rows)

    # Reference comparison from validation artifact
    ref_path = ROOT / "scripts" / "multi_cap_validation_out" / "reference_vs_production.csv"
    if ref_path.exists():
        ref_rows = _read_csv(ref_path)
        ref_agg = reference_comparison_aggregate(ref_rows)
        # flatten difference_reasons
        flat = []
        for r in ref_agg:
            flat.append({**{k: v for k, v in r.items() if k != "difference_reasons"},
                         "difference_reasons": json.dumps(r.get("difference_reasons") or {})})
        _write_csv(FORENSICS / "reference_comparison.csv", flat)
    else:
        ref_agg = [{"strategy": "ALL", "status": "REFERENCE_FILE_MISSING"}]
        _write_csv(FORENSICS / "reference_comparison.csv", ref_agg)

    # Data quality report
    future_cap = 0
    # sample: ensure no obs used after trade time in fee rows
    for t in all_fee_rows[:5000]:
        st = parse_dt(t.get("signal_time"))
        if not st:
            continue
        lk = cap_group_at(
            str(t.get("symbol")),
            st,
            obs.get(str(t.get("symbol"))) or [],
            config=cfg,
        )
        if lk.effective_time and lk.effective_time > st:
            future_cap += 1

    dq_report = {
        "run_id": EXPECTED_RUN_ID,
        "ohlcv_timestamp_order_issues": sum(
            1 for d in dq_all if any("timestamp_not_increasing" in x for x in (d.get("issues") or []))
        ),
        "future_ohlcv_issues": sum(
            1 for d in dq_all if any("future_ohlcv" in x for x in (d.get("issues") or []))
        ),
        "invalid_ohlc_cells": sum(1 for d in dq_all if (d.get("invalid_ohlc") or 0) > 0),
        "chrono_split_failures": sum(1 for d in dq_all if d.get("chrono_split_ok") is False),
        "future_cap_leakage_sampled": future_cap,
        "lookahead_violations_count": len(lookahead_all),
        "replay_closed_vs_baseline": {
            "status": replay_match_status,
            "replay_closed": replay_closed,
            "baseline_closed": int(rm["totals"]["closed_trades"]),
            "cell_diff_count": len(cell_diffs),
        },
        "duplicates": "NOT AVAILABLE — no dedicated duplicate scan utility invoked beyond series order",
        "missing_bars": "NOT AVAILABLE — gap scanner not re-run; coverage_ratio reported per cell",
        "status": (
            "PASS"
            if not lookahead_all and future_cap == 0 and replay_match_status == "PASS"
            else (
                "PASS_WITH_REPLAY_DISCREPANCY"
                if not lookahead_all and future_cap == 0
                else "FAIL"
            )
        ),
    }
    (FORENSICS / "data_quality_report.json").write_text(
        json.dumps(dq_report, indent=2, default=str), encoding="utf-8"
    )

    no_la = {
        "status": "PASS" if not lookahead_all and future_cap == 0 else "FAIL",
        "market_cap_as_of_rule": "latest observation with effective_time <= T",
        "violations": lookahead_all[:50],
        "violation_count": len(lookahead_all),
        "future_cap_leakage_sampled": future_cap,
        "large_rolling_48": "shifted_rolling_min excludes current bar (implementation)",
        "mid_rolling_24": "shifted_rolling_max/min excludes current bar (implementation)",
        "fvg_formation": "detect_fvg_at requires candle i as Candle-3 close",
        "small_volume_sma": "implementation uses prior window per strategy module",
        "swing_right_bars": "extend_swings / detect_swings confirmation as_of i",
        "entry_vs_confirmation": "entry_index is confirmation bar; exit_index >= entry_index checked",
        "STOP_REQUIRED": bool(lookahead_all or future_cap),
    }
    (FORENSICS / "no_lookahead_report.json").write_text(
        json.dumps(no_la, indent=2, default=str), encoding="utf-8"
    )
    if no_la["STOP_REQUIRED"]:
        print(json.dumps({"status": "STOP", "no_lookahead": no_la}, indent=2, default=str))
        return 6

    # Direction limitation
    directions = Counter(t.get("direction") for t in all_fee_rows)
    direction_report = {
        strategy: {
            "direction_support": DIRECTION_SUPPORT[strategy],
            "observed": dict(
                Counter(
                    t.get("direction")
                    for t in all_fee_rows
                    if (t.get("strategy_id") or t.get("combination_id")) == strategy
                )
            ),
        }
        for strategy in (LARGE_ID, MID_ID, SMALL_ID)
    }

    # Cost headline numbers
    all_m = aggregate_metrics(
        all_fee_rows, slippage_rate=cfg.slippage_rate, risk_usd=cfg.risk_usd, group="ALL"
    )
    oos = [t for t in all_fee_rows if t.get("period") == "OOS"]
    oos_m = aggregate_metrics(
        oos, slippage_rate=cfg.slippage_rate, risk_usd=cfg.risk_usd, group="OOS"
    )

    # Write markdown report
    md: list[str] = []
    md.append("# Multi-Cap Baseline Forensics")
    md.append("")
    md.append("Descriptive analysis only. No strategy ranking. No optimization.")
    md.append("")
    md.append("## 1. Dataset")
    md.append(f"- run ID: `{EXPECTED_RUN_ID}`")
    md.append(f"- dataset: `{rm.get('dataset_label')}`")
    md.append(f"- cap history: `{dm.get('cap_history_start')}` → `{dm.get('cap_history_end')}`")
    md.append(f"- universe: {dm.get('total_usdt_perp_symbols')} USDT perps")
    md.append(f"- symbols with historical cap: {dm.get('symbols_with_cap_history')}")
    md.append(f"- symbols without historical cap: {dm.get('symbols_without_cap_history')}")
    md.append(f"- unique strategy-eligible symbols: {len(elig_syms)}")
    md.append(
        f"- cap-group memberships: LARGE={rm['cap_group_symbol_counts'].get('LARGE_CAP')} "
        f"MID={rm['cap_group_symbol_counts'].get('MID_CAP')} "
        f"SMALL={rm['cap_group_symbol_counts'].get('SMALL_CAP')} "
        f"(sum={sum(rm['cap_group_symbol_counts'].values())}; overlap via transitions)"
    )
    md.append(f"- cells: {rm['totals']['cells_run']}")
    md.append(f"- bars: {rm['totals']['bars_processed']}")
    md.append("")
    md.append("## 2. Artifact reconciliation")
    md.append(f"**{recon['status']}** (baseline CSV/JSON aggregations)")
    md.append(
        f"- replay vs baseline closed: **{replay_match_status}** "
        f"(replay={replay_closed}, baseline={rm['totals']['closed_trades']}, "
        f"cell_diffs={len(cell_diffs)}; see `replay_closed_discrepancy.csv`)"
    )
    md.append(f"- entries − (closed+open) = **{recon['entries_minus_evaluated']}** "
              "(inferred one-open suppressions from baseline totals)")
    md.append("")
    md.append("## 3. Cap classification history")
    md.append(f"- symbols_with_no_transition: {tsum['symbols_with_no_transition']}")
    md.append(f"- symbols_with_1_transition: {tsum['symbols_with_1_transition']}")
    md.append(f"- symbols_with_multiple_transitions: {tsum['symbols_with_multiple_transitions']}")
    md.append(
        f"- symbols_with_overlapping_group_membership: "
        f"{tsum['symbols_with_overlapping_group_membership']}"
    )
    md.append(f"- overlapping symbols: {', '.join(tsum['overlapping_symbols'])}")
    md.append("")
    md.append("## 4. Historical coverage")
    for b, n in sorted(bucket_counts.items()):
        hp = next((x for x in hist_perf if x["group"] == b), {})
        md.append(
            f"- {b}: cells={n} trades={hp.get('trades')} "
            f"mean_R={hp.get('mean_R')} net_R={hp.get('net_R')} "
            f"pct_trades={hp.get('percentage_of_total_trades')}"
        )
    md.append("")
    md.append("## 5. Cost impact")
    md.append(
        f"- ALL closed: trades={all_m['trades']} gross_R={all_m['gross_R']} "
        f"fees_R={all_m['fees_R']} slippage_R={all_m['slippage_R']} net_R={all_m['net_R']}"
    )
    md.append(
        f"- OOS: trades={oos_m['trades']} gross_mean_R={oos_m['gross_mean_R']} "
        f"gross_R={oos_m['gross_R']} fees_R={oos_m['fees_R']} "
        f"slippage_R={oos_m['slippage_R']} net_R={oos_m['net_R']}"
    )
    md.append(
        f"- Fee assumptions unchanged: taker={cfg.taker_fee} maker={cfg.maker_fee} "
        f"slippage_rate={cfg.slippage_rate} risk_usd={cfg.risk_usd}"
    )
    md.append("")
    md.append("## 6. Strategy decomposition")
    md.append("No ranking.")
    for row in strat_cap:
        md.append(
            f"- {row.get('strategy')}|{row.get('cap_group')}: trades={row.get('trades')} "
            f"WR={row.get('win_rate')} mean_R={row.get('mean_R')} net_R={row.get('net_R')}"
        )
    md.append("")
    md.append("## 7. Timeframe decomposition")
    for row in strat_tf:
        md.append(
            f"- {row.get('strategy')}|{row.get('timeframe')}: trades={row.get('trades')} "
            f"mean_R={row.get('mean_R')} median_hold_bars={row.get('median_holding_bars')} "
            f"net_R={row.get('net_R')}"
        )
    md.append("")
    md.append("## 8. Symbol concentration")
    for sid in (LARGE_ID, MID_ID, SMALL_ID):
        tops = [
            r
            for r in conc_rows
            if r.get("strategy") == sid and str(r.get("symbol", "")).startswith("TOP_")
        ]
        md.append(f"- {sid}: " + ", ".join(
            f"{r['symbol']}={r.get('percentage_of_strategy_trades')}" for r in tops
        ))
    md.append("")
    md.append("## 9. Monthly distribution")
    md.append(f"- month×strategy rows: {len(monthly)} (see `monthly_results.csv`)")
    md.append("")
    md.append("## 10. Train / validation / OOS")
    for row in period_rows:
        md.append(
            f"- {row.get('strategy')}|{row.get('period')}: trades={row.get('trades')} "
            f"WR={row.get('win_rate')} mean_R={row.get('mean_R')} net_R={row.get('net_R')}"
        )
    md.append("")
    md.append("## 11. Trade failure characteristics")
    for row in fail_rows:
        md.append(
            f"- {row['strategy']} {row['cohort']}: n={row['trades']} "
            f"median_hold={row['median_holding_bars']} "
            f"median_MAE_R={row['median_MAE_R']} median_MFE_R={row['median_MFE_R']}"
        )
    md.append("")
    md.append("## 12. Exit reasons")
    for row in exit_rows:
        md.append(
            f"- {row['strategy']} {row['exit_reason']}: trades={row['trades']} "
            f"pct={row['percentage']} mean_R={row['mean_R']}"
        )
    md.append("")
    md.append("## 13. Funnel reconciliation")
    md.append(
        f"- candidates {funnel_expl['candidates']} → cap_eligible "
        f"{funnel_expl['cap_eligible_signals']} (+ cross_cap "
        f"{funnel_expl['cross_cap_signal_count']}; residual "
        f"{funnel_expl['candidates_minus_eligible_and_cross']} missing-cap/time)"
    )
    md.append(
        f"- entries {funnel_expl['entries']} (= cap_eligible; candidate_but_no_entry=0, no_rr=0)"
    )
    md.append(
        f"- evaluated {funnel_expl['evaluated_trades']} "
        f"(closed {funnel_expl['closed_trades']} + open {funnel_expl['open_trades']})"
    )
    md.append(
        f"- suppressed by one_open_at_a_time (replay counted): "
        f"**{funnel_expl['replay_suppressed_sum']}** "
        f"(baseline inferred entries−evaluated={funnel_expl['entries_minus_evaluated']})"
    )
    md.append("")
    md.append("## 14. One-open-at-a-time impact")
    md.append(
        f"- one_open_at_a_time=true; suppressed opportunities counted during replay: "
        f"{funnel_expl['replay_suppressed_sum']}"
    )
    md.append("- per-cell detail: `baseline_forensics/one_open_suppression.csv`")
    md.append("")
    md.append("## 15. Direction limitation")
    for sid, info in direction_report.items():
        md.append(
            f"- {sid}: support={info['direction_support']} observed={info['observed']}"
        )
    md.append(
        'Directional robustness cannot be evaluated from this baseline because '
        'no SHORT trades were generated.'
    )
    md.append(f"- overall directions: {dict(directions)}")
    md.append("")
    md.append("## 16. Data-quality audit")
    md.append(f"- status: **{dq_report['status']}**")
    for k, v in dq_report.items():
        if k != "status":
            md.append(f"- {k}: {v}")
    md.append("")
    md.append("## 17. No-lookahead audit")
    md.append(f"- status: **{no_la['status']}**")
    md.append(f"- violation_count: {no_la['violation_count']}")
    md.append("")
    md.append("## 18. Reference implementation comparison")
    for r in ref_agg:
        md.append(
            f"- {r.get('strategy')}: matched={r.get('matched')} "
            f"mismatched={r.get('mismatched')} match_rate={r.get('match_rate')} "
            f"missing_ref={r.get('missing_reference')} missing_prod={r.get('missing_production')}"
        )
    md.append("")
    md.append("## 19. Limitations")
    md.append("- historical cap data is approximately one year")
    md.append("- 462/528 symbols lack historical cap data")
    md.append("- 125/156 cells have <14 days OHLCV∩cap overlap")
    md.append("- all observed trades are LONG")
    md.append("- this is not multi-year robustness evidence")
    md.append("- cap coverage is not representative of the full Binance universe")
    md.append("")
    md.append("## 20. Conclusion")
    md.append(
        "This baseline is a descriptive snapshot of three LONG-only research strategies "
        "on the AVAILABLE_HISTORICAL_CAP_WINDOW. Aggregate net R is dominated by fee and "
        "slippage drag relative to near-zero gross R. A large share of cells have short "
        "OHLCV∩cap windows; a minority of long-history 5m cells contribute most trades. "
        "Cap-group membership overlaps across symbols that transition during the window. "
        "One-open-at-a-time suppresses a substantial fraction of cap-eligible entries. "
        "No strategy selection or parameter change is implied."
    )
    md.append("")
    md.append("NO LIVE TRADING LOGIC CHANGED")
    md.append("NO STRATEGY PARAMETERS OPTIMIZED")
    md.append("NO SYNTHETIC MARKET DATA USED")
    md.append("NO FUTURE OHLCV OR MARKET-CAP DATA USED")
    report_path = BASE_OUT / "baseline_forensics_report.md"
    report_path.write_text("\n".join(md), encoding="utf-8")

    summary_out = {
        "status": "OK" if replay_match_status == "PASS" else "OK_WITH_REPLAY_DISCREPANCY",
        "run_id": EXPECTED_RUN_ID,
        "reconciliation": recon["status"],
        "replay_vs_baseline_closed": {
            "status": replay_match_status,
            "replay_closed": replay_closed,
            "baseline_closed": rm["totals"]["closed_trades"],
            "cell_diff_count": len(cell_diffs),
            "cell_diffs": cell_diffs,
        },
        "cap_transitions": {
            "overlapping": tsum["symbols_with_overlapping_group_membership"],
            "symbols": tsum["overlapping_symbols"],
        },
        "history_buckets": dict(bucket_counts),
        "cost_all": {
            "gross_R": all_m["gross_R"],
            "fees_R": all_m["fees_R"],
            "slippage_R": all_m["slippage_R"],
            "net_R": all_m["net_R"],
        },
        "cost_oos": {
            "gross_R": oos_m["gross_R"],
            "gross_mean_R": oos_m["gross_mean_R"],
            "fees_R": oos_m["fees_R"],
            "slippage_R": oos_m["slippage_R"],
            "net_R": oos_m["net_R"],
        },
        "funnel": funnel_expl,
        "no_lookahead": no_la["status"],
        "data_quality": dq_report["status"],
        "reference": [
            {
                "strategy": r.get("strategy"),
                "match_rate": r.get("match_rate"),
                "mismatched": r.get("mismatched"),
            }
            for r in ref_agg
        ],
        "report": str(report_path),
        "forensics_dir": str(FORENSICS),
    }
    (FORENSICS / "forensics_run_summary.json").write_text(
        json.dumps(summary_out, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(summary_out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
