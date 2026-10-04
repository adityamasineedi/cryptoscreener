# Research Architecture

Audit map of the research system. **Do not treat filenames as authority — follow runtime imports.**

Companion docs:

1. [`AI_CONTEXT.md`](./AI_CONTEXT.md) — agent entry checklist
2. [`../../scripts/research_codebase_inventory.md`](../../scripts/research_codebase_inventory.md) — full file inventory
3. [`../../scripts/research_cleanup_plan.md`](../../scripts/research_cleanup_plan.md) — proposed cleanup (not executed)

---

## Critical fact

There is **no single** runtime path:

```
PG → cache → features → BOS events → lifecycle → strategy → metrics
```

That chain is aspirational. In practice there are **two primary evaluation spines** plus separate ingest/cache acceleration layers.

---

## Dependency chain (actual)

```
Binance REST
    │
    ▼
data_pipeline.downloader  ──writes──►  PostgreSQL Ohlcv
                                         │
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                    ▼
           postgres_ohlcv          data_cache (opt)     data_pipeline
           (SQL readers)           Parquet OHLCV        feature_store /
                    │                    │              event_store (PG)
                    │                    │
                    │              FeatureCache ──► NOT consumed by COMBO engines
                    │              EventCache ──► UNUSED by engines
                    │
        ┌───────────┴────────────┐
        ▼                        ▼
BosResearchService      BosStrategyComparisonService
combination_backtest    runner + lifecycle
combination_engine      engine + strategies
        │                        │
        └──────────┬─────────────┘
                   ▼
         app/signals/* (BOS, swing, impulse, pullback, retest, stop, target, risk)
                   │
                   ▼
         research.metrics  /  bos_strategy_comparison.metrics
```

---

## DATA

### AUTHORITATIVE (reads for backtests)

`backend/app/research/postgres_ohlcv.py`

- SQL loaders used by API research services, strategy comparison, multi-cap, candle12 V2, and cache miss path.

### AUTHORITATIVE (ingest / history fill)

`backend/app/research/data_pipeline/`

- `downloader.py`, `sync.py`, `normalizer.py`, `validator.py`, `paginator.py`, `planner.py`, `runner.py`
- Writes candles into Postgres (`ON CONFLICT DO NOTHING` style history expansion).
- CLI: `backend/scripts/run_research_data_pipeline.py`

### RESEARCH ADAPTER (optional acceleration)

`backend/app/research/data_cache/`

- `cache_manager.py` → Parquet OHLCV keyed by `ohlcv_v1`
- `postgres_loader.py` → thin wrapper over `postgres_ohlcv`
- Enabled when `RESEARCH_CACHE_ENABLED=true`
- Used by `BosResearchService._load_research_candles` and `prepare_research_dataset`

### NOT authoritative for strategy candles

- In-memory `services.ohlcv_store` — fallback only in some paths
- Candle12 V1 script historically used memory store; V2 uses `postgres_ohlcv`

---

## FEATURES

| Layer | File | Status |
|---|---|---|
| Production ATR | `backend/app/engines/mtf/indicators.py` (`atr_series`) | **AUTHORITATIVE for COMBO/backtest ATR** |
| Research FeatureCache | `backend/app/research/data_cache/feature_cache.py` | Builds ATR/RVOL/EMA parquet columns; **not consumed by COMBO engines** |
| PG feature_store | `backend/app/research/data_pipeline/feature_store.py` | Wraps production engines; persists `research_feature_cache` |
| Event builders | `backend/app/research/data_pipeline/event_store.py` | Builds BOS/lifecycle-derived events for pipeline |

**Do not duplicate ATR/RVOL/swing/BOS in new research code.** Call production engines or existing research wrappers.

---

## EVENTS

### AUTHORITATIVE (production detection)

`backend/app/signals/bos_engine.py`  
`backend/app/signals/impulse_engine.py`  
`backend/app/signals/pullback_engine.py`  
`backend/app/signals/retest_engine.py`  
`backend/app/signals/swing_detector.py`  
`backend/app/signals/trend_engine.py`

Usually reached via `backend/app/signals/signal_engine.py` (`SignalEngine.analyze_timeframe`).

### RESEARCH ADAPTER

| File | Role |
|---|---|
| `bos_strategy_comparison/lifecycle.py` | Multi-bar BOS→impulse→pullback→retest sequencing (WAITING preserved) |
| `combination_engine.py` | Combo predicates + HTF hard gate around `SignalEngine` |
| `rolling_structure.py` | Incremental structure for Candle12 V2 |
| `data_pipeline/event_store.py` | Offline event materialization |
| `data_cache/event_cache.py` | **UNUSED** Parquet/JSON wrapper around event_store |

---

## LIFECYCLE

### AUTHORITATIVE (research multi-bar)

`backend/app/research/bos_strategy_comparison/lifecycle.py`

Used by BOS Strategy Comparison and data_pipeline event building. Calls production `detect_pullback` / `detect_retest` / `SignalEngine` unchanged.

### Production path

`SignalEngine.analyze_timeframe` — live/same-bar evaluation; **not** the multi-bar research lifecycle owner.

---

## STRATEGIES

### BOS Combination Research (COMBO_*)

| Role | File |
|---|---|
| Definitions | `bos_combinations.py` |
| Evaluation | `combination_engine.py` |
| Backtest / exits | `combination_backtest.py` |
| API service | `service.py` (`BosResearchService`) |
| Persistence | `repository.py` |
| Frozen v1 profile | `v1_production.py` |

API: `/research/bos-combinations*`, `/research/long-strategy/backtest*`

### BOS Strategy Comparison (S1–Sx)

| Role | File |
|---|---|
| Catalog | `bos_strategy_comparison/strategies.py` |
| Engine | `bos_strategy_comparison/engine.py` |
| Runner | `bos_strategy_comparison/runner.py` |
| HTF | `bos_strategy_comparison/htf.py` |
| API | `bos_strategy_comparison/service.py` |

API: `/research/bos-strategies*`

### Candle-1 / Candle-2 V2

| Role | File |
|---|---|
| **AUTHORITATIVE** | `candle12_v2.py` + `rolling_structure.py` |
| LEGACY | `candle12_hypothesis.py` |
| CLI | `scripts/run_candle12_v2_research.py` |

### Multi-cap strategies

| Role | File |
|---|---|
| API | `multi_cap_strategies/service.py` |
| Generators | `large_cap_sweep_choch.py`, `mid_cap_fvg_discount.py`, `small_cap_volume_bos.py` |
| Shared exits | `combination_backtest.evaluate_candidate_trades` |
| Live cap filter | `multi_cap_strategies/cap_filter.py` |

API: `/research/multi-cap-strategies*`

### Historical market-cap

`historical_market_cap/` — time-aware HISTORICAL_CAP for offline research scripts. **Not** on the multi-cap HTTP path today.

### Candidate / paper research

```
dynamic_candidate_discovery
  → combo02_candidate_selector
  → strategy_candidate_registry
  → advance_dynamic_candidates
       → candidate_data_health
       → dynamic_candidate_pipeline
            → combo02_candidate_research
                 → BosResearchService.strategy_matrix  (no local COMBO approx)
```

Also: SHORT research packages (`combo02_short_*`, `short_entry_research`, `short_pullback_rejection`, `short_research_diagnostics`) — research-only; must not arm paper/Telegram/v1 without explicit gates.

---

## BACKTEST

| Backtest | Runner | Engine | Dataset |
|---|---|---|---|
| COMBO combinations | `combination_backtest.py` | `combination_engine` + `SignalEngine` | PG / optional Parquet cache |
| UI long-strategy job | `backtest_job.py` → `BosResearchService` | same as COMBO | same |
| Strategy comparison | `bos_strategy_comparison/runner.py` | lifecycle + engine | PG direct |
| Cache benchmark | `data_cache/runner.py` | COMBO backtest | PreparedResearchBundle |
| Candle12 V2 | `candle12_v2.py` | rolling_structure + signals | PG |
| Multi-cap | `multi_cap_strategies/runner.py` | local gens + shared exit sim | PG |
| Live↔backtest parity | `live_backtest_parity/runner.py` | paper_sim / replay | paper + OHLCV |

Shared exit helper: `combination_backtest.resolve_intrabar_outcome` (used by several research paths).

---

## CACHE

| Cache | Owner | Key / version | Invalidated by |
|---|---|---|---|
| OHLCV Parquet | `data_cache/cache_manager.py` | `ohlcv_v1` + symbol/tf/range key | version bump, force_refresh, range/schema mismatch |
| Feature Parquet | `data_cache/feature_cache.py` | `research_features_cache_v1` | feature version / OHLCV fingerprint |
| Event Parquet/JSON | `data_cache/event_cache.py` | `research_events_bos_v1` | **unused by engines** |
| PG feature rows | `data_pipeline/feature_store.py` + `repository.py` | `FEATURE_VERSION` + data fingerprint | fingerprint change |
| In-process candle LRU | `service.py` `_CANDLE_CACHE` | symbol:limit + timeframe | process lifetime |

Env: `RESEARCH_CACHE_ENABLED`, `RESEARCH_CACHE_PATH`, concurrency/memory knobs in `data_cache/config.py`.

---

## REPORTING

| Area | Files |
|---|---|
| Combo metrics | `research/metrics.py` |
| Strategy comparison metrics | `bos_strategy_comparison/metrics.py` (wraps core R metrics) |
| Trade-plan forensics | `trade_plan_forensics/` |
| SHORT diagnostics reports | `short_research_diagnostics/report.py`, package reports |
| Parity report | `live_backtest_parity/report.py` |
| Pipeline benchmark | `scripts/benchmark_research_pipeline.py`, `scripts/research_benchmark_golden/` |

---

## BOS

**AUTHORITATIVE:** `backend/app/signals/bos_engine.py` (via `SignalEngine` or direct detect)

**RESEARCH ADAPTER:**

- `combination_engine.py` — combo gating
- `bos_strategy_comparison/lifecycle.py` — multi-bar sequencing
- `bos_strategy_comparison/engine.py` — S-strategy evaluation
- `rolling_structure.py` — Candle12 incremental structure
- `data_pipeline/event_store.py` — offline event materialization

Do not reimplement BOS detection inside research.

---

## HTF

**AUTHORITATIVE for research hard-gate / as-of maps:**  
`backend/app/research/bos_strategy_comparison/htf.py`

Uses production `swing_detector` + `trend_engine`. Combo hard gate: `combination_engine` → `classify_htf_alignment`.

Live MTF alignment also exists in `backend/app/signals/mtf_engine.py` — do not casually merge without equality checks.

---

## Production coupling (paper / v1)

These research modules are imported outside `app/research/` (treat as high-risk for cleanup):

- `v1_production.py` — orchestrator, paper, screener, watchers
- `bos_combinations.py`, `combination_engine.py`, `combination_backtest.py` — paper watchers
- `config.py`, `short_research_constants.py`, `dynamic_candidate_constants.py`
- `strategy_candidate_registry.py`
- `live_backtest_parity/entry_price.py` — paper_trade
- `combo02_short_research.py` — paper_trade (gate awareness)

---

## Equality / optimization rule

Any cache or hot-path change that can alter bar inputs, HTF as-of maps, fills, or metrics must pass golden/equality gates (`data_cache/golden_equality.py`, benchmark golden reports) before enabling by default.
