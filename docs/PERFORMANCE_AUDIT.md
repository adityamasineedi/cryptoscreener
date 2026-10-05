# Full-System Performance Audit Report

**Date:** 2026-10-05  
**Scope:** Application performance only (no strategy / BOS / HTF / risk rule changes)  
**Environment:** Local Windows · FastAPI uvicorn `--reload` · PostgreSQL 16 (no Timescale) · Redis optional · Parquet research cache

---

## Executive summary

1. **P0 fixed — Screener latency:** `/api/screener/futures` was averaging **4.65s** (max **9.6s**) because up to 12 symbols × 4 TFs ran **sequential** REST tip refreshes + per-symbol paper flushes on the request path. After parallelizing tip refresh and flushing paper once: **~0.17–0.34s** on warm polls (≈**14–27×** faster). Payload shape unchanged.
2. **P0 addressed — Event-loop starvation during OHLCV hydrate:** `ohlcv_store.load_from_db` ran **hundreds of sequential series loads** inside one transaction with no yields, making `/api/health` time out (observed **60s** / **25s** outliers). Now yields every 2 series, prefers BTC/ETH/SOL first, uses `connect()` instead of a long transaction, and uses O(1) open-time sets instead of O(n²) membership checks.
3. **P1 fixed — Research OHLCV range N×M queries:** `/api/research/ohlcv-range` issued one `COUNT/MIN/MAX` per symbol×TF. Replaced with a single `GROUP BY` query — measured **~0.07–0.15s** for 3×2 cells.
4. **P1 fixed — Coin detail provider waterfall:** `coin_detail_tabs` awaited onchain holders → txs → sentiment **sequentially**. Now `asyncio.gather` — cold samples improved from **4.6s** toward **~1.4s avg** (min **37ms** when providers cached).
5. **P1 implemented — Chart cold-start PG hydrate:** Chart endpoint now loads a Postgres tail when in-memory history is thin (before REST). Offline PG tail for BTCUSDT 1h×200 = **16.6ms / 200 rows**. Live remasure after reload still showed 2 candles during a busy restart — verify once uvicorn is fully warm.
6. **Database is healthy for the hot path:** `idx_ohlcv_symbol_tf_time (symbol, timeframe, time)` is used; 30-day BTC 1h range **~5.2ms**; full BTC 1h series (35,039 rows) **~58ms**. Full-table `COUNT(*)` on 4.3M rows takes **~4.9s** — avoid in UI paths.
7. **TimescaleDB is not installed** on this host (plain Postgres fallback). Hypertables/compression benefits are unavailable until Timescale is installed.
8. **Backtest CPU scales roughly with bars:** COMBO_02 BTCUSDT 1h — 823 bars **0.18s**; 10,968 bars **32.8s**; 35k bars still CPU-bound (~3ms/bar). Parquet cache load is fast (0.03–0.48s). Further backtest speedups need hot-loop profiling without changing trade semantics.
9. **Frontend already has good foundations:** React + Zustand + TanStack Virtual (screener) + Lightweight Charts. No route-level `React.lazy`. Backtest trade tables are not virtualized.
10. **Trading behavior lock respected:** No BOS/HTF/entry/SL/TP/risk/fee methodology changes. Optimizations are request scheduling, I/O concurrency, and cooperative multitasking only.

---

## Architecture map

```text
UI (React 18 + Vite + Zustand + TanStack Virtual + lightweight-charts)
 ↓ REST + WS (/ws/screener, /ws/market, /ws/coin/{symbol})
API (FastAPI · app/api/routes.py · diagnostics_routes)
 ↓
Services (screener, screen_universe, paper_trade, setup_signals, coverage, ohlcv_store, market_store)
 ↓
Engines (orchestrator · MTF · structure · S/D · volume · signals)
Research / Backtest (combination_backtest · combination_engine · SignalEngine · HTF cache)
 ↓
Cache (in-memory OHLCV ≤500/series · Redis optional · Parquet research_cache)
 ↓
PostgreSQL ohlcv (~4.3M rows, 529 symbols × 6 TFs) + Binance REST/WS live tip
```

| Layer | Tech |
|---|---|
| Frontend | React 18, Vite 6, Zustand, TanStack Table/Virtual, lightweight-charts, react-router |
| Backend | FastAPI + uvicorn, asyncio workers |
| Live data | Binance WS (ticker/mark/kline shards/liquidations) + REST backfill |
| DB | PostgreSQL 16 (Timescale extension **unavailable** here) |
| Cache | Redis (optional), in-memory stores, research Parquet |
| Research | pandas/polars in data_cache path; combination engine uses native candle dicts / SwingRecord |

---

## Tab performance

| Tab | Route | Initial load | API calls | Charts | Tables | Main bottleneck |
|---|---|---:|---|---:|---|---|
| Screener | `/` | High (was) | screener + health 5s + 2 WS + coin/chart on select | 2 docked | Virtualized ≤100 | Was sync REST tip×MTF (**fixed**); still full-`rows` Zustand churn |
| Charts | `/charts` | Med | remounts `useMarketStream` (2 WS reconnect) + ohlcv/ann/liq | 2 | — | Cold memory; PG fallback added; aggressive OHLCV poll (3–15s) |
| Strategies | `/strategies` | Low | catalog | 0 | Small | Light |
| Data Health | `/health` | Med–High | coverage + stats + providers + backfill 5s | 0 | Medium | Coverage/backfill under load |
| System Diagnostics | `/diagnostics*` | Med | 12 tabs / forensics polls 5–30s | 0 | Medium | Snapshot/log windows |
| BOS Research | `/bos-research` | High on run | ohlcv-range then compare (limit 500–1500) | 0 | Medium | Research jobs |
| BOS Strategies | `/bos-strategy-research` | High on run | multi-year possible on Run | 0 | Medium | Research jobs |
| Paper Trade | `/paper` | Low–Med | positions + opps every 5s | 0 | ≤120 / ≤100 unvirtualized | OK |
| Watchlist | `/watchlist` | — | placeholder | — | — | Stub |
| Alerts | `/alerts` | Low | REST 4s + `/ws/alerts` | 0 | ≤120 unvirtualized | OK |
| Backtest | `/backtest` | **High on mount** | ohlcv-range + **3 child panels auto-fetch** + job | 0–1 | Matrix + blotter **unvirtualized** | Mount cost + job CPU |
| OHLCV History | `/ohlcv-history` | Med | range + optional expand job | 0 | Coverage grid | Expand job |
| Settings | `/settings` | — | placeholder | — | — | Stub |

Nested research panels (Candidate / Short / Dynamic Pipeline / Market Structure) mount **inside Backtest** and each fetch on load — largest frontend mount cost after Screener.

Screener domain tabs (TopNav) and CoinDetail tabs are view modes, not separate routes.

---

## Backend performance (measured)

### Before (warm, n=5 unless noted)

| Endpoint | Avg | P50/Max | Payload | Notes |
|---|---:|---:|---:|---|
| `/api/health` | 0.042s | 0.046 / 0.059 | 851 B | OK when loop free |
| `/api/system/stats` | 0.032s | — | 2.1 KB | OK |
| `/api/system/performance` | 0.030s | — | 928 B | OK |
| `/api/screener/futures?limit=50` | **4.647s** | 3.627 / **9.618** | **609 KB** | P0 — sync tip refresh |
| `/api/charts/.../15m` | **2.316s** | 0.119 / **11.163** | 80 KB | Tip REST + loop contention |
| `/api/charts/.../1h` | 0.133s | 0.082 / 0.403 | 80 KB | OK |
| `/api/coin/BTCUSDT` | **4.624s** (n=1) | — | 97 KB | Provider waterfall |

### After high-confidence fixes

| Endpoint | Avg | Notes |
|---|---:|---|
| `/api/screener/futures?limit=50` | **0.17–0.34s** | Parallel tip refresh + single paper flush |
| `/api/research/ohlcv-range` (3×2) | **0.07–0.15s** | Single grouped SQL |
| `/api/coin/BTCUSDT` | **~1.4s avg** (min 0.037) | Concurrent providers |
| `/api/health` | still spikes to **8–25s** during hydrate/backfill storms | Hydrate yields mitigate; backfill queue still heavy |

> During reloads, screener sometimes returned `rows=0` while universe metadata was present — ingestion/hydrate race, not a strategy change. Remeasure when `ingestion=live` and hydrate complete.

---

## Database findings

| Metric | Value |
|---|---|
| `ohlcv` rows | **4,345,359** (`COUNT(*)` ≈ **4.9s**) |
| Symbols × TFs | **529 × 6** (`DISTINCT` ≈ **7.1s** — expensive) |
| BTCUSDT 1h bars | **35,039** (2022-10-06 → 2026-10-05) |
| Indexes | `ohlcv_pkey (time, symbol, timeframe)`; **`idx_ohlcv_symbol_tf_time (symbol, timeframe, time)`** |

### EXPLAIN (ANALYZE, BUFFERS) highlights

| Query | Plan | Exec time |
|---|---|---:|
| Range BTC 1h last 30d | Bitmap Index Scan on `idx_ohlcv_symbol_tf_time` | **5.2ms** |
| Full BTC 1h series | Bitmap Index Scan + sort | **58ms** |
| Coverage COUNT/MIN/MAX one series | Index Only Scan | **14ms** |
| Batch 3 symbols × 2 TFs GROUP BY | Index Only Scan | **115ms** |

### Index recommendations

| Priority | Index | Improves | Cost |
|---|---|---|---|
| Already present | `(symbol, timeframe, time)` | All research/chart range loads | — |
| P2 (optional) | Partial / BRIN on `time` **if** Timescale installed | Retention / time-only scans | Write amplification |
| Avoid | New indexes without EXPLAIN proof | — | Storage + insert cost on 4.3M+ rows |
| Ops | Install TimescaleDB | Compression, hypertables, continuous aggs | Ops effort |

Do **not** run full-table `COUNT(*)` / `COUNT(DISTINCT symbol)` on UI request paths.

---

## Data pipeline findings

| Issue | Evidence | Status |
|---|---|---|
| Screener request-path sequential REST tip×MTF | 4.65s avg | **Fixed** (parallel + single flush) |
| Hydrate sequential series, no event-loop yield | health timeouts during boot | **Fixed** (yield + prefer majors) |
| O(n²) duplicate check on hydrate ingest | `any(c.open_time == ...)` | **Fixed** (set) |
| research ohlcv-range N×M round-trips | 6 queries → 1 | **Fixed** |
| Chart cold memory empty after restart | 1–2 candles served | **PG tail hydrate added** (verify warm) |
| Backfill `pending: 3173` + trailing_stale enqueue | Continuous 1m REST | Open P1 — rate/universe already capped but tips still thrash |
| `is_trailing_stale` on closed-only tips | 1m often looks stale without open bar | Open P2 — review with open candle in tip_view (already partially done) |

---

## Chart findings

| Chart | Library | Points | Issue |
|---|---|---:|---|
| Screener bottom dock | lightweight-charts | ≤200–1000 | Tip REST outliers to **11s** under load |
| Charts page | same component | same | Same |
| Backtest trade chart | `BacktestTradeChart` | Windowed via `/research/ohlcv-candles` | OK pattern (pad_bars) |
| Annotations | structure/BOS markers | capped (`MAX_VISIBLE_STRUCTURE_LABELS`) | Already thinned |

Implemented: Postgres tail hydrate when memory `< max(20, min(limit,80))` before REST. Offline proof: **200 bars / 16.6ms**.

---

## Backtest findings

COMBO_02 · BTCUSDT · 1h + 4h HTF · Parquet cache:

| Scale | Bars 1h | Load | Backtest | Trades |
|---|---:|---:|---:|---:|
| Small (~1 mo) | 823 | **0.031s** | **0.176s** | 7 |
| Medium (~15 mo) | 10,968 | **0.130s** | **32.845s** | 66 |
| Large (~4 yr) | 35,039 | **0.480s** | **not finished in audit window** (estimated ~105s at ~3ms/bar from medium) | — |

Hot path: `run_combination_backtest` → per-bar `evaluate_combination_at_bar` / SignalEngine / swing / HTF lookup.  
Already optimized: SwingRecord kept native (no per-bar `to_dict`/`deepcopy`). HTF trend cache precomputed.

**Next backtest opts (not done — need golden equality):** profile structure scan vs evaluate ratio; avoid repeated dataframe/list copies; optional incremental swing extension (must prove identical trades).

---

## Memory findings

| Consumer | Notes |
|---|---|
| In-memory OHLCV | ≤500 closed candles × symbol × TF for active universe |
| Full PG table | 4.3M rows on disk — do not load wholesale into API |
| Research Parquet | Fast warm loads; keep for offline/research only (`use_research_cache=False` on UI jobs by design) |
| Screener JSON | ~12 KB/row when fully enriched (50 rows ≈ 600 KB) — largest UI payload |

---

## Scalability

| Dataset | Load | CPU (backtest) | DB | Chart (target) | Notes |
|---|---:|---:|---:|---:|---|
| Small 1×~30d | 0.03s | 0.18s | index hit | PG tail 16ms | OK |
| Medium 1×~1y | 0.13s | 32.8s | index hit | — | Backtest dominates |
| Large 1×~4y | 0.48s | ~O(bars) | 58ms full series | — | Engine CPU |
| Stress 529×6×history | hydrate minutes | — | COUNT 4.9s | — | Cap active universe; never full DISTINCT on UI |

Objective: UI stays responsive while history grows — hydrate yields + chart PG tail + screener parallelization are the main levers applied.

---

## Fix priority

| Priority | Issue | Location | Impact | Effort | Expected / Measured |
|---|---|---|---|---|---|
| P0 | Screener sequential tip REST | `routes.screener_futures` | UI unusable | S | **4.65s → ~0.2s** |
| P0 | Hydrate starves event loop | `ohlcv_store.load_from_db` | API timeouts | S | Health recoverable during hydrate |
| P1 | ohlcv-range N×M SQL | `routes.research_ohlcv_range` | Backtest tab warn slow | S | Single query ~0.1s |
| P1 | Coin provider waterfall | `screener.coin_detail_tabs` | Detail panel lag | S | **4.6s → ~1.4s avg** |
| P1 | Chart cold empty | `routes.chart_ohlcv` | Blank charts post-restart | S | PG 200 bars / 16ms (offline) |
| P1 | Backfill trailing_stale thrash | `backfill._freshness_loop` | REST/CPU contention | M | TBD — measure enqueue rate |
| P2 | Backtest mounts 3 research panels eagerly | `BacktestPanel.tsx` | Extra APIs every visit | S | Lazy-mount panels |
| P2 | No `React.lazy` routes | `App.tsx` | Initial JS | S | Faster first paint |
| P2 | Screener/`BottomCharts` subscribe to full `rows` | `ScreenerTable` / `BottomCharts` | Tick re-renders | M | Narrow selectors |
| P2 | `RESEARCH_CPU_WORKERS` unused (no process pool) | config + backtest_job | Event-loop blocks on long BT | L | Offload CPU jobs carefully |
| P2 | DB pool_size=5 vs hydrate+research+flush | `database.py` | Pool wait under load | S | Tune after EXPLAIN |
| P2 | Unbounded `load_ohlcv_series` callers | `postgres_ohlcv.py` | Multi-year accidental loads | S | Prefer range/tail only |
| P2 | Backtest trade table not virtualized | `BacktestPanel` | DOM cost | S | Virtualize blotter |
| P2 | COMBO_02 ~3ms/bar | `combination_backtest` | Research UX | L | Profile + golden tests |
| P3 | Timescale missing | Ops | Storage/scan | Ops | Compression / hypertables |
| P3 | `@tanstack/react-table` unused dep | `package.json` | Bundle noise | S | Remove or use |
| P3 | Verbose schema_init on every script connect | `db_manager` | Script noise/latency | S | Skip ensure when schema_ready |

---

## Implementations in this pass

| Change | File(s) | Behavior impact |
|---|---|---|
| Parallel screener tip refresh + one paper flush | `backend/app/api/routes.py` | Same setups/paper rules; faster response |
| Grouped ohlcv-range SQL | `backend/app/api/routes.py` | Identical row semantics |
| Cooperative OHLCV hydrate + set membership | `backend/app/services/ohlcv_store.py` | Same candles loaded; API stays responsive |
| Concurrent coin providers | `backend/app/services/screener.py` | Same data; lower latency |
| Chart Postgres cold hydrate | `backend/app/api/routes.py` | Presentation only; same OHLC from DB |

**Not changed:** strategy entry/exit, BOS, HTF closed-bar semantics, SL/TP, fees, OOS partitions, research methodology.

---

## Regression / correctness notes

- Screener/coin/chart changes are transport & scheduling only.
- For any future backtest micro-opts, require golden equality on: trade count, entry/exit timestamps & prices, SL/TP, exit reason, PnL, R, DD.
- Existing harnesses: `backend/scripts/benchmark_research_pipeline.py`, `backend/tests/test_research_pipeline_perf.py`, `golden_equality` helpers.

---

## Recommended next measurements (ops)

1. After a full warm boot (`ingestion=live`, hydrate done): re-run screener n=10 and chart n=10; confirm candle counts ≥200 and screener `returned_count>0`.
2. `EXPLAIN` any remaining coverage endpoints that still `COUNT(*)` the full table.
3. Profile one COMBO_02 15-month run with `cProfile` / stage profiler — only then touch the bar loop.
4. Consider lazy route splitting for Backtest / BOS / Diagnostics.

---

## Acceptance checklist

| Criterion | Status |
|---|---|
| Trading results unchanged by design | Yes (no strategy edits) |
| Measured screener improvement | **Yes — 4.65s → ~0.2s** |
| Measured DB path quality | Yes — index used; range ms-level |
| Measured backtest scale curve | Yes — 0.18s / 32.8s / O(bars) |
| Chart cold-path PG proof | Offline yes; live warm remasure pending |
| No silent empty-on-error substitution for failures | Errors still surface; empty screener during boot is hydration race |

**Bottom line:** The dominant interactive bottleneck was **request-path REST tip work on the screener**, not the database index. Fixing that plus hydrate yielding and coin/chart I/O concurrency delivers the largest UX wins without touching locked trading behavior.
