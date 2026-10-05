# Startup Backfill Burst Control — Follow-up Report

**Date:** 2026-10-05  
**Scope:** Ingestion scheduling / startup enqueue+REST ramp only  
**Safety lock:** No BOS/HTF/entry/exit/SL/TP/risk/fee/paper/live/Telegram/strategy changes  
**Sandbox:** `backend/reports/startup_backfill_burst_control/` (does not overwrite prior follow-up reports)

**Validation note:** Full 3×10 min acceptance suite was stopped by request. Evidence below is from unit tests + process-pool golden equality + one **120 s** combined-load smoke after a full restart. Config was later tightened further (90 s window, 1 active REST job, queue soft-cap 32) but that tighter config was not fully re-measured.

---

## A. Files changed

| File | Change |
|---|---|
| `backend/app/ingestion/backfill.py` | Batched/priority enqueue, startup soft queue cap, REST token bucket, active-job cap, gap deferral, startup vs steady metrics |
| `backend/app/engines/orchestrator.py` | `mark_ingestion_ready()` after hydrate, before seed enqueue |
| `backend/app/services/performance.py` | Hydrate flag no longer stuck true for entire enqueue ramp |
| `config/market.yaml` | Startup ramp knobs (window, batch, rates, caps) |
| `backend/tests/test_backfill_priority.py` | Startup ramp / dedupe / gap-deferral / re-arm tests |
| `backend/scripts/_startup_backfill_validation.py` | Phase-split probe harness |
| `backend/scripts/_startup_backfill_run_reps.py` | Full-restart driver |

Process-pool isolation left unchanged (`max_workers=1`).

---

## B. Startup versus steady-state metrics

Source: `combined_load_after_startup_ramp.json` (120 s smoke).

| Metric | Startup (first 60 s) | Steady-state | Overall |
|---|---:|---:|---:|
| health p50 | 1118 ms | 774 ms | 1118 ms |
| health p95 | 2639 ms | **1706 ms** | 2488 ms |
| health max | 2848 ms | 2195 ms | 2848 ms |
| event-loop lag p95 | 1282 ms | **39 ms** | 1195 ms |
| event-loop lag max | 1288 ms | 56 ms | 1288 ms |
| screener p95 (overall) | — | — | 8055 ms |
| chart 15m p95 (overall) | — | — | 1223 ms |
| chart 1h p95 (overall) | — | — | 1368 ms |

Server-side ramp snapshot at end of smoke:

| Field | Value |
|---|---:|
| startup_queue_depth_peak | **40** (soft cap) |
| startup_active_jobs_peak | **2** |
| startup_duplicate_suppressions | 57 |
| deferred_gap_candidates | 110 |
| startup_rate_limit_wait | 49.6 s |
| steady enqueue rpm | 16 |
| steady REST rpm (backfill-tagged) | 114 |

Prior baseline (process-pool follow-up): queue max **≈315**, health p95 **≈1663 ms**.

---

## C. Backfill queue behavior

| | Prior | After ramp (smoke) |
|---|---:|---:|
| queue start | 0 | 0 |
| queue max | ~315 | **47** (startup peak **40**) |
| queue end | 213 | 47 |
| unbounded growth | no | **no** |
| duplicate suppressions | present | present (startup 57) |
| gap repair during startup | mixed in | **deferred** (110 candidates) |

Invariants held in unit tests: no duplicate queued/running key; soft/hard queue caps; cooldown/retry unchanged.

---

## D. Health and event-loop latency

| Acceptance | Result |
|---|---|
| health p95 &lt; 500 ms after startup ramp | **FAIL** (steady p95 1706 ms) |
| no health timeout | **PASS** |
| event-loop lag p95 &lt; 200 ms | **FAIL overall** (startup-dominated); **steady lag p95 39 ms PASS** |

Do **not** claim success from steady lag alone. Health remains elevated under concurrent screener tip REST + backfill workers; queue burst is controlled, but REST/screener contention still moves health.

---

## E. Process-pool job result

Smoke COMBO_02 job:

```text
job_id=3d0f5fc1926e
status=done
execution_isolation=process_pool
done_cells=1 / total_cells=1
errors=none
```

---

## F. Golden equality

Artifact: `process_pool_golden_equality.json`

```text
all_pass: true
trade_count: 28 (in-process == process-pool)
dataset_fingerprint: d50be10398eddd0764f09db63b4b36b7
configuration_fingerprint: COMBO_02
```

Equal across: trade IDs/digest, entry/exit timestamps & prices, SL/TP, exit reasons, R multiples, equity curve / max DD, fingerprints.  
(Fee/gross/net fields covered by trade digest equality.)

---

## G. Tests

```text
pytest tests/test_backfill_priority.py tests/test_backtest_cpu_pool.py
31 passed
```

See `test_results.txt`.

---

## H. Remaining risks

1. **Health p95 still misses &lt;500 ms** after ramp — next pressure is screener/chart tip REST and occasional heavy `/api/data/backfill` gap scans, not the initial enqueue dump.
2. Smoke was **120 s / 1 rep**, not 3×10 m; longer soak may show different steady-state queue (deferred gaps drain later).
3. Post-smoke config tighten (90 s / 1 REST / depth 32) is in `market.yaml` but **not** fully re-validated under load.
4. Data Health UI polling `/api/data/backfill` frequently can itself inflate health latency.

---

## I. Safety confirmation

```text
No strategy changes
No BOS/HTF changes
No risk changes
No paper/live trades
No Telegram
No database mutation (beyond normal OHLCV backfill writes already in product path)
No report overwrite (new sandbox directory only)
```

---

## Artifact checklist

```text
backend/reports/startup_backfill_burst_control/
  startup_backfill_metrics.json
  startup_backfill_timeseries.csv
  combined_load_after_startup_ramp.json
  health_latency_by_phase.csv
  event_loop_lag_by_phase.csv
  process_pool_golden_equality.json
  test_results.txt
  startup_backfill_followup_report.md
```
