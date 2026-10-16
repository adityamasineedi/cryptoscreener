# CANDLE12 fast validation plan: four paper books (design only)

**Status:**
- DESIGN_ONLY. Nothing has been run, implemented or started.
- Nothing here enables live trading.
- Freezing the plan, running the historical studies and starting any book each need the user's approval.

**Limitations, stated first:**
- **One evidence pool, not four tests.** The three visible books (ETH 1h, SOL 1h, BTC 4h) run the same CANDLE12 idea on highly correlated crypto prices, so they form **one crypto-family evidence pool**. They are not independent validations of anything, and none of them validates CANDLE12 BTC 1h.
- **"Fast" is limited by trade frequency, not by design.**
  - At the in-sample rates (BTC 1h about 22 trades a year, ETH 1h about 24), the three visible books would close only about **4–5 trades by day 30** and **9–10 by day 60**.
  - The 25-trade gate is therefore reached around **month 5–6**, not day 60.
  - SOL 1h and BTC 4h rates are measured before the freeze (section 6). BTC 4h is expected to be much slower.
- **The 60-day gate is weak evidence** (section 4.3). Under a simple approximation, a zero-edge strategy reaches "eligible for tiny live" about **32%** of the time.

## 1. How this plan relates to earlier designs: user decision needed

| Earlier design | Effect of this plan |
|---|---|
| STAGED-1 draft (`73ddced`): BTC OOS-2 becomes visible, with 3/6/12-month checkpoints | **Conflicts.** This plan keeps BTC OOS-2 **sealed**. The two can't both be adopted. **Recommendation:** adopt this plan and withdraw STAGED-1. That keeps one clean blind test, OOS-2, while the visible books give early, correlated, non-validating evidence. If the user prefers STAGED-1, book 1 below becomes visible under STAGED-1's rules, and this plan governs only books 2–4. |
| ETH visible paper (`08beb91`) | Kept for architecture, isolation, reconciliation and dashboard. Its acceptance rule (≥ 30 trades and 12 months) is **replaced** by the gates in section 4. Its 2R daily halt and 10R drawdown pause are kept for all three visible books (open point 3). |
| BTC operational-only (`7ccdc49`) | Kept as the operational view of sealed book 1 |
| Historical robustness (`ce71ad3`) | Kept for BTC 1h, and extended in section 6 to all four configurations |

## 2. Four-book architecture

| # | Book | Visibility | Runner | Store |
|---|---|---|---|---|
| 1 | `BTCUSDT_1H_OOS2` | **Sealed.** Operational view only (`7ccdc49`). | Existing OOS-2 runner, unchanged | Existing OOS-2 store |
| 2 | `ETHUSDT_1H_VISIBLE_PAPER` | Full | Bounded task, HH:01:00 UTC | own |
| 3 | `SOLUSDT_1H_VISIBLE_PAPER` | Full | Bounded task, HH:02:15 UTC | own |
| 4 | `BTCUSDT_4H_VISIBLE_PAPER` | Full | Bounded task, HH:03:30 UTC, only at 4h closes (00, 04, 08, 12, 16, 20 UTC) | own |

**How books 2–4 run:**
- **Code:** one worktree, `D:/cryptoscreener-worktrees/candle12-visible-books`, on a branch from tag `candle12-v3-oos-2-freeze-2026-10-15`. New code only in `backend/scripts/candle12_visible_books/`.
  - It imports the frozen `PaperAdapter`, `PaperStore`, the simulated broker, `SafetyLimits`, the Binance sources and `reconcile` unchanged. It never imports the BTC CLI or the testnet broker.
  - On every run, the imported frozen files must match the OOS-2 lock hashes. On a mismatch, that book halts; OOS-2 is unaffected.
- **Per book:** its own contract, lock, store, run lock, kill switch, dashboard and scheduled task. A shared engine import is code reuse, not a shared runner.
- **Serial by construction:**
  - the start times are staggered;
  - a machine-wide slot lock (`visible_books.slot`) lets only one book run at a time;
  - each run has a hard 60 s cap, so all three finish before HH:05. The frozen adapter misses an entry placed more than 300 s after the bar close.
- **Data:** Binance public REST only (GET klines, fundingRate, premiumIndex, time). No keys, no database, no LCSC files.
- **Calibration gate:** if any book's p95 runtime exceeds 60 s in a 48-hour dry run, the schedule doesn't fit. Stop and redesign.

## 3. Contracts and frozen configurations

Each visible book is its own hypothesis: "CANDLE12 V3 as frozen, on this symbol and timeframe, has positive fully costed expectancy on forward bars."

**Shared rules:** the OOS-2 contract config, with only the fields below changed:
- `inclusive` entry: fill = Candle-1 high × 1.0002, once;
- structural stop with a 0.2 × ATR buffer;
- `nearest_target_full_exit`;
- 0.02% exit slippage, 0.04% taker fee, funding on;
- stop first on a same-bar touch;
- per-side lock, `once_per_level`, LONG only;
- 2× leverage, `risk_tolerance_pct` 0.10, size rounding tolerance 0.10.

| Field | ETH 1h | SOL 1h | BTC 4h |
|---|---|---|---|
| Contract id | `CANDLE12_IDEA_ETHUSDT_1H_VISIBLE_PAPER/V1` | `CANDLE12_IDEA_SOLUSDT_1H_VISIBLE_PAPER/V1` | `CANDLE12_IDEA_BTCUSDT_4H_VISIBLE_PAPER/V1` |
| `risk_usd` / equity | $10 / $1,000 | $10 / $1,000 | $10 / $1,000 |
| Exchange filters | frozen table (step 0.001, min $20) | **Not in the frozen table.** Set in the config (`qty_step`, `min_qty`, `min_notional`) from `GET /fapi/v1/exchangeInfo` at the freeze, recorded with its time. | frozen table (step 0.001, min $50) |
| Bars | `structure_lookback_bars` 300 = 12.5 days | 300 = 12.5 days | 300 bars = **50 days**. Same bar counts, so a different time scale. The 1,000 warm-up bars are about 167 days. |
| Forward start | first 00:00 UTC after the plan freezes; all three books start together | same | same |

**Sizing problem at $10 (open point 1).** The frozen rule rejects a trade when rounding down to the exchange step cuts the quantity by more than 10%:
- **Below 10 steps:** this rejects about 60% of signals when the raw quantity is 2–6 steps, and about 28% at 4–10 steps.
- **BTC 4h:** at $10 risk and step 0.001 BTC, any stop wider than $1,000 per BTC gives fewer than 10 steps. 4h stops are wide, so many BTC 4h signals would be rejected.
- **SOL:** if SOL's step is 1 coin (confirmed at the freeze), most SOL signals would be rejected too.

**Proposed rule, fixed before the freeze and measured on history only:**
- If a book's historical size-rejection rate exceeds 10% of filled signals, its `risk_usd` is raised to the smallest of $20, $50 or $100 that brings it under 10%.
- Otherwise the book is dropped.

This is a pre-freeze feasibility choice on history, not tuning on forward results.

## 4. Gates (frozen before any book starts)

**Definitions:**
- **R:** net / the book's `risk_usd`.
- **Fully costed:** recorded fees, 0.02% slippage and recorded funding.
- **Pooled expectancy:** the mean R over all closed trades of the **active** visible books, each trade weighted equally.
- **Drawdown:** per book, peak-to-trough of realized cumulative R.
- **Exceptions:** reconciliation or execution exceptions not RESOLVED at evaluation time.
- **When a gate is evaluated:** at the first daily evaluation (00:05 UTC) at which **both** its day count and its trade count hold.
  - "30-day" therefore means ≥ 30 days **and** ≥ 10 pooled closed trades.
  - "60-day" means ≥ 60 days **and** ≥ 25 pooled trades.
  - The user's per-book alternative (10 trades in each book) is out of reach at these trade rates (open point 2).

### 4.1 Gate A ("30-day")

| Rule | Book result |
|---|---|
| Pooled expectancy > 0R | If it fails, pause every active book whose own expectancy is ≤ 0 |
| A book's drawdown > 8R | Pause that book |
| Unresolved exceptions in a book | Pause that book |
| Otherwise | Continue paper |

### 4.2 Gate B ("60-day")

| Rule | |
|---|---|
| Pooled trades ≥ 25 (as part of the trigger) | |
| Pooled expectancy > 0.05R | |
| At least 2 of the 3 visible books have positive own expectancy. A paused book counts as not positive. | |
| No book drawdown > 10R | |
| No unresolved exceptions | |

- **Pass:** eligible for tiny live, subject to a separate live-risk approval (section 5).
- **Fail:**
  - a book with expectancy ≤ 0 or drawdown > 10R is **killed**;
  - the other books continue paper;
  - there is no re-run of the gate.

**Pause and kill (proposed; open point 4):**
- A **paused** book engages its own kill switch, which blocks new entries; an open position runs to its exit.
- A paused book stays out of later pooled gates. Resuming it means a new version with a new window.
- A **killed** book stops and its task is disabled.
- None of this ever affects OOS-2.

### 4.3 What the gates cost (approximate)

**Model:** the binomial approximation for a −1R / +2R payoff, books treated as independent, costs, drawdown and the 2-of-3 rule ignored. Correlation between the books makes the effective sample smaller than the trade count, so real pass rates for a weak strategy are, if anything, *more* variable.

| True edge | P(pass Gate A, 10 trades) | P(pass Gate A, then B at 25) |
|---|---|---|
| Zero (win rate 1/3) | 44% | **32%** |
| −0.3R (win rate ≈ 0.23) | 19% | 7% |
| In-sample-like +0.37R (win rate ≈ 0.46) | 75% | 71% |

A synthetic simulation of the exact rules, including drawdown, 2-of-3 and correlation, goes into the freeze record (section 6, item 6).

## 5. Live staging (conditions only; nothing is enabled)

**Status:** the current system has **no live mode** and no key with trade permission. Any live step needs a separate live-execution design, a safety review, a minimum-size mainnet broker check and the user's explicit live-risk approval.

**Rules for any approved live step:**

| Rule | Value |
|---|---|
| Earliest | After Gate B passes. Never before. |
| Which book | Only a book that passed Gate B **individually**: its own expectancy > 0.05R and its own drawdown ≤ 10R. The choice is fixed by this rule, not by the user picking the best (open point 5). |
| Risk per trade | 0.1% of live equity |
| Positions | One live position at a time, across all books |
| Daily loss limit | 1R: stop new live entries until the user approves a restart |
| Cumulative drawdown limit | 6R: stop live, which needs a new approval |
| Execution mismatch | Any live fill outside 0.05% of the paper fill, quantity mismatch or unknown order state pauses live |
| Reference | The paper books stay the reference. Live results are reported separately and never mixed into gates. |

## 6. Historical robustness (before any book starts; data through 2026-10-15 23:00 UTC)

**Scope:** each of the four configurations (BTC 1h frozen, ETH 1h, SOL 1h, BTC 4h). All of this history has been used before, so these studies can falsify but **cannot validate**.

1. **Rolling walk-forward without refit.**
   - The frozen configuration is evaluated on rolling 6-month test windows, stepped by 3 months, from 2023-01 to 2026-10.
   - There is **no training or re-optimisation step**, because nothing is fitted. A re-fit would turn this into a selection procedure on used data.
   - Per window: trades, expectancy, PF, drawdown.
   - **Rule:** STABLE if ≥ 60% of windows have expectancy > 0 and no window has drawdown > 10R.
2. **Monte Carlo.**
   - Permutation of trade order (path and drawdown only; total R doesn't change) and iid bootstrap, 10,000 each, seed 20261016.
   - Per book and pooled: median, 5th and 95th percentiles; P(DD > 6R / 8R / 10R / 15R).
   - Bootstrap pass rates of Gates A and B on each book's own history.
3. **Random-entry baseline.**
   - As in `ce71ad3` §5: structural framework and geometry-matched variants, 1,000 seeds each.
   - Matched on side, one position, the book's own sizing and costs, and trade count.
   - Selection-bias caveat included.
4. **Sizing feasibility:** the size-rejection rate per book at $10 (section 3).
5. **Correlation and leakage:**
   - entry overlap and outcome concordance of ETH 1h, SOL 1h and BTC 4h trades with BTC 1h in-sample trades;
   - **BTC 4h shares BTC's own price**, so its overlap with sealed OOS-2 is expected to be the highest. The user accepts the residual knowingly before start.
6. **Gate operating characteristics:** a synthetic simulation of Gates A and B under zero, negative and in-sample-like edge, with correlated books. It reads no market data.

**Data:** BTC and ETH 1h come from the read-only database or Binance REST. SOL 1h and BTC 4h come from Binance REST, cached in the study worktree.

**Outcome:** a book that fails studies 1 or 3 is reported as such. The user decides before start whether to drop it. No parameter is changed.

## 7. Dashboards

- **Books 2–4:** one static HTML dashboard per book, plus a pooled page.
  - **Per book:** the fields of `08beb91` §4: open position, entry, stop, target, fills, unrealized and realized PnL, R, trade history, win/loss, equity curve, fees, slippage, funding, order states, reconciliation.
  - **Pooled page:** pooled trades and expectancy, each book's status (active, paused, killed), gate status, and next-gate progress (days and pooled trades) against thresholds.
  - **Banner:** "Correlated crypto-family research book. Not CANDLE12 BTC OOS validation."
- **Book 1:** the operational view only (`7ccdc49`). There is no BTC 1h performance on any page.

## 8. Resource budget

| Item | Budget |
|---|---|
| Visible-book runs | 3 tasks, staggered HH:01:00 / HH:02:15 / HH:03:30, serial via the slot lock. Each: BelowNormal, one-core affinity, ≤ 60 s wall (hard kill at 90 s), RSS ≤ 400 MB, refuses when commit headroom < 1.5 GB (records `RUN_SKIPPED_RESOURCES`; any entry is missed, never back-filled). |
| Daily gate evaluator | 00:05 UTC, < 10 s CPU, < 150 MB |
| Historical studies | Run once, serially, in a separate worktree, as `ce71ad3` §6. About 8 engine walks (4 configs × 2 slippages) plus 25 for the BTC neighbourhood. Hard 3-hour total cap, 1 GB RSS cap. Blackouts HH:15–HH:35 and HH:58–HH:05. |
| Never | Resident processes, process pools, database writes, LCSC access |

## 9. Test plan

Tests from `08beb91` §10 (per book) and `ce71ad3` §7 (studies), plus:

| # | Test | Pass condition |
|---|---|---|
| 1 | Per-book isolation | Each book's path guard rejects the other books' stores, OOS-2 and LCSC; its kill switch is its own and ≠ BTC `DEFAULT_KILL_SWITCH` |
| 2 | Serial slot | Two books started together never overlap; the second waits or skips within its cap |
| 3 | 4h schedule | The BTC 4h task processes only 4h closes; it places its entry within 300 s of the close |
| 4 | SOL filters | The config filters equal the recorded exchangeInfo snapshot; rounding and min-notional rejection behave as configured |
| 5 | Gate triggers | Both day and trade conditions are required; pooled metrics match hand calculations; paused books are excluded |
| 6 | Pause and kill | A synthetic failure pauses or kills the right books; no resume without a new version |
| 7 | Live staging | No order endpoint or trade key is reachable; the live "eligible" flag is informational only |
| 8 | No BTC 1h leakage | No dashboard or pooled page reads or shows OOS-2 performance |
| 9 | Frozen-code check | A hash mismatch halts that book only |
| 10 | Budget | The 60 s cap, RSS cap and headroom refusal work; three runs finish by HH:05 |

## 10. Rollback

- **Before start:** delete the worktree and the docs. Nothing else exists.
- **After start:**
  1. Engage each book's own kill switch.
  2. Let open positions exit.
  3. Disable the three tasks.
  4. Archive the stores.

  OOS-2 and LCSC are never touched, so there is nothing to undo there.
- **Any rule change** makes a new version of that book with a new window. The old records are kept.

## 11. Open points for the user

1. **Sizing at $10:** accept the pre-freeze rule (raise `risk_usd` to $20, $50 or $100, or drop the book)?
2. **Gate triggers:** accept "days **and** pooled trades", in place of the unreachable 10 trades per book?
3. **Halts:** keep the 2R daily halt and 10R drawdown pause from the ETH design on all three books?
4. **Pause semantics:** is a paused book permanently out of the pool (resuming needs a new version)?
5. **Live eligibility:** only individually passing books, chosen by rule?
6. **STAGED-1:** withdraw it (recommended) or adopt it for book 1?
7. **Historical studies:** approve them (section 6), including the BTC 4h leakage measurement.

## 12. Anti-tuning

- All rules above are frozen before any book starts.
- Nothing is changed after seeing paper results.
- A change makes a new version and restarts that book.
- The books are one correlated crypto-family evidence pool, not independent proofs.
- Nothing here changes OOS-2, LCSC, the fingerprinted files or shared modules.
