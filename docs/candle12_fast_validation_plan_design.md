# CANDLE12 fast track: visible paper books for early kill and execution evidence (design only)

**Status:**
- DESIGN_ONLY, revised per the user's decisions of 2026-10-16.
- No paper or live book is implemented or started, and none is authorized.
- **The read-only historical feasibility count (section 3) is authorized.** Its classification rule (section 3.1) was fixed in this document before the count ran. Results: section 3.2.

**This track never authorizes live trading,** for BTC or any other symbol. **STAGED-1 remains the only route to BTC live trading.** OOS-2 remains sealed and untouched.

**Limitations:**
- **One evidence pool.** The visible books (ETH 1h, SOL 1h, BTC 4h) run the same CANDLE12 idea on highly correlated crypto prices. They form one crypto-family evidence pool, not independent validations. None of them validates CANDLE12 BTC 1h.
- **Slow by nature.** At about 22–24 trades a year per 1h book, evidence accumulates slowly.

## 1. Protocol ownership

| Protocol | Scope | Can authorize live? | Status |
|---|---|---|---|
| **OOS-2** (`CANDLE12_V3_EXEC_FIX/OOS-2`) | BTCUSDT 1h formal blind test; the existing runner and store | No | Running, **sealed, untouched** |
| **STAGED-1** (`docs/candle12_v3_staged_validation_design.md`, `73ddced`) | **The only route to BTC live trading** | Only by its own rules, with a separate live-risk approval | Draft, not frozen. While it is unfrozen, OOS-2 stays sealed. |
| **Fast track** (this document) | Visible paper books ETH 1h, SOL 1h, BTC 4h | **Never** | Design only. Feasibility count authorized. |
| Operational view (`7ccdc49`) | Operational fields of OOS-2 | No | Design only |
| Historical robustness (`ce71ad3`) | BTC 1h history up to 2026-10-15 | No | Design only |

**What the fast track can and cannot do:**
- It may pause or kill its own books.
- It cannot authorize live trading, change STAGED-1 or OOS-2, or feed their decisions.

## 2. Architecture: paper trade is the monitoring system

For each book there are exactly three parts:

| Part | What it is |
|---|---|
| **Paper-trade book** | The book's only record and its monitoring system: virtual fills, R, PnL, equity, execution and reconciliation, all in its own store, with its own dashboard (`08beb91` §4). |
| **Pre-start feasibility count** | Read-only, historical, run once before the book can be proposed (section 3). It measures tradability, **not performance or edge**. |
| **Lightweight health check** | Per run, from the book's own store only: last run time, run skipped for resources, latest bar processed, open exceptions, kill-switch state, frozen-code check. Shown on the book's dashboard. |

**Not created:**
- no duplicate monitoring book;
- no separate performance monitor;
- no pooled cross-book performance page.

**Frozen configuration:** each book uses the frozen OOS-2 V3 rules and code, imported unchanged.
- **Shared rules:**
  - inclusive Candle-2 stop entry: fill = Candle-1 high × 1.0002, applied once;
  - structural stop with the 0.2 × ATR buffer;
  - `nearest_target_full_exit`;
  - 0.02% exit slippage, 0.04% taker fee each side, funding on;
  - stop first on a same-bar touch;
  - one position, LONG only, 2× leverage;
  - `risk_tolerance_pct` 0.10 and `size_rounding_tolerance` 0.10.
- **Per book:** `risk_usd` $10, $1,000 virtual capital.
- **Exchange filters:** ETH and BTC from the frozen table; SOL from an exchangeInfo snapshot at its freeze.
- **No change to raise trade count.**

**Contracts:** the ids are `CANDLE12_IDEA_ETHUSDT_1H_VISIBLE_PAPER/V1`, `…SOLUSDT_1H…/V1` and `…BTCUSDT_4H…/V1`. Each has its own lock, store, run lock, kill switch (≠ BTC `DEFAULT_KILL_SWITCH`), dashboard and job.

**Isolation, reconciliation and dashboard:** as in `08beb91` §4, §6 and §7. Every page carries the banner: "Correlated crypto-family research book. Evidence only; cannot authorize live trading. Not CANDLE12 BTC OOS validation."

## 3. Historical feasibility count (authorized, read-only)

**Purpose:** count the trades each book could actually have executed at $10 under the frozen rules. **It does not measure performance or edge, and none is reported.**

| Item | Specification |
|---|---|
| Books | ETH 1h, SOL 1h, BTC 4h, all LONG. Plus BTC 1h at the frozen $20 as a **harness check**: it should reproduce the 84-trade in-sample reference count on the 2023-01-01 to 2026-10-15 13:00 window. That is in-sample history, not OOS-2. |
| Config | `candle12_v3_paper.contract.contract_config()` with `risk_usd` = 10 and `funding_enabled` = False. Funding doesn't affect sizing or counts. Every other rule is frozen. Filters: ETH and BTC from the frozen table (step 0.001; minimum notional $20 for ETH, $50 for BTC). SOL from `GET /fapi/v1/exchangeInfo` (LOT_SIZE stepSize and minQty, MIN_NOTIONAL), passed as `qty_step`, `min_qty` and `min_notional`, and recorded with the fetch time. |
| Data | Binance USDⓈ-M public REST `GET /fapi/v1/klines`, from 1,600 bars before 2023-01-01 through the last bar opening on 2026-10-15. `check_oos_guard` is called on every series. |
| Engine | The frozen `run_candle12_v3` with `index_start` = the first bar ≥ 2023-01-01. Size checks run exactly as frozen. A rejected signal leaves the book flat, as the engine does natively. |
| Outputs per book | Signal funnel: total LONG signals after the start = refire-suppressed + blocked by own position + no fill + size-rejected + entered. **Signals reaching sizing** (the Candle-2 trigger traded). Rejections by reason, counted by any reason and by first reason: step size and rounding (`ZERO_QTY`, `BELOW_MIN_QTY`, `ROUNDING_SHORTFALL`), `MIN_NOTIONAL_REJECTED`, and `RISK_TOLERANCE_REJECTED` (the margin cap). Raw quantity in exchange steps. **Executable trades.** Rate a year (whole window, per calendar year, last 12 months). Expected counts and Poisson 10th/50th/90th percentiles at day 90, day 180 and day 365. |
| Read-only boundaries | **Never touched:** the OOS-2 store, the PostgreSQL database, LCSC files, the D:/cryptoscreener working tree. **Code:** a detached scratch worktree at the current HEAD, where the guard must report the frozen files identical to the OOS-2 lock. Outputs stay in this session's own scratch area. **Interpreter:** the existing CANDLE12 interpreter with `-B` and `PYTHONDONTWRITEBYTECODE=1`, so no bytecode is written anywhere. No pip. |
| Budget | One process at BelowNormal; ≤ 1 GB RSS; ≤ 60 minutes. It starts outside HH:15–HH:35 and HH:58–HH:05 UTC, and refuses under 1.5 GB free memory or above 80% CPU. |
| Limitation | SOL's current exchange filters are applied to all history; past filters may have differed. |

### 3.1 Classification (F2; fixed before the count ran)

**Rate:** executable trades a year over the whole window, 2023-01-01 to 2026-10-15 (3.79 years). The last-12-month rate is reported as context only.

**Rejection share:** (step-size, rounding and minimum-notional rejections) / signals reaching sizing.
- `RISK_TOLERANCE_REJECTED` (the frozen margin-cap rule) is reported separately and isn't counted in the share.
- A signal rejected for both reasons counts once in the share.

| Class | Rule (the worse of rate and rejection decides) | Consequence |
|---|---|---|
| **FEASIBLE** | rate ≥ 12 a year **and** rejection share ≤ 10% | May be proposed for start; starting still needs approval |
| **MARGINAL** | 8 ≤ rate < 12, **or** 10% < share ≤ 20% | Not started without separate approval |
| **NOT_FEASIBLE** | rate < 8, **or** share > 20% | Not frozen, not started |

### 3.2 Results

*(Filled in after the count; see below.)*

## 4. Gates (per book)

**Definitions:**
- **Executable trade:** a closed trade in the book's store.
- **R:** net / $10.
- **Drawdown:** peak-to-trough of realized cumulative R.
- **Clean execution and reconciliation:** no unresolved execution or reconciliation exception, and no repeated execution failure (3 or more execution exceptions of one category within 7 days).

| Review | Fires when | Requirements | Outcome |
|---|---|---|---|
| **First** | day ≥ 90 **and** ≥ 3 executable trades | drawdown ≤ 8R; clean execution and reconciliation | continue, or pause |
| **Mid** | day ≥ 180 **and** ≥ 6 executable trades | drawdown ≤ 8R; clean execution and reconciliation | continue, or pause |
| **Annual** | day ≥ 365 **and** ≥ 12 executable trades | formal evidence review: expectancy with a bootstrap 90% CI, PF, drawdown, holding times, the forward random-entry percentile (method of `ce71ad3` §5), correlation caveat | continue, pause or kill. **It never authorizes live trading.** |
| **Late book** | a review's trade count is still unmet at **twice its expected date**. Expected date = max(the review's day, its trade count / the book's feasibility rate). | — | mark **NOT_FEASIBLE_FORWARD** and pause the book |
| **Any time** | continuous | drawdown > 10R; an unresolved reconciliation exception; repeated execution failure | pause or kill at once |

**No month-6 kill.** A book is never killed at month 6 merely for having fewer than 25 trades. That rule is removed.

**Pause and kill:**
- A pause engages the book's own kill switch, which blocks new entries; an open position runs to its exit.
- A pause for an exception lifts once the exception is resolved with a note.
- Any other pause, or a kill, is final for that version. Resuming means a new version with a new window.

## 5. Resource safety and LCSC isolation

Each job, and the feasibility count:
- **Interpreter:** runs only the existing CANDLE12 interpreter, `D:\cryptoscreener\backend\.venv`. **It installs nothing and writes no bytecode into that shared venv** (`-B`, `PYTHONDONTWRITEBYTECODE=1`). LCSC also runs from this interpreter, and its hourly venv guard blocks LCSC on any venv change.
- **LCSC:** reads no LCSC database, file, record, lock or schedule. Market data comes directly from Binance public REST.
- **Timing:** stays outside HH:15–HH:35 UTC (LCSC hourly at :20). The book jobs run at ETH HH:01:00, SOL HH:02:15 and BTC 4h HH:03:30 (4h closes only), after OOS-2's bar processing.
- **Location:** outputs stay in this session's own worktree, `D:/cryptoscreener-worktrees/candle12-visible-books` (and scratch area for the count). Never in D:/cryptoscreener.
- **Limits:**
  - one job at a time, through a slot lock;
  - each capped at **60 s** (hard kill at 90 s), BelowNormal, one core, RSS ≤ 400 MB;
  - each starts, processes, writes and exits.
- **Refusal:** a job refuses to start when free memory is < 1.5 GB or CPU > 80% (5 s average). It records `RUN_SKIPPED_RESOURCES`. The missed entry is recorded and never back-filled, and the skip rate is on the dashboard.

## 6. Test plan

The per-book tests from `08beb91` §10 apply, plus:

| # | Test | Pass condition |
|---|---|---|
| 1 | Ownership | No code path writes STAGED-1 or OOS-2 records or emits a live-eligibility flag; no order endpoint is reachable |
| 2 | Feasibility count | The BTC 1h harness check matches 84 trades. Synthetic size cases give the expected reasons. The share formula matches section 3.1. Classification boundaries (12, 8; 10%, 20%) are exact. |
| 3 | Not feasible | A NOT_FEASIBLE or (unapproved) MARGINAL book can't be frozen or started |
| 4 | Reviews | Each review needs its day **and** its count. The late-book rule fires at twice the expected date. There is no month-6 trade-count kill. |
| 5 | Outcomes | Synthetic books continue, pause or are killed exactly as in section 4; exception pauses lift on resolution |
| 6 | No duplicate monitor | No second store, book or performance process exists for any symbol |
| 7 | Venv and bytecode | After a job and after the count, no file under the shared venv was created or changed and no `.pyc` appears |
| 8 | Resource refusal | Synthetic low memory or high CPU → `RUN_SKIPPED_RESOURCES` |
| 9 | Serial slot and caps | Jobs never overlap; the 60 s cap and 90 s kill work; all finish by HH:05 |
| 10 | Isolation | Path guards reject OOS-2, LCSC, the database and other books' stores |

## 7. Rollback

- **Before any start:** delete the worktree and these docs.
- **After a start:**
  1. Engage each book's own kill switch.
  2. Let open positions exit.
  3. Disable the jobs.
  4. Archive the stores.

  OOS-2, STAGED-1 and LCSC are never touched.
- **A rule change** makes a new version with a new window.

## 8. Anti-tuning

- Every rule is frozen before any book starts.
- Nothing changes after seeing paper results.
- The books are one correlated evidence pool, not independent proofs.
- The track never authorizes live trading and never touches OOS-2, LCSC, fingerprinted files, shared modules, contracts or locks.
