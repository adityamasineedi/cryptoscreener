#!/usr/bin/env python3
"""Compare COMBO_02 v1 vs COMBO_02_V2 on January 2026 BTCUSDT (research only)."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path


def _metrics(out: dict) -> dict:
    trades = list(out.get("trades") or [])
    closed = [t for t in trades if t.get("outcome") not in (None, "OPEN")]
    rs = []
    for t in closed:
        try:
            if t.get("r_multiple") is not None:
                rs.append(float(t["r_multiple"]))
        except (TypeError, ValueError):
            pass
    wins = sum(1 for x in rs if x > 0)
    losses = sum(1 for x in rs if x < 0)
    gross = sum(x for x in rs if x > 0)
    loss_abs = abs(sum(x for x in rs if x < 0))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in rs:
        equity += x
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    holds = []
    for t in closed:
        try:
            if t.get("holding_bars") is not None:
                holds.append(float(t["holding_bars"]))
        except (TypeError, ValueError):
            pass
    by_dir = Counter(str(t.get("direction") or "") for t in closed)
    by_play = Counter()
    by_regime = Counter()
    for t in closed:
        snap = t.get("condition_snapshot") or {}
        by_play[str(snap.get("playbook") or "N/A")] += 1
        by_regime[str(snap.get("regime") or "N/A")] += 1
    result = out.get("result") or {}
    return {
        "trades": len(closed),
        "long": by_dir.get("LONG", 0),
        "short": by_dir.get("SHORT", 0),
        "win_rate": (wins / len(rs)) if rs else None,
        "average_R": (sum(rs) / len(rs)) if rs else None,
        "total_R": sum(rs) if rs else 0.0,
        "max_drawdown_R": abs(max_dd) if rs else None,
        "profit_factor": (gross / loss_abs) if loss_abs > 0 else None,
        "average_holding_bars": (sum(holds) / len(holds)) if holds else None,
        "by_playbook": dict(by_play),
        "by_regime": dict(by_regime),
        "trend_trades": sum(
            by_regime[k]
            for k in ("BULL_TREND", "BEAR_TREND", "HIGH_VOLATILITY_TREND")
            if k in by_regime
        )
        + by_play.get("TREND_FOLLOWING", 0),
        "range_trades": by_play.get("RANGE_MEAN_REVERSION", 0),
        "reversal_trades": by_play.get("REVERSAL", 0),
        "sample_size": out.get("sample_size"),
        "result_average_R": result.get("average_R"),
        "result_max_drawdown_R": result.get("max_drawdown_R"),
        "result_profit_factor": result.get("profit_factor"),
        "trades_detail": [
            {
                "entry_index": t.get("entry_index"),
                "direction": t.get("direction"),
                "outcome": t.get("outcome"),
                "r_multiple": t.get("r_multiple"),
                "playbook": (t.get("condition_snapshot") or {}).get("playbook"),
                "regime": (t.get("condition_snapshot") or {}).get("regime"),
                "event": (t.get("condition_snapshot") or {}).get("event"),
                "entry_price": t.get("entry_price"),
                "stop_price": t.get("stop_price"),
                "tp1": t.get("tp1"),
            }
            for t in closed
        ],
    }


async def main() -> int:
    from app.config import get_settings
    from app.research.bos_combinations import get_combination
    from app.research.combination_backtest import run_combination_backtest
    from app.research.config import ResearchConfig
    from app.research.service import _load_research_candles
    from app.services.database import db_manager
    from app.signals.config import SignalConfig

    settings = get_settings()
    await db_manager.connect(settings)

    start, end = "2026-01-01", "2026-01-31"
    symbol = "BTCUSDT"
    c1h, _, _ = await _load_research_candles(
        symbol, "1h", start_date=start, end_date=end, use_research_cache=True
    )
    c4h, _, _ = await _load_research_candles(
        symbol, "4h", start_date=start, end_date=end, use_research_cache=True
    )
    c15m, _, _ = await _load_research_candles(
        symbol, "15m", start_date=start, end_date=end, use_research_cache=True
    )

    cfg = ResearchConfig()
    scfg = SignalConfig()
    v1 = run_combination_backtest(
        symbol,
        "1h",
        c1h,
        get_combination("COMBO_02"),
        signal_config=scfg,
        research_config=cfg,
        candles_1h=c1h,
        candles_4h=c4h,
        direction_filter="LONG",
    )
    v2 = run_combination_backtest(
        symbol,
        "1h",
        c1h,
        get_combination("COMBO_02_V2"),
        signal_config=scfg,
        research_config=cfg,
        candles_1h=c1h,
        candles_4h=c4h,
        candles_15m=c15m,
        direction_filter=None,
    )
    # Also v1 ALL for apples-to-apples SHORT visibility
    v1_all = run_combination_backtest(
        symbol,
        "1h",
        c1h,
        get_combination("COMBO_02"),
        signal_config=scfg,
        research_config=cfg,
        candles_1h=c1h,
        candles_4h=c4h,
        direction_filter=None,
    )

    m1 = _metrics(v1)
    m1_all = _metrics(v1_all)
    m2 = _metrics(v2)

    # Prefer playbook counts for v2 regime distribution
    if m2["by_playbook"]:
        m2["trend_trades"] = m2["by_playbook"].get("TREND_FOLLOWING", 0)
        m2["range_trades"] = m2["by_playbook"].get("RANGE_MEAN_REVERSION", 0)
        m2["reversal_trades"] = m2["by_playbook"].get("REVERSAL", 0)

    payload = {
        "symbol": symbol,
        "start": start,
        "end": end,
        "bars_1h": len(c1h),
        "combo02_v1_long": m1,
        "combo02_v1_all": m1_all,
        "combo02_v2": m2,
        "configuration_hash_v1": v1.get("configuration_hash"),
        "configuration_hash_v2": v2.get("configuration_hash"),
        "disclaimer": (
            "Research comparison only. Trade count ≠ profitability. "
            "COMBO_02 v1 unchanged."
        ),
    }
    out_dir = Path("reports/combo02_v2_january_compare")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "comparison.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )

    def row(label: str, a, b, c=None):
        if c is None:
            print(f"{label:<22} {a!s:>12} {b!s:>12}")
        else:
            print(f"{label:<22} {a!s:>12} {b!s:>12} {c!s:>12}")

    print("COMBO_02 January BTCUSDT comparison")
    print(f"{'Metric':<22} {'v1 LONG':>12} {'v1 ALL':>12} {'v2':>12}")
    row("Trades", m1["trades"], m1_all["trades"], m2["trades"])
    row("Long", m1["long"], m1_all["long"], m2["long"])
    row("Short", m1["short"], m1_all["short"], m2["short"])
    row("Trend playbook", "n/a", "n/a", m2["trend_trades"])
    row("Range playbook", "n/a", "n/a", m2["range_trades"])
    row("Reversal playbook", "n/a", "n/a", m2["reversal_trades"])
    row("Win rate", m1["win_rate"], m1_all["win_rate"], m2["win_rate"])
    row("Total R", m1["total_R"], m1_all["total_R"], m2["total_R"])
    row("Avg R", m1["average_R"], m1_all["average_R"], m2["average_R"])
    row("Max DD R", m1["max_drawdown_R"], m1_all["max_drawdown_R"], m2["max_drawdown_R"])
    row("Profit factor", m1["profit_factor"], m1_all["profit_factor"], m2["profit_factor"])
    print("v2 by playbook:", m2["by_playbook"])
    print("v2 by regime:", m2["by_regime"])
    print("wrote", out_dir / "comparison.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
