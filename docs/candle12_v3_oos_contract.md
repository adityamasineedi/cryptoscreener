# CANDLE12_V3_EXEC_FIX: out-of-sample contract

**Contract id:** `CANDLE12_V3_EXEC_FIX/OOS-2`
**Supersedes:** `CANDLE12_V3_EXEC_FIX/OOS-1`, frozen 2026-10-15 15:00:23 UTC and now **SUPERSEDED**.
- **Why:** after the OOS-1 freeze, the paper package gained the operations layer. That layer adds execution modes, restart recovery, the Binance algo-order endpoints, backfill, the freeze and drift guards, preflight, safety, run locking and record integrity. Seven fingerprinted package files changed, and the guard reported CONFIG_DRIFT.
- **What did not change:** no strategy, detection, entry, gap, stop, target, sizing, fee, slippage, threshold, symbol or date rule changed. The other 21 OOS-1 fingerprinted files (engine, detector, signal, sizing and data-loader code) have content identical to the OOS-1 hashes. 17 are byte-identical. `candle12_v3.py`, `feeds.py`, `reconcile.py` and `tracker.py` differ only because the OOS-2 `.gitattributes` pin converts their line endings from LF to CRLF.
- **Where OOS-1 is kept:** its lock and contract text are preserved byte-for-byte in `docs/candle12_v3_drift_review_2026-10-15/`, with these sha256 values and drift-review commit `fd2de20`:
  - lock: `82f54fc792027d4aad23c7a7e4db40c51ce5061ec3d0735273c19a1cdf57e599`
  - contract: `09ac3c85d39d585736776bea7e52a7b1b95675225dd597d62649a61500c3088a`
- `freeze-oos` re-verifies both hashes before it writes the OOS-2 lock.

**Terms:** the sections below, carried over from OOS-1 unchanged in substance. Same window, same earliest read, same book, same thresholds.
**Baseline freeze:** written by `freeze-oos` into `docs/candle12_v3_oos_contract.lock.json`, then committed and tagged `candle12-v3-oos-2-freeze-<date>` (section 9). Until then the guard refuses every OOS-eligible run with `NOT_FROZEN`. `freeze-contract` cannot write an OOS lock.
**Runner:** `backend/app/research/candle12_v3_paper/oos.py`, invoked as `python -m app.research.candle12_v3_paper.cli oos-evaluate`.

These rules were fixed before any bar from the window existed. Nothing in this document may be changed after 2026-10-16 00:00 UTC except by starting a new contract version with a new id. The document's sha256 goes into the lock as `contract_fingerprint`, so an edit after the freeze shows up as `CONFIG_DRIFT`.

## 1. Window

| Rule | Value |
|---|---|
| OOS start | **2026-10-16 00:00 UTC**. The first trade's Candle-1 must close at or after this time. |
| Warm-up | the 1,000 BTCUSDT 1h bars before the start; detection and BOS re-arm bookkeeping run through them, but no trades |
| Earliest evaluation date | 2027-10-15 00:00 UTC |
| Minimum elapsed time | 12 months since the OOS start, i.e. 2027-10-16 00:00 UTC |
| **Effective earliest read** | **2027-10-16 00:00 UTC**. Both conditions must hold, so the later one governs. |
| Minimum closed trades | 30 |

The runner refuses to evaluate before the effective earliest read, and with fewer than 30 closed trades. The only exception is `--diagnostic`, explained in section 6.

## 2. Accepted book

| Item | Value |
|---|---|
| Strategy | CANDLE12_V3_EXEC_FIX, engine 3.1.0 |
| Market | BTCUSDT 1h, LONG only |
| BOS re-fire | `once_per_level` |
| Targets | `nearest_target_full_exit` |
| Lock | `per_side` |
| Entry | Candle-2 stop at C1 high × 1.0002 (0.02% slippage, `inclusive`). If Candle-2 opens above C1 high, fill at the open × 1.0002 (`GAP_ENTRY_FILL`). |
| Exits | Reduce-only stop (structural) and take-profit (nearest target). Gap-stop and gap-target rules as in engine 3.1.0. Same-bar stop and target counts as the stop. |
| Fees | Taker 0.04% on each side |
| Slippage | 0.02% adverse (primary) and 0.05% adverse (stress) |
| Sizing | $20 intended risk, $1,000 constant equity, 2× leverage, quantity floored to 0.001 |
| Exchange filters | Minimum notional $50 enabled; minimum quantity 0.001 |
| Risk tolerance | `risk_tolerance_pct = 0.10`. This is a **trade-selection filter**: any trade whose actual $ risk differs from $20 by more than 10% is not taken. |
| Funding | Enabled where settlement history exists. Reported separately. Counted in the primary net only for trades whose whole holding period is covered (`funding_coverage = FULL`). |
| Execution mode | `virtual_paper_mainnet` only: mainnet candles, mainnet funding, simulated fills (section 8) |
| Allowed symbols | BTCUSDT |
| Config fingerprint (sha256) | Recorded in the lock by `freeze-oos`. The terms now include the OOS mode and the allowed symbols, so the value differs from the 2026-10-15 draft (`d439c0b5…`). |
| Code fingerprint (sha256 over the files listed in the lock) | Recorded in the lock by `freeze-oos`. The list now covers 37 files: it adds the mode, recovery, safety, guard, backfill, freeze, preflight and capability modules and `broker_capabilities.json`. The 28-file draft value (`10f41813…`) no longer applies. |

**How the metrics are computed.** The frozen engine (`run_candle12_v3`) is replayed over the OOS bars, with the contract config, the 1,000-bar warm-up and `index_start` at the OOS start. Trades count when their entry is at or after the OOS start and they closed by the evaluation time. The primary run uses 0.02% slippage and the stress run 0.05%. Paper trading does not produce the metrics; it must reconcile with them (rule 7).

## 3. Primary pass rule

All of these must be true:

1. At least 30 closed trades.
2. At least 12 months elapsed since the OOS start, and the date is on or after 2027-10-15. Together: no read before 2027-10-16 00:00 UTC.
3. Net PnL > 0 after fees and adverse slippage, plus funding on fully covered trades.
4. Net PnL > 0 at both 0.02% and 0.05% adverse slippage.
5. Profit factor ≥ 1.30 (primary run).
6. Maximum peak-to-trough drawdown ≤ 17R on the intended $20 risk, which is $340 under the fixed $1,000 equity convention. It is measured on the cumulative primary net, trade by trade, in exit order.
7. Paper reconciliation has no unresolved execution exceptions.
8. No strategy, config, universe, fee, slippage, target, BOS, risk-tolerance or data-handling change occurred during the window. This is checked as no drift of the code and config fingerprints from the lock.

## 4. Classification

The runner lists every failure. The reported class is the first failure in this order:

| Order | Class | When |
|---:|---|---|
| 1 | `CONFIG_DRIFT` | Any fingerprinted file, or the contract config, differs from the lock (rule 8) |
| 2 | `EXECUTION_MISMATCH` | Any open paper reconciliation exception (rule 7) |
| 3 | `INSUFFICIENT_SAMPLE` | Fewer than 30 closed trades; reachable only in a diagnostic read, since the runner refuses otherwise |
| 4 | `OOS_FAIL_EDGE` | Primary net ≤ 0, or profit factor < 1.30 (rules 3 and 5) |
| 5 | `OOS_FAIL_ROBUSTNESS` | Stress (0.05%) net ≤ 0 (rule 4) |
| 6 | `OOS_FAIL_RISK` | Drawdown > 17R (rule 6) |
| — | `OOS_PASS` | None of the above, in a non-diagnostic read |

Further rules:
- **No conditional pass.** A read either passes or it does not.
- **No refitting after a failure.** A failed contract is closed. A new idea needs a new strategy id and a new contract with its own untouched window.
- **Any change to code, config, symbol, timeframe, fee, slippage, risk, target, BOS or data handling** creates a new version and restarts the OOS window. That includes the paper adapter and the database loader in the fingerprint list.

## 5. Paper reconciliation

Every paper trade is compared field by field with the engine replay on the same closed bars (`reconcile.py`). The fields are:
- signal time, Candle-1 OHLC, Candle-2 OHLC;
- trigger, expected fill, actual fill, entry slippage;
- stop and target prices;
- quantity, notional, intended risk, actual risk;
- fees, funding, gross PnL, net PnL;
- R on actual risk and on the intended $20;
- exit reason, exit time, exit fill;
- rejection tags.

| Field | Tolerance |
|---|---|
| Entry fill | within 0.05% of the engine's expected fill; otherwise an exception whose resolution must explain the gap or market impact |
| Stop exit fill | within 0.05% of the engine's expected stop exit fill; otherwise as above |
| Target exit fill | within 0.05% of the engine's expected target exit fill; otherwise as above |
| Quantity | exact (≤ 1e-12) after exchange step rounding |
| Fees | computed fee within $0.000001 of the exchange-reported fee, where reported |
| Funding | computed funding equal (≤ $1e-9) to exchange-reported funding, where reported |
| Signal time, candle data, exit reason, exit time, rejection tags | exact |
| Order prices (trigger, stop, target) | exact (relative 1e-9) |

Anything outside tolerance becomes an exception, as do these operational events:
- a missing or unexpected trade;
- a rejection mismatch;
- a partial fill;
- an exchange rejection;
- a late placement;
- price beyond the trigger at placement;
- broker unavailable;
- a data gap;
- a failed exit order.

An exception stays open until resolved with a written explanation (`resolve-exception`). Any open exception blocks acceptance (`EXECUTION_MISMATCH`).

## 6. Reads and records

- `oos-evaluate` needs `CANDLE12_V3_ALLOW_OOS=1` and a `VIRTUAL_PAPER_MAINNET` store whose manifest says `counts_toward_oos: true`. That flag is set only when the store was created while the drift guard passed. Smoke, dry-run, `TESTNET_BROKER_CHECK` and legacy `PAPER` stores are refused with `NOT_A_PAPER_STORE`. A virtual store created with `--allow-non-oos` is refused with `NOT_OOS_ELIGIBLE`.
- Every read appends a record to `<paper store>/oos_reads.jsonl`. It holds:
  - the code, config and data fingerprints, and the lock's fingerprints;
  - the evaluation timestamp and the as-of time;
  - the classification, every failure, and the primary and stress metrics.
- `--diagnostic` allows a read before the gates are met. It is labelled `NON_ACCEPTANCE_DIAGNOSTIC`, has `acceptance: false`, and is logged like any other read. A diagnostic read is a look at the window: it must be disclosed with the final result, and it must not lead to any change.
- A smoke run (`NON_ACCEPTANCE_SMOKE_ONLY`) runs on seen bars only, never touches the window, and is never strategy validation or OOS evidence.

## 7. What this contract does not claim

It does not claim an edge. Every result to date (2023-01-01 to 2026-10-15) is IN-SAMPLE. A pass would be evidence for this exact book on this exact window, nothing more.

## 8. Execution modes and what counts toward OOS

| Mode | Candles | Funding | Fills | Store | Counts toward OOS | Strategy validation |
|---|---|---|---|---|---|---|
| `virtual_paper_mainnet` | mainnet `fapi.binance.com` | mainnet settlements | `SimulatedBroker` (engine fill rules) | `VIRTUAL_PAPER_MAINNET` | **yes**, if created under a clean freeze | yes |
| `testnet_broker_check` | testnet `demo-fapi.binance.com` | testnet | `BinanceTestnetBroker` | `TESTNET_BROKER_CHECK` | **never** | **never** |
| smoke / dry-run | stored history (seen) | stored history | `SimulatedBroker` | `SMOKE` / `DRY_RUN` | never | never |

**Mixing is prohibited.** Mainnet candles with testnet fills, or testnet candles with simulated fills, is refused with `MIXED_DATA_BROKER_MODE` by the CLI, by `modes.resolve`, and by `run_live`. A testnet fill comes from a different order book with different prices and liquidity. Reconciling it against mainnet bars would compare two unrelated markets: every difference would look like an execution error, or would hide one. Testnet stores are never reconciled against the engine (`reconcile` refuses them with `TESTNET_NOT_COMPARABLE`), and their results are never compared with the engine under the section 5 tolerances.

**Counts toward OOS:** closed trades whose Candle-1 closes at or after 2026-10-16 00:00 UTC, in a `VIRTUAL_PAPER_MAINNET` store created under a clean freeze, reconciled with the engine replay. The acceptance metrics are always the engine replay's (section 2). The paper store supplies the reconciliation and the exception record.

**Does not count:** anything on testnet; smoke and dry runs; diagnostic reads; a virtual run started without a clean freeze (`--allow-non-oos`, labelled `NON_OOS` permanently); any run after fingerprint drift.

**Open exceptions block acceptance** (`EXECUTION_MISMATCH`). The new ones are:
- `MISSING_FUNDING_DATA`: an open position lived through a settlement the funding feed does not have, or a trade closed without full funding coverage. Funding is never filled in.
- `ORDER_LOST`: a request written ahead was not at the venue after a restart. It is not re-sent.
- `HALTED`: a safety limit, an unknown order state, or a recovery halt.
- Signals missed because of the kill switch or a safety limit.

## 9. Freeze, drift and timing

- **`freeze-oos`** refuses a dirty working tree (`DIRTY_WORKING_TREE`; untracked files count). On a clean tree it writes the lock with:
  - the git commit SHA and branch;
  - the config, code and contract fingerprints;
  - `oos_start` 2026-10-16T00:00:00Z and `earliest_evaluation` 2027-10-16T00:00:00Z;
  - allowed symbols `[BTCUSDT]` and allowed modes `[virtual_paper_mainnet]`;
  - `frozen_after_oos_start`;
  - a hash of the lock itself.

  It then commits only the lock file and adds the annotated tag `candle12-v3-oos-freeze-YYYY-MM-DD`. Nothing is pushed. A second freeze on the same day fails with `TAG_EXISTS`.
- **Drift guard** (`guard.py`). It runs before an OOS-eligible run starts or resumes, and again at every heartbeat. It returns one of:
  - `LOCK_MISSING`;
  - `NOT_FROZEN`;
  - `LOCK_TAMPERED` (the lock's hash does not match);
  - `CONFIG_DRIFT` (contract terms or the contract document changed);
  - `CODE_DRIFT` (a fingerprinted file, or the file list, changed);
  - `MODE_NOT_ALLOWED` / `SYMBOL_NOT_ALLOWED`.

  Any of these refuses the start or resume. During a run it halts the adapter and raises a `FINGERPRINT_DRIFT` alert. `testnet_broker_check` and smoke runs are never blocked; they are labelled `NON_OOS`.
- **Exact times (UTC, candle-open):**
  - The window starts at **2026-10-16 00:00:00**. The first eligible Candle-1 is the bar that opens at that instant.
  - The earliest acceptance read is **2027-10-16 00:00:00**. The runner returns `TOO_EARLY` before it.
- **Timing risk.** The freeze and the virtual run should both be in place before 2026-10-16 00:00 UTC. A Candle-1 that closes while the adapter is not running is recorded as missed. The engine replay still counts that trade, so it becomes a reconciliation exception that has to be explained before acceptance.
