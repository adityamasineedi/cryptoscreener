# CANDLE12 V3 staged validation: design and freeze plan (DRAFT, not frozen)

**Status:**
- DRAFT_FOR_FREEZE. Nothing in this document is in force yet.
- Every BTC forward-performance field stays sealed **until the staged contract is frozen** (section 8, step 3).
- Before the freeze, the user must settle the open points in section 9.

**Statement:** interim reads reduce statistical purity. They are predeclared and non-tuning. Section 6 shows what they cost.

## 1. What changes, and what doesn't

| Item | Change |
|---|---|
| Strategy, entry, stop, target, sizing, slippage, fees, risk tolerance | **Unchanged.** The same frozen code and config: config fingerprint `cd64cf85…`, code fingerprint `8cfb8a18…` over 37 files, lock `34eb8235…`. |
| Runner, store, data feed | **Unchanged.** The existing OOS-2 runner and its records only. No second runner, no second feed or collector, no restart. |
| Forward window start | Unchanged: 2026-10-16T00:00Z |
| Decision protocol | **Replaced.** The one-year blind read is replaced by three predeclared checkpoints with kill rules (section 3). |
| Visibility | After the freeze, a read-only BTC dashboard shows full performance (section 5). |
| OOS-2 contract doc and lock (fingerprinted) | **Not edited.** The staged protocol is a new contract version with its own doc and lock. It records OOS-2's decision protocol as SUPERSEDED_BY_STAGED. |

**Why the runner is not affected:**
- Its guard fingerprints only the 41 contract paths: the 37 files plus the OOS-2 archive and lock files.
- The new staged doc, lock, evaluator and dashboard all sit outside those paths. Freezing them causes no drift and no halt.

**About the frozen evaluator:**
- The frozen `oos-evaluate` command stays in the code and still refuses to run before 2027-10-16.
- It is no longer the decision rule.
- Its classification can still be computed on or after 2027-10-16 for the record. It would then be labelled NOT_BLIND, because of the interim reads.

## 2. Staged contract (to be frozen)

| Item | Value |
|---|---|
| Contract id | `CANDLE12_V3_EXEC_FIX/STAGED-1` |
| Supersedes | The decision protocol of `CANDLE12_V3_EXEC_FIX/OOS-2`. OOS-2's strategy, records and fingerprints carry over unchanged. |
| Book | The OOS-2 store `virtual_paper_mainnet/oos2`: BTCUSDT 1h LONG, virtual paper |
| Trades counted | Every trade closed in the OOS-2 store since 2026-10-16T00:00Z |
| R | net PnL / $20 intended risk (`r_net_intended_risk`) |
| Fully costed | The runner's recorded fees (0.04% taker each side), the 0.02% modelled slippage, and recorded funding. A 0.05% stress replay of the same trades is **reported but not gating**, unless the user makes it gating before the freeze (open point 2). |
| Expectancy | Mean fully costed R over all trades closed at the evaluation time |
| Profit factor | Sum of winning net R / \|sum of losing net R\|. With no losing trades, PF = ∞ (passes). |
| Max drawdown | Peak-to-trough of cumulative realized net R since the window start, including the start value 0 |
| Unresolved exception | Any exception in the OOS-2 store whose status is not RESOLVED at the evaluation time |
| Lock contents | Hashes of this doc, the evaluator, the regime classifier and the dashboard code; the OOS-2 lock hash and fingerprints; freeze commit and tag; disclosed exposure to date |

## 3. Checkpoints and kill rules

**When a checkpoint is evaluated:**
- at the first daily evaluation (00:05 UTC) at which **both** its calendar condition and its trade-count condition hold;
- on all trades closed by then;
- an open position at that time is excluded, since it is unrealized.

Expected trade-count timing below uses the in-sample rate of about 22 trades a year.

| Checkpoint | Earliest | Rules (all must hold) | Pass | Fail |
|---|---|---|---|---|
| **CP1** | ≥ 2027-01-16 **and** ≥ 10 closed trades (≈ 5–6 months at the in-sample rate) | expectancy > −0.10R; max DD ≤ 8R; no unresolved exceptions | continue | **KILL** |
| **CP2** | ≥ 2027-04-16 **and** ≥ 20 closed trades (≈ 11 months) | expectancy > 0R; PF ≥ 1.0; max DD ≤ 10R; no unresolved exceptions | continue | **KILL** |
| **CP3** | ≥ 2027-10-16 **and** ≥ 30 closed trades (≈ 16 months) | expectancy > 0.10R; PF ≥ 1.20; max DD ≤ 15R; positive in ≥ 2 trend regimes (section 3.1); no unresolved exceptions | eligible for small live allocation, subject to a separate live-risk approval | **KILL** |

**Proposed for the freeze (open points 3 and 4):**
- **Exceptions at evaluation:** if exceptions are unresolved at a checkpoint, the evaluation is deferred by up to 14 days so they can be resolved with notes. If any is still unresolved after that, the result is FAIL. A deferral is never a pass.
- **Stall:** if CP3 can't be reached by 2028-10-16, the result is INSUFFICIENT_SAMPLE. The book stops, with no live eligibility.

**KILL procedure:**
1. Write the checkpoint record.
2. Engage the BTC kill switch. It blocks new entries; an open position runs to its own exit.
3. Once flat, the operator stops the runner (`control.ps1 stop`) and disables its tasks.
4. Any live allocation is closed.
5. There is no refit, no conditional pass and no re-run. Any successor is a new strategy version with a new window.

**Checkpoint record:** `staged/checkpoints.jsonl` holds the metrics, the rule results, the verdict, the input file hashes and the evaluator hash. It is append-only and outside the OOS-2 store.

### 3.1 Trend-regime classifier (proposed, frozen with the contract; open point 5)

- **Inputs:** each trade's regime is fixed at its entry time t, using BTCUSDT daily closes (UTC days built from 1h bars) up to the last completed day before t:
  - SMA200 = the 200-day simple moving average of those closes;
  - slope = SMA200 minus its value 20 days earlier.
- **Regimes:**
  - **UP:** close > SMA200 and slope > 0;
  - **DOWN:** close < SMA200 and slope < 0;
  - **RANGE:** otherwise.
- **"Positive in a regime":** net R > 0 over at least **5** trades entered in that regime.
- **The rule:** at least two regimes must be positive.
- **Caveat:** a LONG-only book in a one-regime year (for example a sustained bull market) fails this rule mechanically. That is intended under the user's rule. The user may prefer a different wording before the freeze.

## 4. Capital staging

**The current system cannot trade live:**
- the frozen package has only `virtual_paper_mainnet` and `testnet_broker_check`;
- no key with trade permission exists;
- the safety design forbids mainnet credentials.

**Before any live stage is possible:**
- a separate live-execution design;
- a safety review;
- a minimum-size mainnet broker check;
- the user's explicit live-risk approval.

Each stage below is **a condition for considering live capital, not an authorization**.

| Stage | Starts when | Risk per trade (fixed at the freeze; open point 6) |
|---|---|---|
| 1: paper only | now, until CP1 passes | — |
| 2: tiny live | CP1 passes **and** live approval is granted | proposed 0.10% of live equity, from the user's 0.1–0.25% range |
| 3: small live | CP2 passes **and** live approval is confirmed | proposed 0.25% of live equity, from the user's 0.25–0.5% range |
| Normal risk | CP3 passes **and** a separate live-risk approval is granted | not set here |

**Rules for any live stage:**
- **Checkpoints use the paper book only.** The paper book stays the reference.
- **Live results are reported separately.** They are never mixed into checkpoint metrics.
- **Divergence halts live.** If a live fill differs from paper beyond the 0.05% tolerance, live stops pending review.
- **A KILL closes live too.**
- **Timing:** stages follow checkpoint passes, not calendar months. CP1 can't happen before 2027-01-16, and at the in-sample rate it comes around month 5–6.

## 5. BTC staged-validation dashboard

**Before the freeze:** only the operational fields are shown, as in the operational-view design.

**After the freeze:** full performance is visible. Building it on OOS-2's own records replaces the Track 2 operational-only design (`7ccdc49`); this design supersedes it.

| Panel | Fields |
|---|---|
| Open position | entry time and fill, stop, target, quantity, mark price, unrealized PnL ($ and R), funding accrued |
| Trade history | per trade: entry, stop, target, fills, exit time, price and reason, quantity, fees, slippage, funding, net, R |
| Summary | realized PnL, win/loss count, expectancy, PF, max DD (R), regime split |
| Equity curve | realized R from the window start; drawdown shaded; checkpoint markers |
| Costs | fees, slippage, funding: totals and per trade |
| Orders | requested / accepted / rejected / canceled / filled, with times |
| Reconciliation | per-trade, per-check status (section 7) |
| Health | latest bar, heartbeat age, exceptions, alerts, fingerprint, store integrity |
| **Checkpoint status** | current stage; each checkpoint's verdict and record hash |
| **Next checkpoint** | calendar and trade-count progress (for example 7/10 trades, 2027-01-16); every rule's current value against its threshold, marked "informational until evaluation" |

**How it runs:**
- **Code:** `backend/scripts/candle12_v3_staged/` (outside the fingerprinted files). It writes only to `backend/data/candle12_v3_paper/staged/`.
- **Reads:** the OOS-2 store, read-only. It never takes `runner.lock`, never writes to the store, and never runs the frozen `reconcile` command, which would write `reconciliation.json` into the store.
- **Safe reading:** `state.json` is read with a short open plus retry, using delete-share access so the runner's atomic replace can never be blocked (test 9).
- **Schedule:**
  - on-demand, plus an optional bounded task at HH:10 UTC (outside bar close and HH:15–HH:35);
  - a daily checkpoint evaluation at 00:05 UTC.
- **Budget:** BelowNormal priority; < 5 s CPU, < 150 MB, < 30 s wall (hard kill at 60 s). It refuses when commit headroom is under 1.5 GB.
- **Live mark price:** optional browser polling of Binance public mark price.

## 6. What interim reads cost (approximate; to be confirmed by simulation before the freeze)

**Model:** the binomial approximation for a −1R / +2R payoff, with costs ignored.
- Zero edge corresponds to a 1/3 win rate.
- The in-sample edge (about +0.37R) corresponds to a win rate of about 0.46.

| Case | P(pass CP1) | P(pass CP1 and CP2) | P(pass all three) |
|---|---|---|---|
| Zero edge (win rate 1/3) | 44% (needs ≥ 4 wins of 10) | 36% | 20% |
| Negative edge, −0.3R (win rate ≈ 0.23) | 19% | — | — |
| In-sample edge (win rate ≈ 0.46) | 75% | 72% | 65% |

These are joint probabilities along the nested checkpoints. Wins are counted cumulatively: ≥ 4 of 10, then ≥ 7 of 20, then ≥ 12 of 30. Drawdown and regime rules would lower them further.

**What this means:**
- **Stage 2 can open on a near-zero signal.** A zero-edge strategy would reach Stage 2 (tiny live) 44% of the time and pass all three checkpoints 20% of the time. Releasing live capital after 10 trades with expectancy > −0.10R therefore rests on a very weak signal.
- **A real edge can still be killed.** A strategy with the full in-sample edge would be killed somewhere in the three looks about 35% of the time.

**Compared with the original rule:** OOS-2's single read needed ≥ 30 trades, PF ≥ 1.30 and net > 0 at both slippages. That gives fewer false passes, at the cost of a later answer.

**Pre-freeze step (open point 7):** a synthetic Monte Carlo of exactly these rules (costs, DD and regime rules included). It reads no market data, and its operating-characteristic table goes into the lock.

## 7. Reconciliation

- **When:** daily, plus one run at each checkpoint.
- **How:** in memory, by replaying the frozen engine on the stored closed bars (the same function as the frozen `reconcile`, called read-only).
- **Tolerances** (as in OOS-2 and Track 1):
  - entry, stop and target fill within 0.05%, unless a documented gap event;
  - quantity exact after the 0.001 step;
  - fees within $1e-6;
  - funding within $1e-9 of the exchange-reported rate × notional;
  - slippage equals the configured 0.02%;
  - exit reason equal; net and R recomputed;
  - order prices and candle data exact.
- **Output:** written to `staged/reconciliation/<UTC>.json`, never into the OOS-2 store.
- **Mismatch:** a mismatch beyond tolerance is recorded as a staged exception. It counts as unresolved at a checkpoint until it is resolved with a note.
- **Missing funding:** a funding gap (`MISSING_FUNDING_DATA`) counts the same way.

## 8. Migration plan from OOS-2 to staged validation

| Step | Action | Performance visible? |
|---|---|---|
| 0 | The user settles the open points (section 9). | No |
| 1 | Implement the evaluator, classifier, reconciliation and dashboard on **synthetic stores only**, with tests (section 10). Run the operating-characteristic simulation. | No |
| 2 | Write the contract doc `docs/candle12_v3_staged_validation_contract.md`. Record the disclosed exposure to date: flat position, zero closed trades and startup/drill events, as disclosed. | No |
| 3 | **Freeze:** commit and tag `candle12-v3-staged-1-freeze-<date>`; write `docs/candle12_v3_staged_validation.lock.json`; then verify that the runner's guard is still OK and the runner did not restart. | No, until the tag exists |
| 4 | Enable the dashboard and the daily evaluation. Record the first view in `staged/access_log.jsonl`. | Yes, from here |
| 5 | Record OOS-2's decision protocol as SUPERSEDED_BY_STAGED in the staged lock and the launch log. The OOS-2 files are untouched. | — |

**Rollback before step 3:** delete the new scripts and docs. Nothing in OOS-2 changes.

**After step 3:** the staged protocol can't be loosened. A change means STAGED-2 with a new window, and the existing trades stay seen.

**ETH book (Track 1):** unchanged. It stays a separate correlated research hypothesis. It is not CANDLE12 BTC validation, and its results never change BTC rules.

## 9. Open points for the user before the freeze

1. Confirm that continuous visibility is intended. A live dashboard means unlimited informal looks between checkpoints. The kill and continue rules still bind, and decisions are taken only at checkpoints. A discretionary stop between checkpoints would be logged as a protocol deviation.
2. Should the 0.05% stress be a gating rule (as in OOS-2) or reported only?
3. Exception deferral of 14 days, then FAIL: accept?
4. Stall rule (INSUFFICIENT_SAMPLE at 2028-10-16): accept?
5. Regime classifier and its "≥ 5 trades per regime" requirement: accept or reword?
6. Fixed live risk per stage: 0.10% and 0.25%, or other values within the user's ranges?
7. Approve the synthetic operating-characteristic simulation. It is small and reads no market data.

## 10. Test plan

| # | Test | Pass condition |
|---|---|---|
| 1 | Freeze order | No performance field can be produced before the staged lock exists and verifies. The dashboard refuses and shows operational fields only. |
| 2 | Runner unaffected | Before and after the freeze: guard OK, contract paths clean, runner PIDs unchanged |
| 3 | Read-only | OOS-2 store hashes and mtimes are unchanged by the dashboard and evaluator; `runner.lock` is untouched; a concurrent atomic-replace stress test never fails |
| 4 | Checkpoint timing | Synthetic books: evaluation fires only when both conditions hold; it excludes the open position |
| 5 | Metric definitions | Expectancy, PF (including the no-loss case), max DD (including the start value) and regime split match hand-computed values |
| 6 | Kill rules | A synthetic fail at each checkpoint writes a KILL record and engages the kill switch; there is no re-evaluation path |
| 7 | Exceptions | Unresolved at evaluation → deferred; still unresolved after 14 days → FAIL |
| 8 | Regime classifier | Uses only completed days before entry (a lookahead test with a planted future bar); classifies known synthetic series correctly |
| 9 | Reconciliation | Injected mismatches produce the right staged exceptions; nothing is written into the OOS-2 store |
| 10 | Anti-tuning | The evaluator rejects any config or threshold differing from the lock (hash check) |
| 11 | Dashboard | Every field in section 5 is present on a synthetic store; the checkpoint panels show current value vs threshold |
| 12 | Budget | Overrun, RSS cap and low-headroom refusal behave as specified |
| 13 | No live path | No trade-permission key is loaded; no order endpoint is reachable from these scripts |
