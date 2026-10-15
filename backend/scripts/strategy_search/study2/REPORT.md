# Study 2 report — primary S2_P1_20261014: NOT YET TESTABLE

Written 2026-10-14. Protocol: PROTOCOL.md (written before any study-2 run). Freeze: FREEZE_S2_PRIMARY_20261014T180111Z.json
(sha256 21b0fe52a223ee17…) and FREEZE_S2_BACKUPS_20261014T180111Z.json (sha256 745f4eac064b2c4a…), both read-only.

## Verdict

- **Primary S2_P1_20261014: NOT YET TESTABLE.** The stored history has no untouched period (EXPOSURE_LEDGER.json), so no
  target pass is possible. The only eligible evidence is prospective paper data from 2026-10-14T18:01:11Z, and none
  exists yet.
- **The robustness gates were not all passed.** The discovery screening gates passed. The robust-evidence gates did not:
  0 forward months and 0 forward trades, against the required ≥ 6 months and ≥ 100 trades.
- **No study-2 book is on course for $500 per 30 days.** The primary's validation result was +$142.29 per 30 days at
  its frozen 5% risk (gap to $500: $357.71; to $1,000: $857.71).
- Its one-time historical re-check lost money: −$83.63 per 30 days on 2026-03-24 → 2026-10-13 (gap to $500: $583.63;
  to $1,000: $1,083.63). That window is contaminated, so this is information, not a test.
- Discovery profits below are in-sample. They are not achieved income.
- **Recommendation: do not arm the paper runner for S2_P1.** `paper_config.json` ships with `enabled: false`.

## Final comparison table (per $10,000 account; dollars per 30 days unless marked)

| | S2_P1 (PRIMARY) | S2_P2 (BACKUP_1) | S2_P3 (BACKUP_2) |
|---|---|---|---|
| Status | NOT YET TESTABLE (paper not armed) | NOT YET TESTABLE (not promoted) | NOT YET TESTABLE (not promoted) |
| Evaluation type / dates | prospective paper from 2026-10-14T18:01Z: 0 days; historical re-check 2026-03-24→2026-10-13 (contaminated) | same | same |
| Rules | R3-04 short pullback (below) | R3-16 breakout-retest long + R3-04 | R3-04 + R3-16 + R1-22 failed-breakout short |
| Coins (chosen on research) | ARB AVAX BNB SUI OP DOT LINK BTC ETH APT | AVAX BNB ARB SUI DOT OP ADA NEAR BTC ETH | AVAX DOT BNB SUI DOGE ADA ARB XRP OP NEAR |
| Selected risk (benchmark) | 5% (2%) | 3% (2%) | 2% (2%) |
| Discovery, 16 coins, 2% | +83.74 | +124.89 | +148.82 |
| Research, chosen coins, 2% / selected | +133.70 / +237.80 | +176.93 / +237.77 | +217.98 / same |
| Validation 2% / selected | +77.21 / **+142.29** | +48.96 / +80.08 | +58.98 / same |
| Re-check 2% / selected | −12.21 / **−83.63** | +28.93 / +47.03 | +43.76 / same |
| Re-check gross / fees / execution / funding (hyp.), USD, selected risk | −263.24 / 108.90 / 54.45 / 139.32 | 629.86 / 106.22 / 53.11 / 152.27 | 622.26 / 135.17 / 67.58 / 123.43 |
| Re-check net USD (203 days) | −565.91 | +318.25 | +296.08 |
| Trades (long/short) | 98 (0/98) | 167 (67/100) | 280 (66/214) |
| Net expectancy R (± s.e.) | −0.017 ± 0.207 | +0.115 ± 0.162 | +0.067 ± 0.098 |
| PF / win rate | 0.79 / 26.5% | 1.11 / 31.7% | 1.10 / 38.9% |
| Avg hold / exposure | 97 h / 19% of sleeve-hours | 106 h / 36% | 73 h / 42% |
| Median daily P&L | 0.00 | 0.00 | −1.11 |
| Worst month / losing streak | −468.53 / 18 | −525.53 / 17 | −312.78 / 11 |
| Max drawdown (cash-flow adj., 1h marks) | 13.81% ($1,452) | 9.73% ($1,005) | 6.00% ($616) |
| Months ≥ $500 (Wilson 95%) | 1 of 6 (3–56%) | 1 of 6 (3–56%) | 1 of 6 (3–56%) |
| Months ≥ $1,000 | 1 of 6 | 1 of 6 | 0 of 6 |
| Cost columns: 1.5× / exec 0.05% / exec 0.10% | −103.43 / −93.83 / −111.99 | +23.69 / +36.10 / +29.29 | +20.57 / +26.89 / −3.58 |
| Concentration: best coin / best month share | 0.40 / 0.94 | 0.46 / 0.79 | 0.52 / 0.85 |
| Cash withdrawn / ending equity | $334.91 / $9,099.17 | $740.12 / $9,578.13 | $724.46 / $9,571.62 |
| Gap to $500 / $1,000 (re-check) | 583.63 / 1,083.63 | 452.97 / 952.97 | 456.24 / 956.24 |

The re-check figures are information only and select nothing. The backups' positive re-checks do not promote them.

### Actual calendar months, historical re-check (USD, selected risk; 2026-03 and 2026-10 are partial)

| month | S2_P1 5% | S2_P1 2% | S2_P2 3% | S2_P3 2% |
|---|---|---|---|---|
| 2026-03 (from 24th) | +73 | −5 | +121 | +131 |
| 2026-04 | −362 | −215 | −526 | −309 |
| 2026-05 | −437 | −168 | +17 | −21 |
| 2026-06 | +1,061 | +746 | +1,128 | +924 |
| 2026-07 | −415 | −251 | −431 | −313 |
| 2026-08 | −469 | −183 | 0 | +30 |
| 2026-09 | −17 | −8 | +167 | −19 |
| 2026-10 (to 12th) | 0 | 0 | −158 | −127 |

### Validation months (2025-09-01 → 2026-03-24, selected risk; used for selection)

| month | S2_P1 5% | S2_P1 2% | S2_P2 3% | S2_P3 2% |
|---|---|---|---|---|
| 2025-09 | −655 | −277 | −373 | −467 |
| 2025-10 | +613 | +91 | +298 | +295 |
| 2025-11 | +987 | +615 | +793 | +726 |
| 2025-12 | −173 | −43 | −56 | +91 |
| 2026-01 | +487 | +322 | +338 | +204 |
| 2026-02 | +213 | +82 | −63 | −10 |
| 2026-03 (to 23rd) | −504 | −265 | −393 | −438 |

Validation for S2_P1 at 5%: 117 trades, +0.244R ± 0.183, PF 1.30, max DD 9.51%. Two of six full months were ≥ $500;
none were ≥ $1,000. Withdrawals were $1,471.82, and +$118.42 per 30 days at 1.5× costs.

## The frozen rules

**R3-04, the S2_P1 component.** 4h SHORT. EMA50 < EMA200 and close < EMA200. The high of the last 8 bars (including
the signal bar) reached EMA50, and the bar closes below EMA50 and below the previous bar's low, with a bearish body.
The 4h regime label must be DOWNTREND (corrected map), taken from the last 4h bar that closed strictly before the
signal close. CHOPPY, TRANSITION and UNKNOWN labels mean no trade. Entry is at the next 1h open. The stop is 1.5 × ATR(14)
from the signal close. There is no target: a 3 × ATR trailing stop runs from the best 1h close and acts from the next
bar. A trade is skipped if the fill is through the stop.

**R3-16.** 4h LONG breakout-retest. A close above the prior 55-bar high, then within 12 bars a bar trades to level +
0.25 ATR and closes above the level. The setup is cancelled by a close below level − 0.5 ATR. The label must be
UPTREND, and BTC's daily trend must not be BEARISH. Stop 2.5 ATR, target 2R.

**R1-22.** 4h SHORT failed breakout. The high exceeds the prior-20-bar high, the close is back below it, and the close
is in the lower half of the bar. Any tradeable label is allowed, and BTC's daily trend must not be BULLISH. The stop
is the bar's high + 0.25 ATR. Target 1.5R, with a time exit after 12 bars.

All three use one $1,000 sleeve per coin and one net position per coin. Risk is a share of current sleeve equity, with
a 2× leverage cap, open stop risk ≤ min($200, 2% of equity), daily/weekly pauses at 2%/5%, and a 20% drawdown stop.
Fees are 0.04% plus 0.02% execution per side, with hypothetical funding of 0.01% per 8h in every score. Month-end
withdrawals follow PROTOCOL.md §7.

## Why the target is missed (measured)

- **Validation at the frozen 5% risk:** 17.2 trades per 30 days × $42.65 average executed risk × +0.244R ≈ $179 before
  rounding effects; measured $142. The executed risk is below the nominal $50 because of compounding after
  withdrawals, the 2× notional cap and lot rounding.
- **What $500 would need:** at that frequency and risk, about +0.68R per trade. That is 2.8× validation and 1.4× the
  in-sample research figure of 0.49R. Alternatively it needs about 48 trades per 30 days at 0.24R. With roughly 100 h
  average holds, that means about 6–7 positions open at once at $43 each, and the $200 open-risk cap allows about 4.7.
- **Binding constraints:** forward expectancy, which went from +0.49R in research to +0.24R in validation to −0.02R in
  the re-check, together with the $200 open-risk cap. Adding coins or leverage does not lift either.
- **Discovery breadth:** 1,734 screened variants plus 1,781 gate, neighbour and comparison runs. 135 passed every gate,
  all on 4h (130) or 1h (5). No 15m variant passed: the best was +$3.41 per 30 days, with costs and funding dominating.
  The best 1d variant had fewer than 150 trades.
- **Best single rules, discovery at 2%:** short +$83.74, long +$44.36 per 30 days.

## Robustness and diagnostics

- **Walk-forward (anchored, research, out/walkforward.json).** For R3-04, picking the best of its 12 registered
  variants on earlier folds earned $820 out-of-sample over F2–F4, against $2,532 for the frozen variant. So in-sample
  selection inflated it about 3×. R3-16: $834 vs $1,042. R1-22: $853 vs $853.
- **Folds.** The 16-coin research run for R3-04 had F1 +1,143, F2 +2,615, F3 −420 and F4 (the falling fold) +337.
  It passed 3 of 4 folds.
- **Regime correction (out/regime_cmp.json), research $/30d.**

  | rule | baseline map | corrected map | corrected + shorts |
  |---|---|---|---|
  | TPB 4h long | +2.07 | +2.07 | −15.48 |
  | BRKA 4h long | +11.32 | +11.36 | −3.35 |
  | COMBO_01 4h long | +16.99 | +13.02 | — |
  | FBR 4h long | +8.73 | −0.63 | — |

  The correction moved 7,778 4h bars from UPTREND to DOWNTREND. It is kept because it is the correct label, not
  because it improved results.
- **Duplicates (out/duplicates_engines.json).** COMBO_01 and COMBO_02_LOCAL signals are 97–99% identical on every
  timeframe; only COMBO_01 was used. The pool collapsed 25 overlapping variants (out/pool.json): 13 into R3-04, 11 into
  R3-16 and 1 into R2-29.
- **Simulator (SIM_CHANGES.md, out/sim_effects.json).** R1-06 earned $68.91 per 30 days in the original sim.py and
  $61.71 in sim2. Hypothetical funding cost −$6.1, cost-inclusive sizing −$0.8 and lot rounding −$0.2. The caps,
  pauses and drawdown stop did not bind for that rule. All 19 synthetic cases in test_sim2.py pass.
- **Block bootstrap.** Re-check daily account P&L, 5,000 thirty-day paths, seed 20261014, 5/10/20-day blocks. For
  S2_P1 at 5%: P(≥ $500) 11.5–12.8%, P(≤ 0) 66–76%, median −$182 to −$221. These are descriptive, not forecasts.
- **Paper runner parity.** A replay of 2026-07-01 → 2026-10-13 gave 33 trades and −$560.25. That matches the study
  simulator trade for trade (out/parity_dryrun.txt).

## Data, exposure, deviations

- **Data (DATA_MANIFEST.json, ohlcv sha256 eb8033c0…).** There is no five-year history. The 16 coins cover 2023-06 →
  2026-10-12 with no gaps, duplicates or invalid bars. The 11 V1 coins starting 2024-10-09 are excluded.
- **Not researched:** an annual top-20 universe, because no listing/delisting history or history for other contracts
  exists. Stored funding history is also absent, which is why funding is hypothetical.
- **Lot steps** come from the current `symbols` snapshot.
- **Splits.** Research 2023-06-01→2025-09-01, validation →2026-03-24, re-check →2026-10-13, all half-open.
  F4 (2025-02→2025-09, 41.5% BTC-bearish days) is the falling fold.
- **Deviation 1.** The CSV `base` column was overwritten by the account size. It was fixed before the pool step, and
  the base id is read from the vid prefix. No simulated number changed.
- **Deviation 2.** The `--replay-from` dry-run option was added to paper/runner.py after the freeze, as a plumbing
  check only. It cannot be used with `run`.
- **Untouched:** backend/app, sleeve_study, v1 paper, REGIME_BOOK_OPEN, every paper_eligible flag, COMBO_02_V2_1_A and
  LIVE_EXECUTION_ALLOWED. Code state: HEAD 9e96798 plus uncommitted changes. The diff hash is in the freeze file.

## Next forward-evaluation experiment (one)

The frozen backup S2_P2 at 3% risk is positive in validation (+$80.08) and in the re-check (+$47.03), on both sides
and at 1.5× costs. It could be run as a **6-month prospective measurement of expectancy and trade frequency, not
income.** That would require a separate written forward plan, and the owner arming it.

Its realistic expectation is tens of dollars per 30 days, so it would test whether ≈ +0.1–0.2R survives forward. It
cannot test $500. Reaching $500 needs a rule with roughly +0.6R forward expectancy at about 17 trades per 30 days,
which nothing in 1,734 variants showed outside research.

## Reproduce (from backend/scripts/strategy_search/study2, interpreter ..\..\..\.venv\Scripts\python.exe -B)

```
data_manifest.py ; regimes2.py --shard i/3 ; test_sim2.py
search2.py screen N --shard i/4 ; search2.py gates N --shard i/4 ; search2.py summary N     (N = 1, 2, 3)
regime_cmp.py ; sim_effects.py ; select2.py pool ; select2.py build ; wf2.py ; select2.py validate
select2.py freeze          (write-once)   ; final2.py run   (run-once)
paper\runner.py check-data | dry-run [--now ISO] [--replay-from ISO] | run (refused while enabled=false)
```

Artifact sha256 (first 16 hex): PROTOCOL.md 3d19bf49…, ROUND1/2/3.json 11cf735b…/2315afc7…/b52699d9…,
sim2.py 8940d98f…, signals2.py d683d9c3…, out/variants.csv fe3fa63e… (3,515 rows: every simulation).
Trade ledgers: out/ledger_<book>_<window>_<risk>.csv.
