# Research Cleanup Plan

**STATUS: PROPOSAL ONLY — DO NOT EXECUTE YET.**

This plan follows the audit in `research_codebase_inventory.json` / `.md`.  
No deletes, moves, renames, or behavior changes have been performed.

Authority rule when consolidating duplicates:

1. Runtime imports  
2. API usage  
3. Tests  
4. Production/research integration  
5. Correctness  
6. Documentation  
7. Git history  

**Not** “newest file wins.”

---

## Archiving strategy (recommended later)

Prefer:

```text
backend/research_archive/
  YYYYMMDD_<topic>/
    README.md          # why archived, how to reproduce, last known commit
    <moved files>
```

Rules:

- Archive **only after** proving zero active imports from `app/`, API routes, watchers, and CI tests you intend to keep green
- Do **not** put modules that are still imported into archive
- Keep golden outputs / CSV reports with the archived scripts when reproducible value remains
- Prefer archive over delete for one-time diagnostics and superseded experiments
- Alternative in-tree location `backend/app/research/archive/` is OK only if packages remain non-imported

---

## A. KEEP

Active spines and shared foundations. Do not archive.

| File / area | Reason | Dependencies | Tests | Risk | Recommended action |
|---|---|---|---|---|---|
| `app/research/postgres_ohlcv.py` | Authoritative OHLCV SQL reads | Almost all research services | `test_ohlcv_expand`, cache/pipeline tests | HIGH if touched | Keep; document as sole reader |
| `app/research/combination_engine.py` | COMBO evaluation | API, paper watchers, cache runner | `test_bos_research*`, v1/paper tests | HIGH | Keep |
| `app/research/combination_backtest.py` | Shared backtest/exits | API, candle12, multi-cap, paper | bos/backtest/parity tests | HIGH | Keep |
| `app/research/bos_combinations.py` | Combo catalog | engine, watchers | bos research tests | HIGH | Keep |
| `app/research/service.py` | COMBO API service | routes | bos API tests | HIGH | Keep |
| `app/research/metrics.py` | Core trade metrics | many pipelines | metrics consumers | HIGH | Keep |
| `app/research/data_quality.py` | Authoritative `verify_ohlcv` | cache validation, backtests | pipeline/cache tests | HIGH | Keep |
| `app/research/bos_strategy_comparison/{lifecycle,htf,engine,runner,strategies,service}.py` | Strategy comparison spine | API + pipeline events | `test_bos_strategy_comparison`, lifecycle/htf tests | HIGH | Keep |
| `app/research/data_cache/{cache_manager,prepare,parquet_store,validation,runner}.py` | Active OHLCV cache | service when enabled; CLI prepare | `test_research_data_cache` | MEDIUM | Keep |
| `app/research/data_pipeline/**` (except unused wrappers) | History ingest + PG features/events | CLI pipeline; coverage gates | `test_research_data_pipeline` | HIGH | Keep |
| `app/research/candle12_v2.py`, `rolling_structure.py` | Active Candle12 path | CLI V2 | `test_candle12_v2` | MEDIUM | Keep |
| `app/research/multi_cap_strategies/**` (non-forensic) | Active multi-cap API | routes | `test_multi_cap_strategies` | MEDIUM | Keep |
| Candidate stack (`dynamic_candidate_*`, `combo02_candidate_*`, `strategy_candidate_registry`, `candidate_state_machine`, `advance_dynamic_candidates`, `v1_production`) | Paper/candidate pipeline | routes + services | eligibility/pipeline/v1 tests | HIGH | Keep |
| `app/research/live_backtest_parity/**` | Parity validation | paper_trade entry_price; CLI | `test_live_backtest_parity` | HIGH | Keep |
| `app/research/trade_fees.py`, `backtest_job.py`, `backtest_ui_config.py` | Fees + UI jobs | API / paper | fee/job/ui tests | HIGH | Keep |
| Package `__init__.py` facades | Stable import surfaces | various | indirect | LOW | Keep |

---

## B. CONSOLIDATE

Overlap without deleting yet. Prefer documenting authority first, then thin adapters.

| File | Reason | Dependencies | Tests | Risk | Recommended action |
|---|---|---|---|---|---|
| `data_cache/postgres_loader.py` vs `postgres_ohlcv.py` | Thin metrics wrapper | cache_manager | cache tests | LOW | Keep wrapper; document A=`postgres_ohlcv` |
| `bos_strategy_comparison/data_quality.py` vs `data_quality.py` | Universe audits wrap `verify_ohlcv` | strategy comparison service | coverage/DQ tests | LOW | Consolidate docs; optionally rename to `universe_audit.py` later |
| `data_cache/validation.py` vs `data_quality.py` | Cache schema checks + verify | cache_manager | cache tests | LOW | Keep both roles; avoid new verifiers |
| `bos_strategy_comparison/metrics.py` vs `metrics.py` | Layered metrics | strategy comparison | strategy tests | MEDIUM | Keep layering; no formula fork |
| `data_cache/feature_cache.py` vs `data_pipeline/feature_store.py` | Two feature systems | prepare/benchmark vs pipeline | cache + pipeline tests | HIGH | Decide product intent before merge; engines ignore FeatureCache today |
| `data_cache/event_cache.py` vs `data_pipeline/event_store.py` | EventCache unused wrapper | none (engines) | none direct | MEDIUM | Wire or archive EventCache; authority=`event_store` |
| `historical_market_cap/*` vs `multi_cap_strategies/cap_filter.py` | Historical vs live cap | scripts vs API | historical + multi_cap tests | HIGH | Document dual model; do not silently unify |
| `htf.py` vs `signals/mtf_engine.py` | Research vs live HTF | combination_engine / engine | htf tests | HIGH | Keep research HTF; equality-test any shared extract |
| ATR in FeatureCache vs `engines.mtf.indicators.atr_series` | Duplicate ATR math | FeatureCache only | feature cache tests | MEDIUM | Stop presenting FeatureCache ATR as engine input until wired |
| Candle12 V1 vs V2 | Superseded hypothesis path | V1 script only | primarily V2 tests | LOW | Point all docs/CLIs to V2; archive V1 after one release |

---

## C. ARCHIVE

Candidates **after** dependency proof. Recommended destination: `backend/research_archive/`.

| File | Reason | Dependencies | Tests | Risk | Recommended action |
|---|---|---|---|---|---|
| `app/research/data_cache/event_cache.py` | No importers; engines unused | self + comments/benchmark notes | none | LOW | Archive or wire in a dedicated PR; update benchmark REPORT |
| `app/research/candle12_hypothesis.py` | Superseded by V2 | `scripts/run_candle12_research.py` | legacy V1 path | LOW–MEDIUM | Archive with V1 script + sample outputs |
| `scripts/run_candle12_research.py` | Legacy CLI | hypothesis module | indirect | LOW | Archive with V1 |
| `scripts/select_top_coin_candidates.py` | Back-compat `runpy` wrapper | none else | none | LOW | Archive after updating any external docs/aliases |
| `scripts/run_s3_forensic_sep2024.py` | Dated one-time forensic | s3 diagnostics modules | `test_s3_*` may still need modules | MEDIUM | Archive script; keep library modules until tests retire |
| `scripts/diag_impulse_pullback_gates.py` | One-time probe | research helpers | none required | LOW | Archive with README of findings |
| `scripts/diagnose_backtest_15m.py` | Diagnostic artifact | combination/cache | none | LOW | Archive after capturing summary JSON |
| `scripts/patch_klines_api.py` | Legacy patch script | none | none | LOW | Archive or delete-after-validation |
| Dated forensic CLIs under `scripts/run_s3_*`, impulse local variants | Experiment runners | experimental packages | some have tests | MEDIUM | Archive runners that are not in CI; keep packages until tests say otherwise |

---

## D. DELETE ONLY AFTER VALIDATION

Nothing recommended for immediate deletion. Future delete list **only if** archive retention is unwanted and CI is green:

| File | Reason | Dependencies | Tests | Risk | Recommended action |
|---|---|---|---|---|---|
| `scripts/select_top_coin_candidates.py` | Pure wrapper | none | none | LOW | Delete only after archive + alias notice |
| `scripts/patch_klines_api.py` | Appears obsolete | none | none | LOW | Confirm no external ops use; then delete |
| `data_cache/event_cache.py` | Unused | none | none | MEDIUM | Delete only if product decision is “no event parquet cache” |

**Do not delete** experimental packages with tests (`short_*`, `trade_plan_forensics`, s3 diagnostics libraries) without migrating or retiring those tests intentionally.

---

## E. UNKNOWN

No inventory entries remain classified `UNKNOWN` after import-graph resolution.

Items that still need a **product decision** (not file-class unknown):

| Topic | Why undecided | Recommended action |
|---|---|---|
| Should FeatureCache columns feed COMBO engines? | Built but unused at eval time | Either wire behind equality gate or mark explicitly “offline analytics only” |
| Should EventCache be completed? | Implemented, unused | Wire into prepare/runner **or** archive |
| Should multi-cap API use HISTORICAL_CAP? | Offline scripts use historical; API uses live | Explicit design choice before consolidation |
| Which SHORT research package is “current”? | Multiple parallel packages + API short-candidates | Document primary SHORT path in AI_CONTEXT when chosen |

---

## Highest-priority consolidation candidates

1. **Document dual evaluation spines** (COMBO service vs strategy-comparison lifecycle) — already started in `ARCHITECTURE.md` / `AI_CONTEXT.md`
2. **EventCache**: wire or archive (`data_cache/event_cache.py`)
3. **FeatureCache vs engine ATR**: stop ambiguity; equality-gate if wiring
4. **Candle12 V1 → archive** after confirming no external runners
5. **Cap model clarity**: live `cap_filter` vs `historical_market_cap`

---

## Test impact flags

Tests that may become obsolete if consolidation archives targets:

- Any remaining coverage of `candle12_hypothesis` via V1-only paths
- Future tests of `EventCache` if archived without replacement
- `test_s3_*` / pullback diagnostic tests if those experimental modules are archived
- SHORT package tests (`test_short_pullback_*`, `test_combo02_short_entry_*`, diagnostics) if those experiments are retired

Do **not** delete tests in the same PR as archival without an explicit replacement guarantee.

---

## Suggested execution order (future PR series)

1. Docs-only PR (this audit) — **current**
2. Tiny PR: mark EventCache / FeatureCache status in code comments + AI_CONTEXT (no behavior change)
3. Archive PR: Candle12 V1 + wrapper CLIs (after grep for external refs)
4. Product decision PR: FeatureCache / EventCache wire-or-archive
5. Only then consider package moves into `backend/research_archive/`

Cleanup risk overall: **MEDIUM** (high if touching COMBO/HTF/lifecycle/paper imports; low for pure unused wrappers).
