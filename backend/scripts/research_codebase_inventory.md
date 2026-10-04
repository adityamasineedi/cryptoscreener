# Research Codebase Inventory

Audit-only inventory. No files were deleted, moved, or behavior-changed.

Generated machine-readable twin: `backend/scripts/research_codebase_inventory.json`.

## Summary counts

| Metric | Count |
|---|---:|
| research_python_files | 171 |
| research_related_scripts | 46 |
| research_related_tests | 53 |
| active_files | 156 |
| cli_entrypoints | 44 |
| duplicate_candidates | 14 |
| legacy_candidates | 3 |
| experimental_files | 59 |
| unused_candidates | 1 |
| unknown_files | 0 |
| wrapper_files | 15 |

### By classification

| Classification | Count |
|---|---:|
| EXPERIMENTAL | 59 |
| ACTIVE_TEST | 54 |
| CLI_ENTRYPOINT | 30 |
| ACTIVE_DATA_PIPELINE | 23 |
| ACTIVE_CORE | 22 |
| ACTIVE_BACKTEST | 20 |
| ACTIVE_STRATEGY | 16 |
| WRAPPER | 15 |
| ACTIVE_CACHE | 14 |
| ACTIVE_API | 7 |
| UTILITY | 6 |
| LEGACY | 3 |
| UNUSED_CANDIDATE | 1 |

## Authoritative pipelines (runtime)

### bos_combination

```
backend/app/api/routes.py → backend/app/research/service.py → backend/app/research/postgres_ohlcv.py | data_cache/cache_manager.py → backend/app/research/combination_backtest.py → backend/app/research/combination_engine.py → backend/app/signals/signal_engine.py → backend/app/research/metrics.py
```

### bos_strategy_comparison

```
backend/app/api/routes.py → backend/app/research/bos_strategy_comparison/service.py → backend/app/research/postgres_ohlcv.py → backend/app/research/bos_strategy_comparison/runner.py → backend/app/research/bos_strategy_comparison/lifecycle.py → backend/app/research/bos_strategy_comparison/engine.py → backend/app/signals/* engines → backend/app/research/bos_strategy_comparison/metrics.py
```

### research_cache_prepare

```
backend/scripts/prepare_research_dataset.py → backend/app/research/data_cache/prepare.py → backend/app/research/data_cache/cache_manager.py → backend/app/research/data_cache/postgres_loader.py → backend/app/research/postgres_ohlcv.py → backend/app/research/data_cache/feature_cache.py (optional; not consumed by COMBO engine) → backend/app/research/data_cache/runner.py -> combination_backtest
```

### data_pipeline_ingest

```
backend/scripts/run_research_data_pipeline.py → backend/app/research/data_pipeline/runner.py → backend/app/research/data_pipeline/downloader.py → backend/app/research/data_pipeline/feature_store.py → backend/app/research/data_pipeline/event_store.py
```

## Dependency graph (conceptual)

```
PostgreSQL OHLCV (postgres_ohlcv / data_pipeline.downloader ingest)
        │
        ├─► data_cache (optional Parquet OHLCV) ─► FeatureCache (ATR/RVOL/EMA; NOT consumed by COMBO engines)
        │         └─► EventCache (UNUSED by engines)
        │
        ├─► BosResearchService ─► combination_backtest ─► combination_engine ─► signals.SignalEngine
        │                              │
        │                              └─► research.metrics
        │
        ├─► BosStrategyComparisonService ─► lifecycle ─► engine/strategies ─► signals.*
        │                                         └─► bos_strategy_comparison.metrics
        │
        ├─► multi_cap_strategies (local generators + shared exit sim)
        ├─► candle12_v2 (rolling_structure + production bos/impulse/pullback)
        └─► candidate/paper gates ─► BosResearchService.strategy_matrix
```

## Duplicate candidates

### OHLCV PostgreSQL loading

- **FILE A:** `backend/app/research/postgres_ohlcv.py`
- **FILE B:** `backend/app/research/data_cache/postgres_loader.py`
- **Similarity:** HIGH
- **Authoritative:** A
- **Evidence:** postgres_loader only wraps load_ohlcv_series_range and adds cache metrics; all SQL lives in postgres_ohlcv.

### OHLCV ingest vs load

- **FILE A:** `backend/app/research/postgres_ohlcv.py`
- **FILE B:** `backend/app/research/data_pipeline/downloader.py`
- **Similarity:** LOW
- **Authoritative:** A for reads; B for Binance→PG writes
- **Evidence:** Different roles: read path vs ingest writer. Not merge candidates.

### Trade metrics

- **FILE A:** `backend/app/research/metrics.py`
- **FILE B:** `backend/app/research/bos_strategy_comparison/metrics.py`
- **Similarity:** MEDIUM
- **Authoritative:** A for core R metrics; B for strategy-comparison breakdowns
- **Evidence:** B imports expectancy_r/profit_factor/max_drawdown_r from A and adds net_R/cost sensitivity.

### Ops metrics naming collision

- **FILE A:** `backend/app/research/data_cache/metrics.py`
- **FILE B:** `backend/app/research/data_pipeline/metrics.py`
- **Similarity:** MEDIUM
- **Authoritative:** UNKNOWN
- **Evidence:** Both are timing/hit counters for different subsystems; not trade-metric forks.

### OHLCV data quality

- **FILE A:** `backend/app/research/data_quality.py`
- **FILE B:** `backend/app/research/bos_strategy_comparison/data_quality.py`
- **Similarity:** MEDIUM
- **Authoritative:** A
- **Evidence:** BOS comparison data_quality calls verify_ohlcv from A and adds universe/coverage audits.

### OHLCV validation (cache)

- **FILE A:** `backend/app/research/data_quality.py`
- **FILE B:** `backend/app/research/data_cache/validation.py`
- **Similarity:** MEDIUM
- **Authoritative:** A for semantic verify; B for cache schema/range checks wrapping A
- **Evidence:** validation.py delegates candle correctness to verify_ohlcv.

### Feature cache (Parquet vs PG)

- **FILE A:** `backend/app/research/data_cache/feature_cache.py`
- **FILE B:** `backend/app/research/data_pipeline/feature_store.py`
- **Similarity:** MEDIUM
- **Authoritative:** UNKNOWN
- **Evidence:** FeatureCache builds research ATR/RVOL/EMA parquet columns (not consumed by COMBO engines). feature_store persists production-engine-derived features in PG research_feature_cache.

### BOS event persistence

- **FILE A:** `backend/app/research/data_pipeline/event_store.py`
- **FILE B:** `backend/app/research/data_cache/event_cache.py`
- **Similarity:** HIGH
- **Authoritative:** A
- **Evidence:** EventCache wraps event_store builders but has zero engine/API importers; event_store is used by data_pipeline runner/feature_store/benchmarks.

### Candle12 research

- **FILE A:** `backend/app/research/candle12_v2.py`
- **FILE B:** `backend/app/research/candle12_hypothesis.py`
- **Similarity:** HIGH
- **Authoritative:** A
- **Evidence:** V2 docstring says it extends hypothesis; active CLI is run_candle12_v2_research.py; V1 script still calls hypothesis.

### Candidate selection CLI

- **FILE A:** `backend/scripts/select_combo02_candidates.py`
- **FILE B:** `backend/scripts/select_top_coin_candidates.py`
- **Similarity:** HIGH
- **Authoritative:** A
- **Evidence:** select_top_coin_candidates is documented back-compat runpy wrapper.

### HTF alignment

- **FILE A:** `backend/app/research/bos_strategy_comparison/htf.py`
- **FILE B:** `backend/app/signals/mtf_engine.py`
- **Similarity:** MEDIUM
- **Authoritative:** A for research hard-gate/as-of maps; production mtf_engine for live entry alignment
- **Evidence:** htf.py uses production swing/trend detectors but owns research as-of HTF maps + classify_htf_alignment used by combination_engine.

### Lifecycle sequencing

- **FILE A:** `backend/app/research/bos_strategy_comparison/lifecycle.py`
- **FILE B:** `backend/app/signals/signal_engine.py`
- **Similarity:** MEDIUM
- **Authoritative:** A for multi-bar research lifecycle; B for live/same-bar analyze_timeframe
- **Evidence:** lifecycle calls production pullback/retest/SignalEngine but sequences WAITING across bars — research-only owner.

### ATR calculation

- **FILE A:** `backend/app/engines/mtf/indicators.py`
- **FILE B:** `backend/app/research/data_cache/feature_cache.py`
- **Similarity:** MEDIUM
- **Authoritative:** A for strategy/backtest ATR
- **Evidence:** COMBO backtest uses engines.mtf.indicators.atr_series; FeatureCache has separate Wilder ATR not consumed by engines.

### Market-cap classification

- **FILE A:** `backend/app/research/historical_market_cap/classifier.py`
- **FILE B:** `backend/app/research/multi_cap_strategies/cap_filter.py`
- **Similarity:** MEDIUM
- **Authoritative:** UNKNOWN
- **Evidence:** Multi-cap API path uses live fundamentals via cap_filter; historical_market_cap used by offline scripts for time-aware HISTORICAL_CAP.

## Thin wrappers

| File | Recommendation |
|---|---|
| `backend/app/research/__init__.py` | KEEP |
| `backend/app/research/bos_strategy_comparison/__init__.py` | KEEP |
| `backend/app/research/bos_strategy_comparison/data_quality.py` | CONSOLIDATE |
| `backend/app/research/data_cache/__init__.py` | KEEP |
| `backend/app/research/data_cache/postgres_loader.py` | KEEP |
| `backend/app/research/data_pipeline/__init__.py` | KEEP |
| `backend/app/research/historical_market_cap/__init__.py` | KEEP |
| `backend/app/research/live_backtest_parity/__init__.py` | KEEP |
| `backend/app/research/multi_cap_strategies/__init__.py` | KEEP |
| `backend/app/research/multi_cap_strategies/adapter.py` | KEEP |
| `backend/app/research/short_entry_research/__init__.py` | KEEP |
| `backend/app/research/short_pullback_rejection/__init__.py` | KEEP |
| `backend/app/research/short_research_diagnostics/__init__.py` | KEEP |
| `backend/app/research/trade_plan_forensics/__init__.py` | KEEP |
| `backend/scripts/advance_dynamic_candidates.py` | CLI_ONLY |
| `backend/scripts/audit_combo02_htf_fidelity.py` | CLI_ONLY |
| `backend/scripts/audit_multi_cap_baseline_forensics.py` | CLI_ONLY |
| `backend/scripts/cap_classification_coverage.py` | CLI_ONLY |
| `backend/scripts/check_ohlcv_coverage.py` | CLI_ONLY |
| `backend/scripts/expand_ohlcv_history.py` | CLI_ONLY |
| `backend/scripts/live_backtest_parity/run_parity_validation.py` | CLI_ONLY |
| `backend/scripts/prepare_research_dataset.py` | CLI_ONLY |
| `backend/scripts/run_bos_strategy_comparison.py` | CLI_ONLY |
| `backend/scripts/run_candle12_v2_research.py` | CLI_ONLY |
| `backend/scripts/run_combo02_candidate_research.py` | CLI_ONLY |
| `backend/scripts/run_combo02_short_diagnostics.py` | CLI_ONLY |
| `backend/scripts/run_combo02_short_entry_research.py` | CLI_ONLY |
| `backend/scripts/run_combo02_short_research.py` | CLI_ONLY |
| `backend/scripts/run_dynamic_candidate_discovery.py` | CLI_ONLY |
| `backend/scripts/run_failure_regime_decomposition.py` | CLI_ONLY |
| `backend/scripts/run_hl_lh_long_short.py` | CLI_ONLY |
| `backend/scripts/run_htf_alignment_sensitivity.py` | CLI_ONLY |
| `backend/scripts/run_impulse_pullback_hl_lh.py` | CLI_ONLY |
| `backend/scripts/run_impulse_pullback_hl_lh_local.py` | CLI_ONLY |
| `backend/scripts/run_multi_cap_full_research.py` | CLI_ONLY |
| `backend/scripts/run_research_data_pipeline.py` | CLI_ONLY |
| `backend/scripts/run_short_policy_sensitivity.py` | CLI_ONLY |
| `backend/scripts/run_short_pullback_rejection_research.py` | CLI_ONLY |
| `backend/scripts/run_trend_regime_diagnostic.py` | CLI_ONLY |
| `backend/scripts/select_combo02_candidates.py` | CLI_ONLY |
| `backend/scripts/select_top_coin_candidates.py` | LEGACY_CANDIDATE |
| `backend/scripts/v1_monitor_paper.py` | CLI_ONLY |
| `backend/scripts/validate_multi_cap_strategies.py` | CLI_ONLY |
| `backend/scripts/verify_coverage.py` | CLI_ONLY |
| `backend/scripts/verify_coverage_phase.py` | CLI_ONLY |

## File catalog

### research

#### `backend/app/research/__init__.py`

- **Classification:** WRAPPER
- **Imported by:** `backend/app/research/service.py`, `backend/tests/test_dynamic_candidate_pipeline.py`, `backend/tests/test_short_pullback_rejection_research.py`, `backend/tests/test_short_research_integrity.py`
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_dynamic_candidate_pipeline.py`, `backend/tests/test_short_pullback_rejection_research.py`, `backend/tests/test_short_research_integrity.py`
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Wrapper action:** KEEP
- **Safe to archive:** NO
- **Reason:** thin re-export or pass-through without unique business logic

#### `backend/app/research/advance_dynamic_candidates.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/api/routes.py`, `backend/scripts/advance_dynamic_candidates.py`, `backend/tests/test_dynamic_candidate_pipeline.py`
- **Imports / protects:** `backend/app/research/candidate_data_health.py`, `backend/app/research/dynamic_candidate_constants.py`, `backend/app/research/dynamic_candidate_pipeline.py`, `backend/app/research/strategy_candidate_registry.py`
- **CLI usage:** yes
- **API route:** yes
- **Test coverage:** `backend/tests/test_dynamic_candidate_pipeline.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/backtest_job.py`

- **Classification:** ACTIVE_API
- **Imported by:** `backend/app/api/routes.py`, `backend/app/diagnostics/collectors/jobs.py`, `backend/app/diagnostics/health_eval.py`, `backend/app/research/trade_plan_forensics/ingest.py`, `backend/tests/test_backtest_job.py`, `backend/tests/test_backtest_job_progress.py`, `backend/tests/test_backtest_ui_config.py`
- **Imports / protects:** `backend/app/research/backtest_timing.py`, `backend/app/research/backtest_ui_config.py`, `backend/app/research/query_utils.py`, `backend/app/research/service.py`, `backend/app/research/v1_production.py`
- **CLI usage:** indirect
- **API route:** yes
- **Test coverage:** `backend/tests/test_backtest_job.py`, `backend/tests/test_backtest_job_progress.py`, `backend/tests/test_backtest_ui_config.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** wired to FastAPI research routes or job services

#### `backend/app/research/backtest_timing.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/backtest_job.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/service.py`, `backend/scripts/diagnose_backtest_15m.py`, `backend/tests/test_backtest_job_progress.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_backtest_job_progress.py`
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/backtest_ui_config.py`

- **Classification:** ACTIVE_API
- **Imported by:** `backend/app/api/routes.py`, `backend/app/research/backtest_job.py`, `backend/tests/test_backtest_ui_config.py`
- **Imports / protects:** `backend/app/research/v1_production.py`
- **CLI usage:** indirect
- **API route:** yes
- **Test coverage:** `backend/tests/test_backtest_ui_config.py`
- **Safe to archive:** NO
- **Reason:** wired to FastAPI research routes or job services

#### `backend/app/research/bos_combinations.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/app/research/__init__.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/combination_engine.py`, `backend/app/research/combo02_candidate_research.py`, `backend/app/research/data_cache/runner.py`, `backend/app/research/live_backtest_parity/replay.py`, `backend/app/research/service.py`, `backend/app/research/short_entry_research/runner.py` (+20 more)
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_bos_research.py`, `backend/tests/test_bos_research_compare_api.py`, `backend/tests/test_combo02_candidate_eligibility.py`, `backend/tests/test_combo02_htf_gate.py`, `backend/tests/test_v1_paper_backtest_parity.py`, `backend/tests/test_v1_paper_watcher.py`
- **Last modification:** 2026-10-03T20:45:53+05:30 `8f88377` Freeze COMBO_02 v1 HTF-gated LONG (tag candidate).
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/bos_strategy_comparison/__init__.py`

- **Classification:** WRAPPER
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/strategies.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T08:18:03+05:30 `e3cc5fc` Add BOS strategy comparison research suite without changing live signals.
- **Wrapper action:** KEEP
- **Safe to archive:** NO
- **Reason:** thin re-export or pass-through without unique business logic

#### `backend/app/research/bos_strategy_comparison/config.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/bos_strategy_comparison/data_quality.py`, `backend/app/research/bos_strategy_comparison/diagnostics.py`, `backend/app/research/bos_strategy_comparison/engine.py`, `backend/app/research/bos_strategy_comparison/lifecycle.py`, `backend/app/research/bos_strategy_comparison/metrics.py`, `backend/app/research/bos_strategy_comparison/pullback_diagnostics.py`, `backend/app/research/bos_strategy_comparison/runner.py`, `backend/app/research/bos_strategy_comparison/s3_diagnostics.py` (+14 more)
- **Imports / protects:** `backend/app/research/config.py`, `backend/app/research/trade_fees.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_backtest_lifecycle_integration.py`, `backend/tests/test_bos_strategy_comparison.py`, `backend/tests/test_history_coverage_diagnostics.py`, `backend/tests/test_lifecycle.py`, `backend/tests/test_pullback_diagnostics.py`, `backend/tests/test_s3_diagnostics.py` ...
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/bos_strategy_comparison/data_quality.py`

- **Classification:** WRAPPER
- **Imported by:** `backend/app/research/bos_strategy_comparison/runner.py`, `backend/app/research/bos_strategy_comparison/service.py`, `backend/app/research/data_pipeline/history_coverage.py`, `backend/tests/test_bos_strategy_comparison.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/data_quality.py`, `backend/app/research/postgres_ohlcv.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_bos_strategy_comparison.py`
- **Last modification:** 2026-10-03T08:18:03+05:30 `e3cc5fc` Add BOS strategy comparison research suite without changing live signals.
- **Wrapper action:** CONSOLIDATE
- **Safe to archive:** NO
- **Reason:** thin re-export or pass-through without unique business logic

#### `backend/app/research/bos_strategy_comparison/diagnostics.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/bos_strategy_comparison/service.py`, `backend/tests/test_history_coverage_diagnostics.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/engine.py`, `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/bos_strategy_comparison/strategies.py`, `backend/app/research/combination_engine.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_history_coverage_diagnostics.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/bos_strategy_comparison/engine.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/bos_strategy_comparison/diagnostics.py`, `backend/app/research/bos_strategy_comparison/lifecycle.py`, `backend/app/research/bos_strategy_comparison/pullback_diagnostics.py`, `backend/app/research/bos_strategy_comparison/runner.py`, `backend/app/research/bos_strategy_comparison/s3_diagnostics.py`, `backend/app/research/bos_strategy_comparison/s3_htf_sensitivity.py`, `backend/app/research/data_pipeline/event_store.py`, `backend/tests/test_bos_strategy_comparison.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/bos_strategy_comparison/strategies.py`, `backend/app/research/combination_engine.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_bos_strategy_comparison.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/bos_strategy_comparison/htf.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/research/bos_strategy_comparison/diagnostics.py`, `backend/app/research/bos_strategy_comparison/engine.py`, `backend/app/research/bos_strategy_comparison/htf_forensics.py`, `backend/app/research/bos_strategy_comparison/htf_variants.py`, `backend/app/research/bos_strategy_comparison/pullback_diagnostics.py`, `backend/app/research/bos_strategy_comparison/runner.py`, `backend/app/research/bos_strategy_comparison/s3_diagnostics.py`, `backend/app/research/bos_strategy_comparison/s3_htf_sensitivity.py` (+13 more)
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_backtest_job_progress.py`, `backend/tests/test_bos_strategy_comparison.py`, `backend/tests/test_combo02_short_research.py`, `backend/tests/test_htf_forensics.py`, `backend/tests/test_research_pipeline_perf.py`, `backend/tests/test_s3_diagnostics.py` ...
- **Last modification:** 2026-10-03T08:18:03+05:30 `e3cc5fc` Add BOS strategy comparison research suite without changing live signals.
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/bos_strategy_comparison/htf_forensics.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/bos_strategy_comparison/s3_diagnostics.py`, `backend/app/research/bos_strategy_comparison/s3_htf_sensitivity.py`, `backend/tests/test_htf_forensics.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/htf.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_htf_forensics.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/bos_strategy_comparison/htf_variants.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/bos_strategy_comparison/s3_htf_sensitivity.py`, `backend/app/research/bos_strategy_comparison/service.py`, `backend/tests/test_s3_htf_sensitivity.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/htf.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_s3_htf_sensitivity.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/bos_strategy_comparison/lifecycle.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/bos_strategy_comparison/runner.py`, `backend/app/research/bos_strategy_comparison/s3_diagnostics.py`, `backend/app/research/bos_strategy_comparison/s3_htf_sensitivity.py`, `backend/app/research/bos_strategy_comparison/service.py`, `backend/app/research/data_pipeline/event_store.py`, `backend/scripts/benchmark_research_pipeline.py`, `backend/scripts/profile_strategy_evaluation.py`, `backend/tests/test_backtest_lifecycle_integration.py` (+2 more)
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/engine.py`, `backend/app/research/combination_engine.py`, `backend/app/research/data_cache/stage_profiler.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_backtest_lifecycle_integration.py`, `backend/tests/test_lifecycle.py`, `backend/tests/test_s3_diagnostics.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/bos_strategy_comparison/metrics.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/bos_strategy_comparison/runner.py`, `backend/app/research/bos_strategy_comparison/s3_htf_sensitivity.py`, `backend/tests/test_bos_strategy_comparison.py`, `backend/tests/test_s3_htf_sensitivity.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/schemas.py`, `backend/app/research/metrics.py`, `backend/app/research/trade_fees.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_bos_strategy_comparison.py`, `backend/tests/test_s3_htf_sensitivity.py`
- **Last modification:** 2026-10-03T08:18:03+05:30 `e3cc5fc` Add BOS strategy comparison research suite without changing live signals.
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/bos_strategy_comparison/pullback_diagnostics.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/bos_strategy_comparison/service.py`, `backend/tests/test_pullback_diagnostics.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/engine.py`, `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/combination_engine.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_pullback_diagnostics.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/bos_strategy_comparison/repository.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/bos_strategy_comparison/service.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-03T08:18:03+05:30 `e3cc5fc` Add BOS strategy comparison research suite without changing live signals.
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/bos_strategy_comparison/runner.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/bos_strategy_comparison/s3_htf_sensitivity.py`, `backend/app/research/bos_strategy_comparison/service.py`, `backend/scripts/benchmark_research_pipeline.py`, `backend/tests/test_backtest_lifecycle_integration.py`, `backend/tests/test_bos_strategy_comparison.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/data_quality.py`, `backend/app/research/bos_strategy_comparison/engine.py`, `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/bos_strategy_comparison/lifecycle.py`, `backend/app/research/bos_strategy_comparison/metrics.py`, `backend/app/research/bos_strategy_comparison/schemas.py`, `backend/app/research/bos_strategy_comparison/strategies.py` (+3 more)
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_backtest_lifecycle_integration.py`, `backend/tests/test_bos_strategy_comparison.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/bos_strategy_comparison/s3_diagnostics.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/bos_strategy_comparison/service.py`, `backend/tests/test_htf_forensics.py`, `backend/tests/test_s3_diagnostics.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/engine.py`, `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/bos_strategy_comparison/htf_forensics.py`, `backend/app/research/bos_strategy_comparison/lifecycle.py`, `backend/app/research/bos_strategy_comparison/strategies.py`, `backend/app/research/combination_engine.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_htf_forensics.py`, `backend/tests/test_s3_diagnostics.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/bos_strategy_comparison/s3_htf_sensitivity.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/bos_strategy_comparison/service.py`, `backend/tests/test_s3_htf_sensitivity.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/engine.py`, `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/bos_strategy_comparison/htf_forensics.py`, `backend/app/research/bos_strategy_comparison/htf_variants.py`, `backend/app/research/bos_strategy_comparison/lifecycle.py`, `backend/app/research/bos_strategy_comparison/metrics.py`, `backend/app/research/bos_strategy_comparison/runner.py` (+4 more)
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_s3_htf_sensitivity.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/bos_strategy_comparison/schemas.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/bos_strategy_comparison/metrics.py`, `backend/app/research/bos_strategy_comparison/runner.py`, `backend/app/research/bos_strategy_comparison/s3_htf_sensitivity.py`, `backend/tests/test_bos_strategy_comparison.py`, `backend/tests/test_s3_htf_sensitivity.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_bos_strategy_comparison.py`, `backend/tests/test_s3_htf_sensitivity.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/bos_strategy_comparison/service.py`

- **Classification:** ACTIVE_API
- **Imported by:** `backend/app/api/routes.py`, `backend/scripts/run_bos_strategy_comparison.py`, `backend/scripts/run_s3_forensic_sep2024.py`, `backend/scripts/run_s3_htf_sensitivity.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/data_quality.py`, `backend/app/research/bos_strategy_comparison/diagnostics.py`, `backend/app/research/bos_strategy_comparison/htf_variants.py`, `backend/app/research/bos_strategy_comparison/lifecycle.py`, `backend/app/research/bos_strategy_comparison/pullback_diagnostics.py`, `backend/app/research/bos_strategy_comparison/repository.py`, `backend/app/research/bos_strategy_comparison/runner.py` (+6 more)
- **CLI usage:** yes
- **API route:** yes
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** wired to FastAPI research routes or job services

#### `backend/app/research/bos_strategy_comparison/strategies.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/app/research/bos_strategy_comparison/__init__.py`, `backend/app/research/bos_strategy_comparison/diagnostics.py`, `backend/app/research/bos_strategy_comparison/engine.py`, `backend/app/research/bos_strategy_comparison/runner.py`, `backend/app/research/bos_strategy_comparison/s3_diagnostics.py`, `backend/app/research/bos_strategy_comparison/s3_htf_sensitivity.py`, `backend/app/research/bos_strategy_comparison/service.py`, `backend/tests/test_backtest_lifecycle_integration.py` (+2 more)
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_backtest_lifecycle_integration.py`, `backend/tests/test_bos_strategy_comparison.py`, `backend/tests/test_multi_cap_strategies.py`
- **Last modification:** 2026-10-03T08:18:03+05:30 `e3cc5fc` Add BOS strategy comparison research suite without changing live signals.
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/candidate_data_health.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/api/routes.py`, `backend/app/research/advance_dynamic_candidates.py`, `backend/tests/test_dynamic_candidate_pipeline.py`
- **Imports / protects:** `backend/app/research/combo02_candidate_thresholds.py`, `backend/app/research/ohlcv_expand.py`, `backend/app/research/strategy_candidate_registry.py`
- **CLI usage:** indirect
- **API route:** yes
- **Test coverage:** `backend/tests/test_dynamic_candidate_pipeline.py`
- **Last modification:** 2026-10-04T02:38:38+05:30 `04a30ce` Separate Futures Screener discovery UI from COMBO_02 v1 eligibility.
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/candidate_state_machine.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/api/routes.py`, `backend/app/research/dynamic_candidate_pipeline.py`, `backend/app/research/strategy_candidate_registry.py`, `backend/tests/test_combo02_short_research.py`, `backend/tests/test_dynamic_candidate_pipeline.py`, `backend/tests/test_e2e_paper_candidate_safety_validation.py`
- **CLI usage:** indirect
- **API route:** yes
- **Test coverage:** `backend/tests/test_combo02_short_research.py`, `backend/tests/test_dynamic_candidate_pipeline.py`, `backend/tests/test_e2e_paper_candidate_safety_validation.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/candle12_hypothesis.py`

- **Classification:** LEGACY
- **Imported by:** `backend/scripts/run_candle12_research.py`
- **Imports / protects:** `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`, `backend/app/research/data_quality.py`, `backend/app/research/metrics.py`, `backend/app/research/schemas.py`
- **CLI usage:** yes
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Experimental bucket:** LEGACY_CANDIDATE
- **Safe to archive:** NO
- **Reason:** superseded by a newer implementation but still present

#### `backend/app/research/candle12_v2.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/scripts/run_candle12_v2_research.py`, `backend/tests/test_candle12_v2.py`
- **Imports / protects:** `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`, `backend/app/research/data_quality.py`, `backend/app/research/metrics.py`, `backend/app/research/rolling_structure.py`, `backend/app/research/schemas.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_candle12_v2.py`
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/combination_backtest.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/__init__.py`, `backend/app/research/bos_strategy_comparison/runner.py`, `backend/app/research/candle12_hypothesis.py`, `backend/app/research/candle12_v2.py`, `backend/app/research/data_cache/runner.py`, `backend/app/research/multi_cap_strategies/adapter.py`, `backend/app/research/multi_cap_strategies/runner.py`, `backend/app/research/service.py` (+26 more)
- **Imports / protects:** `backend/app/research/backtest_timing.py`, `backend/app/research/bos_combinations.py`, `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/combination_engine.py`, `backend/app/research/config.py`, `backend/app/research/data_cache/stage_profiler.py`, `backend/app/research/data_quality.py`, `backend/app/research/metrics.py` (+2 more)
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_backtest_job_progress.py`, `backend/tests/test_backtest_lifecycle_integration.py`, `backend/tests/test_bos_research.py`, `backend/tests/test_bos_research_compare_api.py`, `backend/tests/test_bos_strategy_comparison.py`, `backend/tests/test_combo02_htf_gate.py` ...
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/combination_engine.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/app/research/bos_strategy_comparison/diagnostics.py`, `backend/app/research/bos_strategy_comparison/engine.py`, `backend/app/research/bos_strategy_comparison/lifecycle.py`, `backend/app/research/bos_strategy_comparison/pullback_diagnostics.py`, `backend/app/research/bos_strategy_comparison/runner.py`, `backend/app/research/bos_strategy_comparison/s3_diagnostics.py`, `backend/app/research/bos_strategy_comparison/s3_htf_sensitivity.py`, `backend/app/research/combination_backtest.py` (+12 more)
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/config.py`, `backend/app/research/data_cache/stage_profiler.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_bos_research.py`, `backend/tests/test_combo02_htf_gate.py`, `backend/tests/test_s3_diagnostics.py`, `backend/tests/test_v1_paper_watcher.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/combo02_candidate_eligibility.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/research/combo02_candidate_research.py`, `backend/app/research/combo02_short_research.py`, `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/dynamic_candidate_pipeline.py`, `backend/tests/test_combo02_candidate_eligibility.py`
- **Imports / protects:** `backend/app/research/combo02_candidate_thresholds.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_candidate_eligibility.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/combo02_candidate_research.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/combo02_short_research.py`, `backend/app/research/dynamic_candidate_pipeline.py`, `backend/app/research/short_entry_research/validation.py`, `backend/app/research/short_research_diagnostics/ablation.py`, `backend/app/research/short_research_diagnostics/runner.py`, `backend/scripts/run_combo02_candidate_research.py`, `backend/tests/test_combo02_candidate_eligibility.py`, `backend/tests/test_short_pullback_rejection_research.py`
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combo02_candidate_eligibility.py`, `backend/app/research/combo02_candidate_selector.py`, `backend/app/research/combo02_candidate_thresholds.py`, `backend/app/research/config.py`, `backend/app/research/service.py`, `backend/app/research/trade_fees.py`, `backend/app/research/v1_production.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_candidate_eligibility.py`, `backend/tests/test_short_pullback_rejection_research.py`
- **Last modification:** 2026-10-04T01:52:09+05:30 `69adc60` code update
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/combo02_candidate_selector.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/research/combo02_candidate_research.py`, `backend/app/research/dynamic_candidate_discovery.py`, `backend/scripts/run_combo02_candidate_research.py`, `backend/scripts/select_combo02_candidates.py`, `backend/tests/test_combo02_candidate_eligibility.py`
- **Imports / protects:** `backend/app/research/combo02_candidate_thresholds.py`, `backend/app/research/v1_production.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_candidate_eligibility.py`
- **Last modification:** 2026-10-04T01:52:09+05:30 `69adc60` code update
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/combo02_candidate_thresholds.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/research/candidate_data_health.py`, `backend/app/research/combo02_candidate_eligibility.py`, `backend/app/research/combo02_candidate_research.py`, `backend/app/research/combo02_candidate_selector.py`, `backend/app/research/combo02_short_research.py`, `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/dynamic_candidate_discovery.py`, `backend/app/research/dynamic_candidate_pipeline.py` (+7 more)
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_candidate_eligibility.py`, `backend/tests/test_combo02_short_research_api.py`, `backend/tests/test_combo02_short_research_forensics.py`, `backend/tests/test_combo02_short_research_quality.py`
- **Last modification:** 2026-10-04T01:52:09+05:30 `69adc60` code update
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/combo02_short_research.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/app/api/routes.py`, `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/short_research_diagnostics/implementation_verify.py`, `backend/app/services/paper_trade.py`, `backend/tests/test_combo02_short_diagnostics.py`, `backend/tests/test_combo02_short_research.py`, `backend/tests/test_combo02_short_research_api.py`, `backend/tests/test_combo02_short_research_forensics.py` (+5 more)
- **Imports / protects:** `backend/app/research/combo02_candidate_eligibility.py`, `backend/app/research/combo02_candidate_research.py`, `backend/app/research/combo02_candidate_thresholds.py`, `backend/app/research/short_research_constants.py`, `backend/app/research/short_research_forensics.py`, `backend/app/research/trade_fees.py`
- **CLI usage:** indirect
- **API route:** yes
- **Test coverage:** `backend/tests/test_combo02_short_diagnostics.py`, `backend/tests/test_combo02_short_research.py`, `backend/tests/test_combo02_short_research_api.py`, `backend/tests/test_combo02_short_research_forensics.py`, `backend/tests/test_combo02_short_research_prepaper_review.py`, `backend/tests/test_combo02_short_research_quality.py` ...
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/combo02_short_research_runner.py`

- **Classification:** ACTIVE_API
- **Imported by:** `backend/app/api/routes.py`, `backend/app/research/short_entry_research/runner.py`, `backend/scripts/run_combo02_short_research.py`, `backend/tests/test_combo02_short_research_api.py`, `backend/tests/test_combo02_short_research_forensics.py`, `backend/tests/test_combo02_short_research_prepaper_review.py`, `backend/tests/test_combo02_short_research_quality.py`, `backend/tests/test_combo02_short_research_real_batch.py`
- **Imports / protects:** `backend/app/research/combo02_candidate_eligibility.py`, `backend/app/research/combo02_candidate_thresholds.py`, `backend/app/research/combo02_short_research.py`, `backend/app/research/config.py`, `backend/app/research/postgres_ohlcv.py`, `backend/app/research/query_utils.py`, `backend/app/research/service.py`, `backend/app/research/short_research_constants.py` (+5 more)
- **CLI usage:** yes
- **API route:** yes
- **Test coverage:** `backend/tests/test_combo02_short_research_api.py`, `backend/tests/test_combo02_short_research_forensics.py`, `backend/tests/test_combo02_short_research_prepaper_review.py`, `backend/tests/test_combo02_short_research_quality.py`, `backend/tests/test_combo02_short_research_real_batch.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** wired to FastAPI research routes or job services

#### `backend/app/research/config.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/research/__init__.py`, `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/runner.py`, `backend/app/research/bos_strategy_comparison/s3_htf_sensitivity.py`, `backend/app/research/candle12_hypothesis.py`, `backend/app/research/candle12_v2.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/combination_engine.py` (+43 more)
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_backtest_lifecycle_integration.py`, `backend/tests/test_bos_research.py`, `backend/tests/test_bos_research_compare_api.py`, `backend/tests/test_bos_strategy_comparison.py`, `backend/tests/test_candle12_v2.py`, `backend/tests/test_combo02_htf_gate.py` ...
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/data_cache/__init__.py`

- **Classification:** WRAPPER
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/data_cache/cache_manager.py`, `backend/app/research/data_cache/config.py`, `backend/app/research/data_cache/dataset.py`, `backend/app/research/data_cache/prepare.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Wrapper action:** KEEP
- **Safe to archive:** NO
- **Reason:** thin re-export or pass-through without unique business logic

#### `backend/app/research/data_cache/cache_key.py`

- **Classification:** ACTIVE_CACHE
- **Imported by:** `backend/app/research/data_cache/cache_manager.py`, `backend/app/research/data_cache/event_cache.py`, `backend/app/research/data_cache/feature_cache.py`, `backend/tests/test_research_data_cache.py`, `backend/tests/test_research_pipeline_perf.py`
- **Imports / protects:** `backend/app/research/query_utils.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_cache.py`, `backend/tests/test_research_pipeline_perf.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** research parquet/memory cache stack

#### `backend/app/research/data_cache/cache_manager.py`

- **Classification:** ACTIVE_CACHE
- **Imported by:** `backend/app/research/data_cache/__init__.py`, `backend/app/research/data_cache/prepare.py`, `backend/app/research/service.py`, `backend/tests/test_research_pipeline_perf.py`
- **Imports / protects:** `backend/app/research/data_cache/cache_key.py`, `backend/app/research/data_cache/config.py`, `backend/app/research/data_cache/dataset.py`, `backend/app/research/data_cache/manifest.py`, `backend/app/research/data_cache/metrics.py`, `backend/app/research/data_cache/parquet_store.py`, `backend/app/research/data_cache/postgres_loader.py`, `backend/app/research/data_cache/validation.py` (+1 more)
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_pipeline_perf.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** research parquet/memory cache stack

#### `backend/app/research/data_cache/checkpoint.py`

- **Classification:** ACTIVE_CACHE
- **Imported by:** `backend/app/research/data_cache/runner.py`, `backend/tests/test_research_data_cache.py`
- **Imports / protects:** `backend/app/research/data_cache/config.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_cache.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** research parquet/memory cache stack

#### `backend/app/research/data_cache/config.py`

- **Classification:** ACTIVE_CACHE
- **Imported by:** `backend/app/research/data_cache/__init__.py`, `backend/app/research/data_cache/cache_manager.py`, `backend/app/research/data_cache/checkpoint.py`, `backend/app/research/data_cache/event_cache.py`, `backend/app/research/data_cache/feature_cache.py`, `backend/app/research/data_cache/prepare.py`, `backend/app/research/data_cache/runner.py`, `backend/app/research/service.py` (+4 more)
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_cache.py`, `backend/tests/test_research_pipeline_perf.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** research parquet/memory cache stack

#### `backend/app/research/data_cache/dataset.py`

- **Classification:** ACTIVE_CACHE
- **Imported by:** `backend/app/research/data_cache/__init__.py`, `backend/app/research/data_cache/cache_manager.py`, `backend/app/research/data_cache/event_cache.py`, `backend/app/research/data_cache/feature_cache.py`, `backend/app/research/data_cache/memory_loader.py`, `backend/app/research/data_cache/prepare.py`, `backend/app/research/data_cache/runner.py`, `backend/scripts/benchmark_research_pipeline.py` (+1 more)
- **Imports / protects:** `backend/app/research/data_cache/parquet_store.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_cache.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** research parquet/memory cache stack

#### `backend/app/research/data_cache/event_cache.py`

- **Classification:** UNUSED_CANDIDATE
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/data_cache/cache_key.py`, `backend/app/research/data_cache/config.py`, `backend/app/research/data_cache/dataset.py`, `backend/app/research/data_cache/metrics.py`, `backend/app/research/data_pipeline/config.py`, `backend/app/research/data_pipeline/event_store.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** YES
- **Reason:** no runtime importers found outside self-definition

#### `backend/app/research/data_cache/feature_cache.py`

- **Classification:** ACTIVE_CACHE
- **Imported by:** `backend/app/research/data_cache/prepare.py`, `backend/scripts/benchmark_research_pipeline.py`, `backend/tests/test_research_data_cache.py`
- **Imports / protects:** `backend/app/research/data_cache/cache_key.py`, `backend/app/research/data_cache/config.py`, `backend/app/research/data_cache/dataset.py`, `backend/app/research/data_cache/metrics.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_cache.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** research parquet/memory cache stack

#### `backend/app/research/data_cache/golden_equality.py`

- **Classification:** ACTIVE_CACHE
- **Imported by:** `backend/scripts/benchmark_research_pipeline.py`, `backend/scripts/profile_strategy_evaluation.py`, `backend/tests/test_research_pipeline_perf.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_research_pipeline_perf.py`
- **Safe to archive:** NO
- **Reason:** research parquet/memory cache stack

#### `backend/app/research/data_cache/manifest.py`

- **Classification:** ACTIVE_CACHE
- **Imported by:** `backend/app/research/data_cache/cache_manager.py`, `backend/tests/test_research_data_cache.py`, `backend/tests/test_research_pipeline_perf.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_cache.py`, `backend/tests/test_research_pipeline_perf.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** research parquet/memory cache stack

#### `backend/app/research/data_cache/memory_loader.py`

- **Classification:** ACTIVE_CACHE
- **Imported by:** `backend/app/research/short_research_diagnostics/runner.py`
- **Imports / protects:** `backend/app/research/data_cache/dataset.py`, `backend/app/research/data_cache/parquet_store.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** research parquet/memory cache stack

#### `backend/app/research/data_cache/metrics.py`

- **Classification:** ACTIVE_CACHE
- **Imported by:** `backend/app/research/data_cache/cache_manager.py`, `backend/app/research/data_cache/event_cache.py`, `backend/app/research/data_cache/feature_cache.py`, `backend/app/research/data_cache/postgres_loader.py`, `backend/app/research/data_cache/prepare.py`, `backend/app/research/data_cache/runner.py`, `backend/scripts/benchmark_research_pipeline.py`, `backend/tests/test_research_data_cache.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_cache.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** research parquet/memory cache stack

#### `backend/app/research/data_cache/parquet_store.py`

- **Classification:** ACTIVE_CACHE
- **Imported by:** `backend/app/research/data_cache/cache_manager.py`, `backend/app/research/data_cache/dataset.py`, `backend/app/research/data_cache/memory_loader.py`, `backend/tests/test_research_data_cache.py`, `backend/tests/test_research_pipeline_perf.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_cache.py`, `backend/tests/test_research_pipeline_perf.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** research parquet/memory cache stack

#### `backend/app/research/data_cache/postgres_loader.py`

- **Classification:** WRAPPER
- **Imported by:** `backend/app/research/data_cache/cache_manager.py`, `backend/scripts/benchmark_research_pipeline.py`
- **Imports / protects:** `backend/app/research/data_cache/metrics.py`, `backend/app/research/postgres_ohlcv.py`, `backend/app/research/query_utils.py`
- **CLI usage:** yes
- **API route:** no
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Wrapper action:** KEEP
- **Safe to archive:** NO
- **Reason:** thin re-export or pass-through without unique business logic

#### `backend/app/research/data_cache/prepare.py`

- **Classification:** ACTIVE_CACHE
- **Imported by:** `backend/app/research/data_cache/__init__.py`, `backend/scripts/benchmark_research_pipeline.py`, `backend/scripts/prepare_research_dataset.py`
- **Imports / protects:** `backend/app/research/data_cache/cache_manager.py`, `backend/app/research/data_cache/config.py`, `backend/app/research/data_cache/dataset.py`, `backend/app/research/data_cache/feature_cache.py`, `backend/app/research/data_cache/metrics.py`, `backend/app/research/query_utils.py`
- **CLI usage:** yes
- **API route:** no
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** research parquet/memory cache stack

#### `backend/app/research/data_cache/runner.py`

- **Classification:** ACTIVE_CACHE
- **Imported by:** `backend/scripts/benchmark_research_pipeline.py`
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`, `backend/app/research/data_cache/checkpoint.py`, `backend/app/research/data_cache/config.py`, `backend/app/research/data_cache/dataset.py`, `backend/app/research/data_cache/metrics.py`
- **CLI usage:** yes
- **API route:** no
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** research parquet/memory cache stack

#### `backend/app/research/data_cache/stage_profiler.py`

- **Classification:** UTILITY
- **Imported by:** `backend/app/research/bos_strategy_comparison/lifecycle.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/combination_engine.py`, `backend/scripts/profile_strategy_evaluation.py`, `backend/tests/test_stage_profiler.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_stage_profiler.py`
- **Safe to archive:** NO
- **Reason:** helpers/constants with no standalone product surface

#### `backend/app/research/data_cache/validation.py`

- **Classification:** ACTIVE_CACHE
- **Imported by:** `backend/app/research/data_cache/cache_manager.py`, `backend/tests/test_research_data_cache.py`, `backend/tests/test_research_pipeline_perf.py`
- **Imports / protects:** `backend/app/research/data_quality.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_cache.py`, `backend/tests/test_research_pipeline_perf.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** research parquet/memory cache stack

#### `backend/app/research/data_pipeline/__init__.py`

- **Classification:** WRAPPER
- **Imported by:** `backend/app/research/data_pipeline/downloader.py`, `backend/app/research/data_pipeline/feature_store.py`, `backend/app/research/data_pipeline/runner.py`, `backend/app/research/data_pipeline/sync.py`, `backend/scripts/run_research_data_pipeline.py`, `backend/tests/test_research_data_pipeline.py`
- **Imports / protects:** `backend/app/research/data_pipeline/config.py`, `backend/app/research/data_pipeline/coverage.py`, `backend/app/research/data_pipeline/history_coverage.py`, `backend/app/research/data_pipeline/planner.py`, `backend/app/research/data_pipeline/runner.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_pipeline.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Wrapper action:** KEEP
- **Safe to archive:** NO
- **Reason:** thin re-export or pass-through without unique business logic

#### `backend/app/research/data_pipeline/checkpoint.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_pipeline/downloader.py`, `backend/app/research/data_pipeline/manifest.py`, `backend/app/research/data_pipeline/repository.py`, `backend/app/research/data_pipeline/sync.py`, `backend/tests/test_research_data_pipeline.py`
- **Imports / protects:** `backend/app/research/data_pipeline/config.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_pipeline.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/config.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_cache/event_cache.py`, `backend/app/research/data_pipeline/__init__.py`, `backend/app/research/data_pipeline/checkpoint.py`, `backend/app/research/data_pipeline/downloader.py`, `backend/app/research/data_pipeline/event_store.py`, `backend/app/research/data_pipeline/feature_store.py`, `backend/app/research/data_pipeline/history_coverage.py`, `backend/app/research/data_pipeline/manifest.py` (+5 more)
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_pipeline.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/coverage.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_pipeline/__init__.py`, `backend/app/research/data_pipeline/downloader.py`, `backend/app/research/data_pipeline/planner.py`, `backend/app/research/data_pipeline/sync.py`, `backend/tests/test_research_data_pipeline.py`
- **Imports / protects:** `backend/app/research/data_pipeline/paginator.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_pipeline.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/deduplicator.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_pipeline/downloader.py`, `backend/app/research/data_pipeline/sync.py`, `backend/tests/test_research_data_pipeline.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_pipeline.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/downloader.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_pipeline/runner.py`
- **Imports / protects:** `backend/app/research/data_pipeline/__init__.py`, `backend/app/research/data_pipeline/checkpoint.py`, `backend/app/research/data_pipeline/config.py`, `backend/app/research/data_pipeline/coverage.py`, `backend/app/research/data_pipeline/deduplicator.py`, `backend/app/research/data_pipeline/metrics.py`, `backend/app/research/data_pipeline/normalizer.py`, `backend/app/research/data_pipeline/paginator.py` (+5 more)
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/event_store.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_cache/event_cache.py`, `backend/app/research/data_pipeline/feature_store.py`, `backend/app/research/data_pipeline/runner.py`, `backend/scripts/benchmark_research_pipeline.py`, `backend/tests/test_research_data_pipeline.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/engine.py`, `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/bos_strategy_comparison/lifecycle.py`, `backend/app/research/combination_engine.py`, `backend/app/research/data_pipeline/config.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_pipeline.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/feature_store.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_pipeline/runner.py`, `backend/tests/test_research_data_pipeline.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/data_pipeline/__init__.py`, `backend/app/research/data_pipeline/config.py`, `backend/app/research/data_pipeline/event_store.py`, `backend/app/research/data_pipeline/metrics.py`, `backend/app/research/data_pipeline/repository.py`, `backend/app/research/postgres_ohlcv.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_pipeline.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/history_coverage.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/bos_strategy_comparison/service.py`, `backend/app/research/data_pipeline/__init__.py`, `backend/tests/test_history_coverage_diagnostics.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/data_quality.py`, `backend/app/research/data_pipeline/config.py`, `backend/app/research/postgres_ohlcv.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_history_coverage_diagnostics.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/locks.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_pipeline/sync.py`, `backend/tests/test_research_data_pipeline.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_pipeline.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/manifest.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_pipeline/runner.py`, `backend/tests/test_research_data_pipeline.py`
- **Imports / protects:** `backend/app/research/data_pipeline/checkpoint.py`, `backend/app/research/data_pipeline/config.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_pipeline.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/metrics.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_pipeline/downloader.py`, `backend/app/research/data_pipeline/feature_store.py`, `backend/app/research/data_pipeline/runner.py`, `backend/app/research/data_pipeline/sync.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/normalizer.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_pipeline/downloader.py`, `backend/app/research/data_pipeline/sync.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/paginator.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_pipeline/coverage.py`, `backend/app/research/data_pipeline/downloader.py`, `backend/app/research/data_pipeline/sync.py`, `backend/tests/test_research_data_pipeline.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_pipeline.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/planner.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_pipeline/__init__.py`, `backend/app/research/data_pipeline/downloader.py`, `backend/app/research/data_pipeline/sync.py`, `backend/tests/test_research_data_pipeline.py`
- **Imports / protects:** `backend/app/research/data_pipeline/coverage.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_pipeline.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/repository.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_pipeline/downloader.py`, `backend/app/research/data_pipeline/feature_store.py`, `backend/app/research/data_pipeline/runner.py`, `backend/app/research/data_pipeline/sync.py`
- **Imports / protects:** `backend/app/research/data_pipeline/checkpoint.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/runner.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_pipeline/__init__.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/data_pipeline/__init__.py`, `backend/app/research/data_pipeline/config.py`, `backend/app/research/data_pipeline/downloader.py`, `backend/app/research/data_pipeline/event_store.py`, `backend/app/research/data_pipeline/feature_store.py`, `backend/app/research/data_pipeline/manifest.py`, `backend/app/research/data_pipeline/metrics.py` (+2 more)
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/sync.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_pipeline/downloader.py`, `backend/app/research/data_pipeline/runner.py`, `backend/tests/test_research_data_pipeline.py`
- **Imports / protects:** `backend/app/research/data_pipeline/__init__.py`, `backend/app/research/data_pipeline/checkpoint.py`, `backend/app/research/data_pipeline/config.py`, `backend/app/research/data_pipeline/coverage.py`, `backend/app/research/data_pipeline/deduplicator.py`, `backend/app/research/data_pipeline/locks.py`, `backend/app/research/data_pipeline/metrics.py`, `backend/app/research/data_pipeline/normalizer.py` (+4 more)
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_pipeline.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_pipeline/validator.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/data_pipeline/downloader.py`, `backend/app/research/data_pipeline/sync.py`, `backend/tests/test_research_data_pipeline.py`
- **Imports / protects:** `backend/app/research/data_pipeline/config.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_research_data_pipeline.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/data_quality.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/research/bos_strategy_comparison/data_quality.py`, `backend/app/research/candle12_hypothesis.py`, `backend/app/research/candle12_v2.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/data_cache/validation.py`, `backend/app/research/multi_cap_strategies/runner.py`, `backend/tests/test_bos_research.py`, `backend/tests/test_bos_strategy_comparison.py` (+1 more)
- **Imports / protects:** `backend/app/research/config.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_bos_research.py`, `backend/tests/test_bos_strategy_comparison.py`, `backend/tests/test_candle12_v2.py`
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/dynamic_candidate_constants.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/api/routes.py`, `backend/app/research/advance_dynamic_candidates.py`, `backend/app/research/dynamic_candidate_discovery.py`, `backend/app/research/dynamic_candidate_pipeline.py`, `backend/app/research/strategy_candidate_registry.py`, `backend/app/services/paper_trade.py`, `backend/app/services/v2_candidate_paper_watcher.py`, `backend/tests/test_dynamic_candidate_pipeline.py` (+1 more)
- **CLI usage:** indirect
- **API route:** yes
- **Test coverage:** `backend/tests/test_dynamic_candidate_pipeline.py`, `backend/tests/test_e2e_paper_candidate_safety_validation.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/dynamic_candidate_discovery.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/api/routes.py`, `backend/scripts/run_dynamic_candidate_discovery.py`
- **Imports / protects:** `backend/app/research/combo02_candidate_selector.py`, `backend/app/research/combo02_candidate_thresholds.py`, `backend/app/research/dynamic_candidate_constants.py`, `backend/app/research/strategy_candidate_registry.py`
- **CLI usage:** yes
- **API route:** yes
- **Last modification:** 2026-10-04T02:38:38+05:30 `04a30ce` Separate Futures Screener discovery UI from COMBO_02 v1 eligibility.
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/dynamic_candidate_pipeline.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/research/advance_dynamic_candidates.py`
- **Imports / protects:** `backend/app/research/candidate_state_machine.py`, `backend/app/research/combo02_candidate_eligibility.py`, `backend/app/research/combo02_candidate_research.py`, `backend/app/research/combo02_candidate_thresholds.py`, `backend/app/research/dynamic_candidate_constants.py`, `backend/app/research/service.py`, `backend/app/research/strategy_candidate_registry.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/failure_regime_decomposition.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/scripts/run_failure_regime_decomposition.py`, `backend/tests/test_failure_regime_decomposition.py`
- **Imports / protects:** `backend/app/research/htf_alignment_sensitivity.py`, `backend/app/research/trend_regime_diagnostic.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_failure_regime_decomposition.py`
- **Last modification:** 2026-10-02T22:13:35+05:30 `ca3a991` Add COMBO_02 regime/SHORT research, opt-in gates, and backtest UI depth.
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/historical_market_cap/__init__.py`

- **Classification:** WRAPPER
- **Imported by:** `backend/app/research/historical_market_cap/service.py`, `backend/scripts/audit_multi_cap_baseline_forensics.py`, `backend/scripts/cap_classification_coverage.py`, `backend/scripts/run_multi_cap_full_research.py`
- **Imports / protects:** `backend/app/research/historical_market_cap/classifier.py`, `backend/app/research/historical_market_cap/schemas.py`
- **CLI usage:** yes
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Wrapper action:** KEEP
- **Safe to archive:** NO
- **Reason:** thin re-export or pass-through without unique business logic

#### `backend/app/research/historical_market_cap/classifier.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/historical_market_cap/__init__.py`, `backend/app/research/historical_market_cap/provider.py`, `backend/app/research/historical_market_cap/service.py`, `backend/scripts/audit_multi_cap_baseline_forensics.py`, `backend/scripts/cap_classification_coverage.py`, `backend/scripts/run_multi_cap_full_research.py`, `backend/tests/test_historical_market_cap.py`
- **Imports / protects:** `backend/app/research/config.py`, `backend/app/research/historical_market_cap/schemas.py`, `backend/app/research/multi_cap_strategies/config.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_historical_market_cap.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/historical_market_cap/provider.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/historical_market_cap/service.py`
- **Imports / protects:** `backend/app/research/config.py`, `backend/app/research/historical_market_cap/classifier.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/historical_market_cap/repository.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/historical_market_cap/service.py`, `backend/scripts/audit_multi_cap_baseline_forensics.py`, `backend/scripts/cap_classification_coverage.py`, `backend/scripts/run_multi_cap_full_research.py`
- **CLI usage:** yes
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/historical_market_cap/schemas.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/app/research/historical_market_cap/__init__.py`, `backend/app/research/historical_market_cap/classifier.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/historical_market_cap/service.py`

- **Classification:** ACTIVE_DATA_PIPELINE
- **Imported by:** `backend/scripts/cap_classification_coverage.py`, `backend/scripts/run_multi_cap_full_research.py`
- **Imports / protects:** `backend/app/research/config.py`, `backend/app/research/historical_market_cap/__init__.py`, `backend/app/research/historical_market_cap/classifier.py`, `backend/app/research/historical_market_cap/provider.py`, `backend/app/research/historical_market_cap/repository.py`
- **CLI usage:** yes
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** historical ingest / feature / coverage pipeline

#### `backend/app/research/htf_alignment_sensitivity.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/failure_regime_decomposition.py`, `backend/app/research/short_policy_sensitivity.py`, `backend/scripts/run_htf_alignment_sensitivity.py`, `backend/tests/test_htf_alignment_sensitivity.py`, `backend/tests/test_short_policy_sensitivity.py`
- **Imports / protects:** `backend/app/research/metrics.py`, `backend/app/research/trend_regime_diagnostic.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_htf_alignment_sensitivity.py`, `backend/tests/test_short_policy_sensitivity.py`
- **Last modification:** 2026-10-02T22:13:35+05:30 `ca3a991` Add COMBO_02 regime/SHORT research, opt-in gates, and backtest UI depth.
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/live_backtest_parity/__init__.py`

- **Classification:** WRAPPER
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/live_backtest_parity/constants.py`, `backend/app/research/live_backtest_parity/runner.py`
- **CLI usage:** no
- **API route:** no
- **Wrapper action:** KEEP
- **Safe to archive:** NO
- **Reason:** thin re-export or pass-through without unique business logic

#### `backend/app/research/live_backtest_parity/candle_validation.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/live_backtest_parity/replay.py`, `backend/tests/test_live_backtest_parity.py`
- **Imports / protects:** `backend/app/research/live_backtest_parity/models.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_live_backtest_parity.py`
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/live_backtest_parity/constants.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/live_backtest_parity/__init__.py`, `backend/app/research/live_backtest_parity/entry_price.py`, `backend/app/research/live_backtest_parity/models.py`, `backend/app/research/live_backtest_parity/paper_sim.py`, `backend/app/research/live_backtest_parity/replay.py`, `backend/app/research/live_backtest_parity/report.py`, `backend/app/research/live_backtest_parity/runner.py`, `backend/app/research/live_backtest_parity/shadow_compare.py` (+3 more)
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_live_backtest_parity.py`
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/live_backtest_parity/entry_price.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/live_backtest_parity/paper_sim.py`, `backend/app/research/live_backtest_parity/report.py`, `backend/app/services/paper_trade.py`, `backend/tests/test_live_backtest_parity.py`
- **Imports / protects:** `backend/app/research/live_backtest_parity/constants.py`, `backend/app/research/live_backtest_parity/models.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_live_backtest_parity.py`
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/live_backtest_parity/models.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/live_backtest_parity/candle_validation.py`, `backend/app/research/live_backtest_parity/entry_price.py`, `backend/app/research/live_backtest_parity/paper_sim.py`, `backend/app/research/live_backtest_parity/replay.py`, `backend/app/research/live_backtest_parity/report.py`, `backend/app/research/live_backtest_parity/telegram_validation.py`, `backend/tests/test_live_backtest_parity.py`
- **Imports / protects:** `backend/app/research/live_backtest_parity/constants.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_live_backtest_parity.py`
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/live_backtest_parity/paper_sim.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/live_backtest_parity/replay.py`
- **Imports / protects:** `backend/app/research/live_backtest_parity/constants.py`, `backend/app/research/live_backtest_parity/entry_price.py`, `backend/app/research/live_backtest_parity/models.py`
- **CLI usage:** indirect
- **API route:** no
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/live_backtest_parity/replay.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/live_backtest_parity/runner.py`, `backend/tests/test_live_backtest_parity.py`
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_engine.py`, `backend/app/research/config.py`, `backend/app/research/live_backtest_parity/candle_validation.py`, `backend/app/research/live_backtest_parity/constants.py`, `backend/app/research/live_backtest_parity/models.py`, `backend/app/research/live_backtest_parity/paper_sim.py`, `backend/app/research/live_backtest_parity/shadow_compare.py` (+1 more)
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_live_backtest_parity.py`
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/live_backtest_parity/report.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/live_backtest_parity/runner.py`, `backend/tests/test_live_backtest_parity.py`
- **Imports / protects:** `backend/app/research/live_backtest_parity/constants.py`, `backend/app/research/live_backtest_parity/entry_price.py`, `backend/app/research/live_backtest_parity/models.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_live_backtest_parity.py`
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/live_backtest_parity/runner.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/live_backtest_parity/__init__.py`, `backend/scripts/live_backtest_parity/run_parity_validation.py`, `backend/tests/test_live_backtest_parity.py`
- **Imports / protects:** `backend/app/research/live_backtest_parity/constants.py`, `backend/app/research/live_backtest_parity/replay.py`, `backend/app/research/live_backtest_parity/report.py`, `backend/app/research/postgres_ohlcv.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_live_backtest_parity.py`
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/live_backtest_parity/shadow_compare.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/live_backtest_parity/replay.py`, `backend/tests/test_live_backtest_parity.py`
- **Imports / protects:** `backend/app/research/live_backtest_parity/constants.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_live_backtest_parity.py`
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/live_backtest_parity/telegram_validation.py`

- **Classification:** ACTIVE_BACKTEST
- **Imported by:** `backend/app/research/live_backtest_parity/replay.py`, `backend/tests/test_live_backtest_parity.py`
- **Imports / protects:** `backend/app/research/live_backtest_parity/constants.py`, `backend/app/research/live_backtest_parity/models.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_live_backtest_parity.py`
- **Safe to archive:** NO
- **Reason:** backtest runner/engine/lifecycle on active research path

#### `backend/app/research/metrics.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/research/bos_strategy_comparison/metrics.py`, `backend/app/research/candle12_hypothesis.py`, `backend/app/research/candle12_v2.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/htf_alignment_sensitivity.py`, `backend/app/research/multi_cap_strategies/runner.py`, `backend/app/research/service.py`, `backend/scripts/run_multi_cap_full_research.py` (+2 more)
- **Imports / protects:** `backend/app/research/schemas.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_bos_research.py`, `backend/tests/test_bos_research_compare_api.py`
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/multi_cap_strategies/__init__.py`

- **Classification:** WRAPPER
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/multi_cap_strategies/adapter.py`, `backend/app/research/multi_cap_strategies/config.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Wrapper action:** KEEP
- **Safe to archive:** NO
- **Reason:** thin re-export or pass-through without unique business logic

#### `backend/app/research/multi_cap_strategies/adapter.py`

- **Classification:** WRAPPER
- **Imported by:** `backend/app/research/multi_cap_strategies/__init__.py`, `backend/app/research/multi_cap_strategies/runner.py`, `backend/app/research/multi_cap_strategies/service.py`, `backend/scripts/cap_classification_coverage.py`, `backend/scripts/validate_multi_cap_strategies.py`, `backend/tests/test_multi_cap_strategies.py`
- **Imports / protects:** `backend/app/research/combination_backtest.py`, `backend/app/research/multi_cap_strategies/cap_filter.py`, `backend/app/research/multi_cap_strategies/common.py`, `backend/app/research/multi_cap_strategies/config.py`, `backend/app/research/multi_cap_strategies/large_cap_sweep_choch.py`, `backend/app/research/multi_cap_strategies/mid_cap_fvg_discount.py`, `backend/app/research/multi_cap_strategies/schemas.py`, `backend/app/research/multi_cap_strategies/small_cap_volume_bos.py` (+1 more)
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_multi_cap_strategies.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Wrapper action:** KEEP
- **Safe to archive:** NO
- **Reason:** thin re-export or pass-through without unique business logic

#### `backend/app/research/multi_cap_strategies/baseline_forensics.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/scripts/audit_multi_cap_baseline_forensics.py`, `backend/tests/test_multi_cap_baseline_forensics.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_multi_cap_baseline_forensics.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/multi_cap_strategies/cap_filter.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/app/research/multi_cap_strategies/adapter.py`, `backend/app/research/multi_cap_strategies/runner.py`, `backend/app/research/multi_cap_strategies/service.py`, `backend/scripts/validate_multi_cap_strategies.py`, `backend/tests/test_multi_cap_strategies.py`
- **Imports / protects:** `backend/app/research/config.py`, `backend/app/research/multi_cap_strategies/config.py`, `backend/app/research/multi_cap_strategies/schemas.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_multi_cap_strategies.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/multi_cap_strategies/common.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/app/research/multi_cap_strategies/adapter.py`, `backend/app/research/multi_cap_strategies/large_cap_sweep_choch.py`, `backend/app/research/multi_cap_strategies/mid_cap_fvg_discount.py`, `backend/app/research/multi_cap_strategies/small_cap_volume_bos.py`, `backend/scripts/audit_multi_cap_baseline_forensics.py`, `backend/scripts/cap_classification_coverage.py`, `backend/scripts/run_multi_cap_full_research.py`, `backend/scripts/validate_multi_cap_strategies.py` (+1 more)
- **Imports / protects:** `backend/app/research/multi_cap_strategies/config.py`, `backend/app/research/schemas.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_multi_cap_strategies.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/multi_cap_strategies/config.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/app/research/historical_market_cap/classifier.py`, `backend/app/research/multi_cap_strategies/__init__.py`, `backend/app/research/multi_cap_strategies/adapter.py`, `backend/app/research/multi_cap_strategies/cap_filter.py`, `backend/app/research/multi_cap_strategies/common.py`, `backend/app/research/multi_cap_strategies/fvg.py`, `backend/app/research/multi_cap_strategies/large_cap_sweep_choch.py`, `backend/app/research/multi_cap_strategies/mid_cap_fvg_discount.py` (+8 more)
- **Imports / protects:** `backend/app/research/config.py`, `backend/app/research/trade_fees.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_multi_cap_strategies.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/multi_cap_strategies/fvg.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/app/research/multi_cap_strategies/mid_cap_fvg_discount.py`, `backend/scripts/run_multi_cap_full_research.py`, `backend/scripts/validate_multi_cap_strategies.py`, `backend/tests/test_multi_cap_strategies.py`
- **Imports / protects:** `backend/app/research/multi_cap_strategies/config.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_multi_cap_strategies.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/multi_cap_strategies/large_cap_sweep_choch.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/app/research/multi_cap_strategies/adapter.py`, `backend/scripts/audit_multi_cap_baseline_forensics.py`, `backend/scripts/cap_classification_coverage.py`, `backend/scripts/run_multi_cap_full_research.py`, `backend/scripts/validate_multi_cap_strategies.py`, `backend/tests/test_multi_cap_strategies.py`
- **Imports / protects:** `backend/app/research/multi_cap_strategies/common.py`, `backend/app/research/multi_cap_strategies/config.py`, `backend/app/research/multi_cap_strategies/schemas.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_multi_cap_strategies.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/multi_cap_strategies/mid_cap_fvg_discount.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/app/research/multi_cap_strategies/adapter.py`, `backend/scripts/audit_multi_cap_baseline_forensics.py`, `backend/scripts/cap_classification_coverage.py`, `backend/scripts/run_multi_cap_full_research.py`, `backend/scripts/validate_multi_cap_strategies.py`, `backend/tests/test_multi_cap_strategies.py`
- **Imports / protects:** `backend/app/research/multi_cap_strategies/common.py`, `backend/app/research/multi_cap_strategies/config.py`, `backend/app/research/multi_cap_strategies/fvg.py`, `backend/app/research/multi_cap_strategies/schemas.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_multi_cap_strategies.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/multi_cap_strategies/reference.py`

- **Classification:** UTILITY
- **Imported by:** `backend/scripts/run_multi_cap_full_research.py`, `backend/scripts/validate_multi_cap_strategies.py`, `backend/tests/test_multi_cap_strategies.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_multi_cap_strategies.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** helpers/constants with no standalone product surface

#### `backend/app/research/multi_cap_strategies/repository.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/app/research/multi_cap_strategies/service.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/multi_cap_strategies/runner.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/app/research/multi_cap_strategies/service.py`, `backend/scripts/validate_multi_cap_strategies.py`, `backend/tests/test_multi_cap_strategies.py`
- **Imports / protects:** `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`, `backend/app/research/data_quality.py`, `backend/app/research/metrics.py`, `backend/app/research/multi_cap_strategies/adapter.py`, `backend/app/research/multi_cap_strategies/cap_filter.py`, `backend/app/research/multi_cap_strategies/config.py`, `backend/app/research/schemas.py` (+1 more)
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_multi_cap_strategies.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/multi_cap_strategies/schemas.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/app/research/multi_cap_strategies/adapter.py`, `backend/app/research/multi_cap_strategies/cap_filter.py`, `backend/app/research/multi_cap_strategies/large_cap_sweep_choch.py`, `backend/app/research/multi_cap_strategies/mid_cap_fvg_discount.py`, `backend/app/research/multi_cap_strategies/small_cap_volume_bos.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/multi_cap_strategies/service.py`

- **Classification:** ACTIVE_API
- **Imported by:** `backend/app/api/routes.py`
- **Imports / protects:** `backend/app/research/multi_cap_strategies/adapter.py`, `backend/app/research/multi_cap_strategies/cap_filter.py`, `backend/app/research/multi_cap_strategies/config.py`, `backend/app/research/multi_cap_strategies/repository.py`, `backend/app/research/multi_cap_strategies/runner.py`, `backend/app/research/postgres_ohlcv.py`, `backend/app/research/query_utils.py`
- **CLI usage:** indirect
- **API route:** yes
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** wired to FastAPI research routes or job services

#### `backend/app/research/multi_cap_strategies/small_cap_volume_bos.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/app/research/multi_cap_strategies/adapter.py`, `backend/scripts/audit_multi_cap_baseline_forensics.py`, `backend/scripts/cap_classification_coverage.py`, `backend/scripts/run_multi_cap_full_research.py`, `backend/scripts/validate_multi_cap_strategies.py`, `backend/tests/test_multi_cap_strategies.py`
- **Imports / protects:** `backend/app/research/multi_cap_strategies/common.py`, `backend/app/research/multi_cap_strategies/config.py`, `backend/app/research/multi_cap_strategies/schemas.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_multi_cap_strategies.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/ohlcv_expand.py`

- **Classification:** ACTIVE_API
- **Imported by:** `backend/app/api/routes.py`, `backend/app/diagnostics/collectors/jobs.py`, `backend/app/research/candidate_data_health.py`, `backend/scripts/expand_ohlcv_history.py`, `backend/tests/test_ohlcv_expand.py`
- **Imports / protects:** `backend/app/research/query_utils.py`
- **CLI usage:** yes
- **API route:** yes
- **Test coverage:** `backend/tests/test_ohlcv_expand.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** wired to FastAPI research routes or job services

#### `backend/app/research/postgres_ohlcv.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/api/routes.py`, `backend/app/research/bos_strategy_comparison/data_quality.py`, `backend/app/research/bos_strategy_comparison/service.py`, `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/data_cache/postgres_loader.py`, `backend/app/research/data_pipeline/downloader.py`, `backend/app/research/data_pipeline/feature_store.py`, `backend/app/research/data_pipeline/history_coverage.py` (+19 more)
- **Imports / protects:** `backend/app/research/query_utils.py`
- **CLI usage:** yes
- **API route:** yes
- **Test coverage:** `backend/tests/test_backtest_job_progress.py`
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/query_utils.py`

- **Classification:** UTILITY
- **Imported by:** `backend/app/api/routes.py`, `backend/app/research/backtest_job.py`, `backend/app/research/bos_strategy_comparison/service.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/data_cache/cache_key.py`, `backend/app/research/data_cache/cache_manager.py`, `backend/app/research/data_cache/postgres_loader.py` (+7 more)
- **CLI usage:** yes
- **API route:** yes
- **Test coverage:** `backend/tests/test_bos_research_compare_api.py`
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Safe to archive:** NO
- **Reason:** helpers/constants with no standalone product surface

#### `backend/app/research/repository.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/research/service.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/rolling_structure.py`

- **Classification:** ACTIVE_STRATEGY
- **Imported by:** `backend/app/research/candle12_v2.py`, `backend/scripts/run_candle12_v2_research.py`, `backend/tests/test_candle12_v2.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_candle12_v2.py`
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Safe to archive:** NO
- **Reason:** strategy definition / evaluation logic on active research path

#### `backend/app/research/schemas.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/research/candle12_hypothesis.py`, `backend/app/research/candle12_v2.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/metrics.py`, `backend/app/research/multi_cap_strategies/adapter.py`, `backend/app/research/multi_cap_strategies/common.py`, `backend/app/research/multi_cap_strategies/runner.py`, `backend/app/research/service.py` (+5 more)
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_bos_research_compare_api.py`, `backend/tests/test_multi_cap_strategies.py`
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/service.py`

- **Classification:** ACTIVE_API
- **Imported by:** `backend/app/api/routes.py`, `backend/app/research/backtest_job.py`, `backend/app/research/combo02_candidate_research.py`, `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/dynamic_candidate_pipeline.py`, `backend/scripts/audit_combo02_htf_fidelity.py`, `backend/scripts/diagnose_backtest_15m.py`, `backend/tests/test_backtest_ui_config.py` (+1 more)
- **Imports / protects:** `backend/app/research/__init__.py`, `backend/app/research/backtest_timing.py`, `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`, `backend/app/research/data_cache/cache_manager.py`, `backend/app/research/data_cache/config.py`, `backend/app/research/metrics.py` (+5 more)
- **CLI usage:** yes
- **API route:** yes
- **Test coverage:** `backend/tests/test_backtest_ui_config.py`, `backend/tests/test_bos_research_compare_api.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** wired to FastAPI research routes or job services

#### `backend/app/research/short_entry_research/__init__.py`

- **Classification:** WRAPPER
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/short_entry_research/constants.py`, `backend/app/research/short_entry_research/runner.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Wrapper action:** KEEP
- **Safe to archive:** NO
- **Reason:** thin re-export or pass-through without unique business logic

#### `backend/app/research/short_entry_research/constants.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_entry_research/__init__.py`, `backend/app/research/short_entry_research/entry_classification.py`, `backend/app/research/short_entry_research/path_metrics.py`, `backend/app/research/short_entry_research/report.py`, `backend/app/research/short_entry_research/retest_fills.py`, `backend/app/research/short_entry_research/runner.py`, `backend/app/research/short_entry_research/validation.py`, `backend/app/research/short_entry_research/variants.py` (+2 more)
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_entry_research.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_entry_research/entry_classification.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_entry_research/variants.py`, `backend/tests/test_combo02_short_entry_research.py`
- **Imports / protects:** `backend/app/research/short_entry_research/constants.py`, `backend/app/research/short_research_diagnostics/metrics.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_entry_research.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_entry_research/path_metrics.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_entry_research/variants.py`, `backend/tests/test_combo02_short_entry_research.py`
- **Imports / protects:** `backend/app/research/combination_backtest.py`, `backend/app/research/schemas.py`, `backend/app/research/short_entry_research/constants.py`, `backend/app/research/short_research_diagnostics/metrics.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_entry_research.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_entry_research/report.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_entry_research/runner.py`
- **Imports / protects:** `backend/app/research/short_entry_research/constants.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_entry_research/retest_fills.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_entry_research/variants.py`, `backend/tests/test_combo02_short_entry_research.py`
- **Imports / protects:** `backend/app/research/short_entry_research/constants.py`, `backend/app/research/short_research_diagnostics/metrics.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_entry_research.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_entry_research/runner.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_entry_research/__init__.py`, `backend/scripts/run_combo02_short_entry_research.py`
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/short_entry_research/constants.py`, `backend/app/research/short_entry_research/report.py`, `backend/app/research/short_entry_research/validation.py`, `backend/app/research/short_entry_research/variants.py`, `backend/app/research/short_research_constants.py` (+3 more)
- **CLI usage:** yes
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_entry_research/validation.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_entry_research/runner.py`, `backend/tests/test_combo02_short_entry_research.py`
- **Imports / protects:** `backend/app/research/combo02_candidate_research.py`, `backend/app/research/short_entry_research/constants.py`, `backend/app/research/short_research_forensics.py`, `backend/app/research/short_research_quality.py`, `backend/app/research/short_research_windows.py`, `backend/app/research/trade_fees.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_entry_research.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_entry_research/variants.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_entry_research/runner.py`, `backend/tests/test_combo02_short_entry_research.py`
- **Imports / protects:** `backend/app/research/short_entry_research/constants.py`, `backend/app/research/short_entry_research/entry_classification.py`, `backend/app/research/short_entry_research/path_metrics.py`, `backend/app/research/short_entry_research/retest_fills.py`, `backend/app/research/short_research_diagnostics/metrics.py`, `backend/app/research/trade_fees.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_entry_research.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_policy_sensitivity.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/scripts/run_short_policy_sensitivity.py`, `backend/tests/test_short_policy_sensitivity.py`
- **Imports / protects:** `backend/app/research/htf_alignment_sensitivity.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_short_policy_sensitivity.py`
- **Last modification:** 2026-10-02T22:13:35+05:30 `ca3a991` Add COMBO_02 regime/SHORT research, opt-in gates, and backtest UI depth.
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_pullback_rejection/__init__.py`

- **Classification:** WRAPPER
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/short_pullback_rejection/constants.py`, `backend/app/research/short_pullback_rejection/runner.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Wrapper action:** KEEP
- **Safe to archive:** NO
- **Reason:** thin re-export or pass-through without unique business logic

#### `backend/app/research/short_pullback_rejection/constants.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_pullback_rejection/__init__.py`, `backend/app/research/short_pullback_rejection/rejection.py`, `backend/app/research/short_pullback_rejection/report.py`, `backend/app/research/short_pullback_rejection/runner.py`, `backend/app/research/short_pullback_rejection/signals.py`, `backend/app/research/short_pullback_rejection/simulation.py`, `backend/app/research/short_pullback_rejection/validation.py`, `backend/app/research/short_pullback_rejection/variants.py` (+2 more)
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_short_pullback_rejection_research.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_pullback_rejection/regime.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_pullback_rejection/rejection.py`, `backend/app/research/short_pullback_rejection/signals.py`, `backend/app/research/short_pullback_rejection/simulation.py`, `backend/app/research/short_pullback_rejection/zones.py`, `backend/tests/test_short_pullback_rejection_research.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_short_pullback_rejection_research.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_pullback_rejection/rejection.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_pullback_rejection/signals.py`, `backend/tests/test_short_pullback_rejection_research.py`
- **Imports / protects:** `backend/app/research/short_pullback_rejection/constants.py`, `backend/app/research/short_pullback_rejection/regime.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_short_pullback_rejection_research.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_pullback_rejection/report.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_pullback_rejection/runner.py`
- **Imports / protects:** `backend/app/research/short_pullback_rejection/constants.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_pullback_rejection/runner.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_pullback_rejection/__init__.py`, `backend/scripts/run_short_pullback_rejection_research.py`
- **Imports / protects:** `backend/app/research/short_pullback_rejection/constants.py`, `backend/app/research/short_pullback_rejection/report.py`, `backend/app/research/short_pullback_rejection/signals.py`, `backend/app/research/short_pullback_rejection/simulation.py`, `backend/app/research/short_pullback_rejection/validation.py`, `backend/app/research/short_pullback_rejection/variants.py`, `backend/app/research/short_research_diagnostics/runner.py`, `backend/app/research/short_research_windows.py`
- **CLI usage:** yes
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_pullback_rejection/signals.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_pullback_rejection/runner.py`, `backend/app/research/short_pullback_rejection/simulation.py`, `backend/app/research/short_pullback_rejection/variants.py`, `backend/tests/test_short_pullback_rejection_research.py`
- **Imports / protects:** `backend/app/research/short_pullback_rejection/constants.py`, `backend/app/research/short_pullback_rejection/regime.py`, `backend/app/research/short_pullback_rejection/rejection.py`, `backend/app/research/short_pullback_rejection/zones.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_short_pullback_rejection_research.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_pullback_rejection/simulation.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_pullback_rejection/runner.py`, `backend/app/research/short_pullback_rejection/variants.py`, `backend/tests/test_short_pullback_rejection_research.py`
- **Imports / protects:** `backend/app/research/short_pullback_rejection/constants.py`, `backend/app/research/short_pullback_rejection/regime.py`, `backend/app/research/short_pullback_rejection/signals.py`, `backend/app/research/trade_fees.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_short_pullback_rejection_research.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_pullback_rejection/validation.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_pullback_rejection/runner.py`, `backend/tests/test_short_pullback_rejection_research.py`
- **Imports / protects:** `backend/app/research/short_pullback_rejection/constants.py`, `backend/app/research/short_research_forensics.py`, `backend/app/research/short_research_quality.py`, `backend/app/research/short_research_windows.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_short_pullback_rejection_research.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_pullback_rejection/variants.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_pullback_rejection/runner.py`
- **Imports / protects:** `backend/app/research/short_pullback_rejection/constants.py`, `backend/app/research/short_pullback_rejection/signals.py`, `backend/app/research/short_pullback_rejection/simulation.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_pullback_rejection/zones.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_pullback_rejection/signals.py`, `backend/tests/test_short_pullback_rejection_research.py`
- **Imports / protects:** `backend/app/research/short_pullback_rejection/constants.py`, `backend/app/research/short_pullback_rejection/regime.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_short_pullback_rejection_research.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_research_constants.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/api/routes.py`, `backend/app/research/combo02_short_research.py`, `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/short_entry_research/runner.py`, `backend/app/research/short_research_diagnostics/constants.py`, `backend/app/research/short_research_hard_gates.py`, `backend/app/research/short_research_quality.py`, `backend/app/research/short_research_windows.py` (+13 more)
- **CLI usage:** yes
- **API route:** yes
- **Test coverage:** `backend/tests/test_combo02_short_research.py`, `backend/tests/test_combo02_short_research_api.py`, `backend/tests/test_combo02_short_research_forensics.py`, `backend/tests/test_combo02_short_research_prepaper_review.py`, `backend/tests/test_combo02_short_research_quality.py`, `backend/tests/test_combo02_short_research_real_batch.py` ...
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/short_research_diagnostics/__init__.py`

- **Classification:** WRAPPER
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/short_research_diagnostics/implementation_verify.py`, `backend/app/research/short_research_diagnostics/runner.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Wrapper action:** KEEP
- **Safe to archive:** NO
- **Reason:** thin re-export or pass-through without unique business logic

#### `backend/app/research/short_research_diagnostics/ablation.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_research_diagnostics/runner.py`, `backend/tests/test_combo02_short_diagnostics.py`
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/combo02_candidate_research.py`, `backend/app/research/schemas.py`, `backend/app/research/short_research_diagnostics/constants.py`, `backend/app/research/short_research_diagnostics/metrics.py`, `backend/app/research/short_research_forensics.py`, `backend/app/research/trade_fees.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_diagnostics.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_research_diagnostics/classification.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_research_diagnostics/runner.py`
- **Imports / protects:** `backend/app/research/short_research_diagnostics/constants.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_research_diagnostics/classifiers.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_research_diagnostics/runner.py`, `backend/app/research/short_research_diagnostics/trade_dataset.py`, `backend/tests/test_combo02_short_diagnostics.py`
- **Imports / protects:** `backend/app/research/short_research_diagnostics/constants.py`, `backend/app/research/short_research_diagnostics/metrics.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_diagnostics.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_research_diagnostics/constants.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_research_diagnostics/ablation.py`, `backend/app/research/short_research_diagnostics/classification.py`, `backend/app/research/short_research_diagnostics/classifiers.py`, `backend/app/research/short_research_diagnostics/implementation_verify.py`, `backend/app/research/short_research_diagnostics/report.py`, `backend/app/research/short_research_diagnostics/runner.py`, `backend/tests/test_combo02_short_diagnostics.py`
- **Imports / protects:** `backend/app/research/short_research_constants.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_diagnostics.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_research_diagnostics/fee_sensitivity.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_research_diagnostics/runner.py`, `backend/tests/test_combo02_short_diagnostics.py`
- **Imports / protects:** `backend/app/research/trade_fees.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_diagnostics.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_research_diagnostics/implementation_verify.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_research_diagnostics/__init__.py`, `backend/app/research/short_research_diagnostics/runner.py`, `backend/tests/test_combo02_short_diagnostics.py`
- **Imports / protects:** `backend/app/research/combo02_short_research.py`, `backend/app/research/short_research_diagnostics/constants.py`, `backend/app/research/trade_fees.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_diagnostics.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_research_diagnostics/metrics.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_entry_research/entry_classification.py`, `backend/app/research/short_entry_research/path_metrics.py`, `backend/app/research/short_entry_research/retest_fills.py`, `backend/app/research/short_entry_research/variants.py`, `backend/app/research/short_research_diagnostics/ablation.py`, `backend/app/research/short_research_diagnostics/classifiers.py`, `backend/app/research/short_research_diagnostics/trade_dataset.py`, `backend/tests/test_combo02_short_diagnostics.py` (+1 more)
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_diagnostics.py`, `backend/tests/test_combo02_short_entry_research.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_research_diagnostics/report.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_research_diagnostics/runner.py`
- **Imports / protects:** `backend/app/research/short_research_diagnostics/constants.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_research_diagnostics/runner.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_entry_research/runner.py`, `backend/app/research/short_pullback_rejection/runner.py`, `backend/app/research/short_research_diagnostics/__init__.py`, `backend/scripts/run_combo02_short_diagnostics.py`
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/combo02_candidate_research.py`, `backend/app/research/data_cache/memory_loader.py`, `backend/app/research/short_research_diagnostics/ablation.py`, `backend/app/research/short_research_diagnostics/classification.py`, `backend/app/research/short_research_diagnostics/classifiers.py`, `backend/app/research/short_research_diagnostics/constants.py` (+7 more)
- **CLI usage:** yes
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_research_diagnostics/trade_dataset.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/short_entry_research/runner.py`, `backend/app/research/short_research_diagnostics/runner.py`
- **Imports / protects:** `backend/app/research/short_research_diagnostics/classifiers.py`, `backend/app/research/short_research_diagnostics/metrics.py`, `backend/app/research/trade_fees.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_research_forensics.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/combo02_short_research.py`, `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/short_entry_research/validation.py`, `backend/app/research/short_pullback_rejection/validation.py`, `backend/app/research/short_research_diagnostics/ablation.py`, `backend/app/research/short_research_diagnostics/runner.py`, `backend/app/research/short_research_hard_gates.py`, `backend/app/research/short_research_prepaper_review.py` (+5 more)
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_research_forensics.py`, `backend/tests/test_combo02_short_research_prepaper_review.py`, `backend/tests/test_combo02_short_research_real_batch.py`, `backend/tests/test_short_pullback_rejection_research.py`, `backend/tests/test_short_research_integrity.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_research_hard_gates.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/short_research_prepaper_review.py`, `backend/scripts/run_combo02_short_research.py`, `backend/tests/test_combo02_short_research_real_batch.py`
- **Imports / protects:** `backend/app/research/short_research_constants.py`, `backend/app/research/short_research_forensics.py`, `backend/app/research/short_research_prepaper_review.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_research_real_batch.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/short_research_prepaper_review.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/short_research_hard_gates.py`, `backend/tests/test_combo02_short_research_prepaper_review.py`
- **Imports / protects:** `backend/app/research/short_research_forensics.py`, `backend/app/research/short_research_hard_gates.py`, `backend/app/research/short_research_quality.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_research_prepaper_review.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/short_research_quality.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/api/routes.py`, `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/short_entry_research/validation.py`, `backend/app/research/short_pullback_rejection/validation.py`, `backend/app/research/short_research_prepaper_review.py`, `backend/tests/test_combo02_short_research_prepaper_review.py`, `backend/tests/test_combo02_short_research_quality.py`, `backend/tests/test_trade_fees.py`
- **Imports / protects:** `backend/app/research/config.py`, `backend/app/research/short_research_constants.py`, `backend/app/research/trade_fees.py`
- **CLI usage:** indirect
- **API route:** yes
- **Test coverage:** `backend/tests/test_combo02_short_research_prepaper_review.py`, `backend/tests/test_combo02_short_research_quality.py`, `backend/tests/test_trade_fees.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/short_research_windows.py`

- **Classification:** UTILITY
- **Imported by:** `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/short_entry_research/validation.py`, `backend/app/research/short_pullback_rejection/runner.py`, `backend/app/research/short_pullback_rejection/validation.py`, `backend/app/research/short_research_diagnostics/runner.py`, `backend/scripts/run_combo02_short_research.py`, `backend/tests/test_combo02_short_diagnostics.py`, `backend/tests/test_combo02_short_entry_research.py` (+3 more)
- **Imports / protects:** `backend/app/research/combo02_candidate_thresholds.py`, `backend/app/research/short_research_constants.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_short_diagnostics.py`, `backend/tests/test_combo02_short_entry_research.py`, `backend/tests/test_combo02_short_research_forensics.py`, `backend/tests/test_combo02_short_research_prepaper_review.py`, `backend/tests/test_combo02_short_research_real_batch.py`
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** helpers/constants with no standalone product surface

#### `backend/app/research/strategy_candidate_registry.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/api/routes.py`, `backend/app/research/advance_dynamic_candidates.py`, `backend/app/research/candidate_data_health.py`, `backend/app/research/dynamic_candidate_discovery.py`, `backend/app/research/dynamic_candidate_pipeline.py`, `backend/app/services/v2_candidate_paper_watcher.py`, `backend/scripts/advance_dynamic_candidates.py`, `backend/scripts/run_dynamic_candidate_discovery.py` (+3 more)
- **Imports / protects:** `backend/app/research/candidate_state_machine.py`, `backend/app/research/dynamic_candidate_constants.py`
- **CLI usage:** yes
- **API route:** yes
- **Test coverage:** `backend/tests/test_combo02_short_research.py`, `backend/tests/test_dynamic_candidate_pipeline.py`, `backend/tests/test_e2e_paper_candidate_safety_validation.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/trade_fees.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/metrics.py`, `backend/app/research/combo02_candidate_research.py`, `backend/app/research/combo02_short_research.py`, `backend/app/research/multi_cap_strategies/config.py`, `backend/app/research/multi_cap_strategies/runner.py`, `backend/app/research/service.py`, `backend/app/research/short_entry_research/runner.py` (+13 more)
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_short_pullback_rejection_research.py`, `backend/tests/test_trade_fees.py`
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

#### `backend/app/research/trade_plan_forensics/__init__.py`

- **Classification:** WRAPPER
- **Imported by:** `backend/app/api/routes.py`
- **Imports / protects:** `backend/app/research/trade_plan_forensics/service.py`
- **CLI usage:** indirect
- **API route:** yes
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Wrapper action:** KEEP
- **Safe to archive:** NO
- **Reason:** thin re-export or pass-through without unique business logic

#### `backend/app/research/trade_plan_forensics/aggregates.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/trade_plan_forensics/hypotheses.py`, `backend/app/research/trade_plan_forensics/service.py`, `backend/tests/test_trade_plan_forensics.py`
- **Imports / protects:** `backend/app/research/trade_plan_forensics/schemas.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_trade_plan_forensics.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/trade_plan_forensics/hypotheses.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/trade_plan_forensics/service.py`, `backend/tests/test_trade_plan_forensics.py`
- **Imports / protects:** `backend/app/research/trade_plan_forensics/aggregates.py`, `backend/app/research/trade_plan_forensics/schemas.py`, `backend/app/research/trade_plan_forensics/thresholds.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_trade_plan_forensics.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/trade_plan_forensics/ingest.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/trade_plan_forensics/service.py`, `backend/tests/test_trade_plan_forensics.py`
- **Imports / protects:** `backend/app/research/backtest_job.py`, `backend/app/research/trade_plan_forensics/schemas.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_trade_plan_forensics.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/trade_plan_forensics/reconstruct.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/trade_plan_forensics/service.py`, `backend/tests/test_trade_plan_forensics.py`
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/postgres_ohlcv.py`, `backend/app/research/trade_plan_forensics/schemas.py`, `backend/app/research/trade_plan_forensics/thresholds.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_trade_plan_forensics.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/trade_plan_forensics/report.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/trade_plan_forensics/service.py`
- **Imports / protects:** `backend/app/research/trade_plan_forensics/thresholds.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/trade_plan_forensics/schemas.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/trade_plan_forensics/aggregates.py`, `backend/app/research/trade_plan_forensics/hypotheses.py`, `backend/app/research/trade_plan_forensics/ingest.py`, `backend/app/research/trade_plan_forensics/reconstruct.py`, `backend/tests/test_trade_plan_forensics.py`
- **Imports / protects:** `backend/app/research/trade_plan_forensics/thresholds.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_trade_plan_forensics.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/trade_plan_forensics/service.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/trade_plan_forensics/__init__.py`
- **Imports / protects:** `backend/app/research/trade_plan_forensics/aggregates.py`, `backend/app/research/trade_plan_forensics/hypotheses.py`, `backend/app/research/trade_plan_forensics/ingest.py`, `backend/app/research/trade_plan_forensics/reconstruct.py`, `backend/app/research/trade_plan_forensics/report.py`, `backend/app/research/trade_plan_forensics/thresholds.py`
- **CLI usage:** indirect
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/trade_plan_forensics/thresholds.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/trade_plan_forensics/hypotheses.py`, `backend/app/research/trade_plan_forensics/reconstruct.py`, `backend/app/research/trade_plan_forensics/report.py`, `backend/app/research/trade_plan_forensics/schemas.py`, `backend/app/research/trade_plan_forensics/service.py`, `backend/tests/test_trade_plan_forensics.py`
- **CLI usage:** indirect
- **API route:** no
- **Test coverage:** `backend/tests/test_trade_plan_forensics.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/trend_regime_diagnostic.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** `backend/app/research/failure_regime_decomposition.py`, `backend/app/research/htf_alignment_sensitivity.py`, `backend/scripts/run_failure_regime_decomposition.py`, `backend/scripts/run_trend_regime_diagnostic.py`, `backend/tests/test_trend_regime_diagnostic_lookahead.py`
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_trend_regime_diagnostic_lookahead.py`
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/app/research/v1_production.py`

- **Classification:** ACTIVE_CORE
- **Imported by:** `backend/app/engines/orchestrator.py`, `backend/app/research/backtest_job.py`, `backend/app/research/backtest_ui_config.py`, `backend/app/research/combo02_candidate_research.py`, `backend/app/research/combo02_candidate_selector.py`, `backend/app/services/paper_trade.py`, `backend/app/services/screener_presentation.py`, `backend/app/services/v1_paper_watcher.py` (+8 more)
- **CLI usage:** yes
- **API route:** no
- **Test coverage:** `backend/tests/test_combo02_candidate_eligibility.py`, `backend/tests/test_dynamic_candidate_pipeline.py`, `backend/tests/test_e2e_paper_candidate_safety_validation.py`, `backend/tests/test_paper_execution_safety.py`, `backend/tests/test_v1_paper_watcher.py`, `backend/tests/test_v1_production.py`
- **Last modification:** 2026-10-04T01:52:09+05:30 `69adc60` code update
- **Safe to archive:** NO
- **Reason:** shared research foundation used by multiple pipelines

### scripts

#### `backend/scripts/_ohlcv_coverage.py`

- **Classification:** UTILITY
- **Imported by:** _(none found)_
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Safe to archive:** NO
- **Reason:** helpers/constants with no standalone product surface

#### `backend/scripts/_ohlcv_coverage_check.py`

- **Classification:** UTILITY
- **Imported by:** _(none found)_
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T22:13:35+05:30 `ca3a991` Add COMBO_02 regime/SHORT research, opt-in gates, and backtest UI depth.
- **Safe to archive:** NO
- **Reason:** helpers/constants with no standalone product surface

#### `backend/scripts/_paper_trade_smoke.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** _(none found)_
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/scripts/_price_drift.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/postgres_ohlcv.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/scripts/acceptance_bos_research.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Safe to archive:** NO
- **Reason:** pytest suite protecting research behavior

#### `backend/scripts/advance_dynamic_candidates.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/advance_dynamic_candidates.py`, `backend/app/research/strategy_candidate_registry.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/analyze_candle12_v2_results.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** _(none found)_
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/scripts/audit_combo02_htf_fidelity.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/combination_engine.py`, `backend/app/research/config.py`, `backend/app/research/service.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-03T20:45:53+05:30 `8f88377` Freeze COMBO_02 v1 HTF-gated LONG (tag candidate).
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/audit_multi_cap_baseline_forensics.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`, `backend/app/research/historical_market_cap/__init__.py`, `backend/app/research/historical_market_cap/classifier.py`, `backend/app/research/historical_market_cap/repository.py`, `backend/app/research/multi_cap_strategies/baseline_forensics.py`, `backend/app/research/multi_cap_strategies/common.py`, `backend/app/research/multi_cap_strategies/config.py` (+5 more)
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/benchmark_research_pipeline.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/bos_strategy_comparison/lifecycle.py`, `backend/app/research/bos_strategy_comparison/runner.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`, `backend/app/research/data_cache/config.py` (+10 more)
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/scripts/cap_classification_coverage.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`, `backend/app/research/historical_market_cap/__init__.py`, `backend/app/research/historical_market_cap/classifier.py`, `backend/app/research/historical_market_cap/repository.py`, `backend/app/research/historical_market_cap/service.py`, `backend/app/research/multi_cap_strategies/adapter.py`, `backend/app/research/multi_cap_strategies/common.py` (+5 more)
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/check_ohlcv_coverage.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/diag_impulse_pullback_gates.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_engine.py`, `backend/app/research/config.py`, `backend/app/research/postgres_ohlcv.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Experimental bucket:** ONE_TIME_ARTIFACT
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/scripts/diagnose_backtest_15m.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/backtest_timing.py`, `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`, `backend/app/research/query_utils.py`, `backend/app/research/service.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Experimental bucket:** ONE_TIME_ARTIFACT
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/scripts/expand_ohlcv_history.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/ohlcv_expand.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/live_backtest_parity/run_parity_validation.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/live_backtest_parity/constants.py`, `backend/app/research/live_backtest_parity/runner.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/patch_klines_api.py`

- **Classification:** LEGACY
- **Imported by:** _(none found)_
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Experimental bucket:** LEGACY_CANDIDATE
- **Safe to archive:** NO
- **Reason:** superseded by a newer implementation but still present

#### `backend/scripts/prepare_research_dataset.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/data_cache/config.py`, `backend/app/research/data_cache/prepare.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/profile_strategy_evaluation.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/bos_strategy_comparison/lifecycle.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`, `backend/app/research/data_cache/golden_equality.py`, `backend/app/research/data_cache/stage_profiler.py`, `backend/app/research/postgres_ohlcv.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/scripts/research_short_failures.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/combination_engine.py`, `backend/app/research/config.py`, `backend/app/research/postgres_ohlcv.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/scripts/run_bos_strategy_comparison.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/service.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-03T08:18:03+05:30 `e3cc5fc` Add BOS strategy comparison research suite without changing live signals.
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_candle12_research.py`

- **Classification:** LEGACY
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/candle12_hypothesis.py`, `backend/app/research/config.py`, `backend/app/research/schemas.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Experimental bucket:** LEGACY_CANDIDATE
- **Safe to archive:** NO
- **Reason:** superseded by a newer implementation but still present

#### `backend/scripts/run_candle12_v2_research.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/candle12_v2.py`, `backend/app/research/config.py`, `backend/app/research/postgres_ohlcv.py`, `backend/app/research/rolling_structure.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_combo02_candidate_research.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/combo02_candidate_research.py`, `backend/app/research/combo02_candidate_selector.py`, `backend/app/research/combo02_candidate_thresholds.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-04T01:52:09+05:30 `69adc60` code update
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_combo02_short_diagnostics.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/short_research_constants.py`, `backend/app/research/short_research_diagnostics/runner.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_combo02_short_entry_research.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/short_entry_research/constants.py`, `backend/app/research/short_entry_research/runner.py`, `backend/app/research/short_research_constants.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_combo02_short_research.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/short_research_constants.py`, `backend/app/research/short_research_hard_gates.py`, `backend/app/research/short_research_windows.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_dynamic_candidate_discovery.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/dynamic_candidate_discovery.py`, `backend/app/research/strategy_candidate_registry.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-04T02:38:38+05:30 `04a30ce` Separate Futures Screener discovery UI from COMBO_02 v1 eligibility.
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_failure_regime_decomposition.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/config.py`, `backend/app/research/failure_regime_decomposition.py`, `backend/app/research/postgres_ohlcv.py`, `backend/app/research/trend_regime_diagnostic.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_hl_lh_long_short.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/combination_engine.py`, `backend/app/research/config.py`, `backend/app/research/postgres_ohlcv.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_htf_alignment_sensitivity.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/htf_alignment_sensitivity.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T22:13:35+05:30 `ca3a991` Add COMBO_02 regime/SHORT research, opt-in gates, and backtest UI depth.
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_impulse_pullback_hl_lh.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_impulse_pullback_hl_lh_local.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`, `backend/app/research/postgres_ohlcv.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_multi_cap_full_research.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`, `backend/app/research/historical_market_cap/__init__.py`, `backend/app/research/historical_market_cap/classifier.py`, `backend/app/research/historical_market_cap/repository.py`, `backend/app/research/historical_market_cap/service.py`, `backend/app/research/metrics.py`, `backend/app/research/multi_cap_strategies/common.py` (+8 more)
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_research_data_pipeline.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/data_pipeline/__init__.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_s3_forensic_sep2024.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/service.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Experimental bucket:** ONE_TIME_ARTIFACT
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/scripts/run_s3_htf_sensitivity.py`

- **Classification:** EXPERIMENTAL
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/service.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Experimental bucket:** REUSABLE_RESEARCH
- **Safe to archive:** NO
- **Reason:** research-only diagnostic/sensitivity/forensic package

#### `backend/scripts/run_short_policy_sensitivity.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/short_policy_sensitivity.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T22:13:35+05:30 `ca3a991` Add COMBO_02 regime/SHORT research, opt-in gates, and backtest UI depth.
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_short_pullback_rejection_research.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/short_pullback_rejection/runner.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/run_trend_regime_diagnostic.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/config.py`, `backend/app/research/postgres_ohlcv.py`, `backend/app/research/trend_regime_diagnostic.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/select_combo02_candidates.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/combo02_candidate_selector.py`, `backend/app/research/combo02_candidate_thresholds.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-04T01:52:09+05:30 `69adc60` code update
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/select_top_coin_candidates.py`

- **Classification:** WRAPPER
- **Imported by:** _(none found)_
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-04T01:52:09+05:30 `69adc60` code update
- **Duplicate of:** `backend/scripts/select_combo02_candidates.py`
- **Wrapper action:** LEGACY_CANDIDATE
- **Safe to archive:** NO
- **Reason:** thin re-export or pass-through without unique business logic

#### `backend/scripts/v1_monitor_paper.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/v1_production.py`
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-04T01:52:09+05:30 `69adc60` code update
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/validate_multi_cap_strategies.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/multi_cap_strategies/adapter.py`, `backend/app/research/multi_cap_strategies/cap_filter.py`, `backend/app/research/multi_cap_strategies/common.py`, `backend/app/research/multi_cap_strategies/config.py`, `backend/app/research/multi_cap_strategies/fvg.py`, `backend/app/research/multi_cap_strategies/large_cap_sweep_choch.py`, `backend/app/research/multi_cap_strategies/mid_cap_fvg_discount.py`, `backend/app/research/multi_cap_strategies/reference.py` (+3 more)
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/verify_coverage.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

#### `backend/scripts/verify_coverage_phase.py`

- **Classification:** CLI_ENTRYPOINT
- **Imported by:** _(none found)_
- **CLI usage:** entrypoint
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Wrapper action:** CLI_ONLY
- **Safe to archive:** NO
- **Reason:** script entry point invoked manually for research runs

### tests

#### `backend/tests/test_backtest_job.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/backtest_job.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_backtest_job_progress.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/backtest_job.py`, `backend/app/research/backtest_timing.py`, `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/postgres_ohlcv.py`
- **CLI usage:** no
- **API route:** no
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_backtest_lifecycle_integration.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/lifecycle.py`, `backend/app/research/bos_strategy_comparison/runner.py`, `backend/app/research/bos_strategy_comparison/strategies.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_backtest_ui_config.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/backtest_job.py`, `backend/app/research/backtest_ui_config.py`, `backend/app/research/service.py`
- **CLI usage:** no
- **API route:** no
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_bos_research.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/combination_engine.py`, `backend/app/research/config.py`, `backend/app/research/data_quality.py`, `backend/app/research/metrics.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_bos_research_compare_api.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`, `backend/app/research/metrics.py`, `backend/app/research/query_utils.py`, `backend/app/research/schemas.py`, `backend/app/research/service.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_bos_strategy_comparison.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/data_quality.py`, `backend/app/research/bos_strategy_comparison/engine.py`, `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/bos_strategy_comparison/metrics.py`, `backend/app/research/bos_strategy_comparison/runner.py`, `backend/app/research/bos_strategy_comparison/schemas.py`, `backend/app/research/bos_strategy_comparison/strategies.py` (+3 more)
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T08:18:03+05:30 `e3cc5fc` Add BOS strategy comparison research suite without changing live signals.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_candle12_v2.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/candle12_v2.py`, `backend/app/research/config.py`, `backend/app/research/data_quality.py`, `backend/app/research/rolling_structure.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_combo02_candidate_eligibility.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combo02_candidate_eligibility.py`, `backend/app/research/combo02_candidate_research.py`, `backend/app/research/combo02_candidate_selector.py`, `backend/app/research/combo02_candidate_thresholds.py`, `backend/app/research/v1_production.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_combo02_htf_gate.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/combination_engine.py`, `backend/app/research/config.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T20:45:53+05:30 `8f88377` Freeze COMBO_02 v1 HTF-gated LONG (tag candidate).
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_combo02_short_diagnostics.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/combo02_short_research.py`, `backend/app/research/short_research_diagnostics/ablation.py`, `backend/app/research/short_research_diagnostics/classifiers.py`, `backend/app/research/short_research_diagnostics/constants.py`, `backend/app/research/short_research_diagnostics/fee_sensitivity.py`, `backend/app/research/short_research_diagnostics/implementation_verify.py`, `backend/app/research/short_research_diagnostics/metrics.py`, `backend/app/research/short_research_windows.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_combo02_short_entry_research.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/short_entry_research/constants.py`, `backend/app/research/short_entry_research/entry_classification.py`, `backend/app/research/short_entry_research/path_metrics.py`, `backend/app/research/short_entry_research/retest_fills.py`, `backend/app/research/short_entry_research/validation.py`, `backend/app/research/short_entry_research/variants.py`, `backend/app/research/short_research_diagnostics/metrics.py`, `backend/app/research/short_research_windows.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules
- **Note:** may become obsolete if consolidation archives its target

#### `backend/tests/test_combo02_short_research.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/candidate_state_machine.py`, `backend/app/research/combo02_short_research.py`, `backend/app/research/short_research_constants.py`, `backend/app/research/strategy_candidate_registry.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_combo02_short_research_api.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/combo02_candidate_thresholds.py`, `backend/app/research/combo02_short_research.py`, `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/short_research_constants.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_combo02_short_research_forensics.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/combo02_candidate_thresholds.py`, `backend/app/research/combo02_short_research.py`, `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/short_research_constants.py`, `backend/app/research/short_research_forensics.py`, `backend/app/research/short_research_windows.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_combo02_short_research_prepaper_review.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/combo02_short_research.py`, `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/short_research_constants.py`, `backend/app/research/short_research_forensics.py`, `backend/app/research/short_research_prepaper_review.py`, `backend/app/research/short_research_quality.py`, `backend/app/research/short_research_windows.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_combo02_short_research_quality.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/combo02_candidate_thresholds.py`, `backend/app/research/combo02_short_research.py`, `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/short_research_constants.py`, `backend/app/research/short_research_quality.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_combo02_short_research_real_batch.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/combo02_short_research.py`, `backend/app/research/combo02_short_research_runner.py`, `backend/app/research/short_research_constants.py`, `backend/app/research/short_research_forensics.py`, `backend/app/research/short_research_hard_gates.py`, `backend/app/research/short_research_windows.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_direction_primitives.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_dynamic_candidate_pipeline.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/__init__.py`, `backend/app/research/advance_dynamic_candidates.py`, `backend/app/research/candidate_data_health.py`, `backend/app/research/candidate_state_machine.py`, `backend/app/research/dynamic_candidate_constants.py`, `backend/app/research/strategy_candidate_registry.py`, `backend/app/research/v1_production.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_e2e_paper_candidate_safety_validation.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/candidate_state_machine.py`, `backend/app/research/dynamic_candidate_constants.py`, `backend/app/research/strategy_candidate_registry.py`, `backend/app/research/v1_production.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_failure_regime_decomposition.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/failure_regime_decomposition.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-02T22:13:35+05:30 `ca3a991` Add COMBO_02 regime/SHORT research, opt-in gates, and backtest UI depth.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_historical_market_cap.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/config.py`, `backend/app/research/historical_market_cap/classifier.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_history_coverage_diagnostics.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/diagnostics.py`, `backend/app/research/data_pipeline/history_coverage.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_htf_alignment_sensitivity.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/htf_alignment_sensitivity.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-02T22:13:35+05:30 `ca3a991` Add COMBO_02 regime/SHORT research, opt-in gates, and backtest UI depth.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_htf_forensics.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/bos_strategy_comparison/htf_forensics.py`, `backend/app/research/bos_strategy_comparison/s3_diagnostics.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules
- **Note:** may become obsolete if consolidation archives its target

#### `backend/tests/test_lifecycle.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/lifecycle.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_live_backtest_parity.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/live_backtest_parity/candle_validation.py`, `backend/app/research/live_backtest_parity/constants.py`, `backend/app/research/live_backtest_parity/entry_price.py`, `backend/app/research/live_backtest_parity/models.py`, `backend/app/research/live_backtest_parity/replay.py`, `backend/app/research/live_backtest_parity/report.py`, `backend/app/research/live_backtest_parity/runner.py`, `backend/app/research/live_backtest_parity/shadow_compare.py` (+1 more)
- **CLI usage:** no
- **API route:** no
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_multi_cap_baseline_forensics.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/config.py`, `backend/app/research/multi_cap_strategies/baseline_forensics.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_multi_cap_strategies.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/strategies.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/multi_cap_strategies/adapter.py`, `backend/app/research/multi_cap_strategies/cap_filter.py`, `backend/app/research/multi_cap_strategies/common.py`, `backend/app/research/multi_cap_strategies/config.py`, `backend/app/research/multi_cap_strategies/fvg.py`, `backend/app/research/multi_cap_strategies/large_cap_sweep_choch.py` (+5 more)
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_normalizer.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-02T11:48:25+05:30 `898e6f3` Initial commit: crypto screener with HL long strategy backtest UI.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_ohlcv_expand.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/ohlcv_expand.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_paper_execution_safety.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/v1_production.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T01:52:09+05:30 `69adc60` code update
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_paper_risk.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T01:52:09+05:30 `69adc60` code update
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_paper_trade.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T01:52:09+05:30 `69adc60` code update
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_pullback_diagnostics.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/pullback_diagnostics.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_research_data_cache.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/data_cache/cache_key.py`, `backend/app/research/data_cache/checkpoint.py`, `backend/app/research/data_cache/config.py`, `backend/app/research/data_cache/dataset.py`, `backend/app/research/data_cache/feature_cache.py`, `backend/app/research/data_cache/manifest.py`, `backend/app/research/data_cache/metrics.py`, `backend/app/research/data_cache/parquet_store.py` (+1 more)
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_research_data_pipeline.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/data_pipeline/__init__.py`, `backend/app/research/data_pipeline/checkpoint.py`, `backend/app/research/data_pipeline/config.py`, `backend/app/research/data_pipeline/coverage.py`, `backend/app/research/data_pipeline/deduplicator.py`, `backend/app/research/data_pipeline/event_store.py`, `backend/app/research/data_pipeline/feature_store.py`, `backend/app/research/data_pipeline/locks.py` (+5 more)
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_research_gate.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-02T22:13:35+05:30 `ca3a991` Add COMBO_02 regime/SHORT research, opt-in gates, and backtest UI depth.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_research_pipeline_perf.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/data_cache/cache_key.py`, `backend/app/research/data_cache/cache_manager.py`, `backend/app/research/data_cache/config.py`, `backend/app/research/data_cache/golden_equality.py`, `backend/app/research/data_cache/manifest.py`, `backend/app/research/data_cache/parquet_store.py`, `backend/app/research/data_cache/validation.py`
- **CLI usage:** no
- **API route:** no
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_s3_diagnostics.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/bos_strategy_comparison/lifecycle.py`, `backend/app/research/bos_strategy_comparison/s3_diagnostics.py`, `backend/app/research/combination_engine.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules
- **Note:** may become obsolete if consolidation archives its target

#### `backend/tests/test_s3_htf_sensitivity.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/config.py`, `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/bos_strategy_comparison/htf_variants.py`, `backend/app/research/bos_strategy_comparison/metrics.py`, `backend/app/research/bos_strategy_comparison/s3_htf_sensitivity.py`, `backend/app/research/bos_strategy_comparison/schemas.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T14:27:14+05:30 `1b1156c` Add paper trading, diagnostics, sentiment providers, and always-on Docker run docs.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules
- **Note:** may become obsolete if consolidation archives its target

#### `backend/tests/test_short_policy_sensitivity.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/htf_alignment_sensitivity.py`, `backend/app/research/short_policy_sensitivity.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-02T22:13:35+05:30 `ca3a991` Add COMBO_02 regime/SHORT research, opt-in gates, and backtest UI depth.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_short_pullback_rejection_research.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/__init__.py`, `backend/app/research/combo02_candidate_research.py`, `backend/app/research/combo02_short_research.py`, `backend/app/research/short_pullback_rejection/constants.py`, `backend/app/research/short_pullback_rejection/regime.py`, `backend/app/research/short_pullback_rejection/rejection.py`, `backend/app/research/short_pullback_rejection/signals.py`, `backend/app/research/short_pullback_rejection/simulation.py` (+5 more)
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules
- **Note:** may become obsolete if consolidation archives its target

#### `backend/tests/test_short_research_integrity.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/__init__.py`, `backend/app/research/combo02_short_research.py`, `backend/app/research/short_research_constants.py`, `backend/app/research/short_research_forensics.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T17:54:29+05:30 `e17ede8` restore full short research pipeline and direction tests
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_stage_profiler.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/data_cache/stage_profiler.py`
- **CLI usage:** no
- **API route:** no
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_trade_fees.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/short_research_quality.py`, `backend/app/research/trade_fees.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T09:12:58+05:30 `de542f8` backtest cache added
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_trade_plan_entry_annotation.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T01:52:09+05:30 `69adc60` code update
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_trade_plan_forensics.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_strategy_comparison/htf.py`, `backend/app/research/trade_plan_forensics/aggregates.py`, `backend/app/research/trade_plan_forensics/hypotheses.py`, `backend/app/research/trade_plan_forensics/ingest.py`, `backend/app/research/trade_plan_forensics/reconstruct.py`, `backend/app/research/trade_plan_forensics/schemas.py`, `backend/app/research/trade_plan_forensics/thresholds.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-03T20:15:31+05:30 `2286e3a` working code
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_trend_regime_diagnostic_lookahead.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/trend_regime_diagnostic.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-02T22:13:35+05:30 `ca3a991` Add COMBO_02 regime/SHORT research, opt-in gates, and backtest UI depth.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_v1_paper_backtest_parity.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/config.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T01:52:09+05:30 `69adc60` code update
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_v1_paper_watcher.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/bos_combinations.py`, `backend/app/research/combination_backtest.py`, `backend/app/research/combination_engine.py`, `backend/app/research/config.py`, `backend/app/research/v1_production.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T02:38:38+05:30 `04a30ce` Separate Futures Screener discovery UI from COMBO_02 v1 eligibility.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

#### `backend/tests/test_v1_production.py`

- **Classification:** ACTIVE_TEST
- **Imported by:** _(none found)_
- **Imports / protects:** `backend/app/research/v1_production.py`
- **CLI usage:** no
- **API route:** no
- **Last modification:** 2026-10-04T02:38:38+05:30 `04a30ce` Separate Futures Screener discovery UI from COMBO_02 v1 eligibility.
- **Safe to archive:** NO
- **Reason:** pytest coverage for research modules

