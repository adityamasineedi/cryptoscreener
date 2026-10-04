# Research System

Guide for future AI agents working in this repository.

## Read First

1. [`ARCHITECTURE.md`](./ARCHITECTURE.md) — authoritative runtime map
2. [`AI_CONTEXT.md`](./AI_CONTEXT.md) — this file
3. Relevant strategy docs (package `__init__` docstrings; `SHORT_RESEARCH_INTEGRITY.md` if touching SHORT research)
4. Inventory: [`../../scripts/research_codebase_inventory.md`](../../scripts/research_codebase_inventory.md)
5. Cleanup plan (do not execute unless asked): [`../../scripts/research_cleanup_plan.md`](../../scripts/research_cleanup_plan.md)

## Authoritative Implementations

| Concept | Authoritative File | Do Not Duplicate |
|---|---|---|
| PostgreSQL OHLCV reads | `postgres_ohlcv.py` | New SQL loaders / ad-hoc candle queries |
| OHLCV Parquet cache | `data_cache/cache_manager.py` | Parallel cache formats |
| OHLCV ingest (Binance→PG) | `data_pipeline/downloader.py` + `sync.py` | Alternate download loops in scripts |
| OHLCV semantic validation | `data_quality.py` (`verify_ohlcv`) | Forked validators that redefine gaps/OHLC rules |
| BOS / impulse / pullback / retest / swings / trend | `app/signals/*` via `SignalEngine` | Research-local reimplementations |
| Research HTF as-of + hard gate | `bos_strategy_comparison/htf.py` | Second HTF map builder |
| Multi-bar lifecycle | `bos_strategy_comparison/lifecycle.py` | Copy of WAITING state machine |
| COMBO definitions | `bos_combinations.py` | Inline combo copies |
| COMBO evaluation | `combination_engine.py` | Local COMBO_02 approximations |
| COMBO / shared exit sim | `combination_backtest.py` | Alternate intrabar resolvers |
| Trade R metrics | `metrics.py` | Parallel expectancy/PF/DD formulas |
| Fee sizing helpers | `trade_fees.py` | Ad-hoc fee math in new scripts |
| Frozen v1 production profile | `v1_production.py` | Hardcoded v1 symbol/book forks |
| Candidate state machine | `candidate_state_machine.py` + `strategy_candidate_registry.py` | Parallel state enums |
| Candle12 research | `candle12_v2.py` + `rolling_structure.py` | Reviving `candle12_hypothesis.py` as primary |
| Historical market cap | `historical_market_cap/` | Mixing HISTORICAL_CAP with live cap silently |
| Live↔backtest entry parity | `live_backtest_parity/` | One-off parity math in paper code |

## Research Entry Points

| Task | Entry Point |
|---|---|
| COMBO backtest / compare (API) | `GET/POST /research/bos-combinations*` → `service.BosResearchService` |
| UI long-strategy backtest job | `/research/long-strategy/backtest*` → `backtest_job.py` |
| BOS strategy comparison (API) | `/research/bos-strategies*` → `bos_strategy_comparison.service` |
| Multi-cap research (API) | `/research/multi-cap-strategies*` → `multi_cap_strategies.service` |
| Dynamic candidates (API) | `/research/candidates*` → discovery / health / advance |
| SHORT candidates (API) | `/research/short-candidates*` → `combo02_short_research_runner` |
| Trade-plan forensics (API) | `/research/trade-plan-forensics*` |
| OHLCV expand job (API) | `/research/ohlcv-expand*` → `ohlcv_expand.py` |
| Prepare Parquet dataset (CLI) | `scripts/prepare_research_dataset.py` |
| Research data pipeline (CLI) | `scripts/run_research_data_pipeline.py` |
| BOS strategy comparison (CLI) | `scripts/run_bos_strategy_comparison.py` |
| Candle12 V2 (CLI) | `scripts/run_candle12_v2_research.py` |
| COMBO_02 candidate batch (CLI) | `scripts/run_combo02_candidate_research.py` |
| Pipeline benchmark (CLI) | `scripts/benchmark_research_pipeline.py` |
| Live/backtest parity (CLI) | `scripts/live_backtest_parity/run_parity_validation.py` |

## Caches

| Cache | Owner | Key | Invalidated By |
|---|---|---|---|
| OHLCV Parquet | `data_cache/cache_manager.py` | `ohlcv_v1` + symbol/tf/range | version bump, force refresh, manifest mismatch |
| Feature Parquet | `data_cache/feature_cache.py` | `research_features_cache_v1` | feature version / data fingerprint |
| Event cache files | `data_cache/event_cache.py` | `research_events_bos_v1` | unused by engines today |
| PG research features | `data_pipeline/feature_store.py` | `FEATURE_VERSION` + fingerprint | fingerprint change |
| Service candle LRU | `service.py` | symbol:limit + tf | process restart |

## Backtests

| Backtest | Runner | Engine | Dataset |
|---|---|---|---|
| COMBO_* | `combination_backtest.py` | `combination_engine` + `SignalEngine` | PG / optional Parquet |
| Strategy comparison S* | `bos_strategy_comparison/runner.py` | lifecycle + engine | PG |
| Cache→COMBO benchmark | `data_cache/runner.py` | COMBO backtest | `PreparedResearchBundle` |
| Candle12 V2 | `candle12_v2.py` | rolling_structure + signals | PG |
| Multi-cap | `multi_cap_strategies/runner.py` | local gens + shared exits | PG |
| Candidate gates | `combo02_candidate_research.py` | `BosResearchService` | PG |
| Parity validation | `live_backtest_parity/runner.py` | replay / paper_sim | paper + OHLCV |

## Legacy / Experimental

Treat as research history unless a task explicitly requires them:

- **LEGACY:** `candle12_hypothesis.py`, `scripts/run_candle12_research.py`, `scripts/select_top_coin_candidates.py` (wrapper), `scripts/patch_klines_api.py`
- **UNUSED_CANDIDATE:** `data_cache/event_cache.py` (no engine/API importers)
- **EXPERIMENTAL packages:** `short_entry_research/`, `short_pullback_rejection/`, `short_research_diagnostics/`, `trade_plan_forensics/`, many `*_diagnostics.py` / `*_sensitivity.py` / forensic CLIs
- **FeatureCache caveat:** built by prepare/benchmark paths; COMBO engines still compute ATR via `engines.mtf.indicators`

Full lists: inventory JSON/MD + cleanup plan.

## Safe Modification Rules

- Preserve production logic under `app/signals/`, `app/engines/`, paper watchers, and Telegram paths
- No fake / synthetic market data in research conclusions
- No lookahead (HTF and features must be as-of bar time)
- Optimizations that can change results require equality gates (`golden_equality`, benchmark golden)
- Research-only experiments stay isolated from paper/Telegram/v1 arming
- Prefer adapters over copying production detectors
- Do not delete or archive research history without proving zero active dependencies and keeping reproducibility artifacts
- When two implementations overlap, choose authority from runtime imports → API → tests → integration → correctness → docs → git history — **not** file mtime alone
