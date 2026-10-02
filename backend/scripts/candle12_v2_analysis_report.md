# Candle-1 / Candle-2 Research V2 — Analysis Report

- Experiment: `candle12_v2_full_20261001T141908Z`
- Source: `E:/cryptoscreener/backend/scripts/candle12_v2_research_result_full.json`
- Analysis created (UTC): `2026-10-01T14:32:43.851518+00:00`
- Reporting sample threshold: **30** closed trades

This document evaluates an existing historical research experiment only. It does not change live signal logic, production thresholds, or research rules.

## 1. Data integrity

- Consistency validation: **PASS**
- No unresolved internal consistency issues among persisted totals.
- Explained differences (not silently corrected):
  - candles_processed total (604332) = sum(eligible series candle_count) (604328) + excluded series candle_count (4). Runner counted loaded candles before INSUFFICIENT_DATA exclusion.

| Check | Value |
|---|---|
| requested_symbols | 528 |
| symbols_processed | 527 |
| series_eligible | 1581 |
| series_in_list | 1581 |
| series_excluded | 3 |
| candles_processed_total | 604332 |
| sum_series_candle_count | 604328 |
| excluded_candle_count_sum | 4 |
| setups_evaluated | 490667 |
| trades_generated | 2540 |
| closed_trades_overall_sample_size | 2469 |
| open_trades_implied | 71 |
| TRAIN_sample_size | 1254 |
| VALIDATION_sample_size | 686 |
| OOS_sample_size | 529 |
| TRAIN_VAL_OOS_sum | 2469 |
| gap_count_sum | 0 |
| duplicate_count_sum | 0 |

Excluded series:

- `CTUSDT` `5m`: n=2 < min_bars=50 (status=INSUFFICIENT_DATA, candle_count=2)
- `CTUSDT` `15m`: n=1 < min_bars=50 (status=INSUFFICIENT_DATA, candle_count=1)
- `CTUSDT` `1h`: n=1 < min_bars=50 (status=INSUFFICIENT_DATA, candle_count=1)

- Note: totals.candles_processed includes candles loaded for series later marked INSUFFICIENT_DATA/excluded; eligible series[] candle_count sum is lower by that excluded amount when the runner increments total_candles before the skip.
- Note: totals.candles_processed is loaded OHLCV row counts (not instrumentation candles_processed, which excludes warmup/end trim).
- Note: trades_generated may exceed closed sample_size when OPEN trades remain.
- Note: Per-series metrics are FULL-period; TRAIN/VAL/OOS are global chronological buckets across all trades in the saved aggregates.

## 2. Historical-depth distribution

Series do **not** share equal historical coverage. Total candles = 604332 is a sum across unequal depths.

| Depth bucket | Series | Closed trades | % of trades |
|---|---:|---:|---:|
| <100 | 0 | 0 | 0.00% |
| 100–299 | 1557 | 828 | 33.54% |
| 300–999 | 0 | 0 | 0.00% |
| 1000–4999 | 0 | 0 | 0.00% |
| 5000+ | 24 | 1641 | 66.46% |

## 3. Overall results (FULL period aggregate)

### FULL / overall

| Metric | Value |
|---|---|
| sample_size | 2469 |
| LONG | 1462 |
| SHORT | 1007 |
| TP1_rate | 59.54% |
| TP2_rate | 0.04% |
| TP3_rate | 0.00% |
| SL_rate | 40.42% |
| expectancy_R | -0.1544 |
| average_R | -0.1544 |
| median_R | -0.0549 |
| profit_factor | 0.6637 |
| max_drawdown_R | 400.7946 |
| MAE_R | 0.8983 |
| MFE_R | 0.8518 |
| average_holding_bars | 11.5476 |
| net_R | -0.1544 |
| gross_R | -0.0752 |

## 4. TRAIN results

### TRAINING_PERIOD

| Metric | Value |
|---|---|
| sample_size | 1254 |
| LONG | 684 |
| SHORT | 570 |
| TP1_rate | 59.41% |
| TP2_rate | 0.08% |
| TP3_rate | 0.00% |
| SL_rate | 40.51% |
| expectancy_R | -0.1248 |
| average_R | -0.1248 |
| median_R | -0.0581 |
| profit_factor | 0.7295 |
| max_drawdown_R | 179.5367 |
| MAE_R | 0.8938 |
| MFE_R | 0.9184 |
| average_holding_bars | 13.1499 |
| net_R | -0.1248 |
| gross_R | -0.0438 |

## 5. Validation results

### VALIDATION_PERIOD

| Metric | Value |
|---|---|
| sample_size | 686 |
| LONG | 449 |
| SHORT | 237 |
| TP1_rate | 55.39% |
| TP2_rate | 0.00% |
| TP3_rate | 0.00% |
| SL_rate | 44.61% |
| expectancy_R | -0.2003 |
| average_R | -0.2003 |
| median_R | -0.0878 |
| profit_factor | 0.5969 |
| max_drawdown_R | 148.0379 |
| MAE_R | 0.9102 |
| MFE_R | 0.8057 |
| average_holding_bars | 9.9592 |
| net_R | -0.2003 |
| gross_R | -0.1268 |

## 6. OOS results

OOS is evaluation-only. Parameters were fixed before OOS inspection in the original experiment.

### OUT_OF_SAMPLE_PERIOD

| Metric | Value |
|---|---|
| sample_size | 529 |
| LONG | 329 |
| SHORT | 200 |
| TP1_rate | 65.22% |
| TP2_rate | 0.00% |
| TP3_rate | 0.00% |
| SL_rate | 34.78% |
| expectancy_R | -0.1649 |
| average_R | -0.1649 |
| median_R | -0.0439 |
| profit_factor | 0.5920 |
| max_drawdown_R | 93.5162 |
| MAE_R | 0.8916 |
| MFE_R | 0.7398 |
| average_holding_bars | 9.8091 |
| net_R | -0.1649 |
| gross_R | -0.0827 |

## 7. Timeframe breakdown

Saved artifact provides FULL-period aggregates by timeframe. TRAIN/VALIDATION/OOS × timeframe matrices were **not persisted** and are marked unavailable (no backtest repair rerun).

### Timeframe 5m (FULL period)

| Metric | Value |
|---|---|
| sample_size | 773 |
| LONG | 442 |
| SHORT | 331 |
| TP1_rate | 55.89% |
| TP2_rate | 0.13% |
| TP3_rate | 0.00% |
| SL_rate | 43.98% |
| expectancy_R | -0.2707 |
| average_R | -0.2707 |
| median_R | -0.1831 |
| profit_factor | 0.4956 |
| max_drawdown_R | 211.5321 |
| MAE_R | 0.9293 |
| MFE_R | 0.8363 |
| average_holding_bars | 13.1371 |
| net_R | -0.2707 |
| gross_R | -0.1409 |

#### 5m × TRAIN / VALIDATION / OOS

| Period | Status |
|---|---|
| TRAIN | UNAVAILABLE_IN_SAVED_ARTIFACT |
| VALIDATION | UNAVAILABLE_IN_SAVED_ARTIFACT |
| OOS | UNAVAILABLE_IN_SAVED_ARTIFACT |

### Timeframe 15m (FULL period)

| Metric | Value |
|---|---|
| sample_size | 764 |
| LONG | 480 |
| SHORT | 284 |
| TP1_rate | 57.72% |
| TP2_rate | 0.00% |
| TP3_rate | 0.00% |
| SL_rate | 42.28% |
| expectancy_R | -0.1920 |
| average_R | -0.1920 |
| median_R | -0.0882 |
| profit_factor | 0.6011 |
| max_drawdown_R | 147.7488 |
| MAE_R | 0.9179 |
| MFE_R | 0.8172 |
| average_holding_bars | 11.8010 |
| net_R | -0.1920 |
| gross_R | -0.1101 |

#### 15m × TRAIN / VALIDATION / OOS

| Period | Status |
|---|---|
| TRAIN | UNAVAILABLE_IN_SAVED_ARTIFACT |
| VALIDATION | UNAVAILABLE_IN_SAVED_ARTIFACT |
| OOS | UNAVAILABLE_IN_SAVED_ARTIFACT |

### Timeframe 1h (FULL period)

| Metric | Value |
|---|---|
| sample_size | 932 |
| LONG | 540 |
| SHORT | 392 |
| TP1_rate | 64.06% |
| TP2_rate | 0.00% |
| TP3_rate | 0.00% |
| SL_rate | 35.94% |
| expectancy_R | -0.0271 |
| average_R | -0.0271 |
| median_R | 0.0246 |
| profit_factor | 0.9279 |
| max_drawdown_R | 47.0276 |
| MAE_R | 0.8542 |
| MFE_R | 0.8951 |
| average_holding_bars | 10.0215 |
| net_R | -0.0271 |
| gross_R | 0.0079 |

#### 1h × TRAIN / VALIDATION / OOS

| Period | Status |
|---|---|
| TRAIN | UNAVAILABLE_IN_SAVED_ARTIFACT |
| VALIDATION | UNAVAILABLE_IN_SAVED_ARTIFACT |
| OOS | UNAVAILABLE_IN_SAVED_ARTIFACT |

## 8. Direction breakdown

FULL-period LONG/SHORT aggregates are available. Direction × timeframe × period matrices were not persisted.

### Direction LONG (FULL period)

| Metric | Value |
|---|---|
| sample_size | 1462 |
| LONG | 1462 |
| SHORT | 0 |
| TP1_rate | 58.48% |
| TP2_rate | 0.07% |
| TP3_rate | 0.00% |
| SL_rate | 41.45% |
| expectancy_R | -0.0927 |
| average_R | -0.0927 |
| median_R | -0.0477 |
| profit_factor | 0.8006 |
| max_drawdown_R | 163.0932 |
| MAE_R | 0.8941 |
| MFE_R | 0.9359 |
| average_holding_bars | 10.2093 |
| net_R | -0.0927 |
| gross_R | -0.0169 |

### Direction SHORT (FULL period)

| Metric | Value |
|---|---|
| sample_size | 1007 |
| LONG | 0 |
| SHORT | 1007 |
| TP1_rate | 61.07% |
| TP2_rate | 0.00% |
| TP3_rate | 0.00% |
| SL_rate | 38.93% |
| expectancy_R | -0.2439 |
| average_R | -0.2439 |
| median_R | -0.0648 |
| profit_factor | 0.4581 |
| max_drawdown_R | 253.5493 |
| MAE_R | 0.9054 |
| MFE_R | 0.7092 |
| average_holding_bars | 13.4906 |
| net_R | -0.2439 |
| gross_R | -0.1598 |

### Direction × timeframe (FULL) — unavailable

Not present in `candle12_v2_research_result_full.json`. No inference substituted.

### Direction × TRAIN/VALIDATION/OOS — unavailable

Not present in the saved artifact beyond overall period aggregates (section 4–6 include LONG/SHORT counts inside each period block).

Period-level direction counts (from saved by_split):

| Period | LONG | SHORT | sample_size |
|---|---:|---:|---:|
| TRAIN | 684 | 570 | 1254 |
| VALIDATION | 449 | 237 | 686 |
| OOS | 329 | 200 | 529 |

## 9. BTC / ETH / SOL breakdown

### BTCUSDT (FULL, all timeframes combined)

| Metric | Value |
|---|---|
| sample_size | 218 |
| LONG | 114 |
| SHORT | 104 |
| TP1_rate | 62.84% |
| TP2_rate | 0.00% |
| TP3_rate | 0.00% |
| SL_rate | 37.16% |
| expectancy_R | -0.2511 |
| average_R | -0.2511 |
| median_R | -0.1484 |
| profit_factor | 0.4811 |
| max_drawdown_R | 60.3243 |
| MAE_R | 0.9238 |
| MFE_R | 0.8439 |
| average_holding_bars | 13.2523 |
| net_R | -0.2511 |
| gross_R | -0.1153 |

#### BTCUSDT by timeframe (series FULL metrics)

| TF | candles | calendar_days | closed_trades | expectancy_R | net_R | sample_label |
|---|---:|---:|---:|---:|---:|---|
| 5m | 12200 | 42.36 | 62 | -0.4337 | -0.4337 | — |
| 15m | 12200 | 127.07 | 67 | -0.3024 | -0.3024 | — |
| 1h | 12200 | 508.29 | 89 | -0.0853 | -0.0853 | — |

### ETHUSDT (FULL, all timeframes combined)

| Metric | Value |
|---|---|
| sample_size | 183 |
| LONG | 94 |
| SHORT | 89 |
| TP1_rate | 67.76% |
| TP2_rate | 0.00% |
| TP3_rate | 0.00% |
| SL_rate | 32.24% |
| expectancy_R | -0.0532 |
| average_R | -0.0532 |
| median_R | -0.0257 |
| profit_factor | 0.8603 |
| max_drawdown_R | 23.5216 |
| MAE_R | 0.8355 |
| MFE_R | 0.9371 |
| average_holding_bars | 16.5792 |
| net_R | -0.0532 |
| gross_R | 0.0353 |

#### ETHUSDT by timeframe (series FULL metrics)

| TF | candles | calendar_days | closed_trades | expectancy_R | net_R | sample_label |
|---|---:|---:|---:|---:|---:|---|
| 5m | 12200 | 42.36 | 54 | -0.3726 | -0.3726 | — |
| 15m | 12200 | 127.07 | 59 | -0.0294 | -0.0294 | — |
| 1h | 12200 | 508.29 | 70 | 0.1732 | 0.1732 | — |

### SOLUSDT (FULL, all timeframes combined)

| Metric | Value |
|---|---|
| sample_size | 186 |
| LONG | 107 |
| SHORT | 79 |
| TP1_rate | 66.13% |
| TP2_rate | 0.00% |
| TP3_rate | 0.00% |
| SL_rate | 33.87% |
| expectancy_R | -0.0614 |
| average_R | -0.0614 |
| median_R | 0.0053 |
| profit_factor | 0.8412 |
| max_drawdown_R | 19.0602 |
| MAE_R | 0.8872 |
| MFE_R | 0.9618 |
| average_holding_bars | 14.9624 |
| net_R | -0.0614 |
| gross_R | 0.0235 |

#### SOLUSDT by timeframe (series FULL metrics)

| TF | candles | calendar_days | closed_trades | expectancy_R | net_R | sample_label |
|---|---:|---:|---:|---:|---:|---|
| 5m | 12200 | 42.36 | 64 | -0.1204 | -0.1204 | — |
| 15m | 12200 | 127.07 | 51 | -0.1088 | -0.1088 | — |
| 1h | 12200 | 508.29 | 71 | 0.0257 | 0.0257 | — |

### All other eligible symbols (aggregate FULL)

| Metric | Value |
|---|---|
| sample_size | 1882 |
| LONG | 1147 |
| SHORT | 735 |
| TP1_rate | 57.70% |
| TP2_rate | 0.05% |
| TP3_rate | 0.00% |
| SL_rate | 42.24% |
| expectancy_R | -0.1622 |
| average_R | -0.1622 |
| median_R | — |
| profit_factor | — |
| max_drawdown_R | — |
| MAE_R | 0.9025 |
| MFE_R | 0.8359 |
| average_holding_bars | 10.5234 |
| net_R | -0.1622 |
| gross_R | -0.0911 |

## 10. Sample-size distribution

INSUFFICIENT_SAMPLE when closed_trades < 30, matching the experiment min_sample_size_warning. This is a reporting threshold for cautious interpretation, not a universal statistical proof threshold.

| Closed-trade bucket | Series count |
|---|---:|
| 0 | 910 |
| 1–9 | 647 |
| 10–29 | 0 |
| 30–49 | 0 |
| 50–99 | 24 |
| 100–249 | 0 |
| 250+ | 0 |

- Series labeled INSUFFICIENT_SAMPLE (<30): **1557** (98.48%)
- Series at/above reporting threshold: **24**

## 11. Historical-depth analysis

Objective metrics below are trade-weighted means of per-series FULL metrics within each candle-count bucket. Profit factor and max drawdown are not recomputed without trade lists. This section measures whether aggregates are dominated by short-history series; it does not compare groups as better/worse.

### Depth <300 candles

| Metric | Value |
|---|---|
| sample_size | 828 |
| LONG | 560 |
| SHORT | 268 |
| TP1_rate | 51.69% |
| TP2_rate | 0.12% |
| TP3_rate | 0.00% |
| SL_rate | 48.19% |
| expectancy_R | -0.1803 |
| average_R | -0.1803 |
| median_R | — |
| profit_factor | — |
| max_drawdown_R | — |
| MAE_R | 0.9288 |
| MFE_R | 0.7908 |
| average_holding_bars | 7.8466 |
| net_R | -0.1803 |
| gross_R | -0.1265 |

### Depth 300–999

**INSUFFICIENT_SAMPLE** (closed sample_size=0 < reporting threshold 30).

| Metric | Value |
|---|---|
| sample_size | 0 |
| LONG | 0 |
| SHORT | 0 |
| TP1_rate | — |
| TP2_rate | — |
| TP3_rate | — |
| SL_rate | — |
| expectancy_R | — |
| average_R | — |
| median_R | — |
| profit_factor | — |
| max_drawdown_R | — |
| MAE_R | — |
| MFE_R | — |
| average_holding_bars | — |
| net_R | — |
| gross_R | — |

### Depth 1000–4999

**INSUFFICIENT_SAMPLE** (closed sample_size=0 < reporting threshold 30).

| Metric | Value |
|---|---|
| sample_size | 0 |
| LONG | 0 |
| SHORT | 0 |
| TP1_rate | — |
| TP2_rate | — |
| TP3_rate | — |
| SL_rate | — |
| expectancy_R | — |
| average_R | — |
| median_R | — |
| profit_factor | — |
| max_drawdown_R | — |
| MAE_R | — |
| MFE_R | — |
| average_holding_bars | — |
| net_R | — |
| gross_R | — |

### Depth 5000+

| Metric | Value |
|---|---|
| sample_size | 1641 |
| LONG | 902 |
| SHORT | 739 |
| TP1_rate | 63.50% |
| TP2_rate | 0.00% |
| TP3_rate | 0.00% |
| SL_rate | 36.50% |
| expectancy_R | -0.1413 |
| average_R | -0.1413 |
| median_R | — |
| profit_factor | — |
| max_drawdown_R | — |
| MAE_R | 0.8843 |
| MFE_R | 0.8808 |
| average_holding_bars | 13.4150 |
| net_R | -0.1413 |
| gross_R | -0.0493 |

## 12. OOS stability observations

The saved result JSON does not persist trade-level rows or period×timeframe×direction aggregates. Stability observations below use overall by_split only. No backtest rerun was performed.

- Expectancy_R sign is negative in TRAIN, VALIDATION, and OOS (values: TRAIN=-0.12480776127411038, VALIDATION=-0.2003272757056858, OOS=-0.16489210778649468).
- Profit factor by period: TRAIN=0.7294647420165148, VALIDATION=0.5969155469595837, OOS=0.5919951226139369.
- TP1 rate by period: TRAIN=0.5940988835725678, VALIDATION=0.5539358600583091, OOS=0.6521739130434783.
- SL rate by period: TRAIN=0.405103668261563, VALIDATION=0.446064139941691, OOS=0.34782608695652173.
- MAE_R by period: TRAIN=0.8937900484904865, VALIDATION=0.910179538799134, OOS=0.8916381764472662.
- MFE_R by period: TRAIN=0.9184085817489022, VALIDATION=0.8056814735909145, OOS=0.7398361926950133.
- Closed-trade counts by period: TRAIN=1254, VALIDATION=686, OOS=529 (chronological 60/20/20 split; counts are not normalized per calendar day).

No TRAIN/VALIDATION/OOS × timeframe or × direction×timeframe matrix is available in the saved artifact; those comparisons are omitted rather than invented.

## 13. Fees / slippage impact

- Configured trading_fee=0.0004, entry_slippage=0.0002, exit_slippage=0.0002
- Overall gross expectancy_R=-0.0752
- Overall net expectancy_R=-0.1544
- Difference (gross − net)=0.0792
- Approx total cost contribution (R units)=195.4788
- approx_total_cost_contribution_R = (mean gross_R - mean net_R) * closed_trades. Exact per-trade fee/slippage totals were not persisted.

| Period | gross_R | net_R | gross−net |
|---|---:|---:|---:|
| TRAIN | -0.0438 | -0.1248 | 0.0810 |
| VALIDATION | -0.1268 | -0.2003 | 0.0735 |
| OOS | -0.0827 | -0.1649 | 0.0822 |

## 14. Concentration analysis

Figures below measure concentration of closed-trade sample mass. They are not rankings and do not select winners.

| Slice | sample_size | pct of closed trades |
|---|---:|---:|
| Timeframe 5m | 773 | 31.31% |
| Timeframe 15m | 764 | 30.94% |
| Timeframe 1h | 932 | 37.75% |
| Direction LONG | 1462 | 59.21% |
| Direction SHORT | 1007 | 40.79% |
| BTC+ETH+SOL combined | 587 | 23.77% |
| All other symbols | 1882 | 76.23% |
| Top-5 symbols (concentration only) | 1075 | 43.54% |
| Top-10 symbols (concentration only) | 1655 | 67.03% |
| Depth 5000+ candles | 1641 | 66.46% |
| Depth <300 candles | 828 | 33.54% |

- Symbol HHI (Herfindahl–Hirschman on closed-trade shares): 0.0559
- Other-symbol count with ≥1 closed trade: 419

## 15. Limitations

- Trade-level rows were not saved in the experiment JSON; some cross-tabs cannot be rebuilt.
- TRAIN/VALIDATION/OOS × timeframe matrices are unavailable in the saved artifact.
- Direction × timeframe × period matrices are unavailable in the saved artifact.
- Depth-bucket profit_factor and max_drawdown_R are not recomputed without trade lists.
- Median_R in depth buckets is a trade-weighted mean of per-series medians, not a pooled median.
- Most series have short calendar spans; total candle count is dominated by unequal depths.
- Series metrics are FULL-period; period metrics are global chronological buckets.
- INSUFFICIENT_SAMPLE uses reporting threshold 30 (experiment warning), not a universal test.
- This analysis does not establish future profitability.

## Research observations

- Integrity check passed on persisted totals (symbols_processed=527, series=1581, candles=604332, setups=490667, trades_generated=2540, closed=2469).
- TRAIN/VALIDATION/OOS closed-trade counts are 1254/686/529 (sum 2469).
- Gap_count sum=0; duplicate_count sum=0; excluded series=3.
- Depth <300 candles: 1557 series and 828 closed trades (33.54% of trades).
- Depth 5000+: 24 series and 1641 closed trades (66.46% of trades).
- FULL overall expectancy_R=-0.15437884519679648, net_R=-0.15437884519679648, gross_R=-0.07520558372692933.
- Period expectancy_R: TRAIN=-0.12480776127411038, VALIDATION=-0.2003272757056858, OOS=-0.16489210778649468.
- FULL timeframe sample sizes: 5m=773, 15m=764, 1h=932.
- FULL direction sample sizes: LONG=1462, SHORT=1007.
- BTC+ETH+SOL combined closed trades=587 (23.77%); all other symbols=1882 (76.23%).
- Series with closed_trades < 30 (INSUFFICIENT_SAMPLE label): 1557 / 1581.
- Cost impact (overall mean): gross_R − net_R = 0.07917326146986715; approx total cost contribution_R = 195.478782569102.
- Top-5 symbols account for 43.54% of closed trades (concentration statistic only).
- TRAIN/VALIDATION/OOS × timeframe and direction×timeframe×period matrices are absent from the saved experiment output; this analysis does not invent those cells.

### Explicit statements

- This is historical research.
- OOS is evaluation-only.
- The experiment does not establish future profitability.
- Live signal logic was not changed.
- Production thresholds were not changed.
- No parameter optimization was performed.
- LOOKBACK=300, warmup=50, and Candle-1/Candle-2 rules were not changed by this analysis.
