"""In-process SOL V1 / V2.1-A fingerprint reproduction (no HTTP, research only)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _run_one(
    *,
    label: str,
    expected_fp: str,
    combination_id: str,
    strategy_id: str,
    combo_version: str,
    risk_mode: str,
    research_risk_override: bool,
) -> dict:
    from app.research.backtest_ui_config import (
        cell_risk_lookup,
        enrich_row_with_config,
        validate_backtest_request,
    )
    from app.research.service import get_bos_research_service

    resolved = validate_backtest_request(
        symbols=["SOLUSDT"],
        timeframes=["1h"],
        direction="LONG",
        combination_id=combination_id,
        strategy_id=strategy_id,
        combo_version=combo_version,
        setup_timeframe="1h",
        risk_mode=risk_mode,
        research_risk_override=research_risk_override,
        risk_usd=20.0,
        principal_usd=1000.0,
        leverage=2.0,
        taker_fee_pct=0.04,
        maker_fee_pct=0.02,
        start_date="2026-09-01",
        end_date="2026-10-06",
        allow_short=True,
    )
    assert resolved.configuration_fingerprint == expected_fp, (
        resolved.configuration_fingerprint,
        expected_fp,
    )
    assert resolved.paper_trade_created is False
    assert resolved.live_trade_created is False
    assert resolved.telegram_sent is False

    cell = cell_risk_lookup(resolved, "SOLUSDT", "1h")
    cell_risk = float(cell.effective_risk_amount) if cell else 20.0
    svc = get_bos_research_service()
    print(
        f"[{label}] fp={resolved.configuration_fingerprint} "
        f"risk_source={cell.risk_source if cell else None} "
        f"risk={cell_risk} htf_alignment={resolved.htf_alignment}"
    )
    payload = await svc.strategy_matrix(
        combination_id=resolved.combination_id,
        symbols=["SOLUSDT"],
        timeframes=["1h"],
        direction=resolved.direction,
        limit=2000,
        risk_usd=cell_risk,
        principal_usd=1000.0,
        start_date="2026-09-01",
        end_date="2026-10-06",
        taker_fee=0.0004,
        maker_fee=0.0002,
        leverage=2.0,
        include_trades=True,
    )
    rows = payload.get("rows") or []
    row = rows[0] if rows else {}
    row = enrich_row_with_config(row, resolved)
    trades = row.get("trades") or []
    closed = [
        t
        for t in trades
        if str(t.get("outcome") or "").upper() not in ("", "OPEN", "NONE")
    ]
    # best trade share
    nets = [float(t.get("net_pnl_usd") or t.get("pnl_usd_net") or 0) for t in closed]
    best = max(nets) if nets else 0.0
    gross_pos = sum(x for x in nets if x > 0)
    summary = {
        "label": label,
        "configuration_fingerprint": resolved.configuration_fingerprint,
        "expected_fp": expected_fp,
        "fp_match": resolved.configuration_fingerprint == expected_fp,
        "risk_mode": resolved.risk_mode,
        "risk_source": cell.risk_source if cell else None,
        "htf_alignment_resolved": resolved.htf_alignment,
        "bars_loaded": row.get("bars_loaded"),
        "period_start": row.get("period_start"),
        "period_end": row.get("period_end"),
        "sample_size": row.get("sample_size"),
        "win_rate": row.get("win_rate"),
        "pnl_usd": row.get("pnl_usd"),
        "pnl_usd_net": row.get("pnl_usd_net"),
        "average_R": row.get("average_R"),
        "fees_usd": row.get("fees_usd"),
        "paper_trade_created": resolved.paper_trade_created,
        "live_trade_created": resolved.live_trade_created,
        "telegram_sent": resolved.telegram_sent,
        "n_closed": len(closed),
        "best_trade_net": best,
        "best_trade_share_of_gross_pos": (best / gross_pos) if gross_pos > 0 else None,
        "directions": {
            "LONG": sum(1 for t in closed if t.get("direction") == "LONG"),
            "SHORT": sum(1 for t in closed if t.get("direction") == "SHORT"),
        },
        "trades": [
            {
                "direction": t.get("direction"),
                "entry_time": t.get("entry_time") or t.get("signal_time"),
                "exit_time": t.get("exit_time"),
                "outcome": t.get("outcome"),
                "r_multiple": t.get("r_multiple"),
                "net_pnl_usd": t.get("net_pnl_usd") or t.get("pnl_usd_net"),
                "bars_held": t.get("bars_held") or t.get("holding_bars"),
                "actual_risk_usd": t.get("actual_risk_usd") or t.get("risk_usd"),
            }
            for t in closed
        ],
    }
    print(json.dumps({k: v for k, v in summary.items() if k != "trades"}, indent=2))
    for t in summary["trades"]:
        print(" ", t)
    return summary


async def main() -> None:
    from app.config import get_settings
    from app.services.database import db_manager

    await db_manager.connect(get_settings(), ensure_schema=False)
    try:
        out = {}
        out["V1"] = await _run_one(
            label="V1",
            expected_fp="59c539c43934b5ad",
            combination_id="COMBO_02",
            strategy_id="COMBO_02_V1",
            combo_version="v1",
            risk_mode="V1_PRODUCTION_PROFILE",
            research_risk_override=False,
        )
        out["V21A"] = await _run_one(
            label="V21A",
            expected_fp="df43d4bc7280f4e0",
            combination_id="COMBO_02_V2_1_A",
            strategy_id="COMBO_02_V2_1_A",
            combo_version="v2.1-a-choppy-wait",
            risk_mode="RESEARCH_OVERRIDE",
            research_risk_override=True,
        )
        path = ROOT / "reports" / "AUDIT_repro_sol_fingerprints.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        print("wrote", path)
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
