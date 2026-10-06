#!/usr/bin/env python3
"""Run COMBO_02 entry-rejection funnel diagnostic (observability only).

Does not change strategy gates. Writes funnel artifacts under reports/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-01-31")
    parser.add_argument("--direction", default="LONG", choices=["LONG", "SHORT", "ALL"])
    parser.add_argument("--run-id", default="combo02_entry_funnel_diagnostic")
    args = parser.parse_args()

    from app.config import get_settings
    from app.research.bos_combinations import get_combination
    from app.research.combination_backtest import run_combination_backtest
    from app.research.config import ResearchConfig
    from app.research.entry_diagnostics import format_funnel_markdown
    from app.research.market_structure.attach import attach_market_structure_to_row
    from app.research.service import _load_research_candles
    from app.services.database import db_manager
    from app.signals.config import SignalConfig

    settings = get_settings()
    await db_manager.connect(settings)

    combo = get_combination("COMBO_02")
    if combo is None:
        raise SystemExit("COMBO_02 not found")

    c1h, _, meta1 = await _load_research_candles(
        args.symbol,
        "1h",
        start_date=args.start,
        end_date=args.end,
        use_research_cache=True,
    )
    c4h, _, meta4 = await _load_research_candles(
        args.symbol,
        "4h",
        start_date=args.start,
        end_date=args.end,
        use_research_cache=True,
    )
    try:
        c15m, _, meta15 = await _load_research_candles(
            args.symbol,
            "15m",
            start_date=args.start,
            end_date=args.end,
            use_research_cache=True,
        )
    except Exception:  # noqa: BLE001
        c15m, meta15 = [], {"source": "unavailable"}

    direction = None if args.direction == "ALL" else args.direction
    out = run_combination_backtest(
        args.symbol,
        "1h",
        c1h,
        combo,
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        direction_filter=direction,
    )

    trades = list(out.get("trades") or [])
    result = out.get("result") or {}
    row = {
        "combination_id": "COMBO_02",
        "symbol": args.symbol,
        "timeframe": "1h",
        "direction": args.direction,
        "trades": trades,
        "sample_size": out.get("sample_size"),
        "period_start": result.get("period_start"),
        "period_end": result.get("period_end"),
        "bars_loaded": len(c1h),
        "entry_diagnostics": out.get("entry_diagnostics") or {},
        "configuration_fingerprint": out.get("configuration_hash"),
    }
    attached = attach_market_structure_to_row(
        row,
        setup_candles=c1h,
        candles_4h=c4h,
        candles_1h=c1h,
        candles_15m=c15m or None,
        strategy_runtime_seconds=float(out.get("elapsed_seconds") or 0),
        combination_id="COMBO_02",
        strategy_id="COMBO_02_V1",
        run_id=args.run_id,
        write_artifacts=True,
        analytics_enabled=True,
        enable_regime_filtering=False,
        research_only=True,
        closed_htf_policy=True,
    )
    ms = attached.get("market_structure") or {}
    funnel = ms.get("entry_funnel") or {}
    out_dir = Path("reports") / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "symbol": args.symbol,
        "start": args.start,
        "end": args.end,
        "direction_filter": args.direction,
        "bars_1h": len(c1h),
        "bars_4h": len(c4h),
        "bars_15m": len(c15m or []),
        "src_1h": meta1.get("source"),
        "src_4h": meta4.get("source"),
        "src_15m": meta15.get("source"),
        "trade_count": len(trades),
        "sample_size": out.get("sample_size"),
        "configuration_hash": out.get("configuration_hash"),
        "entry_diagnostics_bars": len(out.get("entry_diagnostics") or {}),
        "artifact_paths": ms.get("artifact_paths"),
        "overall_funnel": funnel.get("overall_funnel"),
        "rejection_ranking": funnel.get("rejection_ranking"),
        "by_regime": funnel.get("by_regime"),
        "by_direction": funnel.get("by_direction"),
        "by_research_candidate": funnel.get("by_research_candidate"),
        "unknown_not_exported_count": funnel.get("unknown_not_exported_count"),
        "trades_fingerprint": [
            {
                "entry_index": t.get("entry_index"),
                "direction": t.get("direction"),
                "entry_price": t.get("entry_price"),
                "stop_price": t.get("stop_price"),
                "tp1": t.get("tp1"),
                "outcome": t.get("outcome"),
                "r_multiple": t.get("r_multiple"),
            }
            for t in trades
        ],
        "disclaimer": "Observability only — COMBO_02 trading behavior unchanged",
    }
    (out_dir / "diagnostic_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "entry_funnel.md").write_text(
        format_funnel_markdown(funnel), encoding="utf-8"
    )
    print(format_funnel_markdown(funnel))
    print(json.dumps({
        "run_id": args.run_id,
        "bars_1h": len(c1h),
        "trade_count": len(trades),
        "unknown_not_exported_count": funnel.get("unknown_not_exported_count"),
        "top_rejections": (funnel.get("rejection_ranking") or [])[:10],
        "artifact_dir": str(out_dir / "market_structure"),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
