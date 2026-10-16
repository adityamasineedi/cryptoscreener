# Track 1: CANDLE12_IDEA_ETHUSDT_1H_VISIBLE_PAPER (design only)

**Status:** DESIGN_ONLY. Nothing has been implemented or started. Implementation and start each need the user's approval.

**Disclosure:**
- **This is a separate ETH research paper book.** It is correlated with BTC. It does **not** validate CANDLE12 BTC OOS-2 or any BTC strategy performance.
- **OOS-2 remains the formal blind BTC test.** It stays sealed until its read on or after 2027-10-16.
- **The correlation is more than a regime hint.** It is stronger than "a qualitative hint about market regime":
  - ETH and BTC 1h prices move together, and the same rule family reads the same hourly market structure.
  - So ETH entries may often coincide with BTC entries, and their outcomes may match trade by trade.
  - A visible ETH book can therefore carry partial information about individual OOS-2 trades, not only the regime.
  - Section 9 measures this on history before start, so the user accepts the residual knowingly.

## 1. Hypothesis and contract

| Item | Value |
|---|---|
| Track id / version | `CANDLE12_IDEA_ETHUSDT_1H_VISIBLE_PAPER` / V1 |
| Hypothesis | The CANDLE12 V3 rule set, unchanged except for symbol and risk size, has positive net expectancy on ETHUSDT 1h LONG on forward bars. |
| Symbol, timeframe, side | ETHUSDT, 1h, LONG only |
| Mode | Virtual paper only. No broker, no orders sent anywhere, no API keys. |
| Forward window | Starts at the first 00:00 UTC after the freeze commit, which follows design approval. Only bars at or after the start count. |
| Earliest formal read | Both conditions: ≥ 30 closed trades **and** ≥ 12 months after start. History suggests about 24 ETH LONG trades a year (V2 in-sample), so roughly 15 months. |
| Acceptance (predeclared) | **PASS** needs all four: PF ≥ 1.30; net > 0 at 0.02% slippage; net > 0 at 0.05% (same bars, same entries, replayed); max drawdown ≤ 10R. Other outcomes: FAIL_EDGE, FAIL_RISK (a drawdown pause triggered and was not released), INSUFFICIENT_SAMPLE, EXECUTION_MISMATCH (open reconciliation exceptions). |
| Optional stopping | The book is visible throughout, but **no acceptance or rejection decision is made before the read gate**. Watching is not deciding. |
| Versioning | Any rule or config change creates V2 with a new forward window. V1 records are kept, not reused. |

**In-sample disclosure:**
- CANDLE12 on ETH 1h was already examined in the 2026-10-15 ETH audit.
- That V2 LONG-only book was weak and decaying: n = 92, +$244.58 at $20 risk. In the combined book, trades 32–87 lost $137.
- The V3 ETH in-sample result is reproduced and printed in the freeze record **before** start (section 9). It is history only.

## 2. Frozen configuration

The config is `Candle12V3Config`, built from the BTC OOS-2 contract config with only `risk_usd` changed and the symbol set to ETHUSDT. The engine code is the frozen OOS-2 code, imported unchanged.

| Rule | Setting | Value |
|---|---|---|
| Setup | V2 Candle-1 BOS detector | `require_impulse` true, `require_close_in_bos_direction` true, `structure_lookback_bars` 300, `bos_refire_policy` `once_per_level` |
| Entry | Candle-2 buy-stop at the Candle-1 high | `entry_slippage_mode` `inclusive`, `entry_slippage` 0.0002. **Fill = Candle-1 high × 1.0002, applied once.** The ×1.0002 *is* the 0.02% adverse slippage; it is not added twice. If Candle-2 opens above the order, the fill is the Candle-2 open × 1.0002 (gap rule). |
| Stop | Structural | Min(pullback low, impulse origin, Candle-1 low) minus `sl_buffer_atr` 0.2 × ATR(14). The frozen stop includes this ATR buffer. |
| Target | `nearest_target_full_exit` | Nearest of TP1–TP3 (structural targets ≥ `min_rr` 2.0, else 2R/3R/4R), full exit. There is no ETH-specific target policy. |
| Exit slippage | `exit_slippage` | 0.0002 adverse |
| Fees | `taker_fee` | 0.0004 each side |
| Funding | `funding_enabled` true | Binance public ETHUSDT funding rate × position notional at each settlement held |
| Same bar | Engine rule | If one bar touches both stop and target, the stop is taken first |
| Positions | `portfolio_lock_mode` `per_side`, LONG book | One at a time; the safety limit `max_open_trades_per_symbol` is 1 |
| Sizing | `risk_usd` **10.0**, `account_equity` 1000.0, `leverage` 2.0, ETH exchange filters (step 0.001, min notional $20), `risk_tolerance_pct` 0.10 | A stop tighter than about 0.5% hits the 2× margin cap. Actual risk then falls more than 10% below $10, and the trade is rejected, as on BTC. |

**Stress run:** the 0.05% figure comes from a replay of the same bars with 0.05% slippage. It is used only in the acceptance check.

## 3. Architecture

- **Code:**
  - The worktree is `D:/cryptoscreener-worktrees/candle12-eth-paper`, on branch `candle12-eth-visible-paper` from tag `candle12-v3-oos-2-freeze-2026-10-15`.
  - New code goes only in `backend/scripts/candle12_eth_visible/`: `run_hour.py`, `halts.py`, `dashboard.py`, `contract.py` and tests.
  - It imports the frozen `candle12_v3_paper` modules and the engine unchanged: `PaperAdapter`, `PaperStore`, the simulated broker, `SafetyLimits`, the Binance kline and funding sources, and `reconcile`.
  - It never imports `candle12_v3_paper.cli`, which is hard-coded to BTC, or the testnet broker.
  - **Frozen-code check:** on every run, the frozen files it imports must hash to the OOS-2 lock's values. If they differ, the ETH track halts. OOS-2 is unaffected.
- **Store:** `<worktree>/backend/data/candle12_eth_visible/v1/` (git-ignored). It holds its own manifest (`counts_toward_oos` false, `track_id` set), events, trades, signals, state, heartbeats, exceptions, alerts, reconciliation, halts and approvals, and the dashboard.
- **Kill switch:** `<store>/kill_switch` only. **It is never the BTC `DEFAULT_KILL_SWITCH`.**
- **Process:** one bounded scheduled task, `CANDLE12_ETH_VISIBLE_PAPER_Hourly`, at HH:01:30 UTC. Each run:
  1. Takes the ETH store's own run lock.
  2. Resumes the adapter from the store (simulated broker restore).
  3. Fetches closed ETHUSDT 1h klines and funding from Binance public REST: GET only, no keys.
  4. Processes newly closed bars: signals, Candle-2 entry orders, exits.
  5. Applies the halts in section 5.
  6. Reconciles closed trades.
  7. Writes the dashboard, then exits.
- **Why HH:01:30:**
  - The frozen adapter misses an entry placed more than 300 s after the Candle-1 close, so the run must start within 5 minutes of the close.
  - It starts after OOS-2's bar processing (about 15 s past the hour), well before HH:15–HH:35.
  - A run that is skipped or late records `SIGNAL_MISSED`. It is never back-filled.
  - **If calibration shows a run cannot reliably finish by HH:04:30,** the fallback is a resident BelowNormal process with the same code. That needs separate approval.
- **Interpreter:** the existing CANDLE12 venv, execute-only. Nothing is installed in it. This is the same open question as LCSC Option A, and the user decides.

## 4. Dashboard

The dashboard is a self-contained static HTML page plus `dashboard.json`, regenerated each run in `<store>/dashboard/` and opened locally.
- **Banner:** "Separate ETH research paper book, correlated with BTC. Not CANDLE12 BTC OOS validation."
- **Live mark price (optional):** browser JavaScript polls Binance's public mark price every 30 s for unrealized PnL. It is public, needs no key, and adds no server load.

| Panel | Fields |
|---|---|
| Open position | Entry time and fill, stop, target, quantity, notional, mark price, unrealized PnL ($ and R), funding accrued |
| Pending order | Candle-2 buy-stop price and state |
| Order states | Every order with `ORDER_REQUESTED` / `ORDER_ACCEPTED` / `ORDER_REJECTED` (reason) / `ORDER_PARTIALLY_FILLED` / `ORDER_FILLED` / `ORDER_CANCELED`, with timestamps; counts by state |
| Trade history | Per trade: entry, stop, target, exit time and price, exit reason (STOP/TARGET), quantity, gross, fees, entry and exit slippage, funding, net, R (intended and actual risk) |
| Summary | Realized PnL, closed trades, wins/losses, win rate, PF, expectancy, max drawdown (R), days elapsed and progress to the read gate (n / 30, months / 12) |
| Equity curve | Realized equity from $1,000; drawdown shaded; halt markers |
| Costs | Totals and per trade: fees, slippage, funding |
| Signals | Detected, entered, blocked, rejected, missed, invalid |
| Reconciliation | Per trade and per check: within tolerance, or the exception id |
| Health | Last run, latest bar processed, heartbeat age, open exceptions, alerts, halt state, frozen-code check |

## 5. Risk controls

| Control | Rule | Action |
|---|---|---|
| Virtual capital | $1,000 constant equity for sizing | — |
| Risk per trade | $10 intended; the 10% risk-tolerance check applies | Size-rejected signals are recorded |
| Concurrency | One position | `max_open_trades_per_symbol` = 1 |
| Daily loss halt | Realized net PnL for the UTC day ≤ −2R (−$20) | Engage the ETH kill switch (blocks new entries only); record `HALT_DAILY_LOSS`. An open position keeps its stop and target. |
| Drawdown pause | Realized equity ≥ 10R (−$100) below its running peak | Engage the ETH kill switch; record `HALT_DRAWDOWN` |
| Restart after a halt | **Only with the user's explicit approval** | The approval is appended to `approvals.jsonl` (who, UTC time, reason), then the switch is released. There is no automatic release at the day boundary. |
| Paper only | Simulated broker | No live orders. No credentials are loaded: the task runs with `BINANCE_*` variables scrubbed, and the HTTP client allows only GET on `/fapi/v1/klines`, `/fapi/v1/fundingRate`, `/fapi/v1/premiumIndex` and `/fapi/v1/time`. |

**Effect on the record:**
- Halts reduce the sample. Entries blocked by the kill switch are recorded as `SIGNAL_BLOCKED` with reason `KILL_SWITCH`.
- The engine replay used for reconciliation (section 7) still shows what the unhalted rules would have done. It is reported as a "shadow rule book" line, for information only.
- Acceptance uses the book as traded.

## 6. Isolation

**Never read:**
- OOS-2 BTC files (`backend/data/candle12_v3_paper/`, including heartbeats);
- Track 2's records;
- any LCSC worktree, record, lock, code or schedule (`lcsc-fwd3`, `forward_out`);
- the PostgreSQL database. Market data comes from Binance public REST, so there is no database contention with LCSC or the backfill.

**Never written:**
- `D:/cryptoscreener`;
- the fingerprinted files, shared modules or OOS-2;
- the BTC kill switch;
- LCSC.

**Separation:** separate task name, worktree, store, lock and output folder.

**Enforcement:** a path guard raises on any open outside the ETH store and the worktree code. The only network access is the four public GET endpoints above.

## 7. Reconciliation

On every run, for each newly closed trade, the frozen `reconcile()` (symbol ETHUSDT, the ETH config) replays the frozen engine on the stored closed bars and compares expected and paper values.

| Check | Tolerance |
|---|---|
| Entry fill vs expected | Within 0.05% (`entry_fill_rel`). A documented gap event (Candle-2 opens above the order) is expected at the open × 1.0002 and checked against that. |
| Stop fill vs expected | Within 0.05% (`stop_fill_rel`). A bar that opens through the stop is flagged as a documented gap event. |
| Target fill vs expected | Within 0.05% (`target_fill_rel`) |
| Quantity | Exact after the 0.001 step |
| Fees | Within $1e-6 of the 0.04% model. Virtual mode has no exchange fee reports, so this checks the model. |
| Funding | Within $1e-9 of the Binance-reported rate × notional at each settlement held. If a rate is missing: `MISSING_FUNDING_DATA` exception, no estimate. |
| Slippage | Entry and exit slippage equal the configured 0.02% |
| Exit reason | Paper equals engine (STOP / TARGET) |
| PnL and R | Net equals gross − fees + funding, recomputed; R = net / $10 |
| Order prices, times, candle data | Exact |

- **A check outside tolerance** creates a reconciliation exception, which blocks acceptance until it is resolved with a note.
- **Engine trades missing from paper:**
  - an expected `KILL_SWITCH` block is not a mismatch;
  - anything else is `UNEXPLAINED_SKIP`.
- **Data integrity:** each run re-fetches the last 3 closed klines. Any difference from the stored bars is a `DATA_REVISION` exception.

## 8. Resource budget

| Item | Budget |
|---|---|
| Schedule | Hourly at HH:01:30 UTC. No retries inside HH:15–HH:35. |
| Wall-clock | Target ≤ 60 s; hard kill at 180 s (task time limit) |
| CPU | BelowNormal, one-core affinity; target ≤ 20 s per run (to calibrate) |
| Memory | RSS ≤ 400 MB, else abort with an exception. Refuse to start when commit headroom < 1.5 GB (records `RUN_SKIPPED_RESOURCES`). |
| Network | 3–4 public GET requests per run |
| Disk | A few MB a year |

**Calibration gate:** the first 48 hours run as a dry calibration, with no counted trades and a pre-start store. If p95 runtime exceeds 120 s, stop and propose the resident fallback.

## 9. Pre-start steps (after approval, before the forward start)

Both are historical computations on bars ≤ 2026-10-15 23:00 UTC. Neither touches OOS-2 files.

1. **ETH in-sample reproduction:** one engine walk of the frozen ETH config on 2023-01-01 to 2026-10-15. The result goes into the freeze record as the visible in-sample baseline.
2. **Correlation measurement:**
   - from the BTC reference book (in-sample) and the ETH in-sample book: the share of BTC trades with an ETH entry within ±3 bars;
   - the outcome concordance of those pairs (both STOP or both TARGET);
   - the correlation of 1h returns.

   The figures go into the freeze record, and the user confirms acceptance of the residual information before start.

## 10. Test plan

| # | Test | Pass condition |
|---|---|---|
| 1 | Config freeze | The track config equals the OOS-2 contract config except for `risk_usd` and symbol; it is hashed into the ETH lock |
| 2 | Entry price | Inclusive mode: fill = Candle-1 high × 1.0002, applied once; gap case fills at open × 1.0002 |
| 3 | Same-bar rule | A bar touching both levels exits at the stop |
| 4 | One position | A second signal while in position is `SIGNAL_BLOCKED` |
| 5 | Sizing | $10 intended risk; step rounding; margin-cap rejection beyond 10% |
| 6 | Daily halt | A synthetic −2R day engages the ETH kill switch; the open position is still managed; no release without an approval row |
| 7 | Drawdown pause | A synthetic 10R drawdown engages the kill switch; same release rule |
| 8 | Own kill switch | The path is inside the ETH store and ≠ the BTC `DEFAULT_KILL_SWITCH`; engaging it leaves the BTC file untouched |
| 9 | No live path | Testnet broker and CLI not imported; `BINANCE_*` absent; HTTP allow-list rejects POST and any other endpoint |
| 10 | Isolation | The path guard raises on OOS-2, Track 2, LCSC paths and the database |
| 11 | Frozen-code check | A mismatching hash halts the ETH track and leaves OOS-2 untouched |
| 12 | Late or missed run | A run more than 300 s after close records `SIGNAL_MISSED`; nothing back-filled |
| 13 | Restart idempotence | Rerunning the same hour or killing mid-run then rerunning gives identical records (the write-ahead store) |
| 14 | Reconciliation | Injected entry, exit, quantity, fee, funding, reason and PnL mismatches each raise the right exception; gap events are classified, not failed |
| 15 | Dashboard | Every field in section 4 present on a synthetic store; banner present |
| 16 | Budget | Overrun, RSS cap and low-headroom refusal behave as specified |
| 17 | Forward boundary | No trade before the start counts; warm-up bars are excluded |

## 11. Anti-contamination

- ETH results never change CANDLE12 BTC OOS-2, its rules, thresholds or dates.
- ETH results are never used to tune CANDLE12. A change makes a new ETH version with a new window.
- OOS-2 stays sealed until its formal read. ETH results are not a reason to stop or alter it early.
