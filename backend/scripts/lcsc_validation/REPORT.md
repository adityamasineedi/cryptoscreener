# LARGE_CAP_SWEEP_CHOCH 1h LONG — validation report (final research archive)

2026-10-15. Research only. Protocol: [PROTOCOL.md](PROTOCOL.md), written before any split result.

**Study code:** `load_data.py`, `repro.py`, `study.py`, `render.py`. Every study number below is in `out/tables.md` and `out/results.json`, with per-trade rows in `out/trades_<SYM>.csv`.

**Archive update, same day, after the study.** This added the forward contract and runner (`forward.py`), Monte Carlo v2 (`mc_v2.py`), the simulator v2 comparison (`v2_compare.py`) and the bug-fix modules (§12). It did not re-run the study or touch the split.
- The original report and outputs are preserved byte-for-byte in `out/archive_v1/` (`SHA256SUMS`).
- `out/results.json` is unchanged since the study run.

## 0. Executive verdict

| Book | Verdict |
|---|---|
| BTCUSDT LCSC LONG | **REJECTED** |
| ETHUSDT LCSC LONG | **WATCH_FORWARD_VALIDATION_REQUIRED** |
| Stop-reversal short | **REJECTED** |
| Overall system | **RESEARCH_ONLY**. No paper or live trading. |

- **BTC: rejected.**
  - Fully costed expectancy is +0.006R over the full range and +0.065R on the pseudo-OOS. Costs take 92% of gross profit.
  - It loses money without its single best trade, and it is profitable only in trend-down.
  - It is negative under stress costs (−0.071R). Monte Carlo puts the full-history chance of a drawdown above 15R at 80–92%.
- **ETH: watch.**
  - Fully costed expectancy is +0.117R over the full range and +0.297R on the pseudo-OOS. The full-range 95% CI is [−0.10, +0.33]R.
  - It was flat over the first 60% of history (−0.016R, with a 20R drawdown in 2023). It is fragile on min_rr and on the sweep lookback, and has fewer than 100 OOS trades.
  - **The historical validation and holdout cannot confirm the edge**: ETH and the rules were chosen with bars through 2026-10-15.
  - The only test that can is the frozen forward contract: [docs/lcsc_eth_forward_contract.md](../../../docs/lcsc_eth_forward_contract.md), starting 2026-10-16 00:00 UTC.
- **Stop-reversal short: rejected.** Every costed variant loses on both symbols. With the stop-bar fix, even the uncosted ETH replay is negative (§8).

**Correction to a summary figure.** The ETH screen's +$718.11 (189 trades, fees −$140.44, max drawdown $308.46) is **after taker fees only**. With 5 bps entry slippage, 10 bps stop slippage and funding (the FULL model), the same 189 trades make **+$474**, i.e. +22.07R or +0.117R per trade.

**Read this first.** All stored BTC/ETH 1h history (to 2026-10-14 23:00 UTC) had already been seen for this
strategy: the Backtest screen ran it over 2023-01-01 → 2026-10-15, the 2026-10-15 year-switch study ran it per year,
and ETH was picked because it looked best. The "validation" and "holdout" windows here were not used for any
parameter choice. They were still seen by the person picking the strategy. They can falsify the edge; they cannot
confirm it. The first clean bars open on **2026-10-16 00:00 UTC**.

## 1. Diagnosis — methodological weaknesses found

| # | Weakness | Evidence | Effect |
|---|---|---|---|
| 1 | One in-sample window, no split, and no clean holdout left | Screen window = whole stored history; year-switch study used every year | Every result is in-sample until forward bars arrive |
| 2 | Selection bias | ETH kept from a 10-coin test where 5/10 passed; LCSC kept from a multi-strategy rank | The best-looking cell is chosen, so its in-sample number overstates the edge |
| 3 | Stop-reversal short is not a setup | `stopOutShortReplay.ts:103` walks `candles.slice(fill + 1)`: it skips the stop bar, charges no costs, and treats every long stop as a short | ETH −8R / +8R uncosted becomes −20R / −4R costed (−30R / −17R stop-bar-first). Fixed in replay v2; panel relabelled (§8, §12) |
| 4 | No slippage | `engine_backtest.py` passes no slippage; `MultiCapResearchConfig.slippage_rate = 0.0002` (config.py:103) is never used | Adding 5 bps entry / 10 bps stop costs ETH $189, BTC $246 |
| 5 | Latent fee/slippage bug | `trade_fees.py:221` slips the entry for the fee, but `:269` computes gross PnL from the unslipped entry | Would understate any slippage run's cost; dormant today (rate 0). Fixed in simulator v2 (§12) |
| 6 | A gap through the stop fills at the stop | `combination_backtest.py:202-204` returns the stop level | 0 occurrences on 24/7 BTC/ETH 1h; optimistic elsewhere. Fixed in simulator v2 (§12) |
| 7 | No funding | Longs hold ~77–84 h (≈10 settlements) | −$55 ETH, −$89 BTC over the full range |
| 8 | Mislabelled R | Screen "16.70R gross" DD is the after-fee DD (gross is 15.00R); "0.18R net expectancy" is after fees only | Fully costed expectancy is 0.117R |
| 9 | No uncertainty | ETH after-fee t = 1.71, bootstrap 95% CI [−0.04, +0.39]R | The full-range edge is not statistically distinguishable from zero |
| 10 | Pooled multi-coin number | +$986 over 10 coins hides 5 losing coins | Averaging weak and strong coins misleads |
| 11 | No neighbourhood check on "documented defaults" | — | Fragility was never tested (it is fragile; §6) |
| 12 | No time stop | One BTC long was held 70 days (2026-02-06 → 04-17) | Funding exposure and capital lock-up are uncapped |

## 2. Corrected test plan (as run)

- **Data:** Binance USD-M 1h, 2022-10-16 15:00 → 2026-10-14 23:00 UTC, 35,025 contiguous bars per symbol. Bars before
  2023-01-01 are warm-up only (the engine's 1,000-bar warm-up).
- **Splits** (signal-bar open time, start inclusive):
  - Train 2023-01-01 → 2025-04-10 (60%)
  - Validation 2025-04-10 → 2026-01-11 (20%)
  - Holdout 2026-01-11 → 2026-10-15 (20%)
- **Rules:** v1 frozen (sweep lookback 48, swing 3/3, stop = min(entry − 1.5 ATR, sweep level), target 2R full exit,
  same-bar SL+TP → SL, one trade at a time, $20 risk, $1,000, 2× cap).
- **Walk-forward:** 12-month train, 3-month test, step 3 months; tests run 2024-01-01 → 2026-01-11. Each fold picks from
  a pre-set 27-config grid (min_rr 1.5/2/2.5 × ATR 1/1.5/2 × lookback 24/48/72) by plateau score (the config's FULL
  expectancy averaged with its one-step neighbours, ≥ 30 train trades). The holdout gets one pick, made on
  2025-01-11 → 2026-01-11 and applied once.
- **Costs:** GROSS → AFTER_FEE (taker 0.04% both sides) → FULL (next-bar-open entry +5 bps, stop −10 bps,
  gap-aware stops, limit targets, Binance funding settlements) → FULL_STRESS (10/20 bps, 0.05% taker).
- **Parity:** my simulator reproduces `run_engine_cell` exactly. BTC: 197 trades, $380.86 after fees.
  ETH: 189 trades, $718.11. The screen's short replay also reproduces: ETH 52/60, −8R.

Formulas: expectancy = ΣR/N; profit factor = Σ winning net USD / |Σ losing net USD|; drawdown on the closed-trade
equity E_k = Σ_{i≤k} net_i is max_k(max_{j≤k} E_j − E_k), and the same on cumulative R gives DD in R;
payoff = mean win R / |mean loss R|; fee drag = fees / gross USD.

## 3. Results — v1 frozen, FULL costs

| | BTC train | BTC val | BTC holdout | **BTC OOS** | BTC full | ETH train | ETH val | ETH holdout | **ETH OOS** | ETH full |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Trades | 118 | 43 | 36 | **79** | 197 | 109 | 39 | 41 | **80** | 189 |
| Net USD | −75 | +113 | +9 | **+121** | +46 | −5 | +297 | +183 | **+479** | +474 |
| Net R (FULL) | −3.90 | +4.59 | +0.53 | **+5.12** | +1.22 | −1.71 | +14.83 | +8.95 | **+23.78** | +22.07 |
| Gross R | +11.0 | +11.0 | +6.0 | +17.0 | +28.0 | +11.0 | +18.0 | +13.0 | +31.0 | +42.0 |
| After-fee R | +6.01 | +8.75 | +3.90 | +12.65 | +18.67 | +6.67 | +16.74 | +11.30 | +28.04 | +34.71 |
| Win rate | 36.4% | 41.9% | 38.9% | 40.5% | 38.1% | 36.7% | 48.7% | 43.9% | 46.2% | 40.7% |
| Avg win / loss R | 1.90 / −1.14 | 1.91 / −1.19 | 1.89 / −1.18 | 1.90 / −1.18 | 1.90 / −1.16 | 1.91 / −1.13 | 1.93 / −1.09 | 1.92 / −1.11 | 1.93 / −1.11 | 1.92 / −1.12 |
| Payoff | 1.66 | 1.60 | 1.60 | 1.60 | 1.64 | 1.69 | 1.77 | 1.72 | 1.74 | 1.71 |
| PF (all costs) | 0.96 | 1.20 | 1.02 | **1.11** | 1.02 | 1.00 | 1.68 | 1.37 | **1.51** | 1.19 |
| Expectancy R | −0.033 | +0.107 | +0.015 | **+0.065** | +0.006 | −0.016 | +0.380 | +0.218 | **+0.297** | +0.117 |
| 95% CI (bootstrap) | [−0.29, +0.25] | | | [−0.26, +0.41] | [−0.21, +0.22] | | | | [−0.04, +0.64] | [−0.10, +0.33] |
| Max DD USD / R | 313 / 16.2 | 198 / 10.2 | 161 / 8.9 | 198 / 10.2 | 313 / 17.2 | 371 / 20.1 | 97 / 4.9 | 90 / 4.5 | 112 / 5.6 | 371 / 20.1 |
| Longest losing streak | 7 | 6 | 6 | 6 | 7 | 8 | 4 | 4 | 4 | 8 |
| Avg duration h | 82 | 82 | 92 | 87 | 84 | 87 | 69 | 59 | 64 | 77 |
| Exposure | 49% | 53% | 50% | 52% | 50% | 48% | 40% | 36% | 38% | 44% |
| Fee drag % gross | 45% | 18% | 35% | 24% | 32% | 35% | 7% | 12% | 9% | 16% |
| All-cost drag % gross | 135% | 52% | 92% | 65% | 92% | 102% | 18% | 29% | 23% | 45% |

Cost ladder, full range, expectancy R:

| Symbol | GROSS | AFTER_FEE | FULL | FULL_STRESS |
|---|---:|---:|---:|---:|
| BTC | +0.142 | +0.095 | +0.006 | −0.071 |
| ETH | +0.222 | +0.184 | +0.117 | +0.055 |

Full-range funding: BTC −$89, ETH −$55. Slippage: BTC −$246, ETH −$189.

Calendar years, FULL expectancy R (trades):

| Symbol | 2023 | 2024 | 2025 | 2026 (to Oct 14) |
|---|---:|---:|---:|---:|
| BTC | +0.130 (47) | −0.103 (62) | +0.019 (52) | +0.015 (36) |
| ETH | −0.019 (56) | +0.092 (43) | +0.237 (48) | +0.186 (42) |

Walk-forward re-optimisation, FULL:

| Symbol | Tests 2024 → 2026-01-11 | Holdout | Combined OOS | v1, same span 2024 → end |
|---|---|---|---|---|
| BTC | −0.106R | +0.161R | +0.094R, PF 1.09 | −0.033R |
| ETH | +0.065R | +0.233R | +0.220R, PF 1.36 | +0.174R |

Re-tuning does not beat frozen v1 on ETH, and on BTC it changes the pick almost every fold, so the folds show no
stable optimum. v1 stays frozen.

## 4. Regime table (v1, FULL expectancy R / trades)

| Regime | Bar share BTC / ETH | BTC full | BTC OOS | ETH full | ETH train | ETH OOS |
|---|---|---:|---:|---:|---:|---:|
| Trend-up | 38% / 35% | −0.114 / 70 | −0.309 / 28 | +0.126 / 49 | +0.093 / 30 | +0.177 / 19 |
| Trend-down | 27% / 32% | **+0.260 / 59** | +0.493 / 26 | **+0.249 / 71** | +0.100 / 42 | +0.464 / 29 |
| Range / chop | 35% / 34% | −0.090 / 68 | +0.037 / 25 | −0.025 / 69 | −0.235 / 37 | +0.218 / 32 |
| High-vol | 21% / 21% | +0.158 / 55 | +0.388 / 24 | +0.402 / 45 | +0.335 / 32 | +0.567 / 13 |
| Normal-vol | 56% / 56% | −0.068 / 103 | −0.103 / 37 | −0.089 / 117 | −0.206 / 71 | +0.091 / 46 |
| Low-vol | 22% / 23% | −0.013 / 39 | −0.020 / 18 | +0.534 / 27 | +0.365 / 6 | +0.582 / 21 |

Regimes: trend = close vs EMA200/EMA800 of the 1h close; vol = 90-day percentile of ATR14/close (≥ 80% high,
≤ 20% low). The label is taken at the signal bar.

**Does ETH depend on one bull regime or a few outliers?** Neither. It is weakest in trend-up and strongest in
trend-down (sweep-and-reclaim after a flush), and it is positive in all three trend regimes OOS. Outliers cannot
drive it: every winner is capped near +1.9R, and removing the top 5 trades still leaves +12.2R. What ETH depends on
is **time**: the 60% train window is flat (−0.016R) with a 20R drawdown in 2023, and 2025–2026 carry the result.
BTC is profitable only in trend-down; it loses in the other two regimes over the full range.

## 5. Monte Carlo v1 (archived; superseded by §5b)

FULL R, 2% risk compounding from $1,000, 10,000 runs.

| Sample | Method | Median final | P5 / P95 final | Median DD | P95 DD | P(DD ≥ 20%) | P(DD ≥ 30%) |
|---|---|---:|---:|---:|---:|---:|---:|
| ETH OOS (80) | shuffle | $1,550 | $1,550 / $1,550 | 14.9% | 22.9% | 13.5% | 0.3% |
| ETH OOS (80) | bootstrap | $1,548 | $993 / $2,392 | 15.3% | 27.4% | 22.9% | 2.8% |
| ETH full (189) | shuffle | $1,429 | — | 27.4% | 39.6% | 92.8% | 35.0% |
| ETH full (189) | bootstrap | $1,419 | $731 / $2,758 | 28.0% | 48.1% | 85.7% | 41.7% |
| BTC OOS (79) | bootstrap | $1,067 | $693 / $1,648 | 22.6% | 40.3% | 62.2% | 22.8% |
| BTC full (197) | bootstrap | $945 | $480 / $1,860 | 37.6% | 60.7% | 97.1% | 74.2% |

With compounding fixed-fraction risk, final equity does not depend on trade order, so shuffles cannot spread it.
The bootstrap (resampling with replacement) shows the spread. On the full ETH history, 2% risk reaches a 20%
drawdown in ~86–93% of paths.

## 5b. Monte Carlo v2 (`mc_v2.py`, `out/monte_carlo_v2.json`)

The inputs are the archived FULL-cost R lists (closed trades in exit order, from `out/trades_<SYM>.csv`); nothing is re-simulated. Each method runs 10,000 times with a fixed seed.
- **Final equity:** 2% risk per trade, compounding from $1,000.
- **Drawdown in R:** peak-to-trough on additive cumulative R, i.e. the fixed $20 risk basis that the forward contract uses.

The three resampling schemes, and what each can tell you:
- **Permutation** reorders the observed trades without replacement. It measures how much drawdown depends on sequencing. Final equity is identical in every run: both Σ R and Π(1 + 0.02 R) are order-independent. **Shuffling trade order cannot change final equity**, so it is reported once.
- **Block bootstrap** draws circular blocks of 8 consecutive trades (about two months at ~4 trades per month) with replacement. It keeps short-run clustering of wins and losses. It changes both the trade sequence and the set of trades, and is used for drawdown and final equity.
- **IID bootstrap** draws single trades with replacement. It also changes the sequence and the set of trades, and is used for the spread of final equity. It ignores clustering.

| Sample | Method | Median final | P5 / P95 final | Median DD | P95 DD | P(DD > 10R) | P(DD > 15R) | P(DD > 20R) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| ETH OOS (80) | Permutation | $1,550 | same every run | 7.9R | 12.7R | 20.4% | 1.2% | 0.1% |
| ETH OOS (80) | Block bootstrap | $1,546 | $1,093 / $2,216 | 6.2R | 9.9R | 4.8% | 0.2% | 0.0% |
| ETH OOS (80) | IID bootstrap | $1,551 | $1,009 / $2,386 | 8.0R | 15.5R | 28.1% | 5.8% | 1.1% |
| ETH full (189) | Permutation | $1,429 | same every run | 15.2R | 24.1R | 95.8% | 52.1% | 16.5% |
| ETH full (189) | Block bootstrap | $1,439 | $739 / $2,822 | 15.6R | 30.5R | 89.7% | 54.3% | 26.5% |
| ETH full (189) | IID bootstrap | $1,425 | $736 / $2,810 | 15.6R | 30.6R | 89.6% | 54.2% | 26.4% |
| BTC OOS (79) | Permutation | $1,069 | same every run | 11.8R | 18.6R | 73.1% | 19.9% | 2.5% |
| BTC OOS (79) | Block bootstrap | $1,072 | $730 / $1,555 | 10.7R | 22.2R | 55.9% | 23.0% | 8.1% |
| BTC OOS (79) | IID bootstrap | $1,068 | $690 / $1,656 | 12.2R | 24.8R | 68.3% | 32.5% | 13.8% |
| BTC full (197) | Permutation | $940 | same every run | 21.5R | 32.7R | 100.0% | 92.2% | 60.3% |
| BTC full (197) | Block bootstrap | $939 | $497 / $1,833 | 21.3R | 42.2R | 97.6% | 80.0% | 55.9% |
| BTC full (197) | IID bootstrap | $940 | $479 / $1,852 | 22.2R | 44.0R | 97.8% | 82.2% | 59.2% |

Observed drawdowns, for comparison: ETH OOS 5.6R, ETH full 20.1R, BTC OOS 10.2R, BTC full 17.2R.

**Limitations.**
1. Every method assumes the archived trades are representative of the future. They are not out-of-sample, and they carry selection bias.
2. The ETH OOS rows come from its best stretch (2025–2026). The full-history rows are the more honest guide: over a 189-trade horizon, about half of paths exceed a 15R drawdown.
3. Resampling cannot represent regime shifts that never appeared in the sample.
4. Block length 8 is a stated choice, not an estimate. ETH OOS block results are narrower than IID because the clustered losses in that window are short.
5. The OOS samples share the 2023–2026 data the strategy was picked on.

## 6. Fragility report

**Top-trade dependence (FULL R)**

| Sample | Total | Excl. top 1 | Excl. top 3 | Excl. top 5 | Fragile | Losing months |
|---|---:|---:|---:|---:|---|---|
| BTC full | +1.22 | −0.78 | −4.72 | −8.65 | **yes** | 20 / 44 |
| BTC OOS | +5.12 | +3.12 | −0.81 | −4.71 | **yes** | 5 / 17 |
| ETH full | +22.07 | +20.08 | +16.14 | +12.19 | no | 24 / 46 |
| ETH OOS | +23.78 | +21.80 | +17.85 | +13.91 | no | 9 / 19 |

The top-10 lists (in `out/tables.md`) are all 2R target hits, mostly in trend-down/high-vol. ETH's monthly median
is −$1.81 over the full range: the edge shows up as a few good months, not as steady income.

**Parameter sensitivity (FULL expectancy R, full range; OOS in brackets)**

| Param | Lower | v1 | Higher |
|---|---|---|---|
| ETH min_rr 1.5 / 2.0 / 2.5 | **−0.018** (+0.101) | +0.117 (+0.297) | +0.118 (+0.258) |
| ETH ATR × 1.0 / 1.5 / 2.0 | +0.090 (+0.252) | +0.117 | +0.157 (+0.226) |
| ETH lookback 24 / 48 / 72 | **+0.035** (+0.219) | +0.117 | +0.081 (+0.302) |
| ETH swing 2 / 3 / 4 | +0.070 (+0.147) | +0.117 | +0.099 (+0.240) |
| BTC min_rr 1.5 / 2.0 / 2.5 | +0.020 (−0.018) | +0.006 (+0.065) | −0.022 (−0.098) |
| BTC lookback 24 / 48 / 72 | +0.031 (+0.023) | +0.006 | +0.132 (+0.245) |
| BTC swing 2 / 3 / 4 | **−0.096** (−0.009) | +0.006 | +0.021 (+0.050) |

ETH is fragile on min_rr: 1.5R turns it negative. It is also fragile on lookback: at 24, expectancy is below half of
v1's. On the train window alone, v1 ETH is −0.016R and 24 of 27 grid configs are negative. BTC's lookback-72 cell looks
good OOS (+0.245R), but it was found after looking. Walk-forward picked it in 4 of 9 picks and lost money
overall (−0.106R over the 2024–2025 tests), so it is not adopted.

## 7. Verdicts (v1, combined OOS, FULL costs)

| Gate | BTCUSDT | ETHUSDT |
|---|---|---|
| ≥ 100 OOS trades | ✗ 79 | ✗ 80 (CI [−0.04, +0.64]R includes 0) |
| Expectancy ≥ 0.10R | ✗ +0.065 | ✓ +0.297 |
| PF ≥ 1.30 | ✗ 1.11 | ✓ 1.51 |
| Max DD ≤ 15R | ✓ 10.2R (full range 17.2R) | ✓ 5.6R (full range 20.1R ✗) |
| Survives without top 3 | ✗ −0.81R | ✓ +17.85R |
| ≥ 2 of 3 trend regimes ≥ −0.05R | OOS 2/3, full range 1/3 | ✓ 3/3 |
| Parameters stable | ✗ (swing 2 flips sign) | ✗ (min_rr, lookback) |
| Survives stress costs | ✗ −0.071R full | ✓ +0.055R full |

- **BTCUSDT: FAIL.** Fully costed expectancy is +0.006R over the full range and +0.065R OOS. All costs eat 92% of
  gross profit. It loses money without its single best trade, and it makes money in trend-down only. Stays RESEARCH.
- **ETHUSDT: WATCH.** Positive after all costs, with a good OOS window (+0.297R, PF 1.51), but it fails the trade-count
  gate. It is fragile on two parameters, flat on the first 60% of history (2023 drew down 20R), and its OOS was not
  clean. Stays RESEARCH. Not promoted to PAPER_TRADE, and no live deployment follows from this study.

## 8. Stop-reversal short — rejected

Re-priced on the v1 long stop-outs (full range):

| Symbol | Policy | Screen walk, no costs | Screen walk, costed | Stop-bar-first, costed |
|---|---|---:|---:|---:|
| ETH | 1R | −8.0R (52 W / 60 L) | −20.0R (−0.178R/trade) | −30.4R (−0.271) |
| ETH | mirrored 2R | +8.0R | −4.2R (−0.038) | −16.6R (−0.148) |
| BTC | 1R | −4.0R | −19.2R (−0.157) | −38.1R (−0.312) |
| BTC | mirrored 2R | −8.0R | −23.6R (−0.194) | −48.6R (−0.398) |

Every costed variant is negative on both symbols. The overlay is not a setup: it has no entry condition of its own.
Its R must not be blended with the long book. It should be dropped. A short system needs its own thesis, entry,
filters and protocol; none is proposed here.

**Replay v2 (the stop bar counts).** The screen's uncosted replay started one bar late. Walking the stop bar first changes the uncosted numbers, from the study's archived `CONSERVATIVE_STOP_BAR` walk, which is the same rule:

| Symbol | Policy | Uncosted, v1 (skips stop bar) | Uncosted, v2 (stop bar counts) |
|---|---|---:|---:|
| ETH | 1R | −8.0R (52 W / 60 L) | −18.0R (47 W / 65 L) |
| ETH | mirrored 2R | +8.0R (40 W / 72 L) | −4.0R (36 W / 76 L) |
| BTC | 1R | −4.0R (59 W / 63 L) | −22.0R (50 W / 72 L) |
| BTC | mirrored 2R | −8.0R (38 W / 84 L) | −32.0R (30 W / 92 L) |

The only positive number the panel ever showed was ETH mirrored 2R at +8.0R. It came from skipping the stop bar, and it disappears once that bar counts.

**Panel status.** The Backtest-screen panel is not deleted; deleting it needs your confirmation. It is now:
- collapsed by default;
- labelled `UNCOSTED_RESEARCH_ONLY_DO_NOT_USE`;
- shown with a warning that it is not a tradeable short book and that the fully costed result is negative;
- computed with replay v2, with the archived v1 total shown beside it for reference.

The costed figures stay in this report, not on the panel, because the panel replays whatever strategy is on screen.

## 9. What would falsify ETH

The pseudo-OOS window cannot confirm the edge, so the test is forward. The rule proposed here earlier is superseded by the **frozen contract** [docs/lcsc_eth_forward_contract.md](../../../docs/lcsc_eth_forward_contract.md) (`LCSC_V1_ETHUSDT_LONG/FWD-1`).
- Window: signal bars from 2026-10-16 00:00 UTC.
- Read: once, after at least 40 closed trades **and** no earlier than 2027-07-16.

Any of these kills ETH:
- forward FULL expectancy ≤ 0R;
- PF < 1.0;
- a drawdown above 15R (2023 already showed 20R, so this would mean the 2023 behaviour has returned);
- profit only from trend-down signals while trend-up and range both lose (a capitulation-bounce trade, which is a narrower claim than "sweep + CHOCH works");
- fewer than 40 trades at 12 months (2027-10-16; the exception is not preapproved);
- any unresolved data, funding or record exception.

**Archived warning signs** (not contract rules):
- ETH's train window was −0.016R, and 24 of 27 grid configs were negative on train.
- BTC, the more liquid twin, fails.
- Realised costs above the FULL model, such as stop slippage well beyond 10 bps or funding beyond 0.1R per trade, would make the stress case (+0.055R) the realistic one. The forward runner prices the FULL model and cannot observe real fills.

## 10. Exact cost model

| Model | Entry | Stop exit | Target exit | Fees | Funding |
|---|---|---|---|---|---|
| GROSS | signal-bar close | stop level | target level | none | none |
| AFTER_FEE (the screen) | signal-bar close | stop level | target level | taker 0.04% both sides | none |
| **FULL (verdicts and forward contract)** | next-bar open + 5 bps | stop − 10 bps; a bar opening below the stop exits at open − 10 bps | target level (limit) | taker 0.04% both sides | Binance settlements, long pays `rate × qty × mark` for fill < t ≤ exit-bar open |
| FULL_STRESS | next-bar open + 10 bps | stop − 20 bps (gap: open − 20 bps) | target level | taker 0.05% both sides | as FULL |

- Sizing: qty = min(20 / (signal close − stop), 2000 / signal close).
- R unit: planned risk = qty × (signal close − stop). R = net USD / planned risk.

## 11. Forward contract (frozen)

`LCSC_V1_ETHUSDT_LONG/FWD-1`, locked in `docs/lcsc_eth_forward_contract.lock.json` (config `e27e5579…`, code `f4683811…`).

| Item | Rule |
|---|---|
| Book | ETHUSDT 1h LONG, LCSC v1, FULL costs, funding where available (a missing settlement is an exception) |
| Start | 2026-10-16 00:00 UTC |
| First read | ≥ 40 closed trades and ≥ 9 months (2027-07-16). The runner refuses earlier reads; `--diagnostic` reads are labelled `NON_ACCEPTANCE_DIAGNOSTIC` and logged. |
| KILL (any) | expectancy ≤ 0R; PF < 1.0; drawdown > 15R; profit only from downtrends; < 40 trades at 12 months (not preapproved); unresolved execution, data, fee, funding or reconciliation exception |
| PASS_TO_PAPER_ELIGIBLE (all) | ≥ 40 trades and ≥ 9 months; expectancy > 0.10R; PF ≥ 1.20; drawdown ≤ 15R; net positive in ≥ 2 of 3 trend regimes; no unresolved exceptions |
| Neither | `NO_PASS_CONTINUE`: not a pass |
| Change control | No conditional pass, no refit. Any change to rules, a parameter, costs, symbol, timeframe, data or execution creates a new version and restarts the window. |
| Records | Every run and read stores code, config and data fingerprints and its timestamp. |

The runner (`forward.py`) reproduces this study's ETH walk exactly when pointed at 2023-01-01: 189 trades, +22.07R, +$474, identical R per trade (`tests/test_lcsc_forward.py`).

## 12. Dormant bug fixes and their impact

The fixes live in a separate, versioned path. Version 1 code is unchanged, so every archived number reproduces.

| Bug (v1, kept) | Fix (v2) | Where | Regression tests |
|---|---|---|---|
| Fee helper slips the entry for the fee but takes gross PnL from the unslipped entry (`trade_fees.py`) | `enrich_trade_execution_v2`: gross from both slipped fills; sizing and fees as v1 | `app/research/execution_v2.py` | `tests/test_execution_v2.py` |
| A bar opening through the stop exits at the stop level (`combination_backtest.py`) | `resolve_bar_exit_v2`, `simulate_trade_v2` and `evaluate_candidate_trades_v2` exit at that bar's open | `app/research/execution_v2.py` | `tests/test_execution_v2.py` |
| Stop-out short replay starts on the bar after the stop | `replayShortsAtLongStops(..., "v2_stop_bar_counts")` walks the stop bar first (stop before target); v1 stays the default for the function | `frontend/src/backtest/stopOutShortReplay.ts` | `stopOutShortReplay.test.ts`, `StopOutShortReplay.test.tsx` |

**Impact** (`v2_compare.py`, `out/v2_compare.json`):

| Result | Changes under v2? |
|---|---|
| Backtest-screen LCSC 1h LONG | **No.** BTC 197 trades / +$380.86, ETH 189 / +$718.11 / fees −$140.44 / max DD $308.46, with identical exits. There were 0 gapped stops on 24/7 BTC/ETH 1h, and the screen uses no slippage. |
| This study (all windows, all cost models) | **No.** `study.py` never called the defective helpers. It already priced slipped entries in gross, gap-aware stops and a stop-bar-first short walk. ETH full range (+0.117R), train (−0.016R), validation (+0.380R), holdout (+0.218R) and every BTC number are unchanged; `out/results.json` is byte-identical to the archive. |
| Stop-out short panel (uncosted) | **Yes**, by design: ETH 1R −8.0R → −18.0R; ETH mirrored 2R +8.0R → −4.0R (§8). The panel now shows v2, with v1 alongside, labelled. |
| Any future run with slippage switched on | **Yes.** At the config's 2 bps, v1 overstates net: ETH +$682.91 (v1) vs +$647.89 (v2), BTC +$336.52 vs +$292.28. Use v2. |

## 13. What would qualify ETH for paper

Only the forward contract's `PASS_TO_PAPER_ELIGIBLE` can qualify ETH, and only for a paper decision:
- at least 40 closed forward trades and at least 9 months elapsed;
- FULL expectancy > 0.10R and PF ≥ 1.20;
- drawdown ≤ 15R;
- net positive in at least two trend regimes;
- no open exceptions;
- no drift from the lock.

Nothing in this archive qualifies it today.

Power caveat: if the true edge equals ETH's historical +0.117R, a 40-trade read lands at expectancy ≤ 0 about 30% of the time and above 0.10R only about 53% of the time (block bootstrap of the full-range trades). A KILL would not prove there is no edge, and a pass would not prove there is one.

## 14. Statement on the historical split

The validation (2025-04-10 → 2026-01-11) and holdout (2026-01-11 → 2026-10-15) windows were untouched by parameter tuning in this study. They were not untouched by selection: ETH, LCSC and the v1 rules were picked after seeing results that covered these bars.

**They can falsify the edge, and they cannot confirm it.** ETH's +0.297R pseudo-OOS is not evidence of a validated edge. The first clean evidence will come from bars opening on or after 2026-10-16 00:00 UTC, read under the frozen contract. Until then the system is **RESEARCH_ONLY**.
