
---

## Phase 2b — Pre-registration (written 2026-10-15, before any Phase 2b result was computed)

Cap: 2 h of compute for both tracks. Only one Phase 2b step ran before this text was written: `research/p2b_cells.py` (5 s). It logged the 60 regime × direction subsets already seen in Phase 4 to `research/experiments.csv`, re-using the Phase 3 development trades. No validation or OOS bar has been read.

### Rules common to both tracks

- **Regime classifier (causal):**
  - z_d = ln(C_d / C_{d−30}) / (sd(daily log returns over d−89…d) × √30). C_d is the coin's own UTC daily close, rebuilt from 4h bars.
  - "side" = |z_d| < 1, "bull" = z_d ≥ 1, "bear" = z_d ≤ −1. Same as Phase 1.
  - A signal at time s uses the label of the last UTC day that **closed at or before s**.
  - Code: `structure.daily_z`; labels are assigned by `p4_compare.regime_labels`.
- **Validation and OOS universe:** core + Tier 2, 14 coins pooled. Reason, from development counts only: the core alone would give about 37 (H2), 70 (H8) and 47 (H5) validation trades, below the 100-trade floor by construction. Tier 2 is the designated cross-coin validation set, and it has 15m data throughout validation.
- **Validation gates** (one run, nothing re-run):
  1. At least 100 trades.
  2. Net expectancy ≥ +0.10R.
  3. 95% weekly-block bootstrap lower bound > 0.
  4. DSR ≥ 0.90.
- **OOS** (exactly one run, same universe, only for validation passers): net expectancy > 0 and profit factor ≥ 1.1 (the Phase 6 rule).

### Track A — regime subsets: one validation run each, never re-evaluated on development

Selection rule, fixed now: among the 60 already-seen cells, take those with at least 100 development trades, ranked by development net R, top 3.

| ID | Subset | Development evidence (already seen) |
|---|---|---|
| A1 | H2 (DC BOS + retest), **longs only**, regime = side at signal | +0.176R, 122 trades |
| A2 | H8 (volume expansion), **shorts only**, regime = side at signal | +0.152R, 229 trades |
| A3 | H5 (compression breakout), **shorts only**, regime = side at signal | +0.099R, 153 trades |

- Entries, stops, targets and holding times are exactly as in Phase 2. The subset only discards setups whose side or regime doesn't match.
- **DSR for Track A:** N = 70, which is the 10 Phase 3 trials plus all 60 regime × direction cells looked at. The variance of per-trade Sharpe comes from those 70 development rows in `experiments.csv`.

### Track B — new hypotheses aimed at the cost ratio (development segment, core coins)

Phase 4 gates apply unchanged, on development core pooled:
1. At least 100 trades.
2. Net expectancy ≥ +0.10R.
3. 95% bootstrap lower bound > 0.
4. At least 2 of 3 core coins positive.
5. Beats the random null at p < 0.05.
6. DSR ≥ 0.90, with N = every distinct configuration in `experiments.csv` at the time of the gate. That is 70 + 3 Track A + 12 Track B = 85.

Passers go to validation, then OOS, each read once (gates above). 12 trials:

**B1 — time-series momentum** (6 trials: timeframe ∈ {1d, 4h} × N ∈ {20, 40, 60} bars). Code: `research/tsmom.py`.
- **Signal:** at each bar close, sign of C_t / C_{t−N} − 1.
- **Position:** always in the market, long when the sign is positive and short when negative. On a sign change, exit and reverse at the next bar's open. Each holding period is one trade.
- **Sizing (volatility target):**
  - Notional per coin at entry = E × 0.10 / σ_ann. E = realised book equity at entry ($10k start); σ_ann = annualised standard deviation of the last 60 bar log returns.
  - Capped at 2×E per coin. Notional is fixed until the trade ends (no rebalancing).
- **Costs:** taker 0.05% + slippage 0.03% on every entry and exit; funding 0.01%/8h of notional, both sides.
- **R unit** (TSMOM has no stop): 1R = notional × 2 × ATR(14)/price of the signal timeframe at entry, i.e. the loss a 2-ATR stop would have cost. R = net PnL ÷ that amount.
- **Segment end:** open trades are closed at the last segment bar's close.
- **Baselines:**
  - Buy and hold per coin.
  - Random null: the same coins, the same holding-duration distribution, a random side and a random start time, with the same sizing and R unit; 300 draws.

**B2 — H5 and H8 with longer holds** (4 trials: {H5, H8} × target {3R, 5R}).
- Entries are identical to Phase 2.
- Stop = signal close ∓ **1.5×ATR(14, 4h)**. 1.5 is H5's median Phase 2 stop. For H8, ATR4 is taken from the last 4h bar closed by the signal.
- Target 3R or 5R. Maximum duration 10 days (240 h). Execution timeframes are unchanged (H5 1h, H8 15m).
- Random null as in Phase 3.

**B3 — maker entry for H5 and H8** (2 trials).
- Setups, stops, targets and holding times are identical to Phase 2.
- **Entry:** a limit order at the setup bar's close price. It is cancelled if not filled within **2 setup-timeframe bars**: 8 h for H5, 2 h for H8.
- **Fill rule:** fills only when an execution bar trades **strictly through** the limit (long: low < limit), at min(open, limit) for longs and max(open, limit) for shorts. No entry slippage.
- **Fees:** entry fee **0.02%**. Exits stay taker 0.05% + 0.03% slippage. Funding as before.
- If the fill bar also reaches the stop, the trade counts as stopped.
- Fill rate is reported. Random null as in Phase 3, with market entries, which is conservative for the null.
