# Multi-timeframe strategy discovery — research report

Instructions: [docs/research_instructions.md](../docs/research_instructions.md). Status: **Study complete (2026-10-15), including Phase 2b. Clean negative result: nothing passed in any track, and nothing is recommended for paper trading. The validation segment was read once, for the three pre-registered Track A subsets only. The OOS segment has never been read.** The all-tracks comparison table is in "Phase 2b — Results".

## Conclusion (study closed 2026-10-15)

**Result: negative. No strategy passed its gates in any phase or track. Nothing is recommended for paper trading.**

### Verdict

**The entry logic is the binding constraint, not the cost ratio.**
- Costs ran at 0.04–0.08R per trade in every configuration tested. Cutting them sharply (maker entry, no entry slippage, Track B3) moved net expectancy by only about +0.03R, because the structure entries' gross edge is only about 0.06–0.08R.
- Where net expectancy did clear +0.10R, it came from a larger *gross* edge, not lower costs:
  - letting H8 trades run up to 10 days behind a wider stop (gross 0.21–0.27R);
  - riding daily trends with TSMOM (gross 0.16–0.38R).
  Costs stayed a small fraction of those gross edges.
- Even those cases are not distinguishable from zero once trade clustering is respected. Their 95% weekly-block bootstrap intervals all include zero, and their deflated Sharpe is 0 after 85 configurations.
- TSMOM's edge comes entirely from the long side of a 2023–24 bull market that buy-and-hold captured better.
- The post-hoc regime subsets (Track A) did not survive one look at unseen data.

What would have to change is the *source* of gross edge, at the payoff and holding-period level, not execution costs.

### Every configuration evaluated, ranked by net expectancy

Net of 0.05% taker + 0.03% slippage per side and 0.01%/8h funding (B3: 0.02% maker entry fee, no entry slippage). Risk 0.5% per trade on a $10k book; TSMOM uses volatility-target sizing with 1R = 2×ATR (see Phase 2b).
- **dev** = 2022-10-16 → 2025-03-31, BTC/ETH/SOL pooled.
- **val** = 2025-04-08 → 2025-12-31, core + 11 Tier-2 coins pooled.
- Brackets = 95% weekly-block bootstrap interval.
- Max DD is on closed trades, except TSMOM, which is marked to market.
- DSR was 0.00 for every row.

The 60 regime × direction cells looked at descriptively in Phase 4 are counted in the deflated-Sharpe N (85), but they are not listed below as evaluated strategies.

| Rank | Configuration | Phase | Data | Trades | Trades/month | Net expectancy [95% bootstrap] | PF | Max DD | Failed at |
|---|---|---|---|---|---|---|---|---|---|
| 1 | B1 TSMOM 1d, N=40 | 2b-B | dev | 196 | 6.6 | +0.325R [−0.114, +0.939] | 1.51 | 37.8% | Phase 4 gates (bootstrap, DSR) |
| 2 | B1 TSMOM 1d, N=60 | 2b-B | dev | 177 | 6.0 | +0.301R [−0.158, +0.898] | 1.50 | 34.6% | Phase 4 gates (bootstrap, DSR) |
| 3 | B2 H8, 1.5×ATR4 stop, 5R, 10 days | 2b-B | dev | 448 | 15.2 | +0.191R [−0.046, +0.447] | 1.22 | 11.0% | Phase 4 gates (bootstrap, DSR) |
| 4 | B2 H8, 1.5×ATR4 stop, 3R, 10 days | 2b-B | dev | 538 | 18.2 | +0.149R [−0.036, +0.338] | 1.20 | 11.2% | Phase 4 gates (bootstrap, DSR) |
| 5 | B1 TSMOM 4h, N=40 | 2b-B | dev | 1065 | 36.1 | +0.136R [−0.033, +0.346] | 1.32 | 34.6% | Phase 4 gates (bootstrap, DSR) |
| 6 | B1 TSMOM 1d, N=20 | 2b-B | dev | 286 | 9.7 | +0.120R [−0.102, +0.402] | 1.18 | 33.2% | Phase 4 gates (bootstrap, DSR) |
| 7 | A3 H5 shorts, sideways regime | 2b-A | **val** | 154 | 17.5 | +0.069R [−0.245, +0.412] | 1.11 | 10.4% | validation gates |
| 8 | B1 TSMOM 4h, N=60 | 2b-B | dev | 903 | 30.6 | +0.057R [−0.102, +0.238] | 1.15 | 37.9% | Phase 4 gates |
| 9 | B3 H5, maker entry (fill 99.4%) | 2b-B | dev | 438 | 14.9 | +0.034R [−0.106, +0.174] | 1.05 | 10.2% | Phase 4 gates |
| 10 | B3 H8, maker entry (fill 99.7%) | 2b-B | dev | 756 | 25.6 | +0.026R [−0.087, +0.140] | 1.04 | 19.5% | Phase 4 gates |
| 11 | B1 TSMOM 4h, N=20 | 2b-B | dev | 1745 | 59.2 | +0.024R [−0.065, +0.128] | 1.09 | 31.6% | Phase 4 gates |
| 12 | H5 compression breakout | 3 | dev | 437 | 14.8 | +0.003R [−0.133, +0.140] | 1.00 | 11.4% | Phase 4 gates |
| 13 | H8 volume-supported expansion | 3 | dev | 758 | 25.7 | +0.001R [−0.110, +0.114] | 0.99 | 23.5% | Phase 4 gates |
| 14 | A1 H2 longs, sideways regime | 2b-A | **val** | 137 | 15.6 | −0.003R [−0.388, +0.361] | 0.98 | 10.7% | validation gates |
| 15 | B2 H5, 1.5×ATR4 stop, 3R, 10 days | 2b-B | dev | 386 | 13.1 | −0.050R [−0.226, +0.132] | 0.92 | 17.8% | Phase 4 gates |
| 16 | H3 1h CHoCH + retest | 3 | dev | 352 | 11.9 | −0.051R [−0.168, +0.069] | 0.88 | 18.5% | Phase 4 gates |
| 17 | B2 H5, 1.5×ATR4 stop, 5R, 10 days | 2b-B | dev | 354 | 12.0 | −0.052R [−0.276, +0.186] | 0.92 | 27.7% | Phase 4 gates |
| 18 | H2 BOS + retest, 3% zigzag structure | 3 | dev | 742 | 25.2 | −0.105R [−0.217, +0.007] | 0.84 | 35.5% | Phase 4 gates |
| 19 | H1 4h sweep + reclaim | 3 | dev | 496 | 16.8 | −0.116R [−0.232, +0.003] | 0.83 | 27.3% | Phase 4 gates |
| 20 | H2 BOS + retest, DC 2.5×ATR structure | 3 | dev | 343 | 11.6 | −0.128R [−0.279, +0.020] | 0.82 | 26.4% | Phase 4 gates |
| 21 | H7 4h trend, controlled retracement | 3 | dev | 303 | 10.3 | −0.144R [−0.290, +0.003] | 0.77 | 20.3% | Phase 4 gates |
| 22 | H6 1h failed breakout, range re-entry | 3 | dev | 1028 | 34.9 | −0.150R [−0.238, −0.060] | 0.75 | 57.6% | Phase 4 gates |
| 23 | A2 H8 shorts, sideways regime | 2b-A | **val** | 202 | 23.0 | −0.174R [−0.395, +0.074] | 0.68 | 17.8% | validation gates |
| 24 | H2 BOS + retest, fractal k=3 structure | 3 | dev | 570 | 19.3 | −0.174R [−0.294, −0.055] | 0.74 | 43.7% | Phase 4 gates |
| 25 | H4 4h demand/supply zone retest | 3 | dev | 112 | 3.8 | −0.573R [−0.805, −0.314] | 0.36 | 27.7% | dropped at Phase 4 |

Buy and hold over dev: BTC +331%, ETH +42%, SOL +318%. Tier 2 run of the 10 Phase 3 configurations (dev, never fitted): all negative, −0.002R to −0.200R.

### Closed

These are not to be re-tested on this history. Any revisit needs new data, i.e. bars after 2026-10-14 or paper-forward trading.

- **Strategy families**, as specified in Phase 2, on Binance USDⓈ-M perpetuals, BTC/ETH/SOL (+ 11 Tier-2 coins), at 4h/1h and 1h/15m with 1h/4h stops:
  - A. liquidity sweep + reclaim (H1)
  - B. BOS + first retest (H2)
  - C. CHoCH + retest (H3)
  - D. demand/supply zone first retest (H4)
  - E. volatility compression breakout (H5)
  - F. failed breakout + range re-entry (H6)
  - G. trend continuation after a controlled retracement (H7)
  - H. volume-supported expansion (H8)
- **Structure methods** for BOS entries: confirmed fractal swings (k=3), ATR-normalised directional change (2.5×ATR), fixed-% causal zigzag (3%). None was positive. A fixed-% threshold is also unusable across coins (3× more swings on SOL than BTC).
- **Cost-ratio variants:** maker entry for H5 and H8, and long-hold variants (1.5×ATR4 stop, 3R/5R, 10 days) for H5 and H8.
- **Time-series momentum** on 1d and 4h with N ∈ {20, 40, 60}, volatility-targeted, always in the market.
- **Regime subsets A1–A3** (H2 longs / H8 shorts / H5 shorts in daily-z sideways regimes). The validation segment 2025-04-08 → 2025-12-31 has been used once for these and is no longer a clean test for them.
- **The 60 descriptive regime × direction cells** from Phase 4. These were seen on development, so they must not be presented as findings.

### Out-of-sample segment

**2026-01-08 → 2026-10-14 (UTC, inclusive) has never been read by this study.**
- `research/common.py` raises an error on any read reaching 2026-01-08 unless `RESEARCH_ALLOW_OOS=1`, and that variable was never set.
- Earlier, unrelated studies in this repo have looked at these bars (see Phase 1, "Earlier use of this history"). The segment is therefore clean only with respect to this study's rules.

### Compute used

| Phase | Wall time | Budget |
|---|---|---|
| 1 (data audit) | a few minutes; not timed | none set |
| 2 (look-ahead tests, descriptive stats) | ≈ 22 min | outside the Phase 3–7 budget |
| 3–7 | ≈ 2 m 55 s | 4 h |
| 2b (Tracks A + B) | ≈ 2 m 22 s | 2 h |
| **Total timed** | **≈ 27 min** (budgeted phases: 5 m 17 s of 6 h) | |

Per-run log: `research/out/compute_log.csv`. All 85 configurations: `research/experiments.csv`.

### Reproducing

All scripts run from the repo root with `backend/.venv/Scripts/python.exe` and read Postgres `ohlcv` read-only.
- The bar cache `research/out/cache/` (≈ 91 MB of raw OHLCV pickles) is not committed. It regenerates on first run.
- **Fixed at close-out:** trade logs for configurations with a `:` in their name were written as NTFS alternate data streams. On Windows, `trades_H2:fractal…` created a hidden stream on a file called `trades_H2`, so git could not see them. All 13 were copied byte-for-byte to normal files (`:` → `_`), and `runner.trades_file()` now writes sanitised names.

---

## Comparison table, Phases 3–4 only (the full ranking is in the Conclusion above)

Development segment (2022-10-16 → 2025-03-31), core coins pooled, net of 0.05% taker + 0.03% slippage per side + 0.01%/8h funding, 0.5% risk per trade on a $10k book. Max drawdown is on closed trades. Source: `research/p3_baselines.py` and `research/p4_compare.py`, written to `research/out/p4/p4_tables.md`.

| Rank | Strategy | Coins | Direction | Timeframes | Trades/month | Net expectancy | Profit factor | Max drawdown | OOS status |
|---|---|---|---|---|---|---|---|---|---|
| – | H5 compression breakout | BTC ETH SOL | long + short | 4h setup / 1h exec | 14.8 | +0.003R [−0.133, +0.140] | 1.00 | 11.4% | not run: failed Phase 4 |
| – | H8 volume expansion | BTC ETH SOL | long + short | 1h setup / 15m exec | 25.7 | +0.001R [−0.110, +0.114] | 0.99 | 23.5% | not run: failed Phase 4 |
| – | H3 CHoCH + retest | BTC ETH SOL | long + short | 1h setup / 15m exec | 11.9 | −0.051R [−0.168, +0.069] | 0.88 | 18.5% | not run: failed Phase 4 |
| – | H2 BOS + retest (zigzag 3%) | BTC ETH SOL | long + short | 4h / 1h | 25.2 | −0.105R [−0.217, +0.007] | 0.84 | 35.5% | not run: failed Phase 4 |
| – | H1 sweep + reclaim | BTC ETH SOL | long + short | 4h / 1h | 16.8 | −0.116R [−0.232, +0.003] | 0.83 | 27.3% | not run: failed Phase 4 |
| – | H2 BOS + retest (DC, baseline) | BTC ETH SOL | long + short | 4h / 1h | 11.6 | −0.128R [−0.279, +0.020] | 0.82 | 26.4% | not run: failed Phase 4 |
| – | H7 trend pullback | BTC ETH SOL | long + short | 4h + 1h trigger / 1h | 10.3 | −0.144R [−0.290, +0.003] | 0.77 | 20.3% | not run: failed Phase 4 |
| – | H6 failed breakout | BTC ETH SOL | long + short | 1h / 15m | 34.9 | −0.150R [−0.238, −0.060] | 0.75 | 57.6% | not run: failed Phase 4 |
| – | H2 BOS + retest (fractal) | BTC ETH SOL | long + short | 4h / 1h | 19.3 | −0.174R [−0.294, −0.055] | 0.74 | 43.7% | not run: failed Phase 4 |
| – | H4 demand/supply zone | BTC ETH SOL | long + short | 4h / 1h | 3.8 | −0.573R [−0.805, −0.314] | 0.36 | 27.7% | dropped at Phase 4 (failed every gate except the trade floor: 112 trades) |

Brackets = 95% block-bootstrap interval. No row is ranked because none passed the Phase 4 gates.

## Locked study parameters (approved 2026-10-15, after Phase 1)

Encoded in [research/common.py](common.py). Any data read that reaches the OOS segment raises an error unless `RESEARCH_ALLOW_OOS=1`, and only Phase 6 sets it.

| Item | Locked value |
|---|---|
| Development | 2022-10-16 → 2025-03-31 |
| Validation | 2025-04-08 → 2025-12-31 |
| OOS | 2026-01-08 → 2026-10-14. Not read until Phase 6. |
| Embargo / purge | 7-day gaps between segments. A trade must close inside the segment it opened in: signals within the maximum holding time of a segment's end are dropped. |
| Core (all development and parameter work) | BTCUSDT, ETHUSDT, SOLUSDT |
| Tier 2 (cross-coin validation only, Phases 4–6, never fitted) | BNBUSDT, XRPUSDT, ADAUSDT, APTUSDT, AVAXUSDT, DOGEUSDT, DOTUSDT, LINKUSDT, LTCUSDT, NEARUSDT, OPUSDT |
| Excluded | TONUSDT, AAVEUSDT, ATOMUSDT, BCHUSDT, ETCUSDT, FILUSDT, ICPUSDT, INJUSDT, RENDERUSDT, TRXUSDT, UNIUSDT (history starts 2024-10; TON also has zero-volume bars); ARBUSDT (history from 2023-03), SUIUSDT (from 2023-05). Neither was in the Phase 1 universe as approved, and the locked universe takes no ad-hoc additions (user decision, Phase 2 approval). |
| 15m | Entry timing and exit monitoring only, on 1h structure, and only where 15m bars are stored. No backfill, no DB writes. Config B and Swing take priority over config A. |
| Costs | taker 0.05% + slippage 0.03% per side; funding a flat 0.01% per 8h charged to both sides. Phase 7 finalists get an extra sensitivity row at 0% and 0.02%. |
| Compute budget, Phases 3–7 | 4 h total. **Used: about 3 min of runs** (tally in the Phase 7 section). |

Notes on how I read the universe instructions:
- The "14 coins with history from ~2023-01" in the Phase 1 coverage table *include* BTC, ETH and SOL, and TON was never among them. Tier 2 is therefore the other 11 coins.
- ARB (history from 2023-03) and SUI (from 2023-05) are in neither the 14 nor the 2024-10 group. **Excluded**, confirmed by the user at Phase 2 approval: the universe was locked in Phase 1 and gets no ad-hoc additions.
- Tier-2 15m coverage starts at: APT, AVAX, DOGE, DOT 2023-01; ADA, OP 2023-11; LINK 2024-04; NEAR 2024-05; LTC 2024-06; BNB 2024-08; XRP 2024-10. For each coin, the 15m hypotheses (H3, H6, H8) run only from that coin's 15m start date.

---

## Phase 1 — Data audit

Scripts (read-only against Postgres `ohlcv`; re-run from the repo root):
- `backend/.venv/Scripts/python.exe research/p1_audit.py` → `research/out/p1/p1_tables.md` (all tables below, plus full per-coin detail) and CSVs in `research/out/p1/`.
- `backend/.venv/Scripts/python.exe research/p1_funding.py` → `research/out/p1/funding_btc.md`.

### Part 1 context, as found in the repo (the brief left these blank)

| Item | Finding |
|---|---|
| OHLCV store | Postgres table `ohlcv` (`time, symbol, timeframe, OHLC, volume, quote_volume, trade_count, taker_buy_*, source`), unique key `(time, symbol, timeframe)`. Read-only loader: `backend/scripts/strategy_rank/multicap/_db.py`. |
| Timestamps | UTC, **bar open time**. Every bar sits on its timeframe grid (0 off-grid bars), and open-time buckets of the lower timeframe reproduce the stored higher-timeframe bars (alignment table below). |
| Venue | **Binance USDⓈ-M perpetual futures** (evidence below). |
| Backtester | `backend/scripts/sleeve_study/sim.py`: entry at the next bar's open plus slippage, stop checked before target when both are hit in one bar, gap fills at the open, time stop, ATR trailing stop, funding (historical or flat), per-trade risk sizing, leverage cap, account open-risk cap. Its trade accounting was independently re-derived in an earlier audit (2,642 trades, 0 errors). **Missing:** limit/retest entries and partial take-profit. Both would be added as a thin wrapper in `research/` if Phase 2 needs them. |
| Python | `backend/.venv` (Python 3.12, pandas 3.0, numpy 2.2, asyncpg). |
| Costs (brief defaults) | taker 0.05% + slippage 0.03% per side = **0.16% round trip**. This is stricter than the repo's earlier studies (0.04% + 0.02%). |
| Funding | Historical settlements exist **only for BTCUSDT** (2022-10-01→2026-10-05, 4,398 rows). The `funding_rates` table is a live premium-index stream that starts 2026-10-01, so it is not usable for history. For all other coins: flat 0.01%/8h, charged to both longs and shorts (see decision 4). |
| Trade floor / compute budget | Floor of 100 development-set trades assumed. **Compute budget not given** (decision 5). |

### Venue: spot or perpetual?

Evidence that this is Binance USDⓈ-M perpetual futures data, not spot:
1. Code: ingestion calls `/fapi/v1/klines` ([binance_rest.py:388](../backend/app/ingestion/binance_rest.py#L388)), and the research pipeline uses `rest.futures_klines`; its manifest says "Real Binance USDⓈ-M Futures klines only".
2. Data: futures-only contract tickers are present (`1000PEPEUSDT`, `1000SHIBUSDT`, `1000BONKUSDT`, `1MBABYDOGEUSDT` and 9 others). These do not exist on spot.
3. Volume level: BTCUSDT median daily volume over the last 12 months is 150,854 BTC ($11.9B). That is the scale of perpetual-futures volume, several times Binance spot BTCUSDT.

### Symbols and coverage

There are 530 symbols, but most have only recent history. The table counts symbols whose first bar is on or before each date:

| history from on/before | 15m | 1h | 4h | 1d |
|---|---|---|---|---|
| 2022-10-20 | 3 | 3 | 3 | 3 |
| 2023-01-05 | 7 | 11 | 14 | 14 |
| 2024-01-05 | 11 | 15 | 16 | 16 |
| 2024-10-10 | 27 | 27 | 27 | 27 |

The usable research universe is therefore the 27 coins earlier repo studies already backfilled ("core-27"). Their coverage:

| Coins | 1h / 4h / 1d from | 15m from |
|---|---|---|
| BTC, ETH, SOL | 2022-10-16 (4h from 2021-10-16) | 2022-10-16 |
| APT, AVAX, DOGE, DOT | 2023-01-01 (AVAX 1h 2024-04, DOGE 1h 2023-02; both rebuildable from 15m) | 2023-01-01 |
| BNB, XRP, ADA, LINK, LTC, NEAR, OP, ARB (2023-03), SUI (2023-05) | 2023-01 to 2023-05 | 2023-09 to 2024-10 |
| AAVE, ATOM, BCH, ETC, FIL, ICP, INJ, RENDER, TON, TRX, UNI | 2024-10-09 | 2024-10-09 |

**"About 4 years" is true only for BTC, ETH and SOL.** For most coins, 15m history starts between 2023-09 and 2024-10. This limits the 15m-execution configurations (Intraday A and B) during development; see decision 3.

**Survivorship:** none of the 530 symbols stopped updating before 2026-10-01, so the DB holds no delisted contracts. The core-27 were chosen by earlier studies, after the fact, from coins that still trade today. This bias cannot be removed with the existing data. It is the main reason a forward paper test is required at the end.

### Missing and duplicate candles, OHLC sanity (core-27)

| tf | bars | missing | longest gap | off-grid | duplicates | bad OHLC | zero-volume | |Δclose| > 20% |
|---|---|---|---|---|---|---|---|---|
| 15m | 2,541,089 | 256 | 59 bars | 0 | 0 | 0 | 10,651 | 35 |
| 1h | 711,683 | 0 | 0 | 0 | 0 | 0 | 2,726 | 21 |
| 4h | 188,069 | 0 | 0 | 0 | 0 | 0 | 681 | 48 |
| 1d | 30,183 | 0 | 0 | 0 | 0 | 0 | 107 | 158 |

- **All 256 missing bars and all zero-volume bars are TONUSDT.** About 15% of its 15m bars have zero volume, and 15m data is missing on 2026-10-05→09. **Recommendation: exclude TON.**
- Duplicates are impossible because of the unique key, and the check confirms 0. No bar violates low ≤ open/close ≤ high.
- I checked the large moves against the other timeframes, and they agree. They match known events, for example 2022-11-09 FTX (SOL −51% on the day), 2023-07-13 XRP +73%, 2024-12-03 TRX +96%, 2025-03-02 ADA +72%, and the 2025-10-10 21:00 UTC liquidation cascade (−13% to −29% in one hour across alts). These are real tails, not bad ticks, and they stay in the data.
- Stored 1d bars end on 2026-10-12 (or 10-08 for the 2024-10 coins), so daily bars will be **rebuilt from 4h/1h** rather than read from stored 1d.

### Timeframe alignment

Each check aggregates complete buckets of the lower timeframe and compares them with the stored higher-timeframe bar:

| pair | buckets compared | price mismatches (O/H/L/C) | volume off > 0.1% |
|---|---|---|---|
| 15m → 1h | 622,970 | 0 / 40 / 19 / 45 | 97 |
| 1h → 4h | 177,919 | 27 / 3 / 30 / 17 | 54 |
| 4h → 1d | 30,181 | 0 / 20 / 23 / 24 | 60 |

Mismatches are 0.03% of buckets, and almost all fall on four UTC days: 2024-10-28 (all 27 coins), 2023-11-10, 2023-08-16 and 2026-10-01 (the live-ingestion switch-over). Full list: `research/out/p1/alignment_mismatch_detail.csv`. **Policy:** each coin's higher timeframes are built from one base series (1h, or 15m where it is stored), so signal bars and execution bars always see the same prices.

### Volume sanity and liquidity

- `quote_volume / volume` falls inside the bar's [low, high] range on all but 3 bars, and `quote_volume` is null on only 158 BTC 15m bars. Relative-volume features are usable.
- Ranking by median daily quote volume, 2025: BTC $15.6B, ETH $14.0B, SOL $3.7B, XRP $1.7B, DOGE $1.2B, SUI $658M, BNB $506M … RENDER $38M, ICP $33M (full yearly top-30 in `p1_tables.md`). In 2026 the top 30 includes coins with under one year of data (HYPE, ZEC, TAO, WLD, ENA…). These cannot be researched historically.

### Cost and volatility feasibility (median ATR(14), 2023-01 → 2026-10)

| | ATR% 15m | ATR% 1h | ATR% 4h | cost ÷ 1 ATR, 15m | 1h | 4h |
|---|---|---|---|---|---|---|
| BTC | 0.28 | 0.61 | 1.27 | **0.58** | 0.26 | 0.13 |
| ETH | 0.38 | 0.83 | 1.74 | 0.43 | 0.19 | 0.09 |
| BNB | 0.31 | 0.67 | 1.42 | 0.52 | 0.24 | 0.11 |
| SOL | 0.55 | 1.17 | 2.42 | 0.29 | 0.14 | 0.07 |
| typical alt | 0.5–0.7 | 1.1–1.5 | 2.2–3.2 | 0.22–0.36 | 0.11–0.17 | 0.05–0.08 |
| TRX | 0.18 | 0.39 | 0.86 | 0.88 | 0.41 | 0.19 |

With the brief's 0.16% round-trip cost, a 15m trade with a 1-ATR stop on BTC pays **0.58R in costs before any edge**, and 0.2–0.4R on alts. On 4h stops the same cost is 0.05–0.13R. Implications for Phase 2:
- 15m-based setups need stops of several 15m ATRs, so in practice structure from 1h or above. Otherwise BTC, BNB and TRX are cost-infeasible at 15m.
- Confirmed 3-bar fractal swings per month are roughly 570 on 15m, 145 on 1h and 36 on 4h for every coin. Signal frequency is not the constraint; cost per trade is.

### Market regimes (volatility-normalised, causal)

Method: daily closes are rebuilt from 4h bars. The score is z = ln(C_t / C_{t−30}) / (σ_daily,90d · √30): bull if z ≥ 1, bear if z ≤ −1, otherwise sideways. The label for day t can be used from day t+1. Monthly label = majority of days.

Share of days by half-year (BASKET5 = equal-weight index of BTC/ETH/SOL/BNB/XRP; it starts in 2023-04 because BNB and XRP history starts in 2023-01 and the score needs a warm-up):

| half | BTC bull/side/bear % | BASKET5 bull/side/bear % | notable months |
|---|---|---|---|
| 2022H1 | 1 / 55 / 36 (8 na) | – | 2022-05, 06 bear |
| 2022H2 | 1 / 84 / 16 | – | 2022-11 FTX (SOL bear) |
| 2023H1 | 30 / 69 / 1 | (partial) | 2023-01 bull |
| 2023H2 | 40 / 47 / 14 | 39 / 50 / 11 | 2023-11, 12 bull |
| 2024H1 | 25 / 74 / 1 | 28 / 69 / 3 | 2024-03 bull |
| 2024H2 | 23 / 71 / 6 | 23 / 72 / 5 | 2024-11 bull |
| 2025H1 | 10 / 81 / 9 | 7 / 81 / 12 | 2025-02, 03 bear (ETH/SOL) |
| 2025H2 | 14 / 67 / 18 | 18 / 67 / 14 | 2025-07 bull, 2025-11 bear |
| 2026H1 | 3 / 65 / 33 | 0 / 68 / 32 | 2026-02, 06 bear |
| 2026H2 (to 10-14) | 29 / 70 / 1 | 29 / 70 / 1 | 2026-09 bull |

Monthly map per coin: `research/out/p1/regime_monthly_majors.csv`. Two points matter for the split:
- Sideways dominates every period: 47–84% of days.
- Sustained bear phases in the multi-coin data occur only in 2025-11, 2026-02 and 2026-06. 2022 is bearish but covers only BTC, ETH and SOL.

### Earlier use of this history (from previous studies in this repo)

Every bar through 2026-10-13 on these 27 coins has already been looked at by earlier studies. Those studies tested different rules (EMA pullback, squeeze, Donchian, BOS combos, RSI(2)), not the structure families in this brief. Their final tests used:
- 2025-09 → 2026-02 (sleeve study)
- 2026-02-25 → 2026-07-15 (Regime Lab fix study)
- 2024-10 → 2026-10 on 11 coins (strategy search holdout)
- 2025-09 → 2026-10-13 (study 2 re-check)

**No historical window is truly untouched.** Any OOS window below is untouched by *this* study's rule design only. The only clean test is bars after the Phase 7 freeze, i.e. paper/forward trading.

### Proposed data split (to be locked on approval)

| Segment | Dates (UTC, inclusive) | Coins available | Regime content |
|---|---|---|---|
| Development | 2022-10-16 → 2025-03-31 | 3 → 16 → 26 as history begins | 2022 bear tail, 2023–24 bull and sideways, 2025-02/03 correction |
| embargo | 2025-04-01 → 04-07 | — | — |
| Validation | 2025-04-08 → 2025-12-31 | 26 | 2025-07 bull, 2025-10-10 crash, 2025-11 bear |
| embargo | 2026-01-01 → 01-07 | — | — |
| **OOS (held back until Phase 6)** | 2026-01-08 → 2026-10-14 | 26 | 2026-02 and 06 bear, 2026-09 bull, long sideways stretches |
| Forward paper | after the Phase 7 freeze | frozen list | the only clean test |

Rules attached to the split:
- Trades must close inside the segment they open in. Signals within the maximum holding time of a segment's end are dropped.
- Walk-forward in Phases 5–6 uses an expanding window with 6-month test folds across development + validation (fold tests begin 2023-10, 2024-04, 2024-10, 2025-04, 2025-10).
- The 2026 OOS runs once, after the rules are frozen.

Proposed universe policy (fixed in advance; documented changes only):
- Core-27 minus TON = 26 coins.
- A coin becomes eligible 120 days after its first bar.
- On each quarter start, a coin trades that quarter if its trailing-90-day median daily quote volume is ≥ $50M.

### Decisions needed before Phase 2

*Resolved 2026-10-15; see "Locked study parameters" at the top.* The list below is kept as asked.

1. **Split.** Approve the dates above, or move the OOS start (e.g. 2026-04-01 for a larger development set, with a shorter 6.5-month OOS).
2. **Universe.** 26 coins (drop TON), or the 5 majors only?
3. **15m history gap.** Most coins have no 15m data before 2023-09…2024-10. Options:
   - (a) Accept it. Intraday A/B are developed on BTC/ETH/SOL/APT/AVAX/DOGE/DOT before 2024-10, then all coins.
   - (b) Backfill 15m from 2023-01 using the existing research pipeline. This **writes rows to the `ohlcv` table**, so I won't do it without your OK.
4. **Funding.** Flat 0.01%/8h charged to both sides (conservative; BTC's historical mean is 0.0064% per settlement, positive 86% of the time). Alternatively, backfill settlement history with the existing grid funding backfill, which is also a DB write.
5. **Compute budget for Phases 3–7.** Hours? Long runs here must use serial shards (`ProcessPoolExecutor` intermittently fails on this machine).

---

## Phase 2 — Strategy hypotheses

Scripts (re-run from the repo root with `backend/.venv/Scripts/python.exe`):

| Script | What it does |
|---|---|
| [research/structure.py](structure.py) | Causal detectors |
| [research/hypotheses.py](hypotheses.py) | Setup generators H1–H8; every parameter is in the dict `P` |
| `research/p2_lookahead.py <COIN>` | Truncation test, written to `research/out/p2/lookahead_<COIN>.json` |
| `research/p2_summarize.py` | Builds the look-ahead table |
| `research/p2_stats.py` | Structure-method statistics and setup counts, written to `research/out/p2/p2_stats.md` |

Everything below was computed on the **development segment, core coins only**. It counts setups and describes structure; **no trade outcome was computed or looked at.**

### Look-ahead (truncation) test

<!-- lookahead:begin -->
Run: `research/p2_lookahead.py` once per core coin on the development segment (wall time per coin: BTC 938 s, ETH 947 s, SOL 941 s). **Overall: all detectors and generators pass; both controls are caught.**

| Item | BTC truncations / mismatches | ETH truncations / mismatches | SOL truncations / mismatches | Result |
|---|---|---|---|---|
| detector `atr14` (1h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `atr100` (1h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `fractal_k3` (1h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `dc_2.5atr` (1h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `zigzag_3pct` (1h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `breaks_dc` (1h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `breaks_fractal` (1h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `sweeps_fractal` (1h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `compression` (1h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `failed_breakout` (1h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `sd_zones` (1h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `rel_volume` (1h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `donchian24` (1h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `CONTROL_leaky_fractal` (1h) | 620 / 354 | 620 / 354 | 620 / 320 | control caught (expected) |
| detector `atr14` (4h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `atr100` (4h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `fractal_k3` (4h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `dc_2.5atr` (4h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `zigzag_3pct` (4h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `breaks_dc` (4h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `breaks_fractal` (4h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `sweeps_fractal` (4h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `compression` (4h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `failed_breakout` (4h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `sd_zones` (4h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `rel_volume` (4h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `donchian24` (4h) | 620 / 0 | 620 / 0 | 620 / 0 | pass |
| detector `CONTROL_leaky_fractal` (4h) | 620 / 319 | 620 / 316 | 620 / 313 | control caught (expected) |
| detector `daily_z` (1d) | 998 / 0 | 998 / 0 | 998 / 0 | pass |
| detector `asof_map_atr4` (1h<-4h) | 20 / 0 | 20 / 0 | 20 / 0 | pass |
| setups `H1` | 248 / 0 (277 setups) | 248 / 0 (267 setups) | 248 / 0 (213 setups) | pass |
| setups `H2` | 248 / 0 (162 setups) | 248 / 0 (185 setups) | 248 / 0 (160 setups) | pass |
| setups `H3` | 248 / 0 (180 setups) | 248 / 0 (164 setups) | 248 / 0 (157 setups) | pass |
| setups `H4` | 248 / 0 (71 setups) | 248 / 0 (66 setups) | 248 / 0 (49 setups) | pass |
| setups `H5` | 248 / 0 (210 setups) | 248 / 0 (208 setups) | 248 / 0 (226 setups) | pass |
| setups `H6` | 248 / 0 (389 setups) | 248 / 0 (439 setups) | 248 / 0 (460 setups) | pass |
| setups `H7` | 248 / 0 (100 setups) | 248 / 0 (122 setups) | 248 / 0 (111 setups) | pass |
| setups `H8` | 248 / 0 (359 setups) | 248 / 0 (313 setups) | 248 / 0 (226 setups) | pass |
| setups `H2_fractal` | 248 / 0 (315 setups) | 248 / 0 (310 setups) | 248 / 0 (322 setups) | pass |
| setups `H2_zigzag` | 248 / 0 (313 setups) | 248 / 0 (436 setups) | 248 / 0 (766 setups) | pass |
| setups `CONTROL_H1_stamped_at_open` | 248 / 44 (277 setups) | 248 / 47 (267 setups) | 248 / 42 (213 setups) | control caught (expected) |
| setups `CONTROL_H8_centered_relvol` | 248 / 223 (349 setups) | 248 / 163 (302 setups) | 248 / 147 (224 setups) | control caught (expected) |
| setups `INFO_leaky_fractal_H1` | 248 / 0 (277 setups) | 248 / 0 (267 setups) | 248 / 0 (213 setups) | info: identical setups. A fractal low at i needs bars i+1..i+3 at or above it, so knowing it early cannot change a sweep |
<!-- lookahead:end -->

How the test works:
- **Detectors:** truncated after every bar of a 600-bar window (bars 400–999, after warm-up), plus 20 random cut points across the whole development series. All output arrays are compared exactly, treating NaNs as equal.
- **Setup generators:** every timeframe is cut to the bars closed by τ, for:
  - every 1h close in one week in the middle of development (168 points);
  - 20 random τ;
  - for 15 sampled setups per hypothesis, τ at the signal time, 1 h before and after it, and 4 h before it.
  - Setups with signal time ≤ τ must match exactly, and no extra setup may appear.
- **Setup counts in this table** include the 200-day warm-up period before 2022-10-16. The frequency table further down counts development only.
- **Controls:**
  - A fractal that is "known" at its pivot bar is caught at detector level.
  - At setup level, H1 signals stamped at the bar's open and H8 using a future-inclusive volume window are both caught.
  - The leaky fractal fed into H1 gives identical setups. The fractal definition itself rules out a sweep during the confirmation window, so this is a property of H1, not a gap in the test.

### Structure-identification methods compared (section 4)

Measured on the development segment ([p2_stats.md](out/p2/p2_stats.md)). Ranges span BTC, ETH and SOL:

| Method (tf) | Swings / month | Median confirmation delay | Median swing size | How far price has moved by confirmation | Bars labelled "range" |
|---|---|---|---|---|---|
| Fractal k=3 (4h) | 36–38 | 3 bars (fixed) | 2.2–2.3 ATR | 1.2–1.3 ATR | 40–42% |
| DC 2.5×ATR (4h) | 16–19 | 3 bars (variable) | 4.1–4.2 ATR | 2.3 ATR | 30–33% |
| Zigzag 3% (4h) | **32 BTC / 46 ETH / 96 SOL** | 1 bar | 1.8–2.9 ATR | 0.9–1.6 ATR | 27–31% |
| Fractal k=3 (1h) | 144–151 | 3 bars | 2.2–2.3 ATR | 1.2–1.3 ATR | 40–43% |
| DC 2.5×ATR (1h) | 64–74 | 3–4 bars | 4.1–4.2 ATR | 2.3 ATR | 31–34% |
| Zigzag 1.5% (1h) | **117 BTC / 171 ETH / 356 SOL** | 1 bar | 1.7–2.8 ATR | 0.9–1.5 ATR | 28–33% |

How each method answers the section 4 questions:

| | Fractal k bars | ATR directional change (DC) | Fixed-% zigzag (causal) |
|---|---|---|---|
| How highs/lows are found | Bar i is a high if it exceeds the k bars before it and is ≥ the k bars after it | The running extreme becomes a swing once price reverses θ·ATR(14) from it | Same as DC, with threshold = p% of price |
| When it is known | Close of bar i+k, always | Close of the first bar whose low (high) is θ·ATR beyond the running extreme | Same as DC |
| Regime from structure | Up = last 2 highs and last 2 lows both rising; down = both falling; otherwise range | same | same |
| Noise separation | None by size, only by time. Many small swings, 40% of bars "range" | By size in volatility units. Swings are ≈4 ATR | By size in % only, so not volatility-adjusted |
| Effect of delay on entry | Fixed 3 bars, but price is already about 1.2 ATR from the pivot | Price is already ≈2.3 ATR from the pivot. The level is meaningful, but entries keyed to the *pivot* are late; entries keyed to a *break of the level* are not affected | Shortest delay, but noisiest |
| False-breakout detection | A trade beyond the level that closes back inside: the sweep detector (H1) | Same close-back-inside test on DC levels (H2/H3 cancel rules) | same |
| Across coins | Same counts on every coin; size is not normalised | **Same statistics on BTC, ETH and SOL**: normalised by construction | **Breaks across coins**: SOL produces 3× BTC's swings at the same % |

Two conclusions shape the hypotheses:
- A fixed-% threshold is not usable across coins. It is kept only as the "one global parameter" arm (approach A of section 7) in the H2 structure-method comparison. Everything else uses volatility-normalised parameters (approach B).
- DC is the main structure method. Fractal k=3 is kept where a *recent, local* level matters (H1 sweeps), and as a comparison arm for H2.

**Structural vs volatility-normalised regime** (4h DC trend vs the daily z of Phase 1, development segment):
- DC structure calls a trend on 67–70% of 4h bars; the daily z calls one on only 30–35%.
- When both call a trend, they agree in sign 75–76% of the time.
- Both are candidate *single* filters in Phase 5. Neither is applied at baseline.

### Common rules (apply to every hypothesis unless the hypothesis says otherwise)

- **C1 Timing.** Bars are UTC, stamped with their open time. A setup is evaluated at the close of its signal bar, using only bars closed by that moment. A 4h value reaches a 1h bar only after the 4h bar has closed. Higher timeframes are built from the 15m/1h base series (Phase 1 policy).
- **C2 ATR.** ATR4 / ATR1 = Wilder ATR(14) on 4h / 1h at the signal bar's close.
- **C3 Market entry.** Market order at the open of the first execution bar after the signal close. Fill = open moved 0.03% against the trade; fee 0.05% taker. If that open is already beyond the stop, the trade is skipped.
- **C4 Limit entry.**
  - The limit is placed at the signal close and stays valid for the stated window.
  - It is checked on execution bars. A long fills when a bar's low ≤ limit, at min(open, limit). A short mirrors this.
  - Fills pay taker fee plus 0.03% slippage (no maker rebate assumed).
  - If the fill bar also reaches the stop, the trade counts as stopped in that bar.
  - The order is cancelled when a stop-timeframe bar closes beyond the stated cancel level, or when the window expires.
  - If the signal close is already at or through the limit, the order is a C3 market entry instead.
- **C5 Stop bounds.**
  - Stop distance is measured from the signal close (market entries) or the limit price.
  - 4h-stop hypotheses: widened to at least 0.75×ATR4; the setup is skipped if wider than 3.0×ATR4.
  - 1h-stop hypotheses: at least 1.5×ATR1; skipped if wider than 5.0×ATR1.
  - Both minimums keep round-trip cost at or below about 0.17R on BTC, the worst core coin.
- **C6 Exits**, evaluated on execution bars (1h for 4h-stop hypotheses; 15m for 1h-stop hypotheses where 15m exists):
  - A bar that opens beyond the stop exits at the open.
  - A bar that reaches the stop exits at the stop price.
  - A bar that reaches the target exits at the target price.
  - If stop and target are both reached in one bar, the stop is assumed hit first.
  - Time exit at the close of the execution bar where the holding limit is reached.
  - All exits pay taker fee plus slippage.
- **C7 Sizing and exposure.**
  - Each hypothesis is its own book (reference equity $10,000).
  - Risk per trade 0.5% of equity; 0.25% is run as a Phase 5 sensitivity.
  - Quantity = risk ÷ |fill − stop|. Notional ≤ 3× equity.
  - Total open initial risk ≤ 2% of equity; a new signal that would exceed it is skipped.
  - One open position per coin per hypothesis. Signals on a coin that already has a position are ignored.
  - Funding: 0.01% of notional at each 00/08/16 UTC settlement while held, charged to both sides.
- **C8 Conflicts.** If a long and a short signal fire on the same coin at the same close, both are skipped.
- **C9 Segment purge.** No signal is taken if its maximum holding time would run past the segment end.

**Simulator extension needed** (stated as required by Part 2): `backend/scripts/sleeve_study/sim.py` has no limit entries (C4) and no price-level targets (H6). Phase 3 adds a thin wrapper in `research/`. The sim's fill and exit code is not modified.

### Hypotheses

The baseline target for every hypothesis except H6 is a fixed 2.0R. This is a neutral starting point, not a claim that 2R is optimal; exits are compared empirically in Phase 5. **"Market regime: all" means no regime filter at baseline.** One regime filter is tested later, alone, under the minimal-filter policy.

Frequency = setups per month across BTC+ETH+SOL in development ([p2_stats.md](out/p2/p2_stats.md)). For limit-entry hypotheses this counts orders, so trades will be fewer. Cost-in-R = 0.16% ÷ median stop distance.

| | Family | Structure method | Config / timeframes | Stop tf | Setups / month (3 coins) | Long / short | Median stop | Median cost |
|---|---|---|---|---|---|---|---|---|
| H1 | A sweep + reclaim | Fractal k=3 (4h) | Swing: 4h structure, 1h execution | 4h | 21.1 | 303 / 318 | 0.95 ATR4 (1.6%) | 0.10R |
| H2 | B BOS + first retest | DC 2.5×ATR4 | Swing | 4h | 14.6 (87% limit) | 243 / 190 | 1.1 ATR4 (2.0%) | 0.08R |
| H3 | C CHoCH + retest | DC 2.5×ATR1 | Config B: 1h structure, 15m execution | 1h | 17.0 (83% limit) | 246 / 255 | 3.6 ATR1 (3.1%) | 0.05R |
| H4 | D demand/supply zone retest | Base + impulse zones (4h) | Swing | 4h | 5.1 (all limit) | 70 / 79 | 0.75 ATR4 (1.3%) | 0.12R |
| H5 | E compression breakout | 12-bar box ≤ 3×ATR100 (4h) | Swing | 4h | 17.6 | 253 / 267 | 1.5 ATR4 (2.6%) | 0.06R |
| H6 | F failed breakout, range re-entry | 48-bar Donchian box (1h) | Config B | 1h | 43.7 | 649 / 639 | 1.5 ATR1 (1.3%) | 0.12R |
| H7 | G trend continuation, controlled retracement | DC 2.5×ATR4 + 1h trigger | Swing | 4h | 11.3 | 179 / 154 | 1.5 ATR4 (2.5%) | 0.06R |
| H8 | H volume-supported expansion | 24-bar Donchian + relative volume (1h) | Config B | 1h | 30.5 | 527 / 371 | 2.3 ATR1 (2.1%) | 0.08R |

**Trade-floor risk:** H4 has 149 development setups in total and all are limit orders. Unless at least two-thirds fill, it will miss the 100-trade floor and stop at Phase 4.

#### H1 — 4h liquidity sweep and reclaim of a fractal swing (family A)
1. **Market regime:** all.
2. **HTF context:** none.
3. **Setup:**
   - L = the most recent confirmed 4h fractal swing low (k=3), known at the close of the previous 4h bar.
   - Its pivot is at most 60 4h bars old.
   - No 4h low has traded below L since it was confirmed.
4. **Long entry condition:** the 4h signal bar has low < L − 0.1×ATR4, depth (L − low) ≤ 1.5×ATR4, and close > L.
5. **Short entry condition:** mirror, using the last swing high H: high > H + 0.1×ATR4, depth ≤ 1.5×ATR4, close < H.
6. **Order:** market at the next 1h open (C3).
7. **Initial stop:** sweep extreme ∓ 0.25×ATR4, within C5 bounds.
8. **Profit-taking:** single exit at 2.0R.
9. **Time exit:** 30 4h bars (120 h) after entry.
10. **Invalidation:** each swing level can be swept once; any trade through it consumes the level, even without a valid reclaim. After entry, only the stop.
11. **Maximum duration:** 120 h.
12. **Sizing:** C7.
13. **Filters:** none beyond the depth bounds. These define a sweep as opposed to a breakdown.

#### H2 — 4h break of structure, then first retest (family B)
1. **Market regime:** all.
2. **HTF context:** none.
3. **Setup:**
   - Bullish BOS = the first 4h close above the last confirmed DC(2.5×ATR4) swing high H, while the structural trend at the previous bar is not down.
   - A break while the trend *is* down is a CHoCH and belongs to H3-style logic.
4. **Long entry:** buy limit at H + 0.1×ATR4, valid 12 4h bars (48 h). If the BOS close is already ≤ that price, market entry instead.
5. **Short entry:** mirror at the last swing low.
6. **Order:** limit, filled on 1h bars (C4).
7. **Initial stop:** H − 1.0×ATR4 (mirror for shorts), within C5 bounds.
8. **Profit-taking:** 2.0R.
9. **Time exit:** 120 h after fill.
10. **Invalidation:** cancel the unfilled order if a 4h bar closes below H − 0.5×ATR4 (the break failed) or the 48 h window expires.
11. **Maximum duration:** 48 h waiting + 120 h in the trade.
12. **Sizing:** C7.
13. **Filters:** none.
- **Planned comparison (section 4):** the same rules with fractal k=3 swings and with a 3% zigzag. This is the only structure-method test, so it adds 2 trials.

#### H3 — 1h change of character, then retest (family C), config B
1. **Market regime:** all.
2. **HTF context:** none at baseline. The Phase 5 candidate is 4h DC trend agreement, which tests config A against config B.
3. **Setup:**
   - The 1h DC(2.5×ATR1) structure is down: the last two swing highs and last two swing lows are both lower.
   - Bullish CHoCH = the first 1h close above the last confirmed swing high LH.
4. **Long entry:** buy limit at LH + 0.1×ATR1, valid 8 h. If the CHoCH close is already ≤ that price, market entry instead.
5. **Short entry:** mirror. The structure is up, and the CHoCH is the first close below the last swing low.
6. **Order:** limit, monitored on 15m bars (C4). Only coins and dates with stored 15m data.
7. **Initial stop:** the lowest low from the LH pivot bar to the CHoCH bar, minus 0.25×ATR1, within C5 bounds (1.5–5×ATR1).
8. **Profit-taking:** 2.0R.
9. **Time exit:** 48 h after fill.
10. **Invalidation:** the order expires after 8 h. After entry, only the stop.
11. **Maximum duration:** 8 h waiting + 48 h in the trade.
12. **Sizing:** C7.
13. **Filters:** none.

#### H4 — 4h demand / supply zone, first retest (family D)
1. **Market regime:** all.
2. **HTF context:** none.
3. **Setup:**
   - The impulse 4h bar has range ≥ 2.0×ATR4 of the previous bar.
   - It closes in its top 30% (demand zone) or bottom 30% (supply zone), and beyond every base bar's extreme.
   - The base = the 1–3 bars directly before it, each with range ≤ 0.7×ATR4.
   - Demand zone = [lowest base low, highest base body top]. Supply zone = [lowest base body bottom, highest base high].
4. **Long entry:** buy limit at the zone top, valid 60 4h bars (10 days).
5. **Short entry:** sell limit at the bottom of a supply zone.
6. **Order:** limit, filled on 1h bars (C4).
7. **Initial stop:** the zone's far edge ∓ 0.25×ATR4, widened to ≥ 0.75×ATR4 from the limit, within C5 bounds.
8. **Profit-taking:** 2.0R.
9. **Time exit:** 120 h after fill.
10. **Invalidation:** cancel if a 4h bar closes beyond the zone's far edge before fill, or after 10 days. Only the first touch counts.
11. **Maximum duration:** 10 days waiting + 120 h in the trade.
12. **Sizing:** C7.
13. **Filters:** none.

#### H5 — 4h volatility compression, then breakout (family E)
1. **Market regime:** all.
2. **HTF context:** none.
3. **Setup:** the 12 4h bars before the signal bar form a box (highest high, lowest low) whose width is ≤ 3.0×ATR(100, 4h) of the previous bar.
4. **Long entry:** the signal bar closes above the box high.
5. **Short entry:** the signal bar closes below the box low.
6. **Order:** market at the next 1h open (C3).
7. **Initial stop:** box midpoint, widened to ≥ 0.75×ATR4, within C5 bounds.
8. **Profit-taking:** 2.0R. The Phase 5 exit candidate is a 2.5×ATR4 trailing stop.
9. **Time exit:** 120 h.
10. **Invalidation:** after a signal, no new H5 signal on that coin for 12 4h bars.
11. **Maximum duration:** 120 h.
12. **Sizing:** C7.
13. **Filters:** none.

#### H6 — 1h failed breakout, back into the range (family F), config B
1. **Market regime:** all at baseline. Its premise is sideways markets; the daily z "side" label is its Phase 5 filter candidate.
2. **HTF context:** none.
3. **Setup:** range = highest high and lowest low of the 48 1h bars before the breakout attempt, qualifying if its width is ≤ 7.0×ATR1.
4. **Long entry condition:**
   - The low of the signal bar, or of the previous bar if that bar closed below the range, is at least 0.1×ATR1 below the range low.
   - The signal bar closes back inside the range.
5. **Short entry condition:** mirror at the range high.
6. **Order:** market at the next 15m open (C3). Only coins and dates with stored 15m data.
7. **Initial stop:** breakout extreme ∓ 0.25×ATR1, within C5 bounds (1.5–5×ATR1).
8. **Profit-taking:** the range midpoint (a price-level target). The setup is skipped if the midpoint is less than 1.0R away.
9. **Time exit:** 24 h.
10. **Invalidation:** none after entry beyond the stop.
11. **Maximum duration:** 24 h.
12. **Sizing:** C7.
13. **Filters:** the width ≤ 7×ATR1 condition defines "range". Nothing else.

#### H7 — 4h trend continuation after a controlled retracement, 1h trigger (family G)
1. **Market regime:** the 4h DC(2.5×ATR4) structure must be up (higher highs and higher lows) for longs, down for shorts. This is part of the setup's definition, not an added filter.
2. **HTF context:** the 4h structure, mapped to 1h after each 4h close.
3. **Setup:**
   - The most recent confirmed 4h swing is a swing high H in an uptrend.
   - L0 = the swing low before it; leg = H − L0.
   - The setup is armed from the 1h bar that opens after H is confirmed, for 120 h.
4. **Long entry condition:**
   - Since the H pivot, the lowest 1h low has retraced at least 38.2% of the leg.
   - No 1h close has gone below the 78.6% retracement level.
   - The 1h signal bar closes above the previous 1h bar's high, and below H.
5. **Short entry:** mirror in a downtrend.
6. **Order:** market at the next 1h open (C3).
7. **Initial stop:** the lowest low since H minus 0.5×ATR4, within C5 bounds.
8. **Profit-taking:** 2.0R. The Phase 5 exit candidate is a structure target at H.
9. **Time exit:** 120 h.
10. **Invalidation:** a 1h close below the 78.6% level, or 120 h passing without a trigger, kills the setup. One trade per swing.
11. **Maximum duration:** 120 h waiting + 120 h in the trade.
12. **Sizing:** C7.
13. **Filters:** none beyond the definition.

#### H8 — 1h volume-supported expansion out of structure (family H), config B
1. **Market regime:** all.
2. **HTF context:** none.
3. **Setup:**
   - The 1h signal bar's range is ≥ 2.0×ATR1 of the previous bar.
   - Its volume is ≥ 2.5× the median volume of the previous 168 1h bars (the coin's own last 7 days).
4. **Long entry:** the bar closes above the highest high of the previous 24 1h bars, and in the top 25% of its own range.
5. **Short entry:** mirror, below the 24-bar low and in the bottom 25%.
6. **Order:** market at the next 15m open (C3). Only coins and dates with stored 15m data.
7. **Initial stop:** the signal bar's low − 0.1×ATR1 (high + 0.1×ATR1 for shorts), within C5 bounds.
8. **Profit-taking:** 2.0R.
9. **Time exit:** 24 h.
10. **Invalidation:** none after entry beyond the stop.
11. **Maximum duration:** 24 h.
12. **Sizing:** C7.
13. **Filters:** the relative-volume threshold is part of the definition. Nothing else.

### Baselines (section 10)

- **Random-entry null, per hypothesis:**
  - Same coins, same segment, same long/short mix.
  - The stop distance (in ATR) is drawn from that hypothesis's own setups, with the same target and time exit.
  - Entry bars are drawn uniformly.
  - 500 draws give a null distribution of net expectancy in R.
- **Buy-and-hold** per core coin, for context on the long side.

### Phase 3–6 plan and pre-declared pass rules

**Phase 3 trials** (each logged to `research/experiments.csv`):
- 8 baselines on core coins, development segment, each coin and pooled.
- 2 H2 structure-method variants.
- That is 10 trials, plus the random-entry nulls, which are not counted as trials.

**Phase 4, "promising"** (all must hold, development segment, core coins pooled):
1. At least 100 trades.
2. Net expectancy ≥ +0.10R.
3. The **95%** block-bootstrap lower bound on net expectancy is > 0. The bootstrap resamples weekly clusters of trades by entry week, 5,000 times, and uses the 2.5th percentile. (Tightened from 90% at Phase 2 approval.)
4. Expectancy is positive on at least 2 of the 3 core coins.
5. It beats the random-entry null at one-sided p < 0.05.
6. Deflated Sharpe probability ≥ 0.90.
   - Uses per-trade net R, with the Bailey & López de Prado correction for skew and kurtosis.
   - N = **every distinct trial and parameter variant logged in `research/experiments.csv`** (each hypothesis × variant configuration, across all phases), not only the 10 named trials.
   - The variance of Sharpe across trials comes from the same file. (Clarified at Phase 2 approval.)

**Phase 5:** one change at a time, from this menu only:
- A regime filter: daily z, or 4h DC trend agreement (config A vs B).
- Exits: 2R vs structure target vs 2.5×ATR4 trail; partial vs single exit.
- Risk: 0.25% vs 0.5%.

A change is kept only if it improves validation-segment expectancy on the core coins *and* does not lower Tier-2 expectancy (development + validation). **To pass Phase 5**, the strategy (with any kept change) must have a 95% block-bootstrap lower bound on net expectancy > 0 in the validation segment, core coins pooled. (Added at Phase 2 approval.)

**Phase 6:**
- Expanding walk-forward with 6-month test folds over development + validation; at least 60% of folds must be positive.
- Then one OOS run on core + Tier 2. Pass = net expectancy > 0 and profit factor ≥ 1.1 there.

**Phase 7:** up to 3 finalists, with funding sensitivity at 0% and 0.02% per 8h.

### Compute used

| Phase | Wall time | What ran |
|---|---|---|
| 2 (not in the 4 h budget) | ≈ 22 min | look-ahead tests: 3 coins in parallel, ~16 min each plus ~5 min for the controls; descriptive stats < 1 min |
| 3–7 | ≈ 3 min of 4 h | see the Phase 7 tally |

---

## Phase 3 — Baseline backtests

Scripts:

| Script | What it does |
|---|---|
| [research/engine.py](engine.py) | Trade resolver and portfolio book |
| [research/rstats.py](rstats.py) | Metrics, bootstrap, deflated Sharpe, random-entry null |
| [research/runner.py](runner.py) | Runs one configuration and logs it |
| [research/p3_baselines.py](p3_baselines.py) | The Phase 3 driver. Writes `research/out/p3/p3_results.json`, `trades_*.csv` and rows in `research/experiments.csv` |

**Engine check.** The engine adds limit entries (C4), price-level targets (H6) and 15m execution, which `backend/scripts/sleeve_study/sim.py` lacks. Its market-entry path was checked against sim.py with `research/p3_parity.py`: H1 and H5 on BTC, development segment, funding off. All **313/313 trades matched**, with identical exit times and a max |ΔR| of 4.4e-16 (`research/out/p3/parity.csv`). I also checked the limit-entry handling by inspecting trades: fills at the limit or a better open, and stopped trades at −1.06 to −1.22R after costs.

**Results.** 10 trials, core coins, development segment, all baseline rules exactly as approved:

| Config | Setups | Trades | Gross R | Cost R | Net R | 95% bootstrap | PF | BTC / ETH / SOL net R | Long / short net R | Random-entry null R (p) |
|---|---|---|---|---|---|---|---|---|---|---|
| H1 | 617 | 496 | −0.037 | 0.079 | −0.116 | [−0.232, +0.003] | 0.83 | −0.283 / +0.023 / −0.106 | −0.112 / −0.120 | −0.107 (0.55) |
| H2 | 432 | 343 | −0.063 | 0.066 | −0.128 | [−0.279, +0.020] | 0.82 | −0.256 / −0.231 / +0.119 | −0.024 / −0.251 | −0.091 (0.69) |
| H2:fractal | 767 | 570 | −0.104 | 0.069 | −0.174 | [−0.294, −0.055] | 0.74 | −0.181 / −0.295 / −0.049 | −0.073 / −0.281 | −0.089 (0.92) |
| H2:zigzag | 1135 | 742 | −0.051 | 0.054 | −0.105 | [−0.217, +0.007] | 0.84 | −0.238 / −0.163 / −0.011 | −0.080 / −0.136 | −0.070 (0.77) |
| H3 | 499 | 352 | −0.004 | 0.047 | −0.051 | [−0.168, +0.069] | 0.88 | −0.030 / −0.175 / +0.054 | +0.017 / −0.108 | −0.062 (0.43) |
| H4 | 146 | 112 | −0.477 | 0.097 | −0.573 | [−0.805, −0.314] | 0.36 | −0.576 / −0.660 / −0.447 | −0.507 / −0.629 | −0.129 (1.00) |
| H5 | 518 | 437 | +0.063 | 0.060 | +0.003 | [−0.133, +0.140] | 1.00 | −0.092 / +0.022 / +0.081 | +0.003 / +0.004 | −0.077 (0.11) |
| H6 | 1286 | 1028 | −0.065 | 0.085 | −0.150 | [−0.238, −0.060] | 0.75 | −0.210 / −0.110 / −0.135 | −0.132 / −0.168 | −0.128 (0.73) |
| H7 | 332 | 303 | −0.085 | 0.059 | −0.144 | [−0.290, +0.003] | 0.77 | −0.278 / −0.258 / +0.108 | −0.089 / −0.205 | −0.065 (0.86) |
| H8 | 898 | 758 | +0.058 | 0.058 | +0.001 | [−0.110, +0.114] | 0.99 | +0.024 / −0.044 / +0.028 | −0.026 / +0.037 | −0.078 (**0.04**) |

Notes on the table:
- **Setups** fall to **trades** because of limit orders that never filled (H2 79, H3 99, H4 33, H2:fractal 128, H2:zigzag 155). The C4 cancel rules never triggered, because price must trade through the limit before it can close beyond the cancel level, the one-position-per-coin rule, and the 2% open-risk cap.
- **Null** = 300 sets of random-entry trades with the same coin, side, stop %, target and hold mix (C3 market entries). Its mean is negative because random entries pay the same costs.
- Full metrics, including win rate, average win/loss, consecutive losses, monthly distribution, exposure and Sharpe per trade, are in `research/out/p4/p4_tables.md`.

**What Phase 3 shows:**
1. **No hypothesis has a gross edge large enough to pay for trading.** The best gross expectancies are +0.063R (H5) and +0.058R (H8), and they are almost exactly cancelled by costs of 0.060R and 0.058R.
2. **H8 is the only setup measurably better than random entry** (p = 0.04, about +0.08R over the null). That edge is the same size as the cost, so net expectancy is zero.
3. **H4 is clearly negative** (−0.57R): 4h demand/supply zone retests are broken through far more often than they hold.
4. **Structure method (section 4, H2):**
   - DC (−0.128R) and zigzag (−0.105R) are statistically indistinguishable; their intervals overlap almost entirely.
   - Fractal is worst (−0.174R).
   - None is positive, so the evidence does not let me pick a "best" structure method; all three fail.
5. **Shorts are worse than longs** in 7 of 10 configurations. This fits a development period in which BTC rose 331% and SOL 318%.

## Phase 4 — Statistical comparison and gates

Script: [research/p4_compare.py](p4_compare.py), written to `research/out/p4/p4_tables.md` and `p4_gates.json`. Deflated Sharpe: N = 10 distinct configurations in `research/experiments.csv` (all that were logged; Tier-2 rows reuse the same configurations), with a variance of per-trade Sharpe across trials of 0.0200.

| Config | ≥100 trades | Net ≥ +0.10R | 95% boot lo > 0 | ≥2 of 3 coins positive | Beats null p<0.05 | DSR ≥ 0.90 | Promising |
|---|---|---|---|---|---|---|---|
| H1 | ✓ 496 | ✗ −0.116 | ✗ −0.232 | ✗ 1 | ✗ 0.55 | ✗ 0.00 | **no** |
| H2 | ✓ 343 | ✗ −0.128 | ✗ −0.279 | ✗ 1 | ✗ 0.69 | ✗ 0.00 | **no** |
| H2:fractal | ✓ 570 | ✗ −0.174 | ✗ −0.294 | ✗ 0 | ✗ 0.92 | ✗ 0.00 | **no** |
| H2:zigzag | ✓ 742 | ✗ −0.105 | ✗ −0.217 | ✗ 0 | ✗ 0.77 | ✗ 0.00 | **no** |
| H3 | ✓ 352 | ✗ −0.051 | ✗ −0.168 | ✗ 1 | ✗ 0.43 | ✗ 0.00 | **no** |
| H4 | ✓ 112 | ✗ −0.573 | ✗ −0.805 | ✗ 0 | ✗ 1.00 | ✗ 0.00 | **no** (dropped) |
| H5 | ✓ 437 | ✗ +0.003 | ✗ −0.133 | ✓ 2 | ✗ 0.11 | ✗ 0.00 | **no** |
| H6 | ✓ 1028 | ✗ −0.150 | ✗ −0.238 | ✗ 0 | ✗ 0.73 | ✗ 0.00 | **no** |
| H7 | ✓ 303 | ✗ −0.144 | ✗ −0.290 | ✗ 1 | ✗ 0.86 | ✗ 0.00 | **no** |
| H8 | ✓ 758 | ✗ +0.001 | ✗ −0.110 | ✓ 2 | ✓ 0.04 | ✗ 0.00 | **no** |

**Cross-coin (Tier 2, development, same rules, never fitted).** Net expectancy in R (trades):

| H1 | H2 | H2:fractal | H2:zigzag | H3 | H4 | H5 | H6 | H7 | H8 |
|---|---|---|---|---|---|---|---|---|---|
| −0.055 (1309) | −0.102 (963) | −0.111 (1468) | −0.095 (1769) | −0.002 (632) | −0.200 (242) | −0.077 (818) | −0.104 (2123) | −0.087 (763) | −0.015 (962) |

All ten are negative on 11 other coins too, so the failure is not specific to BTC, ETH or SOL. H5's flat core result turns negative on Tier 2.

**Robustness of the negative result** (`research/p4_ambiguity.py`, written to `research/out/p4/ambiguity_bound.csv`):
- The stop-first rule for bars that hit both stop and target affected only H4 (9 trades), H2 (2), H2:fractal (2) and H2:zigzag (1).
- Optimistic upper bound: score those bars as target hits *and* set every fee, slippage and funding cost to zero. The best result is still H5 at +0.063R, below the +0.10R gate.
- The conclusion therefore does not depend on the conservative execution assumptions.

**Baselines for context** (development segment, buy and hold): BTC +331%, ETH +42%, SOL +318%. Over the same period, every strategy book ended between −55% (H6) and −0.6% (H5) at 0.5% risk per trade.

**Regime and direction (descriptive only, not used for any selection).**
- Splitting net R by the daily-z regime at signal time and by direction gives cells of 3–376 trades (table in `p4_tables.md`).
- A few cells are positive, for example H3 longs in bull regimes at +0.39R (24 trades) and H8 shorts in sideways regimes at +0.15R (229 trades).
- These were found after looking at outcomes. Trading only those cells would be exactly the regime filter Phase 5 exists to test, and Phase 5 is open only to strategies that passed Phase 4. They are recorded as **hypotheses for a future study**, not results.

## Phase 5 — Minimal improvement

**Not run.** Phase 5 is limited to strategies that passed Phase 4, and none did. Tuning H5 or H8 (net ≈ 0, deflated Sharpe 0.00) toward the +0.10R gate after seeing the development results is the kind of data-mining the multiple-testing rules exist to prevent. The validation segment (2025-04-08 → 2025-12-31) was therefore **not used**.

## Phase 6 — Out-of-sample validation

**Not run.** There were no finalists. The OOS segment (2026-01-08 → 2026-10-14) was **never read**: the `RESEARCH_ALLOW_OOS` guard in `research/common.py` was never set. It remains clean for this study's rules and can serve as the one-shot test for a future pre-registered study.

## Phase 7 — Final selection

**No strategy is recommended for paper trading.** Funding sensitivity (0% / 0.02% per 8h) was reserved for finalists; there are none. The zero-cost bound above already shows that even 0% funding would not change any verdict.

What the evidence supports:
- On Binance USDⓈ-M perpetuals, with realistic costs (0.16% round trip plus funding), none of the eight market-structure families has a net edge on BTC, ETH or SOL from 2022-10 to 2025-03, nor on 11 other liquid coins. The families are sweep/reclaim, BOS retest, CHoCH, S/D zones, compression breakout, failed breakout, trend pullback and volume expansion. All were run at 4h/1h or 1h/15m with stops on 1h or 4h.
- The two best families (H5 compression breakout, H8 volume expansion) have a gross edge of about +0.06R, roughly equal to costs. H8's edge over random entry is statistically detectable (p = 0.04) but economically zero after fees.
- This agrees with the earlier studies recorded for this repo (strategy search, study 2, sleeve study, Regime Lab), none of which passed their own final tests.

If you continue, these would make a meaningful new test (each needs a fresh pre-registration):
1. Cut costs rather than search for more signal. Maker entries (limit orders paying a maker rebate rather than the taker fee assumed here) would roughly halve costs. That would put H5 and H8 at about +0.03R, which is still below the gate.
2. Pre-register one of the descriptive regime cells, e.g. "H8 shorts only in daily-z sideways regimes". Test it on the validation segment once, then on OOS once. It was found after looking, so expect it to shrink.
3. Otherwise, treat this as a reason not to deploy a discretionary-structure bot on these coins at these costs.

### Compute tally (Phases 3–7; budget 4 h)

| Run | Wall time |
|---|---|
| `p3_parity.py` (engine vs sim.py) | 2.5 s |
| `p3_baselines.py` (10 trials + random nulls) | 1 m 41 s |
| `p4_compare.py`, first attempt (crashed on a Tier-2 NaN in H7; fixed with a guard that cannot change core results) | 31 s |
| `p4_compare.py` (gates, Tier-2 × 10, buy and hold, regime table) | 39 s |
| `p4_ambiguity.py` | 1.4 s |
| **Total** | **≈ 2 m 55 s of 4 h** |

Per-run seconds are also logged in `research/out/compute_log.csv`. `research/experiments.csv` holds 160 rows: 10 configurations × (pooled + per coin) × (core development + Tier-2 development).

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

**Amendment, recorded after Track A ran and before any Track B result was computed.**
- In Track A, the registered variance of per-trade Sharpe was 9.49, which makes SR0 = 7.40 per trade, a meaningless threshold.
- The cause is one 3-trade cell (H4 longs in bear regimes, Sharpe −25.9). Its three losses were nearly identical, so the standard deviation was tiny.
- For Track B, and for the Track A sensitivity row, the Sharpe variance is therefore taken over development-core configurations with **at least 30 trades**. N still counts every configuration.
- This does not change any Track A verdict: Track A fails the expectancy and bootstrap gates either way.

## Phase 2b — Results

Pre-registration: the section above, frozen as `research/out/p2b/PREREG.md` (sha256 `7d75506c…80c3c3`), written before any Phase 2b result existed.

### Track A — regime subsets, one validation run each

Script: `research/p2b_trackA.py`, written to `research/out/p2b/trackA.json`. Validation segment 2025-04-08 → 2025-12-31, core + Tier 2 (14 coins), run once, never on development.

| ID | Subset | Development R (seen) | Validation trades | Validation net R | Gross R | 95% bootstrap | PF | Null p | DSR (registered / ≥30-trade variance) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| A1 | H2 longs, side regime | +0.176 | 137 | **−0.003** | +0.058 | [−0.388, +0.361] | 0.98 | 0.35 | 0.00 / 0.00 | **fail** |
| A2 | H8 shorts, side regime | +0.152 | 202 | **−0.174** | −0.122 | [−0.395, +0.074] | 0.68 | 0.88 | 0.00 / 0.00 | **fail** |
| A3 | H5 shorts, side regime | +0.099 | 154 | **+0.069** | +0.121 | [−0.245, +0.412] | 1.11 | 0.13 | 0.00 / 0.00 | **fail** |

- All three development "edges" shrank toward or below zero on unseen data, as expected for subsets chosen after looking. A2, the largest development sample, reversed.
- None gets an OOS run.
- DSR as registered: N = 70, Sharpe variance 9.49, SR0 = 7.40 (degenerate; see the amendment). With the variance from configurations with ≥ 30 trades (0.028): SR0 = 0.40, DSR still 0.00 for all three (`research/p2b_trackA_dsr.py`).
- **The validation segment has now been read**, once, for A1–A3 only.

### Track B — cost-ratio hypotheses, development segment, core coins

Script: `research/p2b_trackB.py` (+ [research/tsmom.py](tsmom.py)), written to `research/out/p2b/trackB_tables.md` and `trackB.json`. Gates are the Phase 4 set. DSR N = 85 configurations in `experiments.csv`, Sharpe variance 0.0277. TSMOM drawdown is marked to market at every bar close; the others are on closed trades.

| Config | Trades | /month | Gross R | Cost R | Net R | 95% bootstrap | PF | Net return | Max DD | Coins + | Null p | DSR | Fill | Long / short R | Pass |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 TSMOM 1d N20 | 286 | 9.7 | 0.162 | 0.043 | **+0.120** | [−0.102, +0.402] | 1.18 | +26% | 33% | 2 | 0.02 | 0.00 | – | +0.39 / −0.15 | ✗ |
| B1 TSMOM 1d N40 | 196 | 6.6 | 0.385 | 0.060 | **+0.325** | [−0.114, +0.939] | 1.51 | +76% | 38% | 3 | 0.007 | 0.00 | – | +0.78 / −0.12 | ✗ |
| B1 TSMOM 1d N60 | 177 | 6.0 | 0.370 | 0.069 | **+0.301** | [−0.158, +0.898] | 1.50 | +68% | 35% | 3 | 0.007 | 0.00 | – | +0.81 / −0.19 | ✗ |
| B1 TSMOM 4h N20 | 1745 | 59.2 | 0.070 | 0.046 | +0.024 | [−0.065, +0.128] | 1.09 | +41% | 32% | 2 | 0.01 | 0.00 | – | +0.11 / −0.06 | ✗ |
| B1 TSMOM 4h N40 | 1065 | 36.1 | 0.192 | 0.055 | **+0.136** | [−0.033, +0.346] | 1.32 | +154% | 35% | 3 | 0.003 | 0.00 | – | +0.32 / −0.04 | ✗ |
| B1 TSMOM 4h N60 | 903 | 30.6 | 0.118 | 0.061 | +0.057 | [−0.102, +0.238] | 1.15 | +48% | 38% | 3 | 0.02 | 0.00 | – | +0.26 / −0.14 | ✗ |
| B2 H5, 1.5×ATR4 stop, 3R, 10 days | 386 | 13.1 | 0.019 | 0.069 | −0.050 | [−0.226, +0.132] | 0.92 | −11% | 18% | 1 | 0.37 | 0.00 | – | +0.01 / −0.10 | ✗ |
| B2 H5, 1.5×ATR4 stop, 5R, 10 days | 354 | 12.0 | 0.027 | 0.079 | −0.052 | [−0.276, +0.186] | 0.92 | −11% | 28% | 1 | 0.46 | 0.00 | – | +0.10 / −0.19 | ✗ |
| B2 H8, 1.5×ATR4 stop, 3R, 10 days | 538 | 18.2 | 0.214 | 0.065 | **+0.149** | [−0.036, +0.338] | 1.20 | +44% | 11% | 3 | 0.007 | 0.00 | – | +0.18 / +0.11 | ✗ |
| B2 H8, 1.5×ATR4 stop, 5R, 10 days | 448 | 15.2 | 0.269 | 0.078 | **+0.191** | [−0.046, +0.447] | 1.22 | +46% | 11% | 2 | 0.01 | 0.00 | – | +0.28 / +0.06 | ✗ |
| B3 H5 maker entry | 438 | 14.9 | 0.081 | 0.048 | +0.034 | [−0.106, +0.174] | 1.05 | +6% | 10% | 2 | 0.04 | 0.00 | 99.4% | +0.03 / +0.04 | ✗ |
| B3 H8 maker entry | 756 | 25.6 | 0.069 | 0.044 | +0.026 | [−0.087, +0.140] | 1.04 | +8% | 19% | 2 | 0.003 | 0.00 | 99.7% | −0.00 / +0.07 | ✗ |

Buy and hold over the same development segment: BTC +331%, ETH +42%, SOL +318%.

**No Track B configuration passes the Phase 4 gates.** Six of the twelve clear +0.10R and beat random entry (p ≤ 0.02). All six fail the 95% bootstrap gate, with lower bounds of −0.03 to −0.16, and all twelve have DSR 0.00 at N = 85. Nothing proceeds to validation or OOS, and Track B never read either segment.

Notes:
- **TSMOM** earned its development profit on the long side of a strong bull market. Shorts lost at every N. Each book trailed buy-and-hold on BTC and SOL, with 32–38% marked-to-market drawdowns.
- **B3** shows what maker entry actually buys. It cut cost from 0.060 to 0.048R (H5) and from 0.058 to 0.044R (H8), and nearly every order filled, but net expectancy rose only to +0.03R.
- **B2** helped H8 but not H5. Holding H8 for up to 10 days with a wider 4h-ATR stop raised its gross edge from 0.058R to 0.21–0.27R. The same change did nothing for H5 (gross 0.02–0.03R).

### Comparison table, all tracks (net of all costs; none ranked because none passed)

| Strategy | Segment / coins | Direction | Timeframes | Trades/month | Net expectancy [95% bootstrap] | PF | Max DD | Status |
|---|---|---|---|---|---|---|---|---|
| B1 TSMOM 1d N40 | dev / core | long + short | 1d | 6.6 | +0.325R [−0.114, +0.939] | 1.51 | 38% (MTM) | failed Phase 4 (bootstrap, DSR) |
| B1 TSMOM 1d N60 | dev / core | long + short | 1d | 6.0 | +0.301R [−0.158, +0.898] | 1.50 | 35% (MTM) | failed Phase 4 (bootstrap, DSR) |
| B2 H8, 1.5×ATR4 stop, 5R, 10 days | dev / core | long + short | 1h / 15m | 15.2 | +0.191R [−0.046, +0.447] | 1.22 | 11% | failed Phase 4 (bootstrap, DSR) |
| B2 H8, 1.5×ATR4 stop, 3R, 10 days | dev / core | long + short | 1h / 15m | 18.2 | +0.149R [−0.036, +0.338] | 1.20 | 11% | failed Phase 4 (bootstrap, DSR) |
| B1 TSMOM 4h N40 | dev / core | long + short | 4h | 36.1 | +0.136R [−0.033, +0.346] | 1.32 | 35% (MTM) | failed Phase 4 (bootstrap, DSR) |
| B1 TSMOM 1d N20 | dev / core | long + short | 1d | 9.7 | +0.120R [−0.102, +0.402] | 1.18 | 33% (MTM) | failed Phase 4 (bootstrap, DSR) |
| A3 H5 shorts, side regime | **val** / core+T2 | short | 4h / 1h | – | +0.069R [−0.245, +0.412] | 1.11 | 10% | failed validation |
| B1 TSMOM 4h N60 | dev / core | long + short | 4h | 30.6 | +0.057R | 1.15 | 38% (MTM) | failed Phase 4 |
| B3 H5 maker | dev / core | long + short | 4h / 1h | 14.9 | +0.034R | 1.05 | 10% | failed Phase 4 |
| B3 H8 maker | dev / core | long + short | 1h / 15m | 25.6 | +0.026R | 1.04 | 19% | failed Phase 4 |
| B1 TSMOM 4h N20 | dev / core | long + short | 4h | 59.2 | +0.024R | 1.09 | 32% (MTM) | failed Phase 4 |
| H5 compression breakout | dev / core | long + short | 4h / 1h | 14.8 | +0.003R | 1.00 | 11% | failed Phase 4 |
| H8 volume expansion | dev / core | long + short | 1h / 15m | 25.7 | +0.001R | 0.99 | 23% | failed Phase 4 |
| A1 H2 longs, side regime | **val** / core+T2 | long | 4h / 1h | – | −0.003R [−0.388, +0.361] | 0.98 | 11% | failed validation |
| B2 H5, 3R / 5R, 10 days | dev / core | long + short | 4h / 1h | 12–13 | −0.050 / −0.052R | 0.92 | 18–28% | failed Phase 4 |
| H3, H2 (all 3 structure methods), H1, H7, H6 | dev / core | long + short | various | 10–35 | −0.05 to −0.17R | 0.74–0.88 | 18–58% | failed Phase 4 |
| A2 H8 shorts, side regime | **val** / core+T2 | short | 1h / 15m | – | −0.174R [−0.395, +0.074] | 0.68 | 18% | failed validation |
| H4 demand/supply zone | dev / core | long + short | 4h / 1h | 3.8 | −0.573R | 0.36 | 28% | dropped at Phase 4 |

The OOS segment (2026-01-08 → 2026-10-14) has **still never been read**.

### Phase 2b compute tally (cap 2 h)

| Run | Wall time |
|---|---|
| `p2b_cells.py` (log the 60 seen cells) | 4.8 s |
| `p2b_trackA.py` (3 subsets, validation, with nulls) | 35.8 s |
| `p2b_trackA_dsr.py` | ≈ 1 s |
| `p3_parity.py` re-check after the engine change | 2.5 s |
| `p2b_trackB.py` (12 trials, nulls, gates) | 1 m 38 s |
| **Total** | **≈ 2 m 22 s of 2 h** |

Across the whole study: Phases 3–7 ≈ 2 m 55 s, plus Phase 2b ≈ 2 m 22 s. `experiments.csv` now holds 85 distinct configurations.

### Verdict: cost ratio or entry logic?

**The entry logic is the binding constraint, not the cost ratio.**
- Costs run at 0.04–0.08R per trade in every configuration tested. Cutting them sharply (maker entry, no entry slippage, B3) moved net expectancy by only about +0.03R, because the structure entries' gross edge is only about 0.06–0.08R.
- Where net expectancy did clear +0.10R, it came from a larger *gross* edge, not lower costs:
  - letting H8 trades run up to 10 days behind a wider stop (gross 0.21–0.27R);
  - riding daily trends with TSMOM (gross 0.16–0.38R).
  Costs stayed a small fraction of those gross edges.
- Even those cases are not distinguishable from zero once trade clustering is respected. Their 95% weekly-block bootstrap intervals all include zero, and their deflated Sharpe is 0 after 85 configurations.
- TSMOM's edge comes entirely from the long side of a 2023–24 bull market that buy-and-hold captured better.
- The post-hoc regime subsets (Track A) did not survive one look at unseen data.

What would have to change is the *source* of gross edge, at the payoff and holding-period level, not execution costs. On this evidence nothing here is ready for validation, let alone paper trading.

**Nothing passed. Per instructions, the study stops here.**
