# SHORT Policy Sensitivity Study (Research Only)

Generated: `2026-10-02T16:36:12.678044+00:00`
Dataset: BOS Combination Research · Combo: `COMBO_02` · Source: `trend_regime_trade_rows.json`

> Analysis only. Existing trades preserved. Live engines unchanged by default.

## 1. Dataset

- Closed trades: **219**
- Symbols: `['BTCUSDT', 'ETHUSDT', 'SOLUSDT']`
- Timeframes: `['15m', '1h']`
- Directions: `{'LONG': 107, 'SHORT': 112}`
- Research HTF states: `{'HTF_CONFLICT': 88, 'HTF_NEUTRAL_OR_UNAVAILABLE': 67, 'HTF_ALIGNED': 64}`

## 2. Scenario results

| Scenario | retained | excl | ret% | WR | mean R | PF | SHORT n/WR/meanR | LONG n/WR/meanR | sample |
|---|---:|---:|---:|---:|---:|---:|---|---|---|
| CURRENT | 219 | 0 | 100.0 | 26.9% | -0.146 | 0.801 | 112 / 19.6% / -0.374 | 107 / 34.6% / 0.093 | SAMPLE_SIZE_OK |
| LONG only | 107 | 112 | 48.9 | 34.6% | 0.093 | 1.142 | 0 / — / — | 107 / 34.6% / 0.093 | SAMPLE_SIZE_OK |
| LONG + SHORT HTF_ALIGNED only | 131 | 88 | 59.8 | 31.3% | -0.016 | 0.977 | 24 / 16.7% / -0.499 | 107 / 34.6% / 0.093 | SAMPLE_SIZE_OK |
| LONG + SHORT 15m only | 158 | 61 | 72.1 | 28.5% | -0.086 | 0.880 | 51 / 15.7% / -0.461 | 107 / 34.6% / 0.093 | SAMPLE_SIZE_OK |
| LONG + SHORT 1h only | 168 | 51 | 76.7 | 30.4% | -0.050 | 0.928 | 61 / 23.0% / -0.300 | 107 / 34.6% / 0.093 | SAMPLE_SIZE_OK |
| LONG + SHORT 15m HTF_ALIGNED | 118 | 101 | 53.9 | 32.2% | 0.017 | 1.024 | 11 / 9.1% / -0.725 | 107 / 34.6% / 0.093 | SAMPLE_SIZE_OK |

## 3. Gate draft signal (not auto-enabled)

- holds_for_gate_draft: **True**
- suggested_defaults: `{'research_gate_enabled': False, 'research_gate_block_shorts': True, 'research_gate_block_htf_conflict': False}`
- rationale: SHORT mean R negative while LONG mean R positive; LONG_ONLY improves aggregate mean R vs CURRENT on this sample.
- caveat: Requires SHORT n>=10 and descriptive improvement; SAMPLE_THRESHOLD=30. Not a profitability claim.

## 4. Warnings

- MULTIPLE_TESTING_RISK: six predefined SHORT-policy scenarios are reported on the same closed-trade sample. Descriptive only; not a production selection.
- Research only. Live engines unchanged unless research_gate_* flags are explicitly enabled.
- Unavailable HTF never treated as bullish/bearish.
