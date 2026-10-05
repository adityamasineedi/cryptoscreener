# Full-System Performance Follow-up Report

**Date:** 2026-10-05  
**Scope:** Application performance, monitoring, request cancellation, reporting only  
**Safety lock:** No BOS/HTF/entry/exit/SL/TP/risk/fee/paper/live/Telegram/regime/fingerprint changes

API under test for live probes: `http://127.0.0.1:8001`

---

## A. Files changed

### Backend (behavior-preserving / observability / scheduling)
- `backend/app/ingestion/backfill.py` — queue dedupe, cooldown, window key, max attempts, backlog metrics + state classification + priority policy, active-universe prune
- `backend/app/ingestion/klines.py` — open-interval tip freshness (prior pass)
- `backend/app/services/ohlcv_store.py` — cooperative hydrate yields; batched DB writes
- `backend/app/services/performance.py` — event-loop lag monitor
- `backend/app/main.py` — start/stop lag monitor
- `backend/app/engines/orchestrator.py` — hydrate_active flag
- `backend/app/api/routes.py` — chart metadata + **documented limit contract** (`closed_count`, `open_included`, `limit_applies_to`, `contract_note`)
- `backend/tests/test_backfill_priority.py` — backlog classification / queue / retry tests
- `backend/tests/test_chart_ohlcv_metadata.py` — ordering / metadata tests
- Scripts: `backend/scripts/_followup_*.py`, `_recover_combined_load_10m.py`, `_followup_backlog_snapshot.py`

### Frontend
- `frontend/src/components/LazyResearchMount.tsx` (+ tests)
- `frontend/src/lib/researchPanelCache.ts` / `client.ts` AbortSignal wiring
- `frontend/src/components/BacktestPanel.tsx`, `CandidateResearchPanel.tsx`, `ShortResearchPanel.tsx`, `DynamicCandidatePipelinePanel.tsx`
- `frontend/src/components/RequestCancellation.test.tsx`

### Artifacts
All under `backend/reports/full_system_performance_followup/`.

---

## B. Cold chart results

Full process restart validation (`chart_cold_validation.json`) on BTCUSDT `15m`/`1h` `limit=200`.

| Phase | TF | Source | Returned | Closed | Open tip | Latency avg | Dupes | Sorted | Errors |
|---|---|---|---:|---:|---|---:|---:|---|---|
| Immediate + post-hydrate + 10× | 15m | memory | 201 | 200 | yes | 32.6 ms | 0 | yes | none |
| Immediate + post-hydrate + 10× | 1h | memory | 201 | 200 | yes | 33.4 ms | 0 | yes | none |

Notes:
- Lifespan blocks HTTP until orchestrator start/hydrate progresses, so true “memory-empty HTTP” is rare on a full restart; first ready responses were already **memory**.
- Offline Postgres proof (`_followup_chart_pg_proof.py`): BTCUSDT 15m/1h ×200 from PG in **20.1 ms / 8.4 ms**, `sufficient_for_limit_200=true`.
- Acceptance: sorted ascending, `duplicate_count==0`, no silent empty, REST not required when memory/PG hold the window.

---

## C. Warm chart results (200 vs 201)

Warm remasure (`chart_warm_validation.json`): memory only, stable, no errors.

| TF | Source | Returned | Avg latency |
|---|---|---:|---:|
| 15m | memory | 201 | 55.5 ms |
| 1h | memory | 201 | 48.1 ms |

### Intended API contract (not a bug; **not trimmed**)

```text
requested_limit counts CLOSED candles
an open/forming tip MAY add +1
returned_count may be limit or limit+1
closed_count == requested_limit when enough history exists
open_included == true when forming tip present
limit_applies_to == "closed_candles"
```

Cause of 201: **deliberate open/forming tip candle**, not inclusive-endpoint off-by-one and not accidental padding.

---

## D. Backtest runtime reconciliation

Harness: COMBO_02 / BTCUSDT / 1h setup + 4h HTF / **10,968** 1h bars / same fees·leverage·risk·entry mode·dataset / analytics **off** / profiler **off**.

Artifact: `backtest_runtime_reconciliation.json`, `backtest_runtime_runs.csv`

| Cohort | Avg strategy_s | p50 | p95 | min | max | n |
|---|---:|---:|---:|---:|---:|---:|
| Cold process (disk-warm cache) | 14.863 | 14.628 | 14.767 | 13.491 | 17.595 | 5 |
| Warm process | 14.566 | 13.613 | 14.803 | 13.392 | 17.442 | 5 |

Stable across all runs:
- `bar_count=10968`, `trade_count=66`, trade digest `8f92c0dd0771cc2db072b8c8`
- dataset fingerprint `d50be10398eddd0764f09db63b4b36b7`
- configuration fingerprint `COMBO_02`

### 32.8s vs 47s explanation
- **47s** ≈ same workload **with cProfile** (instrumentation overhead).
- **32.8s** prior audit wall time under different process/load conditions; reproducible no-profiler warm mean is **~14.6s** on this machine with parquet cache warm.
- No strategy-loop optimization performed in this step.

Golden equality (`backtest_golden_equality.json`): `equality.all_pass=true` (trades, prices, SL/TP, exit timestamps, R-multiples, fingerprints).

---

## E. Combined-load stress results

Primary run: **600s** (`combined_load_stress.json`, `combined_load_timeseries.csv`).

Workload: health 2s, screener 5s, BTC 15m/1h charts 5s, coin 15s, backfill/freshness active.

| Endpoint | n | p50 ms | p95 ms | max ms | errors | timeouts |
|---|---:|---:|---:|---:|---:|---:|
| health | 266 | 30.85 | **1304.6** | 2231.5 | 0 | 0 |
| screener | 69 | 3437 | 9029.9 | 17376 | 0 | 0 |
| chart 15m | 108 | 39.5 | 633.3 | 1390 | 0 | 0 |
| chart 1h | 108 | 32.2 | 954.9 | 1430 | 0 | 0 |
| coin | 39 | 86.4 | 354.1 | 1087 | 0 | 0 |

Queue: start **236** → end **240** (max 240) — **no unbounded growth**, no duplicate-job explosion.  
Event-loop lag (10s samples): p50 **0.04 ms**, p95 **46.2 ms**, max **456.7 ms**.  
Silent empty charts: **0**.

### Acceptance
| Target | Result |
|---|---|
| health p95 < 500 ms | **FAIL** (1304.6 ms) |
| no health timeout | **PASS** |
| no silent empty charts | **PASS** |
| no unbounded queue growth | **PASS** |

### Bottleneck (exact)
1. Screener + concurrent REST tip work consume Binance limiter tokens (health snapshot showed ~2–3 tokens / 1100 capacity under load).
2. Polling `/api/data/backfill` every 10s runs expensive gap aggregation for the active universe and competes with health on the event loop.
3. Primary 10m run’s COMBO_02 job used a **wrong path** (`/api/research/long-strategy-backtest/job` → 404); script fixed to `/api/research/long-strategy/backtest/start`.
4. A later supplemental start of the research backtest under load **starved the asyncio event loop** (CPU-bound job task), producing health timeouts — research backtests must not share the interactive event loop under combined load (documented; strategy math unchanged).

---

## F. Backfill policy

Artifact: `backfill_backlog_metrics.json` (post-restart snapshot).

| Metric | Value |
|---|---|
| backlog_state | **STABLE_BACKLOG** |
| queue_size | 200 |
| gap_backlog_count | 42155 |
| enqueue_rpm | 31 |
| completion_rpm | 38 |
| failed_gap_count | 1 |
| retry_count | 2 |
| estimated_completion_seconds | ~319 |
| cooldown_suppressed_count | 106 |
| duplicate_suppressed_count | 92 |

### Priority policy (no auto concurrency increase)
1. user-requested chart / research data  
2. active-universe freshness  
3. historical gap repair  
4. inactive-symbol repair  

Historical gap repair must not starve health, screener, charts, or user research.

Classification rules implemented: CLEAR / DRAINING / STABLE_BACKLOG / GROWING / BLOCKED.  
BLOCKED refined so a lone `RETRY_WAIT` cannot flip state while the queue is otherwise stable; BLOCKED requires sustained zero completions with failures or a stuck idle queue.

---

## G. Request cancellation

Artifact: `request_cancellation_validation.json` + vitest.

| Check | Result |
|---|---|
| AbortSignal reaches fetch helpers | **PASS** |
| Collapse/unmount aborts in-flight request | **PASS** |
| Stale response discarded / new runId cannot show old cache | **PASS** |
| Lazy panels do not fetch before expand | **PASS** |
| Panels covered | Candidate, Short, Dynamic (via shared LazyResearchMount + dedupedPanelFetch) |

---

## H. Tests

| Suite | Result |
|---|---|
| `pytest` backfill + chart metadata | **23 passed** |
| vitest LazyResearchMount + RequestCancellation | **6 passed** |
| Golden / strategy equality | **all_pass=true** |

Warnings:
- Combined-load health p95 missed `<500ms`.
- Primary stress backtest path 404 (script corrected afterward).
- Research backtest on API event loop starves interactive endpoints under load.

---

## I. Strategy safety

Explicit confirmation for this follow-up:

- strategy trades unchanged (golden equality + reconcile digest stable at 66 trades)
- entry/exit timestamps unchanged (`exit_ts` equality true)
- prices unchanged (entry/exit price equality true)
- SL/TP unchanged (equality true)
- PnL / R-multiples unchanged (`r_multiples` equality true)
- equity / trade set unchanged for the optimized vs baseline compare
- configuration fingerprint unchanged (`COMBO_02`)
- dataset fingerprint unchanged (`d50be10398eddd0764f09db63b4b36b7`)
- no paper trades created
- no live trades created
- no Telegram messages sent
- no regime filter enabled
- no trading-rule / BOS / HTF / closed-HTF / RR / fee / leverage / sizing changes

---

## Artifact checklist

```text
backend/reports/full_system_performance_followup/
  chart_cold_validation.json
  chart_warm_validation.json
  backtest_runtime_reconciliation.json
  backtest_runtime_runs.csv
  combined_load_stress.json
  combined_load_timeseries.csv
  backfill_backlog_metrics.json
  request_cancellation_validation.json
  performance_followup_report.md
  test_results.txt
```
