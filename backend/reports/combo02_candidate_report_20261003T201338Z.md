# COMBO_02 candidate eligibility research report

Generated: `2026-10-03T20:15:51.401261+00:00`

## Mandatory disclaimer

Research only. No symbol from this report was added to the frozen COMBO_02 v1 paper/live universe or Telegram eligibility list.

## Frozen strategy fingerprint

> COMBO_02 LONG, 1h setup, 1h+4h HTF_ALIGNED, Path A-equivalent, same fee/risk/SL/TP/swing/BOS logic as v1.

```json
{
  "text": "COMBO_02 LONG, 1h setup, 1h+4h HTF_ALIGNED, Path A-equivalent, same fee/risk/SL/TP/swing/BOS logic as v1.",
  "combination_id": "COMBO_02",
  "combo_require_htf": true,
  "direction": "LONG",
  "setup_timeframe": "1h",
  "window_start": "2025-01-01",
  "window_end": "2026-01-31",
  "oos_dev_end": "2025-06-30",
  "oos_val_start": "2025-07-01",
  "oos_val_end": "2026-01-31",
  "risk_usd_per_r": 20.0,
  "principal_usd": 1000.0,
  "taker_fee": 0.0004,
  "maker_fee": 0.0002,
  "research_engine_version": "bos_research_v2_tp1_min_rr",
  "path": "A",
  "no_path_b": true,
  "no_combo_02_local": true,
  "no_15m": true,
  "settings_identical_across_candidates": true
}
```

## Selection criteria and immutable candidate manifest

Manifest file: `reports\combo02_candidates_20261003T200737Z.json`

```json
{
  "quote_asset": "USDT",
  "market_type": "perpetual",
  "top_n": 10,
  "min_history_days": 500.0,
  "min_ohlcv_completeness": 0.99,
  "min_avg_24h_quote_volume_usd": 5000000.0,
  "exclude_stablecoins": true,
  "exclude_leveraged_tokens": true,
  "exclude_inactive_or_delisted": true,
  "exclude_nonstandard_symbols": true,
  "exclude_v1_universe": true,
  "setup_timeframe": "1h",
  "selector_version": "v1"
}
```

Candidates (5): XRPUSDT, SUIUSDT, BNBUSDT, DOGEUSDT, LINKUSDT

## Complete results table

| symbol | status | tier | n | win% | net avg R | PF | net PnL | maxDD R | lose streak | fees/gross | BTC overlap |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| XRPUSDT | COMPLETED | REJECT | 28 | 0.3214 | -0.0706 | 0.902 | -39.5583 | 7.3721 | 7 | 7.0031 | 0.0714 |
| SUIUSDT | COMPLETED | PROMISING | 22 | 0.4545 | 0.4203 | 1.743 | 184.9151 | 3.3391 | 3 | 0.0876 | 0.0 |
| BNBUSDT | COMPLETED | PROMISING | 36 | 0.4444 | 0.3376 | 1.5503 | 243.0943 | 3.9113 | 3 | 0.2694 | 0.0278 |
| DOGEUSDT | COMPLETED | REJECT | 27 | 0.2593 | -0.1674 | 0.7818 | -90.3874 | 9.417 | 8 | 0.3054 | 0.0 |
| LINKUSDT | COMPLETED | REJECT | 38 | 0.2632 | -0.2116 | 0.7296 | -160.7988 | 13.6919 | 11 | 0.3974 | 0.0263 |

## Tier pass/fail conditions

### XRPUSDT — `REJECT`

Reasons: `non_positive_net_expectancy_or_pnl; profit_factor_le_1; fees_consume>=70%_gross`

| condition | passed | detail |
|---|---|---|
| trades_ge_20 | True | trades=28 (need >=20) |
| net_avg_r_gt_0_25 | False | net_avg_r=-0.0706 (need >0.25) |
| net_pnl_gt_0 | False | net_pnl=-39.5583 |
| profit_factor_gt_1_25 | False | pf=0.9020 (need >1.25) |
| max_dd_le_6r | False | max_dd=7.3721R (need <=6.0R) |
| max_losing_streak_le_6 | False | streak=7 (need <=6) |
| fees_lt_70pct_gross | False | fee_share=7.0031 (need <0.7) |

### SUIUSDT — `PROMISING`

Reasons: `meets_promising_thresholds`

| condition | passed | detail |
|---|---|---|
| trades_ge_20 | True | trades=22 (need >=20) |
| net_avg_r_gt_0_25 | True | net_avg_r=0.4203 (need >0.25) |
| net_pnl_gt_0 | True | net_pnl=184.9151 |
| profit_factor_gt_1_25 | True | pf=1.7430 (need >1.25) |
| max_dd_le_6r | True | max_dd=3.3391R (need <=6.0R) |
| max_losing_streak_le_6 | True | streak=3 (need <=6) |
| fees_lt_70pct_gross | True | fee_share=0.0876 (need <0.7) |

### BNBUSDT — `PROMISING`

Reasons: `meets_promising_thresholds`

| condition | passed | detail |
|---|---|---|
| trades_ge_20 | True | trades=36 (need >=20) |
| net_avg_r_gt_0_25 | True | net_avg_r=0.3376 (need >0.25) |
| net_pnl_gt_0 | True | net_pnl=243.0943 |
| profit_factor_gt_1_25 | True | pf=1.5503 (need >1.25) |
| max_dd_le_6r | True | max_dd=3.9113R (need <=6.0R) |
| max_losing_streak_le_6 | True | streak=3 (need <=6) |
| fees_lt_70pct_gross | True | fee_share=0.2694 (need <0.7) |

### DOGEUSDT — `REJECT`

Reasons: `non_positive_net_expectancy_or_pnl; profit_factor_le_1`

| condition | passed | detail |
|---|---|---|
| trades_ge_20 | True | trades=27 (need >=20) |
| net_avg_r_gt_0_25 | False | net_avg_r=-0.1674 (need >0.25) |
| net_pnl_gt_0 | False | net_pnl=-90.3874 |
| profit_factor_gt_1_25 | False | pf=0.7818 (need >1.25) |
| max_dd_le_6r | False | max_dd=9.4170R (need <=6.0R) |
| max_losing_streak_le_6 | False | streak=8 (need <=6) |
| fees_lt_70pct_gross | True | fee_share=0.3054 (need <0.7) |

### LINKUSDT — `REJECT`

Reasons: `non_positive_net_expectancy_or_pnl; profit_factor_le_1; max_dd=13.69R > 10.0R; lose_streak=11 > 10`

| condition | passed | detail |
|---|---|---|
| trades_ge_20 | True | trades=38 (need >=20) |
| net_avg_r_gt_0_25 | False | net_avg_r=-0.2116 (need >0.25) |
| net_pnl_gt_0 | False | net_pnl=-160.7988 |
| profit_factor_gt_1_25 | False | pf=0.7296 (need >1.25) |
| max_dd_le_6r | False | max_dd=13.6919R (need <=6.0R) |
| max_losing_streak_le_6 | False | streak=11 (need <=6) |
| fees_lt_70pct_gross | True | fee_share=0.3974 (need <0.7) |


## Out-of-sample (PROMISING only)

| symbol | OOS n | net avg R | net PnL | PF | maxDD | lose streak | label |
|---|---:|---:|---:|---:|---:|---:|---|
| SUIUSDT | 13 | -0.0919 | -23.8881 | 0.8724 | 3.288 | 3 | PROMISING_NEEDS_MORE_EVIDENCE |
| BNBUSDT | 19 | 0.3712 | 141.0632 | 1.6317 | 3.3348 | 3 | V2_PAPER_CANDIDATE |

## Portfolio overlap / concurrency

### SUIUSDT

- Base result: `PROMISING`
- OOS result: `PROMISING_NEEDS_MORE_EVIDENCE`
- BTC overlap: 0% of entries
- Peak concurrent positions: `4`
- Incremental portfolio DD: `-2.6478R`
- Recommendation: **NEEDS_MORE_EVIDENCE** — Promising in-sample; OOS/portfolio evidence incomplete.

### BNBUSDT

- Base result: `PROMISING`
- OOS result: `V2_PAPER_CANDIDATE`
- BTC overlap: 3% of entries
- Peak concurrent positions: `4`
- Incremental portfolio DD: `-2.542R`
- Recommendation: **CORRELATED** — Do not add without a portfolio exposure cap — entries largely duplicate BTC/ETH/SOL risk.


## Final lists

- **V2_PAPER_CANDIDATE**: BNBUSDT
- **PROMISING_NEEDS_MORE_EVIDENCE**: SUIUSDT
- **WATCHLIST**: none
- **REJECT**: XRPUSDT, DOGEUSDT, LINKUSDT
- **INSUFFICIENT_DATA**: none

## Hard boundary confirmation

- Frozen v1 remains BTC/ETH/SOL only.
- No screener candidate was automatically promoted, paper-traded, or made Telegram eligible.

Research only. No symbol from this report was added to the frozen COMBO_02 v1 paper/live universe or Telegram eligibility list.
