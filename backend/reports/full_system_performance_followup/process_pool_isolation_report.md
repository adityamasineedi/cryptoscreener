# Process-Pool Research Backtest Isolation

**Date:** 2026-10-05  
**Scope:** Research job scheduling / worker isolation only  
**Safety:** No strategy / BOS / HTF / SL/TP / fee / sizing / paper / live changes

## Problem

`asyncio.to_thread(run_combination_backtest)` still held the CPython GIL during the
pure-Python strategy walk, starving the FastAPI event loop (health p95 ≈ 1.3s+).

## Architecture

```text
POST /api/research/long-strategy/backtest/start
  → BacktestJobService.start() returns job_id immediately (single-flight)
  → asyncio task orchestrates I/O (Postgres/cache candle loads)
  → CPU walk: ProcessPoolExecutor(max_workers=1) via pickle payload blob
  → cancel: tempfile flag (FileCancelEvent)
  → progress: tempfile JSON + job heartbeats
  → postprocess (fee enrich + optional market-structure attach): worker thread
    with heartbeats (avoids re-pickling 40k+ 15m bars into a second process)
```

Bounded workers only (`max_workers=1`). No paper/live imports in the CPU worker.
Worker sets `RESEARCH_CPU_WORKER=1` and refuses forced live flags.

## Files

- `backend/app/research/backtest_cpu_pool.py` (new)
- `backend/app/research/backtest_cpu_worker.py` (new)
- `backend/app/research/service.py` — strategy_matrix uses process pool
- `backend/app/research/backtest_job.py` — cancel event + isolation metadata
- `backend/app/main.py` — pool shutdown on lifespan exit
- `backend/tests/test_backtest_cpu_pool.py`
- `backend/scripts/_followup_process_pool_golden.py`
- `backend/scripts/_followup_combined_load.py` — correct start path, sparse backfill polls

## Golden equality (in-process vs process pool)

Artifact: `backtest_process_pool_golden_equality.json`

```text
all_pass: true
bars: 10968 1h / 2742 4h
trade_count equal
entry/exit timestamps, prices, SL/TP, exit reasons, R multiples equal
equity_curve_r / max_drawdown_R equal
configuration_fingerprint: COMBO_02
dataset_fingerprint equal
```

## Combined-load 10 minutes

Artifact: `combined_load_stress_600s_process_pool.json`  
Server for this run: `ENABLE_MARKET_STRUCTURE_ANALYTICS=false` (observability only;
strategy path unchanged). Research job delayed 45s after loadgen start.

| Metric | Result |
|---|---|
| Research job | **done** `job_id=e0c2c5b80e3d`, `execution_isolation=process_pool`, 1/1 cells |
| health timeouts | **0** |
| health p50 | 97.5 ms |
| health p95 | **1663 ms** (FAIL &lt;500) |
| health max | 3727 ms |
| silent empty charts | **0** |
| chart 15m p50 / p95 | 173 / 1048 ms |
| chart 1h p50 / p95 | 35 / 985 ms |
| screener p50 / p95 | 5492 / 7787 ms (reported separately) |
| event-loop lag p50 / p95 / max | 9.5 / 570 / 1361 ms |
| backfill queue | 0 → max 315 → end 213 (bounded, not unbounded) |

### Health p95 miss — bottleneck

Spikes concentrate in the **first ~35s** while backfill enqueues a large freshness
burst (queue 0→315) and REST contention rises. After settle, and during the
process-pool strategy walk, timeseries health samples are typically &lt;200 ms.
This is **not** the prior GIL-bound strategy-walk failure mode.

Remaining pressure sources (outside strategy math):

1. Backfill enqueue / REST tip catch-up bursts
2. Screener under Binance limiter contention
3. Occasional `/api/data/backfill` gap aggregation (now polled sparsely by the harness)

## Smoke proof (process pool during STRUCTURE_SCAN)

With a lone COMBO_02 job (no full stress), health stayed ~50–160 ms through
`STRUCTURE_SCAN_HEARTBEAT` while `execution_isolation=process_pool`.

## Tests

```text
pytest tests/test_backtest_cpu_pool.py tests/test_backtest_job.py
13 passed
```

## Strategy safety

- Strategy calculations unchanged (golden equality)
- No paper/live trades, no Telegram, no regime filter
- Worker isolated; cannot open trades
- Job API contract preserved (start → job_id, status poll, cancel)
