# Backtest and Strategies screen audit, 2026-10-17

Branch `fix/strategies-screen-audit` (worktree `D:/cryptoscreener-worktrees/strategies-screen-audit`, base `398bec1`).
The screens were read from the live app (frontend http://127.0.0.1:5173, backend http://127.0.0.1:8000, both served from `D:/cryptoscreener`).
`D:/cryptoscreener` was not edited. Clicks were driven by headless Chromium (playwright-core, scratchpad only).
The live `BacktestPanel.tsx` has uncommitted edits from another session (regime book for "Passed strategies (auto)"), so Part 1 tests that live version, not the `398bec1` file.

Evidence (scripts, JSON results, screenshots) is in the session scratchpad:
`C:/Users/masin/AppData/Local/Temp/claude/d--cryptoscreener/bc7b17e6-8aa9-477f-a519-b5b019d17591/scratchpad/pw/` (screenshots in `shots/`).

Sealed windows. CANDLE12_V3_EXEC_FIX on BTC was not run, and no CANDLE12_V3 performance was read from any source (OOS-2 sealed from 2026-10-16 00:00 UTC until 2027-10-16). Regime Lab (`/regime-lab`) and the regime-book worktree study are not on the backtest menu and were not run. The Backtest screen's "Passed strategies (auto)" regime book is a different thing; it was run once (Part 1).

---

## Part 1 — Backtest screen, before any strategy run

Result: **53 PASS, 6 FAIL, 1 environmental FAIL (1.11), 9 cases not reachable in Part 1** (the not-reachable output cases were checked on the Part 2 runs; see "Part 1 addendum" at the end of Part 2).

### Fresh-load defaults (fresh browser context, `/backtest`)

| # | Case | Result | Text that decided it |
|---|---|---|---|
| 1.01 | Heading | PASS | `Strategy Backtest` |
| 1.02 | Tabs, Short selected | PASS | tabs `Long trades, Short trades, Hedge trades, Grid trades`; Short `aria-selected=true` |
| 1.03 | Strategy shows Passed strategies (auto), chips selected | PASS | select value `PASSED_AUTO`; 8 passed chips, all `aria-pressed=true`; chip `Strategy: Passed strategies (auto)` |
| 1.04 | Regime copy | PASS | `Passed short strategies stay selected. Run applies the BTC daily regime on each 1h bar.` |
| 1.05 | Direction SHORT, Status Research, banner | PASS | `Direction: SHORT`, `Status: Research`, `RESEARCH-ONLY CONFIGURATION` |
| 1.06 | Coins BTC ETH SOL, no USDT, coin-rule | PASS | chips `BTC× ETH× SOL×`; coin-rule `Large cap and mid cap coins only (cap book).` |
| 1.07 | Available coins | PASS | `Large cap BNB XRP DOGE ADA TRX AVAX LINK DOT BCH LTC TON` then `Mid cap NEAR ATOM APT ARB OP SUI INJ AAVE UNI FIL ETC ICP RENDER`; no BTC/ETH/SOL; nothing outside the book |
| 1.08 | Timeframes | PASS | 1h on; 15m, 4h present and off; note `Auto timeframe 1h: 3 of 3 coins have stored bars.` |
| 1.09 | Money inputs | PASS | principal 1000, risk 2 %, $20, leverage 2, taker 0.04, maker 0.02 |
| 1.10 | Period + pre-run summary | PASS | DB-tail mode on, `≈ 240 days at 1h(5,760)` on; summary `Selected symbol: BTCUSDT, ETHUSDT, SOLUSDT · Direction: SHORT · Setup timeframe: 1h · Effective risk: 2% / $20 · Leverage: 2× · Production comparable: NO` |
| 1.11 | Run enabled, Cancel hidden, no result table, no red error | **FAIL (environmental)** | Run enabled, no Cancel, no red error, but the output shows a job from another session: `This is a different run than the form above... Job ed443186a7f0 · COMBO_03_TRANSITION_B · SHORT · BTCUSDT · 1h`, with its result table and blotter. Shown correctly as foreign (see 1.46). Screenshot `P1_1.11_fresh_output.png` |

Notes on the fresh view (not separate cases): the chip next to the strategy says `Runs COMBO_02_V2 SHORT` and the pre-run summary says `Runs COMBO_02_V2 on the selected direction. COMBO_04_HTF is not used.` while Passed-auto actually starts the regime book. `Risk source: V1 production profile` is shown for a SHORT research book.

### Books

| # | Case | Result | Text |
|---|---|---|---|
| 1.12 | Long book | PASS | `Passed long strategies stay selected. Run uses COMBO_03_TRANSITION_B LONG.`; `Direction: LONG`; first optgroup `Working` = [`COMBO_02 V1`], v1 appears once |
| 1.13 | Hedge book | PASS | `Hedge strategy stays selected. Run uses COMBO_02 V2.1-A LONG and SHORT together.`; chip `Direction: LONG+SHORT (native)`; 0 LONG / 0 SHORT buttons; `RESEARCH ONLY` |
| 1.14a | Grid book | PASS | strategy select gone; `Grid backtest Binance USDT-M futures, long grid at 1x–5x ... Live orders stay off.` |
| 1.14b | Back to Short restores defaults | PASS | `PASSED_AUTO`, `Direction: SHORT`, coins BTC ETH SOL |
| 1.15 | Switching books does not mix coins/direction/strategy | **FAIL** | In Long, picked COMBO_02_V1 and removed ETH, SOL. After switching to Short: strategy back to Passed-auto and SHORT (correct) but **coins stayed `["BTC"]`**. `openTradeBook()` resets strategy and direction, never coins. Screenshot `P1_1.15_book_switch_coins_carry.png` |

### Strategy menu

| # | Case | Result | Text |
|---|---|---|---|
| 1.16 | Order Working, passed-auto, Passed, Research | PASS | Long: `Working[COMBO_02_V1] > Passed long strategies (auto) > Passed[COMBO_03_TRANSITION_B] > Research[COMBO_02_V2, COMBO_02_V2_1B, COMBO_01, COMBO_SD_STRUCTURE, CANDLE12_V2, LARGE_CAP_SWEEP_CHOCH, SMALL_CAP_VOLUME_BOS, OSE_A…, OSE_B…, OSE_D…]`; Short: `auto > Passed[8]`; Hedge: `auto > Passed[COMBO_02_V2_1_A]` |
| 1.17 | Named strategy turns passed-auto off; chip matches | PASS | passed chips gone; `Strategy: COMBO_02 · 1h SHORT` |
| 1.18 | COMBO_02 v1 in long book only | PASS | only `Long trades` contains `COMBO_02_V1` |
| 1.19 BTC | Frozen v1 BTC 1h LONG | PASS | `FROZEN V1 CONFIGURATION`, `Status: Paper forward-test`, `Direction: LONG`, `Setup TF: 1h`, 0 SHORT buttons, risk inputs disabled, chip `BTC v1 CORE`, `Effective risk: BTCUSDT 2% / $20`, `Risk source: V1 production profile`, `Production comparable: YES` |
| 1.19 ETH | same, ETH | PASS | chip `ETH v1 SECONDARY`, `ETHUSDT 2% / $20`, Production comparable YES |
| 1.19 SOL | same, SOL | PASS | chip `SOL v1 SECONDARY`, `SOLUSDT 2% / $20`, Production comparable YES |
| 1.22 | Research risk override | PASS | inputs enabled; `This risk differs from the frozen COMBO_02 v1 production profile...`; `Status: Research`; `Production comparable: NO` |
| 1.23 | 4h on v1 | PASS | `4h setup is not the frozen COMBO_02 v1 1h production configuration. Results are research-only...`; `Reasons: setup_timeframe_mismatch`; Production comparable NO |
| 1.24 | V2.1-A note | PASS | `V2.1-A runs native LONG+SHORT (direction filter off). COMBO_04 SHORT path is not used.`; no LONG/SHORT buttons; `RESEARCH ONLY` |
| 1.25 | Passed cap-book id kept | PASS | summary `Strategy: COMBO_02 · 1h SHORT · RESEARCH ONLY`; no strategy line names COMBO_04_HTF |
| 1.26 | SD structure | PASS | `Structure variant` select appears; coins reset from BTC ETH SOL BNB to `BTC ETH SOL`; note `Auto timeframe is on...`; after SHORT: `Strategy: SD + CHoCH + BOS · COMBO_SD_C · reversal · B + same-slot RVOL 1.5`, notice `SHORT uses the same supply/demand structure variant. It does not switch to COMBO_04_HTF.` |

### Coins, timeframes, period, risk

| # | Case | Result | Text |
|---|---|---|---|
| 1.27 | Add / remove chips | PASS | added BNB, NEAR → `BTC ETH SOL BNB NEAR`; removed → BNB back under Large cap, NEAR back under Mid cap |
| 1.28 | Previous / Next coin | PASS | `Coin 1 of 3: BTC → 2 ETH → 3 SOL → 1 BTC`, Previous from BTC → `Coin 3 of 3: SOL`; one accent chip (SOL); `Run includes every coin in this row...` |
| 1.29 | Typed INJ | PASS | placeholder `Type a large or mid cap coin, e.g. INJ`; typing `inj` shows `INJ`; Add adds INJ and clears the box |
| 1.30 | PEPE | PASS | not added; `PEPEUSDT is not in the large-cap or mid-cap book.`; selection unchanged |
| 1.31 | `INJ, PEPE` (Enter) | PASS | coins `BTC ETH SOL INJ`; notice names PEPEUSDT |
| 1.32 | Empty Add | PASS | nothing changed |
| 1.33a | Screener handoff, none in book | PASS | picked PONSUSDT, ENAUSDT on `/` → `No screener coin was loaded: PONSUSDT, ENAUSDT are not in the large-cap or mid-cap book. The current coins are unchanged.` |
| 1.33b | Screener handoff, mixed | PASS | picked TRXUSDT + PONSUSDT → coins `TRX`; notice `PONSUSDT is not in the large-cap or mid-cap book.`; manual timeframe turned back to auto (`1 screener coin loaded. 1 skipped...`) |
| 1.34 | TF roles, manual/auto | PASS | `15m research-only`, `1h v1 setup timeframe`, `4h HTF context`; click → `Timeframe is manual...`; `Use auto timeframe` → `Auto timeframe is on...` |
| 1.35 | 5,760-bar day label | PASS | 15m `≈ 60 days at 15m`, 1h `≈ 240 days at 1h`, 4h `≈ 960 days at 4h` |
| 1.36a | DB-tail copy and presets | PASS | `Uses the latest N available candles`, `Does not represent a calendar-year filter`; presets (1,200 / 2,880 / 5,760 / 8,640 bars) + `lookback-max`. Labels are day counts at the current timeframe, not `12d/30d/60d/90d`: at 1h they read `≈ 50 / 120 / 240 / 360 days`; only at 15m are they 12.5 / 30 / 60 / 90 days. |
| 1.36b | Custom bars | PASS | `12a3x4` → `1234`; `Bars requested: 1,234`; `Approximate duration: 51.4 days at 1h`; line `DB-tail mode · Bars requested: 1,234 · ≈ 51.4 days at 1h · 3×1 cells` |
| 1.36c | "12d" preset at 1h | PASS (note) | `Bars requested: 1,200`, `Approximate duration: 50 days at 1h`. **The preset the brief calls "DB-tail 12d" is 1,200 bars, which is ≈ 50 days at 1h.** All Part 2 runs use this preset, so their window is ≈ 50 days, not 12. |
| 1.37a | Calendar copy and presets | PASS | `Uses only candles inside the requested UTC range`; 2023, 2024, 2025, 2026 YTD; two date inputs |
| 1.37b | Last 365 days | PASS | start `2025-10-16`, end `2026-10-16` (UTC today); highlighted; after editing the start it is no longer highlighted |
| 1.38 | Calendar, both dates empty | PASS | `Custom dates mode needs a start and/or end day (UTC)`; 0 start POSTs |
| 1.39 | Outside-history warning link | PASS | `/ohlcv-history?symbols=BTCUSDT%2CETHUSDT%2CSOLUSDT&tfs=1h&until=2020-01-01` |
| 1.40a | Principal 1000 production risk | PASS | `BTCUSDT core — 2% / $20`, `ETHUSDT secondary — 2% / $20`, `SOLUSDT secondary — 2% / $20` |
| 1.40b | Risk $ = round(principal × % / 100), min $1 | PASS | 1000×1.5 → $15; 1234×1.5 → $19; 1000×0.01 → $1; 250×3 → $8 |
| 1.41 | Frozen v1 keeps inputs disabled, V1 production profile | PASS | see 1.19 |
| 1.42a | Fee block | PASS | `Entry type: MARKET or LIMIT_RETEST`, `Entry fee type: Market entry → taker fee`, `Exit type: MARKET`, `Exit fee type: Market exit → taker fee`, `Fee basis: executed notional`, `Fee convention: negative cost` |
| 1.42b | Leverage clamps below 1 up to 1 | **FAIL** | `0.5 → 1`, `-3 → 1`, but **`0 → 2`** (`Math.max(1, Number(v) \|\| 2)` treats 0 as empty). Screenshot `P1_1.42b_leverage_zero.png` |
| 1.42c | Fee defaults | PASS | 0.04 / 0.02 |

### Run and output (the one Part 1 run)

The run: Short book, Passed strategies (auto) as selected, BTC only, 1h, 1,200-bar preset. It posted
`POST /api/research/regime-backtest/start {"symbols":["BTCUSDT"],"limit":1200}` (no backtest job). It finished `done` after about 8 minutes. During that time the backend answered other requests in about 10 s.
Regime result: 5 trades, 2 wins, 3 losses, net **+$13.73**, gross +$21.51, fees −$7.78, max DD $33.12 (3.3 %). By strategy: COMBO_03_TRANSITION SHORT +$35.44 (1), COMBO_02_V2 SHORT −$0.29 (3), COMBO_01 SHORT −$21.43 (1); daily regime BULLISH 396 bars, BEARISH 356, NEUTRAL 528.

| # | Case | Result | Text |
|---|---|---|---|
| 1.43a | Passed-auto on short starts the regime book | PASS | POST body above |
| 1.43b | While running: Run disabled / `Running…` / `%`, Cancel shown, progress block | **FAIL** | for the whole regime run the button stayed `Run backtest`, enabled, with 0 Cancel buttons and no progress block; only `Regime book results · running · regime book` showed. The regime run does not use the backtest job store. Screenshot `P1_1.43b_regime_run_no_progress.png` |
| 1.43c | Cancel once, button returns to Run backtest | **FAIL** | Cancel never appeared, so it could not be clicked; there is no cancel route for the regime book |
| 1.44 | Finished matching job shows Backtest output (coin, principal, net, ending equity) | **FAIL (regime path)** | the regime result shows only in the `Regime book results` box. `Backtest output · BTC` kept showing the foreign job `ed443186a7f0` (COMBO_03_TRANSITION_B SHORT, +$153.56). Screenshot `P1_1.44_after_regime_run.png` |
| 1.46 | Foreign job shows backtest-foreign-run | PASS | `This is a different run than the form above. It was not started by this form's Run button. Job ed443186a7f0 ...` |
| 1.47 | Causal title only for CAUSAL_NEXT_OPEN | PASS (foreign job) | `Trade blotter — BTCUSDT · 1h · SHORT`, no `causal-trade-detail` (pnl_model is legacy) |
| 1.48a | Win rate counts net > 0; max DD from trades | PASS | stats `win rate (net > 0) 60% · max DD $21.45`; recomputed from the 10 blotter nets: 60 %, $21.45 |
| 1.48b | Virtualized blotter first and last | PASS | first `1`, last `10` = `closed 10` |
| 1.48c | Market-context tooltip | PASS | `volume 0.80x its 20-bar average · close at 67% of the prior 24-bar range · bar 2026-03-03T02:00:00+00:00` |
| 1.50 | In-sample warning only when payload says in-sample | **FAIL** | `backtest-in-sample-warning` is shown, and no job or row field carries `in_sample`; `BacktestPanel.tsx` renders it unconditionally (live file lines 2276–2284). Screenshot `P1_1.50_in_sample_unconditional.png` |

Not reachable in Part 1 (one run allowed, and it was the regime book): progress text and Cancel for a named-strategy job, output of a finished matching job, net colour, Previous/Next coin with result cells (all Part 2 runs are BTC only), header status after a later form change, `backtest-run-mismatch`, LONG-v1-shown-as-SHORT guard, empty-trade em dashes, `candle12-v2-warnings`. These are checked on the Part 2 runs where possible (addendum below). `backtest-run-mismatch` and the "LONG v1 job shown for a SHORT request" guard cannot be produced from the screen without a backend that returns the wrong job; they were not reached. CANDLE12_V3 rows were not produced (sealed), so "no candle12-v2-warnings on V3 rows" was not reached.

---

## Part 2 — Every strategy, then why the losses happened

### Inventory (written before any Part 2 run)

Sources, read 2026-10-17 from the live backend: `GET /api/research/strategies` (catalog), `GET /api/research/strategies/rank` (rank file `backend/scripts/strategy_rank/ranked.json`, written 2026-10-12 09:23 UTC), and `BACKTEST_STRATEGY_OPTIONS` + `LONG/SHORT/HEDGE_STRATEGY_IDS` in `frontend/src/backtest/v1Config.ts`.

Counts:
- **Catalog: 20 cards** (Scalping 5, Intraday 4, Swing 11). Statuses: WORKING 1, RESEARCH 17, BASELINE 1, EXPERIMENTAL 1.
- **Rank board: 48 rows in 4 tabs** (Day trading 1h: 10, Scalp 5m·15m: 1, Swing 4h·1d: 11, Grid: 26). 13 distinct non-grid strategy ids (11 rows of them say "Not in catalog": CANDLE12_V2, LARGE_CAP_SWEEP_CHOCH, COMBO_01 ×3, OSE_A, OSE_B, OSE_D, SMALL_CAP_VOLUME_BOS, COMBO_02_V2_1B ×2) plus 6 grid ids (GTB-ARI-4/8/12, GTB-GEO-4/8/12, all "Not in catalog"; grid is a separate book). Hidden by the rank file: 8 duplicate and 132 failure rows (counts only, not listed).
- **Backtest menu: 21 entries, 17 distinct ids** — Long 12, Short 8, Hedge 1. `CANDLE12_V3_EXEC_FIX` is defined in `BACKTEST_STRATEGY_OPTIONS` but is in no book list, so it is not on the menu. `COMBO_SD_STRUCTURE` has 10 variants (COMBO_SD_A, B, C (default), C12, C20, D, E, F, C_FRESH, LIQ).
- **Union: 37 ids** (20 catalog + 11 non-catalog strategy ids + 6 grid ids); the SD variants are counted under COMBO_SD_STRUCTURE.

| id | catalog status | horizon | direction | setup TF | rank row(s) | backtest book | note |
|---|---|---|---|---|---|---|---|
| COMBO_02_V1 | WORKING | swing | LONG | 1h | Day #3 `COMBO_02 LONG 1h`; Swing #11 `COMBO_02 LONG 4h` (both map to this card) | long | primary_working |
| COMBO_02_V1_CLOSED_HTF | RESEARCH | swing | LONG | 1h | not ranked as its own row (`COMBO_02_CLOSED_HTF` is listed as identical to COMBO_02) | absent (menu has `COMBO_02_CLOSED_HTF`, short) | parent COMBO_02_V1 |
| COMBO_02_V2 | RESEARCH | swing | LONG_SHORT | 1h | Day #8 LONG 1h; Swing #5 SHORT 1d | long, short | parent COMBO_02_V1 |
| COMBO_02_V2_1_A | RESEARCH | swing | LONG_SHORT | 1h | Swing #2 SHORT 1d, #6 LONG 1d (alias COMBO_02_V2_1A identical) | hedge | parent COMBO_02_V2 |
| COMBO_02_V1_1_RISK_CONTROLLED | RESEARCH | swing | LONG | 1h | not ranked | absent | parent COMBO_02_V1 |
| COMBO_02_SHORT_RESEARCH | RESEARCH | swing | SHORT | 1h | Swing #8 `COMBO_02 SHORT 1d` | absent (menu id `COMBO_02`, short) | |
| COMBO_04_HTF_SHORT | RESEARCH | swing | SHORT | 1h | not ranked | absent | |
| COMBO_03 | RESEARCH | intraday | LONG / SHORT | 15m or 1h | not ranked | absent | |
| COMBO_03_TRANSITION | RESEARCH | swing | LONG / SHORT | 1h (+15m confirm) | not ranked | short | |
| COMBO_03_TRANSITION_B | RESEARCH | swing | LONG / SHORT | 1h | Day #9 SHORT 1h; Swing #1 SHORT 4h, #3 LONG 4h | long, short | |
| COMBO_03_TRANSITION_C | RESEARCH | swing | LONG / SHORT | 1h (+15m confirm) | not ranked | absent | |
| COMBO_04 | RESEARCH | intraday | LONG / SHORT | 15m or 1h | not ranked | absent | |
| COMBO_02_LOCAL | BASELINE | swing | LONG | 1h / 15m | not its own row ("nearly the same as" COMBO_01) | short | menu label `COMBO_02_LOCAL · 1h SHORT` |
| PATH_B_EXPERIMENTAL | EXPERIMENTAL | intraday | LONG | 15m (+ MTF) | not ranked | absent | |
| INTRADAY_BOS_SD_V1 | RESEARCH | intraday | LONG / SHORT | 5m | Scalp #1 `INTRADAY_BOS_SD_V1.C LONG 5m` (variant C) | absent | |
| scalp_bos_retest_v1 | RESEARCH | scalping | LONG / SHORT | 1m | not ranked | absent | |
| scalp_sweep_reclaim_v1:reclaim_only | RESEARCH | scalping | LONG / SHORT | 1m | not ranked | absent | |
| scalp_sweep_reclaim_v1:reclaim_choch | RESEARCH | scalping | LONG / SHORT | 1m | not ranked | absent | |
| scalp_vwap_pullback_v1 | RESEARCH | scalping | LONG / SHORT | 1m | not ranked | absent | |
| scalp_compression_retest_v1 | RESEARCH | scalping | LONG / SHORT | 1m | not ranked | absent | registered only |
| COMBO_02 | — (cards COMBO_02_V1 / COMBO_02_SHORT_RESEARCH) | — | menu: SHORT | 1h | Day #3, Swing #8, #11 | short | rank rows carry catalog_id |
| COMBO_02_CLOSED_HTF | — | — | menu: SHORT | 1h | alias only (identical to COMBO_02 in 80/80 cells) | short | |
| COMBO_02_V2_1B | not in catalog | — | menu: LONG, SHORT | 1h | Swing #7 SHORT 1d, #10 LONG 1d | long, short | **Not in catalog** |
| COMBO_01 | not in catalog | — | menu: LONG, SHORT | 1h | Day #4 LONG 1h; Swing #4 SHORT 1d, #9 LONG 1d | long, short | **Not in catalog** |
| COMBO_SD_STRUCTURE (+10 variants) | not in catalog | — | LONG (SHORT button available) | auto (1h) | not ranked | long | no card, no rank row |
| CANDLE12_V2 | not in catalog | — | LONG | 1h (engine 5m/15m/1h) | Day #1 LONG 1h | long | **Not in catalog**; frozen BTC 1h book |
| CANDLE12_V3_EXEC_FIX | not in catalog | — | LONG | 1h (engine 5m/15m/1h) | not ranked | absent (option defined, in no book) | OOS-2 sealed |
| LARGE_CAP_SWEEP_CHOCH | not in catalog | — | LONG | 1h (engine 5m/15m/1h) | Day #2 LONG 1h | long | **Not in catalog** |
| SMALL_CAP_VOLUME_BOS | not in catalog | — | LONG | 1h (engine 5m/15m/1h) | Day #10 LONG 1h | long | **Not in catalog** |
| OSE_A_corrected_baseline | not in catalog | — | LONG | 1h (engine 15m/1h) | Day #5 LONG 1h | long | **Not in catalog** |
| OSE_B_acceptance | not in catalog | — | LONG | 1h (engine 15m/1h) | Day #6 LONG 1h | long | **Not in catalog** |
| OSE_D_faster_confirmation | not in catalog | — | LONG | 1h (engine 15m/1h) | Day #7 LONG 1h | long | **Not in catalog** |
| GTB-ARI-4 / 8 / 12, GTB-GEO-4 / 8 / 12 | not in catalog | — | LONG | 5m–1d | Grid #1–#26 | grid (separate) | grid book, not a catalog strategy |

### Strategies screen, card by card and row by row

Read-only page: 0 POST requests to `/api` during the whole click-through. Result: **91 of 91 checks PASS** (20 cards, 48 rank rows, 23 other cases). Failure modes were tested in the browser only, by intercepting the two GET requests (no backend change): a 500 on the rank file, a 500 on the catalog after the rank board had loaded, and a catalog without scalping cards.

Notes that are not failures:
- The detail pane shows `horizon · setup timeframe` but not the direction; direction is on the card (`1h · LONG`). Both match the API.
- The detail pane does not render `parent_strategy_id`; COMBO_02_V1_CLOSED_HTF and COMBO_02_V1_1_RISK_CONTROLLED name COMBO_02_V1 in their summaries, so the parent is visible as text.
- Risk-controlled card: `Risk: BTC 1.5% / ETH 0.5% / SOL 0.5% · cluster ≤4% equity · max 2 concurrent · e.g. $1000 → $15 = 1R`.
- COMBO_02_V1: `Risk: 2% of equity per trade (1R) · e.g. $1000 → $20 = 1R`; header `Logic freeze: v1-combo02-long-htf`, `Code freeze: pre-research-freeze-20261005`; sizing block `Equity $1000`, `Risk 2% = $20 (1R)`.
- Rank rows without a catalog id open `strategy-rank-detail` with `This id is not in the strategy catalog, so there is no gates or example pane for it.` and no card selected. Rows with a catalog id select that card (accent border). No coin cell is shown as Pass with net < 0 or with fail reasons. No row in the current rank file is `do_not_use` or `alias_of`; the 6 rows that carry `aliases` show them.
- Mis-match between menu and card that is not a label error: COMBO_02_LOCAL's card says direction `LONG`, while the backtest menu offers it only as `COMBO_02_LOCAL · 1h SHORT` (short book). Both are true (the card describes the baseline long A/B; the short book runs the same combination short); see Part 3.

| card | result | evidence |
|---|---|---|
| `COMBO_02_V1` | PASS | accent=true accentCards=1 badge=WORKING name="COMBO_02 v1 — HTF-gated LONG" line="SWING · 1H" summary=true gates=true(4) doNot=true(4) links=[Paper blotter:/paper:link ; Strategy backtest:/backtest:link ; Screener v1 watcher:/:link] linkOk=true example: PASS steps 7/7, fail-closed 4 |
| `COMBO_02_V1_CLOSED_HTF` | PASS | accent=true accentCards=1 badge=RESEARCH name="COMBO_02 v1 closed-HTF — fully closed 4h/1h" line="SWING · 1H" summary=true gates=true(4) doNot=true(3) links=[Docs:docs/combo02_v1_closed_htf.md:text ; Comparison audit:backend/reports/combo02_v1_closed_htf_compare:text] linkOk=true example: PASS steps 7/7, fail-closed 4 |
| `COMBO_02_V2` | PASS | accent=true accentCards=1 badge=RESEARCH name="COMBO_02 v2 — adaptive 3-regime" line="SWING · 1H" summary=true gates=true(5) doNot=true(3) links=[Research backtest script:backend/scripts/run_combo02_v2_january_compare.py:text] linkOk=true example: example keys label,regime,playbook,direction,event,confirmation,status (no title/s |
| `COMBO_02_V2_1_A` | PASS | accent=true accentCards=1 badge=RESEARCH name="COMBO_02 V2.1-A — CHOPPY WAIT" line="SWING · 1H" summary=true gates=true(5) doNot=true(3) links=[Strategy Backtest:/backtest:link ; V2.1 research compare:backend/reports/combo02_v21_variant_compare:text] linkOk=true example: example keys label,regime,playbook,direction,status,note ( |
| `COMBO_02_V1_1_RISK_CONTROLLED` | PASS | accent=true accentCards=1 badge=RESEARCH name="COMBO_02 v1.1 — risk-controlled LONG" line="SWING · 1H" summary=true gates=true(9) doNot=true(3) links=[Docs:docs/combo02_v1_1_risk_controlled.md:text ; OOS reports:backend/reports/combo02_v1_1_risk_controlled_oos:text] linkOk=true example: PASS steps 7/7, fail-closed 4 |
| `COMBO_02_SHORT_RESEARCH` | PASS | accent=true accentCards=1 badge=RESEARCH name="COMBO_02 SHORT — research only" line="SWING · 1H" summary=true gates=true(3) doNot=true(0) links=[BOS Research:/bos-research:link] linkOk=true example: PASS steps 0/0, fail-closed 0, steps 4 |
| `COMBO_04_HTF_SHORT` | PASS | accent=true accentCards=1 badge=RESEARCH name="COMBO_04_HTF SHORT — reverse of long (research)" line="SWING · 1H" summary=true gates=true(6) doNot=true(0) links=[Strategy Backtest:/backtest:link ; BOS Research:/bos-research:link] linkOk=true example: PASS steps 0/0, fail-closed 0, steps 4 |
| `COMBO_03` | PASS | accent=true accentCards=1 badge=RESEARCH name="COMBO_03 — Trend + BOS + Pullback" line="INTRADAY · 15M OR 1H" summary=true gates=true(3) doNot=true(0) links=[BOS Research:/bos-research:link] linkOk=true example: PASS steps 0/0, fail-closed 0, steps 4 |
| `COMBO_03_TRANSITION` | PASS | accent=true accentCards=1 badge=RESEARCH name="COMBO_03_TRANSITION — CHOPPY → breakout research" line="SWING · 1H (+15M CONFIRM)" summary=true gates=true(5) doNot=true(4) links=[BOS Research:/bos-research:link ; Validation script:backend/scripts/run_combo03_transition_validation.py:text] linkOk=true example: PASS steps 0/0, fail |
| `COMBO_03_TRANSITION_B` | PASS | accent=true accentCards=1 badge=RESEARCH name="COMBO_03_TRANSITION_B — no mandatory 15M (comparison)" line="SWING · 1H" summary=true gates=true(5) doNot=true(2) links=[] linkOk=true example: no example |
| `COMBO_03_TRANSITION_C` | PASS | accent=true accentCards=1 badge=RESEARCH name="COMBO_03_TRANSITION_C — + displacement (comparison)" line="SWING · 1H (+15M CONFIRM)" summary=true gates=true(5) doNot=true(2) links=[] linkOk=true example: no example |
| `COMBO_04` | PASS | accent=true accentCards=1 badge=RESEARCH name="COMBO_04 — Trend + BOS + Impulse + Pullback" line="INTRADAY · 15M OR 1H" summary=true gates=true(4) doNot=true(0) links=[BOS Research:/bos-research:link] linkOk=true example: PASS steps 0/0, fail-closed 0, steps 4 |
| `COMBO_02_LOCAL` | PASS | accent=true accentCards=1 badge=BASELINE name="COMBO_02_LOCAL — setup-TF only (A/B)" line="SWING · 1H / 15M" summary=true gates=true(3) doNot=true(0) links=[BOS Research:/bos-research:link] linkOk=true example: PASS steps 0/0, fail-closed 0, steps 3 |
| `PATH_B_EXPERIMENTAL` | PASS | accent=true accentCards=1 badge=EXPERIMENTAL name="Path B — full setup (experimental)" line="INTRADAY · 15M (+ MTF)" summary=true gates=true(6) doNot=true(0) links=[Paper (Path B label):/paper:link ; Screener Trade Plan:/:link] linkOk=true example: PASS steps 0/0, fail-closed 0, steps 4 |
| `INTRADAY_BOS_SD_V1` | PASS | accent=true accentCards=1 badge=RESEARCH name="Intraday BOS / CHoCH / zones" line="INTRADAY · 5M" summary=true gates=true(0) doNot=true(2) links=[Intraday status:/intraday-bos-sd:link ; Research script:backend/scripts/run_intraday_bos_sd.py:text] linkOk=true example: no example |
| `scalp_bos_retest_v1` | PASS | accent=true accentCards=1 badge=RESEARCH name="Scalp BOS retest" line="SCALPING · 1M" summary=true gates=true(0) doNot=true(0) links=[Research script:backend/scripts/run_scalping_research.py:text] linkOk=true example: no example |
| `scalp_sweep_reclaim_v1:reclaim_only` | PASS | accent=true accentCards=1 badge=RESEARCH name="Scalp sweep reclaim" line="SCALPING · 1M" summary=true gates=true(0) doNot=true(0) links=[Research script:backend/scripts/run_scalping_research.py:text] linkOk=true example: no example |
| `scalp_sweep_reclaim_v1:reclaim_choch` | PASS | accent=true accentCards=1 badge=RESEARCH name="Scalp sweep reclaim + CHoCH" line="SCALPING · 1M" summary=true gates=true(0) doNot=true(0) links=[Research script:backend/scripts/run_scalping_research.py:text] linkOk=true example: no example |
| `scalp_vwap_pullback_v1` | PASS | accent=true accentCards=1 badge=RESEARCH name="Scalp VWAP pullback" line="SCALPING · 1M" summary=true gates=true(0) doNot=true(0) links=[Research script:backend/scripts/run_scalping_research.py:text] linkOk=true example: no example |
| `scalp_compression_retest_v1` | PASS | accent=true accentCards=1 badge=RESEARCH name="Scalp compression retest" line="SCALPING · 1M" summary=true gates=true(0) doNot=true(1) links=[] linkOk=true example: no example |

| rank row | result | evidence |
|---|---|---|
| `day|CANDLE12_V2|LONG|1h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `day|LARGE_CAP_SWEEP_CHOCH|LONG|1h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `day|COMBO_02|LONG|1h` | PASS | detail=true inView=true card COMBO_02_V1 selected=true cells=10/10 badCells=[] aliases shown=true |
| `day|COMBO_01|LONG|1h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[] aliases shown=true |
| `day|OSE_A_corrected_baseline|LONG|1h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `day|OSE_B_acceptance|LONG|1h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `day|OSE_D_faster_confirmation|LONG|1h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `day|COMBO_02_V2|LONG|1h` | PASS | detail=true inView=true card COMBO_02_V2 selected=true cells=3/3 badCells=[]  |
| `day|COMBO_03_TRANSITION_B|SHORT|1h` | PASS | detail=true inView=true card COMBO_03_TRANSITION_B selected=true cells=10/10 badCells=[]  |
| `day|SMALL_CAP_VOLUME_BOS|LONG|1h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `scalp|INTRADAY_BOS_SD_V1.C|LONG|5m` | PASS | detail=true inView=true card INTRADAY_BOS_SD_V1 selected=true cells=10/10 badCells=[]  |
| `swing|COMBO_03_TRANSITION_B|SHORT|4h` | PASS | detail=true inView=true card COMBO_03_TRANSITION_B selected=true cells=10/10 badCells=[]  |
| `swing|COMBO_02_V2_1_A|SHORT|1d` | PASS | detail=true inView=true card COMBO_02_V2_1_A selected=true cells=10/10 badCells=[] aliases shown=true |
| `swing|COMBO_03_TRANSITION_B|LONG|4h` | PASS | detail=true inView=true card COMBO_03_TRANSITION_B selected=true cells=10/10 badCells=[]  |
| `swing|COMBO_01|SHORT|1d` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[] aliases shown=true |
| `swing|COMBO_02_V2|SHORT|1d` | PASS | detail=true inView=true card COMBO_02_V2 selected=true cells=10/10 badCells=[]  |
| `swing|COMBO_02_V2_1_A|LONG|1d` | PASS | detail=true inView=true card COMBO_02_V2_1_A selected=true cells=10/10 badCells=[] aliases shown=true |
| `swing|COMBO_02_V2_1B|SHORT|1d` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `swing|COMBO_02|SHORT|1d` | PASS | detail=true inView=true card COMBO_02_SHORT_RESEARCH selected=true cells=10/10 badCells=[] aliases shown=true |
| `swing|COMBO_01|LONG|1d` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[] aliases shown=true |
| `swing|COMBO_02_V2_1B|LONG|1d` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `swing|COMBO_02|LONG|4h` | PASS | detail=true inView=true card COMBO_02_V1 selected=true cells=10/10 badCells=[] aliases shown=true |
| `grid|GTB-ARI-8|LONG|15m` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-GEO-12|LONG|1h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-GEO-12|LONG|15m` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-GEO-8|LONG|15m` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-ARI-12|LONG|15m` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-GEO-4|LONG|15m` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-ARI-4|LONG|15m` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-GEO-8|LONG|1h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-ARI-12|LONG|1h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-GEO-4|LONG|5m` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-ARI-4|LONG|5m` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-ARI-12|LONG|4h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-GEO-12|LONG|4h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-GEO-4|LONG|1h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-ARI-4|LONG|1h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-ARI-8|LONG|1h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-ARI-8|LONG|4h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-GEO-8|LONG|1d` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-GEO-8|LONG|4h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-ARI-4|LONG|1d` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-ARI-12|LONG|1d` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-GEO-12|LONG|1d` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-ARI-8|LONG|1d` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-GEO-4|LONG|4h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-GEO-4|LONG|1d` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |
| `grid|GTB-ARI-4|LONG|4h` | PASS | detail=true inView=true not-in-catalog note=true; rowBadge=true cells=10/10 badCells=[]  |

| other screen case | result | evidence |
|---|---|---|
| Header logic-freeze and code-freeze tags | PASS | Strategies /  / Historical / paper research only. Not a claim of future profitability. UI ENTRY candidates are never automatic BUY orders. /  / Logic freeze: v1-combo02-long-htf / Code freeze: pre-research-freeze-20261005 |
| Catalog count | PASS | CATALOG (20) |
| Group scalping | PASS | SCALPING 1m · 5 :: scalp_bos_retest_v1, scalp_sweep_reclaim_v1:reclaim_only, scalp_sweep_reclaim_v1:reclaim_choch, scalp_vwap_pullback_v1, scalp_compression_retest_v1 |
| Group intraday | PASS | INTRADAY 5m · 15m · 4 :: COMBO_03, COMBO_04, PATH_B_EXPERIMENTAL, INTRADAY_BOS_SD_V1 |
| Group swing | PASS | SWING 1h · 4h · 11 :: COMBO_02_V1, COMBO_02_V1_CLOSED_HTF, COMBO_02_V2, COMBO_02_V2_1_A, COMBO_02_V1_1_RISK_CONTROLLED, COMBO_02_SHORT_RESEARCH, COMBO_04_HTF_SHORT, COMBO_03_TRANSITION, COMBO_03_TRANSITION_B, COMBO_03_TRANSITION_C, COMBO_02_LOCAL |
| Required group placement | PASS | scalp_bos_retest_v1→scalping (want scalping); INTRADAY_BOS_SD_V1→intraday (want intraday); PATH_B_EXPERIMENTAL→intraday (want intraday); COMBO_02_V1→swing (want swing); COMBO_03_TRANSITION→swing (want swing) |
| Every card in the group of its API horizon | PASS | misplaced=[] |
| COMBO_02_V1 WORKING LONG 1h, risk line, sizing $1000 / $20 | PASS | Risk: 2% of equity per trade (1R) · e.g. $1000 → $20 = 1R / sizing: Equity	$1000 Risk	2% = $20 (1R) / card line "1h · LONG" |
| COMBO_02_V2_1_A stays RESEARCH | PASS | badge=RESEARCH |
| COMBO_02_LOCAL stays BASELINE | PASS | badge=BASELINE |
| COMBO_02_V1_CLOSED_HTF stays RESEARCH | PASS | badge=RESEARCH parent_strategy_id=COMBO_02_V1; summary names COMBO_02_V1=true; detail renders the parent field=true |
| COMBO_02_V1_1_RISK_CONTROLLED stays RESEARCH | PASS | badge=RESEARCH parent_strategy_id=COMBO_02_V1; summary names COMBO_02_V1=true; detail renders the parent field=true risk line="Risk: BTC 1.5% / ETH 0.5% / SOL 0.5% · cluster ≤4% equity · max 2 concurrent · e.g. $1000 → $15 = 1R" |
| In-app Where-to-run links navigate | PASS | COMBO_02_V1 /paper → /paper; COMBO_02_V1 /backtest → /backtest; COMBO_02_V1 / → /; COMBO_02_SHORT_RESEARCH /bos-research → /bos-research; INTRADAY_BOS_SD_V1 /intraday-bos-sd → /intraday-bos-sd |
| Rank window shows tested dates and in-sample note | PASS | dates="Tested 2026-02-14 to 2026-10-12" window="BACKTEST WINDOW / 2026-02-14 to 2026-10-12 (1d candles end 2026-10-08) / In-sample, one window, no out-of-sample test. / COINS (10, USDT PERPS) / BTC ETH BNB SOL XRP DOGE ADA TRX AVAX LINK / ACCOUNT / 1000 USD, 20 USD risk per trade, 2x / Intraday BOS/SD is 2.50 USD risk and 1x. Gr |
| Rank caveats | PASS | • Nothing here is production-ready on this evidence. / • In-sample only: one window, no out-of-sample test. / • COMBO_01 and COMBO_02_LOCAL are nearly the same strategy. COMBO_02_CLOSED_HTF matches COMBO_02. They are aliases, not separate winners. / • COMBO strategies fill at the signal close with no slippage. CANDLE12_V2 and th |
| Rank tab day shows every row | PASS | rows shown 10/10; hidden note="Left off this list: 30 that lost money or made no trades, 2 duplicates of another row." |
| Rank tab scalp shows every row | PASS | rows shown 1/1; hidden note="Left off this list: 31 that lost money or made no trades, 2 duplicates of another row." |
| Rank tab swing shows every row | PASS | rows shown 11/11; hidden note="Left off this list: 63 that lost money or made no trades, 4 duplicates of another row." |
| Rank tab grid shows every row | PASS | rows shown 26/26; hidden note="Left off this list: 8 that lost money or made no trades, 0 duplicates of another row." |
| Strategies page sends no POST | PASS | POST requests to /api: 0 [] |
| Rank failure shows strategy-rank-error, catalog still loads | PASS | error="Ranking unavailable. API /api/research/strategies/rank failed: 500 The catalog below is not affected." cards=20 |
| Catalog failure shows red error and keeps the loaded rank board | PASS | rank rows before=10 after=10; red=["API /api/research/strategies failed: 500"] |
| Empty group says "None in this catalog." | PASS | SCALPING / 1m · 0 /  / None in this catalog. |

### Small backtest per menu id

Every run was started from the screen (fresh page, book tab, strategy select, BTC only, 1h only, the 1,200-bar "12d" preset, principal 1000, leverage 2, taker 0.04, maker 0.02), one at a time, after confirming that neither the backtest job nor the regime job was running. Nothing was cancelled. The request body was captured from the browser and compared with the job's echo.

Window: every run evaluated 1,200 1h bars, **2026-08-27 20:00 → 2026-10-16 19:00 UTC (≈ 50 days)**, plus warm-up (100 bars for COMBO runs, 500 for OSE, 1,000 for CANDLE12_V2 and the multicap engines). The "12d" preset is 12.5 days only at 15m.

Sealed / skipped:
- **CANDLE12_V3_EXEC_FIX — SKIPPED, sealed window.** OOS-2 BTC 1h started 2026-10-16 00:00 UTC; this window's last bars are inside it, and V3 BTC performance is sealed until 2027-10-16. It is also not on the menu (option defined, in no book list). No V3 number was read anywhere.
- Regime Lab (`/regime-lab`) and the regime-book worktree study: not on the backtest menu, not run.
- LARGE_CAP_SWEEP_CHOCH was run on **BTC** only. Its forward contract (FWD-3) is ETH-only and was not touched; BTC was rejected in the LCSC study, so no BTC window is reserved.
- CANDLE12_V2 BTC 1h (frozen book, not sealed) was run unchanged; the last ~19 bars of its window are after 2026-10-16 00:00. It has 3 trades, the last entered 2026-10-12.

Every job: status `done`, no error, posted id = echoed id (no rewrite to COMBO_04_HTF anywhere), `paper_trade_created=false · live_trade_created=false · telegram_sent=false` on screen and in the API.

| book | menu id | posted strategy_id / combo_version / direction | echoed id / direction | job | trades | net $ | fees $ | gross $ | outcome mix | error | flags paper/live/tg | result on screen | backtest |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| long | COMBO_02_V1 | COMBO_02_V1 / v1 / LONG | COMBO_02_V1 / LONG | `f0379111307f` done | 4 | +34.41 | -4.17 | +38.58 | {'TP1': 2, 'SL': 2} | — | False/False/False | Trade blotter — BTCUSDT · 1h · LONG | PASS |
| long | COMBO_03_TRANSITION_B | COMBO_03_TRANSITION_B / combo03-transition-b / LONG | COMBO_03_TRANSITION_B / LONG | `2f4573998553` done | 0 | — | +0.00 | — | — | — | False/False/False | NO TRADES row | PASS |
| long | COMBO_01 | COMBO_01 / combo01-bos / LONG | COMBO_01 / LONG | `d908224ffc46` done | 13 | +1.09 | -17.37 | +18.47 | {'SL': 9, 'TP1': 4} | — | False/False/False | Trade blotter — BTCUSDT · 1h · LONG | PASS |
| long | COMBO_02_V2 | COMBO_02_V2 / v2-combo02-adaptive-3-regime / LONG | COMBO_02_V2 / LONG | `db7998cb160b` done | 18 | +34.78 | -26.26 | +61.04 | {'SL': 10, 'TP1': 8} | — | False/False/False | HIDDEN: backtest-run-mismatch | FAIL |
| long | COMBO_02_V2_1B | COMBO_02_V2_1B / v2.1-b-choppy-15m / LONG | COMBO_02_V2_1B / LONG | `bacd6692a1a0` done | 18 | +67.77 | -27.23 | +95.01 | {'SL': 10, 'TP1': 8} | — | False/False/False | HIDDEN: backtest-run-mismatch | FAIL |
| long | COMBO_SD_STRUCTURE | COMBO_SD_C / sd-structure / LONG | COMBO_SD_C / LONG | `e75001193bf9` done | 0 | — | +0.00 | — | — | — | False/False/False | NO TRADES row | PASS |
| long | CANDLE12_V2 | CANDLE12_V2 / engine-candle12 / LONG | CANDLE12_V2 / LONG | `3a442785adac` done | 3 | -2.50 | -3.18 | +0.68 | {'TP1': 1, 'SL': 2} | — | False/False/False | Trade blotter — BTCUSDT · 1h · LONG | PASS |
| long | LARGE_CAP_SWEEP_CHOCH | LARGE_CAP_SWEEP_CHOCH / engine-multicap / LONG | LARGE_CAP_SWEEP_CHOCH / LONG | `60adaee6870a` done | 13 | -97.03 | -15.27 | -81.77 | {'TP1': 3, 'SL': 10} | — | False/False/False | Trade blotter — BTCUSDT · 1h · LONG | PASS |
| long | SMALL_CAP_VOLUME_BOS | SMALL_CAP_VOLUME_BOS / engine-multicap / LONG | SMALL_CAP_VOLUME_BOS / LONG | `c9f76f8c96f2` done | 3 | +7.68 | -2.60 | +10.28 | {'SL': 2, 'TP1': 1} | — | False/False/False | Trade blotter — BTCUSDT · 1h · LONG | PASS |
| long | OSE_A_corrected_baseline | OSE_A_corrected_baseline / engine-ose / LONG (job echo; request capture lost, see note) | OSE_A_corrected_baseline / LONG | `5f7c755a606a` done | 30 | -75.82 | -35.18 | -40.64 | {'timeout': 17, 'stop': 10, 'target': 3} | — | False/False/False | shown (view capture failed mid-blotter, see note) | PASS |
| long | OSE_B_acceptance | OSE_B_acceptance / engine-ose / LONG | OSE_B_acceptance / LONG | `ead2ca5c83f7` done | 23 | -76.02 | -25.01 | -51.00 | {'timeout': 13, 'stop': 7, 'target': 3} | — | False/False/False | Trade blotter — BTCUSDT · 1h · LONG | PASS |
| long | OSE_D_faster_confirmation | OSE_D_faster_confirmation / engine-ose / LONG | OSE_D_faster_confirmation / LONG | `c6e06e6ae2a1` done | 31 | -107.56 | -39.24 | -68.32 | {'stop': 13, 'timeout': 15, 'target': 3} | — | False/False/False | Trade blotter — BTCUSDT · 1h · LONG | PASS |
| short | COMBO_02_V2 | COMBO_02_V2 / v2-combo02-adaptive-3-regime / SHORT | COMBO_02_V2 / SHORT | `96421d7d9c57` done | 18 | +34.78 | -26.26 | +61.04 | {'SL': 10, 'TP1': 8} | — | False/False/False | HIDDEN: backtest-run-mismatch | FAIL |
| short | COMBO_02_V2_1B | COMBO_02_V2_1B / v2.1-b-choppy-15m / SHORT | COMBO_02_V2_1B / SHORT | `9f068c4ea1cd` done | 18 | +67.77 | -27.23 | +95.01 | {'SL': 10, 'TP1': 8} | — | False/False/False | HIDDEN: backtest-run-mismatch | FAIL |
| short | COMBO_02 | COMBO_02 / v1 / SHORT | COMBO_02 / SHORT | `22cd2c59d40b` done | 2 | -33.24 | -2.63 | -30.60 | {'SL': 2} | — | False/False/False | Trade blotter — BTCUSDT · 1h · SHORT | PASS |
| short | COMBO_02_CLOSED_HTF | COMBO_02_CLOSED_HTF / v1-combo02-long-htf-closed / SHORT | COMBO_02_CLOSED_HTF / SHORT | `8d38139aca04` done | 2 | -33.24 | -2.63 | -30.60 | {'SL': 2} | — | False/False/False | Trade blotter — BTCUSDT · 1h · SHORT | PASS |
| short | COMBO_03_TRANSITION | COMBO_03_TRANSITION / combo03-transition / SHORT | COMBO_03_TRANSITION / SHORT | `21ddd10a516a` done | 0 | — | +0.00 | — | — | — | False/False/False | NO TRADES row | PASS |
| short | COMBO_03_TRANSITION_B | COMBO_03_TRANSITION_B / combo03-transition-b / SHORT | COMBO_03_TRANSITION_B / SHORT | `7b024e832d35` done | 3 | +76.75 | -4.47 | +81.22 | {'TP1': 2, 'SL': 1} | — | False/False/False | Trade blotter — BTCUSDT · 1h · SHORT | PASS |
| short | COMBO_01 | COMBO_01 / combo01-bos / SHORT | COMBO_01 / SHORT | `2be8f60e2d1b` done | 8 | -39.81 | -11.08 | -28.73 | {'SL': 6, 'TP1': 2} | — | False/False/False | Trade blotter — BTCUSDT · 1h · SHORT | PASS |
| short | COMBO_02_LOCAL | COMBO_02_LOCAL / combo02-local / SHORT | COMBO_02_LOCAL / SHORT | `9cc2ac358bf1` done | 8 | -39.81 | -11.08 | -28.73 | {'SL': 6, 'TP1': 2} | — | False/False/False | Trade blotter — BTCUSDT · 1h · SHORT | PASS |
| hedge | Hedge (auto) | COMBO_02_V2_1_A / v2.1-a-choppy-wait / LONG | COMBO_02_V2_1_A / LONG | `c03d92658ffa` done | 15 | +173.52 | -23.45 | +196.97 | {'SL': 6, 'TP1': 9} | — | False/False/False | Trade blotter — BTCUSDT · 1h · LONG | PASS |

Notes on the table:
- **COMBO_02_V2 and COMBO_02_V2_1B FAIL on both books.** The request is LONG (long book) or SHORT (short book), but the job returns the same 18 trades either way: 9 LONG and 9 SHORT. The screen then hides the result with `backtest-run-mismatch`: `Result not shown: the job does not match what was requested, or contradicts itself. BTCUSDT 1h: trade_no 3 is SHORT in a LONG run. ...` (COMBO_02_V2 trades have no `trade_no`, so its lines read `trade_no ?`). The guard behaves as designed; the menu offers a single-direction run for an engine that trades both sides. Screenshots `runs/shots/long_COMBO_02_V2_page.png`, `runs/shots/short_COMBO_02_V2_1B_page.png`.
- COMBO_SD_STRUCTURE posts the default variant id `COMBO_SD_C` (by design: the variant select is part of that menu entry). Zero trades.
- COMBO_02 SHORT and COMBO_02_CLOSED_HTF SHORT gave identical blotters (2 trades, −$33.24), and so did COMBO_01 SHORT and COMBO_02_LOCAL SHORT (8 trades, −$39.81), matching the rank file's alias notes.
- Zero-trade runs (COMBO_03_TRANSITION_B LONG, COMBO_SD_STRUCTURE, COMBO_03_TRANSITION SHORT) are valid results: `NO TRADES — empty sample for selected filters`, `No closed trades for this cell`, net `—`, ending equity `—`.
- OSE_A: the job finished and the result was shown, but the script's capture of the blotter stopped at trade 20 (row click timed out), so the request body and overlay text for that run were not saved. The job echo (`OSE_A_corrected_baseline / engine-ose / LONG`) is what the screen sends for that option.
- A positive net here is one ≈ 50-day in-sample window on one coin. It is not evidence for promotion.

#### Part 1 addendum: run-and-output cases observed on these runs

| # | Case | Result | Text |
|---|---|---|---|
| 1.43d | Named-strategy job: Run disabled, `Running…` / `{pct}%`, Cancel only while running, progress line | PASS | COMBO_02_V1: button `2%`, disabled, Cancel present; `Running BTCUSDT 1h · CONTEXT_BUILD · rows loaded 1300 · trades 0 — safe to leave this tab`; after the job `Run backtest` enabled, no Cancel |
| 1.44 | Finished matching job shows Backtest output (coin, principal, net, ending equity); net colour | PASS | COMBO_02_V1: `Backtest output · BTC`, `+$34.41` green (`text-emerald-400`), ending `$1034.41`; LCSC `$-97.03` red, ending `$902.97`. Zero-trade runs show `—`, styled red |
| 1.44b | COMBO_02_V1 long, BTC 1h: Paper forward-test, Production comparable YES | PASS | header `Status: Paper forward-test`, `Production comparable: YES`, `Risk source: V1_PRODUCTION_PROFILE`; lead `Production comparable is YES.` |
| 1.45 | Header follows the job, not a later form change | PASS | form chip `Status: Research` → `Status: Paper forward-test` after picking COMBO_02_V1 + BTC; header stayed `Status: Research`, `Direction: LONG+SHORT (native)` (the V2.1-A job) |
| 1.47 | Causal blotter title, causal-trade-detail, optimistic block | NOT REACHED | the only CAUSAL_NEXT_OPEN rows (COMBO_02_V2) were hidden by the mismatch guard |
| 1.48d | Empty trades show em dashes, not NaN | PASS | zero-trade runs: net `—`, ending `—`, `NO TRADES`; no `NaN` |
| 1.48e | First and last trade numbers reachable | PASS | every shown blotter: first `1`, last = sample size (e.g. OSE_D 1…31) |
| 1.49 | candle12-v2-warnings on CANDLE12_V2 rows | PASS | `Avg R gross is net R. / market_context uses Candle-2 close. / HTF label is not applicable to CANDLE12_V2.`; absent on all other runs. V3 rows not produced (sealed) |
| 1.51 | A contradicting job hides the result table | PASS (guard) | COMBO_02_V2 / V2_1B, above |
| 1.52 | Trade chart overlay loads for every engine | **FAIL** | OSE_B and OSE_D: every clicked loss shows `#2 · undefined undefined · LONG No candles in window` — OSE trades carry no `symbol` / `timeframe`, so the overlay cannot load candles. Screenshots `runs/shots/long_OSE_B_acceptance_trade2.png`, `runs/shots/long_OSE_D_faster_confirmation_trade1.png` |
| 1.53 | Run lead TP / stop-out counts | **FAIL (OSE)** | OSE runs read `(0 TP, 0 stop-outs)` because OSE outcomes are `target` / `stop` / `timeout` (OSE_D has 3 target, 13 stop) |
| — | `backtest-in-sample-warning` | FAIL (same as 1.50) | shown on every result; the payload has no in-sample field |

### Loss review

Source: the job rows behind each shown blotter (the rows the blotter renders), saved from `GET /api/research/long-strategy/backtest/job` right after each run. Times are candle open times (UTC). Each losing row was clicked and the chart overlay read (overlay text quoted per trade; screenshots `runs/shots/<run>_trade<N>.png`). OSE overlays could not load (1.52), so for OSE trades the "way to TP1" figure comes from `/api/research/ohlcv-candles` (bars after entry through the exit bar), marked `(candles)`. For COMBO, multicap and CANDLE12 rows it is the row's `mfe` (also through the exit bar), marked `(row mfe)`.

Bucket rule (exactly one per loss, first match wins): 3 gross > 0 and net < 0 → 2 stop after ≥ 50 % of the way to TP1 → 4 structure WRONG or trend opposed → 5 long in the upper half / short in the lower half of the prior 24-bar range → 1 other stop before TP1 → 7 anything else. Bucket 6 needs a setup-invalidation field; no row has one, so it is never used. "R net" is the engine's realized-risk R; "R net $ basis" is net ÷ $20.

Not in the per-trade review (still in the table above): COMBO_02_V2 and COMBO_02_V2_1B on both books (blotter hidden by the mismatch guard; 10 losses each, −$169.62 and −$156.02), COMBO_02_CLOSED_HTF SHORT (identical to COMBO_02 SHORT) and COMBO_02_LOCAL SHORT (identical to COMBO_01 SHORT). Pooled totals count each distinct shown blotter once: 148 trades, 89 losses.


#### COMBO_02_V2_1_A (Hedge strategy (auto)) · hedge book

Blotter: `Trade blotter — BTCUSDT · 1h · LONG`. Trades 15, winners 9, losers 6, scratches 0; net +$173.52, fees −$23.45, gross +$196.97. Row totals: net +$173.52, fees −$23.45.

**Trade 1 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-08-29T19:00:00Z, exit 2026-08-29T21:00:00Z, hold 2 bars
- Entry 78,154.90, stop 78,043.62, TP1 79,138.70, exit 78,043.62, outcome SL
- Gross −$2.85, fees −$1.60, net −$4.45, R net -1.561, R net $ basis -0.222
- Market context: Structure correct · Uptrend · Normal volume · High zone · tooltip `volume 1.06x its 20-bar average · close at 93% of the prior 24-bar range · bar 2026-08-29T19:00:00+00:00`; structure CORRECT, trend BULLISH, volume 1.06x, range position 93%, decision bar 2026-08-29T19:00:00+00:00
- Stop distance 0.14% of entry; best move before exit 12% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #1 · BTCUSDT 1h · LONG 131 bars · entry/SL/TP1/exit key=n1 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · Normal volume · High zone"; close at 93% of the prior 24-bar range (upper half for a long); volume 1.06x the 20-bar average; stop 0.14% from entry.

**Trade 6 · BTCUSDT · 1h · LONG** — bucket 2
- Entry 2026-09-21T19:00:00Z, exit 2026-09-22T01:00:00Z, hold 6 bars
- Entry 86,454.30, stop 85,809.18, TP1 87,744.54, exit 85,809.18, outcome SL
- Gross −$17.05, fees −$1.82, net −$18.87, R net -1.107, R net $ basis -0.943
- Market context: Structure correct · Uptrend · Normal volume · High zone · tooltip `volume 1.16x its 20-bar average · close at 102% of the prior 24-bar range · bar 2026-09-21T19:00:00+00:00`; structure CORRECT, trend BULLISH, volume 1.16x, range position 102%, decision bar 2026-09-21T19:00:00+00:00
- Stop distance 0.75% of entry; best move before exit 72% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #6 · BTCUSDT 1h · LONG 135 bars · entry/SL/TP1/exit key=n6 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · Normal volume · High zone"; close at 102% of the prior 24-bar range (upper half for a long); volume 1.16x the 20-bar average; stop 0.75% from entry.

**Trade 7 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-23T04:00:00Z, exit 2026-09-23T05:00:00Z, hold 1 bars
- Entry 87,115.10, stop 86,484.26, TP1 88,376.77, exit 86,484.26, outcome SL
- Gross −$16.27, fees −$1.79, net −$18.06, R net -1.110, R net $ basis -0.903
- Market context: Structure correct · Uptrend · Normal volume · High zone · tooltip `volume 1.40x its 20-bar average · close at 108% of the prior 24-bar range · bar 2026-09-23T04:00:00+00:00`; structure CORRECT, trend BULLISH, volume 1.40x, range position 108%, decision bar 2026-09-23T04:00:00+00:00
- Stop distance 0.72% of entry; best move before exit 0% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #7 · BTCUSDT 1h · LONG 130 bars · entry/SL/TP1/exit key=n7 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · Normal volume · High zone"; close at 108% of the prior 24-bar range (upper half for a long); volume 1.40x the 20-bar average; stop 0.72% from entry.

**Trade 12 · BTCUSDT · 1h · SHORT** — bucket 5
- Entry 2026-10-08T13:00:00Z, exit 2026-10-08T14:00:00Z, hold 1 bars
- Entry 82,144.50, stop 82,695.09, TP1 80,882.00, exit 82,695.09, outcome SL
- Gross −$16.06, fees −$1.92, net −$17.98, R net -1.120, R net $ basis -0.899
- Market context: Structure correct · Downtrend · High volume · Low zone · tooltip `volume 2.84x its 20-bar average · close at 16% of the prior 24-bar range · bar 2026-10-08T13:00:00+00:00`; structure CORRECT, trend BEARISH, volume 2.84x, range position 16%, decision bar 2026-10-08T13:00:00+00:00
- Stop distance 0.67% of entry; best move before exit 4% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #12 · BTCUSDT 1h · SHORT 130 bars · entry/SL/TP1/exit key=n12 · green=entry · red=SL · blu`
- At entry the context cell read "Structure correct · Downtrend · High volume · Low zone"; close at 16% of the prior 24-bar range (lower half for a short); volume 2.84x the 20-bar average; stop 0.67% from entry.

**Trade 13 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-11T15:00:00Z, exit 2026-10-12T12:00:00Z, hold 21 bars
- Entry 83,868.70, stop 83,027.54, TP1 85,632.70, exit 83,027.54, outcome SL
- Gross −$20.00, fees −$1.59, net −$21.59, R net -1.079, R net $ basis -1.079
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `volume 9.13x its 20-bar average · close at 233% of the prior 24-bar range · bar 2026-10-11T15:00:00+00:00`; structure CORRECT, trend BULLISH, volume 9.13x, range position 233%, decision bar 2026-10-11T15:00:00+00:00
- Stop distance 1.00% of entry; best move before exit 27% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #13 · BTCUSDT 1h · LONG 150 bars · entry/SL/TP1/exit key=n13 · green=entry · red=SL · blue`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 233% of the prior 24-bar range (upper half for a long); volume 9.13x the 20-bar average; stop 1.00% from entry.

**Trade 15 · BTCUSDT · 1h · SHORT** — bucket 5
- Entry 2026-10-15T03:00:00Z, exit 2026-10-15T04:00:00Z, hold 1 bars
- Entry 80,879.00, stop 81,624.12, TP1 79,125.30, exit 81,624.12, outcome SL
- Gross −$20.00, fees −$1.74, net −$21.74, R net -1.087, R net $ basis -1.087
- Market context: Structure correct · Downtrend · Normal volume · Low zone · tooltip `volume 0.91x its 20-bar average · close at 18% of the prior 24-bar range · bar 2026-10-15T03:00:00+00:00`; structure CORRECT, trend BEARISH, volume 0.91x, range position 18%, decision bar 2026-10-15T03:00:00+00:00
- Stop distance 0.92% of entry; best move before exit 4% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #15 · BTCUSDT 1h · SHORT 105 bars · entry/SL/TP1/exit key=n15 · green=entry · red=SL · blu`
- At entry the context cell read "Structure correct · Downtrend · Normal volume · Low zone"; close at 18% of the prior 24-bar range (lower half for a short); volume 0.91x the 20-bar average; stop 0.92% from entry.

Losses by bucket: 2: 1 trades, −$18.87 (18% of loss $); 5: 5 trades, −$83.82 (82% of loss $).
Losses by outcome: {'SL': 6}. By structure: {'CORRECT': 6}.
Largest losses: #15 −$21.74, #13 −$21.59, #6 −$18.87, #7 −$18.06, #12 −$17.98.
Loss days: 6 distinct UTC entry days for 6 losses; no day has more than one loss.


#### CANDLE12_V2 · long book

Blotter: `Trade blotter — BTCUSDT · 1h · LONG`. Trades 3, winners 1, losers 2, scratches 0; net −$2.50, fees −$3.18, gross +$0.68. Row totals: net −$2.50, fees −$3.18.

**Trade 2 · BTCUSDT · 1h · LONG** — bucket 2
- Entry 2026-09-21T09:00:00Z, exit 2026-10-08T15:00:00Z, hold 414 bars
- Entry 84,292.36, stop 81,582.39, TP1 89,712.29, exit 81,566.07, outcome SL
- Gross −$20.12, fees −$0.49, net −$20.61, R net -1.031, R net $ basis -1.031
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `volume 5.13x its 20-bar average · close at 109% of the prior 24-bar range · bar 2026-09-21T09:00:00+00:00`; structure CORRECT, trend BULLISH, volume 5.13x, range position 109%, decision bar 2026-09-21T09:00:00+00:00
- Stop distance 3.21% of entry; best move before exit 57% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #2 · BTCUSDT 1h · LONG 543 bars · entry/SL/TP1/exit key=n2 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 109% of the prior 24-bar range (upper half for a long); volume 5.13x the 20-bar average; stop 3.21% from entry.

**Trade 3 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-11T16:00:00Z, exit 2026-10-12T12:00:00Z, hold 20 bars
- Entry 83,971.79, stop 83,027.54, TP1 86,098.10, exit 83,010.94, outcome SL
- Gross −$20.35, fees −$1.41, net −$21.77, R net -1.088, R net $ basis -1.088
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `volume 4.34x its 20-bar average · close at 93% of the prior 24-bar range · bar 2026-10-11T16:00:00+00:00`; structure CORRECT, trend BULLISH, volume 4.34x, range position 93%, decision bar 2026-10-11T16:00:00+00:00
- Stop distance 1.12% of entry; best move before exit 17% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #3 · BTCUSDT 1h · LONG 149 bars · entry/SL/TP1/exit key=n3 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 93% of the prior 24-bar range (upper half for a long); volume 4.34x the 20-bar average; stop 1.12% from entry.

Losses by bucket: 2: 1 trades, −$20.61 (49% of loss $); 5: 1 trades, −$21.77 (51% of loss $).
Losses by outcome: {'SL': 2}. By structure: {'CORRECT': 2}.
Largest losses: #3 −$21.77, #2 −$20.61.
Loss days: 2 distinct UTC entry days for 2 losses; no day has more than one loss.


#### COMBO_01 · long book

Blotter: `Trade blotter — BTCUSDT · 1h · LONG`. Trades 13, winners 4, losers 9, scratches 0; net +$1.09, fees −$17.37, gross +$18.47. Row totals: net +$1.09, fees −$17.37.

**Trade 1 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-08-29T19:00:00Z, exit 2026-08-29T21:00:00Z, hold 2 bars
- Entry 78,154.90, stop 78,043.62, TP1 79,138.70, exit 78,043.62, outcome SL
- Gross −$2.85, fees −$1.60, net −$4.45, R net -1.561, R net $ basis -0.222
- Market context: Structure correct · Uptrend · Normal volume · High zone · tooltip `volume 1.06x its 20-bar average · close at 93% of the prior 24-bar range · bar 2026-08-29T19:00:00+00:00`; structure CORRECT, trend BULLISH, volume 1.06x, range position 93%, decision bar 2026-08-29T19:00:00+00:00
- Stop distance 0.14% of entry; best move before exit 12% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #1 · BTCUSDT 1h · LONG 131 bars · entry/SL/TP1/exit key=n1 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · Normal volume · High zone"; close at 93% of the prior 24-bar range (upper half for a long); volume 1.06x the 20-bar average; stop 0.14% from entry.

**Trade 3 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-03T15:00:00Z, exit 2026-09-04T12:00:00Z, hold 21 bars
- Entry 81,316.70, stop 80,202.31, TP1 83,545.48, exit 80,202.31, outcome SL
- Gross −$20.00, fees −$1.16, net −$21.16, R net -1.058, R net $ basis -1.058
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `volume 4.30x its 20-bar average · close at 120% of the prior 24-bar range · bar 2026-09-03T15:00:00+00:00`; structure CORRECT, trend BULLISH, volume 4.30x, range position 120%, decision bar 2026-09-03T15:00:00+00:00
- Stop distance 1.37% of entry; best move before exit 43% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #3 · BTCUSDT 1h · LONG 150 bars · entry/SL/TP1/exit key=n3 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 120% of the prior 24-bar range (upper half for a long); volume 4.30x the 20-bar average; stop 1.37% from entry.

**Trade 4 · BTCUSDT · 1h · LONG** — bucket 1
- Entry 2026-09-12T14:00:00Z, exit 2026-09-12T17:00:00Z, hold 3 bars
- Entry 77,430.50, stop 77,277.25, TP1 77,767.80, exit 77,277.25, outcome SL
- Gross −$4.03, fees −$1.63, net −$5.65, R net -1.404, R net $ basis -0.283
- Market context: Structure correct · Uptrend · Normal volume · Low zone · tooltip `volume 0.77x its 20-bar average · close at 20% of the prior 24-bar range · bar 2026-09-12T14:00:00+00:00`; structure CORRECT, trend BULLISH, volume 0.77x, range position 20%, decision bar 2026-09-12T14:00:00+00:00
- Stop distance 0.20% of entry; best move before exit 6% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #4 · BTCUSDT 1h · LONG 132 bars · entry/SL/TP1/exit key=n4 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · Normal volume · Low zone"; close at 20% of the prior 24-bar range (lower half for a long); volume 0.77x the 20-bar average; stop 0.20% from entry.

**Trade 5 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-14T09:00:00Z, exit 2026-09-14T13:00:00Z, hold 4 bars
- Entry 78,109.30, stop 77,489.05, TP1 79,384.40, exit 77,489.05, outcome SL
- Gross −$16.06, fees −$1.61, net −$17.67, R net -1.100, R net $ basis -0.884
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `volume 2.01x its 20-bar average · close at 109% of the prior 24-bar range · bar 2026-09-14T09:00:00+00:00`; structure CORRECT, trend BULLISH, volume 2.01x, range position 109%, decision bar 2026-09-14T09:00:00+00:00
- Stop distance 0.79% of entry; best move before exit 29% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #5 · BTCUSDT 1h · LONG 133 bars · entry/SL/TP1/exit key=n5 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 109% of the prior 24-bar range (upper half for a long); volume 2.01x the 20-bar average; stop 0.79% from entry.

**Trade 7 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-14T21:00:00Z, exit 2026-09-15T05:00:00Z, hold 8 bars
- Entry 78,728.20, stop 77,356.60, TP1 81,500.00, exit 77,356.60, outcome SL
- Gross −$20.00, fees −$0.91, net −$20.91, R net -1.046, R net $ basis -1.046
- Market context: Structure correct · Uptrend · Normal volume · High zone · tooltip `volume 0.88x its 20-bar average · close at 74% of the prior 24-bar range · bar 2026-09-14T21:00:00+00:00`; structure CORRECT, trend BULLISH, volume 0.88x, range position 74%, decision bar 2026-09-14T21:00:00+00:00
- Stop distance 1.74% of entry; best move before exit 3% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #7 · BTCUSDT 1h · LONG 137 bars · entry/SL/TP1/exit key=n7 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · Normal volume · High zone"; close at 74% of the prior 24-bar range (upper half for a long); volume 0.88x the 20-bar average; stop 1.74% from entry.

**Trade 9 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-18T10:00:00Z, exit 2026-09-18T12:00:00Z, hold 2 bars
- Entry 78,246.80, stop 77,973.88, TP1 78,966.00, exit 77,973.88, outcome SL
- Gross −$7.10, fees −$1.62, net −$8.72, R net -1.229, R net $ basis -0.436
- Market context: Structure correct · Uptrend · Normal volume · High zone · tooltip `volume 0.70x its 20-bar average · close at 92% of the prior 24-bar range · bar 2026-09-18T10:00:00+00:00`; structure CORRECT, trend BULLISH, volume 0.70x, range position 92%, decision bar 2026-09-18T10:00:00+00:00
- Stop distance 0.35% of entry; best move before exit 21% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #9 · BTCUSDT 1h · LONG 131 bars · entry/SL/TP1/exit key=n9 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · Normal volume · High zone"; close at 92% of the prior 24-bar range (upper half for a long); volume 0.70x the 20-bar average; stop 0.35% from entry.

**Trade 11 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-21T10:00:00Z, exit 2026-10-08T17:00:00Z, hold 415 bars
- Entry 84,400.10, stop 80,403.04, TP1 92,394.21, exit 80,403.04, outcome SL
- Gross −$20.00, fees −$0.33, net −$20.33, R net -1.016, R net $ basis -1.016
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `volume 1.58x its 20-bar average · close at 82% of the prior 24-bar range · bar 2026-09-21T10:00:00+00:00`; structure CORRECT, trend BULLISH, volume 1.58x, range position 82%, decision bar 2026-09-21T10:00:00+00:00
- Stop distance 4.74% of entry; best move before exit 37% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #11 · BTCUSDT 1h · LONG 544 bars · entry/SL/TP1/exit key=n11 · green=entry · red=SL · blue`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 82% of the prior 24-bar range (upper half for a long); volume 1.58x the 20-bar average; stop 4.74% from entry.

**Trade 12 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-10T17:00:00Z, exit 2026-10-10T22:00:00Z, hold 5 bars
- Entry 83,068.70, stop 82,930.67, TP1 83,487.10, exit 82,930.67, outcome SL
- Gross −$3.42, fees −$1.64, net −$5.06, R net -1.481, R net $ basis -0.253
- Market context: Structure correct · Uptrend · Normal volume · High zone · tooltip `volume 1.20x its 20-bar average · close at 103% of the prior 24-bar range · bar 2026-10-10T17:00:00+00:00`; structure CORRECT, trend BULLISH, volume 1.20x, range position 103%, decision bar 2026-10-10T17:00:00+00:00
- Stop distance 0.17% of entry; best move before exit 8% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #12 · BTCUSDT 1h · LONG 134 bars · entry/SL/TP1/exit key=n12 · green=entry · red=SL · blue`
- At entry the context cell read "Structure correct · Uptrend · Normal volume · High zone"; close at 103% of the prior 24-bar range (upper half for a long); volume 1.20x the 20-bar average; stop 0.17% from entry.

**Trade 13 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-11T15:00:00Z, exit 2026-10-12T12:00:00Z, hold 21 bars
- Entry 83,868.70, stop 83,027.54, TP1 85,632.70, exit 83,027.54, outcome SL
- Gross −$20.00, fees −$1.59, net −$21.59, R net -1.079, R net $ basis -1.079
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `volume 9.13x its 20-bar average · close at 233% of the prior 24-bar range · bar 2026-10-11T15:00:00+00:00`; structure CORRECT, trend BULLISH, volume 9.13x, range position 233%, decision bar 2026-10-11T15:00:00+00:00
- Stop distance 1.00% of entry; best move before exit 27% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #13 · BTCUSDT 1h · LONG 150 bars · entry/SL/TP1/exit key=n13 · green=entry · red=SL · blue`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 233% of the prior 24-bar range (upper half for a long); volume 9.13x the 20-bar average; stop 1.00% from entry.

Losses by bucket: 1: 1 trades, −$5.65 (5% of loss $); 5: 8 trades, −$119.89 (95% of loss $).
Losses by outcome: {'SL': 9}. By structure: {'CORRECT': 9}.
Largest losses: #13 −$21.59, #3 −$21.16, #7 −$20.91, #11 −$20.33, #5 −$17.67.
Loss days: 8 distinct UTC entry days for 9 losses; repeated days {'2026-09-14': 2}.


#### COMBO_02_V1 · long book

Blotter: `Trade blotter — BTCUSDT · 1h · LONG`. Trades 4, winners 2, losers 2, scratches 0; net +$34.41, fees −$4.17, gross +$38.58. Row totals: net +$34.41, fees −$4.17.

**Trade 2 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-18T10:00:00Z, exit 2026-09-18T12:00:00Z, hold 2 bars
- Entry 78,246.80, stop 77,973.88, TP1 78,966.00, exit 77,973.88, outcome SL
- Gross −$7.14, fees −$1.64, net −$8.78, R net -1.229, R net $ basis -0.439
- Market context: Structure correct · Uptrend · Normal volume · High zone · tooltip `volume 0.70x its 20-bar average · close at 92% of the prior 24-bar range · bar 2026-09-18T10:00:00+00:00`; structure CORRECT, trend BULLISH, volume 0.70x, range position 92%, decision bar 2026-09-18T10:00:00+00:00
- Stop distance 0.35% of entry; best move before exit 21% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #2 · BTCUSDT 1h · LONG 131 bars · entry/SL/TP1/exit key=n2 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · Normal volume · High zone"; close at 92% of the prior 24-bar range (upper half for a long); volume 0.70x the 20-bar average; stop 0.35% from entry.

**Trade 4 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-21T10:00:00Z, exit 2026-10-08T17:00:00Z, hold 415 bars
- Entry 84,400.10, stop 80,403.04, TP1 92,394.21, exit 80,403.04, outcome SL
- Gross −$20.00, fees −$0.33, net −$20.33, R net -1.016, R net $ basis -1.016
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `volume 1.58x its 20-bar average · close at 82% of the prior 24-bar range · bar 2026-09-21T10:00:00+00:00`; structure CORRECT, trend BULLISH, volume 1.58x, range position 82%, decision bar 2026-09-21T10:00:00+00:00
- Stop distance 4.74% of entry; best move before exit 37% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #4 · BTCUSDT 1h · LONG 544 bars · entry/SL/TP1/exit key=n4 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 82% of the prior 24-bar range (upper half for a long); volume 1.58x the 20-bar average; stop 4.74% from entry.

Losses by bucket: 5: 2 trades, −$29.11 (100% of loss $).
Losses by outcome: {'SL': 2}. By structure: {'CORRECT': 2}.
Largest losses: #4 −$20.33, #2 −$8.78.
Loss days: 2 distinct UTC entry days for 2 losses; no day has more than one loss.


#### COMBO_02_V2 · long book

Blotter: `—`. Trades 18, winners 8, losers 10, scratches 0; net +$34.78, fees −$26.26, gross +$61.04. Row totals: net +$34.78, fees −$26.26.

Not in the per-trade loss review or the pooled totals: blotter hidden by backtest-run-mismatch. Losing trades 10, loss $ −$169.62.


#### COMBO_02_V2_1B · long book

Blotter: `—`. Trades 18, winners 8, losers 10, scratches 0; net +$67.77, fees −$27.23, gross +$95.01. Row totals: net +$67.77, fees −$27.23.

Not in the per-trade loss review or the pooled totals: blotter hidden by backtest-run-mismatch. Losing trades 10, loss $ −$156.02.


#### COMBO_03_TRANSITION_B · long book

Blotter: `Trade blotter — BTCUSDT · 1h · LONG`. Trades 0, winners 0, losers 0, scratches 0; net +$0.00, fees +$0.00, gross +$0.00. Row totals: net —, fees +$0.00.

No losing trade in this run.


#### COMBO_SD_STRUCTURE · long book

Blotter: `Trade blotter — BTCUSDT · 1h · LONG`. Trades 0, winners 0, losers 0, scratches 0; net +$0.00, fees +$0.00, gross +$0.00. Row totals: net —, fees +$0.00.

No losing trade in this run.


#### LARGE_CAP_SWEEP_CHOCH · long book

Blotter: `Trade blotter — BTCUSDT · 1h · LONG`. Trades 13, winners 3, losers 10, scratches 0; net −$97.03, fees −$15.27, gross −$81.77. Row totals: net −$97.03, fees −$15.27.

**Trade 2 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-08-31T17:00:00Z, exit 2026-09-01T16:00:00Z, hold 23 bars
- Entry 78,865.00, stop 77,353.00, TP1 81,889.00, exit 77,353.00, outcome SL
- Gross −$20.00, fees −$0.83, net −$20.83, R net -1.041, R net $ basis -1.041
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `volume 0.97x its 20-bar average · close at 81% of the prior 24-bar range · bar 2026-08-31T17:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 0.97x, range position 81%, decision bar 2026-08-31T17:00:00+00:00
- Stop distance 1.92% of entry; best move before exit 12% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #2 · BTCUSDT 1h · LONG 152 bars · entry/SL/TP1/exit key=n2 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 81% of the prior 24-bar range (upper half for a long); volume 0.97x the 20-bar average; stop 1.92% from entry.

**Trade 3 · BTCUSDT · 1h · LONG** — bucket 1
- Entry 2026-09-02T05:00:00Z, exit 2026-09-02T08:00:00Z, hold 3 bars
- Entry 77,618.50, stop 76,931.18, TP1 78,993.14, exit 76,931.18, outcome SL
- Gross −$17.71, fees −$1.59, net −$19.30, R net -1.090, R net $ basis -0.965
- Market context: Structure neutral · No trend · Low volume · Low zone · tooltip `volume 0.62x its 20-bar average · close at 44% of the prior 24-bar range · bar 2026-09-02T05:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 0.62x, range position 44%, decision bar 2026-09-02T05:00:00+00:00
- Stop distance 0.89% of entry; best move before exit 8% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #3 · BTCUSDT 1h · LONG 132 bars · entry/SL/TP1/exit key=n3 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure neutral · No trend · Low volume · Low zone"; close at 44% of the prior 24-bar range (lower half for a long); volume 0.62x the 20-bar average; stop 0.89% from entry.

**Trade 5 · BTCUSDT · 1h · LONG** — bucket 1
- Entry 2026-09-08T01:00:00Z, exit 2026-09-08T02:00:00Z, hold 1 bars
- Entry 79,409.00, stop 78,938.70, TP1 80,349.60, exit 78,938.70, outcome SL
- Gross −$11.85, fees −$1.60, net −$13.44, R net -1.135, R net $ basis -0.672
- Market context: Structure neutral · No trend · Normal volume · Low zone · tooltip `volume 0.71x its 20-bar average · close at 44% of the prior 24-bar range · bar 2026-09-08T01:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 0.71x, range position 44%, decision bar 2026-09-08T01:00:00+00:00
- Stop distance 0.59% of entry; best move before exit 7% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #5 · BTCUSDT 1h · LONG 130 bars · entry/SL/TP1/exit key=n5 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure neutral · No trend · Normal volume · Low zone"; close at 44% of the prior 24-bar range (lower half for a long); volume 0.71x the 20-bar average; stop 0.59% from entry.

**Trade 6 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-09T04:00:00Z, exit 2026-09-09T15:00:00Z, hold 11 bars
- Entry 79,136.00, stop 78,225.80, TP1 80,956.40, exit 78,225.80, outcome SL
- Gross −$20.00, fees −$1.38, net −$21.38, R net -1.069, R net $ basis -1.069
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `volume 1.26x its 20-bar average · close at 112% of the prior 24-bar range · bar 2026-09-09T04:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 1.26x, range position 112%, decision bar 2026-09-09T04:00:00+00:00
- Stop distance 1.15% of entry; best move before exit 33% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #6 · BTCUSDT 1h · LONG 140 bars · entry/SL/TP1/exit key=n6 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 112% of the prior 24-bar range (upper half for a long); volume 1.26x the 20-bar average; stop 1.15% from entry.

**Trade 7 · BTCUSDT · 1h · LONG** — bucket 1
- Entry 2026-09-12T14:00:00Z, exit 2026-09-14T00:00:00Z, hold 34 bars
- Entry 77,430.50, stop 76,402.90, TP1 79,485.70, exit 76,402.90, outcome SL
- Gross −$20.00, fees −$1.20, net −$21.20, R net -1.060, R net $ basis -1.060
- Market context: Structure correct · Uptrend · Normal volume · Low zone · tooltip `volume 0.77x its 20-bar average · close at 20% of the prior 24-bar range · bar 2026-09-12T14:00:00+00:00`; structure CORRECT, trend BULLISH, volume 0.77x, range position 20%, decision bar 2026-09-12T14:00:00+00:00
- Stop distance 1.33% of entry; best move before exit 1% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #7 · BTCUSDT 1h · LONG 163 bars · entry/SL/TP1/exit key=n7 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · Normal volume · Low zone"; close at 20% of the prior 24-bar range (lower half for a long); volume 0.77x the 20-bar average; stop 1.33% from entry.

**Trade 8 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-14T14:00:00Z, exit 2026-09-15T13:00:00Z, hold 23 bars
- Entry 78,479.30, stop 76,458.90, TP1 82,520.10, exit 76,458.90, outcome SL
- Gross −$20.00, fees −$0.61, net −$20.61, R net -1.031, R net $ basis -1.031
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `volume 2.67x its 20-bar average · close at 100% of the prior 24-bar range · bar 2026-09-14T14:00:00+00:00`; structure CORRECT, trend BULLISH, volume 2.67x, range position 100%, decision bar 2026-09-14T14:00:00+00:00
- Stop distance 2.57% of entry; best move before exit 27% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #8 · BTCUSDT 1h · LONG 152 bars · entry/SL/TP1/exit key=n8 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 100% of the prior 24-bar range (upper half for a long); volume 2.67x the 20-bar average; stop 2.57% from entry.

**Trade 9 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-16T11:00:00Z, exit 2026-09-16T14:00:00Z, hold 3 bars
- Entry 76,210.20, stop 75,526.77, TP1 77,577.07, exit 75,526.77, outcome SL
- Gross −$17.94, fees −$1.59, net −$19.53, R net -1.089, R net $ basis -0.976
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `volume 0.70x its 20-bar average · close at 54% of the prior 24-bar range · bar 2026-09-16T11:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 0.70x, range position 54%, decision bar 2026-09-16T11:00:00+00:00
- Stop distance 0.90% of entry; best move before exit 5% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #9 · BTCUSDT 1h · LONG 132 bars · entry/SL/TP1/exit key=n9 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 54% of the prior 24-bar range (upper half for a long); volume 0.70x the 20-bar average; stop 0.90% from entry.

**Trade 10 · BTCUSDT · 1h · LONG** — bucket 1
- Entry 2026-09-26T08:00:00Z, exit 2026-09-28T05:00:00Z, hold 45 bars
- Entry 84,167.30, stop 83,122.00, TP1 86,257.90, exit 83,122.00, outcome SL
- Gross −$20.00, fees −$1.28, net −$21.28, R net -1.064, R net $ basis -1.064
- Market context: Structure neutral · No trend · Low volume · Low zone · tooltip `volume 0.66x its 20-bar average · close at 50% of the prior 24-bar range · bar 2026-09-26T08:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 0.66x, range position 50%, decision bar 2026-09-26T08:00:00+00:00
- Stop distance 1.24% of entry; best move before exit 47% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #10 · BTCUSDT 1h · LONG 174 bars · entry/SL/TP1/exit key=n10 · green=entry · red=SL · blue`
- At entry the context cell read "Structure neutral · No trend · Low volume · Low zone"; close at 50% of the prior 24-bar range (lower half for a long); volume 0.66x the 20-bar average; stop 1.24% from entry.

**Trade 12 · BTCUSDT · 1h · LONG** — bucket 4
- Entry 2026-10-10T05:00:00Z, exit 2026-10-15T02:00:00Z, hold 117 bars
- Entry 82,800.20, stop 80,716.00, TP1 86,968.60, exit 80,716.00, outcome SL
- Gross −$20.00, fees −$0.63, net −$20.63, R net -1.031, R net $ basis -1.031
- Market context: Structure wrong · Downtrend · Low volume · Low zone · tooltip `volume 0.36x its 20-bar average · close at 47% of the prior 24-bar range · bar 2026-10-10T05:00:00+00:00`; structure WRONG, trend BEARISH, volume 0.36x, range position 46%, decision bar 2026-10-10T05:00:00+00:00
- Stop distance 2.52% of entry; best move before exit 37% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #12 · BTCUSDT 1h · LONG 223 bars · entry/SL/TP1/exit key=n12 · green=entry · red=SL · blue`
- At entry the context cell read "Structure wrong · Downtrend · Low volume · Low zone"; close at 46% of the prior 24-bar range (lower half for a long); volume 0.36x the 20-bar average; stop 2.52% from entry.

**Trade 13 · BTCUSDT · 1h · LONG** — bucket 4
- Entry 2026-10-16T11:00:00Z, exit 2026-10-16T15:00:00Z, hold 4 bars
- Entry 82,598.70, stop 81,314.00, TP1 85,168.10, exit 81,314.00, outcome SL
- Gross −$20.00, fees −$1.02, net −$21.02, R net -1.051, R net $ basis -1.051
- Market context: Structure wrong · Downtrend · High volume · High zone · tooltip `volume 2.45x its 20-bar average · close at 99% of the prior 24-bar range · bar 2026-10-16T11:00:00+00:00`; structure WRONG, trend BEARISH, volume 2.45x, range position 99%, decision bar 2026-10-16T11:00:00+00:00
- Stop distance 1.56% of entry; best move before exit 1% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #13 · BTCUSDT 1h · LONG 73 bars · entry/SL/TP1/exit key=n13 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure wrong · Downtrend · High volume · High zone"; close at 99% of the prior 24-bar range (upper half for a long); volume 2.45x the 20-bar average; stop 1.56% from entry.

Losses by bucket: 1: 4 trades, −$75.22 (38% of loss $); 4: 2 trades, −$41.65 (21% of loss $); 5: 4 trades, −$82.35 (41% of loss $).
Losses by outcome: {'SL': 10}. By structure: {'NEUTRAL': 6, 'CORRECT': 2, 'WRONG': 2}.
Largest losses: #6 −$21.38, #10 −$21.28, #7 −$21.20, #13 −$21.02, #2 −$20.83.
Loss days: 10 distinct UTC entry days for 10 losses; no day has more than one loss.


#### OSE_A_corrected_baseline · long book

Blotter: `—`. Trades 30, winners 13, losers 17, scratches 0; net −$75.82, fees −$35.18, gross −$40.64. Row totals: net −$75.82, fees −$35.18.

**Trade 2 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-08-30T13:00:00Z, exit 2026-08-30T23:00:00Z, hold 10 bars
- Entry 78,765.85, stop 77,944.90, TP1 80,407.75, exit 77,944.90, outcome stop
- Gross −$20.00, fees −$1.53, net −$21.53, R net -1.076, R net $ basis -1.076
- Market context: Structure neutral · No trend · High volume · High zone · tooltip `—`; structure NEUTRAL, trend NEUTRAL, volume 8.46x, range position 152%, decision bar 2026-08-30T12:00:00+00:00
- Stop distance 1.04% of entry; best move before exit 38% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure neutral · No trend · High volume · High zone"; close at 152% of the prior 24-bar range (upper half for a long); volume 8.46x the 20-bar average; stop 1.04% from entry.

**Trade 3 · BTCUSDT · 1h · LONG** — bucket 1
- Entry 2026-09-02T06:00:00Z, exit 2026-09-02T09:00:00Z, hold 3 bars
- Entry 77,634.12, stop 76,682.30, TP1 79,537.77, exit 76,682.30, outcome stop
- Gross −$20.00, fees −$1.30, net −$21.30, R net -1.065, R net $ basis -1.065
- Market context: Structure neutral · No trend · Low volume · Low zone · tooltip `—`; structure NEUTRAL, trend NEUTRAL, volume 0.62x, range position 44%, decision bar 2026-09-02T05:00:00+00:00
- Stop distance 1.23% of entry; best move before exit 5% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure neutral · No trend · Low volume · Low zone"; close at 44% of the prior 24-bar range (lower half for a long); volume 0.62x the 20-bar average; stop 1.23% from entry.

**Trade 5 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-03T20:00:00Z, exit 2026-09-04T15:00:00Z, hold 19 bars
- Entry 81,739.14, stop 77,422.80, TP1 90,371.83, exit 79,368.92, outcome timeout
- Gross −$10.98, fees −$0.30, net −$11.28, R net -0.564, R net $ basis -0.564
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `—`; structure CORRECT, trend BULLISH, volume 1.51x, range position 109%, decision bar 2026-09-03T19:00:00+00:00
- Stop distance 5.28% of entry; best move before exit 6% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 109% of the prior 24-bar range (upper half for a long); volume 1.51x the 20-bar average; stop 5.28% from entry.

**Trade 7 · BTCUSDT · 1h · LONG** — bucket 4
- Entry 2026-09-06T23:00:00Z, exit 2026-09-07T07:00:00Z, hold 8 bars
- Entry 80,021.40, stop 79,125.30, TP1 81,813.60, exit 79,125.30, outcome stop
- Gross −$20.00, fees −$1.42, net −$21.42, R net -1.071, R net $ basis -1.071
- Market context: Structure wrong · Downtrend · High volume · High zone · tooltip `—`; structure WRONG, trend BEARISH, volume 1.78x, range position 92%, decision bar 2026-09-06T22:00:00+00:00
- Stop distance 1.12% of entry; best move before exit 22% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure wrong · Downtrend · High volume · High zone"; close at 92% of the prior 24-bar range (upper half for a long); volume 1.78x the 20-bar average; stop 1.12% from entry.

**Trade 8 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-09T05:00:00Z, exit 2026-09-09T15:00:00Z, hold 10 bars
- Entry 79,151.73, stop 78,230.50, TP1 80,994.18, exit 78,230.50, outcome stop
- Gross −$20.00, fees −$1.37, net −$21.37, R net -1.068, R net $ basis -1.068
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `—`; structure NEUTRAL, trend NEUTRAL, volume 1.26x, range position 112%, decision bar 2026-09-09T04:00:00+00:00
- Stop distance 1.16% of entry; best move before exit 32% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 112% of the prior 24-bar range (upper half for a long); volume 1.26x the 20-bar average; stop 1.16% from entry.

**Trade 9 · BTCUSDT · 1h · LONG** — bucket 4
- Entry 2026-09-11T13:00:00Z, exit 2026-09-12T08:00:00Z, hold 19 bars
- Entry 78,008.30, stop 76,402.90, TP1 81,219.10, exit 77,309.63, outcome timeout
- Gross −$8.70, fees −$0.77, net −$9.48, R net -0.474, R net $ basis -0.474
- Market context: Structure wrong · Downtrend · High volume · High zone · tooltip `—`; structure WRONG, trend BEARISH, volume 12.61x, range position 104%, decision bar 2026-09-11T12:00:00+00:00
- Stop distance 2.06% of entry; best move before exit 58% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure wrong · Downtrend · High volume · High zone"; close at 104% of the prior 24-bar range (upper half for a long); volume 12.61x the 20-bar average; stop 2.06% from entry.

**Trade 10 · BTCUSDT · 1h · LONG** — bucket 1
- Entry 2026-09-12T15:00:00Z, exit 2026-09-12T18:00:00Z, hold 3 bars
- Entry 77,445.89, stop 77,129.00, TP1 78,079.66, exit 77,129.00, outcome stop
- Gross −$8.18, fees −$1.60, net −$9.78, R net -1.195, R net $ basis -0.489
- Market context: Structure correct · Uptrend · Normal volume · Low zone · tooltip `—`; structure CORRECT, trend BULLISH, volume 0.77x, range position 20%, decision bar 2026-09-12T14:00:00+00:00
- Stop distance 0.41% of entry; best move before exit 0% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure correct · Uptrend · Normal volume · Low zone"; close at 20% of the prior 24-bar range (lower half for a long); volume 0.77x the 20-bar average; stop 0.41% from entry.

**Trade 11 · BTCUSDT · 1h · LONG** — bucket 4
- Entry 2026-09-13T18:00:00Z, exit 2026-09-14T00:00:00Z, hold 6 bars
- Entry 77,311.26, stop 76,458.90, TP1 79,015.98, exit 76,458.90, outcome stop
- Gross −$20.00, fees −$1.44, net −$21.44, R net -1.072, R net $ basis -1.072
- Market context: Structure wrong · Downtrend · Normal volume · High zone · tooltip `—`; structure WRONG, trend BEARISH, volume 0.75x, range position 92%, decision bar 2026-09-13T17:00:00+00:00
- Stop distance 1.10% of entry; best move before exit 7% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure wrong · Downtrend · Normal volume · High zone"; close at 92% of the prior 24-bar range (upper half for a long); volume 0.75x the 20-bar average; stop 1.10% from entry.

**Trade 13 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-16T12:00:00Z, exit 2026-09-16T18:00:00Z, hold 6 bars
- Entry 76,225.34, stop 75,308.40, TP1 78,059.23, exit 75,308.40, outcome stop
- Gross −$20.00, fees −$1.32, net −$21.32, R net -1.066, R net $ basis -1.066
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `—`; structure NEUTRAL, trend NEUTRAL, volume 0.70x, range position 54%, decision bar 2026-09-16T11:00:00+00:00
- Stop distance 1.20% of entry; best move before exit 17% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 54% of the prior 24-bar range (upper half for a long); volume 0.70x the 20-bar average; stop 1.20% from entry.

**Trade 16 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-19T15:00:00Z, exit 2026-09-19T21:00:00Z, hold 6 bars
- Entry 81,563.31, stop 80,882.00, TP1 82,925.93, exit 80,882.00, outcome stop
- Gross −$16.71, fees −$1.59, net −$18.30, R net -1.095, R net $ basis -0.915
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `—`; structure NEUTRAL, trend NEUTRAL, volume 1.44x, range position 89%, decision bar 2026-09-19T14:00:00+00:00
- Stop distance 0.84% of entry; best move before exit 23% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 89% of the prior 24-bar range (upper half for a long); volume 1.44x the 20-bar average; stop 0.84% from entry.

**Trade 19 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-22T16:00:00Z, exit 2026-09-23T11:00:00Z, hold 19 bars
- Entry 86,384.67, stop 85,080.00, TP1 88,994.02, exit 85,605.68, outcome timeout
- Gross −$11.94, fees −$1.05, net −$13.00, R net -0.650, R net $ basis -0.650
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `—`; structure NEUTRAL, trend NEUTRAL, volume 1.08x, range position 56%, decision bar 2026-09-22T15:00:00+00:00
- Stop distance 1.51% of entry; best move before exit 33% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 56% of the prior 24-bar range (upper half for a long); volume 1.08x the 20-bar average; stop 1.51% from entry.

**Trade 20 · BTCUSDT · 1h · LONG** — bucket 1
- Entry 2026-09-26T09:00:00Z, exit 2026-09-26T13:00:00Z, hold 4 bars
- Entry 84,184.23, stop 83,794.20, TP1 84,964.30, exit 83,794.20, outcome stop
- Gross −$9.27, fees −$1.60, net −$10.86, R net -1.172, R net $ basis -0.543
- Market context: Structure neutral · No trend · Low volume · Low zone · tooltip `—`; structure NEUTRAL, trend NEUTRAL, volume 0.66x, range position 50%, decision bar 2026-09-26T08:00:00+00:00
- Stop distance 0.46% of entry; best move before exit 0% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure neutral · No trend · Low volume · Low zone"; close at 50% of the prior 24-bar range (lower half for a long); volume 0.66x the 20-bar average; stop 0.46% from entry.

**Trade 25 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-04T21:00:00Z, exit 2026-10-05T16:00:00Z, hold 19 bars
- Entry 85,808.36, stop 84,474.60, TP1 88,475.87, exit 85,242.95, outcome timeout
- Gross −$8.48, fees −$1.03, net −$9.50, R net -0.475, R net $ basis -0.475
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `—`; structure CORRECT, trend BULLISH, volume 2.45x, range position 122%, decision bar 2026-10-04T20:00:00+00:00
- Stop distance 1.55% of entry; best move before exit 44% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 122% of the prior 24-bar range (upper half for a long); volume 2.45x the 20-bar average; stop 1.55% from entry.

**Trade 26 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-06T11:00:00Z, exit 2026-10-07T01:00:00Z, hold 14 bars
- Entry 86,137.02, stop 85,072.70, TP1 88,265.67, exit 85,072.70, outcome stop
- Gross −$20.00, fees −$1.29, net −$21.29, R net -1.064, R net $ basis -1.064
- Market context: Structure neutral · No trend · Low volume · High zone · tooltip `—`; structure NEUTRAL, trend NEUTRAL, volume 0.66x, range position 68%, decision bar 2026-10-06T10:00:00+00:00
- Stop distance 1.24% of entry; best move before exit 25% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure neutral · No trend · Low volume · High zone"; close at 68% of the prior 24-bar range (upper half for a long); volume 0.66x the 20-bar average; stop 1.24% from entry.

**Trade 27 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-09T12:00:00Z, exit 2026-10-10T07:00:00Z, hold 19 bars
- Entry 83,216.64, stop 81,571.50, TP1 86,506.92, exit 82,743.45, outcome timeout
- Gross −$5.75, fees −$0.81, net −$6.56, R net -0.328, R net $ basis -0.328
- Market context: Structure neutral · No trend · High volume · High zone · tooltip `—`; structure NEUTRAL, trend NEUTRAL, volume 1.94x, range position 119%, decision bar 2026-10-09T11:00:00+00:00
- Stop distance 1.98% of entry; best move before exit 2% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure neutral · No trend · High volume · High zone"; close at 119% of the prior 24-bar range (upper half for a long); volume 1.94x the 20-bar average; stop 1.98% from entry.

**Trade 28 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-10T15:00:00Z, exit 2026-10-11T10:00:00Z, hold 19 bars
- Entry 82,969.29, stop 82,241.50, TP1 84,424.87, exit 82,941.91, outcome timeout
- Gross −$0.66, fees −$1.60, net −$2.26, R net -0.129, R net $ basis -0.113
- Market context: Structure neutral · No trend · High volume · High zone · tooltip `—`; structure NEUTRAL, trend NEUTRAL, volume 1.77x, range position 69%, decision bar 2026-10-10T14:00:00+00:00
- Stop distance 0.88% of entry; best move before exit 15% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure neutral · No trend · High volume · High zone"; close at 69% of the prior 24-bar range (upper half for a long); volume 1.77x the 20-bar average; stop 0.88% from entry.

**Trade 29 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-11T16:00:00Z, exit 2026-10-12T11:00:00Z, hold 19 bars
- Entry 83,885.57, stop 82,916.70, TP1 85,823.32, exit 83,342.73, outcome timeout
- Gross −$11.21, fees −$1.38, net −$12.59, R net -0.629, R net $ basis -0.629
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `—`; structure CORRECT, trend BULLISH, volume 9.13x, range position 233%, decision bar 2026-10-11T15:00:00+00:00
- Stop distance 1.15% of entry; best move before exit 23% of the way to TP1 (candles); chart overlay `—`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 233% of the prior 24-bar range (upper half for a long); volume 9.13x the 20-bar average; stop 1.15% from entry.

Losses by bucket: 1: 3 trades, −$41.94 (17% of loss $); 4: 3 trades, −$52.34 (21% of loss $); 5: 11 trades, −$158.99 (63% of loss $).
Losses by outcome: {'stop': 10, 'timeout': 7}. By structure: {'NEUTRAL': 10, 'CORRECT': 4, 'WRONG': 3}.
Largest losses: #2 −$21.53, #11 −$21.44, #7 −$21.42, #8 −$21.37, #13 −$21.32.
Loss days: 17 distinct UTC entry days for 17 losses; no day has more than one loss.


#### OSE_B_acceptance · long book

Blotter: `Trade blotter — BTCUSDT · 1h · LONG`. Trades 23, winners 9, losers 14, scratches 0; net −$76.02, fees −$25.01, gross −$51.00. Row totals: net −$76.02, fees −$25.01.

**Trade 2 · BTCUSDT · 1h · LONG** — bucket 4
- Entry 2026-08-30T14:00:00Z, exit 2026-08-30T23:00:00Z, hold 9 bars
- Entry 78,833.46, stop 77,944.90, TP1 80,610.59, exit 77,944.90, outcome stop
- Gross −$20.00, fees −$1.41, net −$21.41, R net -1.071, R net $ basis -1.071
- Market context: Structure wrong · Downtrend · High volume · High zone · tooltip `volume 2.58x its 20-bar average · close at 100% of the prior 24-bar range · bar 2026-08-30T13:00:00+00:00`; structure WRONG, trend BEARISH, volume 2.58x, range position 100%, decision bar 2026-08-30T13:00:00+00:00
- Stop distance 1.13% of entry; best move before exit 31% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #2 · undefined undefined · LONG No candles in window key=n2 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure wrong · Downtrend · High volume · High zone"; close at 100% of the prior 24-bar range (upper half for a long); volume 2.58x the 20-bar average; stop 1.13% from entry.

**Trade 4 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-03T21:00:00Z, exit 2026-09-04T16:00:00Z, hold 19 bars
- Entry 81,436.48, stop 77,422.80, TP1 89,463.85, exit 79,693.26, outcome timeout
- Gross −$8.69, fees −$0.32, net −$9.01, R net -0.450, R net $ basis -0.450
- Market context: Structure correct · Uptrend · Normal volume · High zone · tooltip `volume 0.72x its 20-bar average · close at 93% of the prior 24-bar range · bar 2026-09-03T20:00:00+00:00`; structure CORRECT, trend BULLISH, volume 0.72x, range position 93%, decision bar 2026-09-03T20:00:00+00:00
- Stop distance 4.93% of entry; best move before exit 2% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #4 · undefined undefined · LONG No candles in window key=n4 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure correct · Uptrend · Normal volume · High zone"; close at 93% of the prior 24-bar range (upper half for a long); volume 0.72x the 20-bar average; stop 4.93% from entry.

**Trade 5 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-05T17:00:00Z, exit 2026-09-06T12:00:00Z, hold 19 bars
- Entry 80,017.50, stop 79,405.20, TP1 81,242.10, exit 79,866.02, outcome timeout
- Gross −$3.79, fees −$1.60, net −$5.38, R net -0.352, R net $ basis -0.269
- Market context: Structure neutral · No trend · High volume · High zone · tooltip `volume 3.52x its 20-bar average · close at 132% of the prior 24-bar range · bar 2026-09-05T16:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 3.52x, range position 132%, decision bar 2026-09-05T16:00:00+00:00
- Stop distance 0.77% of entry; best move before exit 6% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #5 · undefined undefined · LONG No candles in window key=n5 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure neutral · No trend · High volume · High zone"; close at 132% of the prior 24-bar range (upper half for a long); volume 3.52x the 20-bar average; stop 0.77% from entry.

**Trade 6 · BTCUSDT · 1h · LONG** — bucket 4
- Entry 2026-09-07T00:00:00Z, exit 2026-09-07T07:00:00Z, hold 7 bars
- Entry 80,317.06, stop 79,125.30, TP1 82,700.58, exit 79,125.30, outcome stop
- Gross −$20.00, fees −$1.07, net −$21.07, R net -1.054, R net $ basis -1.054
- Market context: Structure wrong · Downtrend · High volume · High zone · tooltip `volume 3.27x its 20-bar average · close at 123% of the prior 24-bar range · bar 2026-09-06T23:00:00+00:00`; structure WRONG, trend BEARISH, volume 3.27x, range position 123%, decision bar 2026-09-06T23:00:00+00:00
- Stop distance 1.48% of entry; best move before exit 4% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #6 · undefined undefined · LONG No candles in window key=n6 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure wrong · Downtrend · High volume · High zone"; close at 123% of the prior 24-bar range (upper half for a long); volume 3.27x the 20-bar average; stop 1.48% from entry.

**Trade 7 · BTCUSDT · 1h · LONG** — bucket 4
- Entry 2026-09-11T14:00:00Z, exit 2026-09-12T09:00:00Z, hold 19 bars
- Entry 79,223.84, stop 76,402.90, TP1 84,865.72, exit 77,304.54, outcome timeout
- Gross −$13.61, fees −$0.44, net −$14.05, R net -0.703, R net $ basis -0.703
- Market context: Structure wrong · Downtrend · High volume · High zone · tooltip `volume 5.36x its 20-bar average · close at 152% of the prior 24-bar range · bar 2026-09-11T13:00:00+00:00`; structure WRONG, trend BEARISH, volume 5.36x, range position 152%, decision bar 2026-09-11T13:00:00+00:00
- Stop distance 3.56% of entry; best move before exit 0% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #7 · undefined undefined · LONG No candles in window key=n7 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure wrong · Downtrend · High volume · High zone"; close at 152% of the prior 24-bar range (upper half for a long); volume 5.36x the 20-bar average; stop 3.56% from entry.

**Trade 8 · BTCUSDT · 1h · LONG** — bucket 4
- Entry 2026-09-13T19:00:00Z, exit 2026-09-14T00:00:00Z, hold 5 bars
- Entry 77,329.76, stop 76,458.90, TP1 79,071.49, exit 76,458.90, outcome stop
- Gross −$20.00, fees −$1.41, net −$21.41, R net -1.071, R net $ basis -1.071
- Market context: Structure wrong · Downtrend · Low volume · High zone · tooltip `volume 0.49x its 20-bar average · close at 92% of the prior 24-bar range · bar 2026-09-13T18:00:00+00:00`; structure WRONG, trend BEARISH, volume 0.49x, range position 92%, decision bar 2026-09-13T18:00:00+00:00
- Stop distance 1.13% of entry; best move before exit 6% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #8 · undefined undefined · LONG No candles in window key=n8 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure wrong · Downtrend · Low volume · High zone"; close at 92% of the prior 24-bar range (upper half for a long); volume 0.49x the 20-bar average; stop 1.13% from entry.

**Trade 12 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-19T16:00:00Z, exit 2026-09-19T21:00:00Z, hold 5 bars
- Entry 81,625.32, stop 80,882.00, TP1 83,111.97, exit 80,882.00, outcome stop
- Gross −$18.21, fees −$1.59, net −$19.81, R net -1.087, R net $ basis -0.990
- Market context: Structure neutral · No trend · High volume · High zone · tooltip `volume 2.25x its 20-bar average · close at 90% of the prior 24-bar range · bar 2026-09-19T15:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 2.25x, range position 90%, decision bar 2026-09-19T15:00:00+00:00
- Stop distance 0.91% of entry; best move before exit 13% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #12 · undefined undefined · LONG No candles in window key=n12 · green=entry · red=SL · blu`
- At entry the context cell read "Structure neutral · No trend · High volume · High zone"; close at 90% of the prior 24-bar range (upper half for a long); volume 2.25x the 20-bar average; stop 0.91% from entry.

**Trade 14 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-22T17:00:00Z, exit 2026-09-23T12:00:00Z, hold 19 bars
- Entry 86,395.18, stop 85,080.00, TP1 89,025.53, exit 85,448.01, outcome timeout
- Gross −$14.40, fees −$1.05, net −$15.45, R net -0.772, R net $ basis -0.772
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `volume 1.13x its 20-bar average · close at 56% of the prior 24-bar range · bar 2026-09-22T16:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 1.13x, range position 56%, decision bar 2026-09-22T16:00:00+00:00
- Stop distance 1.52% of entry; best move before exit 32% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #14 · undefined undefined · LONG No candles in window key=n14 · green=entry · red=SL · blu`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 56% of the prior 24-bar range (upper half for a long); volume 1.13x the 20-bar average; stop 1.52% from entry.

**Trade 15 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-27T09:00:00Z, exit 2026-09-28T01:00:00Z, hold 16 bars
- Entry 84,770.35, stop 83,778.40, TP1 86,754.25, exit 83,778.40, outcome stop
- Gross −$20.00, fees −$1.36, net −$21.36, R net -1.068, R net $ basis -1.068
- Market context: Structure neutral · No trend · High volume · High zone · tooltip `volume 2.43x its 20-bar average · close at 99% of the prior 24-bar range · bar 2026-09-27T08:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 2.43x, range position 99%, decision bar 2026-09-27T08:00:00+00:00
- Stop distance 1.17% of entry; best move before exit 19% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #15 · undefined undefined · LONG No candles in window key=n15 · green=entry · red=SL · blu`
- At entry the context cell read "Structure neutral · No trend · High volume · High zone"; close at 99% of the prior 24-bar range (upper half for a long); volume 2.43x the 20-bar average; stop 1.17% from entry.

**Trade 16 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-30T12:00:00Z, exit 2026-10-01T07:00:00Z, hold 19 bars
- Entry 83,899.78, stop 82,901.30, TP1 85,896.73, exit 83,364.92, outcome timeout
- Gross −$10.71, fees −$1.34, net −$12.05, R net -0.603, R net $ basis -0.603
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `volume 1.34x its 20-bar average · close at 61% of the prior 24-bar range · bar 2026-09-30T11:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 1.34x, range position 61%, decision bar 2026-09-30T11:00:00+00:00
- Stop distance 1.19% of entry; best move before exit 87% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #16 · undefined undefined · LONG No candles in window key=n16 · green=entry · red=SL · blu`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 61% of the prior 24-bar range (upper half for a long); volume 1.34x the 20-bar average; stop 1.19% from entry.

**Trade 19 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-06T12:00:00Z, exit 2026-10-07T01:00:00Z, hold 13 bars
- Entry 86,228.24, stop 85,072.70, TP1 88,539.33, exit 85,072.70, outcome stop
- Gross −$20.00, fees −$1.19, net −$21.19, R net -1.059, R net $ basis -1.059
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `volume 1.20x its 20-bar average · close at 73% of the prior 24-bar range · bar 2026-10-06T11:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 1.20x, range position 73%, decision bar 2026-10-06T11:00:00+00:00
- Stop distance 1.34% of entry; best move before exit 19% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #19 · undefined undefined · LONG No candles in window key=n19 · green=entry · red=SL · blu`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 73% of the prior 24-bar range (upper half for a long); volume 1.20x the 20-bar average; stop 1.34% from entry.

**Trade 20 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-09T13:00:00Z, exit 2026-10-10T08:00:00Z, hold 19 bars
- Entry 83,004.60, stop 81,571.50, TP1 85,870.79, exit 82,787.24, outcome timeout
- Gross −$3.03, fees −$0.93, net −$3.96, R net -0.198, R net $ basis -0.198
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `volume 1.09x its 20-bar average · close at 84% of the prior 24-bar range · bar 2026-10-09T12:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 1.09x, range position 84%, decision bar 2026-10-09T12:00:00+00:00
- Stop distance 1.73% of entry; best move before exit 9% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #20 · undefined undefined · LONG No candles in window key=n20 · green=entry · red=SL · blu`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 84% of the prior 24-bar range (upper half for a long); volume 1.09x the 20-bar average; stop 1.73% from entry.

**Trade 22 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-11T17:00:00Z, exit 2026-10-12T12:00:00Z, hold 19 bars
- Entry 83,886.87, stop 82,916.70, TP1 85,827.22, exit 82,916.70, outcome stop
- Gross −$20.00, fees −$1.38, net −$21.38, R net -1.069, R net $ basis -1.069
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `volume 4.34x its 20-bar average · close at 93% of the prior 24-bar range · bar 2026-10-11T16:00:00+00:00`; structure CORRECT, trend BULLISH, volume 4.34x, range position 93%, decision bar 2026-10-11T16:00:00+00:00
- Stop distance 1.16% of entry; best move before exit 23% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #22 · undefined undefined · LONG No candles in window key=n22 · green=entry · red=SL · blu`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 93% of the prior 24-bar range (upper half for a long); volume 4.34x the 20-bar average; stop 1.16% from entry.

**Trade 23 · BTCUSDT · 1h · LONG** — bucket 4
- Entry 2026-10-15T17:00:00Z, exit 2026-10-16T12:00:00Z, hold 19 bars
- Entry 82,097.62, stop 80,455.00, TP1 85,382.85, exit 81,992.20, outcome timeout
- Gross −$1.28, fees −$0.80, net −$2.08, R net -0.104, R net $ basis -0.104
- Market context: Structure wrong · Downtrend · Normal volume · High zone · tooltip `volume 1.10x its 20-bar average · close at 82% of the prior 24-bar range · bar 2026-10-15T16:00:00+00:00`; structure WRONG, trend BEARISH, volume 1.10x, range position 82%, decision bar 2026-10-15T16:00:00+00:00
- Stop distance 2.00% of entry; best move before exit 24% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #23 · undefined undefined · LONG No candles in window key=n23 · green=entry · red=SL · blu`
- At entry the context cell read "Structure wrong · Downtrend · Normal volume · High zone"; close at 82% of the prior 24-bar range (upper half for a long); volume 1.10x the 20-bar average; stop 2.00% from entry.

Losses by bucket: 4: 5 trades, −$80.03 (38% of loss $); 5: 9 trades, −$129.58 (62% of loss $).
Losses by outcome: {'stop': 7, 'timeout': 7}. By structure: {'WRONG': 5, 'CORRECT': 2, 'NEUTRAL': 7}.
Largest losses: #8 −$21.41, #2 −$21.41, #22 −$21.38, #15 −$21.36, #19 −$21.19.
Loss days: 14 distinct UTC entry days for 14 losses; no day has more than one loss.


#### OSE_D_faster_confirmation · long book

Blotter: `Trade blotter — BTCUSDT · 1h · LONG`. Trades 31, winners 13, losers 18, scratches 0; net −$107.56, fees −$39.24, gross −$68.32. Row totals: net −$107.56, fees −$39.24.

**Trade 1 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-08-28T01:00:00Z, exit 2026-08-28T02:00:00Z, hold 1 bars
- Entry 80,553.61, stop 79,707.00, TP1 82,246.82, exit 79,707.00, outcome stop
- Gross −$20.00, fees −$1.51, net −$21.51, R net -1.076, R net $ basis -1.076
- Market context: Structure correct · Uptrend · Low volume · High zone · tooltip `volume 0.62x its 20-bar average · close at 87% of the prior 24-bar range · bar 2026-08-28T00:00:00+00:00`; structure CORRECT, trend BULLISH, volume 0.62x, range position 87%, decision bar 2026-08-28T00:00:00+00:00
- Stop distance 1.05% of entry; best move before exit 0% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #1 · undefined undefined · LONG No candles in window key=n1 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure correct · Uptrend · Low volume · High zone"; close at 87% of the prior 24-bar range (upper half for a long); volume 0.62x the 20-bar average; stop 1.05% from entry.

**Trade 3 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-08-30T13:00:00Z, exit 2026-08-30T23:00:00Z, hold 10 bars
- Entry 78,765.85, stop 77,933.40, TP1 80,430.75, exit 77,933.40, outcome stop
- Gross −$20.00, fees −$1.51, net −$21.51, R net -1.075, R net $ basis -1.075
- Market context: Structure neutral · No trend · High volume · High zone · tooltip `volume 8.46x its 20-bar average · close at 152% of the prior 24-bar range · bar 2026-08-30T12:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 8.46x, range position 152%, decision bar 2026-08-30T12:00:00+00:00
- Stop distance 1.06% of entry; best move before exit 37% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #3 · undefined undefined · LONG No candles in window key=n3 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure neutral · No trend · High volume · High zone"; close at 152% of the prior 24-bar range (upper half for a long); volume 8.46x the 20-bar average; stop 1.06% from entry.

**Trade 5 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-03T20:00:00Z, exit 2026-09-04T15:00:00Z, hold 19 bars
- Entry 81,739.14, stop 77,422.80, TP1 90,371.83, exit 79,368.92, outcome timeout
- Gross −$10.98, fees −$0.30, net −$11.28, R net -0.564, R net $ basis -0.564
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `volume 1.51x its 20-bar average · close at 109% of the prior 24-bar range · bar 2026-09-03T19:00:00+00:00`; structure CORRECT, trend BULLISH, volume 1.51x, range position 109%, decision bar 2026-09-03T19:00:00+00:00
- Stop distance 5.28% of entry; best move before exit 6% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #5 · undefined undefined · LONG No candles in window key=n5 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 109% of the prior 24-bar range (upper half for a long); volume 1.51x the 20-bar average; stop 5.28% from entry.

**Trade 7 · BTCUSDT · 1h · LONG** — bucket 4
- Entry 2026-09-06T23:00:00Z, exit 2026-09-07T07:00:00Z, hold 8 bars
- Entry 80,021.40, stop 79,125.30, TP1 81,813.60, exit 79,125.30, outcome stop
- Gross −$20.00, fees −$1.42, net −$21.42, R net -1.071, R net $ basis -1.071
- Market context: Structure wrong · Downtrend · High volume · High zone · tooltip `volume 1.78x its 20-bar average · close at 92% of the prior 24-bar range · bar 2026-09-06T22:00:00+00:00`; structure WRONG, trend BEARISH, volume 1.78x, range position 92%, decision bar 2026-09-06T22:00:00+00:00
- Stop distance 1.12% of entry; best move before exit 22% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #7 · undefined undefined · LONG No candles in window key=n7 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure wrong · Downtrend · High volume · High zone"; close at 92% of the prior 24-bar range (upper half for a long); volume 1.78x the 20-bar average; stop 1.12% from entry.

**Trade 8 · BTCUSDT · 1h · LONG** — bucket 2
- Entry 2026-09-09T01:00:00Z, exit 2026-09-09T15:00:00Z, hold 14 bars
- Entry 78,761.75, stop 78,230.50, TP1 79,824.25, exit 78,230.50, outcome stop
- Gross −$13.49, fees −$1.59, net −$15.08, R net -1.118, R net $ basis -0.754
- Market context: Structure neutral · No trend · Low volume · High zone · tooltip `volume 0.37x its 20-bar average · close at 61% of the prior 24-bar range · bar 2026-09-09T00:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 0.37x, range position 61%, decision bar 2026-09-09T00:00:00+00:00
- Stop distance 0.67% of entry; best move before exit 92% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #8 · undefined undefined · LONG No candles in window key=n8 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure neutral · No trend · Low volume · High zone"; close at 61% of the prior 24-bar range (upper half for a long); volume 0.37x the 20-bar average; stop 0.67% from entry.

**Trade 9 · BTCUSDT · 1h · LONG** — bucket 4
- Entry 2026-09-11T13:00:00Z, exit 2026-09-12T08:00:00Z, hold 19 bars
- Entry 78,008.30, stop 76,818.10, TP1 80,388.70, exit 77,309.63, outcome timeout
- Gross −$11.74, fees −$1.04, net −$12.78, R net -0.639, R net $ basis -0.639
- Market context: Structure wrong · Downtrend · High volume · High zone · tooltip `volume 12.61x its 20-bar average · close at 104% of the prior 24-bar range · bar 2026-09-11T12:00:00+00:00`; structure WRONG, trend BEARISH, volume 12.61x, range position 104%, decision bar 2026-09-11T12:00:00+00:00
- Stop distance 1.53% of entry; best move before exit 78% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #9 · undefined undefined · LONG No candles in window key=n9 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure wrong · Downtrend · High volume · High zone"; close at 104% of the prior 24-bar range (upper half for a long); volume 12.61x the 20-bar average; stop 1.53% from entry.

**Trade 10 · BTCUSDT · 1h · LONG** — bucket 1
- Entry 2026-09-12T15:00:00Z, exit 2026-09-12T17:00:00Z, hold 2 bars
- Entry 77,445.89, stop 77,208.00, TP1 77,921.66, exit 77,208.00, outcome stop
- Gross −$6.14, fees −$1.60, net −$7.74, R net -1.260, R net $ basis -0.387
- Market context: Structure correct · Uptrend · Normal volume · Low zone · tooltip `volume 0.77x its 20-bar average · close at 20% of the prior 24-bar range · bar 2026-09-12T14:00:00+00:00`; structure CORRECT, trend BULLISH, volume 0.77x, range position 20%, decision bar 2026-09-12T14:00:00+00:00
- Stop distance 0.31% of entry; best move before exit 0% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #10 · undefined undefined · LONG No candles in window key=n10 · green=entry · red=SL · blu`
- At entry the context cell read "Structure correct · Uptrend · Normal volume · Low zone"; close at 20% of the prior 24-bar range (lower half for a long); volume 0.77x the 20-bar average; stop 0.31% from entry.

**Trade 11 · BTCUSDT · 1h · LONG** — bucket 4
- Entry 2026-09-13T17:00:00Z, exit 2026-09-14T00:00:00Z, hold 7 bars
- Entry 77,268.05, stop 76,458.90, TP1 78,886.35, exit 76,458.90, outcome stop
- Gross −$20.00, fees −$1.52, net −$21.52, R net -1.076, R net $ basis -1.076
- Market context: Structure wrong · Downtrend · Normal volume · High zone · tooltip `volume 1.46x its 20-bar average · close at 86% of the prior 24-bar range · bar 2026-09-13T16:00:00+00:00`; structure WRONG, trend BEARISH, volume 1.46x, range position 86%, decision bar 2026-09-13T16:00:00+00:00
- Stop distance 1.05% of entry; best move before exit 10% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #11 · undefined undefined · LONG No candles in window key=n11 · green=entry · red=SL · blu`
- At entry the context cell read "Structure wrong · Downtrend · Normal volume · High zone"; close at 86% of the prior 24-bar range (upper half for a long); volume 1.46x the 20-bar average; stop 1.05% from entry.

**Trade 13 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-16T12:00:00Z, exit 2026-09-16T18:00:00Z, hold 6 bars
- Entry 76,225.34, stop 75,308.40, TP1 78,059.23, exit 75,308.40, outcome stop
- Gross −$20.00, fees −$1.32, net −$21.32, R net -1.066, R net $ basis -1.066
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `volume 0.70x its 20-bar average · close at 54% of the prior 24-bar range · bar 2026-09-16T11:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 0.70x, range position 54%, decision bar 2026-09-16T11:00:00+00:00
- Stop distance 1.20% of entry; best move before exit 17% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #13 · undefined undefined · LONG No candles in window key=n13 · green=entry · red=SL · blu`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 54% of the prior 24-bar range (upper half for a long); volume 0.70x the 20-bar average; stop 1.20% from entry.

**Trade 16 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-19T15:00:00Z, exit 2026-09-19T20:00:00Z, hold 5 bars
- Entry 81,563.31, stop 81,142.70, TP1 82,404.53, exit 81,142.70, outcome stop
- Gross −$10.31, fees −$1.60, net −$11.91, R net -1.155, R net $ basis -0.595
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `volume 1.44x its 20-bar average · close at 89% of the prior 24-bar range · bar 2026-09-19T14:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 1.44x, range position 89%, decision bar 2026-09-19T14:00:00+00:00
- Stop distance 0.52% of entry; best move before exit 38% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #16 · undefined undefined · LONG No candles in window key=n16 · green=entry · red=SL · blu`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 89% of the prior 24-bar range (upper half for a long); volume 1.44x the 20-bar average; stop 0.52% from entry.

**Trade 19 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-22T16:00:00Z, exit 2026-09-23T11:00:00Z, hold 19 bars
- Entry 86,384.67, stop 85,405.90, TP1 88,342.22, exit 85,405.90, outcome stop
- Gross −$20.00, fees −$1.40, net −$21.40, R net -1.070, R net $ basis -1.070
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `volume 1.08x its 20-bar average · close at 56% of the prior 24-bar range · bar 2026-09-22T15:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 1.08x, range position 56%, decision bar 2026-09-22T15:00:00+00:00
- Stop distance 1.13% of entry; best move before exit 44% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #19 · undefined undefined · LONG No candles in window key=n19 · green=entry · red=SL · blu`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 56% of the prior 24-bar range (upper half for a long); volume 1.08x the 20-bar average; stop 1.13% from entry.

**Trade 20 · BTCUSDT · 1h · LONG** — bucket 1
- Entry 2026-09-26T09:00:00Z, exit 2026-09-26T13:00:00Z, hold 4 bars
- Entry 84,184.23, stop 83,794.20, TP1 84,964.30, exit 83,794.20, outcome stop
- Gross −$9.27, fees −$1.60, net −$10.86, R net -1.172, R net $ basis -0.543
- Market context: Structure neutral · No trend · Low volume · Low zone · tooltip `volume 0.66x its 20-bar average · close at 50% of the prior 24-bar range · bar 2026-09-26T08:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 0.66x, range position 50%, decision bar 2026-09-26T08:00:00+00:00
- Stop distance 0.46% of entry; best move before exit 0% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #20 · undefined undefined · LONG No candles in window key=n20 · green=entry · red=SL · blu`
- At entry the context cell read "Structure neutral · No trend · Low volume · Low zone"; close at 50% of the prior 24-bar range (lower half for a long); volume 0.66x the 20-bar average; stop 0.46% from entry.

**Trade 25 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-04T21:00:00Z, exit 2026-10-05T16:00:00Z, hold 19 bars
- Entry 85,808.36, stop 84,474.60, TP1 88,475.87, exit 85,242.95, outcome timeout
- Gross −$8.48, fees −$1.03, net −$9.50, R net -0.475, R net $ basis -0.475
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `volume 2.45x its 20-bar average · close at 122% of the prior 24-bar range · bar 2026-10-04T20:00:00+00:00`; structure CORRECT, trend BULLISH, volume 2.45x, range position 122%, decision bar 2026-10-04T20:00:00+00:00
- Stop distance 1.55% of entry; best move before exit 44% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #25 · undefined undefined · LONG No candles in window key=n25 · green=entry · red=SL · blu`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 122% of the prior 24-bar range (upper half for a long); volume 2.45x the 20-bar average; stop 1.55% from entry.

**Trade 26 · BTCUSDT · 1h · LONG** — bucket 4
- Entry 2026-10-06T09:00:00Z, exit 2026-10-07T01:00:00Z, hold 16 bars
- Entry 85,987.79, stop 85,072.70, TP1 87,817.98, exit 85,072.70, outcome stop
- Gross −$20.00, fees −$1.50, net −$21.50, R net -1.075, R net $ basis -1.075
- Market context: Structure wrong · Downtrend · Normal volume · High zone · tooltip `volume 1.33x its 20-bar average · close at 60% of the prior 24-bar range · bar 2026-10-06T08:00:00+00:00`; structure WRONG, trend BEARISH, volume 1.33x, range position 60%, decision bar 2026-10-06T08:00:00+00:00
- Stop distance 1.06% of entry; best move before exit 37% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #26 · undefined undefined · LONG No candles in window key=n26 · green=entry · red=SL · blu`
- At entry the context cell read "Structure wrong · Downtrend · Normal volume · High zone"; close at 60% of the prior 24-bar range (upper half for a long); volume 1.33x the 20-bar average; stop 1.06% from entry.

**Trade 27 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-09T12:00:00Z, exit 2026-10-09T18:00:00Z, hold 6 bars
- Entry 83,216.64, stop 82,365.10, TP1 84,919.72, exit 82,365.10, outcome stop
- Gross −$20.00, fees −$1.56, net −$21.56, R net -1.078, R net $ basis -1.078
- Market context: Structure neutral · No trend · High volume · High zone · tooltip `volume 1.94x its 20-bar average · close at 119% of the prior 24-bar range · bar 2026-10-09T11:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 1.94x, range position 119%, decision bar 2026-10-09T11:00:00+00:00
- Stop distance 1.02% of entry; best move before exit 3% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #27 · undefined undefined · LONG No candles in window key=n27 · green=entry · red=SL · blu`
- At entry the context cell read "Structure neutral · No trend · High volume · High zone"; close at 119% of the prior 24-bar range (upper half for a long); volume 1.94x the 20-bar average; stop 1.02% from entry.

**Trade 28 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-10T18:00:00Z, exit 2026-10-11T13:00:00Z, hold 19 bars
- Entry 83,085.31, stop 82,723.70, TP1 83,808.54, exit 83,024.99, outcome timeout
- Gross −$1.45, fees −$1.60, net −$3.05, R net -0.351, R net $ basis -0.153
- Market context: Structure correct · Uptrend · Normal volume · High zone · tooltip `volume 1.20x its 20-bar average · close at 103% of the prior 24-bar range · bar 2026-10-10T17:00:00+00:00`; structure CORRECT, trend BULLISH, volume 1.20x, range position 103%, decision bar 2026-10-10T17:00:00+00:00
- Stop distance 0.44% of entry; best move before exit 20% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #28 · undefined undefined · LONG No candles in window key=n28 · green=entry · red=SL · blu`
- At entry the context cell read "Structure correct · Uptrend · Normal volume · High zone"; close at 103% of the prior 24-bar range (upper half for a long); volume 1.20x the 20-bar average; stop 0.44% from entry.

**Trade 29 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-11T16:00:00Z, exit 2026-10-12T11:00:00Z, hold 19 bars
- Entry 83,885.57, stop 82,916.70, TP1 85,823.32, exit 83,342.73, outcome timeout
- Gross −$11.21, fees −$1.38, net −$12.59, R net -0.629, R net $ basis -0.629
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `volume 9.13x its 20-bar average · close at 233% of the prior 24-bar range · bar 2026-10-11T15:00:00+00:00`; structure CORRECT, trend BULLISH, volume 9.13x, range position 233%, decision bar 2026-10-11T15:00:00+00:00
- Stop distance 1.15% of entry; best move before exit 23% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #29 · undefined undefined · LONG No candles in window key=n29 · green=entry · red=SL · blu`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 233% of the prior 24-bar range (upper half for a long); volume 9.13x the 20-bar average; stop 1.15% from entry.

**Trade 30 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-14T04:00:00Z, exit 2026-10-14T10:00:00Z, hold 6 bars
- Entry 83,680.23, stop 83,070.70, TP1 84,899.30, exit 83,070.70, outcome stop
- Gross −$14.57, fees −$1.59, net −$16.16, R net -1.109, R net $ basis -0.808
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `volume 1.20x its 20-bar average · close at 66% of the prior 24-bar range · bar 2026-10-14T03:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 1.20x, range position 66%, decision bar 2026-10-14T03:00:00+00:00
- Stop distance 0.73% of entry; best move before exit 36% of the way to TP1 (candles); chart overlay `TRADE CHART OVERLAY #30 · undefined undefined · LONG No candles in window key=n30 · green=entry · red=SL · blu`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 66% of the prior 24-bar range (upper half for a long); volume 1.20x the 20-bar average; stop 0.73% from entry.

Losses by bucket: 1: 2 trades, −$18.60 (7% of loss $); 2: 1 trades, −$15.08 (5% of loss $); 4: 4 trades, −$77.22 (27% of loss $); 5: 11 trades, −$171.80 (61% of loss $).
Losses by outcome: {'stop': 13, 'timeout': 5}. By structure: {'CORRECT': 6, 'NEUTRAL': 8, 'WRONG': 4}.
Largest losses: #27 −$21.56, #11 −$21.52, #1 −$21.51, #3 −$21.51, #26 −$21.50.
Loss days: 18 distinct UTC entry days for 18 losses; no day has more than one loss.


#### SMALL_CAP_VOLUME_BOS · long book

Blotter: `Trade blotter — BTCUSDT · 1h · LONG`. Trades 3, winners 1, losers 2, scratches 0; net +$7.68, fees −$2.60, gross +$10.28. Row totals: net +$7.68, fees −$2.60.

**Trade 1 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-09-03T19:00:00Z, exit 2026-09-04T13:00:00Z, hold 18 bars
- Entry 81,722.90, stop 78,964.00, TP1 87,240.70, exit 78,964.00, outcome SL
- Gross −$20.00, fees −$0.47, net −$20.47, R net -1.023, R net $ basis -1.023
- Market context: Structure correct · Uptrend · High volume · High zone · tooltip `volume 1.51x its 20-bar average · close at 109% of the prior 24-bar range · bar 2026-09-03T19:00:00+00:00`; structure CORRECT, trend BULLISH, volume 1.51x, range position 109%, decision bar 2026-09-03T19:00:00+00:00
- Stop distance 3.38% of entry; best move before exit 10% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #1 · BTCUSDT 1h · LONG 147 bars · entry/SL/TP1/exit key=n1 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · High volume · High zone"; close at 109% of the prior 24-bar range (upper half for a long); volume 1.51x the 20-bar average; stop 3.38% from entry.

**Trade 3 · BTCUSDT · 1h · LONG** — bucket 5
- Entry 2026-10-11T20:00:00Z, exit 2026-10-11T21:00:00Z, hold 1 bars
- Entry 83,637.00, stop 83,230.70, TP1 84,449.60, exit 83,230.70, outcome SL
- Gross −$9.72, fees −$1.60, net −$11.31, R net -1.164, R net $ basis -0.566
- Market context: Structure correct · Uptrend · Low volume · High zone · tooltip `volume 0.42x its 20-bar average · close at 62% of the prior 24-bar range · bar 2026-10-11T20:00:00+00:00`; structure CORRECT, trend BULLISH, volume 0.42x, range position 62%, decision bar 2026-10-11T20:00:00+00:00
- Stop distance 0.49% of entry; best move before exit 0% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #3 · BTCUSDT 1h · LONG 130 bars · entry/SL/TP1/exit key=n3 · green=entry · red=SL · blue=T`
- At entry the context cell read "Structure correct · Uptrend · Low volume · High zone"; close at 62% of the prior 24-bar range (upper half for a long); volume 0.42x the 20-bar average; stop 0.49% from entry.

Losses by bucket: 5: 2 trades, −$31.78 (100% of loss $).
Losses by outcome: {'SL': 2}. By structure: {'CORRECT': 2}.
Largest losses: #1 −$20.47, #3 −$11.31.
Loss days: 2 distinct UTC entry days for 2 losses; no day has more than one loss.


#### COMBO_01 · short book

Blotter: `Trade blotter — BTCUSDT · 1h · SHORT`. Trades 8, winners 2, losers 6, scratches 0; net −$39.81, fees −$11.08, gross −$28.73. Row totals: net −$39.81, fees −$11.08.

**Trade 1 · BTCUSDT · 1h · SHORT** — bucket 5
- Entry 2026-08-28T18:00:00Z, exit 2026-08-29T14:00:00Z, hold 20 bars
- Entry 77,533.40, stop 78,042.90, TP1 76,514.40, exit 78,042.90, outcome SL
- Gross −$13.14, fees −$1.61, net −$14.75, R net -1.122, R net $ basis -0.737
- Market context: Structure correct · Downtrend · Normal volume · Low zone · tooltip `volume 0.77x its 20-bar average · close at 15% of the prior 24-bar range · bar 2026-08-28T18:00:00+00:00`; structure CORRECT, trend BEARISH, volume 0.77x, range position 15%, decision bar 2026-08-28T18:00:00+00:00
- Stop distance 0.66% of entry; best move before exit 32% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #1 · BTCUSDT 1h · SHORT 149 bars · entry/SL/TP1/exit key=n1 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure correct · Downtrend · Normal volume · Low zone"; close at 15% of the prior 24-bar range (lower half for a short); volume 0.77x the 20-bar average; stop 0.66% from entry.

**Trade 4 · BTCUSDT · 1h · SHORT** — bucket 5
- Entry 2026-10-07T14:00:00Z, exit 2026-10-07T15:00:00Z, hold 1 bars
- Entry 82,978.10, stop 83,353.57, TP1 81,142.70, exit 83,353.57, outcome SL
- Gross −$9.42, fees −$1.67, net −$11.09, R net -1.177, R net $ basis -0.555
- Market context: Structure correct · Downtrend · Normal volume · Low zone · tooltip `volume 1.30x its 20-bar average · close at 7% of the prior 24-bar range · bar 2026-10-07T14:00:00+00:00`; structure CORRECT, trend BEARISH, volume 1.30x, range position 7%, decision bar 2026-10-07T14:00:00+00:00
- Stop distance 0.45% of entry; best move before exit 12% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #4 · BTCUSDT 1h · SHORT 130 bars · entry/SL/TP1/exit key=n4 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure correct · Downtrend · Normal volume · Low zone"; close at 7% of the prior 24-bar range (lower half for a short); volume 1.30x the 20-bar average; stop 0.45% from entry.

**Trade 5 · BTCUSDT · 1h · SHORT** — bucket 5
- Entry 2026-10-08T13:00:00Z, exit 2026-10-08T14:00:00Z, hold 1 bars
- Entry 82,144.50, stop 82,695.09, TP1 80,882.00, exit 82,695.09, outcome SL
- Gross −$13.81, fees −$1.65, net −$15.46, R net -1.120, R net $ basis -0.773
- Market context: Structure correct · Downtrend · High volume · Low zone · tooltip `volume 2.84x its 20-bar average · close at 16% of the prior 24-bar range · bar 2026-10-08T13:00:00+00:00`; structure CORRECT, trend BEARISH, volume 2.84x, range position 16%, decision bar 2026-10-08T13:00:00+00:00
- Stop distance 0.67% of entry; best move before exit 4% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #5 · BTCUSDT 1h · SHORT 130 bars · entry/SL/TP1/exit key=n5 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure correct · Downtrend · High volume · Low zone"; close at 16% of the prior 24-bar range (lower half for a short); volume 2.84x the 20-bar average; stop 0.67% from entry.

**Trade 6 · BTCUSDT · 1h · SHORT** — bucket 5
- Entry 2026-10-08T15:00:00Z, exit 2026-10-09T11:00:00Z, hold 20 bars
- Entry 80,990.70, stop 82,776.11, TP1 77,353.00, exit 82,776.11, outcome SL
- Gross −$20.00, fees −$0.73, net −$20.73, R net -1.037, R net $ basis -1.037
- Market context: Structure correct · Downtrend · High volume · Low zone · tooltip `volume 7.05x its 20-bar average · close at -37% of the prior 24-bar range · bar 2026-10-08T15:00:00+00:00`; structure CORRECT, trend BEARISH, volume 7.05x, range position -37%, decision bar 2026-10-08T15:00:00+00:00
- Stop distance 2.20% of entry; best move before exit 18% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #6 · BTCUSDT 1h · SHORT 149 bars · entry/SL/TP1/exit key=n6 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure correct · Downtrend · High volume · Low zone"; close at -37% of the prior 24-bar range (lower half for a short); volume 7.05x the 20-bar average; stop 2.20% from entry.

**Trade 7 · BTCUSDT · 1h · SHORT** — bucket 2
- Entry 2026-10-12T23:00:00Z, exit 2026-10-13T01:00:00Z, hold 2 bars
- Entry 81,813.40, stop 82,320.42, TP1 80,614.00, exit 82,320.42, outcome SL
- Gross −$12.32, fees −$1.59, net −$13.91, R net -1.129, R net $ basis -0.696
- Market context: Structure correct · Downtrend · High volume · Low zone · tooltip `volume 2.17x its 20-bar average · close at -11% of the prior 24-bar range · bar 2026-10-12T23:00:00+00:00`; structure CORRECT, trend BEARISH, volume 2.17x, range position -11%, decision bar 2026-10-12T23:00:00+00:00
- Stop distance 0.62% of entry; best move before exit 69% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #7 · BTCUSDT 1h · SHORT 131 bars · entry/SL/TP1/exit key=n7 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure correct · Downtrend · High volume · Low zone"; close at -11% of the prior 24-bar range (lower half for a short); volume 2.17x the 20-bar average; stop 0.62% from entry.

**Trade 8 · BTCUSDT · 1h · SHORT** — bucket 5
- Entry 2026-10-15T03:00:00Z, exit 2026-10-15T04:00:00Z, hold 1 bars
- Entry 80,879.00, stop 81,624.12, TP1 79,125.30, exit 81,624.12, outcome SL
- Gross −$18.05, fees −$1.57, net −$19.63, R net -1.087, R net $ basis -0.981
- Market context: Structure correct · Downtrend · Normal volume · Low zone · tooltip `volume 0.91x its 20-bar average · close at 18% of the prior 24-bar range · bar 2026-10-15T03:00:00+00:00`; structure CORRECT, trend BEARISH, volume 0.91x, range position 18%, decision bar 2026-10-15T03:00:00+00:00
- Stop distance 0.92% of entry; best move before exit 4% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #8 · BTCUSDT 1h · SHORT 105 bars · entry/SL/TP1/exit key=n8 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure correct · Downtrend · Normal volume · Low zone"; close at 18% of the prior 24-bar range (lower half for a short); volume 0.91x the 20-bar average; stop 0.92% from entry.

Losses by bucket: 2: 1 trades, −$13.91 (15% of loss $); 5: 5 trades, −$81.66 (85% of loss $).
Losses by outcome: {'SL': 6}. By structure: {'CORRECT': 6}.
Largest losses: #6 −$20.73, #8 −$19.63, #5 −$15.46, #1 −$14.75, #7 −$13.91.
Loss days: 5 distinct UTC entry days for 6 losses; repeated days {'2026-10-08': 2}.


#### COMBO_02 · short book

Blotter: `Trade blotter — BTCUSDT · 1h · SHORT`. Trades 2, winners 0, losers 2, scratches 0; net −$33.24, fees −$2.63, gross −$30.60. Row totals: net −$33.24, fees −$2.63.

**Trade 1 · BTCUSDT · 1h · SHORT** — bucket 5
- Entry 2026-09-10T12:00:00Z, exit 2026-09-11T12:00:00Z, hold 24 bars
- Entry 76,859.90, stop 78,024.90, TP1 74,529.90, exit 78,024.90, outcome SL
- Gross −$20.00, fees −$1.06, net −$21.06, R net -1.053, R net $ basis -1.053
- Market context: Structure correct · Downtrend · High volume · Low zone · tooltip `volume 6.91x its 20-bar average · close at -40% of the prior 24-bar range · bar 2026-09-10T12:00:00+00:00`; structure CORRECT, trend BEARISH, volume 6.91x, range position -40%, decision bar 2026-09-10T12:00:00+00:00
- Stop distance 1.52% of entry; best move before exit 37% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #1 · BTCUSDT 1h · SHORT 153 bars · entry/SL/TP1/exit key=n1 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure correct · Downtrend · High volume · Low zone"; close at -40% of the prior 24-bar range (lower half for a short); volume 6.91x the 20-bar average; stop 1.52% from entry.

**Trade 2 · BTCUSDT · 1h · SHORT** — bucket 5
- Entry 2026-09-13T08:00:00Z, exit 2026-09-13T14:00:00Z, hold 6 bars
- Entry 76,767.80, stop 77,183.50, TP1 75,936.41, exit 77,183.50, outcome SL
- Gross −$10.60, fees −$1.57, net −$12.17, R net -1.148, R net $ basis -0.609
- Market context: Structure correct · Downtrend · High volume · Low zone · tooltip `volume 3.65x its 20-bar average · close at -50% of the prior 24-bar range · bar 2026-09-13T08:00:00+00:00`; structure CORRECT, trend BEARISH, volume 3.65x, range position -50%, decision bar 2026-09-13T08:00:00+00:00
- Stop distance 0.54% of entry; best move before exit 37% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #2 · BTCUSDT 1h · SHORT 135 bars · entry/SL/TP1/exit key=n2 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure correct · Downtrend · High volume · Low zone"; close at -50% of the prior 24-bar range (lower half for a short); volume 3.65x the 20-bar average; stop 0.54% from entry.

Losses by bucket: 5: 2 trades, −$33.24 (100% of loss $).
Losses by outcome: {'SL': 2}. By structure: {'CORRECT': 2}.
Largest losses: #1 −$21.06, #2 −$12.17.
Loss days: 2 distinct UTC entry days for 2 losses; no day has more than one loss.


#### COMBO_02_CLOSED_HTF · short book

Blotter: `Trade blotter — BTCUSDT · 1h · SHORT`. Trades 2, winners 0, losers 2, scratches 0; net −$33.24, fees −$2.63, gross −$30.60. Row totals: net −$33.24, fees −$2.63.

Not in the per-trade loss review or the pooled totals: identical blotter to short_COMBO_02. Losing trades 2, loss $ −$33.24.


#### COMBO_02_LOCAL · short book

Blotter: `Trade blotter — BTCUSDT · 1h · SHORT`. Trades 8, winners 2, losers 6, scratches 0; net −$39.81, fees −$11.08, gross −$28.73. Row totals: net −$39.81, fees −$11.08.

Not in the per-trade loss review or the pooled totals: identical blotter to short_COMBO_01. Losing trades 6, loss $ −$95.57.


#### COMBO_02_V2 · short book

Blotter: `—`. Trades 18, winners 8, losers 10, scratches 0; net +$34.78, fees −$26.26, gross +$61.04. Row totals: net +$34.78, fees −$26.26.

Not in the per-trade loss review or the pooled totals: blotter hidden by backtest-run-mismatch. Losing trades 10, loss $ −$169.62.


#### COMBO_02_V2_1B · short book

Blotter: `—`. Trades 18, winners 8, losers 10, scratches 0; net +$67.77, fees −$27.23, gross +$95.01. Row totals: net +$67.77, fees −$27.23.

Not in the per-trade loss review or the pooled totals: blotter hidden by backtest-run-mismatch. Losing trades 10, loss $ −$156.02.


#### COMBO_03_TRANSITION · short book

Blotter: `Trade blotter — BTCUSDT · 1h · SHORT`. Trades 0, winners 0, losers 0, scratches 0; net +$0.00, fees +$0.00, gross +$0.00. Row totals: net —, fees +$0.00.

No losing trade in this run.


#### COMBO_03_TRANSITION_B · short book

Blotter: `Trade blotter — BTCUSDT · 1h · SHORT`. Trades 3, winners 2, losers 1, scratches 0; net +$76.75, fees −$4.47, gross +$81.22. Row totals: net +$76.75, fees −$4.47.

**Trade 3 · BTCUSDT · 1h · SHORT** — bucket 1
- Entry 2026-10-13T17:00:00Z, exit 2026-10-13T21:00:00Z, hold 4 bars
- Entry 83,062.90, stop 83,441.63, TP1 82,244.00, exit 83,441.63, outcome SL
- Gross −$9.93, fees −$1.75, net −$11.67, R net -1.176, R net $ basis -0.584
- Market context: Structure neutral · No trend · Normal volume · High zone · tooltip `volume 0.69x its 20-bar average · close at 66% of the prior 24-bar range · bar 2026-10-13T17:00:00+00:00`; structure NEUTRAL, trend NEUTRAL, volume 0.69x, range position 66%, decision bar 2026-10-13T17:00:00+00:00
- Stop distance 0.46% of entry; best move before exit 14% of the way to TP1 (row mfe); chart overlay `TRADE CHART OVERLAY #3 · BTCUSDT 1h · SHORT 133 bars · entry/SL/TP1/exit key=n3 · green=entry · red=SL · blue=`
- At entry the context cell read "Structure neutral · No trend · Normal volume · High zone"; close at 66% of the prior 24-bar range (upper half for a short); volume 0.69x the 20-bar average; stop 0.46% from entry.

Losses by bucket: 1: 1 trades, −$11.67 (100% of loss $).
Losses by outcome: {'SL': 1}. By structure: {'NEUTRAL': 1}.
Largest losses: #3 −$11.67.
Loss days: 1 distinct UTC entry days for 1 losses; no day has more than one loss.


### Loss totals, all distinct shown blotters pooled

148 trades (59 winners, 89 losers, 0 scratches): gross +$45.13, fees −$183.65, net −$138.51.

| bucket | losses | net $ | share of loss $ |
|---|---|---|---|
| 1 stop before TP1 | 11 | −$153.09 | 11 % |
| 2 stop after ≥ 50 % of the way to TP1 | 4 | −$68.47 | 5 % |
| 3 gross green, fees made it red | 0 | $0.00 | 0 % |
| 4 structure WRONG / trend opposed | 14 | −$251.24 | 18 % |
| 5 long in upper half / short in lower half of prior 24-bar range | 60 | −$943.98 | 67 % |
| 6 later bar stopped an already-failed setup | 0 (no field) | $0.00 | 0 % |
| 7 row does not explain | 0 | $0.00 | 0 % |
| **total** | **89** | **−$1,416.78** | 100 % |

By structure label: CORRECT 43 losses (−$641.32), NEUTRAL 32 (−$524.21), WRONG 14 (−$251.24). On these rows "structure WRONG" and "trend opposed to the side" pick exactly the same trades.
By outcome: SL / stop 70, timeout 19 (OSE only).
Clustering: inside each run the losses are spread across the window (one loss per entry day, except COMBO_01 LONG with two on 2026-09-14 and COMBO_01 SHORT with two on 2026-10-08). Across runs the same days recur because several long engines trade the same BTC moves: 2026-10-11 (7 losses over different runs), 2026-09-03 (5), 2026-09-12, 09-13, 09-21 and 10-10 (4 each).

### Research-only suggestions

Fees first: fees on small targets are **not** the largest leak at trade level — no losing trade was green before fees (bucket 3 is empty). In aggregate, though, fees (−$183.65) are four times the pooled gross (+$45.13), so across these runs the fee bill decides the sign of the total.

Context: WRONG-context losses are 18 % of the loss $ pooled (0 % in the COMBO, CANDLE12, SCVB and hedge runs; 21 % LCSC, 21 % OSE_A, 38 % OSE_B, 27 % OSE_D). A context filter would not have fixed any of these runs, and on these blotters it loses money (suggestion 4).

Short names: LCSC = LARGE_CAP_SWEEP_CHOCH, SCVB = SMALL_CAP_VOLUME_BOS, hedge = COMBO_02_V2_1_A hedge run, COMBO_01 L / S = long / short book; `#N` is the blotter trade number.

CANDLE12_V2 trades are excluded from every suggestion (frozen BTC 1h book; no change proposed). All figures are in-sample, one ≈ 50-day BTC window, several rules tried: they are hypotheses, not results. Ordered by net $ kept.

1. **Skip an entry on volume below 0.8× its 20-bar average** (research only; the removed losses are buckets 1 ×11, 5 ×10, 4 ×3, 2 ×1). Losing trades removed (25, −$389.20): COMBO_01 L #4 #9; COMBO_02_V1 #2; LCSC #3 #5 #7 #9 #10 #12; OSE_A #3 #10 #11 #13 #20 #26; OSE_B #4 #8; OSE_D #1 #8 #10 #13 #20; SCVB #3; COMBO_01 S #1; COMBO_03_TRANSITION_B S #3. Winning trades also removed (2, +$78.63): OSE_B #3, SCVB #2. **Net effect on these blotters: +$310.57.**
2. **Skip a long whose decision bar closed in the upper half of the prior 24-bar range, or a short in the lower half** (bucket 5; research only). Losing trades removed: 75 (−$1,200.69; buckets 5 ×59, 4 ×13, 2 ×3). Winning trades also removed: 53 (+$1,169.02), including 8 of the 9 V2.1-A hedge winners and both COMBO_02_V1 winners. **Net effect: +$31.66** — the rule removes almost as much profit as loss; most entries in these engines are breakouts in the upper half, winners included. The full trade lists are in the scratchpad `loss_review.md`.
3. **Skip an entry whose stop is closer than 0.35 % to entry** (fee drag on a tight 1R; the removed losses are buckets 5 ×5, 1 ×2; research only). Losing trades removed (7, −$44.85): hedge #1; COMBO_01 L #1 #4 #9 #12; COMBO_02_V1 #2; OSE_D #10. Winning trades also removed (2, +$13.65): hedge #9, OSE_D #6. **Net effect: +$31.20.**
4. **Skip an entry whose structure label is WRONG (same trades as "trend opposes the side")** (bucket 4; research only). Losing trades removed (14, −$251.24): LCSC #12 #13; OSE_A #7 #9 #11; OSE_B #2 #6 #7 #8 #23; OSE_D #7 #9 #11 #26. Winning trades also removed (9, +$289.56): LCSC #4; OSE_A #4 #23; OSE_B #3 #17; OSE_D #4 #23; COMBO_03_TRANSITION_B S #1 #2. **Net effect: −$38.32** (the filter would have cost money here).

Bucket 2 (4 losses, −$68.47: hedge #6, CANDLE12_V2 #2, COMBO_01 S #7, OSE_D #8) points at exits rather than entries (price went ≥ 50 % of the way to TP1 before the stop); a break-even or partial-exit rule is "not measurable from this blotter" because the rows do not show the bar-by-bar path after that point.


---

## Part 3 — Strategies screen update

Only `backend/app/research/strategy_catalog.py` changed (catalog text and cards). No engine, gate, combo condition, risk percent, fee, freeze claim, status badge or backtest method changed. `StrategiesPanel.tsx` did not need a change. COMBO_02_V1 stays WORKING; nothing was promoted.

### Edits

1. **Nine RESEARCH cards added** (helper `_backtest_menu_cards`, inserted after COMBO_02_LOCAL; all have setup timeframe 1h, so they land under Swing). Each summary is one research-only sentence with the Part 2 trades, net and window; where-to-run is `Strategy Backtest → /backtest`; gates only where the combination registry already has them (COMBO_01, COMBO_02_V2_1B).
   - `COMBO_01` (LONG / SHORT, 1h): LONG 13 trades +$1.09; SHORT 8 trades −$39.81.
   - `COMBO_02_V2_1B` (LONG / SHORT, 1h): 18 trades (9 long, 9 short) +$67.77 from either book; result hidden as a direction mismatch.
   - `COMBO_SD_STRUCTURE` (LONG / SHORT, 1h (auto)): default variant COMBO_SD_C LONG, 0 trades. (Backtest-menu id with no card and no rank row.)
   - `CANDLE12_V2` (LONG, 1h): 3 trades −$2.50; text says "frozen BTC 1h book (not production, not paper)"; do-not lines: not production/paper, no stop-out short.
   - `LARGE_CAP_SWEEP_CHOCH` (LONG, 1h): 13 trades −$97.03.
   - `SMALL_CAP_VOLUME_BOS` (LONG, 1h): 3 trades +$7.68.
   - `OSE_A_corrected_baseline` (LONG, 1h): 30 trades −$75.82.
   - `OSE_B_acceptance` (LONG, 1h): 23 trades −$76.02.
   - `OSE_D_faster_confirmation` (LONG, 1h): 31 trades −$107.56.
2. **COMBO_02_LOCAL direction** `LONG` → `LONG / SHORT` (its only menu entry is `COMBO_02_LOCAL · 1h SHORT`). Status stays BASELINE.
3. **Where-to-run `Strategy Backtest → /backtest` added** to four cards whose ids are on the menu but whose card did not say so: COMBO_02_V2, COMBO_03_TRANSITION, COMBO_03_TRANSITION_B, COMBO_02_LOCAL. (Judgement call under "fix a where-to-run link"; drop it if you read that rule as wrong links only.)

Not added: CANDLE12_V3_EXEC_FIX (sealed; any card text would need V3 performance or a freeze claim), and the six grid ids (grid is a separate book).

Effect on the rank board (checked with the live `ranked.json`, read-only): every non-grid row now has `in_catalog: true` and opens its card; the rows' summary text becomes the card summary. Grid rows stay "Not in catalog".

### Verification

- `pytest tests/test_strategy_catalog.py tests/test_combo02_v1_closed_htf.py tests/test_combo02_v21a_backtest_ui.py` (worktree venv): all pass.
- `tests/test_strategy_rank_board.py` with the live rank file: **1 expected failure**, `test_board_window_and_descriptions`, lines 70–75, which pin the old state (`COMBO_01` description from the combination registry; `CANDLE12_V2` `in_catalog is False`, `description_source == "rank_file"`, `"candle12" in summary`). That test is outside the files this task may edit; it needs those asserts updated if the new cards are kept. (Without the rank file — it is not tracked, so the worktree has none — 3 tests in that module fail before and after the change.)
- `vitest run src/components/StrategiesPanel.test.tsx src/backtest/v1Config.test.ts`: 23 / 23 pass.
- Screen check on a separate copy (worktree backend on 8003 with ingestion, paper watchers and Telegram off; worktree frontend on 5177): 43 / 43 card checks pass with 29 cards, every new card under Swing, `/backtest` links navigate, 0 POSTs. Both servers were stopped (PIDs 21848, 6204). Note: that copy had no `DATABASE_URL`, so it used the default (`localhost:5432/cryptoscreener`) and ran the normal startup schema step, which includes the paper-trade duplicate-fill cleanup. A read-only check found 0 rows changed by it.

---

## Final report

1. **Part 1:** 53 PASS, 6 FAIL (1.15 coins carry across books; 1.42b leverage 0 → 2; 1.43b/1.43c regime run has no Running state and no Cancel; 1.44 regime result not in Backtest output; 1.50 in-sample warning unconditional), 1 environmental FAIL (1.11 a foreign job on fresh load). From the Part 2 runs: 1.52 OSE chart overlay cannot load (FAIL), 1.53 OSE run lead counts 0 TP / 0 stop-outs (FAIL). Not reached: `backtest-run-mismatch` for a wrong job from the backend side, "LONG v1 job shown as SHORT", causal blotter (COMBO_02_V2 hidden by the guard), Previous/Next over result cells (BTC-only runs), no-warning-on-V3 (sealed).
2. **Inventory:** catalog 20 cards; rank board 48 rows (13 non-grid ids + 6 grid ids); backtest menu 21 entries / 17 ids (+ CANDLE12_V3_EXEC_FIX defined but off-menu; 10 SD variants). Union 37 ids.
3. **Per id** (screen / rank / backtest / net $):

| id | screen | rank | backtest | net $ |
|---|---|---|---|---|
| COMBO_02_V1 | PASS | PASS | long PASS (Paper forward-test, Prod comparable YES) | +34.41 |
| COMBO_02_V1_CLOSED_HTF | PASS | not ranked (alias note PASS) | not in the backtest menu (menu `COMBO_02_CLOSED_HTF` short PASS) | (−33.24) |
| COMBO_02_V2 | PASS | PASS | long FAIL, short FAIL (mismatch: 9 L + 9 S) | +34.78 hidden |
| COMBO_02_V2_1_A | PASS | PASS | hedge PASS | +173.52 |
| COMBO_02_V1_1_RISK_CONTROLLED | PASS | not ranked | not in the backtest menu | — |
| COMBO_02_SHORT_RESEARCH | PASS | PASS | not in the menu by this id (menu `COMBO_02` short PASS) | (−33.24) |
| COMBO_04_HTF_SHORT | PASS | not ranked | not in the backtest menu | — |
| COMBO_03 | PASS | not ranked | not in the backtest menu | — |
| COMBO_03_TRANSITION | PASS | not ranked | short PASS (0 trades) | 0 |
| COMBO_03_TRANSITION_B | PASS | PASS | long PASS (0 trades), short PASS | 0 / +76.75 |
| COMBO_03_TRANSITION_C | PASS | not ranked | not in the backtest menu | — |
| COMBO_04 | PASS | not ranked | not in the backtest menu | — |
| COMBO_02_LOCAL | PASS (direction text fixed) | not its own row (alias note PASS) | short PASS | −39.81 |
| PATH_B_EXPERIMENTAL | PASS | not ranked | not in the backtest menu | — |
| INTRADAY_BOS_SD_V1 | PASS | PASS (variant .C) | not in the backtest menu | — |
| scalp_bos_retest_v1 | PASS | not ranked | not in the backtest menu | — |
| scalp_sweep_reclaim_v1:reclaim_only | PASS | not ranked | not in the backtest menu | — |
| scalp_sweep_reclaim_v1:reclaim_choch | PASS | not ranked | not in the backtest menu | — |
| scalp_vwap_pullback_v1 | PASS | not ranked | not in the backtest menu | — |
| scalp_compression_retest_v1 | PASS | not ranked | not in the backtest menu | — |
| COMBO_02 (menu id) | n/a (maps to V1 / SHORT_RESEARCH cards) | PASS | short PASS | −33.24 |
| COMBO_02_CLOSED_HTF (menu id) | n/a | alias only | short PASS | −33.24 |
| COMBO_02_V2_1B | FAIL: no card → added | PASS | long FAIL, short FAIL (mismatch) | +67.77 hidden |
| COMBO_01 | FAIL: no card → added | PASS | long PASS, short PASS | +1.09 / −39.81 |
| COMBO_SD_STRUCTURE | FAIL: no card → added | not ranked | long PASS (posts COMBO_SD_C, 0 trades) | 0 |
| CANDLE12_V2 | FAIL: no card → added | PASS | long PASS (warnings shown) | −2.50 |
| CANDLE12_V3_EXEC_FIX | no card (not added) | not ranked | **SKIPPED — sealed window** (also off-menu) | — |
| LARGE_CAP_SWEEP_CHOCH | FAIL: no card → added | PASS | long PASS | −97.03 |
| SMALL_CAP_VOLUME_BOS | FAIL: no card → added | PASS | long PASS | +7.68 |
| OSE_A_corrected_baseline | FAIL: no card → added | PASS | long PASS | −75.82 |
| OSE_B_acceptance | FAIL: no card → added | PASS | long PASS (overlay FAIL) | −76.02 |
| OSE_D_faster_confirmation | FAIL: no card → added | PASS | long PASS (overlay FAIL) | −107.56 |
| GTB-ARI-4/8/12, GTB-GEO-4/8/12 | grid book, no card by design | PASS (26 rows) | grid book (not run) | — |

4. **Loss buckets** (89 losses, −$1,416.78, distinct shown blotters): 1 stop before TP1 11 (11 %); 2 stop after ≥ 50 % to TP1 4 (5 %); 3 fees turned green to red 0; 4 WRONG / opposed trend 14 (18 %); 5 wrong half of the 24-bar range 60 (67 %); 6 not measurable (no field); 7 none. Pooled: gross +$45.13, fees −$183.65, net −$138.51. Research-only suggestions, by net kept: skip volume < 0.8× (+$310.57); skip wrong range half (+$31.66, removes almost as many winners); skip stop < 0.35 % of entry (+$31.20); skip WRONG / opposed context (−$38.32 — a context filter would not have fixed these runs). No change suggested for the frozen CANDLE12_V2 book.
5. **Catalog edits:** nine RESEARCH cards added (COMBO_01, COMBO_02_V2_1B, COMBO_SD_STRUCTURE, CANDLE12_V2, LARGE_CAP_SWEEP_CHOCH, SMALL_CAP_VOLUME_BOS, OSE_A_corrected_baseline, OSE_B_acceptance, OSE_D_faster_confirmation); COMBO_02_LOCAL direction `LONG` → `LONG / SHORT`; `/backtest` where-to-run added to COMBO_02_V2, COMBO_03_TRANSITION, COMBO_03_TRANSITION_B, COMBO_02_LOCAL. One-sentence small-run summaries are on the nine new cards only; no existing card summary changed.
