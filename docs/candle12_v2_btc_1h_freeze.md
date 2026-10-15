# CANDLE12_V2 freeze — BTCUSDT 1h LONG

**Status:** Frozen research. Not production. Not paper.  
**Frozen:** 2026-10-15  
**Symbol:** BTCUSDT only  
**Timeframe:** 1h  
**Direction:** LONG  
**Engine:** `backend/app/research/candle12_v2.py` (`run_candle12_v2`)

Other symbols and the 5m / 15m timeframes are not part of this freeze.

## Frozen rules

| Area | Rule |
|------|------|
| Setup | Candle-1 bullish BOS. Candle-1 must close in the break direction (close above its open). |
| Entry | Next candle. Wick fill when that candle’s high trades above Candle-1 high. Fill = that high + 0.02% slippage. The entry candle’s close is not the fill. |
| Stop | Structural stop from the stop engine, bounded by Candle-1 low minus the ATR buffer. A candle low through the stop exits. |
| Target | Nearest target price touched by a candle high. Same candle through both stop and target counts as the stop. |
| Costs | Taker fee 0.04% each side. Exit slippage 0.02% adverse. |
| Account | $1,000 start per calendar year in the audited runs. Risk $20 per trade, not 2% of the growing balance. Leverage 2×. Margin cap $1,000. |
| Position | One open trade at a time. |

## Audited result (do not replace these numbers)

Four separate $1,000 years, 2023-01-01 through 2026-10-15. 92 closed longs. Net **+$598** ($1,598 if the $20 risk is kept fixed).

| Year | Trades | End equity | Strategy | Bitcoin, first close to last close |
|------|--------|------------|----------|-------------------------------------|
| 2023 | 21 | $1,022 | +2.2% | +158% |
| 2024 | 31 | $1,284 | +28.4% | +120% |
| 2025 | 21 | $1,139 | +13.9% | −6.9% |
| 2026 to 15 Oct | 19 | $1,154 | +15.4% | −7.1% |

2023 is flat (+1.1R). Dollars ÷ $20 is about +0.33R per trade and is not true R on margin-capped trades. No slippage beyond the 0.02% already in the engine, and no funding. Closed-trade drops inside a year were 11.8% (2023), 10.5% (2024), 12.9% (2025), and about 7% (2026).

A candle-close entry, filled at the next candle’s open on the same 92 signals, made about **+$279**. The wick fill stays.

## Do not change

1. Do not switch this BTC 1h long book from the wick entry to a candle-close entry.
2. Do not add the stop-out short. On these stops it showed no edge (−7.6R at the mirrored target, −9R at 1R), and the on-screen replay starts one candle late.
3. Do not scale risk with equity, change the $20 risk, the 2× leverage, the $1,000 margin cap, or the fee and slippage rates for this book.
4. Do not retune stops, targets, or BOS rules to improve the +$598.
5. Do not mark the book production-comparable or turn it on for paper.

## What is allowed

- Behavior-preserving refactors covered by the existing candle12 tests.
- A new strategy id if the entry, stop, target, or risk changes.
- Research on other symbols and timeframes, labeled as not this freeze.

## What this freeze does not claim

It does not claim a proven edge, a bear-market hedge, or a buy-and-hold replacement. 2025 and 2026 were down from the first close to the last close and still contained large up-legs.
