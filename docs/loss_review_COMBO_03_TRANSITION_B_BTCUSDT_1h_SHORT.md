# Loss review — COMBO_03_TRANSITION_B · BTCUSDT · 1h · SHORT

Research only. This is a reading of the finished result; nothing in strategy code, stops, targets, fees or filters was changed.

## Pre-run summary

| Field | Value |
|---|---|
| Strategy | COMBO_03_TRANSITION_B (CHOPPY_TRANSITION_BREAKOUT_NO_15M), version v1 |
| Direction / symbol / setup TF | SHORT / BTCUSDT / 1h (HTF 1h + 4h) |
| Period | Calendar 2026-02-18 → 2026-10-16 (candles 2026-02-18 00:00 → 2026-10-16 18:00 UTC, 5,779 bars evaluated after 100 warm-up) |
| Principal / risk / leverage | $1,000 / 2% = $20 / 2× |
| Fees | taker 0.04%, maker 0.02% (all entries MARKET → taker both legs) |
| Status | Research (research_only = true, production_comparable = false) |
| Blotter | Standard trade blotter (execution model LEGACY_SIGNAL_CLOSE, not causal); no optimistic-comparison block applies |

Source: the screen's job was gone from the backend (status idle after a reload), so the cell was re-run unchanged on the normal backend (job `ed443186a7f0`). All 10 trades match the earlier run `e1d3c22e6b5b` (DB-tail, ending 2026-10-16 14:00) on entry, exit, gross and fees. The chart check for each loss used the same 1h candles from PostgreSQL. Times are candle open times in UTC.

Six of the ten trades were capped by the leverage limit ($1,000 × 2 = $2,000 notional), so their risk was below $20. "R net" is the screen's value (net ÷ the trade's actual risk). "R net $" uses 1R = $20, the row risk.

## Losing trades (net < 0): 4 of 10

### Trade 3 · BTCUSDT · 1h · SHORT
- **Times:** entry 2026-05-21 10:00 → exit 2026-05-21 17:00, held 7 bars.
- **Prices:** entry 77,172.6, stop 77,724.94, TP1 76,014.2, exit 77,724.94. Outcome SL.
- **P&L:** gross −$15.01, fees −$1.68, net −$16.69. R net −1.11; R net $ −0.83. Capped: risk was $15.01, not $20.
- **Market context:** Structure WRONG · Uptrend · High volume (2.45× the 20-bar average) · Low zone (0.24 of the prior 24-bar range). Decision bar 2026-05-21 10:00.
- **Distance:** stop 0.72% above entry; TP1 1.50% below, so TP1 was 2.1× farther than the stop. Best low was 76,696.7: 41% of the way to TP1 and 0.86R in favor, 4 bars before the stop bar.
- **Bucket 4:** entry against the context (WRONG) and against the trend label.
- **At entry:** the context cell already said the short went against an uptrend, with volume running 2.45× normal near the bottom of the prior range.

### Trade 5 · BTCUSDT · 1h · SHORT
- **Times:** entry 2026-07-24 13:00 → exit 2026-07-26 16:00, held 51 bars.
- **Prices:** entry 64,129.1, stop 64,840.75, TP1 62,658.4, exit 64,840.75. Outcome SL.
- **P&L:** gross −$20.00, fees −$1.45, net −$21.45. R net −1.07; R net $ −1.07.
- **Market context:** Structure WRONG · Uptrend · High volume (6.65×) · Low zone (−0.44: below the prior 24-bar range). Decision bar 2026-07-24 13:00.
- **Distance:** stop 1.11% above; TP1 2.29% below (2.07× the stop). Best low 63,666.0, 31% of the way to TP1 and 0.65R in favor, came on the first bar after entry. The stop then took 50 more bars.
- **Bucket 4:** entry against the context (WRONG) and the trend label. The 51-bar hold also fits bucket 6, but the row does not show when the setup failed.
- **At entry:** the context cell showed an uptrend, with volume 6.65× normal and the decision close already below the prior 24-bar range.

### Trade 7 · BTCUSDT · 1h · SHORT
- **Times:** entry 2026-08-03 03:00 → exit 2026-08-03 13:00, held 10 bars.
- **Prices:** entry 62,839.7, stop 63,201.82, TP1 62,075.6, exit 63,201.82. Outcome SL.
- **P&L:** gross −$12.55, fees −$1.75, net −$14.30. R net −1.14; R net $ −0.72. Capped: risk was $12.55.
- **Market context:** Structure WRONG · Uptrend · High volume (1.59×) · Low zone (−0.15). Decision bar 2026-08-03 03:00.
- **Distance:** stop 0.58% above; TP1 1.22% below (2.11× the stop). Best low was 62,268.2: 75% of the way to TP1 and 1.58R in favor, reached 5 bars before the stop bar, on a different bar. Price first passed +1R in favor on bar 5 after entry.
- **Bucket 2:** the stop was hit after price had gone most of the way to TP1, so an open profit was given back. (The WRONG context also applies.)
- **At entry:** the context cell showed WRONG structure in an uptrend, with the close just below the prior 24-bar range.

### Trade 10 · BTCUSDT · 1h · SHORT
- **Times:** entry 2026-10-13 17:00 → exit 2026-10-13 21:00, held 4 bars.
- **Prices:** entry 83,062.9, stop 83,441.63, TP1 82,244.0, exit 83,441.63. Outcome SL.
- **P&L:** gross −$10.63, fees −$1.87, net −$12.50. R net −1.18; R net $ −0.63. Capped: risk was $10.63; fees came to 0.18R, the highest in the run.
- **Market context:** Structure neutral · No trend · Normal volume (0.69×) · High zone (0.66). Decision bar 2026-10-13 17:00.
- **Distance:** stop 0.46% above, the tightest stop in the run; TP1 0.99% below (2.16× the stop). Best low 82,946.2, only 14% of the way to TP1 and 0.31R in favor, 3 bars before the stop bar.
- **Bucket 1:** stop hit before TP1; price never got near the target.
- **At entry:** the context cell showed nothing against the short (neutral, upper half of the range). The only stand-out fact is the 0.46% stop on below-average volume.

### Green on gross, red on net
None. All four losses were already negative on gross; fees flipped no trade.

## Summary

- **Trades:** 10. Winners (net > 0): 6. Losers (net < 0): 4. Scratches: 0.
- **Net:** +$153.56. Fees −$14.61. Gross +$168.17.
- **Total loss $:** −$64.95. Of that, −$6.75 is fees on the losing trades.

**Losses by bucket**

| Bucket | Count | Net $ | Share of loss $ |
|---|---:|---:|---:|
| 1 Stop before TP1 | 1 (#10) | −12.50 | 19.2% |
| 2 Gave back open profit | 1 (#7) | −14.30 | 22.0% |
| 3 Fees turned TP negative | 0 | 0 | 0% |
| 4 Against context / trend | 2 (#3, #5) | −38.14 | 58.7% |
| 5 Wrong half of range | 0 | 0 | 0% |
| 6 Held after setup failed | 0 | 0 | 0% |
| 7 Not enough information | 0 | 0 | 0% |
| **Total** | **4** | **−64.95** | **100%** |

Each loss has one bucket. Precedence: path facts first (3, then 2), then entry facts (4, then 5), then 6, then 1. Trades 3, 5 and 7 were also shorts in the lower half of the range, the bucket 5 condition.

- **By symbol:** BTCUSDT 4 / −$64.95.
- **By side:** SHORT 4 / −$64.95.
- **By outcome:** SL 4 / −$64.95.

**By market context**

| Context | Losses | Net $ | Winners in the same context |
|---|---:|---:|---|
| WRONG | 3 (#3, #5, #7) | −52.44 (80.7%) | 3 (#6, #8, #9): +$130.61 |
| NEUTRAL | 1 (#10) | −12.50 (19.3%) | 3 (#1, #2, #4): +$87.90 |
| CORRECT | 0 | 0 | 0 |

**Largest losses** (there are only 4)

| Trade | Entry time | Net $ | Bucket |
|---|---|---:|---|
| #5 | 2026-07-24 13:00 | −21.45 | 4 |
| #3 | 2026-05-21 10:00 | −16.69 | 4 |
| #7 | 2026-08-03 03:00 | −14.30 | 2 |
| #10 | 2026-10-13 17:00 | −12.50 | 1 |

**Clustering:** the losses are spread out, not one cluster. They fall in May, late July and early August (#5 and #7 are 10 days apart, with winner #6 between them), and mid October. No two losses are back to back. The worst drawdown is −$21.45 (trade #5). The equity curve makes a new high after every loss.

## Suggestions (research only — none is a live rule)

Ordered by the net $ you can add up from this blotter, largest first. The two that can't be added up come last.

**Not the biggest leak:**
- **Fees** were −$14.61 overall and −$6.75 on losers, and flipped no trade.
- **The WRONG-context filter:** WRONG losses are 80.7% of the loss $. But the same filter would also have removed three larger winners. On this run it would have cost money, not saved it.

**1. Skip shorts whose stop is closer than 0.5% of entry. (Bucket 1)**
- Removes: #10 (−$12.50).
- Winners removed: none (the next-tightest stop is 0.58%).
- Net effect: +$12.50.
- Caveat: it rests on one trade. Tight stops also carry the highest fee per R here (#10 paid 0.18R in fees).

**2. Skip shorts in the lower half of the prior 24-bar range (range position < 0.5). (Bucket 5 condition; overlaps bucket 4)**
- Removes: losers #3, #5, #7 (−$52.44).
- Winners removed: #4, #8, #9 (+$130.47).
- Net effect: −$78.03. It would have made this run worse.

**3. Skip shorts whose market context is WRONG, or whose trend label is Uptrend. (Bucket 4, plus #7)**
In this run both rules pick the same six trades.
- Removes: losers #3, #5, #7 (−$52.44).
- Winners removed: #6, #8, #9 (+$130.61).
- Net effect: −$78.16. It would have made this run worse, so the context filter would not have fixed it.

**4. Move the stop to entry once price has gone +1R in favor. (Bucket 2)**
- Changes: #7, which passed +1R on bar 5 and was stopped on bar 10. Exiting at entry would have cost only the fees, about −$1.74, instead of −$14.30: $12.56 kept on that trade.
- Winners: not visible on the blotter. Its MAE column is the worst point over the whole trade, not the worst point after +1R, so I can't tell whether a winner would have been stopped at entry first.
- Net effect: not measurable from this blotter.

**5. Exit at the close if TP1 has not been hit within 40 bars. (Bucket 6 pattern on #5)**
- Changes: #5, held 51 bars; its best price came on bar 1.
- Winners removed: none. The longest winner (#8) closed at bar 38.
- Net effect: not measurable from this blotter. The row does not show the bar-40 close.

**Caveat:** with 10 trades and 4 losses, none of these is evidence. Each would need its own pre-registered test on data not yet read. This window, 2026-02-18 → 2026-10-16, has now been read twice for this book.
