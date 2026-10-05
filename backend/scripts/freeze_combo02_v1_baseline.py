"""Freeze COMBO_02 v1 regression baseline (BTC/ETH/SOL 1h) — no param tuning.

Saves trade-level + summary metrics for before/after safety-fix diffs.
Does not alter swing/BOS/HTF/stop/TP/risk engines.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.research.combo02_candidate_eligibility import max_losing_streak
from app.research.config import ResearchConfig
from app.research.service import BosResearchService
from app.research.v1_production import COMBO_ID, COMBO_VERSION, FROZEN_V1_SYMBOLS

OUT_DIR = Path(__file__).resolve().parents[1] / "reports" / "combo02_v1_regression"
SYMBOLS = sorted(FROZEN_V1_SYMBOLS)  # BTCUSDT, ETHUSDT, SOLUSDT
TIMEFRAMES = ["1h"]
LIMIT = 2000
RISK_USD = 20.0  # $1,000 × 2%


def _trade_key(t: dict[str, Any]) -> str:
    return "|".join(
        [
            str(t.get("symbol") or ""),
            str(t.get("timeframe") or ""),
            str(t.get("signal_time") or ""),
            str(t.get("entry_price") or ""),
            str(t.get("stop_price") or ""),
            str(t.get("tp1") or ""),
            str(t.get("outcome") or ""),
        ]
    )


def _summarize_row(row: dict[str, Any]) -> dict[str, Any]:
    trades = list(row.get("trades") or [])
    closed = [t for t in trades if t.get("outcome") not in (None, "OPEN")]
    rs_gross = [float(t["r_multiple"]) for t in closed if t.get("r_multiple") is not None]
    rs_net = [float(t["r_net"]) for t in closed if t.get("r_net") is not None]
    wins = sum(1 for r in rs_gross if r > 0)
    n = len(closed)
    return {
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "sample_size": n,
        "win_rate": (wins / n) if n else None,
        "average_R": row.get("average_R"),
        "average_R_net": row.get("average_R_net"),
        "pnl_usd": row.get("pnl_usd"),
        "pnl_usd_net": row.get("pnl_usd_net"),
        "fees_usd": row.get("fees_usd"),
        "max_drawdown_R": row.get("max_drawdown_R"),
        "max_losing_streak": max_losing_streak(rs_gross) if rs_gross else 0,
        "max_losing_streak_net": max_losing_streak(rs_net) if rs_net else 0,
        "tp1_hits": row.get("tp1_hits"),
        "sl_hits": row.get("sl_hits"),
        "period_start": row.get("period_start"),
        "period_end": row.get("period_end"),
        "bars_loaded": row.get("bars_loaded"),
        "candle_source": row.get("candle_source"),
        "trade_keys": [_trade_key(t) for t in closed],
    }


def _param_fingerprint() -> str:
    raw = json.dumps(
        {
            "combo_id": COMBO_ID,
            "combo_version": COMBO_VERSION,
            "symbols": SYMBOLS,
            "timeframes": TIMEFRAMES,
            "direction": "LONG",
            "limit": LIMIT,
            "risk_usd": RISK_USD,
            "require_htf": True,
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


async def run_baseline(*, tag: str) -> Path:
    from app.config import get_settings
    from app.services.database import db_manager

    await db_manager.connect(get_settings())
    if not db_manager.enabled or db_manager.engine is None:
        raise SystemExit(f"DB unavailable: status={db_manager.status}")

    svc = BosResearchService()
    matrix = await svc.strategy_matrix(
        combination_id=COMBO_ID,
        symbols=SYMBOLS,
        timeframes=TIMEFRAMES,
        limit=LIMIT,
        direction="LONG",
        risk_usd=RISK_USD,
        include_trades=True,
    )

    rows = list(matrix.get("rows") or [])
    summaries = [_summarize_row(r) for r in rows]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{tag}_{stamp}.json"

    payload = {
        "label": "COMBO_02_V1_REGRESSION_BASELINE",
        "tag": tag,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "combo_id": COMBO_ID,
        "combo_version": COMBO_VERSION,
        "param_fingerprint": _param_fingerprint(),
        "params": {
            "symbols": SYMBOLS,
            "timeframes": TIMEFRAMES,
            "direction": "LONG",
            "limit": LIMIT,
            "risk_usd": RISK_USD,
            "require_htf_alignment": True,
            "note": (
                "Frozen baseline for safety-fix regression. "
                "Do not tune swing/BOS/HTF/stop/TP/risk."
            ),
        },
        "research_config": {
            "min_rr": ResearchConfig().min_rr,
            "min_bars": ResearchConfig().min_bars,
        },
        "matrix_status": matrix.get("status"),
        "elapsed_seconds": matrix.get("elapsed_seconds"),
        "summaries": summaries,
        "rows": rows,
        "disclaimer": (
            "Historical regression baseline only — not a profitability claim. "
            "Small n is directional evidence, not robustness proof."
        ),
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    # Stable pointer for post-fix diff tooling
    latest = OUT_DIR / f"{tag}_latest.json"
    latest.write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"Wrote {out_path}")
    print(f"Wrote {latest}")
    print(f"param_fingerprint={payload['param_fingerprint']}")
    for s in summaries:
        print(
            f"  {s['symbol']}: n={s['sample_size']} WR={s['win_rate']} "
            f"avgR={s['average_R']} avgR_net={s['average_R_net']} "
            f"pnl_net={s['pnl_usd_net']} maxDD={s['max_drawdown_R']} "
            f"lose_streak={s['max_losing_streak']}"
        )
    return out_path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--tag",
        default="baseline_pre_safety",
        help="baseline_pre_safety | post_safety",
    )
    args = p.parse_args()
    asyncio.run(run_baseline(tag=str(args.tag)))


if __name__ == "__main__":
    main()
