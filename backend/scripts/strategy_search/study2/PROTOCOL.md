# Study 2 — research protocol (written 2026-10-14 before any study-2 strategy run)

Private R&D, one account. Nothing under `backend/app` or `backend/scripts/sleeve_study` is edited. COMBO_02_V1 /
`v1-combo02-long-htf`, `docs/v1_freeze.md`, V1_SYMBOLS, REGIME_BOOK_OPEN, every `paper_eligible` flag, COMBO_02_V2_1_A
status and LIVE_EXECUTION_ALLOWED are untouched. Every rule here gets a new id `S2_*`. The paper runner is off by default.

## 0. Status of the evidence before starting
- DATA_MANIFEST.json: stored history is not five years. 16 of the 27 V1_SYMBOLS cover 2023-06-01 → 2026-10-12 on 1h/4h/1d;
  the other 11 start 2024-10-09 and are excluded (no bars in research folds F1–F3; already the strategy_search holdout).
- EXPOSURE_LEDGER.json: every stored date from 2022-10 to 2026-10-12 was inspected by earlier studies. **No clean
  historical final test exists.** The only eligible evidence for a target pass is prospective paper data after the freeze.
- Therefore: discovery and validation run on previously inspected data; the "test" split is a one-time historical
  re-check labelled contaminated; the deliverable is a frozen book plus a prospective paper plan. Status of the primary
  can only be NOT YET TESTABLE at freeze time (or TARGET NOT MET if the paper period later fails).

## 1. Objective and arithmetic
$10,000 account, ten $1,000 sleeves. Minimum $500 net per 30 days, stretch $1,000.
`net_30day_usd = total net trading P&L / elapsed calendar days × 30`; gaps = max(0, 500 − x), max(0, 1000 − x).
Constraint arithmetic (stated before any run): aggregate open stop risk ≤ min($200, 2% of account equity).
At 2% sleeve risk one trade risks $20 (1R), so $500/30d needs +25R net per 30 days across the account; at 5% risk
(1R = $50, at most four full-risk positions open) it needs +10R per 30 days. The search measures whether any rule
reaches that; it does not assume expectancy scales with capital, leverage or signal count.

## 2. Universe, windows, folds
- Universe (current-universe retrospective): ADA APT ARB AVAX BNB BTC DOGE DOT ETH LINK LTC NEAR OP SOL SUI XRP.
  15m setups only on APT AVAX BTC DOGE DOT ETH SOL (others' 15m starts 2023-09 … 2024-10).
  AVAX 1h before 2024-04-04 is built from complete groups of four stored 15m bars (real bars, no fill-in).
- Annual top-20 quote-volume universe: **not researched** — no listing/delisting history and no history for other
  contracts (symbols table is a current snapshot, all TRADING, listed_at/delisted_at NULL).
- Common window 2023-06-01 → 2026-10-13 (1,230 days, half-open). Earlier bars are warm-up only.
  - Research  2023-06-01 → 2025-09-01 (823 d, 66.9%)
  - Validation 2025-09-01 → 2026-03-24 (204 d, 16.6%)
  - Test (historical re-check, contaminated) 2026-03-24 → 2026-10-13 (203 d, 16.5%)
- Research folds: F1 2023-06-01→2024-01-01, F2 →2024-08-01, F3 →2025-02-01, F4 →2025-09-01.
  Falling-market fold = the fold with the largest share of BTC-BEARISH days (RegimeClock, bars closed before each day
  ends): F1 23.8%, F2 17.8%, F3 20.1%, **F4 41.5% → F4 is the falling fold.** (Validation: 27.5% bearish, BTC −34.5%.)
- Walk-forward inside research: anchored; for k = F2, F3, F4 pick the best variant of a candidate's registered
  neighbourhood on folds before k, record its fold-k result. Reported, not a gate.

## 3. Regimes (causal)
Labels from regimes2.py (existing classifiers, unchanged). Mappings:
- MAP_BASELINE (sleeve-study map): UPTREND = BULL_TREND + all HIGH_VOLATILITY_TREND; DOWNTREND = BEAR_TREND.
- MAP_CORRECTED (study 2 default): UPTREND = BULL_TREND + HVT with reason CLASSIFIED_HIGH_VOL_BULL_TREND;
  DOWNTREND = BEAR_TREND + HVT with CLASSIFIED_HIGH_VOL_BEAR_TREND (confirmed in regime.py:229-243).
- Both: RANGE = RANGE, HIGH_VOLATILITY_RANGE, LOW_VOLATILITY_COMPRESSION. CHOPPY, TRANSITION, UNKNOWN, an HVT bar
  without either reason code, and missing labels = no trade (every rule, every mode).
- Availability: 15m/1h/4h signals use the last 4h label whose bar closed strictly before the signal bar's close;
  1d signals use the last 1d label closed strictly before the signal close (the previous day). BTC daily trend
  (optional filter: long needs ≠ BEARISH, short needs ≠ BULLISH) = RegimeClock trend over daily bars closed strictly
  before the signal close. Swing pivots (fractal, k bars each side) become usable k bars after the pivot bar.
- Regime correction is measured three ways on the same rules: baseline map longs; corrected map longs; corrected map
  longs + shorts. Each change is reported separately.

## 4. Discovery
Three rounds, each registered in `ROUNDn.json` before it runs, ≤ 50 unique base configurations per round
(base = family + timeframe + side + entry parameters + regime mode + map). Each base runs its registered exit / stop /
filter variants; every simulation (including failures, neighbours, cost and risk runs) is appended to
`out/variants.csv`. All rounds use the research window only. Families: trend pullback/continuation, breakout with
acceptance or retest, failed breakout/rejection, range mean reversion, volatility-expansion breakout, trend following
with trailing/time exits; plus two existing-engine references (COMBO_01, OSE_A signals cached by strategy_search,
native stop distance; their cache ends 2026-10-08).
Long and short are separate base configurations and are reported separately.

### Discovery simulation
sim2.py (copy of sleeve_study/sim.py + changes in SIM_CHANGES.md), all 16 coins in one account of 16 × $1,000 sleeves,
2% of current sleeve equity, every account rule of §5 with the $200 cap scaled to $320 (= 2% of $16,000), base costs
including hypothetical funding. Dollars per 30 days are reported per $10,000 (× 10 / 16).

### Candidate gates (screening floors; not relaxed)
At 2% risk on research: ≥ 150 closed trades; net > 0 in ≥ 3 of 4 folds, and net > 0 in F4 (falling fold) for any
book meant to cover both directions (single-side rules are tagged with their F4 result; a long+short portfolio needs
F4 > 0); pooled net > 0 at 1.5× costs; every numeric parameter at ×0.8 and ×1.2 (one at a time) net > 0 and the mean
of those neighbours ≥ 50% of the centre's net (no narrow spike); best coin ≤ 35% of summed positive coin
contributions; best month ≤ 30% of summed positive month contributions; 20% drawdown stop never triggered.
Negative coin / month contributions are reported separately. Gates are applied in the order cheap → expensive and
every evaluated gate is logged.

## 5. Account, sizing, risk (all simulations)
- One chronological account; each coin one sleeve; all rules on that coin share its equity; ≤ 1 net position per coin.
- risk_budget = current sleeve equity × risk_frac (baseline 2%; development 1%, 3%, 5%; 10% exploratory only).
  qty = budget / (|fill − stop| + taker·fill + (taker + slippage)·stop) — entry fee and stop-exit costs inside the budget —
  then capped by 2× gross notional / sleeve equity, then by the remaining account open-risk allowance, then rounded down
  to the lot step (10^−qty_precision); below 5 USDT notional or qty 0 → skip. Leverage above 2× is not studied (no
  verified margin/liquidation model).
- Open stop risk of a position = max(0, qty·side·(avg_fill − stop) + qty·stop·(taker + slippage)). After accepting an
  entry/addition the account total must be ≤ min($200, 2% of marked account equity) (discovery: $320 / 2%).
- Daily entry pause when marked equity falls 2% below the 00:00 UTC day-start equity; weekly pause at 5% below the
  Monday 00:00 UTC week-start equity. Exits keep running. At 20% cash-flow-adjusted peak drawdown every position exits
  at the next executable open and the experiment stops (no restart).
- Pyramiding (only where a round registers it): ≤ 2 additions, only on a fresh same-direction signal of the same rule
  while open profit ≥ 1R; aggregate position risk after the addition ≤ the position's original allowance. No martingale.

## 6. Execution and costs
Signals on completed candles; entry at the next execution bar's open (1h exec bars for 1h/4h/1d setups, 15m for 15m
setups); skip if the bar is missing or the fill is through the stop or target. Flat-coin conflicts at one timestamp:
higher signal timeframe wins; equal timeframe → the side uniquely matching the regime; otherwise skip. A later signal
never removes an executed trade. Same-bar stop/target → stop. Open through stop → fill at open. Trailing/BE/time
updates use completed exec bars and act from the next bar (time exits fill at the next open). Partial exits, additions,
forced and withdrawal reductions pay costs. All fills taker: 0.04% fee + 0.02% adverse execution per side.
Hypothetical funding: 0.01% of current notional at each 00:00/08:00/16:00 UTC boundary while held, charged to both
sides, **included in every score and labelled hypothetical** (no rule may use the funding clock as an input).
Sensitivities: 1.5× fees+execution+funding; execution 0.05% and 0.10% per side.
Marking: 1h common grid (15m is not common across the window); open positions marked at the last closed exec bar;
intrabar path inside a 1h (or 15m) bar is unknown — drawdowns are on that grid, not continuous.

## 7. Withdrawals
At each month start (00:00 UTC on the 1st), excess = marked account equity − $10,000 (discovery: N × $1,000). If > 0,
it is assigned to sleeves in proportion to their positive marked equity; each sleeve pays from free collateral
(cash − notional/2 − unrealised loss; unrealised profit is never paid). A shortfall triggers a proportional position
reduction at that coin's next exec-bar open (costs charged), then the sleeve pays what is free; anything still unpaid is
recorded as deferred and the next month-end recomputes the excess. Losses carry, no top-ups, sleeves are never reset.
Trading P&L, withdrawals and ending equity are reported separately; drawdown is cash-flow adjusted.

## 8. Validation selection (mechanical, fixed now)
1. Pool = variants passing every gate (any round). Keep the best variant per base config (research $/30d). If two
   pooled candidates' trades overlap > 50% (same coin and side, overlapping hold), keep the higher one; record duplicates.
2. Portfolios built on research only: P1 = best single candidate; P2 = best long + best short candidate (if both exist);
   P3 = greedy: from P1 add candidates by research $/30d while the combined research $/30d rises and the 20% stop never
   triggers, ≤ 4 components. A portfolio identical to an earlier one is dropped (≤ 3 materially distinct).
3. Coins per portfolio: top 10 by research net of that portfolio (2% risk, 16-coin research run) with net > 0; unused
   sleeves stay cash in the $10,000 account.
4. Validation runs each portfolio on its coins in a $10,000 account at 1%, 2%, 3%, 5% (10% exploratory), with 1.5× costs
   and 0.05%/0.10% execution columns.
5. Selected risk = highest of 1/2/3/5% with no 20% stop in research or validation, validation cash-flow-adjusted max DD
   ≤ 10%, research max DD ≤ 15%, and validation net at 1.5× costs > 0. The 2% benchmark is always shown beside it.
6. Eligible = validation net > 0 at 2% and at 1.5× costs (2%). PRIMARY = eligible portfolio with the highest validation
   $/30d at its selected risk; backups = the others in that order. If none is eligible, the best by validation $/30d at
   2% is frozen as PRIMARY labelled NOT_ELIGIBLE (no paper arming).
7. If no rule passes the discovery gates after three rounds, the top 3 distinct rules by research $/30d with ≥ 150
   trades and research net > 0 go to validation labelled NOT_A_CANDIDATE, for information only.
8. Freeze files (write-once): FREEZE_S2_PRIMARY_<utc>.json, FREEZE_S2_BACKUPS_<utc>.json with rules, parameters,
   coins, regimes, risk, conflicts, costs, withdrawals, data hash, code commit + diff hash, dates, rationale, seeds.

## 9. After the freeze
- Historical re-check on the test split, run once (primary, backups, 2% benchmark, cost columns). Labelled
  contaminated; never used to rank, drop, add, retune or rescale.
- Prospective paper evaluation starts at the freeze timestamp (paper runner, off by default). Robust evidence needs
  ≥ 6 full months and ≥ 100 closed trades, positive expectancy with an interval, risk-limit compliance and positive net
  at 1.5× costs. Months ≥ $500 / ≥ $1,000 reported with denominators and Wilson 95% intervals. Block bootstrap of joint
  daily account P&L (blocks 5/10/20 days, seed 20261014) for the 30-day distribution — descriptive, not a guarantee.
- A failed primary is recorded as failed; a revised book needs a new freeze and a new forward period.
