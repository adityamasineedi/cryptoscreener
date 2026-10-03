"""Build historical market-cap coverage + controlled Multi-Cap matrix.

Research only. Does not modify live signals or strategy parameters.

Usage (from backend/):
  python scripts/cap_classification_coverage.py
  python scripts/cap_classification_coverage.py --max-fetch 80 --days 365
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.research.combination_backtest import evaluate_candidate_trades  # noqa: E402
from app.research.historical_market_cap.classifier import (  # noqa: E402
    CLASSIFICATION_RULE_VERSION,
    filter_candidates_by_historical_cap,
)
from app.research.historical_market_cap import repository as repo  # noqa: E402
from app.research.historical_market_cap.service import (  # noqa: E402
    build_coverage_report,
    build_eligibility_rows,
    ingest_historical_for_symbols,
    list_usdt_perp_symbols,
    ohlcv_span,
    select_controlled_universe,
)
from app.research.multi_cap_strategies.adapter import MultiCapStrategyAdapter  # noqa: E402
from app.research.multi_cap_strategies.common import candidate_to_research_trade  # noqa: E402
from app.research.multi_cap_strategies.config import MultiCapResearchConfig  # noqa: E402
from app.research.multi_cap_strategies.large_cap_sweep_choch import (  # noqa: E402
    STRATEGY_ID as LARGE_ID,
)
from app.research.multi_cap_strategies.large_cap_sweep_choch import (
    generate_candidates as gen_large,
)
from app.research.multi_cap_strategies.mid_cap_fvg_discount import (  # noqa: E402
    STRATEGY_ID as MID_ID,
)
from app.research.multi_cap_strategies.mid_cap_fvg_discount import (
    generate_candidates as gen_mid,
)
from app.research.multi_cap_strategies.small_cap_volume_bos import (  # noqa: E402
    STRATEGY_ID as SMALL_ID,
)
from app.research.multi_cap_strategies.small_cap_volume_bos import (
    generate_candidates as gen_small,
)
from app.research.postgres_ohlcv import load_ohlcv_series_tail  # noqa: E402
from app.research.config import split_period_indices  # noqa: E402
from app.services.database import db_manager  # noqa: E402

OUT_DIR = ROOT / "scripts" / "cap_classification_out"
TIMEFRAMES = ("5m", "15m", "1h")
LIMIT_BARS = 900
STRATEGY_MAP = {
    "LARGE_CAP": (LARGE_ID, gen_large),
    "MID_CAP": (MID_ID, gen_mid),
    "SMALL_CAP": (SMALL_ID, gen_small),
}


def _md_coverage(report: dict[str, Any], status: str, blockers: list[str]) -> str:
    src = report["source"]
    lines = [
        "# Historical Market-Cap Classification Coverage",
        "",
        f"**Status:** `{status}`",
        "",
        "## Source",
        f"- Provider: `{src['provider']}`",
        f"- Endpoint: `{src['endpoint']}`",
        f"- Units: {src['units']}",
        f"- Currency: `{src['currency']}`",
        f"- Rule version: `{src['classification_rule_version']}`",
        f"- Timestamp semantics: {src['timestamp_semantics']}",
        f"- Missing data: {src['missing_data_behavior']}",
        f"- Fallback: {src['fallback_behavior']}",
        f"- BTC/ETH: {src['btc_eth_taxonomy']}",
        "",
        "### Thresholds (unchanged)",
        f"- LARGE >= {src['thresholds']['large_cap_min']:,.0f}",
        f"- MID [{src['thresholds']['mid_cap_min']:,.0f}, {src['thresholds']['mid_cap_max']:,.0f})",
        f"- SMALL [{src['thresholds']['small_cap_min']:,.0f}, {src['thresholds']['small_cap_max']:,.0f})",
        "",
        "## Coverage",
        f"- Total Binance USDT-perp symbols: **{report['total_binance_usdt_perp_symbols']}**",
        f"- With historical cap observations: **{report['symbols_with_historical_cap']}**",
        f"- With no cap: **{report['symbols_with_no_cap']}**",
        f"- Partial history (<30d span): **{report['symbols_with_partial_history_lt_30d']}**",
        "",
        "### Historical span buckets",
    ]
    for k, v in report["historical_coverage_counts"].items():
        lines.append(f"- {k}: {v}")
    lines += ["", "### Cap-group counts (as-of)"]
    for k, v in report["cap_group_counts_as_of"].items():
        lines.append(f"- {k}: {v}")
    if blockers:
        lines += ["", "## Blockers"]
        for b in blockers:
            lines.append(f"- {b}")
    lines.append("")
    return "\n".join(lines)


async def _pick_fetch_targets(max_fetch: int) -> list[str]:
    """Deterministic: deepest 15m OHLCV first, then alphabetical fill."""
    symbols = await list_usdt_perp_symbols()
    scored: list[tuple[int, str]] = []
    for sym in symbols:
        oh = await ohlcv_span(sym, "15m")
        scored.append((int(oh["bars"]), sym))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [s for _, s in scored[:max_fetch]]


async def run_controlled_matrix(
    selected: dict[str, list[str]],
) -> dict[str, Any]:
    cfg = MultiCapResearchConfig()
    adapter = MultiCapStrategyAdapter(cfg)
    all_syms = sorted({s for v in selected.values() for s in v})
    obs = await repo.load_observations_many(all_syms)
    rows: list[dict[str, Any]] = []

    for group, (sid, gen_fn) in STRATEGY_MAP.items():
        symbols = selected.get(group) or []
        for sym in symbols:
            for tf in TIMEFRAMES:
                candles = await load_ohlcv_series_tail(sym, tf, limit=LIMIT_BARS)
                bars = len(candles)
                # Signal logic (cap-bypass diagnostics)
                cands = gen_fn(sym, tf, candles, config=cfg) if bars else []
                eligible, _cross, cap_stats = filter_candidates_by_historical_cap(
                    cands,
                    required_group=group,
                    observations_by_symbol=obs,
                    config=cfg,
                )
                open_trades = []
                for c in eligible:
                    rt = candidate_to_research_trade(
                        strategy_id=sid,
                        symbol=c.symbol,
                        timeframe=c.timeframe,
                        direction=c.direction,
                        entry_index=c.entry_index,
                        signal_time=c.signal_time,
                        entry_price=c.entry_price,
                        atr=float(c.metadata["atr"])
                        if c.metadata.get("atr") is not None
                        else None,
                        structural_invalidation=c.structural_invalidation,
                        asset_group=group,
                        condition_snapshot=dict(c.metadata),
                        config=cfg,
                    )
                    if rt is not None:
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
                # period labels
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
                closed = [t for t in evaluated if t.outcome and t.outcome != "OPEN"]
                open_n = sum(1 for t in evaluated if t.outcome == "OPEN")
                period = {
                    "TRAIN": sum(
                        1 for t in closed if t.period_label == "TRAINING_PERIOD"
                    ),
                    "VALIDATION": sum(
                        1 for t in closed if t.period_label == "VALIDATION_PERIOD"
                    ),
                    "OOS": sum(
                        1
                        for t in closed
                        if t.period_label == "OUT_OF_SAMPLE_PERIOD"
                    ),
                }
                excluded_by_entry = max(0, len(eligible) - len(open_trades))
                rows.append(
                    {
                        "strategy_id": sid,
                        "cap_group": group,
                        "symbol": sym,
                        "timeframe": tf,
                        "bars": bars,
                        "signals_detected": cap_stats["signals_detected"],
                        "cap_eligible_signals": cap_stats["cap_eligible_signals"],
                        "cross_cap_signal_count": cap_stats["cross_cap_signal_count"],
                        "candidates": len(eligible),
                        "entries": len(open_trades),
                        "closed_trades": len(closed),
                        "open_trades": open_n,
                        "LONG": sum(1 for t in closed if t.direction == "LONG"),
                        "SHORT": sum(1 for t in closed if t.direction == "SHORT"),
                        "TRAIN": period["TRAIN"],
                        "VALIDATION": period["VALIDATION"],
                        "OOS": period["OOS"],
                        "excluded_by_cap": cap_stats["excluded_by_cap"],
                        "excluded_by_data": 0 if bars >= cfg.min_bars else 1,
                        "excluded_by_entry": excluded_by_entry,
                        "excluded_by_risk": 0,
                        "excluded_by_other": 0,
                        "classification_rule_version": CLASSIFICATION_RULE_VERSION,
                        "note": (
                            "Official funnel uses historical cap_group_at(signal_time). "
                            "cross_cap_signal_count retained for detector diagnostics."
                        ),
                    }
                )
    return {
        "selected_universe": selected,
        "limit_bars": LIMIT_BARS,
        "timeframes": list(TIMEFRAMES),
        "rows": rows,
        "totals": {
            "signals_detected": sum(r["signals_detected"] for r in rows),
            "cap_eligible_signals": sum(r["cap_eligible_signals"] for r in rows),
            "cross_cap_signal_count": sum(r["cross_cap_signal_count"] for r in rows),
            "candidates": sum(r["candidates"] for r in rows),
            "closed_trades": sum(r["closed_trades"] for r in rows),
        },
        "NO_LIVE_TRADING_LOGIC_WAS_CHANGED": True,
        "no_optimization": True,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-fetch", type=int, default=60)
    parser.add_argument("--days", type=str, default="365")
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--per-group", type=int, default=3)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    await db_manager.connect(settings)
    if not db_manager.enabled or db_manager.engine is None:
        payload = {
            "status": "CAP_DATA_BLOCKED",
            "blockers": ["DATABASE unavailable"],
        }
        (OUT_DIR / "cap_coverage.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        print(json.dumps(payload, indent=2))
        return 2

    fetch_stats: dict[str, Any] = {"skipped": True}
    if not args.skip_fetch:
        targets = await _pick_fetch_targets(args.max_fetch)
        print(f"Fetching historical mcap for {len(targets)} symbols...", flush=True)
        fetch_stats = await ingest_historical_for_symbols(
            targets, days=args.days, max_symbols=args.max_fetch
        )
        print(json.dumps({"fetch": fetch_stats}, indent=2), flush=True)

    coverage = await build_coverage_report()
    eligibility = await build_eligibility_rows(timeframes=TIMEFRAMES)
    selected = select_controlled_universe(
        eligibility, per_group=args.per_group, timeframe="15m"
    )

    blockers: list[str] = []
    remaining: list[str] = []
    if coverage["symbols_with_historical_cap"] == 0:
        blockers.append("historical cap data is unavailable (0 symbols ingested)")
    for g in ("LARGE_CAP", "MID_CAP", "SMALL_CAP"):
        if len(selected.get(g) or []) < args.per_group:
            blockers.append(
                f"insufficient eligible {g} symbols for controlled test "
                f"(have {len(selected.get(g) or [])}, need {args.per_group})"
            )
    # sparsity: if <5% of universe has hist cap → hard block
    total = coverage["total_binance_usdt_perp_symbols"] or 1
    hist_pct = coverage["symbols_with_historical_cap"] / total
    if hist_pct < 0.05:
        blockers.append(
            f"cap history too sparse ({coverage['symbols_with_historical_cap']}/{total} = {hist_pct:.1%})"
        )
    # Remaining limitations (do not block controlled readiness, but gate full-universe)
    if coverage["symbols_with_no_cap"] > 0:
        remaining.append(
            f"{coverage['symbols_with_no_cap']}/{total} USDT-perp symbols still have "
            "no historical market-cap observations (continue CoinGecko ingest)"
        )
    if coverage["historical_coverage_counts"].get(">=1y", 0) == 0:
        remaining.append(
            "CoinGecko demo/free market_chart span is ~365d; >=1y/2y/3y/5y buckets "
            "remain empty without a paid plan or longer persisted history"
        )
    remaining.append(
        "BTC/ETH taxonomy labels never map to LARGE/MID/SMALL strategy groups "
        "(unchanged classify_asset_group behavior)"
    )

    matrix: dict[str, Any] | None = None
    if not blockers and all(len(selected[g]) >= args.per_group for g in STRATEGY_MAP):
        print("Running controlled strategy matrix...", flush=True)
        matrix = await run_controlled_matrix(selected)
    else:
        matrix = {
            "selected_universe": selected,
            "rows": [],
            "totals": {},
            "skipped_reason": blockers,
        }

    # READY = controlled population + temporal layer validated.
    # Full 528-symbol ingest is tracked under remaining_limitations.
    status = "CAP_DATA_READY_FOR_FULL_RESEARCH" if not blockers else "CAP_DATA_BLOCKED"

    # Write artifacts
    (OUT_DIR / "cap_coverage.json").write_text(
        json.dumps(
            {
                **coverage,
                "fetch_stats": fetch_stats,
                "status": status,
                "blockers": blockers,
                "remaining_limitations": remaining,
                "selected_universe": selected,
                "controlled_matrix_totals": (matrix or {}).get("totals"),
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    (OUT_DIR / "cap_coverage.md").write_text(
        _md_coverage(coverage, status, blockers + remaining), encoding="utf-8"
    )
    with (OUT_DIR / "cap_eligibility.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "symbol",
            "timeframe",
            "data_start",
            "data_end",
            "market_cap_start",
            "market_cap_end",
            "cap_group",
            "eligible",
            "exclusion_reason",
        ]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in eligibility:
            w.writerow(row)
    (OUT_DIR / "controlled_cap_matrix.json").write_text(
        json.dumps(matrix, indent=2, default=str), encoding="utf-8"
    )

    summary = {
        "status": status,
        "blockers": blockers,
        "remaining_limitations": remaining,
        "symbols_with_historical_cap": coverage["symbols_with_historical_cap"],
        "symbols_with_no_cap": coverage["symbols_with_no_cap"],
        "historical_coverage_counts": coverage["historical_coverage_counts"],
        "cap_group_counts_as_of": coverage["cap_group_counts_as_of"],
        "selected_universe": selected,
        "matrix_totals": (matrix or {}).get("totals"),
        "out_dir": str(OUT_DIR),
        "classification_rule_version": CLASSIFICATION_RULE_VERSION,
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0 if status == "CAP_DATA_READY_FOR_FULL_RESEARCH" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
