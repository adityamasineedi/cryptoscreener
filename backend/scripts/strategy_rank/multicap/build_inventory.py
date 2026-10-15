"""Job A: write _inventory.json. Ids are read from the repo registries where they exist."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from app.research.bos_combinations import COMBINATIONS  # noqa: E402
from app.research.bos_strategy_comparison.strategies import STRATEGIES as BSC  # noqa: E402
from app.research.grid_range_research.config import GRID_VARIANTS  # noqa: E402
from app.research.multi_cap_strategies.adapter import STRATEGIES as MC  # noqa: E402
from app.research.ohlcv_structure_experiment.config import VARIANTS as OSE  # noqa: E402
from app.research.scalping.catalog import SCALPING_STRATEGIES  # noqa: E402
from app.research.sd_structure.variants import VARIANTS as SD  # noqa: E402

E: list[dict] = []


def add(id, package, entry, directions, timeframes, runnable, owner, kind="strategy", reason="", notes=""):
    E.append({
        "id": id, "package": package, "engine_entry_point": entry, "directions": directions,
        "timeframes": timeframes, "kind": kind, "runnable_backtest": runnable, "owner": owner,
        "not_backtestable_reason": reason, "notes": notes,
    })


CB = "backend/app/research/bos_combinations.py"
CE = "combination_backtest.run_combination_backtest"
for cid, c in COMBINATIONS.items():
    if cid in SD:
        add(cid, "backend/app/research/sd_structure/ (registered in bos_combinations.py)", CE,
            ["LONG", "SHORT"], ["15m", "1h", "4h"], True, "combo",
            notes=f"SD-structure variant {SD[cid]}; name={c.name}")
    else:
        extra = ""
        if cid.startswith("COMBO_02_V2"):
            extra = " routed through combo02_v2/ (router, playbooks, research_variants)"
        if cid.startswith("COMBO_03_TRANSITION"):
            extra = " routed through combo03_transition/ (state machine, variants)"
        if cid == "COMBO_02_V2_1A":
            extra += "; alias of COMBO_02_V2_1_A (same definition name, older id)"
        add(cid, CB, CE, ["LONG", "SHORT"], ["5m", "15m", "1h", "4h", "1d"], True, "combo",
            notes=f"name={c.name}.{extra}")

# strategy_catalog.py / v1Config.ts ids that are presentation aliases of a combination
ALIASES = [
    ("COMBO_02_V1", "COMBO_02 LONG, frozen v1 profile (v1_production.py)", ["LONG"], ["1h"]),
    ("COMBO_02_V1_CLOSED_HTF", "COMBO_02_CLOSED_HTF", ["LONG"], ["1h"]),
    ("COMBO_02_SHORT_RESEARCH", "COMBO_02 SHORT via service.strategy_matrix (combo02_short_research*.py)", ["SHORT"], ["1h"]),
    ("COMBO_04_HTF_SHORT", "COMBO_04_HTF SHORT", ["SHORT"], ["1h"]),
    ("COMBO_SD_STRUCTURE", "UI menu group for the ten COMBO_SD_* variants (v1Config.ts)", ["LONG", "SHORT"], ["15m", "1h", "4h"]),
    ("COMBO_02_V2_RESEARCH", "COMBO_02 on dynamic registry candidates (dynamic_candidate_pipeline.py / strategy_candidate_registry.py)", ["LONG"], ["1h"]),
]
for aid, what, dirs, tfs in ALIASES:
    add(aid, "backend/app/research/strategy_catalog.py + frontend/src/backtest/v1Config.ts", CE,
        dirs, tfs, True, "combo", kind="alias",
        notes=f"Not a separate rule set: {what}. Covered by the combo owner under the underlying COMBO id.")

add("COMBO_02_V1_1_RISK_CONTROLLED", "backend/app/research/combo02_v1_1_risk_controlled.py",
    "replay_candidates_with_risk_controls(candidates)", ["LONG"], ["1h"], False, "none",
    kind="portfolio_overlay",
    reason="Portfolio risk overlay that replays an existing COMBO_02 trade list across symbols "
           "(cluster heat, concurrency, daily/symbol loss limits); no signal logic of its own and "
           "no per-symbol cell. Needs the combo owner's COMBO_02 LONG trades as input.",
    notes="UNCOVERED by all four owners as a standalone row.")
add("PATH_B_EXPERIMENTAL", "backend/app/signals/signal_engine.py (live Trade Plan path B)",
    "app.signals.backtest.run_backtest (same SignalEngine)", ["LONG"], ["15m"], False, "none",
    kind="live_signal_path",
    reason="Live-signal path; its only backtest (signals/backtest.run_backtest) returns an "
           "aggregate R report with no per-trade records, so no USD row can be built without "
           "editing backend/app. Closest researched equivalents: COMBO_04..08 (combo owner) and "
           "bos_strategy_comparison STRATEGY_2.",
    notes="UNCOVERED by all four owners.")
add("LIVE_SIGNAL_ENGINE", "backend/app/signals/ (signal_engine, bos/choch/impulse/pullback/retest/entry/stop/target engines)",
    "app.signals.backtest.run_backtest", ["LONG", "SHORT"], ["5m", "15m", "1h", "4h"], False, "multicap",
    kind="live_signal_engine",
    reason="run_backtest exposes only aggregate R metrics (no trade list) -> NOT_RUN row written.")

for reg in SCALPING_STRATEGIES:
    sid = reg.strategy_id if reg.variant == "default" else f"{reg.strategy_id}:{reg.variant}"
    add(sid, "backend/app/research/scalping/", "scalping.backtest.run_backtest / ScalpingStrategyEngine",
        ["LONG", "SHORT"], ["1m (5m/15m context)"], bool(reg.specification_complete), "scalp",
        reason="" if reg.specification_complete else "specification_complete=False in catalog.py: rules were never supplied, engine refuses to evaluate",
        notes=f"version={reg.version}")
add("INTRADAY_BOS_SD_V1", "backend/app/research/intraday_bos_sd/", "intraday_bos_sd.runtime / execution / portfolio",
    ["LONG", "SHORT"], ["5m setup (1h direction, 15m zone, 1m execution)"], True, "scalp",
    notes="Frozen spec intraday_bos_sd_v1.0.0; own costs (taker 4 bps, slippage 5 bps, equity 10000, leverage cap 1).")

for vid, v in GRID_VARIANTS.items():
    add(vid, "backend/app/research/grid_range_research/", "grid_range_research.engine.run_grid_backtest",
        ["LONG", "SHORT"], ["15m (1h regime)"], True, "grid",
        notes=f"n_levels={v.n_levels} max_simultaneous={v.max_simultaneous} include_lvc={v.include_lvc}")
add("GRID_TRADING_BOOK", "backend/app/grid_trading/", "grid_trading.engine.run_grid(candles, GridConfig, funding)",
    ["LONG (long-inventory grid)"], ["any OHLCV timeframe"], True, "grid",
    notes="User-parameterised grid (GridConfig) with settlement funding; scanner.py is a range-suitability scorer, not a strategy.")

for sid, d in MC.items():
    add(sid, "backend/app/research/multi_cap_strategies/",
        f"multi_cap_strategies.{sid.lower()}.generate_candidates + combination_backtest.evaluate_candidate_trades",
        [d.direction], list(d.timeframes), True, "multicap",
        notes=f"designed cap tier {d.asset_group}; cap filter is in MultiCapStrategyAdapter (universe selection).")
add("MULTI_CAP_REFERENCE", "backend/app/research/multi_cap_strategies/reference.py", "ref_* detector functions",
    ["LONG"], ["n/a"], False, "multicap", kind="diagnostic",
    reason="numpy cross-check of signal detectors only; no entries/stops/exits. baseline_forensics.py is an artifact audit.")

add("SHORT_PULLBACK_REJECTION_RESEARCH", "backend/app/research/short_pullback_rejection/",
    "signals.generate_signals_for_symbol + simulation.simulate_signals", ["SHORT"], ["1h (4h regime)"],
    True, "multicap", notes="Primary variant stop_buffer_0_25/nearest_support run; 3x4 stop/TP grid is a sensitivity study (not run).")
add("CANDLE12_V2", "backend/app/research/candle12_v2.py", "run_candle12_v2", ["LONG", "SHORT"],
    ["5m", "15m", "1h"], True, "multicap", notes="Candle-1 BOS -> Candle-2 trigger entry.")
add("CANDLE12_HYPOTHESIS_V1", "backend/app/research/candle12_hypothesis.py", "run_hypothesis_a / run_hypothesis_b",
    ["LONG", "SHORT"], ["5m", "15m", "1h"], False, "multicap", kind="superseded",
    reason="Superseded by candle12_v2 (v2 adds the fee/slippage model and bounded lookback); not run.")
for sid, d in BSC.items():
    add(sid, "backend/app/research/bos_strategy_comparison/", "runner.run_multi_strategy_backtest",
        ["LONG", "SHORT"], ["15m (4h/1h HTF, 5m context)"], True, "multicap",
        kind="strategy" if d.kind == "STRATEGY" else "control_baseline",
        notes=f"{d.strategy_name}. Very slow (production SignalEngine bar-by-bar).")
for v in OSE:
    add(f"OSE_{v}", "backend/app/research/ohlcv_structure_experiment/", "engine.breaks_for_variant + engine.run_variant",
        ["LONG", "SHORT"], ["any (no declared TF); run on 15m, 1h"], True, "multicap",
        kind="timing_experiment",
        notes="BOS/CHoCH break-entry experiment with full entry/stop/target/hold rules; R-only engine.")

NONE = [
    ("SHORT_ENTRY_RESEARCH_VARIANTS", "backend/app/research/short_entry_research/", "runner.run_short_entry_research",
     ["SHORT"], ["1h"], "sensitivity_study",
     "Re-simulates entry/stop/TP variants (baseline, chasing exclusion, extension filter, retest, stop/TP grids) over a parent COMBO_02 SHORT trade file (PARENT_RUN_ID); not a standalone strategy, and its runner writes reports."),
    ("MARKET_STRUCTURE_DISCOVERY_V1", "backend/app/research/market_structure_discovery_v1/", "runner.run_discovery",
     ["LONG", "SHORT"], ["1h"], "discovery_wrapper",
     "Bounded search wrapper that calls run_combination_backtest on existing COMBO ids for BTC/ETH/SOL; families.py says its labels are research questions, not entry rules (3 of 6 families map to no combination)."),
    ("LONG_COMMIT_OPTIMIZER", "backend/app/research/long_commit_optimizer/ (only __pycache__, no source)", "n/a",
     ["LONG"], ["n/a"], "optimiser", "Source files are absent from the working tree (compiled .pyc only); an optimiser over commits, not a strategy."),
    ("TREND_EVAL", "backend/app/research/trend_eval/ (only __pycache__, no source)", "n/a", [], ["n/a"],
     "diagnostic", "Source files are absent from the working tree (compiled .pyc only)."),
    ("AUTO_RESEARCH", "backend/app/research/auto_research/ (only __pycache__, no source)", "n/a", [], ["n/a"],
     "optimiser", "Source files are absent (compiled .pyc only); automated candidate search/scoring, not a strategy."),
    ("COMBO02_DIAGNOSTICS", "backend/app/research/{entry_diagnostics,failure_regime_decomposition,trend_regime_diagnostic,htf_alignment_sensitivity,short_policy_sensitivity}.py, short_research_diagnostics/, market_structure/, trade_plan_forensics/, live_backtest_parity/",
     "various", [], ["n/a"], "diagnostic",
     "Diagnostics / sensitivity filters / parity checks over COMBO_02 trades; none defines entries."),
    ("ENGINES_ANALYTICS", "backend/app/engines/ (orchestrator, structure, supply_demand, mtf, oi, liquidations, volume, valuation, derivatives, filters, signals/tech_rating, signals/entry_exit)",
     "engines.orchestrator", [], ["n/a"], "live_analytics",
     "Screener analytics / indicator engines feeding the live screener and Trade Plan; no backtest entry point; several need OI / liquidation / derivatives history that is not in the ohlcv table."),
    ("CROSS_SECTIONAL_DISCOVERY", "backend/app/research/cross_sectional_discovery.py", "read-only adapter", [], ["n/a"],
     "artifact_reader", "Reads artifacts from research_sandbox; no strategy code in backend/app."),
]
for nid, pkg, entry, dirs, tfs, kind, reason in NONE:
    add(nid, pkg, entry, dirs, tfs, False, "none", kind=kind, reason=reason)

out = {
    "generated_by": "scripts/strategy_rank/multicap/build_inventory.py",
    "count": len(E),
    "owner_counts": {o: sum(1 for e in E if e["owner"] == o) for o in ("combo", "scalp", "grid", "multicap", "none")},
    "entries": E,
}
(HERE / "_inventory.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print(out["count"], out["owner_counts"])
