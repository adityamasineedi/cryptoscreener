#!/usr/bin/env python3
"""Profile COMBO_02 BTCUSDT stages (analytics off then on) — no strategy changes."""

from __future__ import annotations

import asyncio
import cProfile
import io
import json
import pstats
import time
from pathlib import Path


async def _load(symbol: str, start: str, end: str):
    from app.config import get_settings
    from app.research.service import _load_research_candles
    from app.services.database import db_manager

    settings = get_settings()
    await db_manager.connect(settings)
    t0 = time.perf_counter()
    c1h, _, meta1 = await _load_research_candles(
        symbol, "1h", start_date=start, end_date=end, use_research_cache=True
    )
    c4h, _, meta4 = await _load_research_candles(
        symbol, "4h", start_date=start, end_date=end, use_research_cache=True
    )
    load_s = time.perf_counter() - t0
    return c1h, c4h, meta1, meta4, load_s


def _profile_run(c1h, c4h, analytics: bool) -> tuple[dict, str]:
    from app.research.bos_combinations import get_combination
    from app.research.combination_backtest import run_combination_backtest
    from app.research.config import ResearchConfig

    combo = get_combination("COMBO_02")
    assert combo is not None
    cfg = ResearchConfig()
    # analytics flag if supported; otherwise note unavailable
    kwargs = dict(
        symbol="BTCUSDT",
        timeframe="1h",
        candles=c1h,
        combination=combo,
        research_config=cfg,
        candles_4h=c4h,
    )
    # Do not invent analytics kwargs that change trade path — only pass if present.
    import inspect

    sig = inspect.signature(run_combination_backtest)
    if "include_analytics" in sig.parameters:
        kwargs["include_analytics"] = analytics
    elif "market_structure" in sig.parameters:
        kwargs["market_structure"] = analytics
    elif "attach_market_structure" in sig.parameters:
        kwargs["attach_market_structure"] = analytics

    pr = cProfile.Profile()
    t0 = time.perf_counter()
    pr.enable()
    out = run_combination_backtest(**kwargs)
    pr.disable()
    runtime = time.perf_counter() - t0

    buf = io.StringIO()
    stats = pstats.Stats(pr, stream=buf).sort_stats("cumulative")
    stats.print_stats(60)
    text = buf.getvalue()

    trades = out.get("trades") or []
    result = out.get("result") or {}
    summary = {
        "analytics": analytics,
        "runtime_s": round(runtime, 3),
        "bars": len(c1h),
        "trade_count": len(trades),
        "kwargs_used": {k: v for k, v in kwargs.items() if k not in ("candles", "candles_4h", "combination", "research_config")},
        "fingerprint": result.get("configuration_fingerprint") or out.get("configuration_fingerprint"),
        "top_functions_note": "see companion .txt profile",
    }
    return summary, text


async def main() -> None:
    out_dir = Path("reports/full_system_performance_followup")
    out_dir.mkdir(parents=True, exist_ok=True)

    c1h, c4h, meta1, meta4, load_s = await _load("BTCUSDT", "2025-07-01", "2026-09-30")
    print("loaded", len(c1h), len(c4h), "load_s", round(load_s, 3))

    off_sum, off_txt = _profile_run(c1h, c4h, analytics=False)
    on_sum, on_txt = _profile_run(c1h, c4h, analytics=True)

    (out_dir / "backtest_profile_analytics_off.txt").write_text(
        f"load_s={load_s}\nmeta1={meta1}\nmeta4={meta4}\n\n{off_txt}", encoding="utf-8"
    )
    (out_dir / "backtest_profile_analytics_on.txt").write_text(
        f"load_s={load_s}\n\n{on_txt}", encoding="utf-8"
    )
    meta = {
        "load_s": round(load_s, 3),
        "off": off_sum,
        "on": on_sum,
        "analytics_overhead_s": round(on_sum["runtime_s"] - off_sum["runtime_s"], 3),
        "trade_count_equal": off_sum["trade_count"] == on_sum["trade_count"],
    }
    (out_dir / "backtest_profile_summary.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    print(json.dumps(meta, indent=2))

    from app.services.database import db_manager

    await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
