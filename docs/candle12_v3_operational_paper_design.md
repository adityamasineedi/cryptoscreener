# CANDLE12_V3_OPERATIONAL_PAPER: design (DESIGN_ONLY)

**Status:** DESIGN_ONLY. Nothing here is implemented, scheduled or started. Implementation needs separate approval.
**Purpose:** execution and operational readiness evidence for CANDLE12_V3, without exposing or using any performance information from the formal OOS-2 window. The window opened 2026-10-16 00:00 UTC; the earliest read is 2027-10-16.
**This track does not validate strategy performance.** Only the formal OOS-2 read can do that.

## 1. The two problems this design has to solve

1. **The "allowed" outputs can leak outcomes.** The track uses the same frozen rules on the same BTCUSDT 1h forward bars as OOS-2, so every trade is an OOS-2 trade. For a LONG, exit purpose (stop or target), exit price, exit time, holding time, per-trade exit fee and per-trade funding each reveal the trade outcome. "N stop fills, M target fills" is the win/loss count. All exit-side detail is therefore **sealed**. Only entry-side detail and outcome-blind verdicts are visible.
2. **A second runner adds no execution information.** OOS-2 already *is* a virtual paper adapter on these rules and bars, with a deterministic SimulatedBroker. A second runner with the same broker would produce byte-identical orders and fills. It would add load, a second Binance feed and interference risk, and no new execution evidence.

**Recommended architecture: V, a sealed operational view over OOS-2's own records.** It has its own store and its own outputs, and no second collector. Architecture S, a shadow runner, is described as the alternative. Its only extra value is a hash-only determinism cross-check.

## 2. Architecture

### V. Sealed operational view (recommended)

```
OOS-2 store (read-only, never locked, never written)            CANDLE12_V3_OPERATIONAL_PAPER store (own)
 events.jsonl ─┐                                                  manifest.json
 exceptions.jsonl ─┤   opsview (read-only parser, outcome-blind)    ops_events.jsonl   (blind, allowed fields only)
 alerts.jsonl ─┼──>  maps + aggregates + reconciles (blind)  ──>   recon.jsonl        (blind per-trade verdicts)
 heartbeats.jsonl ─┤                                                ops_metrics.jsonl  (hourly snapshot)
 state.json ─┘   (trades.jsonl and reconciliation detail: NOT READ)  seal_log.jsonl     (every sealed-data access)
```

- **Code:** `backend/scripts/candle12_v3_operational_paper/opsview.py`. This is outside the 37 fingerprinted files. It may *import* package modules read-only, and never modifies them.
- **Inputs:**
  - opens OOS-2 files read-only, with sharing that never blocks the runner's writers or its atomic replaces;
  - takes no lock and never touches `runner.lock`;
  - tolerates a partial last line (skips it until the next run, never errors).
- **Never reads** `trades.jsonl`, `signals.jsonl`, `reconciliation.json` or `oos_reads.jsonl`. It needs no outcome field to produce its outputs.
- **Exit events** are read only as event kinds. `STOP_TRIGGERED` and `TARGET_TRIGGERED` are mapped to a single purpose-blind `EXIT_FILLED` before anything is written.
- **Schedule:** once an hour at HH:12 UTC. That's after the bar close and before the HH:20 backfill. Priority **BelowNormal**: the view must never compete with the runner, which runs at Normal. On-demand runs are allowed too.

### S. Shadow runner (alternative, not recommended now)

- A second `virtual_paper_mainnet`-style process, track ID `CANDLE12_V3_OPERATIONAL_PAPER`, with its own store `backend/data/candle12_v3_paper/operational_paper/`, its own task, the same frozen config, and a SimulatedBroker.
- **Only benefit: a determinism check.** Compare a SHA-256 of OOS-2's event stream with the shadow's per bar. The result is only *equal* or *different*, so it reveals nothing. Any difference is a nondeterminism bug.
- **Costs:**
  - a second feed;
  - about doubled Binance REST polling;
  - a second runner process;
  - the same shared venv.
- It must never write OOS-2 files, and never be counted as OOS evidence.

## 3. Store schema (own store, both architectures)

`backend/data/candle12_v3_paper/operational_paper/` is already git-ignored under `backend/data/candle12_v3_paper/`. Files are append-only JSON lines, except the manifest.

| File | Row fields |
|---|---|
| `manifest.json` | `track_id` (CANDLE12_V3_OPERATIONAL_PAPER), `label` (OPERATIONAL_PAPER_ONLY), `architecture` (V or S), `source_store`, `oos_contract_id`, `oos_lock_hash`, `seal_until` (2027-10-16T00:00:00Z), `design_version`, `created_at`, `counts_toward_oos` (false), `strategy_validation` (false) |
| `ops_events.jsonl` | `seq`, `at`, `kind`, `trade_ref`, `bar_time_entry_side`, `detail` (allowed fields only; see section 6) |
| `recon.jsonl` | `trade_ref`, `entry_fill_delta_bps`, `entry_qty_match`, `entry_fee_delta_usd`, `exit_reconciled`, `abs_exit_fill_delta_bps`, `funding_reconciled`, `exceptions` (category list) |
| `ops_metrics.jsonl` | the hourly snapshot of section 6: counts, delays, health |
| `seal_log.jsonl` | `at`, `who`, `reason`, `what`. Every access to sealed data; must stay empty before 2027-10-16 unless break-glass (section 5) |

- `trade_ref` is an opaque HMAC of the trade id, with a key stored only in the track's own config. The view can then join entry and exit events without exposing timestamps.
- `bar_time_entry_side` is the Candle-1 and Candle-2 times only. Those are known before any outcome. There is no exit time.

## 4. Order-state machine and visibility

These are the frozen adapter states, unchanged. The view only decides what is visible.

| Adapter state / event | Operational view |
|---|---|
| `SIGNAL_DETECTED` | visible (count, Candle-1 time) |
| `ORDER_REQUESTED` / `ORDER_ACCEPTED` / `ORDER_REJECTED` (+reason) | visible |
| `ORDER_FILLED` / `ORDER_PARTIALLY_FILLED` (entry) | visible: qty, expected vs actual fill, slippage bps, entry fee |
| `ORDER_CANCELED` (`CANDLE2_CLOSED`, entry not filled) | visible |
| `NO_FILL`, `SIGNAL_MISSED` (+reason), `SIGNAL_BLOCKED`, `ORDER_LOST` | visible |
| `EXIT_ORDERS_PLACED` | visible as an event only; the stop and target **levels are sealed** (they imply R) |
| `STOP_TRIGGERED` / `TARGET_TRIGGERED` | **mapped** to `EXIT_FILLED`; purpose, price and time sealed |
| `ORDER_CANCELED` (`POSITION_CLOSED`, the sibling exit) | **mapped** into `EXIT_FILLED` (not shown separately, since which order was cancelled reveals the purpose) |
| `POSITION_CLOSED` | **mapped** into `EXIT_FILLED`; net PnL and exit reason sealed |
| `HALTED`, `RECOVERY_*`, `ADAPTER_RESUMED`, `RUNNER_CRASH*`, `DATA_GAP`, `DATA_STALE`, `MISSING_FUNDING_DATA` | visible |

## 5. Seal rules

1. **Sealed until 2027-10-16T00:00:00Z** (the formal read):
   - exit purpose;
   - exit price, time and slippage sign;
   - holding time;
   - per-trade exit fee and funding amount;
   - stop and target levels;
   - every PnL, R or equity field.
2. **The view never reads** `trades.jsonl`, `signals.jsonl`, `reconciliation.json` or `oos_reads.jsonl`. Its parser rejects any field outside the allow-list (fail closed).
3. **Aggregates must be purpose-blind.** A view output must be byte-identical whether a trade exited at its stop or at its target. Section 8 tests this.
4. **No per-trade exit detail is ever displayed,** even when reconciliation fails. A failed exit check shows only the category and the absolute delta in bps.
5. **Break-glass.** Sealed detail may be opened before 2027-10-16 only to resolve an execution exception that cannot be diagnosed otherwise. It needs the owner's written approval. Each access is logged in `seal_log.jsonl` (who, when, why, what), and every break-glass event must be disclosed with the OOS-2 result. It must never lead to a rule change.
6. **No decision of any kind** (continue, pause, size, start live, change anything) may be based on sealed data before the formal read.
7. **The existing `reconcile` CLI must not be run during the window.** It writes `reconciliation.json`, which holds full per-trade engine and paper values, into the OOS-2 store. A blind reconciliation (section 7) writes only verdicts into the track's own store.

## 6. Operational metrics (allowed) and forbidden metrics

**Allowed. Every metric is outcome-blind.**
- Bar-processing delay (seconds after close), missed bars, data gaps and stale data.
- Heartbeat age. Recovery, restart and halt events.
- Signals detected. Entry orders requested, accepted, rejected (by reason), filled, partially filled and cancelled. Entry slippage in bps (expected vs actual). Entry fee delta.
- Exits: a count of `EXIT_FILLED` (purpose-blind). Exit reconciled yes/no. Absolute exit-fill delta in bps, pooled across purposes.
- Funding settlements observed vs expected. Funding reconciled yes/no (no amounts).
- Exceptions by category: open or resolved, and time to resolution.
- Store integrity: sequence contiguity, torn-line checks, duplicate trade suppression. Fingerprint status. Resource use.

**Forbidden before 2027-10-16:**
- PnL, R, win rate, loss rate, expectancy, equity, drawdown;
- the number of winners or losers;
- exit purpose, singly or aggregated (stop-fill or target-fill counts);
- exit prices and times;
- holding times;
- per-trade funding or exit fees;
- MAE or MFE;
- any cumulative or derived series;
- any rule to continue, pause or size based on any of these.

## 7. Blind reconciliation: tolerances and escalation

**What is compared.** For each trade, the paper fills from the OOS-2 events are compared with an engine replay on the same closed bars. The replay runs in memory inside `opsview` and is never written; only verdicts are kept. The tolerances are the OOS-2 contract's, unchanged:

| Check | Tolerance | Visible output |
|---|---|---|
| Entry fill vs expected | within 0.05% of the expected fill | delta in bps |
| Entry quantity | exact (≤ 1e-12) | match yes/no |
| Entry fee vs computed (0.04% taker) | within $1e-6 | delta in USD |
| Stop or target exit fill vs expected | within 0.05% | exit reconciled yes/no, absolute delta in bps, purpose hidden |
| Funding vs settlement history | equal (≤ $1e-9) where reported | funding reconciled yes/no |
| Signal time, Candle-1/2 data, order prices | exact | pass/fail |
| Missing or unexpected trade, rejection mismatch | 0 | category |

**Escalation:**
1. Any breach writes an open row in the track's own exceptions file and an operational alert.
   - The OOS-2 store is never written. OOS-2's own exceptions already block acceptance through the frozen code.
2. Two or more breaches of the same category within 7 days: escalate to the owner, who reviews by category without opening the seal.
3. A breach that cannot be diagnosed blind: the break-glass procedure (section 5.5), or leave it open, which blocks OOS acceptance through OOS-2's own record if the frozen reconcile later confirms it.
4. A store-integrity or fingerprint failure: stop the view. The runner is never touched.

## 8. Test plan

All tests use synthetic data, or seen history before 2026-10-16. **None runs on OOS bars.**

1. **Allow-list.** A forbidden-key scanner over every file the view writes finds no PnL, R, win, loss, equity, exit-price, exit-time, purpose, holding, MAE or MFE key or value.
2. **Purpose-blindness.** Two synthetic OOS-2-shaped stores, identical except that one trade exits at its stop and the other at its target, give byte-identical view outputs.
3. **The seal is enforced in code.** The view refuses to open `trades.jsonl`, `signals.jsonl`, `reconciliation.json` or `oos_reads.jsonl`, and each attempt is logged.
4. **Reconciliation is correct.** Injected entry, exit, quantity, fee and funding mismatches produce exactly the expected verdicts and categories, and no exit detail is shown.
5. **Read-only isolation.**
   - The OOS-2 store's file hashes and mtimes are unchanged after a run.
   - No handle blocks an atomic replace during a concurrent-writer stress test.
   - `runner.lock` is untouched.
6. **A partial last line** is skipped without error and picked up on the next run.
7. **Resources:** under budget (section 9) on a year-sized synthetic store, about 9,000 bars and 300 trades.
8. **Freeze integrity:** guard OK and the tree clean before and after (no fingerprinted file touched).
9. **Architecture S only:** the shadow's per-bar event-stream hashes equal OOS-2's on a seen-history replay, and a deliberately perturbed shadow is detected as `different`.

## 9. Resource budget

- **V:**
  - under 5 s CPU and under 150 MB RAM per hourly run;
  - reads only (about a few MB of JSON lines over a year);
  - BelowNormal priority;
  - no network, except an optional exchangeInfo read for filters;
  - skips its run if commit headroom is under 1.5 GB or the runner's heartbeat is over 7 min old.
- **S:**
  - under 100 MB RSS and under 2% average CPU;
  - one REST poll every 15 s;
  - started only if commit headroom stays at least 2 GB;
  - stopped automatically if headroom falls under 1 GB or the OOS-2 heartbeat is late.

## 10. Rollback

- **V:**
  - delete the HH:12 scheduled task;
  - archive or delete `operational_paper/`;
  - remove the script directory.

  Nothing in OOS-2 was written, so nothing there needs undoing.
- **S:**
  - disable and remove the shadow task;
  - stop only the verified shadow process (by command line and store path);
  - archive its store.

  OOS-2's processes, tasks, store and files are untouched.

## 11. Isolation summary

- Own track ID, own store and own records.
- No writes to the OOS-2 store, tasks or processes.
- No restart of OOS-2.
- No fingerprinted file touched.
- No second collector in V.
- Bounded CPU and memory.
- Labelled `OPERATIONAL_PAPER_ONLY`: `counts_toward_oos=false`, `strategy_validation=false`.

**This track does not validate strategy performance and never counts toward OOS.** Its outputs are execution and operational evidence only. Every performance field stays sealed until the formal OOS-2 read on or after 2027-10-16.
