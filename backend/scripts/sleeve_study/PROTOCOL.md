# Sleeve study — protocol (written 2026-10-14, before any research-window result was computed)

Label: **current-universe retrospective**. Research only. Nothing under `backend/app` is edited.
COMBO_02_V1 (tag `v1-combo02-long-htf`), its paper watcher, REGIME_BOOK_OPEN, and the short research ids'
`paper_eligible` flags are not touched. A book that passes gets a **new** research id.

## Data and windows (Agent 1, user decisions 2026-10-14)
- Universe: 16 of the 27 `V1_SYMBOLS` whose 1d/4h/1h history covers the window: ADA APT ARB AVAX BNB BTC DOGE DOT
  ETH LINK LTC NEAR OP SOL SUI XRP. Excluded (history starts 2024-10-09): AAVE ATOM BCH ETC FIL ICP INJ RENDER TON TRX UNI.
- Per-timeframe gaps (coin dropped from strategies that need that timeframe): AVAX 1h (starts 2024-04-04);
  15m only APT BTC DOGE DOT ETH SOL cover the window.
- The study ends **2026-02-13** so that the final test does not overlap the published rank window
  (2026-02-14 → 2026-10-12, contaminated for every id in ranked.json). That window is used only as the known in-sample baseline.
- Research 2023-06-01 → 2025-03-31 (22 months). Validation 2025-04-01 → 2025-08-31 (5 months).
  Final test 2025-09-01 → 2026-02-13 (5 months + 13 days). Bars stored before 2023-06-01 are warmup only.
- Walk-forward folds inside research (reported, not a gate): F1 2023-06→2023-11, F2 2023-12→2024-05, F3 2024-06→2024-10, F4 2024-11→2025-03.

## Regimes (Agent 2)
`build_timeframe_feature_table` → `snapshot_at_index` → `classify_market_regime` per coin on 4h and 1d; BTC daily trend from
`passed_regime_backtest.RegimeClock` → `btc_daily_regime.daily_trend`. Fixed map: UPTREND = BULL_TREND, HIGH_VOLATILITY_TREND;
DOWNTREND = BEAR_TREND; RANGE = RANGE, HIGH_VOLATILITY_RANGE, LOW_VOLATILITY_COMPRESSION; CHOPPY/TRANSITION/UNKNOWN = no trade.
Label used for a signal = last 4h bar whose close ≤ the signal bar's open (15m/1h/4h setups); last 1d bar whose close ≤ the
signal bar's open (1d setups). BTC trend = trend over daily bars closed by 00:00 UTC of the signal bar's day.
Known flaw, kept because the map is fixed: the classifier emits HIGH_VOLATILITY_TREND for bear trends too, so those bars count as UPTREND.

## Families and candidate signal sources (Agents 3 and 4)
| Family | Regime | Sources (existing ids) |
|---|---|---|
| LONG_CONT | UPTREND | COMBO_01 / COMBO_02_LOCAL (one family, counted once), COMBO_02, COMBO_02_V2_1_A:TREND_FOLLOWING, SMALL_CAP_VOLUME_BOS, OSE_A/B/C/D (bos_with_trend), CANDLE12_V2 |
| LONG_RANGE | RANGE | LARGE_CAP_SWEEP_CHOCH, MID_CAP_FVG_DISCOUNT, COMBO_02_V2_1_A:RANGE_MEAN_REVERSION, COMBO_03_TRANSITION_B |
| SHORT_CONT | DOWNTREND | COMBO_01 / COMBO_02_LOCAL short, COMBO_02 short (= COMBO_02_SHORT_RESEARCH), COMBO_02_V2_1_A:TREND_FOLLOWING, SHORT_PULLBACK_REJECTION_RESEARCH, OSE_A/B/C/D, CANDLE12_V2 |
| SHORT_RANGE | RANGE | COMBO_02_V2_1_A:RANGE_MEAN_REVERSION, COMBO_03_TRANSITION_B |
Timeframes 15m, 1h, 4h, 1d where the source supports them. Not tested: COMBO_03/04/04_HTF(_SHORT)/05–08 and COMBO_03_TRANSITION A/C
(zero trades in the rank), SD family (zero trades), PATH_B (no per-trade output), grid books (not signal-shaped; GRID-A..C use the
same-hour regime = lookahead), scalp/intraday ids (5m/1m excluded). No new id is created for a rule an existing id already expresses.

## Variant grid (fixed now; every variant tested is recorded, pass or fail)
variant = (source id, side, timeframe, regime filter, exit pair)
- Regime filter RF1: coin bucket matches the family. RF2: RF1 and BTC daily trend aligned (LONG_CONT → BULLISH, SHORT_CONT → BEARISH, range families → NEUTRAL).
- Exit pairs (exactly three, written before research and carried unchanged into validation):
  X_NAT = strategy's own stop, strategy's own first target (OSE: its own 2R from fill);
  X_15R = own stop, 1.5R from the fill; X_3R = own stop, 3R from the fill. No time stop; positions open at window end are marked and closed.

## Research screen
Per variant, all eligible coins, one $1,000 sleeve per coin, 2% of sleeve equity risk, 2x cap, base costs, no account cap, no withdrawal.
Pass = net > 0, profit factor > 1.2, ≥ 30 trades on the research window. A pass is not a freeze.

## Validation selection (mechanical)
1. Run every screened variant on validation, same per-coin sleeves.
2. Eligible cell (variant, coin): validation net > 0 and the same coin's research net > 0.
3. Per (coin, family) keep the eligible cell with the highest validation net; COMBO_01 and COMBO_02_LOCAL count as one source family.
4. Coin score = sum of its kept cells' validation net. Take the top 10 coins with score > 0.
5. Run the full account simulation on validation (shared sleeves, conflict rule, $200 open-risk cap, month-end withdrawal) and report:
   base, 1.5× costs, 1% risk, funding sensitivity, best-coin share, best-month share, worst month.
No step uses final-test data. The freeze file is written after step 5, then the final test is run once.

## Account simulation (fixed by the brief)
$10,000 = 10 sleeves × $1,000; entry next bar open; one position per coin; higher TF wins same-bar conflicts, same TF → side matching
the regime, then source priority (higher validation net first); risk 2% of sleeve equity (column: 1%), open initial risk ≤ $200,
2x leverage cap; min notional $5 (repo default `paper_sizing._DEFAULT_MIN_NOTIONAL`; no per-symbol minimum is stored);
same bar stop+target → stop; open through stop → fill at open; all taker. Costs: taker 0.04%, maker 0.02% (unused), slippage 0.02%/side.
Funding: no stored settlement history covers the window (grid_funding_settlements has BTCUSDT from 2026-08-02 only) → sensitivity
column 0.01% of notional per 8h held, not used for selection. Withdrawal: at month end, marked equity above $10,000 is withdrawn
(from sleeves above $1,000 pro rata to their excess); losses carry; the next month starts at min(equity, $10,000).
