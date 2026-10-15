# CANDLE12_V3_EXEC_FIX: paper readiness

**Book:** CANDLE12_V3_EXEC_FIX, engine 3.1.0, BTCUSDT 1h LONG. Default `Candle12V3Config`: `per_side`, `once_per_level`, `nearest_target_full_exit`, `inclusive`, funding off. Sizing: $20 risk, $1,000 equity, 2×, 0.04% taker, 0.02% slippage.
**Status (2026-10-15, about 17:40 UTC):** **LAUNCHING AS OOS-2**. OOS-1 (frozen at 15:00 UTC) drifted when the operations layer was added. It is SUPERSEDED, and its lock and text are preserved in `docs/candle12_v3_drift_review_2026-10-15/` (commit fd2de20). OOS-2 keeps the same window (2026-10-16 00:00 UTC), earliest read (2027-10-16 00:00 UTC), book and thresholds. The launch gates are `freeze-oos` (versioned, refused at or after the start) and the 12-check `preflight`. The fingerprint now covers 37 files; line endings are pinned in `.gitattributes`.

**Earlier status (2026-10-15, evening):** PAPER_READY_PENDING_FREEZE. The operations layer is built and tested (see Operations readiness). The first supervised `virtual_paper_mainnet` run is blocked until `freeze-oos` runs on a clean tree. The testnet broker check has not been run.

**Earlier status (2026-10-15, later):** PAPER_READY_PENDING_OOS.
- The paper adapter is built: `backend/app/research/candle12_v3_paper/`. Its dry run reconciles with the engine with zero exceptions.
- The OOS contract is frozen: `docs/candle12_v3_oos_contract.md` and its lock file. It supersedes the proposed read rule below.
- No paper order has been sent. The OOS read has not started.

The status line before the adapter and contract existed read: RESEARCH_ONLY, recommendation PAPER_ELIGIBLE_AFTER_OOS.

## Operations readiness (2026-10-15, evening)

Built in `backend/app/research/candle12_v3_paper/`. Nothing here changes Candle-1 detection, the entry trigger, the gap rule, stop or target distance, sizing, leverage, fees, or the OOS thresholds. `run_smoke` on 2025-10-01 → 2026-03-01 still gives SMOKE_PASS, 11/11 checks, 5 trades RECONCILED with 0 exceptions, and `counts_toward_oos=false`.

### Execution modes

| | `virtual_paper_mainnet` | `testnet_broker_check` |
|---|---|---|
| Candles / funding | mainnet `https://fapi.binance.com` | testnet `https://demo-fapi.binance.com` |
| Broker | `SimulatedBroker` (engine fill rules) | `BinanceTestnetBroker` |
| Store | `VIRTUAL_PAPER_MAINNET` | `TESTNET_BROKER_CHECK` (`NON_OOS`) |
| Counts toward OOS | yes, only if created under a clean `freeze-oos` lock | **never** |
| Strategy validation | yes | **never** |
| Engine reconciliation under OOS tolerances | yes | **refused** (`TESTNET_NOT_COMPARABLE`) |

Mainnet candles with testnet fills, and the reverse, are rejected with `MIXED_DATA_BROKER_MODE` by `modes.resolve`, the CLI, and `run_live`. The old `run --paper` was exactly that mix, so it is now refused unless `--mode testnet_broker_check` is given.

### Commands (run from `backend/`)

```
python -m app.research.candle12_v3_paper.cli run --mode virtual_paper_mainnet [--recover] [--allow-non-oos] [--store DIR]
python -m app.research.candle12_v3_paper.cli run --mode testnet_broker_check --paper [--recover]
python -m app.research.candle12_v3_paper.cli broker-check --symbol BTCUSDT --max-trades 1 [--exit-wait 900]
python -m app.research.candle12_v3_paper.cli backfill-data --symbols BTCUSDT,ETHUSDT --start 2026-10-12 --end now [--loop --interval-s 3600]
python -m app.research.candle12_v3_paper.cli freeze-oos
python -m app.research.candle12_v3_paper.cli status --store DIR
python -m app.research.candle12_v3_paper.cli kill-switch engage|release|status [--reason TEXT]
```

- `run` changes: `--mode` is required.
  - `--recover` is opt-in. A store with a saved state refuses to start without it (`RECOVERY_REQUIRED`).
  - `virtual_paper_mainnet` refuses to start unless the drift guard passes. `--allow-non-oos` runs it anyway, but that store stays `NON_OOS` forever.
  - Optional limits: `--kill-switch`, `--max-daily-orders` (default 20), `--max-broker-errors` (default 5), `--heartbeat-minutes` (default 5).
  - Exit code 3 means a recovery halt.
- `broker-check` and testnet `run` need `CANDLE12_V3_PAPER_ENABLE=1`, `BINANCE_TESTNET_API_KEY` and `BINANCE_TESTNET_API_SECRET`. Use a dedicated testnet key; the client refuses any non-testnet host.
- `reconcile` refuses testnet stores. `oos-evaluate` accepts only an OOS-eligible `VIRTUAL_PAPER_MAINNET` store.

### State machine additions

The trade states are unchanged. These records are new:
- `inflight` in state.json: a request is written ahead before the broker call and cleared once the call is acknowledged or rejected. This is the persisted `ORDER_REQUESTED` state.
- `ORDER_FOUND_AFTER_TIMEOUT`: the request timed out, a lookup found the order, and it was adopted, not re-sent.
- `ORDER_LOST`: the request never reached the venue. It is not re-entered.
- `HALTED`, `HALT_CLEARED`, `ADAPTER_RESUMED`.
- `MISSING_FUNDING_DATA`, `DUPLICATE_TRADE_SUPPRESSED`.
- `RECOVERY_ORDER_ADOPTED`, `RECOVERY_FILL_APPLIED`, `RECOVERY_EXIT_REPLACED`, `RECOVERY_EXIT_RESIZED`, `RECOVERY_RESUMED_POSITION`, `RECOVERY_ENTRY_RESUMED`, `RECOVERY_HALTED`, `RECOVERY_COMPLETE`.
- `SIGNAL_MISSED` with reasons `KILL_SWITCH`, `HALTED`, `MAX_OPEN_TRADES` or `MAX_DAILY_ORDER_ATTEMPTS`, each with an open exception.

Persistence:
- state.json is written atomically (temp file plus replace). It includes the venue snapshot: for the simulated broker, its orders, position and fills.
- Every place and cancel is persisted before the call returns.
- Trades are written once per `trade_id`.
- New files: `alerts.jsonl` (missed candle, missing funding, order rejection, exit-order failure, unexpected position, recovery action, fingerprint drift, process restart, halt, kill switch) and `heartbeats.jsonl`.

### Restart recovery (`recovery.py`)

`run --recover` follows this sequence:
1. Load state.json.
2. Re-feed the detector the processed bars silently, which restores the swing and once-per-level bookkeeping exactly.
3. Restore slots, pending signals, in-flight requests, counters and the venue snapshot.
4. Query the venue: open orders, position, recent fills.
5. Reconcile, as below.
6. Only then process the bars that closed while the process was down. Entries on those bars are late and recorded as `LATE_PLACEMENT`.

| Local | Venue | Action |
|---|---|---|
| in-flight request | order or fill with that id | `RECOVERY_ORDER_ADOPTED` |
| in-flight entry request | nothing | `ORDER_LOST`, open exception, **no re-entry** |
| in-flight exit request | nothing | `ORDER_LOST`, then the exit is re-placed (row below) |
| any | fill not in the local book | `RECOVERY_FILL_APPLIED`; a partial entry fill restores the filled qty and resizes the exits |
| any | open order unknown locally | halt `UNRECONCILED_EXTERNAL_ORDER` |
| no open trade / `POSITION_CLOSED` | position ≠ 0 | halt `UNRECONCILED_EXTERNAL_POSITION` (`NO_LOCAL_TRADE_RECORD` / `LOCAL_POSITION_CLOSED`) |
| `POSITION_OPEN` | position ≠ local open qty | halt `UNRECONCILED_EXTERNAL_POSITION` (`POSITION_QTY_MISMATCH`) |
| `POSITION_OPEN` | both exits, right size | `RECOVERY_RESUMED_POSITION` |
| `POSITION_OPEN` | an exit missing | `RECOVERY_EXIT_REPLACED`: reduce-only, at the frozen plan stop / target |
| `POSITION_OPEN` | an exit sized for another qty | `RECOVERY_EXIT_RESIZED` |
| `ORDER_ACCEPTED` | entry resting | `RECOVERY_ENTRY_RESUMED` |
| `ORDER_ACCEPTED` | entry gone, no fill | `ORDER_LOST`, no re-entry |
| halted, halt exception open | — | `STILL_HALTED`; nothing resumes until `resolve-exception` |

- Every row writes an append-only event with `recovery_id`, `prior_state`, `observed_broker_state`, `action_taken`, `order_ids`, `timestamp` and `reason_code`, plus an alert.
- Halts are checked before any order is placed.
- A halted adapter never places an entry. It still manages exits.

### Binance broker findings (docs checked 2026-10-15; `broker_capabilities.json`)

- **Endpoints.** Binance moved conditional orders (STOP_MARKET, TAKE_PROFIT_MARKET, STOP, TAKE_PROFIT, TRAILING_STOP_MARKET) to the Algo Service effective **2025-12-09** (announced 2025-11-06).
  - `POST /fapi/v1/order` now rejects them with `-4120 STOP_ORDER_SWITCH_ALGO`.
  - The previous client sent the Candle-2 stop entry and both exits to `/fapi/v1/order` with `stopPrice`, so every one of those orders would have failed.
  - It now sends them to `POST /fapi/v1/algoOrder` with `algoType=CONDITIONAL`, `triggerPrice`, `workingType=CONTRACT_PRICE`, `clientAlgoId` and `reduceOnly`.
  - Queries and cancels use `GET/DELETE /fapi/v1/algoOrder`; open orders use `GET /fapi/v1/openAlgoOrders`; fills come through `actualOrderId`, then `/fapi/v1/order` and `/fapi/v1/userTrades`.
  - MARKET orders stay on `/fapi/v1/order`.
- **Testnet host.** The USDⓈ-M testnet REST host in the current docs is `https://demo-fapi.binance.com`, which is now the default. `testnet.binancefuture.com` is still accepted.
- **Client ids.** Binance requires `^[.A-Z:/a-z0-9_-]{1,36}$`. The adapter's ids (`BTCUSDT-LONG-2026-…+00:00-E`) break that rule, so they are hashed deterministically to `c12_` plus 32 hex characters.
- **Errors.** A duplicate client id is `-4116`. A timeout (`-1007`, or HTTP 503 "Unknown error") leaves the order state unknown, so the adapter looks the order up before any retry.
- **Supported types:** MARKET, STOP_MARKET, TAKE_PROFIT_MARKET. Any other type raises `UnsupportedOrderType` before the exchange is called.
- **Known limitations:**
  - The algoStatus enum is incompletely documented.
  - The `openAlgoOrders` parameters were truncated in the docs.
  - No error code is documented for a duplicate `clientAlgoId`.
  - There is no margin check before trigger, so a margin rejection of the stop entry shows up only at trigger time.
  - An untriggered conditional order cannot be modified, so a resize is a cancel plus a new order.
  - There is a limit of 200 conditional orders.
  - The changelog says nothing about the testnet's migration date.

### Broker check (`broker-check`)

It checks, against the testnet:
- testnet-only host enforcement;
- authentication;
- order rejection (reduce-only while flat);
- MARKET entry at the minimum viable quantity, and fill sync;
- reduce-only STOP_MARKET and TAKE_PROFIT_MARKET on the algo endpoint;
- open-order query;
- duplicate client id;
- cancellation;
- API timeout handling (a deliberate 0.1 ms timeout, then a lookup);
- one exit fill cancelling the other (a tight exit pair, waiting up to `--exit-wait`);
- no order after terminal state (zero exchange calls);
- every transition persisted.

Partial fills are checked only if the testnet produces one; otherwise the check is SKIPPED and does not block the pass. The run holds at most one trade and always cancels open orders and flattens in `finally`. It prints `BROKER_CHECK_PASS` only if every required check passes. **It has not been run against the testnet**: no testnet keys exist in this environment. The tests run it against a scripted fake exchange.

### Data backfill (`backfill-data`)

- Fetches closed mainnet 1h klines and funding settlements.
- Inserts into `ohlcv` and `grid_funding_settlements` with `ON CONFLICT DO NOTHING`. It uses its own writable connection; `_db.py` stays read-only.
- A fetched row that differs from the stored one is a reported conflict and is not applied. Missing bars and settlements are reported as gaps and never fabricated.
- The watermark file `data/candle12_v3_paper/backfill_state.json` records the last success per symbol and data type. A failed fetch leaves it unchanged.
- `--loop` runs it in the foreground.
- In `virtual_paper_mainnet`, a settlement missing for an open position raises `MISSING_FUNDING_DATA`: an open exception, an alert, and a block on OOS acceptance.

### Safety

- **Kill switch:** the global file `backend/data/candle12_v3_paper/KILL`. While it exists, no new entry is placed; exits keep working.
- **Limits:** max open trades per symbol 1; max entry attempts per UTC day 20; 5 consecutive broker errors halt the adapter.
- **Heartbeat:** every 5 minutes. Each heartbeat also re-checks the fingerprints of an OOS-eligible run, and a drift halts it.
- **Status:** `status` prints, from disk only:
  - mode, symbols, current state;
  - open position, open orders, in-flight requests;
  - last processed candle and last funding settlement;
  - last heartbeat;
  - open exceptions;
  - OOS eligibility with reasons, and fingerprint status;
  - the kill switch.

### Tests

`tests/test_candle12_v3_paper_ops.py` has 61 tests:
- mode separation and the mixed-mode refusals;
- every recovery row above, including the testnet-restart partial-fill path;
- broker-check pass and fail paths, against a fake exchange that enforces the algo endpoint;
- endpoint compatibility and unsupported types;
- gap detection;
- the missing-funding block;
- idempotent backfill, conflicts and watermarks;
- freeze in a temporary git repo (dirty refusal, commit, tag, `TAG_EXISTS`);
- drift (`CODE_DRIFT`, `CONFIG_DRIFT`, `NOT_FROZEN`, `LOCK_TAMPERED`);
- kill switch, error halt and daily cap;
- heartbeat and status;
- the OOS read: `TOO_EARLY` before 2027-10-16.

Existing tests: 87 pass. Three assertions were updated, none weakened:
- `test_testnet_order_request_is_signed_and_mapped` and `test_stop_and_target_orders_after_fill` now require the algo endpoint and `triggerPrice`. They used to require `stopPrice` on `/fapi/v1/order`, which Binance rejects.
- The OOS tests' default store mode is now `VIRTUAL_PAPER_MAINNET`, with new assertions that `PAPER`, `TESTNET_BROKER_CHECK` and `DRY_RUN` are refused.

All 167 CANDLE12 tests pass.

### Remaining risks

- **Testnet client unproven.** It has never touched the testnet. The algo status values, the `openAlgoOrders` shape and the duplicate-`clientAlgoId` behaviour are inferred from incomplete docs. `broker-check` is the first real evidence.
- **Virtual fills.** `virtual_paper_mainnet` fills are simulated with the engine's rules, so paper-vs-engine reconciliation checks the plumbing, not market execution. Real slippage stays unmeasured; the 0.05% stress run in the contract is the only guard.
- **One process, one host.** There is no process supervisor or external alert delivery (alerts are files), and the heartbeat is not watched by anything else.
- **Clean tree required.** The whole working tree must be committed for `freeze-oos`. As of now it has about 290 uncommitted entries across unrelated work.
- **Late start.** If the virtual run starts after 2026-10-16 00:00 UTC, any Candle-1 before the start is a missed trade and an exception.
- **Shared funding table.** The funding table `grid_funding_settlements` is shared with the grid book; the backfill only inserts.
- **Slower test runs.** Each persisted snapshot costs about 2 ms on Windows. The smoke test takes about 2× longer; at one bar an hour this does not matter live.

### Required before the first supervised `virtual_paper_mainnet` run

1. Review and commit this work, and every other pending change, so the working tree is clean.
2. Run `freeze-oos`. It writes and commits the lock and tags the commit. The guard must then report `OK`.
3. Run `backfill-data --symbols BTCUSDT --start 2026-10-12 --end now`, with no gaps reported. Stored funding currently ends 2026-10-11.
4. Start `run --mode virtual_paper_mainnet` before **2026-10-16 00:00 UTC**, under supervision. Check `status` (eligible store, fingerprint `OK`) and the first heartbeat.
5. Rehearse `kill-switch engage/release` and a `--recover` restart (stop the process, then restart it with `--recover`) before the first Candle-1 of the window if possible. On a clean store, recovery is opt-in for this first supervised run.

`broker-check` is **not** required for the virtual run: it validates the testnet API only.

**`testnet_broker_check` does not validate the strategy edge and never counts toward OOS.** Its prices, liquidity, fills and funding are the testnet's. It is never reconciled against the engine under the OOS tolerances, and no result from it can support or weaken the CANDLE12_V3 OOS claim.

## Checklist

| Item | Result | Evidence |
|---|---|---|
| V2 regression | PASS | `candle12_v2.py` is unchanged and its 30 tests pass. V3 set to V2's rules reproduces all 186 V2 BTC trades with 0 mismatches. |
| Gap execution tests | PASS | Unit tests cover gaps through entry, stop and target, LONG and SHORT. The deterministic BTCUSDT fixture (`test_candle12_v3_acceptance.py`) checks exact prices, quantity, fees, gross, net, all three R values and the tags. Real gap counts: see "Historical gap scan". |
| Per-side lock tests | PASS | A LONG book is never blocked by a SHORT, and the reverse. The shared lock blocks the other side. `shared` is refused for a one-sided book. |
| Minimum notional tests | PASS | Exactly at the minimum, below it, and rounding down to it or below it. A rejected trade is tagged and takes no position. Filters: BTCUSDT and ETHUSDT from exchangeInfo, 2026-10-15. |
| Quantity rounding tests | PASS | Floor to step, `BELOW_MIN_QTY`, `ROUNDING_SHORTFALL`. The risk tolerance has separate tests at 10% exactly, past it, and switched off. |
| Funding labeling | PASS | Each trade gets FULL / PARTIAL / NONE coverage. The run label is `PARTIAL_HISTORY_FUNDING_SENSITIVITY`, the coverage window is reported, and funding stays out of the acceptance metrics. |
| Breakeven tests | PASS | Set when above the old stop and below the TP1 bar's close. Skipped (and never re-judged) when the close is below BE. Skipped when BE is below the current stop. TP1 and the stop on one later bar stop out the whole position. Max-hold exit after breakeven. |
| Runner-target tests | PASS | TP2 when far enough. TP3 fallback. No qualifying target leads to a max-hold exit. SHORT side and slot order are covered. |
| BOS re-arm tests | PASS | Repeat close. New opposing swing. Deep pullback with no new swing. Opposing swing older than the setup. New level on the same side. SHORT mirror. Real-detector suppression. |
| OOS reservation | PASS | The window, code and config fingerprints, read guard and read rule are below. The pass thresholds are **proposed** and need sign-off before the first read. |
| Paper-fill reconciliation plan | PASS (plan only) | The plan is below. No paper adapter exists for V3 yet, so it cannot run. |

## Out-of-sample reservation

- **Reserved window:** BTCUSDT 1h bars from **2026-10-16 00:00 UTC** onward. No CANDLE12 run in this repo has read them. Every V3 result so far ends at or before 2026-10-15 13:00 UTC and is IN-SAMPLE.
- **Guard:** `candle12_v3.check_oos_guard` raises on any bar at or after `OOS_RESERVED_FROM` unless `CANDLE12_V3_ALLOW_OOS=1`. `run_candle12_v3_exec_fix.py` calls it on every load. Set the flag only for the single OOS read.
- **Frozen config:** `Candle12V3Config()` defaults, engine 3.1.0. Config sha256 (sorted-key JSON of `to_dict()`), first 16 hex: `529ed8e5a339e7cb`.
- **Frozen code** (sha256, first 16 hex). Any change to these files means a new strategy id, not a re-read:

| File | sha256[:16] |
|---|---|
| app/research/candle12_v3.py | e31fd15982e0a08f |
| app/research/candle12_v2.py | 0d6bc58552ec3da1 |
| app/research/rolling_structure.py | 841ecb1ecdfbb780 |
| app/research/combination_backtest.py | 66c717dea320f76a |
| app/signals/bos_engine.py | 451d88a48d510eec |
| app/signals/impulse_engine.py | 2abb56c588b239e0 |
| app/signals/pullback_engine.py | d27f1510cc8cea75 |
| app/signals/trend_engine.py | acf8df175bc47b52 |
| app/signals/swing_detector.py | 73fccca6f6d05972 |
| app/signals/stop_engine.py | 043908c5523dcee0 |
| app/signals/target_engine.py | 6421326461a1538e |
| app/signals/config.py | ec1a37466daf1bb7 |
| app/engines/mtf/indicators.py | 19d3a1c08aa9b7b9 |
| app/services/paper_sizing.py | bb63e460db36ca0b |

- **Read rule (proposed; confirm or change before the first read, never after):**
  - **When:** one read, once at least 30 LONG trades have closed in the window. If 2027-10-16 arrives with fewer than 30, label the read INSUFFICIENT_SAMPLE and wait; do not decide anything. The in-sample rate is about 22 trades a year, so 30 trades is roughly 16 months.
  - **Pass if all of these hold:**
    - net PnL > 0 at 0.02% slippage;
    - net PnL > 0 at 0.05% slippage;
    - profit factor ≥ 1.2;
    - max drawdown ≤ $340 (2× the in-sample $169.35);
    - every trade reconciles with the paper-fill plan's replay checks.
  - **Fail:** any miss. A fail retires the id. A new idea needs a new id and its own reservation.
  - Nothing is tuned between now and the read.

## Paper-fill reconciliation plan

**Prerequisite (not built):** a paper adapter for CANDLE12_V3_EXEC_FIX. At each Candle-1 close it places a stop-market order at the stop-order price, valid for Candle-2 only (cancelled at Candle-2 close). Quantity comes from `size_position` with the same filters. The stop and the full-exit target are placed as the engine models them.

For every paper trade, the same bars are replayed through `run_candle12_v3` and compared:

| # | Check | Tolerance | On breach |
|---|---|---|---|
| 1 | Every engine setup has a paper order, and every paper order has an engine setup | 0 mismatches | Halt the paper book until explained |
| 2 | Quantity | equal to the step | Halt |
| 3 | Entry fill vs engine fill (bps). Gap entries must carry `GAP_ENTRY_FILL` in the replay. | ≤ 3 bps per trade; rolling 20-trade mean ≤ 2 bps | Use the 0.05% results as the baseline and say so |
| 4 | Stop and target exit fills vs engine raw exit adjusted for slippage | as #3 | as #3; a gap exit must carry its gap tag |
| 5 | Fees charged vs 0.04% × executed notional | ≤ 0.5 bps | Correct the fee rate in the report (new id if it changes results) |
| 6 | Funding paid vs settlement history × position | report only | Funding history must cover the trade, or it is labelled PARTIAL |
| 7 | Same-bar SL + TP: check the 1m path to see whether stop-first held | report the count | If target-first is common, report the conservative bias |
| 8 | Rejections (`SIZE_REJECTED`, `MIN_NOTIONAL_REJECTED`, `RISK_TOLERANCE_REJECTED`) | same set in paper and replay | Halt |

The report is weekly, with one row per trade: engine values, paper values, difference, pass/fail. Paper results do not change any parameter; a change means a new strategy id.

## Historical gap scan

`backend/scripts/scan_candle12_v3_gaps.py`, all bars before 2026-10-16. Run 2026-10-15.

**Raw census.** Bars whose open lies outside the previous bar's range, in every series with more than 5,000 bars:

| Timeframe | Series | Bars | Open above prior high | Open below prior low | Missing-candle gaps |
|---|---:|---:|---:|---:|---:|
| 1h | 27 | 712,007 | 547 | 329 | 0 |
| 15m | 27 | 2,542,470 | 6,749 | 5,482 | 21 |
| 5m | 46 | 3,531,922 | 41,366 | 41,591 | 309 |

**Engine events.** V3 defaults, LONG and SHORT books, on 27 hourly series plus BTC, ETH and SOL at 15m. That is 30 cells and 4,571 trades.

| Event | Count | Where |
|---|---:|---|
| Gap through entry, filled (`GAP_ENTRY_FILL`) | 6 | APTUSDT 1h LONG, LINKUSDT 1h LONG, NEARUSDT 1h SHORT, BTCUSDT 15m SHORT, SOLUSDT 15m LONG, SOLUSDT 15m SHORT |
| Gap through entry on a setup the sizing rejected | 1 | ETHUSDT 15m LONG |
| Gap through stop (`GAP_STOP_FILL`) | 0 | — |
| Gap beyond target (`GAP_TARGET_FILL`) | 0 | — |
| BTCUSDT 1h (this book) | 0 of each | — |

V2 would have filled the 6 gap entries at the untraded trigger price.

The 925 rejections in the scan are mostly `RISK_TOLERANCE_REJECTED` margin-capped trades on symbols without exchange filters. The gap-stop and gap-target rules have never fired on stored data, so only the unit tests and the fixture prove them.
