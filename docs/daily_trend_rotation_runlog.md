# Daily trend rotation — run log

Pre-registration: `docs/daily_trend_rotation_prereg.md`, sha256 `24da2ebd557cbd424a1807497eb1d3029d6ba3beb5c81a20070f8ba18ec13364`, written 2026-10-18T07:42:09Z.

## Trial 20, run 1: in-sample diagnostic (2026-10-18T07:48Z)

- **Setup:** one run through the Backtest tab API (research-only instance, worktree feat/backtest-daily-trend). Symbols BTCUSDT, ETHUSDT and SOLUSDT; direction BOTH; Postgres 1d; start 2022-10-01, end 2026-07-14. Principal $10,000, risk $50 per trade (scaled 1/3 for the rotation, 1/12 for the benchmark), leverage 2, taker 0.04%, no funding.
- **First rotation entry:** 2024-01-09. The first rebalance comes 6 months after all 12 books are computable, and Postgres 1d starts in 2022-10. The benchmark trades from 2022-11.

| Strategy | Coin | Trades | Net $ (full window) | PF | 2023 | 2024 | 2025 | 2026 (to 07-14) | Net $ 2024-01 → 2026-07-14 |
|---|---|---|---|---|---|---|---|---|---|
| Rotation | BTC | 109 | +439 | 1.84 | n/a | +230 | +138 | +72 | +440 |
| Static 12 | BTC | 436 | +823 | 2.55 | +557 | +135 | +67 | +72 | +274 |
| Rotation | ETH | 106 | +227 | 1.41 | n/a | +41 | +268 | −82 | +227 |
| Static 12 | ETH | 432 | +264 | 1.42 | +143 | −48 | +151 | +54 | +157 |
| Rotation | SOL | 105 | −236 | 0.59 | n/a | −109 | −84 | −43 | −236 |
| Static 12 | SOL | 488 | +1,094 | 2.53 | +1,266 | −80 | −74 | −9 | −163 |
| Rotation | 3 coins | 320 | +431 | 1.26 | n/a | +161 | +323 | −53 | +431 |
| Static 12 | 3 coins | 1,356 | +2,180 | 2.17 | +1,966 | +7 | +144 | +118 | +269 |

Year columns are by entry year.

**Comparison window.** The pre-registration did not say which window the benchmark comparison uses. The full-window totals favor the benchmark only because the rotation cannot trade in 2023. The last column compares the two over the same period (2024-01 → 2026-07-14); that choice was made after seeing these results.

Rotation trades by book: 1: 12, 2: 28, 3: 65, 4: 8, 5: 1, 6: 11, 9: 2, 11: 16, 12: 64, 14: 35, 15: 75, 19: 3.

**Status:** in-sample diagnostic only. It cannot pass or fail the rule, and the confirmation criterion in the pre-registration has not been tested. The holdout from 2026-07-15 stays sealed.
