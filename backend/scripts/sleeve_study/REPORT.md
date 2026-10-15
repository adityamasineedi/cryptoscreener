# Sleeve study report — current-universe retrospective

Written 2026-10-14. Protocol: PROTOCOL.md. Freeze: FREEZE.json (sha256 1b75a2f8d5d30d16…). Final test run once: out/final_result.json.

## Result

Final test 2025-09-01 → 2026-02-13 (166 days): **-91.15 USD**, **-16.47 USD per 30 days**. Objective $1,000 per 30-day month: **gap 1016.47 USD per month**. Months ≥ $1,000: 0 of 5 full months. Stretch ($1,500/month): gap 1516.47.

Status: final-test net ≤ 0, so under the rule written in FREEZE.json before the test the SLV1 ids stay **RESEARCH** (not FROZEN_FOR_PAPER). COMBO_02_V2_1_A stays RESEARCH. Nothing was promoted.

## Final-test month table (USD)

| month | base_2pct | risk_1pct | costs_1.5x | funding_0.01pct_8h |
|---|---|---|---|---|
| 2025-09 | -62.51 | -31.25 | -63.74 | -63.23 |
| 2025-10 | +37.28 | +18.96 | +36.54 | +35.04 |
| 2025-11 | -40.62 | -20.42 | -41.13 | -40.88 |
| 2025-12 | -19.86 | -10.03 | -19.98 | -20.84 |
| 2026-01 | -5.45 | -2.15 | -6.97 | -8.12 |
| 2026-02 (13 days) | +0.00 | +0.00 | +0.00 | +0.00 |
| **total** | -91.15 | -44.9 | -95.29 | -98.03 |
| **avg per 30 days** | -16.47 | -8.11 | -17.22 | -17.72 |
| **median day** | 0.0 | 0.0 | 0.0 | 0.0 |
| **worst month** | -62.51 | -31.25 | -63.74 | -63.23 |
| **max drawdown (marked)** | 131.42 | 65.59 | 133.56 | 135.39 |
| **trades** | 12 | 12 | 12 | 12 |
| **withdrawn** | 0.0 | 0.0 | 0.0 | 0.0 |

## Validation table that justified the freeze (2025-04-01 → 2025-08-31, in-sample for selection)

| new id | source | coin | tf | regime filter | exit | validation net | validation trades | research net (coin) |
|---|---|---|---|---|---|---|---|---|
| SLV1_LC_COMBO_01_4h_RF2_X_3R | COMBO_01 | ETHUSDT | 4h | RF2 | X_3R | +189.30 | 3 | +64.21 |
| SLV1_LC_COMBO_01_4h_RF2_X_3R | COMBO_01 | BNBUSDT | 4h | RF2 | X_3R | +187.72 | 3 | +217.16 |
| SLV1_LC_OSE_B_acceptance_1h_RF2_X_3R | OSE_B_acceptance | DOGEUSDT | 1h | RF2 | X_3R | +183.62 | 7 | +77.54 |
| SLV1_LC_OSE_A_corrected_baseline_1h_RF2_X_3R | OSE_A_corrected_baseline | SOLUSDT | 1h | RF2 | X_3R | +141.16 | 5 | +177.42 |
| SLV1_LC_OSE_A_corrected_baseline_1h_RF2_X_3R | OSE_A_corrected_baseline | ADAUSDT | 1h | RF2 | X_3R | +140.93 | 5 | +288.38 |
| SLV1_LC_COMBO_01_4h_RF1_X_3R | COMBO_01 | BTCUSDT | 4h | RF1 | X_3R | +100.78 | 6 | +53.18 |
| SLV1_LC_OSE_A_corrected_baseline_4h_RF2_X_3R | OSE_A_corrected_baseline | XRPUSDT | 4h | RF2 | X_3R | +100.26 | 3 | +79.61 |
| SLV1_LC_COMBO_01_4h_RF1_X_3R | COMBO_01 | NEARUSDT | 4h | RF1 | X_3R | +99.39 | 3 | +168.67 |
| SLV1_LC_OSE_A_corrected_baseline_1h_RF2_X_3R | OSE_A_corrected_baseline | LINKUSDT | 1h | RF2 | X_3R | +71.87 | 8 | +162.59 |
| SLV1_LC_COMBO_02_LOCAL_4h_RF2_X_NAT | COMBO_02_LOCAL | ARBUSDT | 4h | RF2 | X_NAT | +59.96 | 4 | +14.02 |

Account simulation on validation (selected book):

| month | base_2pct | risk_1pct | costs_1.5x | funding_0.01pct_8h |
|---|---|---|---|---|
| 2025-04 | +123.85 | +66.78 | +121.15 | +123.02 |
| 2025-05 | +219.24 | +109.94 | +216.59 | +214.44 |
| 2025-06 | -27.70 | -13.73 | -28.13 | -29.75 |
| 2025-07 | +954.46 | +471.79 | +945.24 | +941.24 |
| 2025-08 | -5.88 | -3.36 | -6.41 | -8.73 |
| **total** | 1263.97 | 631.42 | 1248.44 | 1240.22 |
| **avg per 30 days** | 247.84 | 123.81 | 244.79 | 243.18 |
| **worst month** | -27.7 | -13.73 | -28.13 | -29.75 |
| **max drawdown** | 217.98 | 107.53 | 218.96 | 222.84 |
| **best-coin share** | 0.15 | 0.146 | 0.151 | 0.15 |
| **best-month share** | 0.755 | 0.747 | 0.757 | 0.759 |
| **trades** | 47 | 47 | 47 | 47 |

Best coin ETHUSDT, best month 2025-07, worst month 2025-06.

## Research screen (2023-06-01 → 2025-03-31)

| family | variants | net > 0 | pass (net>0, PF>1.2, ≥30 trades) |
|---|---|---|---|
| LONG_CONT | 186 | 76 | 30 |
| LONG_RANGE | 54 | 7 | 0 |
| SHORT_CONT | 180 | 32 | 3 |
| SHORT_RANGE | 30 | 9 | 0 |

All 450 variants with folds: out/research_screen.md. Validation of the 33 screened variants: out/validation.json (`screened_variants`).

## Notes

- Baseline, contaminated window 2026-02-14 → 2026-10-12 (published rank, not a test): best long book COMBO_01 1h +$1,035.92 over 240 days on ten separate $1,000 accounts (~$129/month), LEGACY_SIGNAL_CLOSE, no slippage; CANDLE12_V2 1h +$525.68 with 0.02% slippage; no 10-coin short pass; grid best ~+$237; funding not in those nets.

- Fixed regime map flaw: HIGH_VOLATILITY_TREND is emitted for bear trends too; under the map 18.0% of 4h UPTREND coin-days (3.3% on 1d) were high-vol bear trends.

- Short rule from the brief (validation net must stand on its own) was omitted from PROTOCOL.md and applied after the first validation pass, before the freeze. It removed all three screened short variants (validation nets −486, −73, −118). First-pass book with those short cells: +$1,474 on validation; out/validation_pass1_before_short_rule.json.

- Engine gating: combo and CANDLE12 signal lists come from position-gated walks with the engines' own signal-close exits; the simulator re-gates with its own fills. OSE, multi-cap, SPR signals are ungated.

- Not tested: range families never reached 30 research trades with net > 0; COMBO_03/04/05–08, COMBO_03_TRANSITION A/C, SD family (zero trades in the rank), PATH_B (no per-trade output), grid books (not signal-shaped; same-hour regime lookahead), V2 REVERSAL playbook (counter-trend, in no family), 5m/1m.

