# LARGE_CAP_SWEEP_CHOCH 1h LONG — validation protocol

Written 2026-10-15, after reproducing the Backtest-screen numbers. No split, walk-forward, regime or Monte Carlo
result had been computed when this file was written. Research only. Nothing under `backend/app` is changed.

## Contamination statement (read first)
Every stored bar of BTCUSDT and ETHUSDT 1h (2022-10-16 → 2026-10-14) has already been seen for this strategy. The
Backtest screen ran it over 2023-01-01 → 2026-10-15, the 2026-10-15 year-switch study ran it per calendar year on
7 coins, and LCSC/ETH was picked out of a multi-strategy, multi-coin screen because it looked good. The chronological
"validation" and "holdout" below are therefore **pseudo-out-of-sample**: they are untouched by parameter tuning in
this study, but not untouched by the researcher. They can falsify the edge; they cannot confirm it.
The only clean holdout is bars opening on or after **2026-10-16 00:00 UTC** (paper forward).

## Frozen v1 rules (MultiCapResearchConfig defaults, engine `multi_cap_strategies_v1`)
- Sweep: bar low < min(low of the prior 48 bars, current bar excluded) and close > that level. No entry on the sweep bar.
- CHOCH: first close above the latest confirmed swing high (left 3 / right 3) formed after the sweep. One CHOCH per sweep.
- Stop: the lower of entry − 1.5 × ATR(14) and the sweep level. Target: 2R. The first target touched exits the whole position.
- One open trade at a time. Same-bar SL + TP → SL.
- Sizing: $20 risk, $1,000 equity, 2× notional cap (qty = min(20 / (entry − stop), 2000 / entry)).

## Data and windows (UTC, candle-open times, start inclusive / end exclusive)
- Bars 2022-10-16 15:00 → 2026-10-14 23:00, 35,025 per symbol, no gaps. Bars before 2023-01-01 are warm-up only.
- Evaluation range 2023-01-01 → 2026-10-15 (1,383 days), assigned by signal-bar open time:
  - Train / development (60%): 2023-01-01 → 2025-04-10
  - Validation (20%): 2025-04-10 → 2026-01-11
  - Holdout (20%): 2026-01-11 → 2026-10-15
- A trade belongs to the window that contains its signal bar. Its exit may fall in the next window, as it would live.
  For a parameter selection on a training window, the walk is cut at the window end and open trades are closed at
  the last close (no outcome from after the window is used).

## Cost models
| Label | Entry | Stop exit | Target exit | Fees | Funding |
|---|---|---|---|---|---|
| GROSS | signal-bar close | stop level | target level | none | none |
| AFTER_FEE | signal-bar close | stop level | target level | taker 0.04% both sides | none |
| FULL (base) | next-bar open + 5 bps | stop − 10 bps; a bar opening below the stop exits at open − 10 bps | target level (limit) | taker 0.04% both sides | Binance settlements |
| FULL_STRESS | next-bar open + 10 bps | stop − 20 bps (gap: open − 20 bps) | target level | taker 0.05% both sides | Binance settlements |

- Funding: a long pays `rate × qty × mark` at every settlement t with fill time < t ≤ exit-bar open (paid when the
  rate is positive, received when negative). Mark = Binance markPrice when given, else the open of the bar at t.
- R unit = planned risk in USD = qty × (signal close − stop). R = net USD / planned risk.
- The trade list (which signals are taken) is the same in every cost model; exit bars do not change.

## Parameter neighbourhood (fixed now)
- Walk-forward grid (27 configs): min_rr {1.5, 2.0, 2.5} × atr_multiplier {1.0, 1.5, 2.0} × sweep lookback {24, 48, 72}.
- One-at-a-time sensitivity (train window only, then full range for reference): the three above plus swing
  left/right {2, 3, 4}.
- Fragile parameter: v1 is profitable (FULL) and a one-step neighbour is not, or the neighbour's expectancy is below
  half of v1's.

## Walk-forward
- Rolling 12-month train, 3-month test, step 3 months. Test windows 2024-01-01 → 2026-01-11 (last one stretched to
  the validation end). Selection uses FULL costs on the train window only.
- Selection rule: score = mean FULL expectancy of the config and its one-step grid neighbours; configs with < 30
  train trades are ineligible; highest score wins; ties go to v1.
- Holdout: one config chosen by the same rule on 2025-01-11 → 2026-01-11, applied once to the holdout. v1 frozen is
  run on the holdout too. The holdout is not used for any choice.
- Reported: v1 frozen (primary) and walk-forward re-optimised (secondary) for train, validation, holdout, and
  combined OOS (validation + holdout for v1; walk-forward tests + holdout for re-optimised).

## Regimes (per 1h bar, computed from closed bars only; a trade gets its signal bar's label)
- Trend lens: fast EMA(200), slow EMA(800) of 1h close. Trend-up: close > fast > slow. Trend-down: close < fast < slow.
  Otherwise Range/chop.
- Volatility lens: ATR(14)/close, percentile rank over the trailing 2,160 bars (90 days). ≥ 0.80 High-vol,
  ≤ 0.20 Low-vol, else Normal-vol.

## Formulas
- Expectancy (R) = Σ R_i / N. Profit factor = Σ positive net USD / |Σ negative net USD|.
- Drawdown: equity E_k = Σ_{i≤k} net_i (closed trades, in trade-exit order); max DD = max_k (max_{j≤k} E_j − E_k).
  The same formula on cumulative R gives DD in R. Monte Carlo account DD uses compounding E_k = E_{k−1}(1 + 0.02 R_k),
  DD% = max_k (1 − E_k / max_{j≤k} E_j).
- Payoff = mean winning R / |mean losing R|. Fee drag = fees / gross profit (gross net USD before any cost).

## Monte Carlo
10,000 shuffles of the observed FULL R list, 2% risk per trade compounding from $1,000. Because the product of
(1 + 0.02 R) does not depend on order, shuffles cannot move final equity; 10,000 bootstrap resamples (with
replacement, same N) are reported alongside for final-equity spread. Both report DD percentiles and
P(DD ≥ 20%), P(DD ≥ 30%).

## Stop-reversal short
Re-priced on the v1 long stop-outs only to document why it is rejected: short entry at the long stop (stop-market,
−10 bps), stop at the long entry (+10 bps), target 1R or mirrored 2R (limit), taker fees, funding. Two walks: the
screen's (starts on the bar after the stop) and the conservative one (the stop bar counts: if its high reaches the
short stop, the short is stopped). No independent short system is built in this study; it would be a new thesis
and needs its own protocol.

## Gates (user's promotion criteria, applied to combined OOS with FULL costs, v1 frozen)
≥ 100 OOS trades (or a stated lower threshold with caveats); expectancy ≥ 0.10R; PF ≥ 1.30; max DD ≤ 15R; total
stays positive without the top 3 trades; positive or near-neutral (≥ −0.05R) in ≥ 2 of 3 trend regimes; no fragile
parameter. PASS = all gates; WATCH = positive FULL expectancy but a gate fails; FAIL = non-positive FULL expectancy,
PF < 1.0, fees > 50% of gross profit, or profit from one regime only. Whatever the verdict, no live deployment
follows from this study, and PAPER_TRADE would need the 2026-10-16 forward window.
