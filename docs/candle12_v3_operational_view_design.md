# CANDLE12_V3_OPERATIONAL_VIEW: design (DESIGN_ONLY)

**Status:** DESIGN_ONLY. Nothing is implemented or started; implementing it needs separate approval. It replaces the withdrawn CANDLE12_V3_EXPLORATORY_PAPER and CANDLE12_V3_OPERATIONAL_PAPER proposals. There is no second runner, no exploratory track, no second feed and no broker.
**Core rule:** the view never reveals an OOS-2 strategy outcome before the formal OOS read (on or after 2027-10-16). It reports operational health only.
**This view does not validate strategy performance and never counts toward OOS.**

## 1. Architecture

- **What it is:** a short-lived, on-demand, read-only report command at `backend/scripts/candle12_v3_operational_view/opsview.py`. It is outside the 37 fingerprinted files and changes none of them.
- **What it never does:**
  - start a collector or a market-data feed;
  - connect to a broker or place an order;
  - write any OOS-2 file;
  - take or touch `runner.lock`;
  - run on a schedule. On-demand only; a light schedule would need separate approval.
- **Output:** a single report to stdout and to the view's own directory, `backend/data/candle12_v3_paper/operational_view/` (git-ignored). The directory holds `reports/<UTC>.json` and `access_log.jsonl`.
- **Process limits:**
  - runs at BelowNormal priority;
  - refuses to start when commit headroom is under 1.5 GB or the runner's heartbeat is over 7 minutes old;
  - aborts after 30 s or above 150 MB of memory.

## 2. Data flow and store-access rules

Sealed outcome data sits inside files that operations also needs, for example the `slots` in `state.json` and the heartbeats, and the exit events in `events.jsonl`. File permissions therefore cannot separate it, so **the view enforces the seal with a field allow-list: only listed fields are ever read.** A field that isn't on the list is never read, even if it appears in a file the view opens.

| Source (read-only) | Fields the view may parse | Never parsed |
|---|---|---|
| `manifest.json` | mode, execution_mode, contract_id, counts_toward_oos, label, created_at, history_start, first_trade_index | — |
| `state.json` | last_closed_time, last_closed_index, completed_bar_index, halted, halt_exception_id, `len(inflight)`, saved_at | slots, pending, broker, orders, last_exit_bar, funding_flagged, attempts_by_day |
| `heartbeats.jsonl` | at, halted, `len(inflight)`, last_closed, kill_switch | slots |
| `events.jsonl` | the event kind and its time, for the allow-listed kinds in section 4. For entry orders, also the order type, qty, expected and actual entry fill and fee. | every exit-side field. Exit kinds are mapped (section 4). net_pnl_usd and exit_reason are never read. |
| `exceptions.jsonl` | id, category, status, created_at, resolved_at | detail payloads (they can hold prices); notes |
| `alerts.jsonl` | alert kind, at | payload fields (slots, trade_id, prices) |
| `runner.lock` | pid, started_at, refreshed_at | — |
| PostgreSQL (read-only session) | candle and settlement timestamps, for coverage | rates and prices |
| `guard.fingerprint_status()` | status, codes | — |
| **Never opened** | `trades.jsonl`, `signals.jsonl`, `reconciliation.json`, `oos_reads.jsonl`, `state.json.tmp` | — |

The parser fails closed. The report fails if an allow-listed field holds a value of an unexpected shape, for example a dict where a count was expected.

## 3. Sealed fields, before the formal read

Every item below is sealed, singly, aggregated or derived:
- **Outcomes:**
  - exit reason (STOP vs TARGET) and any count of either;
  - winner or loser counts;
  - PnL, R and equity, per trade or cumulative.
- **Exit-side values:**
  - exit price, exit time and exit slippage sign;
  - per-trade holding time;
  - stop and target levels, which imply R.
- **Fees and funding:**
  - per-trade funding;
  - exit fees, and **fee totals** (a target exit fills higher than a stop, so the fee total reveals the mix);
  - funding totals.
- **Position timeline:**
  - position open or closed state over time, and holding times reconstructed from heartbeats, state or alerts;
  - the per-trade sequence of exit events with timestamps.
- **Anything else that identifies whether a trade won or lost.** This includes MAE and MFE, and exit-side deviation *magnitudes* (see section 5).

## 4. Allowed operational metrics

Only these, and only purpose-blind:
- **Latest processed bar.** Processing delay after close: the latest value and the maximum over the window.
- **Heartbeat age.** Runner uptime since the last start, and restart count (ADAPTER_RESUMED and RUNNER_CRASH_RECOVERED counts).
- **Bars processed:** the `last_closed_index` minus the first window index. Missed or late bars and data gaps (DATA_GAP, DATA_STALE, MISSED_CANDLE counts).
- **Orders** submitted, accepted, rejected (by reason), filled or partially filled, cancelled.
  - Entry orders: shown in detail.
  - Exit orders: shown only as totals. They're identical on every path under the frozen `nearest_target_full_exit` book (two placed, one filled, one cancelled per trade), which section 9 tests.
- **Reconciliation:** pass/fail per check category, exception type, and the count of unresolved reconciliation exceptions.
- **Aggregate entry-fill deviation** (bps): mean and maximum. Exit-side deviation is pass/fail against tolerance only.
- **Funding coverage:** every settlement since the window start is present in the runner feed and the database (yes/no), plus the MISSING_FUNDING_DATA count. No amounts.
- **Store integrity:** sequence numbers contiguous, no torn lines, duplicate-trade suppressions counted.
- **Fingerprint status** and clean-tree status for the contract paths.
- **Open exceptions and alerts:** counts by category, open or resolved.

Event mapping before anything is counted:

| Event | Mapped to |
|---|---|
| `STOP_TRIGGERED`, `TARGET_TRIGGERED`, exit `ORDER_FILLED`, sibling `ORDER_CANCELED (POSITION_CLOSED)`, `POSITION_CLOSED` | `EXIT_COMPLETED` (one per closed trade; no time, price or purpose) |
| `EXIT_ORDERS_PLACED` | `EXIT_ORDERS_PLACED` (count only; levels dropped) |

## 5. Reconciliation without leakage

Inside the process only, nothing written, the view replays the frozen engine on the stored closed bars. For each trade it compares expected and actual:
- entry fill;
- stop or target fill;
- quantity;
- fees;
- funding;
- slippage.

The tolerances are the OOS-2 contract's, unchanged:
- fills within 0.05% of expected;
- quantity exact;
- fees within $1e-6;
- funding within $1e-9 where reported;
- times and order prices exact.

**What leaves the process:** pass/fail by category, exception type, aggregate *entry* deviation statistics, and the unresolved-exception count. Exit checks report only "within tolerance: N of M" and a category on failure.

- **Why exit deviation magnitudes stay sealed even though they're equal today:** the simulated broker applies the same 0.02% adverse slippage to stop and target exits, so they carry no information now. They are sealed as defence in depth. Under a different broker (stop-market vs limit target), the mean exit deviation would approximate the stop share.
- **The frozen `reconcile` CLI must not be run during the window.** It writes `reconciliation.json`, with full per-trade engine and paper values, into the OOS-2 store. The view's in-memory reconciliation replaces it for operations.

**Escalation:**
1. **First breach.** The report flags any failed category and asks for owner review by category. The view writes nothing to OOS-2, and the frozen runner's own exceptions still block acceptance.
2. **Repeated breaches** (2 or more of one category in 7 days): escalate to the OOS owner.
3. **A breach that can't be diagnosed blind:** break-glass only (section 6).

## 6. Access control

| Role | May see | Must not see |
|---|---|---|
| OPERATOR (people and sessions running operations: me, the LCSC session, the user) | the view's report (section 4) | every sealed field; raw OOS-2 files other than through the view |
| OOS_OWNER | the operator view. Sealed records **only** through the formal `oos-evaluate` path on or after 2027-10-16, which replays the engine from stored candles and settlements. | sealed fields before the formal read, by any route |
| Everyone | — | sealed fields before 2027-10-16 |

**How it is enforced:**
- **Technical.** The view's allow-list parser cannot output sealed fields, and every run is appended to the view's `access_log.jsonl` (who, when, which sources were opened).
- **Procedural.** The OOS-2 store is plain files readable by the user's account, which the runner itself needs. No role check can therefore *prevent* a direct read; **the seal is procedural plus logged, not cryptographic.** The rules:
  - operators use only the view;
  - nobody opens `trades.jsonl`, `signals.jsonl`, the `slots` in `state.json` or heartbeats, or the exit rows in `events.jsonl`;
  - nobody runs `status` fields `open_position` or `current_state`, or the frozen `reconcile` command.
- **Detection (optional, needs elevation and approval).** A Windows object-access audit (SACL) on `trades.jsonl` and `signals.jsonl` logs every read in the Security event log. It cannot cover `state.json`, `heartbeats.jsonl` or `events.jsonl` selectively, because the runner and operations read those too.
- **Break-glass.** Opening sealed detail before 2027-10-16 needs the user's written approval and a logged reason. It is disclosed with the OOS-2 result and must never lead to a rule change.

**Exposure to date, disclosed:** operational checks on 2026-10-15 and 16 used `status` and the store layout. That showed the book was flat at each check, and that `trades.jsonl` did not exist (no closed trade yet) at 02:58 UTC on 2026-10-16. That is position state and a trade count, not an outcome. From adoption of this design, those fields are sealed for operators.

**Disclosed operational-state exposure: LCSC session reads (added 2026-10-16).** The LCSC session (cryptoscreener-48) disclosed the reads below. They were not reread or reconstructed for this record. Both were read-only.

| UTC (2026-10-16) | Purpose | Printed in full | Parsed only |
|---|---|---|---|
| about 02:57–03:00 | LCSC's operational health check of the shared machine and the OOS-2 runner | `manifest.json`, `state.json`, `runner.lock`, the backfill watermark file, `events.jsonl`, `alerts.jsonl`, `exceptions.jsonl` | every heartbeat line, including `slots` |
| 05:31 | evidence snapshot for LCSC's deferred permission-change request (`candle12-evidence`) | none disclosed | `state.json` and the last heartbeat |

- **Reported contents at the time:**
  - **Flat position.** The heartbeat `slots` were empty, per the LCSC disclosure.
  - **Zero closed trades.** `trades.jsonl` did not exist at 02:58, per this session's own check at that time (the paragraph above).
  - **Startup and drill events only.** `events.jsonl` held only the 7 startup and restart-drill events, per this session's earlier account.
  - **Nothing further disclosed.** The disclosure reported no contents for the 05:31 read beyond those fields.
- **Not read:** the LCSC session states it has never read `trades.jsonl` or `signals.jsonl`.
- **Two separate findings:**
  - **Outcomes:** no closed-trade outcome is reported to have existed or been seen at either read.
  - **Read restriction:** both reads predate the allow-list in section 2, and both went beyond it. Full `state.json`, `slots`, and `events.jsonl` and `alerts.jsonl` in full are all outside it.
  - What the reads exposed is information about the forward window: position state and a trade count. It is disclosed here, not dismissed.
- **Allow-list in effect:** from 13:21 UTC on 2026-10-16, the LCSC checkpoint script reads only `at` and `halted` from the last 4 KB of `heartbeats.jsonl`.
- **Dormant tooling stays prohibited:** `option_b.resource_health` and `candle12_acl.evidence` still parse more than the allow-list. They must not run against OOS-2 until their reads are narrowed to the allow-list and the change is separately approved. The permission-change operation itself remains DEFERRED_PENDING_REVIEW_AND_CAPACITY.
- **Status:** this entry neither invalidates OOS-2 nor exempts it from review, and it doesn't make OOS-2 fully blind. It preserves the facts for assessment under the frozen policy at the formal OOS-2 read.

## 7. Resource budget

- On-demand only.
- Under 5 s of CPU, under 150 MB peak memory, under 30 s wall-clock.
- BelowNormal priority.
- Read-only file I/O of a few MB per year of records; database reads are timestamp-only.
- No network, apart from the local database.
- It refuses to run when commit headroom is under 1.5 GB or the runner's heartbeat is over 7 minutes old.

## 8. Rollback

- Delete `backend/scripts/candle12_v3_operational_view/` and `backend/data/candle12_v3_paper/operational_view/`.
- Nothing in OOS-2 was written, started or changed, so there's nothing to undo there.
- No scheduled task exists to remove.

## 9. Test plan

All tests use synthetic stores, or seen history before 2026-10-16. None runs on OOS bars.

| # | Test | Pass condition |
|---|---|---|
| 1 | Exit reason sealed | no output, log or report contains a STOP or TARGET distinction |
| 2 | Exit price sealed | no exit price or exit-slippage sign in any output |
| 3 | Per-trade funding sealed | no funding amount; coverage only yes/no plus a count |
| 4 | PnL, R, equity and fee totals sealed | forbidden-key and forbidden-value scanner finds none |
| 5 | Stop-vs-target counts sealed | **identity test:** an all-stop book and an all-target book with the same entries give byte-identical reports |
| 6 | Holding time and position timeline sealed | two books differing only in holding times give byte-identical reports |
| 7 | Operational fields available | every metric in section 4 is present, with the expected values on a known synthetic store |
| 8 | Reconciliation visible without outcome | injected entry, exit, qty, fee and funding mismatches give exactly the expected failed categories; still byte-identical across the all-stop and all-target variants |
| 9 | Never writes to OOS-2 | the OOS-2 store's file hashes and mtimes are unchanged; a concurrent-writer stress test shows the runner's atomic replace is never blocked; `runner.lock` is untouched |
| 10 | Never starts a collector | there's no network or broker client, and no new process persists after exit |
| 11 | Never opens forbidden files | opening `trades.jsonl` or the other never-opened files raises; the attempt is recorded in `access_log.jsonl` |
| 12 | Resource budget | a year-sized synthetic store (about 9,000 bars, 300 trades) stays within section 7; the refusal checks trigger |
| 13 | Partial last line | a torn final line is skipped, not an error |
| 14 | Freeze integrity | guard OK and the contract paths clean before and after |

## 10. Statement

CANDLE12_V3_OPERATIONAL_VIEW reports execution and operational health only. **It does not validate strategy performance and never counts toward OOS.** All performance fields stay sealed until the formal OOS-2 read on or after 2027-10-16. OOS-2's contract, thresholds, dates, fingerprinted files, runner and store are unchanged by this design.
