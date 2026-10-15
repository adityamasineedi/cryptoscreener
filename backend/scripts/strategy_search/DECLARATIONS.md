# Strategy search — declarations written before any variant was run (2026-10-14)

## Windows
- Discovery (in-sample, tuning allowed): 16 coins ADA APT ARB AVAX BNB BTC DOGE DOT ETH LINK LTC NEAR OP SOL SUI XRP,
  2023-06-01 → 2026-10-08. Bars before 2023-06-01 are indicator warmup only.
- Holdout (same-period coin test, run once after the freeze): AAVE ATOM BCH ETC FIL ICP INJ RENDER TON TRX UNI,
  2024-10-09 → 2026-10-08. These coins have no stored bars before 2024-10-09, so indicator and regime warmup is taken
  from inside the holdout window (EMA200 on 1d needs ~200 days; 4h regime labels need ~33 days). Signals before
  warmup completes simply do not exist. TRX was in the published rank (2026-02-14 → 2026-10-12); noted, kept.
- Folds (chronological, month aligned): F1 2023-06→2024-03, F2 2024-04→2025-01, F3 2025-02→2025-11, F4 2025-12→2026-10-08.
- BTC daily trend days per fold (btc_daily_regime.daily_trend via RegimeClock):
  F1 BEARISH 58 / NEUTRAL 107 / BULLISH 140; F2 68/110/128; F3 **122**/97/84; F4 77/112/123.
- Falling fold: F3 has the most BEARISH days (40%, the most common label but not a majority). Because "mostly bearish"
  is ambiguous, a candidate must be profitable on BOTH F3 and the brief's fallback window 2025-09-01 → 2026-02-13.

## Regimes (research copy, classifiers unchanged)
UPTREND = BULL_TREND + HIGH_VOLATILITY_TREND with reason CLASSIFIED_HIGH_VOL_BULL_TREND.
DOWNTREND = BEAR_TREND + HIGH_VOLATILITY_TREND with reason CLASSIFIED_HIGH_VOL_BEAR_TREND.
RANGE = RANGE, HIGH_VOLATILITY_RANGE, LOW_VOLATILITY_COMPRESSION. CHOPPY, TRANSITION, UNKNOWN = no trade (no new rule defines them).
Label = last 4h bar closed by the signal bar's open (15m/1h/4h setups); last 1d bar for 1d setups.
Regime filter modes: TREND (long needs UPTREND, short needs DOWNTREND), RANGE (both sides need RANGE),
ANY (UPTREND, DOWNTREND or RANGE; never a no-trade label). Optional BTC filter: long needs BTC daily trend ≠ BEARISH,
short needs ≠ BULLISH (trend over daily bars closed by 00:00 UTC of the signal day).

## Accounting (sleeve_study/sim.py, extended only with optional trail / time stop / no fixed target)
Signal at close, entry next bar open; taker 0.04%/side; slippage 0.02%/side (columns 0.05%, 0.10%); 1.5× taker and
slippage column; funding column 0.01% of notional per 8h (not used to pick). Same bar stop+target → stop; open through
stop → open. One position per coin; one $1,000 sleeve per coin; all coins in a universe run as one account.
Month end: equity above N × $1,000 withdrawn (N = coins in the universe); losses carry. No account open-risk cap.
Dollars per 30 days are reported per $10,000: universe net × 10 / N / days × 30. Raw totals are also reported.
Leverage cap 3x for every risk level. No pyramiding is used.

## Risk choice (rule fixed now)
Risk levels tested: 0.5%, 1%, 1.5%, 2%, 5% of sleeve equity. For each candidate rule the frozen risk is the highest
level at which every candidate gate passes AND discovery max drawdown of marked account equity is ≤ 25% of the
account base. The freeze file records that one level; the holdout uses only it.

## Candidate gates (all at the frozen risk, discovery)
≥150 trades; profit in ≥3 of 4 folds; profit in F3 and in 2025-09-01→2026-02-13; profit at 1.5× taker and slippage;
profit with each numeric stop / target / trail multiple at ×0.8 and ×1.2 (one at a time); best coin ≤ 35% of total
profit; best calendar month ≤ 30% of total profit.
Ranking: discovery dollars per 30 days (per $10k) at the frozen risk. Top 3 distinct rules go to the freeze.

## Search grid (stage 1, at 2% risk)
Families DON, PB, BRT, SWP, BB, RSI2, SQZ (strategies.py), two parameter values each; timeframes 15m, 1h, 4h, 1d;
side LONG, SHORT, or BOTH (BOTH with TREND = regime switching: long in UPTREND, short in DOWNTREND; BOTH is
offered to trend families only); regime TREND or ANY for trend families, RANGE or ANY for reversion families; BTC filter off/on;
stop 1.5 or 2.5 ATR; exits: trend families R2, R3, TRAIL2 (2 ATR, no target), TRAIL3; reversion families R1, R2, R1+T
(1R or time stop after 12 setup bars). Every run is logged to out/variants.csv. 5m and 1m are not used.

## Added 2026-10-14 before any grid result was read
- Engine sources added to the grid: ENG_C01 (COMBO_01 at 1h/4h/1d), ENG_OSEA/OSEB/OSED (OSE A/B/D at 1h/4h), signals
  from sleeve_study/a34_signals.py through 2026-10-08. Their own structural stop distance is kept (stop_k 1.0 = native,
  scaled ×0.8 / ×1.2 for the gate); exits R2, R3, TRAIL2, TRAIL3; same sides, regime modes and BTC filter as trend families.
- Top 3 must be distinct in (family, timeframe, side); within a tie group the higher discovery $/30d wins.
- If fewer than 3 rules pass every gate, the freeze holds only the passing rules. If none pass, the freeze holds the top 3
  by discovery $/30d (distinct as above) among variants with ≥150 trades and net > 0 at 2% risk, each labelled
  NOT_A_CANDIDATE with its failed gates and judged at 2% risk; the holdout is then run for information only.
