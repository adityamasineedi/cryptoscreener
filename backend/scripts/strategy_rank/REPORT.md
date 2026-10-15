# Strategy ranking report

Generated 2026-10-12 from the four result sets in this folder. Full machine-readable output is in `ranked.json`; the unedited ranked tables are in `ranked.md`.

## Summary

About 1,870 backtest cells ran. 81 pass the rule: 37 in day trading, 3 in scalp, 0 in swing, 41 in grid. No strategy is production-ready on this evidence. The only consistent edge is in long-only strategies on the 1h timeframe.

Everything here is in-sample: one window, no out-of-sample test.

## Test setup

| Item | Value |
|---|---|
| Coins | BTC, ETH, BNB, SOL, XRP, DOGE, ADA, TRX, AVAX, LINK (USDT perps) |
| Timeframes | 5m, 15m, 1h, 4h, 1d |
| Window | 2026-02-14 to 2026-10-12 (1d candles end 2026-10-08) |
| Account | 1000 USD, 20 USD risk per trade, 2x leverage |
| Fees | 0.04% taker, 0.02% maker, on executed notional |
| Database | read-only session, no data pulled, no strategy code edited |
| Code state | git head 9e96798 with about 200 uncommitted changed files |

Style buckets are set by setup timeframe: scalp = 5m and 15m, day trading = 1h, swing = 4h and 1d, grid = the grid engines.

A cell (one strategy, one direction, one coin, one timeframe) passes only if all four hold:

- net profit above 0
- profit factor above 1.2
- at least 15 closed trades
- fees smaller than gross profit

## Day trading (1h)

406 cells, 88 net positive, 37 pass.

### Strategies, ten coins combined

| # | Strategy | Direction | Coins passing | Coins net positive | Trades | Net USD | Fees USD | Worst coin USD | Passing coins |
|---|---|---|---|---|---|---|---|---|---|
| 1 | CANDLE12_V2 | Long | 6 | 7 | 194 | +525.68 | -164.10 | -97.27 | ADA AVAX BTC DOGE ETH SOL |
| 2 | LARGE_CAP_SWEEP_CHOCH | Long | 5 | 7 | 367 | +986.37 | -319.56 | -106.37 | AVAX BNB BTC ETH TRX |
| 3 | COMBO_02 (screen name: COMBO_02 V1) | Long | 5 | 8 | 210 | +785.82 | -256.15 | -58.71 | BTC ETH LINK SOL XRP |
| 4 | COMBO_01 | Long | 4 | 6 | 608 | +1,035.92 | -733.96 | -150.98 | ADA BNB SOL XRP |
| 5 | COMBO_02_LOCAL | Long | 4 | 7 | 602 | +968.19 | -734.10 | -150.98 | ADA BNB SOL XRP |
| 6 | OSE_A_corrected_baseline | Long | 3 | 8 | 1,210 | +824.58 | -1,071.49 | -54.87 | BNB LINK XRP |
| 7 | OSE_B_acceptance | Long | 2 | 5 | 1,020 | +140.13 | -842.89 | -160.60 | LINK XRP |
| 8 | OSE_C_retest | Long | 2 | 7 | 614 | -101.52 | -567.06 | -338.61 | LINK XRP |
| 9 | OSE_D_faster_confirmation | Long | 1 | 6 | 1,426 | +634.78 | -1,323.92 | -217.68 | XRP |
| 10 | COMBO_02_V2 (3 coins only) | Long | 1 of 3 | 3 of 3 | 106 | +359.45 | -132.88 | +34.81 | SOL |
| 11 | COMBO_03_TRANSITION_B | Short | 1 | 4 | 126 | +51.12 | -170.82 | -108.97 | LINK |
| 12 | OSE_C_retest | Short | 1 | 2 | 523 | -600.27 | -443.68 | -187.97 | LINK |
| 13 | OSE_D_faster_confirmation | Short | 1 | 3 | 1,349 | -999.25 | -1,190.22 | -331.79 | XRP |
| 14 | OSE_B_acceptance | Short | 1 | 1 | 958 | -1,159.08 | -717.51 | -291.69 | LINK |
| 15 | SMALL_CAP_VOLUME_BOS | Long | 0 | 7 | 113 | +47.53 | -83.04 | -186.44 | none |

### Top cells

| # | Strategy | Direction | Coin | Trades | Win % | Net USD | Fees USD | Profit factor | Max drawdown USD | Passes |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | COMBO_02_LOCAL | Long | BNB | 49 | 49.0 | +449.13 | -76.29 | 2.07 | 113.78 | yes |
| 2 | COMBO_01 | Long | BNB | 49 | 49.0 | +442.76 | -74.76 | 2.03 | 162.13 | yes |
| 3 | COMBO_01 | Long | ADA | 60 | 43.3 | +372.75 | -55.89 | 1.54 | 210.37 | yes |
| 4 | OSE_D_faster_confirmation | Long | XRP | 131 | 45.0 | +332.32 | -113.62 | 1.35 | 112.24 | yes |
| 5 | COMBO_02_LOCAL | Long | ADA | 58 | 43.1 | +327.66 | -53.69 | 1.49 | 189.91 | yes |
| 6 | OSE_A_corrected_baseline | Long | LINK | 126 | 47.6 | +311.63 | -89.77 | 1.35 | 164.54 | yes |
| 7 | LARGE_CAP_SWEEP_CHOCH | Long | BNB | 37 | 48.6 | +305.88 | -43.31 | 1.85 | 66.59 | yes |
| 8 | COMBO_02_LOCAL | Long | SOL | 55 | 40.0 | +287.31 | -66.93 | 1.49 | 156.03 | yes |
| 9 | COMBO_02_V2 | Long | SOL | 39 | 43.6 | +273.93 | -46.70 | 1.62 | 120.60 | yes |
| 10 | LARGE_CAP_SWEEP_CHOCH | Long | ETH | 36 | 47.2 | +268.65 | -30.01 | 1.70 | 62.55 | yes |
| 11 | COMBO_01 | Long | SOL | 56 | 39.3 | +267.29 | -67.78 | 1.44 | 175.26 | yes |
| 12 | OSE_D_faster_confirmation | Short | XRP | 132 | 43.9 | +254.83 | -112.53 | 1.28 | 94.36 | yes |
| 13 | OSE_B_acceptance | Long | XRP | 92 | 46.7 | +247.52 | -71.25 | 1.40 | 122.77 | yes |
| 14 | OSE_D_faster_confirmation | Long | LINK | 153 | 48.4 | +234.79 | -119.59 | 1.18 | 149.37 | no: profit factor |
| 15 | LARGE_CAP_SWEEP_CHOCH | Long | TRX | 30 | 50.0 | +229.15 | -41.14 | 2.04 | 57.34 | yes |

### Best-trade check on passing cells

Net of the passing cells of each strategy, and the same net with each cell's single best trade removed. Every passing cell stays net positive.

| Strategy | Direction | Passing cells | Net USD | Net USD without best trades |
|---|---|---|---|---|
| CANDLE12_V2 | Long | 6 | +671.4 | +354.3 |
| LARGE_CAP_SWEEP_CHOCH | Long | 5 | +1,063.7 | +865.7 |
| COMBO_02 | Long | 5 | +751.3 | +463.4 |
| COMBO_02_LOCAL | Long | 4 | +1,213.1 | +989.4 |
| COMBO_01 | Long | 4 | +1,282.0 | +1,050.4 |
| OSE_A_corrected_baseline | Long | 3 | +722.8 | +603.9 |
| OSE_B_acceptance | Long | 2 | +391.1 | +311.8 |
| OSE_C_retest | Long | 2 | +243.2 | +163.9 |
| OSE_D_faster_confirmation | Long | 1 | +332.3 | +292.5 |
| COMBO_02_V2 | Long | 1 | +273.9 | +188.4 |

### Notes

- COMBO_01 and COMBO_02_LOCAL are nearly the same strategy: 42 cells have identical trade lists. Treat them as one.
- Shorts do not work on 1h. Every short strategy is negative or marginal across the ten coins.
- Fill models differ between engines. The COMBO strategies fill at the signal close with no slippage (`LEGACY_SIGNAL_CLOSE`, which the repo labels optimistic). CANDLE12_V2 and the OSE variants already include 0.02% slippage, so their figures are the more conservative ones.
- The multi-cap strategies were designed for market-cap tiers. Only 6 of the 10 coins fall inside the large-cap tier as the code resolves it, and none fall in the mid-cap or small-cap tiers.

## Scalp (5m, 15m)

340 cells, 35 net positive, 3 pass. Nothing holds up across coins.

### Strategies, ten coins combined

| # | Strategy | Direction | Timeframe | Coins passing | Coins net positive | Trades | Net USD | Fees USD |
|---|---|---|---|---|---|---|---|---|
| 1 | COMBO_02 | Short | 15m | 2 | 5 | 242 | -272.40 | -344.42 |
| 2 | LARGE_CAP_SWEEP_CHOCH | Long | 15m | 1 | 3 | 1,317 | -1,975.41 | -1,650.17 |
| 3 | COMBO_02 | Long | 15m | 0 | 5 | 336 | -63.81 | -488.39 |
| 4 | CANDLE12_V2 | Long | 15m | 0 | 3 | 571 | -881.95 | -768.09 |
| 5 | SMALL_CAP_VOLUME_BOS | Long | 15m | 0 | 3 | 451 | -993.77 | -534.43 |
| 6 | INTRADAY_BOS_SD_V1.A | Long | 5m | 0 | 0 | 1,955 | -1,011.50 | -558.91 |
| 7 | INTRADAY_BOS_SD_V1.A | Short | 5m | 0 | 0 | 1,959 | -1,078.93 | -549.65 |

### Top cells

| # | Strategy | Direction | Coin | Timeframe | Trades | Win % | Net USD | Fees USD | Profit factor | Passes |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | LARGE_CAP_SWEEP_CHOCH | Long | TRX | 15m | 120 | 43.3 | +273.15 | -187.23 | 1.37 | yes |
| 2 | LARGE_CAP_SWEEP_CHOCH | Long | ADA | 15m | 139 | 39.6 | +193.91 | -152.06 | 1.11 | no: profit factor |
| 3 | COMBO_02_LOCAL | Long | AVAX | 15m | 265 | 37.0 | +176.22 | -363.48 | 1.08 | no: profit factor |
| 4 | COMBO_02 | Long | ADA | 15m | 13 | 61.5 | +170.76 | -18.23 | 3.01 | no: under 15 trades |
| 5 | COMBO_01 | Long | AVAX | 15m | 268 | 36.9 | +149.64 | -364.72 | 1.06 | no: profit factor |
| 6 | COMBO_02 | Short | ADA | 15m | 32 | 40.6 | +66.22 | -42.71 | 1.20 | yes |
| 7 | COMBO_02 | Short | LINK | 15m | 27 | 37.0 | +63.48 | -42.26 | 1.26 | yes |

### Notes

- The three passing cells belong to strategies that lose across the ten coins as a whole, so they look like chance.
- The intraday BOS/SD engine sizes at 2.50 USD risk and 1x leverage (fixed in its code), so its USD figures are about 8 times smaller in scale than the others. Variant A is negative on all ten coins with profit factor 0.38 to 0.77. Variant D, the full strategy, made no trades in eight months.
- The scalping engine was not tested on this window. Its strategies take signals and fills from 1m candles, and 1m history starts 2026-10-01. A separate 11-day run is in `scalp/rows_supplemental_1m.json` and is not part of this ranking.

## Swing (4h, 1d)

780 cells, 92 net positive, 0 pass. No cell reaches 15 trades; the most on a single coin is 12.

### Strategies, ten coins combined

| # | Strategy | Direction | Timeframe | Coins net positive | Trades | Net USD | Fees USD | Worst coin USD |
|---|---|---|---|---|---|---|---|---|
| 1 | COMBO_03_TRANSITION_B | Short | 4h | 6 | 29 | +217.74 | -23.26 | -61.57 |
| 2 | COMBO_02_V2_1_A | Short | 1d | 5 | 9 | +127.51 | -3.56 | -41.32 |
| 3 | COMBO_03_TRANSITION_B | Long | 4h | 4 | 25 | +119.98 | -24.39 | -42.60 |
| 4 | COMBO_01 / COMBO_02_LOCAL | Short | 1d | 5 | 20 | +115.07 | -6.34 | -40.78 |
| 5 | COMBO_02_V2 | Short | 1d | 5 | 18 | +108.09 | -9.41 | -41.26 |
| 6 | COMBO_02_V2_1_A | Long | 1d | 5 | 17 | +94.98 | -7.17 | -61.14 |
| 7 | COMBO_02_V2_1B | Short | 1d | 4 | 19 | +88.89 | -10.26 | -61.55 |
| 8 | COMBO_02 | Short | 1d | 3 | 6 | +59.06 | -0.94 | -20.27 |
| 9 | COMBO_01 / COMBO_02_LOCAL | Long | 1d | 5 | 32 | +56.68 | -11.05 | -42.95 |
| 10 | COMBO_02 | Long | 4h | 6 | 87 | +16.64 | -72.27 | -162.79 |

### Notes

- These are leads to test on a longer window, not results. An eight-month window gives too few 4h and 1d trades.
- All ten SD variants made zero trades on 1d. SD variants and the V2 family were not run on 4h.

## Grid

340 cells, 179 net positive, 41 pass.

### Variants, ten coins combined (grid trading book, long only)

| # | Variant | Timeframe | Coins passing | Coins net positive | Round trips | Net USD | Fees USD | Worst coin USD |
|---|---|---|---|---|---|---|---|---|
| 1 | GTB-ARI-8 | 15m | 5 | 5 | 370 | +131.30 | -22.28 | -26.35 |
| 2 | GTB-GEO-12 | 1h | 4 | 9 | 179 | +179.12 | -7.58 | -1.34 |
| 3 | GTB-GEO-12 | 15m | 4 | 5 | 868 | +159.10 | -32.75 | -25.62 |
| 4 | GTB-GEO-8 | 15m | 4 | 5 | 394 | +156.54 | -23.64 | -26.54 |
| 5 | GTB-ARI-12 | 15m | 4 | 5 | 799 | +113.49 | -30.39 | -24.88 |
| 6 | GTB-GEO-4 | 15m | 3 | 5 | 116 | +237.51 | -15.33 | -19.70 |
| 7 | GTB-ARI-4 | 15m | 3 | 5 | 105 | +181.63 | -14.07 | -15.71 |
| 8 | GTB-GEO-8 | 1h | 2 | 8 | 87 | +175.07 | -5.96 | -16.64 |
| 9 | GTB-ARI-12 | 1h | 2 | 9 | 144 | +139.36 | -6.22 | -10.74 |
| 10 | GTB-ARI-12 | 4h | 1 | 10 | 84 | +171.98 | -3.64 | +2.84 |

GEO is geometric spacing, ARI is arithmetic; the number is the grid count.

### Top cells

| # | Variant | Coin | Timeframe | Round trips | Net USD | Fees USD | Profit factor | Max drawdown USD | Passes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | GTB-GEO-4 | LINK | 15m | 41 | +183.33 | -4.63 | 4.68 | 41.01 | yes |
| 2 | GTB-GEO-8 | LINK | 15m | 127 | +135.71 | -6.84 | 4.11 | 35.71 | yes |
| 3 | GTB-ARI-4 | ETH | 1d | 1 | +128.49 | -0.13 | n/a | 36.50 | no: under 15 trades |
| 4 | GTB-GEO-12 | LINK | 15m | 257 | +117.21 | -9.05 | 3.82 | 34.00 | yes |
| 5 | GTB-ARI-4 | LINK | 15m | 26 | +116.62 | -2.97 | 4.95 | 27.83 | yes |
| 6 | GTB-ARI-8 | LINK | 15m | 91 | +94.42 | -4.96 | 3.80 | 29.19 | yes |
| 7 | GTB-ARI-12 | LINK | 15m | 199 | +87.63 | -7.06 | 3.49 | 28.65 | yes |
| 8 | GTB-GEO-8 | DOGE | 1h | 20 | +71.78 | -1.25 | 2.30 | 49.77 | yes |

### Notes

- The combined figures are sums over ten separate 1000 USD accounts. The best variant earns about 2% per account in eight months.
- Funding is not in any grid net figure. Settlement history exists only for BTC, covering 29% of the window; the other nine coins have none.
- Leverage is 1x. The grid book rejects anything higher.
- The range is taken from the 400 bars before the window and held fixed. Nine of ten coins were stopped out in early June on 1h.
- Win rates near 99% are round trips between adjacent levels; the single stop-out is usually the one loss.
- The grid range research engine (GRID-A, GRID-B, GRID-C, GRID-A-LVC, 15m) loses 7,400 to 18,400 USD per cell on 1000 USD in all 40 cells. It has no equity stop. Do not use it.

## Not tested

| What | Cells | Why |
|---|---|---|
| Combination engine: all 5m, most of 15m, SD variants on 1h and 4h, V2 family below 1d | 1,574 | Runtime. The full grid is estimated at 130+ worker-hours. The runner is resumable. |
| Scalping engine, four strategies | 40 | Needs 1m candles from February; 1m history starts 2026-10-01. |
| `scalp_compression_retest_v1` | 10 | No rules in the code. |
| Grid UI preset with in-sample range | 50 | Look-ahead: it picks the range from the bars it backtests. A causal equivalent was run instead. |
| `bos_strategy_comparison`, seven strategies | 7 | One coin on 15m did not finish in 40 minutes. |
| Live signal engine, experimental trade-plan path, risk-controlled overlay | 3 | No per-trade USD backtest without editing application code. |
| Long-commit optimizer, trend eval, auto research | — | No source in the tree, only compiled caches. |

Duplicates removed from the ranking after checking the data: COMBO_02_CLOSED_HTF is identical to COMBO_02 in all 80 cells, and COMBO_02_V2_1A is identical to COMBO_02_V2_1_A in all 20 cells.

## Code problems found during the run

None of these were changed.

| Problem | Status |
|---|---|
| The screen's short option for COMBO_02 V1 runs COMBO_04_HTF, which made zero trades on every coin and timeframe tested. | Verified in results |
| The research service does not load 15m candles for the COMBO_03 family; the baseline and C variants made zero trades. That the missing 15m data is the cause is an inference. | Loading verified in code, cause inferred |
| The grid UI scans and backtests the same bars. | Read from code |
| The `funding_rates` table is a roughly 4-second rate stream from 2026-10-01, and the intraday runner would charge each row as a funding settlement. | Read from code, not executed |
| `trade_fees.py` gained a running-equity option on 2026-10-12, after the earlier audit scan; capped-trade USD figures moved slightly as a result. | Verified by re-run |

## Files

| Path | Contents |
|---|---|
| `REPORT.md` | This report |
| `ranked.md`, `ranked.json` | Ranked tables and every ranked cell |
| `merge_rank.py` | Merges the four result sets and applies the rule |
| `combo/` | Combination engine: `run_combo_rank.py`, `rows.json`, `trades/` |
| `scalp/` | Scalping and intraday: `run_scalp_intraday.py`, `rows.json`, `rows_supplemental_1m.json`, `trades/` |
| `grid/` | Grid: `run_grid_rank.py`, `rows.json`, `_funding_coverage.json`, `trades/` |
| `multicap/` | Multi-cap, CANDLE12, OSE, short pullback: `run_multicap_rank.py`, `rows.json`, `_inventory.json`, `trades/` |

To rebuild the ranking after adding results: `python -B merge_rank.py` from this folder.
