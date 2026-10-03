# Multi-Cap Strategy Research — Available Historical Cap Window

**Descriptive baseline only. No strategy ranking. No optimization.**

> This is limited by currently available historical market-cap coverage. This is **not** multi-year cap-regime research.

## Dataset
- Label: `AVAILABLE_HISTORICAL_CAP_WINDOW`
- Cap history start: `2025-10-04T00:00:00+00:00`
- Cap history end: `2026-10-03T13:34:00+00:00`
- Symbols with cap history: **66** / 528
- Symbols without cap history: **462**
- Cap observations: **23517**

## Cap-group counts (eligible symbols)
- LARGE_CAP: 8 — BNBUSDT, DOGEUSDT, LINKUSDT, SOLUSDT, SUIUSDT, TRXUSDT, XMRUSDT, XRPUSDT
- MID_CAP: 8 — AAVEUSDT, LINKUSDT, RENDERUSDT, SKYUSDT, SUIUSDT, VETUSDT, WLFIUSDT, XMRUSDT
- SMALL_CAP: 38 — ALTUSDT, ARKMUSDT, BLURUSDT, CFXUSDT, CHIPUSDT, DEXEUSDT, DYDXUSDT, ESPUSDT, FLOWUSDT, FORMUSDT, GRASSUSDT, IDUSDT, KAITOUSDT, KAVAUSDT, KSMUSDT, LPTUSDT, MUBARAKUSDT, NEOUSDT, ONTUSDT, OPUSDT, PENDLEUSDT, PLUMEUSDT, PYTHUSDT, RAVEUSDT, RENDERUSDT, ROSEUSDT, RUNEUSDT, SEIUSDT, SNXUSDT, SPKUSDT, STRKUSDT, STXUSDT, SUSHIUSDT, TUSDT, USELESSUSDT, VETUSDT, WUSDT, XTZUSDT

## Funnel totals
- cells_run: 156
- cells_error: 0
- bars_processed: 1413583
- signals_detected: 56178
- cap_eligible_signals: 17002
- cross_cap_signal_count: 6791
- candidates: 23906
- entries: 17002
- closed_trades: 9651
- open_trades: 26
- LONG: 9651
- SHORT: 0

## Strategy rollup (descriptive)

| strategy | trades | win_rate | mean_R | net_R | TRAIN | VAL | OOS | sample |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| LARGE_CAP_SWEEP_CHOCH | 2884 | 0.32558945908460474 | -0.023231622746185843 | -572.096065217104 | 1731 | 554 | 599 | MORE_RELIABLE_DESCRIPTIVE_SAMPLE |
| MID_CAP_FVG_DISCOUNT | 6207 | 0.34009988722410184 | 0.020299661672305448 | -952.5937788627549 | 3990 | 1284 | 933 | MORE_RELIABLE_DESCRIPTIVE_SAMPLE |
| SMALL_CAP_VOLUME_BOS | 560 | 0.2982142857142857 | -0.10535714285714284 | -120.50044952871912 | 304 | 105 | 151 | MORE_RELIABLE_DESCRIPTIVE_SAMPLE |

## Direction
- LONG: trades=9651 win_rate=0.3333333333333333 mean_R=-5.0386248553084505e-18 (MORE_RELIABLE_DESCRIPTIVE_SAMPLE)
- SHORT: trades=0 win_rate=None mean_R=None (VERY_SMALL)

## TRAIN / VALIDATION / OOS
- TRAIN: trades=6025 win_rate=0.3278008298755187 mean_R=-0.016597510373443987 (MORE_RELIABLE_DESCRIPTIVE_SAMPLE)
- VALIDATION: trades=1943 win_rate=0.33247555326814204 mean_R=-0.002573340195573876 (MORE_RELIABLE_DESCRIPTIVE_SAMPLE)
- OOS: trades=1683 win_rate=0.3541295306001188 mean_R=0.06238859180035652 (MORE_RELIABLE_DESCRIPTIVE_SAMPLE)

## Fees / slippage (unchanged config)
- taker_fee: 0.0004
- maker_fee: 0.0002
- slippage_rate: 0.0002
- risk_usd: 100.0

## Sample-size warnings
Labels describe sample size only — not strategy quality: VERY_SMALL (<10), SMALL (10–29), DESCRIPTIVE (30–99), MORE_RELIABLE_DESCRIPTIVE_SAMPLE (≥100).

## Data-quality / regime
- HTF aligned / conflict / trend / chop: `REGIME_NOT_AVAILABLE` (no new trading filters created).
- Errors: 0
- Excluded cells: 1437 (reason `CAP_GROUP_UNAVAILABLE` — no silent fabrication).
- 17 symbols have historical cap observations but never classified into LARGE/MID/SMALL during an OHLCV∩cap window (includes BTC/ETH taxonomy and below-threshold / unavailable).
- OHLCV coverage is uneven: many 15m/1h cells have only days of overlap while some 5m cells span ~Jan–Oct 2026. Each cell reports `requested_*` vs `actual_*`.
- Warmup bars may make `actual_start` earlier than `requested_start`; evaluation uses loaded series without inventing OHLCV.

## Validation
- no_synthetic_ohlcv: True
- no_synthetic_market_cap: True
- no_future_market_cap_classification: True
- no_future_ohlcv: True
- no_strategy_parameter_changes: True
- production_signals_unchanged: True
- production_execution_unchanged: True
- trade_plan_unchanged: True
- exclusions_visible: True
- errors_visible: True
- train_validation_oos_chronological: True
- fees_slippage_applied: True
- dataset_label: AVAILABLE_HISTORICAL_CAP_WINDOW
- limitation: This is limited by currently available historical market-cap coverage.

## Files
- `dataset_manifest.json`
- `exclusions.csv`
- `oos_results.csv`
- `run_log.txt`
- `run_manifest.json`
- `strategy_by_cap.csv`
- `strategy_by_direction.csv`
- `strategy_by_month.csv`
- `strategy_by_period.csv`
- `strategy_by_symbol.csv`
- `strategy_by_timeframe.csv`
- `strategy_funnel.csv`
- `strategy_summary.csv`
