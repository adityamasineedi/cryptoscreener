# Research Backtest Performance Report

Measured on real PostgreSQL OHLCV. No synthetic data. No strategy-rule changes.

## Dataset

| Field | Value |
|-------|-------|
| symbols | BTCUSDT, ETHUSDT, SOLUSDT |
| timeframes | 15m, 1h (+1h/4h HTF for COMBO_02) |
| date range | 2025-01-01 → 2025-02-01 |
| rows | 12,096 candles across loaded series |
| source | PostgreSQL → Parquet research cache |

## Baseline

| Metric | Value |
|--------|-------|
| COMBO_02 strategy eval (PG candles) | 21.03 s |
| DB queries (cold PG pass) | 9 |
| Peak RSS (approx) | ~137 MB |
| Trades | 17 |

## Optimized

| Metric | Value |
|--------|-------|
| COMBO_02 strategy eval (Parquet candles) | 25.01 s |
| Warm Parquet prepare | 0.18 s, **0** PG queries |
| Cold Parquet write | 3.32 s |
| Multi-combo bundle (COMBO_02 + LOCAL) | 44.54 s |
| Trades | 17 (identical to baseline) |

## Speedup

| Path | Runtime | Notes |
|------|---------|-------|
| Cold (PG load + parquet write + baseline eval) | 25.28 s | |
| Warm (parquet read + optimized eval) | 25.66 s | ~1.0× vs cold load+eval |
| Warm cache prepare alone | 0.18 s vs 0.45 s PG | Cache helps **I/O**, not strategy CPU |
| PG queries warm | **0** | vs 9 cold |

Honest conclusion: for this golden window, **strategy evaluation is ~95% of total runtime**. Warm Parquet does not materially speed COMBO_02 walks because the engine still dominates.

## Bottleneck ranking

1. **Strategy evaluation — 94.9%** (90.6 s)
2. Parquet write (cold) — 3.5%
3. Polars→dict conversion — 0.5%
4. PostgreSQL load — 0.5%
5. Parquet read (warm) — 0.2%

**DATABASE_IS_NOT_PRIMARY_BOTTLENECK**

Further large speedups require research-engine CPU work (lifecycle/structure scanning) without changing BOS/entry/SL/TP semantics — already started via HTF precompute + index maps (same closed-bar labels as on-demand `trend_at_as_of`).

## CPU Profiling

Profiled with `cProfile` + `RESEARCH_PROFILE_ENABLED` stage timers on the same golden dataset.

Artifacts:
- `profile_strategy.txt`
- `profile_strategy.json`
- `stage_timings.json`

### COMBO_02 strategy evaluation

| Metric | Value |
|--------|-------|
| Total strategy evaluation wall | **18.97 s** (this profile run; same path as ~90.6s stage earlier when bundled with multi-combo) |
| Trades | **17** (matches golden) |
| Bars walked | ~11,220 |
| Full `evaluate_combination_at_bar` calls | **205** (BOS prefilter skips the rest) |

Top bottleneck (COMBO_02):

| Rank | Function / stage | Runtime | % wall | Calls | Class |
|------|------------------|---------|--------|-------|-------|
| 1 | `evaluate_combination_at_bar` → `analyze_timeframe` | 15.0s / 14.4s | 79% / 76% | 205 | PRIMARY_BOTTLENECK |
| 2 | `SwingRecord.to_dict` → `asdict` / `_asdict_inner` | 11.7s / 11.0s | ~62% | ~50k / 506k | PRIMARY_BOTTLENECK |
| 3 | `deepcopy` (inside asdict path) | 8.55s | 45% | 727,870 | PRIMARY_BOTTLENECK |

Stage timers (COMBO_02):

| Stage | Calls | Seconds |
|-------|-------|---------|
| structure_analyze_timeframe | 205 | 14.41 |
| evaluate_combination_at_bar | 205 | 15.05 |
| bos_prefilter | 10,794 | 1.43 |
| swing_extend | 11,220 | 0.73 |
| htf_precompute | 6 | 0.51 |
| htf_alignment_gate | 205 | 0.46 |
| trade_simulation | 426 | 0.008 |

### Lifecycle discovery (BTCUSDT 15m only)

| Stage | Calls | Seconds | Class |
|-------|-------|---------|-------|
| lifecycle_discover_analyze_timeframe | 3,072 | **422.6** | PRIMARY_BOTTLENECK |
| detect_retest | 319 | 4.32 | SECONDARY |
| detect_pullback | 896 | 0.14 | LOW_IMPACT |
| candles_as_of_copy | 1,215 | 0.026 | LOW_IMPACT |

Lifecycle discovery still runs **full `analyze_timeframe` / `detect_swings` every bar** (no incremental extend). That is why a single 15m month costs minutes even though COMBO_02 prefilter keeps combination eval to 205 hits.

### Dependency map (measured)

```
OHLCV
  -> incremental swing_extend (every bar)     [CHEAP: 0.73s]
  -> bos_prefilter (every flat bar)           [CHEAP: 1.43s]
  -> analyze_timeframe (205 BOS candidates)   [EXPENSIVE: 14.4s]
       includes swings[i].to_dict()/asdict/deepcopy  [DOMINANT inside]
  -> htf_alignment_gate (cached)              [CHEAP: 0.46s]
  -> trade sim                                [NEGLIGIBLE]

Lifecycle path (separate):
OHLCV -> analyze_timeframe EVERY bar (3072)   [EXTREMELY EXPENSIVE: 422s]
      -> pullback/retest on follow bars
```

Structure/BOS is **not** shared across strategies yet — only candle conversion is reused in the bundle runner.

### Optimization candidates (NOT implemented yet)

1. **Skip research hot-path SwingRecord `to_dict`/`asdict`/`deepcopy`** when `_swings_objs` already exists — PRIMARY for COMBO_02.
2. **Lifecycle discovery: incremental swings + BOS prefilter** (same pattern as combination_backtest) — PRIMARY for lifecycle / multi-strategy runner.
3. **Shared structure/BOS/lifecycle caches across strategies** on one series — SECONDARY for multi-strategy throughput.

## Decision

**READY_FOR_OPTIMIZATION**

## Correctness

| Check | Result |
|-------|--------|
| OHLCV PG vs Parquet | PASS |
| Features (research FeatureCache) | PASS |
| BOS event stage | SKIPPED (optional flag) |
| Lifecycle stage | SKIPPED in this run (`--skip-lifecycle`) |
| Strategies / trades | PASS (17 = 17) |
| Metrics | PASS after inf-equality fix (`profit_factor=+inf` when no losses); trades already matched |
| No-lookahead HTF maps | PASS |
| Overall benchmark status | **OK** |

## Production isolation

| Surface | Status |
|---------|--------|
| Live signal engine | UNCHANGED |
| WebSocket / REST collectors | UNCHANGED |
| Trade Plan | UNCHANGED |
| Paper trading | UNCHANGED |
| Telegram | UNCHANGED |

## Tests

| Suite | Result |
|-------|--------|
| `test_research_pipeline_perf.py` | PASS |
| `test_research_data_cache.py` | PASS |
| `test_bos_strategy_comparison.py` | PASS |
| `test_combo02_htf_gate.py` | PASS |
| `test_lifecycle.py` | PASS |
| Combined (53) | PASS |

## Files changed

- `backend/scripts/benchmark_research_pipeline.py` — full stage A–K benchmark + golden
- `backend/app/research/data_cache/golden_equality.py` — trade/OHLCV/metrics digests
- `backend/app/research/data_cache/cache_manager.py` — cache hit skips redundant parquet materialize
- `backend/app/research/data_cache/runner.py` — convert candles once; reuse across strategies
- `backend/app/research/bos_strategy_comparison/runner.py` — HTF trend precompute + as-of index maps
- `backend/tests/test_research_pipeline_perf.py` — cache/HTF/no-lookahead/equality tests
- `backend/scripts/research_benchmark_golden/` — baseline + last_benchmark artifacts

## Files intentionally untouched

- Live `SignalEngine` / swing / BOS / pullback / retest production paths (semantics preserved)
- Paper trading, Telegram, Trade Plan, WebSocket/REST collectors
- Strategy thresholds, fees, slippage, candidate eligibility
- `bos_strategy_comparison/service.py` Parquet wiring **not** enabled (warmup-range semantics would risk divergence)

## Remaining bottlenecks

1. Per-bar structure / BOS evaluation inside combination + lifecycle discovery (CPU).
2. Polars→Python dict boundary (minor).
3. Lifecycle path not fully event-driven for all research APIs yet (`EventCache` still unused by engines).

Do **not** optimize by changing trading rules. Next safe CPU wins: incremental structure for lifecycle discovery (equality-gated), keep HTF maps everywhere research evaluates HTF.
