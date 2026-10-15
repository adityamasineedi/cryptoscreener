"""Simulator v1 (archived) vs v2 (corrected) on the Backtest-screen LCSC 1h LONG path.

Same candles, same candidates, same sizing as run_engine_cell. Reports whether the
screen numbers change under v2, and how large the v1 fee-helper defect would be if
the dormant slippage rate were switched on. Writes out/v2_compare.json.

Run: PYTHONPATH=. .venv/Scripts/python.exe scripts/lcsc_validation/v2_compare.py
"""

from __future__ import annotations

import copy
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path

from app.research.combination_backtest import evaluate_candidate_trades
from app.research.execution_v2 import enrich_trades_v2, evaluate_candidate_trades_v2
from app.research.multi_cap_strategies.adapter import MultiCapStrategyAdapter
from app.research.multi_cap_strategies.config import MultiCapResearchConfig
from app.research.multi_cap_strategies.large_cap_sweep_choch import DEFINITION, generate_candidates
from app.research.trade_fees import enrich_trades

OUT = Path(__file__).resolve().parent / "out"
SIZING = dict(risk_usd=20.0, taker_fee=0.0004, maker_fee=0.0002, leverage=2.0, account_equity=1000.0)


def summary(rows: list[dict]) -> dict:
    nets = [r["net_pnl_usd"] for r in rows]
    eq = pk = dd = 0.0
    for p in nets:
        eq += p
        pk = max(pk, eq)
        dd = max(dd, pk - eq)
    return {"trades": len(rows), "net_usd": round(sum(nets), 2), "gross_usd": round(sum(r["gross_pnl_usd"] for r in rows), 2),
            "fees_usd": round(sum(r["fee_total_usd"] for r in rows), 2), "max_dd_usd": round(dd, 2)}


def main() -> None:
    report = {"generated_utc": datetime.now(timezone.utc).isoformat(), "symbols": {}}
    for sym in ("BTCUSDT", "ETHUSDT"):
        candles = pickle.loads((OUT / f"candles_{sym}_1h.pkl").read_bytes())
        i0 = next(i for i, c in enumerate(candles) if c["time"] >= datetime(2023, 1, 1, tzinfo=timezone.utc))
        sub = candles[max(0, i0 - 1000):]
        first = i0 - max(0, i0 - 1000)
        cfg = MultiCapResearchConfig()
        cands = generate_candidates(sym, "1h", sub, config=cfg, index_start=first)
        base = MultiCapStrategyAdapter(cfg).candidates_to_trades(cands, asset_group=DEFINITION.asset_group,
                                                                 period_label="FULL")
        v1_eval = evaluate_candidate_trades(sub, copy.deepcopy(base), handling=cfg.ambiguous_handling,
                                            one_open_at_a_time=cfg.one_open_at_a_time)
        v2_eval = evaluate_candidate_trades_v2(sub, copy.deepcopy(base), handling=cfg.ambiguous_handling,
                                               one_open_at_a_time=cfg.one_open_at_a_time)
        v1_closed = [t.to_dict() for t in v1_eval if t.outcome and t.outcome != "OPEN"]
        v2_closed = [t.to_dict() for t in v2_eval if t.outcome and t.outcome != "OPEN"]
        gaps = sum(1 for t in v2_eval if (t.condition_snapshot or {}).get("gap_stop"))
        same_exits = [(a["entry_index"], a["exit_index"], a["exit_price"]) for a in v1_closed] == \
                     [(b["entry_index"], b["exit_index"], b["exit_price"]) for b in v2_closed]
        row = {
            "gap_stop_exits_v2": gaps,
            "identical_exits": same_exits,
            "screen_v1": summary(enrich_trades(v1_closed, **SIZING)),
            "screen_v2": summary(enrich_trades_v2(v2_closed, **SIZING)),
            # The dormant defect: what the 0.02% config slippage would have shown.
            "slip_2bps_v1": summary(enrich_trades(v1_closed, slippage_rate=0.0002, **SIZING)),
            "slip_2bps_v2": summary(enrich_trades_v2(v2_closed, slippage_rate=0.0002, **SIZING)),
        }
        report["symbols"][sym] = row
        print(sym, json.dumps(row))
    (OUT / "v2_compare.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
