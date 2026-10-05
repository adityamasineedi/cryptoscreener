# Changelog

## Pre-research code freeze — 2026-10-05

**Tag:** `pre-research-freeze-20261005`  
**Doc:** [`docs/pre_research_freeze.md`](./docs/pre_research_freeze.md)

Working baseline before new strategy research. HTF entry logic remains `v1-combo02-long-htf`.

- Paper sizing module (`paper_sizing.py`) with tick/lot/leverage/fee realism
- Paper risk defaults: max 15 open positions / 30% open risk
- Extended COMBO_02 1h paper universe @ 2% (claim set still BTC/ETH/SOL)
- Chart time-axis + annotation fixes; paper/backtest/v1 watcher UI sync

## COMBO_02 candidate eligibility pipeline — 2026-10-04

**Research only — frozen v1 (BTC/ETH/SOL) unchanged**

- `app/research/combo02_candidate_thresholds.py` — centralized selector + tier + OOS thresholds.
- `app/research/combo02_candidate_selector.py` + `scripts/select_combo02_candidates.py` — reproducible manifest (`combo02_candidates_<UTC>.json`).
- `app/research/combo02_candidate_research.py` + `scripts/run_combo02_candidate_research.py` — frozen COMBO_02 LONG 1h HTF batch, metrics, tiers, OOS, portfolio overlap; reports under `backend/reports/`.
- Read-only UI: `GET /api/research/combo02-candidate-results` + Backtest tab Candidate Research panel (no promote/Telegram/paper actions).
- Tests: selector exclusions, tier/OOS/portfolio helpers, enrich_trades field mapping, v1 boundary AST checks.

## v1 execution-safety + candidate research — 2026-10-04

**Legacy paper isolation (Part A)**

- `PAPER_LEGACY_AUTO_ENTRY_ENABLED=false` by default — 15m setup→paper opens disabled; screener/BOS/liq/UI alerts continue.
- Explicit classification on paper trades/alerts: `strategy_id`, `source`, `combo_id`, `combo_version`, `path`, `telegram_eligible`.
- V1 watcher: `COMBO_02_V1` / `V1_PAPER_WATCHER` / 1h Path A; legacy: `RESEARCH_15M` / `LEGACY_SETUP_SIGNAL` (never v1).
- Telegram fail-closed on full v1 identity + `telegram_eligible` + `HTF_ALIGNED` (still off by default).
- `v1_monitor_paper.py` scores only v1 watcher rows; diagnostic exclusion counts.
- Alerts UI badges/filters: V1 VERIFIED vs RESEARCH 15M / Structure / Liquidations / Experimental.
- Operator action: `POST /api/paper/close-legacy?confirm=true` (reason `legacy_cleanup`).

**Candidate research runner (Part B)** — research only, no v1 promotions

- `scripts/select_combo02_candidates.py` — reproducible universe selector (replaces ad-hoc Top-100).
- `scripts/run_combo02_candidate_research.py` — frozen COMBO_02 LONG 1h HTF backtests + tiers + OOS.
- Reports under `backend/reports/combo02_candidate_*`.

## v1 production profile — 2026-10-03

**Doc:** [`docs/v1_production.md`](./docs/v1_production.md)  
**Module:** `backend/app/research/v1_production.py`

Minimal paper/live book on top of freeze `v1-combo02-long-htf` (HTF gate unchanged):

- Core: BTC 1h @ **1.5%**; secondary: ETH/SOL 1h @ **0.5%**; 4h optional; 15m research-only.
- Path A: `PAPER_V1_*` env — universe BTC/ETH/SOL, per-symbol risk, HTF fields in `signal_snippet`.
- Backtest job: COMBO_02 core/secondary cells sized from profile × `principal_usd`.
- UI badges (core / secondary / research) on Backtest + Paper panels.
- Weekly monitor script: `backend/scripts/v1_monitor_paper.py`.

## v1-combo02-long-htf — 2026-10-03

**Freeze tag:** `v1-combo02-long-htf`  
**Doc:** [`docs/v1_freeze.md`](./docs/v1_freeze.md)

Production-candidate freeze for the HTF-gated **COMBO_02 LONG** playbook (BTC/ETH/SOL, 1h):

- Research/backtest: `COMBO_02` with `require_htf_alignment=True` (fail closed).
- Paper Path A: always requires 4h+1h bullish HTF; `LONG_ENTRY_CANDIDATE` no longer bypasses the gate in `path_a` mode.
- `ResearchService.walk_forward` loads 1h/4h for HTF-gated combos.
- `COMBO_02_LOCAL` marked LEGACY/RESEARCH-ONLY (not v1).
- Path B labeled experimental — **not** COMBO_02 v1.
- v1-critical tests: `test_combo02_htf_gate.py` + Path A HTF tests in `test_paper_trade.py`.

Any behavior-changing edit to frozen v1 logic requires a new version tag (v2+), not silent mutation of this freeze.
