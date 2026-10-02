# HTF Alignment Sensitivity Study (Research Only)

Generated: `2026-10-02T16:36:15.457825+00:00`
Dataset: BOS Combination Research · Combo: `COMBO_02` · Source: `trend_regime_trade_rows.json`

> Analysis only. Existing trades preserved. Live engines and production thresholds unchanged. Scenarios are not ranked.

## 1. Dataset

- Closed trades: **219**
- Symbols: `['BTCUSDT', 'ETHUSDT', 'SOLUSDT']`
- Timeframes present: `['15m', '1h']`
- Directions: `['LONG', 'SHORT']`
- 5m: **NO DATA**
- Result counts: `{'TP1': 58, 'SL': 160, 'TP3': 1}`
- Current regime counts: `{'HTF_CONFLICT': 88, 'LOCAL_ONLY': 67, 'ALIGNED_TREND': 64}`
- Research HTF state counts: `{'HTF_CONFLICT': 88, 'HTF_NEUTRAL_OR_UNAVAILABLE': 67, 'HTF_ALIGNED': 64}`

## 2. Existing regime definition

- `ALIGNED_TREND`: setup trending and available directional HTF(s) agree, none opposite
- `HTF_CONFLICT`: setup trending and ≥1 HTF opposite
- `LOCAL_ONLY`: setup trending; HTF neutral/unavailable/no directional agreement
- `NEUTRAL_STRUCTURE`: setup not BULLISH/BEARISH
- Note: Labels taken from existing trend_regime_trade_rows.json; not recomputed.

## 3. Scenario definitions

- **CURRENT** (`A_CURRENT`): All historical trades; no hypothetical HTF filter.
- **HTF_ALIGNED only** (`B_HTF_ALIGNED_ONLY`): Retain only trades where setup direction agrees with available directional HTF.
- **HTF_ALIGNED + HTF_NEUTRAL_OR_UNAVAILABLE** (`C_HTF_ALIGNED_PLUS_NEUTRAL_OR_UNAVAILABLE`): Retain HTF_ALIGNED plus trades with no opposing directional HTF (neutral / unavailable HTF). Does not treat unavailable as bullish/bearish.
- **CURRENT exclude HTF_CONFLICT** (`D_CURRENT_EXCLUDE_HTF_CONFLICT`): Retain current dataset excluding HTF_CONFLICT trades.

Research filter labels:
- `HTF_ALIGNED`: Setup direction agrees with available directional HTF.
- `HTF_CONFLICT`: Setup direction disagrees with at least one directional HTF.
- `HTF_NEUTRAL_OR_UNAVAILABLE`: No opposing HTF direction is available (neutral / unavailable / local-only). Unavailable is not treated as bullish/bearish.
- Mapping: `{'ALIGNED_TREND': 'HTF_ALIGNED', 'HTF_CONFLICT': 'HTF_CONFLICT', 'LOCAL_ONLY': 'HTF_NEUTRAL_OR_UNAVAILABLE', 'NEUTRAL_STRUCTURE': 'HTF_NEUTRAL_OR_UNAVAILABLE'}`

## 4. Overall results

| Scenario | orig | retained | excluded | retention % | wins | losses | win rate | SL rate | TP1 rate | mean R | median R | PF | max DD R | MAE | MFE | sample |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| CURRENT | 219 | 219 | 0 | 100.0 | 59 | 160 | 26.9% | 73.1% | 26.9% | -0.146 | -1.000 | 0.801 | 43.725 | 339.53 | 354.35 | SAMPLE_SIZE_OK |
| HTF_ALIGNED only | 219 | 64 | 155 | 29.2 | 26 | 38 | 40.6% | 59.4% | 40.6% | 0.251 | -1.000 | 1.424 | 13.930 | 264.52 | 358.25 | SAMPLE_SIZE_OK |
| HTF_ALIGNED + HTF_NEUTRAL_OR_UNAVAILABLE | 219 | 131 | 88 | 59.8 | 41 | 90 | 31.3% | 68.7% | 31.3% | -0.038 | -1.000 | 0.945 | 25.356 | 312.20 | 366.39 | SAMPLE_SIZE_OK |
| CURRENT exclude HTF_CONFLICT | 219 | 131 | 88 | 59.8 | 41 | 90 | 31.3% | 68.7% | 31.3% | -0.038 | -1.000 | 0.945 | 25.356 | 312.20 | 366.39 | SAMPLE_SIZE_OK |

## 5. LONG results

| Scenario | n | wins | losses | win rate | mean R | sample |
|---|---:|---:|---:|---:|---:|---|
| CURRENT | 107 | 37 | 70 | 34.6% | 0.093 | SAMPLE_SIZE_OK |
| HTF_ALIGNED only | 40 | 22 | 18 | 55.0% | 0.702 | SAMPLE_SIZE_OK |
| HTF_ALIGNED + HTF_NEUTRAL_OR_UNAVAILABLE | 71 | 28 | 43 | 39.4% | 0.223 | SAMPLE_SIZE_OK |
| CURRENT exclude HTF_CONFLICT | 71 | 28 | 43 | 39.4% | 0.223 | SAMPLE_SIZE_OK |

## 6. SHORT results

| Scenario | n | wins | losses | win rate | mean R | sample |
|---|---:|---:|---:|---:|---:|---|
| CURRENT | 112 | 22 | 90 | 19.6% | -0.374 | SAMPLE_SIZE_OK |
| HTF_ALIGNED only | 24 | 4 | 20 | 16.7% | -0.499 | INSUFFICIENT_SAMPLE |
| HTF_ALIGNED + HTF_NEUTRAL_OR_UNAVAILABLE | 60 | 13 | 47 | 21.7% | -0.346 | SAMPLE_SIZE_OK |
| CURRENT exclude HTF_CONFLICT | 60 | 13 | 47 | 21.7% | -0.346 | SAMPLE_SIZE_OK |

## 7. 15M results

| Scenario | n | wins | losses | win rate | mean R | LONG n/wr/meanR | SHORT n/wr/meanR | sample |
|---|---:|---:|---:|---:|---:|---|---|---|
| CURRENT | 109 | 26 | 83 | 23.9% | -0.235 | 58 / 31.0% / -0.036 | 51 / 15.7% / -0.461 | SAMPLE_SIZE_OK |
| HTF_ALIGNED only | 34 | 12 | 22 | 35.3% | 0.095 | 23 / 47.8% / 0.488 | 11 / 9.1% / -0.725 | SAMPLE_SIZE_OK |
| HTF_ALIGNED + HTF_NEUTRAL_OR_UNAVAILABLE | 56 | 16 | 40 | 28.6% | -0.113 | 35 / 37.1% / 0.161 | 21 / 14.3% / -0.568 | SAMPLE_SIZE_OK |
| CURRENT exclude HTF_CONFLICT | 56 | 16 | 40 | 28.6% | -0.113 | 35 / 37.1% / 0.161 | 21 / 14.3% / -0.568 | SAMPLE_SIZE_OK |

## 8. 1H results

| Scenario | n | wins | losses | win rate | mean R | LONG n/wr/meanR | SHORT n/wr/meanR | sample |
|---|---:|---:|---:|---:|---:|---|---|---|
| CURRENT | 110 | 33 | 77 | 30.0% | -0.057 | 49 / 38.8% / 0.245 | 61 / 23.0% / -0.300 | SAMPLE_SIZE_OK |
| HTF_ALIGNED only | 30 | 14 | 16 | 46.7% | 0.429 | 17 / 64.7% / 0.991 | 13 / 23.1% / -0.308 | SAMPLE_SIZE_OK |
| HTF_ALIGNED + HTF_NEUTRAL_OR_UNAVAILABLE | 75 | 25 | 50 | 33.3% | 0.019 | 36 / 41.7% / 0.283 | 39 / 25.6% / -0.226 | SAMPLE_SIZE_OK |
| CURRENT exclude HTF_CONFLICT | 75 | 25 | 50 | 33.3% | 0.019 | 36 / 41.7% / 0.283 | 39 / 25.6% / -0.226 | SAMPLE_SIZE_OK |

### 5M

**NO DATA** — current dataset contains zero 5m trades.

## 9. Timeframe × direction × HTF cross-tab

| Cell | n | wins | losses | loss rate | mean R | sample |
|---|---:|---:|---:|---:|---:|---|
| 15M LONG ALIGNED | 23 | 11 | 12 | 52.2% | 0.488 | INSUFFICIENT_SAMPLE |
| 15M LONG CONFLICT | 23 | 5 | 18 | 78.3% | -0.335 | INSUFFICIENT_SAMPLE |
| 15M LONG NEUTRAL_OR_UNAVAILABLE | 12 | 2 | 10 | 83.3% | -0.466 | INSUFFICIENT_SAMPLE |
| 15M SHORT ALIGNED | 11 | 1 | 10 | 90.9% | -0.725 | INSUFFICIENT_SAMPLE |
| 15M SHORT CONFLICT | 30 | 5 | 25 | 83.3% | -0.387 | SAMPLE_SIZE_OK |
| 15M SHORT NEUTRAL_OR_UNAVAILABLE | 10 | 2 | 8 | 80.0% | -0.395 | INSUFFICIENT_SAMPLE |
| 1H LONG ALIGNED | 17 | 11 | 6 | 35.3% | 0.991 | INSUFFICIENT_SAMPLE |
| 1H LONG CONFLICT | 13 | 4 | 9 | 69.2% | 0.139 | INSUFFICIENT_SAMPLE |
| 1H LONG NEUTRAL_OR_UNAVAILABLE | 19 | 4 | 15 | 78.9% | -0.350 | INSUFFICIENT_SAMPLE |
| 1H SHORT ALIGNED | 13 | 3 | 10 | 76.9% | -0.308 | INSUFFICIENT_SAMPLE |
| 1H SHORT CONFLICT | 22 | 4 | 18 | 81.8% | -0.432 | INSUFFICIENT_SAMPLE |
| 1H SHORT NEUTRAL_OR_UNAVAILABLE | 26 | 7 | 19 | 73.1% | -0.185 | INSUFFICIENT_SAMPLE |
| 5M | 0 | — | — | — | — | NO DATA |

## 10. Retention analysis

| Scenario | original | retained | excluded | retention % |
|---|---:|---:|---:|---:|
| CURRENT | 219 | 219 | 0 | 100.0 |
| HTF_ALIGNED only | 219 | 64 | 155 | 29.2 |
| HTF_ALIGNED + HTF_NEUTRAL_OR_UNAVAILABLE | 219 | 131 | 88 | 59.8 |
| CURRENT exclude HTF_CONFLICT | 219 | 131 | 88 | 59.8 |

## 11. Loss-share vs loss-rate analysis

Loss **rate** = losses in category / trades in category. Loss **share** = losses in category / all losers.

Total losers: **160**

| HTF state | n | losses | loss rate | loss share | sample |
|---|---:|---:|---:|---:|---|
| HTF_ALIGNED | 64 | 38 | 59.4% | 23.8% | SAMPLE_SIZE_OK |
| HTF_CONFLICT | 88 | 70 | 79.5% | 43.8% | SAMPLE_SIZE_OK |
| HTF_NEUTRAL_OR_UNAVAILABLE | 67 | 52 | 77.6% | 32.5% | SAMPLE_SIZE_OK |

## 12. Bootstrap uncertainty

Bootstrap 95% intervals for mean R (uncertainty only; not used to select a scenario).

| Scenario | mean R | CI low | CI high | n |
|---|---:|---:|---:|---:|
| CURRENT | -0.146 | -0.325 | 0.040 | 219 |
| HTF_ALIGNED only | 0.251 | -0.133 | 0.616 | 64 |
| HTF_ALIGNED + HTF_NEUTRAL_OR_UNAVAILABLE | -0.038 | -0.274 | 0.201 | 131 |
| CURRENT exclude HTF_CONFLICT | -0.038 | -0.274 | 0.201 | 131 |

Note: CI omitted when n_R < 10 (same rule as trend_regime_diagnostic).

## 13. Multiple-testing warning

**MULTIPLE_TESTING_RISK: four predefined scenarios are reported on the same closed-trade sample. Intervals and rates are descriptive only; do not treat any scenario as selected or validated by this study.**

No threshold optimization, no search over cutoffs, no selection of a winning scenario.

## 14. Limitations

- COMBO_02 Path A closed trades only; not full live Path B.
- Sample sizes for several subgroups remain below 30 (INSUFFICIENT_SAMPLE).
- Direction asymmetry (LONG vs SHORT) confounds regime comparisons.
- 4h HTF often HTF_UNAVAILABLE in source rows → many NEUTRAL_OR_UNAVAILABLE.
- Scenarios C and D can coincide when NEUTRAL_STRUCTURE count is zero.
- No fees re-simulated; R values come from the existing diagnostic rows.
- Bootstrap CIs describe sampling variability; they do not validate filters.
- MULTIPLE_TESTING_RISK: four predefined scenarios are reported on the same closed-trade sample. Intervals and rates are descriptive only; do not treat any scenario as selected or validated by this study.

## Factual observations

- Analyzed n=219 existing closed COMBO_02 diagnostic trades.
- HTF_ALIGNED: n=64, losses=38/64, loss_rate=59.4%, loss_share=23.8% of all losers (38/160); SAMPLE_SIZE_OK.
- HTF_CONFLICT: n=88, losses=70/88, loss_rate=79.5%, loss_share=43.8% of all losers (70/160); SAMPLE_SIZE_OK.
- HTF_NEUTRAL_OR_UNAVAILABLE: n=67, losses=52/67, loss_rate=77.6%, loss_share=32.5% of all losers (52/160); SAMPLE_SIZE_OK.
- HTF_ALIGNED only: retained 64/219 (29.2% retention), mean_R=0.251, SAMPLE_SIZE_OK.
- HTF_ALIGNED + HTF_NEUTRAL_OR_UNAVAILABLE: retained 131/219 (59.8% retention), mean_R=-0.038, SAMPLE_SIZE_OK.
- CURRENT exclude HTF_CONFLICT: retained 131/219 (59.8% retention), mean_R=-0.038, SAMPLE_SIZE_OK.
- 15M LONG: 18 wins / 40 losses from 58 trades (SAMPLE_SIZE_OK).
- 15M SHORT: 8 wins / 43 losses from 51 trades (SAMPLE_SIZE_OK).
- 1H LONG: 19 wins / 30 losses from 49 trades (SAMPLE_SIZE_OK).
- 1H SHORT: 14 wins / 47 losses from 61 trades (SAMPLE_SIZE_OK).
- 5m: NO DATA (zero trades in current dataset).
- Direction asymmetry remains large; regime effects must not be attributed independently of LONG/SHORT mix.
- MULTIPLE_TESTING_RISK: four predefined scenarios are reported on the same closed-trade sample. Intervals and rates are descriptive only; do not treat any scenario as selected or validated by this study.

## Acceptance

- `live_engine_unchanged`: True
- `production_thresholds_unchanged`: True
- `existing_trades_unchanged`: True
- `no_future_information_used`: True
- `no_parameter_optimization`: True
- `no_new_sideways_detector`: True
- `no_fabricated_data`: True
- `no_trade_entry_recalculation`: True
- `all_sample_sizes_reported`: True
- `long_short_separated`: True
- `tf_15m_1h_separated`: True
