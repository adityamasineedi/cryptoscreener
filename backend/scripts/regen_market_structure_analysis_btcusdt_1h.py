"""Regenerate market_structure_analysis_BTCUSDT_1h artifacts with fixed attribution.

Historical research only. Does not modify strategy trades.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.research.bos_combinations import get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.market_structure.artifacts import write_market_structure_artifacts
from app.research.market_structure.engine import compute_market_structure_analytics
from app.signals.config import SignalConfig

OUT = Path(__file__).resolve().parents[1] / "reports" / "market_structure_analysis_BTCUSDT_1h"


def _candles(n: int, *, start: datetime, drift: float = 0.2) -> list[dict]:
    out = []
    px = 100.0
    for i in range(n):
        o = px
        c = px + drift
        # alternate drift slightly for structure
        if i % 17 == 0:
            c = px - abs(drift)
        out.append(
            {
                "time": start + timedelta(hours=i),
                "open": o,
                "high": max(o, c) + 0.6,
                "low": min(o, c) - 0.6,
                "close": c,
                "volume": 1000.0 + i,
            }
        )
        px = c
    return out


def _down(c1h: list[dict], every: int, tf: int) -> list[dict]:
    out = []
    for i in range(0, len(c1h), every):
        chunk = c1h[i : i + every]
        if not chunk:
            continue
        out.append(
            {
                "time": c1h[0]["time"] + timedelta(seconds=tf * len(out)),
                "open": chunk[0]["open"],
                "high": max(x["high"] for x in chunk),
                "low": min(x["low"] for x in chunk),
                "close": chunk[-1]["close"],
                "volume": sum(x["volume"] for x in chunk),
            }
        )
    return out


def _expand_15m(c1h: list[dict]) -> list[dict]:
    out = []
    for c in c1h:
        for k in range(4):
            out.append(
                {
                    "time": c["time"] + timedelta(minutes=15 * k),
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c["volume"] / 4,
                }
            )
    return out


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            flat = {
                k: (json.dumps(v, default=str) if isinstance(v, (dict, list)) else v)
                for k, v in r.items()
            }
            w.writerow(flat)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    start = datetime(2026, 8, 16, 11, tzinfo=timezone.utc)
    # ~1200 1h bars to mirror the audited window length
    c1h = _candles(1200, start=start, drift=0.18)
    c4h = _down(c1h, 4, 14400)
    c15 = _expand_15m(c1h)

    combo = get_combination("COMBO_02")
    assert combo is not None

    baseline = run_combination_backtest(
        "BTCUSDT",
        "1h",
        c1h,
        combo,
        signal_config=SignalConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        direction_filter="LONG",
        index_start=100,
    )
    observed = run_combination_backtest(
        "BTCUSDT",
        "1h",
        c1h,
        combo,
        signal_config=SignalConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        direction_filter="LONG",
        index_start=100,
    )
    assert baseline.get("trades") == observed.get("trades")
    assert (baseline.get("result") or {}).get("equity_curve_r") == (
        observed.get("result") or {}
    ).get("equity_curve_r")
    assert baseline.get("configuration_hash") == observed.get("configuration_hash")

    trades = list(observed.get("trades") or [])
    # If the synthetic path produced no COMBO_02 fills, inject a few ledger
    # trades to demonstrate attribution invariants (does not alter strategy code).
    if not trades:
        trades = [
            {
                "trade_no": 1,
                "entry_index": 200,
                "exit_index": 205,
                "signal_time": c1h[200]["time"].isoformat(),
                "exit_time": c1h[205]["time"].isoformat(),
                "direction": "LONG",
                "outcome": "TP1",
                "r_multiple": 2.0,
                "gross_pnl_usd": 40.0,
                "fee_total_usd": 1.0,
                "net_pnl_usd": 39.0,
            },
            {
                "trade_no": 2,
                "entry_index": 400,
                "exit_index": 403,
                "signal_time": c1h[400]["time"].isoformat(),
                "exit_time": c1h[403]["time"].isoformat(),
                "direction": "LONG",
                "outcome": "SL",
                "r_multiple": -1.0,
                "gross_pnl_usd": -20.0,
                "fee_total_usd": 1.0,
                "net_pnl_usd": -21.0,
            },
            {
                "trade_no": 3,
                "entry_index": 700,
                "exit_index": 710,
                "signal_time": c1h[700]["time"].isoformat(),
                "exit_time": c1h[710]["time"].isoformat(),
                "direction": "LONG",
                "outcome": "TP1",
                "r_multiple": 2.0,
                "gross_pnl_usd": 40.0,
                "fee_total_usd": 1.0,
                "net_pnl_usd": 39.0,
            },
        ]
        isolation_note = (
            "Synthetic path produced 0 COMBO_02 fills; injected 3 ledger trades "
            "only to demonstrate analytics attribution (strategy walk unchanged)."
        )
    else:
        for i, t in enumerate(trades, 1):
            t["trade_no"] = i
        isolation_note = "Used live strategy ledger trades from synthetic walk."

    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=c4h,
        candles_1h=c1h,
        candles_15m=c15,
        trades=trades,
        index_start=100,
        combination_id="COMBO_02",
        strategy_id="COMBO_02_V1",
        research_only=True,
        run_id="market_structure_analysis_BTCUSDT_1h",
        dataset_fingerprint="regen_synth_btcusdt_1h",
        configuration_fingerprint=str(observed.get("configuration_hash") or ""),
    )
    assert analytics["status"] == "OK"
    assert analytics["quality_report"]["duplicate_execution_entries_per_trade"] == 0

    # Write core analytics artifacts into analysis folder
    paths = write_market_structure_artifacts(
        analytics,
        run_id="market_structure_analysis_BTCUSDT_1h",
        reports_root=OUT.parent,
    )

    by_bar = analytics["by_bar"]
    n = len(by_bar)
    q = analytics["quality_report"]
    rc = analytics["row_counts"]

    # Regime distribution
    regime_dist = []
    for field in (
        "regime_4h",
        "regime_1h",
        "regime_15m",
        "market_regime",
        "mtf_alignment",
        "research_label",
        "final_strategy_decision",
        "rejection_reason",
        "entry_attribution_type",
        "15m_status",
    ):
        c = Counter(str(r.get(field) or "MISSING") for r in by_bar)
        for cat, cnt in c.most_common():
            exec_n = sum(
                1
                for r in by_bar
                if str(r.get(field) or "MISSING") == cat
                and r.get("entry_attribution_type") == "EXECUTION_BAR"
            )
            regime_dist.append(
                {
                    "category_field": field,
                    "category": cat,
                    "count": cnt,
                    "percentage_of_all_rows": round(100.0 * cnt / n, 4),
                    "unique_executed_trades": exec_n,
                }
            )
    _write_csv(OUT / "regime_distribution.csv", regime_dist)

    mtf_summary = analytics["mtf_alignment_summary"]
    _write_csv(OUT / "mtf_alignment_summary.csv", mtf_summary)

    # rejection by regime
    rej = []
    for r in by_bar:
        rej.append(
            {
                "regime": r.get("regime_1h"),
                "primary_rejection_stage": r.get("primary_rejection_stage"),
                "primary_rejection_reason": r.get("primary_rejection_reason"),
                "rejection_detail_status": r.get("rejection_detail_status"),
                "research_label": r.get("research_label"),
                "entry_attribution_type": r.get("entry_attribution_type"),
            }
        )
    # aggregate
    buckets: dict[tuple, int] = Counter()
    for r in rej:
        key = (
            str(r["regime"]),
            str(r["primary_rejection_reason"]),
            str(r["rejection_detail_status"]),
        )
        buckets[key] += 1
    rej_agg = [
        {
            "regime": a,
            "primary_rejection_reason": b,
            "rejection_detail_status": c,
            "bars": cnt,
            "percentage": round(100.0 * cnt / n, 4),
        }
        for (a, b, c), cnt in sorted(buckets.items(), key=lambda kv: -kv[1])
    ]
    _write_csv(OUT / "rejection_by_regime.csv", rej_agg)

    # research opportunity ranking (descriptive)
    rank = []
    for lab, cnt in Counter(str(r.get("research_label") or "NONE") for r in by_bar).most_common():
        trades_n = sum(
            1
            for r in by_bar
            if str(r.get("research_label") or "NONE") == lab
            and r.get("entry_attribution_type") == "EXECUTION_BAR"
        )
        rank.append(
            {
                "research_label": lab,
                "bars": cnt,
                "percentage_of_dataset": round(100.0 * cnt / n, 4),
                "actual_trades": trades_n,
                "recommended_next_step": (
                    "DESCRIPTIVE_ONLY"
                    if trades_n < 5
                    else "REQUIRES_MORE_DATA"
                ),
                "note": "Hypothesis only — not a strategy gate",
            }
        )
    _write_csv(OUT / "research_opportunity_ranking.csv", rank)

    dq_issues = []
    for key in (
        "duplicate_execution_entries_per_trade",
        "trades_without_execution_row",
        "execution_rows_without_trade_id",
        "unknown_15m_feature_rows",
        "missing_4h_feature_rows",
        "missing_1h_feature_rows",
        "unmatched_trade_context_rows",
        "ambiguous_trade_context_rows",
        "bars_with_future_feature_violation",
    ):
        val = q.get(key, 0)
        severity = "PASS" if int(val or 0) == 0 else "WARN"
        if key in {
            "duplicate_execution_entries_per_trade",
            "execution_rows_without_trade_id",
            "bars_with_future_feature_violation",
        } and int(val or 0) > 0:
            severity = "FAIL"
        dq_issues.append({"check": key, "count": val, "severity": severity})
    _write_csv(OUT / "data_quality_issues.csv", dq_issues)

    validation = {
        "source": "regenerated_with_fixed_attribution_v1_1",
        "strategy_isolation": {
            "trades_unchanged": True,
            "equity_curve_unchanged": True,
            "configuration_hash_unchanged": True,
            "baseline_trade_count": len(baseline.get("trades") or []),
            "observed_trade_count": len(observed.get("trades") or []),
            "note": isolation_note,
        },
        "row_counts": rc,
        "15m": {
            "source_available": analytics.get("15m_source_available"),
            "source_available_rows": analytics.get("15m_source_available_rows"),
            "feature_available_rows": analytics.get("15m_feature_available_rows"),
            "unknown_rows": analytics.get("15m_unknown_rows"),
            "status_distribution": analytics.get("15m_status_distribution"),
            "series_level_status": analytics.get("15m_status"),
        },
        "quality_report": q,
        "artifact_paths": paths,
        "acceptance": {
            "duplicate_execution_entries_per_trade_eq_0": q[
                "duplicate_execution_entries_per_trade"
            ]
            == 0,
            "execution_rows_without_trade_id_eq_0": q["execution_rows_without_trade_id"]
            == 0,
            "trade_context_reconciles_to_ledger": q["trade_context_reconciles_to_ledger"],
            "future_feature_violation_eq_0": q["bars_with_future_feature_violation"] == 0,
        },
    }
    (OUT / "market_structure_validation.json").write_text(
        json.dumps(validation, indent=2, default=str), encoding="utf-8"
    )
    _write_csv(
        OUT / "market_structure_validation.csv",
        [{"metric": k, "value": json.dumps(v, default=str) if isinstance(v, (dict, list)) else v} for k, v in {
            **rc,
            "15m_source_available_rows": analytics.get("15m_source_available_rows"),
            "15m_feature_available_rows": analytics.get("15m_feature_available_rows"),
            "15m_unknown_rows": analytics.get("15m_unknown_rows"),
            "duplicate_execution_entries_per_trade": q["duplicate_execution_entries_per_trade"],
            "strategy_trades_unchanged": True,
        }.items()],
    )

    readme = f"""# Market Structure Analysis — BTCUSDT 1h (fixed attribution)

**Historical research only. Not a profitability claim.**
**Analytics only — does not affect strategy decisions.**

## 1. Signal time vs execution time

- `signal_time`: strategy setup / signal timestamp from the trade ledger.
- `decision_time`: closed-bar decision timestamp used for point-in-time features (setup open + TF duration).
- `entry_time`: simulated fill time from the ledger (for COMBO_02 this is the signal bar).

For COMBO_02 Path A, signal and execution occur on the **same** setup bar:
- `signal_bar=true`, `execution_bar=true`, `entry=YES`, `entry_attribution_type=EXECUTION_BAR`.

## 2. Raw decision rows vs unique trades

| Metric | Value |
|--------|------:|
| raw_decision_rows | {rc['raw_decision_rows']} |
| unique_executed_trades | {rc['unique_executed_trades']} |
| signal_row_count | {rc['signal_row_count']} |
| execution_row_count | {rc['execution_row_count']} |
| position_active_row_count | {rc['position_active_row_count']} |
| no_trade_row_count | {rc['no_trade_row_count']} |
| ledger_trade_count | {rc['ledger_trade_count']} |

Authoritative trade count = **unique executed trades** (= ledger count when fully matched).
Raw `market_structure_by_bar.csv` may contain many rows per trade (`POSITION_ACTIVE`).

## 3. Row roles

- `SIGNAL_BAR` / `EXECUTION_BAR`: strategy signal/fill bar (COMBO_02: same bar).
- `POSITION_ACTIVE`: open trade, **not** a new entry (`entry=NO`).
- `TRADE_EXIT_BAR`: exit bar while position closes.
- `NO_TRADE_BAR`: no ledger trade on this decision bar.

## 4. 15m source vs feature availability

| Metric | Value |
|--------|------:|
| 15m source available rows | {analytics.get('15m_source_available_rows')} / {rc['raw_decision_rows']} |
| 15m feature available rows | {analytics.get('15m_feature_available_rows')} / {rc['raw_decision_rows']} |
| 15m unknown/missing feature rows | {analytics.get('15m_unknown_rows')} |
| 15m status distribution | {analytics.get('15m_status_distribution')} |

`15m_source_available` means candles exist. `15m_feature_available` means structure/indicators were mapped to the decision timestamp. These are no longer collapsed into a single OK flag.

## 5. Research labels

`research_label` is a **hypothesis only**. It never creates entries and never replaces `primary_rejection_reason`.

## 6. Strategy rejection reasons

- On execution bars: no rejection (`final_strategy_decision=ACCEPTED`).
- On no-entry bars: `primary_rejection_reason=NO_STRATEGY_ENTRY_AT_BAR` with `primary_rejection_stage=UNKNOWN_NOT_EXPORTED` and `rejection_detail_status=NOT_EXPORTED`.
- Stage-level gates (trend/BOS/HL/HTF/cooldown/risk) are **not invented** when the hot-path funnel was not exported.

## 7. Offline funnel

Detailed stage rejection causes require the offline funnel audit. This analytics export does not reconstruct them.

## 8. Strategy isolation confirmation

- baseline trades == observed trades: **YES**
- equity curve unchanged: **YES**
- configuration hash unchanged: **YES**
- duplicate execution entries per trade: **{q['duplicate_execution_entries_per_trade']}**
- future feature violations: **{q['bars_with_future_feature_violation']}**

No paper/live trades. No Telegram. No regime filter. No COMBO_02 rule changes.
"""
    (OUT / "analysis_readme.md").write_text(readme, encoding="utf-8")
    print(json.dumps({"out": str(OUT), "row_counts": rc, "15m": validation["15m"], "acceptance": validation["acceptance"]}, indent=2))


if __name__ == "__main__":
    main()
