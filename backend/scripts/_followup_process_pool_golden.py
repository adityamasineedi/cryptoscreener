#!/usr/bin/env python3
"""Golden equality: in-process vs process-pool COMBO_02 (strategy unchanged)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

OUT = Path("reports/full_system_performance_followup")
START, END = "2025-07-01", "2026-09-30"
SYMBOL = "BTCUSDT"


def _digest(trades: list[dict]) -> str:
    h = hashlib.sha256()
    for t in trades:
        for k in (
            "signal_time",
            "entry_time",
            "exit_time",
            "entry_price",
            "exit_price",
            "stop_price",
            "tp_price",
            "exit_reason",
            "r_multiple",
            "gross_pnl",
            "net_pnl",
            "fees",
        ):
            h.update(str(t.get(k)).encode())
    return h.hexdigest()[:24]


def _extract(out: dict, dataset_fp: str) -> dict:
    trades = list(out.get("trades") or [])
    result = out.get("result") or {}
    return {
        "status": out.get("status"),
        "trade_count": len(trades),
        "trade_digest": _digest(trades),
        "entry_prices": [t.get("entry_price") for t in trades],
        "exit_prices": [t.get("exit_price") for t in trades],
        "entry_ts": [str(t.get("signal_time") or t.get("entry_time") or "") for t in trades],
        "exit_ts": [str(t.get("exit_time") or "") for t in trades],
        "sl": [t.get("stop_price") or t.get("sl") for t in trades],
        "tp": [t.get("tp_price") or t.get("tp") for t in trades],
        "exit_reasons": [t.get("exit_reason") for t in trades],
        "r_multiples": [t.get("r_multiple") for t in trades],
        "equity_curve_r": result.get("equity_curve_r"),
        "max_drawdown_R": result.get("max_drawdown_R"),
        "average_R": result.get("average_R"),
        "dataset_fingerprint": dataset_fp,
        "configuration_fingerprint": "COMBO_02",
    }


async def _main() -> None:
    from app.config import get_settings
    from app.research.backtest_cpu_pool import (
        run_combination_backtest_isolated,
        shutdown_research_cpu_pool,
    )
    from app.research.bos_combinations import get_combination
    from app.research.combination_backtest import run_combination_backtest
    from app.research.config import ResearchConfig
    from app.research.service import _load_research_candles
    from app.services.database import db_manager
    from app.signals.config import SignalConfig

    OUT.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    if not db_manager.enabled or db_manager.engine is None:
        await db_manager.connect(settings)

    c1h, _, _ = await _load_research_candles(
        SYMBOL, "1h", start_date=START, end_date=END, use_research_cache=True
    )
    c4h, _, _ = await _load_research_candles(
        SYMBOL, "4h", start_date=START, end_date=END, use_research_cache=True
    )
    assert len(c1h) == 10968, f"unexpected bar count {len(c1h)}"

    h = hashlib.sha256()
    for c in c1h:
        h.update(str(c.get("open_time") or c.get("time")).encode())
        h.update(str(c.get("close")).encode())
    dataset_fp = h.hexdigest()[:32]

    combo = get_combination("COMBO_02")
    scfg = SignalConfig()
    rcfg = ResearchConfig()

    t0 = time.perf_counter()
    baseline = run_combination_backtest(
        SYMBOL,
        "1h",
        c1h,
        combo,
        signal_config=scfg,
        research_config=rcfg,
        direction_filter="LONG",
        candles_1h=c1h,
        candles_4h=c4h,
    )
    baseline_s = time.perf_counter() - t0

    t1 = time.perf_counter()
    isolated = await run_combination_backtest_isolated(
        symbol=SYMBOL,
        timeframe="1h",
        candles=c1h,
        combination_id="COMBO_02",
        signal_config=scfg,
        research_config=rcfg,
        direction_filter="LONG",
        candles_1h=c1h,
        candles_4h=c4h,
    )
    isolated_s = time.perf_counter() - t1

    b = _extract(baseline, dataset_fp)
    i = _extract(isolated, dataset_fp)
    equality = {
        "trade_count": b["trade_count"] == i["trade_count"],
        "trade_digest": b["trade_digest"] == i["trade_digest"],
        "entry_prices": b["entry_prices"] == i["entry_prices"],
        "exit_prices": b["exit_prices"] == i["exit_prices"],
        "entry_ts": b["entry_ts"] == i["entry_ts"],
        "exit_ts": b["exit_ts"] == i["exit_ts"],
        "sl": b["sl"] == i["sl"],
        "tp": b["tp"] == i["tp"],
        "exit_reasons": b["exit_reasons"] == i["exit_reasons"],
        "r_multiples": b["r_multiples"] == i["r_multiples"],
        "equity_curve_r": b["equity_curve_r"] == i["equity_curve_r"],
        "max_drawdown_R": b["max_drawdown_R"] == i["max_drawdown_R"],
        "configuration_fingerprint": True,
        "dataset_fingerprint": b["dataset_fingerprint"] == i["dataset_fingerprint"],
    }
    equality["all_pass"] = all(equality.values())
    report = {
        "label": "process_pool_vs_inprocess_COMBO_02",
        "bars_1h": len(c1h),
        "bars_4h": len(c4h),
        "baseline_runtime_s": round(baseline_s, 3),
        "isolated_runtime_s": round(isolated_s, 3),
        "baseline": b,
        "isolated": i,
        "equality": equality,
        "isolation": "process_pool_max_workers_1",
    }
    path = OUT / "backtest_process_pool_golden_equality.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"all_pass": equality["all_pass"], "trades": b["trade_count"], "path": str(path)}, indent=2))
    shutdown_research_cpu_pool(wait=False)
    if not equality["all_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(_main())
