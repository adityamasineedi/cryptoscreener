# CANDLE12 fast track: early kill and execution evidence, three visible paper books (design only)

**Status:**
- DESIGN_ONLY, revised per the user's decision of 2026-10-16.
- Nothing has been implemented, run or started.
- The historical feasibility study (section 3) is a **specification**. Running it is a historical engine run and needs separate approval.

**This track cannot authorize live trading,** for BTC or for any other symbol. It cannot replace STAGED-1. OOS-2 remains sealed and untouched.

**Limitations:**
- **One evidence pool, not independent tests.** The visible books (ETH 1h, SOL 1h, BTC 4h) run the same CANDLE12 idea on highly correlated crypto prices, so they form one crypto-family evidence pool. None of them validates CANDLE12 BTC 1h.
- **Trade frequency limits how fast evidence arrives.**
  - At the historical BTC and ETH 1h rates of about 22–24 trades a year, one 1h book has about a 0.1% chance of 25 trades by month 6. It needs about **12–14 months** to reach 25 (section 4.2).

## 1. Protocol ownership

| Protocol | Scope | Can authorize live? | Status |
|---|---|---|---|
| **OOS-2** (`CANDLE12_V3_EXEC_FIX/OOS-2`) | BTCUSDT 1h formal blind test; the existing runner and store | No | Running, **sealed, untouched** |
| **STAGED-1** (`docs/candle12_v3_staged_validation_design.md`, `73ddced`) | **The sole BTC live-eligibility protocol** | Only as its own rules state, with a separate live-risk approval | Draft, **not frozen**. Seven open points remain. While it is unfrozen, OOS-2 stays sealed. Freezing it is a separate user decision. |
| **Fast track** (this document) | Visible paper books 2–4 only: ETH 1h, SOL 1h, BTC 4h | **Never** | Design only |
| Operational view (`7ccdc49`) | Operational fields of OOS-2 | No | Design only |
| Historical robustness (`ce71ad3`) | BTC 1h history up to 2026-10-15 | No | Design only |

**What the fast track can and cannot do:**
- It **may** pause or kill its own visible books.
- It **cannot** authorize live trading, change STAGED-1, change OOS-2, or feed STAGED-1 or OOS-2 decisions.
- Its results are evidence only.

**Earlier designs:**
- The ETH design (`08beb91`) keeps its architecture, isolation, reconciliation and dashboard.
- Its acceptance section and halts are replaced by section 4 here.
- The live-staging section of this plan's previous revision is **withdrawn**.

## 2. Books and frozen configurations

| # | Book | Visibility | Process |
|---|---|---|---|
| 1 | `BTCUSDT_1H_OOS2` | Sealed (operational view only) | The existing OOS-2 runner. Not part of this track. |
| 2 | `ETHUSDT_1H_VISIBLE_PAPER` | Full | Short job, HH:01:00 UTC |
| 3 | `SOLUSDT_1H_VISIBLE_PAPER` | Full | Short job, HH:02:15 UTC |
| 4 | `BTCUSDT_4H_VISIBLE_PAPER` | Full | Short job, HH:03:30 UTC, at 4h closes only (00, 04, 08, 12, 16, 20 UTC) |

**Shared rules:** the frozen OOS-2 V3 rules and code, imported unchanged:
- inclusive Candle-2 stop entry: fill = Candle-1 high × 1.0002, applied once;
- structural stop with the 0.2 × ATR buffer;
- `nearest_target_full_exit`;
- 0.02% exit slippage, 0.04% taker fee each side, funding on;
- stop first on a same-bar touch;
- one position, LONG only, 2× leverage;
- `risk_tolerance_pct` 0.10 and `size_rounding_tolerance` 0.10.

**Per-book values:**
- **Risk and capital:** `risk_usd` **$10**, $1,000 virtual capital.
- **Exchange filters:**
  - ETH and BTC use the frozen table: step 0.001; minimum notional $20 for ETH and $50 for BTC.
  - SOL uses `GET /fapi/v1/exchangeInfo` (LOT_SIZE stepSize and minQty, MIN_NOTIONAL), snapshotted with its time when the book freezes.
- **No change to raise trade count.** The previous revision's idea of raising `risk_usd` to get more trades is **dropped**. A book that can't trade at $10 is NOT_FEASIBLE.

**Architecture, isolation, reconciliation and dashboard:** as in `08beb91` §3, §4, §6 and §7, applied per book. In brief:
- **Location:** one worktree, `D:/cryptoscreener-worktrees/candle12-visible-books`, on a branch from tag `candle12-v3-oos-2-freeze-2026-10-15`.
- **Per book:** its own contract, lock, store, run lock, kill switch and dashboard. Each kill switch is separate from BTC's `DEFAULT_KILL_SWITCH`.
- **Frozen-code check:** imported frozen files must match the OOS-2 lock hashes, or that book halts.
- **Live mark price:** optional, polled by the browser from Binance's public API.
- **Pooled page:** shows each book's status and gate progress.
- **Banner on every page:** "Correlated crypto-family research book. Evidence only; cannot authorize live trading. Not CANDLE12 BTC OOS validation."

## 3. Historical feasibility study (specification; runs before any book freezes, with approval)

**Purpose:** count the trades each book could actually have executed at $10 under the frozen rules. Nothing is changed to raise the count.

| Item | Specification |
|---|---|
| Books | ETH 1h, SOL 1h, BTC 4h. Plus BTC 1h at the frozen $20 as a reproduction check: it must reproduce 84 trades, +$601.34 and $169.35 drawdown on the 13:00 reference window, or the study stops. |
| Data | Binance public REST klines, 2023-01-01 to 2026-10-15 23:00 UTC (bar open), cached in the study worktree. **No database, no LCSC files, no OOS-2 files.** The engine's reserved-window guard stays on. |
| Engine | The frozen `run_candle12_v3` with each book's config. The size checks run exactly as frozen: `ZERO_QTY`, `BELOW_MIN_QTY`, `ROUNDING_SHORTFALL` (> 10% cut by step rounding), minimum notional, and the 2× margin cap with its 10% actual-vs-intended risk check. A rejected signal leaves the book flat, so later setups can enter, as the engine does natively. |
| SOL filters | The current exchangeInfo values are applied to all history. Past filters may have differed; this is stated as a limitation. |
| BTC 4h | Size rejections are reported by raw quantity in exchange steps (< 2, 2–6, 6–10, ≥ 10). At 2–6 steps, the frozen 10% rule rejects about 60% of uniformly spread quantities. |
| Per book, reported | Signals; would-be fills; **executable trades**; rejections by reason and share; executable trades a year (overall, each calendar year, last 12 months); holding-time distribution |
| Expected forward counts | From the executable rate λ (overall and last 12 months), at day 30, day 60, month 3, month 6 and month 12: the expected count, the Poisson 10th/50th/90th percentiles, and P(gate count reached by the gate date) for the section 4 gates |
| Budget | Serial, BelowNormal, one core; about 4 walks; ≤ 1 GB RSS; ≤ 60 minutes total; blackouts HH:15–HH:35 and HH:58–HH:05. Runs in the study worktree. Installs nothing in any venv. |

**Feasibility rule.** It must be fixed **before** the study runs (open point 1):

| Option | FEASIBLE when | Likely effect |
|---|---|---|
| F1: on schedule (literal) | The median expected executable count meets every gate count by its date: ≥ 3 by day 30, ≥ 6 by day 60, ≥ 25 by month 6. That needs about 50 trades a year. | At the historical rates (about 22–24 a year before $10 rejections), **no book is expected to qualify**, so nothing starts |
| **F2: rate floor (recommended)** | Executable rate ≥ 12 a year, both overall and in the last 12 months, **and** size rejections ≤ 10% of would-be fills | Books that trade at least monthly start. The gates fire when the date and the count are both reached (section 4). The expected gate dates are frozen with the book as estimates. |

A NOT_FEASIBLE book is not frozen or started. Its result is recorded.

## 4. Gates (calendar plus trade count; per book unless stated)

**Definitions:**
- **Executable trade:** a closed trade in the book's store.
- **R:** net / $10.
- **Fully costed:** recorded fees, 0.02% slippage and recorded funding.
- **Drawdown:** peak-to-trough of realized cumulative R.
- **Aggregate expectancy:** the mean R over all closed trades of the active books.
- **Unresolved exception:** an execution or reconciliation exception not RESOLVED.
- **When a gate is evaluated:** at the first daily evaluation (00:05 UTC) at which **both** the calendar date and the trade count hold.

### 4.1 Gates

| Gate | Trigger | Rules | Decision |
|---|---|---|---|
| **G30** (per book) | day ≥ 30 **and** the book has ≥ 3 executable trades | drawdown ≤ 8R; no unresolved exceptions | pass: continue; fail: **pause** that book |
| **G60** (pooled) | day ≥ 60 **and** every active book has ≥ 6 executable trades | ≥ 2 of the 3 visible books have positive expectancy (a paused or NOT_FEASIBLE book counts as not positive); aggregate expectancy > 0.10R; no book drawdown > 8R | pass: continue all. Fail: pause each book with own expectancy ≤ 0 or drawdown > 8R. If the 2-of-3 or aggregate rule fails, pause every book with own expectancy ≤ 0. |
| **R25 formal review** (per book) | month ≥ 5 **and** the book has ≥ 25 executable trades | A predeclared statistical report: expectancy with a bootstrap 90% CI, PF, drawdown, holding times, the forward random-entry percentile (same method as `ce71ad3` §5), and the pooled view with the correlation caveat | **Evidence only.** It can't authorize live trading and can't change STAGED-1 or OOS-2. |
| **Any time** (per book) | continuous | drawdown > 10R → **kill**; an unresolved reconciliation exception → **pause** new entries until it is resolved with a note; 3 or more execution exceptions of one category within 7 days → **pause** | as stated |

**Pause and kill (proposed; open point 3):**
- A **gate pause** engages the book's own kill switch, which blocks new entries; an open position runs to its exit. The book then stays out of pooled rules, and resuming means a new version with a new window.
- An **exception pause** lifts once the exception is resolved.
- A **kill** stops the book and disables its job.

### 4.2 Too few trades at a gate date (open point 2)

Under F2, a gate whose date has passed but whose count isn't met is **NOT_YET**. It is re-checked daily.
- **Deadline:** if a book hasn't reached a gate's count by twice that gate's frozen expected date (from section 3), it is marked **NOT_FEASIBLE_FORWARD**, paused, and excluded from pooled rules.
- **G60 waits** for every active book, so a slow book either catches up or drops out at its deadline.

**Illustration** (Poisson; at the historical 1h rate, before $10 rejections):

| Rate | E[day 30] | E[day 60] | E[month 3] | E[month 6] | E[month 12] | P(≥ 3 by day 30) | P(≥ 6 by day 60) | P(≥ 25 by month 6) | Median days to 25 |
|---|---|---|---|---|---|---|---|---|---|
| 24 / yr | 2.0 | 3.9 | 6.0 | 12.0 | 24.0 | 32% | 21% | 0.1% | ≈ 375 |
| 22 / yr | 1.8 | 3.6 | 5.5 | 11.0 | 22.0 | 27% | 16% | ≈ 0% | ≈ 410 |
| 12 / yr | 1.0 | 2.0 | 3.0 | 6.0 | 12.0 | 8% | 2% | ≈ 0% | ≈ 750 |

The R25 review should be expected around month 12–14 for a 1h book, not month 5–6. A BTC 4h book is expected to be slower still. The feasibility study replaces these figures with measured executable rates.

## 5. Resource safety and LCSC isolation

**Jobs:** three short jobs (ETH HH:01:00, SOL HH:02:15, BTC 4h HH:03:30 UTC).
- **One at a time:** a machine-wide slot lock, `visible_books.slot`, ensures it. A job that can't take the slot within 20 s skips.
- **Each job:**
  - starts, processes newly closed bars, writes its own store and dashboard, then exits;
  - is capped at **60 s**, with a hard kill at 90 s;
  - runs at BelowNormal, with one-core affinity and RSS ≤ 400 MB.
- **Refuses to start when** free physical memory < 1.5 GB or CPU > 80% (a 5 s average before start). The refusal is recorded as `RUN_SKIPPED_RESOURCES`.
  - **A skipped job means a missed entry.** The frozen adapter misses entries placed more than 300 s after the close, and they are never back-filled.
  - The skip rate is shown on the dashboard. On this machine, which hit 100% CPU today, skips may be frequent and will lower trade counts.
- **Market data:** read directly from Binance public REST (GET klines, fundingRate, premiumIndex, time). No keys.
- **Never touched:** the LCSC worktree, the database, LCSC records, LCSC schedule, and any venv.
- **Interpreter:** the existing CANDLE12 interpreter, execute-only. **Nothing is installed in any venv.**
- **Calibration:** a 48-hour dry run on a pre-start store. If any job's p95 runtime exceeds 60 s, the schedule doesn't fit; stop and redesign.
- **OOS-2:** the jobs never read OOS-2 files and never run in its bar-close window, HH:00:00–HH:00:30.

## 6. Test plan

The per-book tests from `08beb91` §10 apply, plus:

| # | Test | Pass condition |
|---|---|---|
| 1 | Ownership | No code path in this track writes STAGED-1 or OOS-2 records, or emits a live-eligibility flag. A scanner finds no live or order endpoint. |
| 2 | Feasibility study | The BTC 1h reproduction check matches. Each frozen size rule on synthetic cases gives the expected rejection reason. Rejected signals leave the book flat. Poisson outputs match hand calculations. |
| 3 | NOT_FEASIBLE | A synthetic below-rule book can't be frozen or started |
| 4 | Gate triggers | Date **and** count are both needed; NOT_YET re-checks daily; the twice-expected-date deadline marks NOT_FEASIBLE_FORWARD |
| 5 | G30, G60 and any-time rules | Synthetic books pause, kill or continue exactly as in section 4.1, with paused books excluded from pooled rules |
| 6 | R25 report | Fields present; marked evidence only; no authorization field |
| 7 | Resource refusal | Synthetic free memory < 1.5 GB or CPU > 80% → `RUN_SKIPPED_RESOURCES`, and the missed entry is recorded |
| 8 | Serial slot and caps | Concurrent starts never overlap; the 60 s cap and 90 s kill work; all three jobs finish by HH:05 |
| 9 | Isolation | Path guards reject OOS-2, LCSC, the database and other books' stores; no venv write is attempted |
| 10 | 4h schedule | The BTC 4h job processes 4h closes only, within 300 s |

## 7. Rollback

- **Before any start:** delete the worktree and these docs. Nothing else exists.
- **After a start:**
  1. Engage each book's own kill switch.
  2. Let open positions exit.
  3. Disable the three jobs.
  4. Archive the stores.

  OOS-2, STAGED-1 and LCSC are never touched, so there is nothing to undo there.
- **Any rule change** makes a new version of that book with a new window. Old records are kept.

## 8. Open points for the user (before the feasibility study runs)

1. **Feasibility rule:** F1 (literal; probably starts nothing) or F2 (rate ≥ 12 a year and rejections ≤ 10%; recommended)?
2. **Too few trades:** NOT_YET with a deadline at twice the expected date, then NOT_FEASIBLE_FORWARD. Accept?
3. **Pause meaning:** gate pause is permanent (resuming means a new version); exception pause lifts when resolved. Accept?
4. **Running the study:** approve the historical feasibility study run (section 3)?

## 9. Anti-tuning

- Every rule is frozen before any book starts.
- Nothing changes after seeing paper results. A change makes a new version and restarts that book.
- The books are one correlated evidence pool, not independent proofs.
- The track never authorizes live trading, never replaces STAGED-1, and never touches OOS-2, LCSC, fingerprinted files, shared modules, contracts or locks.
