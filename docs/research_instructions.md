# Crypto Multi-Timeframe Strategy Discovery — Claude Code Prompt

Before starting, save this entire file into the repo as `docs/research_instructions.md` so you can re-read it in later sessions. Then begin at "START HERE" at the bottom.

---

## PART 1 — PROJECT CONTEXT (fill in before running)

- Repo root: `[path]`
- OHLCV data: `[path, e.g. data/ohlcv/{symbol}/{timeframe}.parquet]` — format: `[parquet/csv]`, timestamps in `[UTC, candle open/close]`
- Existing backtester entry point: `[path/module and how to invoke it]`
- Existing research notebooks/scripts worth reusing: `[paths, or "none"]`
- Python env: `[uv/poetry/venv command to activate]`; run tests with `[command]`
- Exchange/venue the data came from: `[e.g. Binance USDT-M perps]` — if unknown, infer from the data and state your evidence
- Fee/slippage assumptions to use unless the data contradicts them: taker `[0.05%]`, slippage `[0.03%]` per side, funding `[use historical if available, else 0.01%/8h flat]`
- Trade count floor for advancing a strategy past Phase 4: `[100]` trades in the development set across coins
- Compute budget for Phases 3–7: `[N]` hours

---

## PART 2 — WORKING RULES FOR CLAUDE CODE

### Clarification of the "no coding" rule in Part 3

- Do NOT build a trading bot, redesign the backtester, add UI, or change the database.
- DO write whatever research scripts, structure detectors, and backtest configs are needed to produce real evidence. Put them under `research/` and keep them disposable.
- Reuse the existing backtester wherever it fits. Extend it only if a required feature (e.g. intrabar stop/target ambiguity, funding) is missing, and say so.

### Rules

1. **Checkpoints.** Stop and wait for my approval after Phase 1 (data audit) and after Phase 2 (strategy hypotheses with exact rules). Do not start backtesting until I approve the hypotheses. After that, run Phases 3–7 without stopping unless you hit a blocker.
2. **Write results to files, not chat.** Keep a running `research/REPORT.md` with the required comparison table and one section per strategy. Update it at the end of each phase. In chat, give me a 5–10 line summary and the path.
3. **Never fabricate numbers.** Every figure in the report must come from a script that exists in the repo and can be re-run. Name the script next to each table. If something couldn't be tested, write "not tested" and why.
4. **Causality is non-negotiable.** Before any backtest, write and run a look-ahead test for each structure detector (truncate the data at each bar and confirm no earlier signal changes when future candles are removed). Record the result in the report.
5. **Data splits are fixed up front.** In Phase 1, propose exact date ranges for development / validation / untouched OOS. Lock them after my approval. Do not touch the OOS range until Phase 6.
6. **Multiple-testing discipline.** Log every strategy × parameter × coin combination you run in `research/experiments.csv` with results. Apply a deflated Sharpe or similar correction before calling anything "promising."
7. **Trade count floor.** Do not advance any strategy to Phase 5 with fewer trades than the floor in Part 1.
8. **Budget.** Aim to finish Phases 1–2 in one session. If Phases 3–7 are going to exceed the compute budget, or you are looping on the same problem, stop and report rather than grinding.
9. **If nothing passes, say so.** A clean negative result with the evidence is a valid final output.

---

## PART 3 — RESEARCH INSTRUCTIONS

### ROLE

Act as a senior quantitative crypto trading researcher, systematic strategy designer, and market-structure specialist.

Your objective is to discover, compare, and validate automated crypto trading strategies using my existing historical OHLCV datasets.

Important: Strategy research first. No bot implementation, architecture redesign, or unnecessary lengthy research (see Part 2 for what code is allowed).

Do not recommend strategies as profitable without actual backtesting evidence.

### 1. AVAILABLE DATA

- Approximately 4 years of historical OHLCV data.
- Timeframes: 15m, 1h, 4h, 1d.
- Markets: BTC, ETH, SOL, BNB, XRP, and other major liquid crypto pairs.
- Trading styles: Intraday and short swing trading.
- Direction: Long and short.
- Execution: Fully automated Python trading bot in a later phase.

Use the existing datasets and available backtesting system where possible.

First identify the exact available symbols, date coverage, missing candles, timeframe alignment, and whether the OHLCV represents spot or perpetual futures.

Do not request new datasets unless existing data is insufficient.

### 2. MAIN OBJECTIVE

Discover strategies that:

1. Generate clear and frequent enough signals for practical automated trading.
2. Have positive net expectancy after realistic fees, slippage, and funding where applicable.
3. Work across several liquid coins or clearly identify the coins on which they work.
4. Adapt to bullish, bearish, and sideways conditions.
5. Avoid unnecessary confirmation filters.
6. Have objectively defined entry, stop-loss, take-profit, and invalidation rules.
7. Can be evaluated without look-ahead bias.
8. Remain reasonably stable on unseen data.

Do not force a strategy to achieve a target return. Report actual evidence and uncertainty.

### 3. RESEARCH PRIORITIES

Research the following strategy families first:

- A. Liquidity sweep + market-structure reclaim.
- B. Break of Structure (BOS) + first pullback or retest.
- C. Change of Character (CHoCH) + confirmed reversal.
- D. Supply/demand zone retest with structural invalidation.
- E. Volatility compression + directional breakout.
- F. Failed breakout + range re-entry.
- G. Trend continuation after a controlled retracement.
- H. Volume-supported expansion from a meaningful price structure.

Also consider a small number of well-established systematic strategies that can be implemented using OHLCV alone.

Do not waste time on isolated RSI, MACD, EMA crossover, or similar basic indicator strategies. Indicators may support a strategy, but should not be the entire strategy.

### 4. MARKET STRUCTURE IDENTIFICATION

This is a major research priority. Compare these methods:

1. Confirmed swing highs and swing lows.
2. ATR-normalized directional-change structure.
3. Causally confirmed ZigZag-style structure.
4. Break of Structure using meaningful structural levels.
5. CHoCH with clear prior-trend definitions.
6. Liquidity sweep followed by reclaim or rejection.
7. Range highs/lows and volatility-based boundaries.

For each method explain:

- How highs and lows are identified.
- When the information becomes available in real time.
- How bullish, bearish, and sideways regimes are classified.
- How minor noise is separated from meaningful structure.
- How confirmation delay affects entry quality.
- How false breakouts are detected.
- How it behaves across coins with different volatility.

Never use future candles to create an earlier signal. A pivot must not become tradable before its actual confirmation.

Prefer a small number of meaningful structural conditions over many indicators.

Select the best structure method based on out-of-sample results, signal delay, and robustness rather than visual appearance.

### 5. MULTI-TIMEFRAME DESIGN

Compare these configurations:

**Intraday configuration A** — 4h: market regime. 1h: important structure and liquidity. 15m: entry and execution.

**Intraday configuration B** — 1h: market regime and structure. 15m: entry and execution.

**Swing configuration** — 1d: optional macro context. 4h: structure and directional bias. 1h: entry and execution.

Test whether higher-timeframe confirmation actually improves net expectancy. Do not assume more timeframes produce better trades.

Only use completed higher-timeframe candles available at the signal timestamp.

Recommend the simplest timeframe combination that performs robustly.

### 6. MARKET REGIME AND DIRECTION

Classify market conditions as: bullish trend, bearish trend, sideways/ranging, transition or uncertain regime.

Compare structural classification against simple volatility-normalized alternatives.

Test: long-only, short-only, both directions, direction conditional on market regime.

Do not force every strategy to trade every regime. Avoid excessive regime filters that eliminate most opportunities. If a strategy performs better without a directional filter, preserve that finding.

### 7. HANDLING DIFFERENT COINS

Do not assume BTC settings work unchanged on every coin.

Normalize relevant measurements using:

- ATR or percentage volatility.
- Relative volume against the coin's own history.
- Structural distance relative to volatility.
- Realistic spread, fees, and slippage assumptions.
- Liquidity and trading activity.

Compare three approaches:

- A. One global parameter set across coins.
- B. Shared strategy logic with a small number of volatility-normalized parameters.
- C. Separate coin-specific parameters.

Prefer B when it offers robust performance without overfitting.

Evaluate each coin individually and the combined portfolio. Identify coins that genuinely improve results versus coins that add correlated risk.

Do not continuously change coin selection based on recent winners. Use a predefined, periodic universe-selection policy based on liquidity, data quality, and stability. Avoid survivorship bias and document changes to the tradable universe.

### 8. STRATEGY RULES

For every candidate strategy define:

1. Market regime.
2. Higher-timeframe context, if needed.
3. Exact setup conditions.
4. Long-entry conditions.
5. Short-entry conditions.
6. Entry order type and timing.
7. Initial stop-loss placement.
8. Profit-taking and exit rules.
9. Time-based exit.
10. Invalidation conditions.
11. Maximum trade duration.
12. Position sizing.
13. Minimal essential filters.

Rules must be objective, reproducible, and usable by a bot. Avoid vague descriptions such as "strong structure" or "good volume."

### 9. BACKTEST METHODOLOGY

Use chronological validation. Separate the available history into: initial development data, validation data, final untouched out-of-sample data.

Use walk-forward testing for promising strategies. Use purging or embargo where overlapping labels or trades could cause leakage.

Include: fees, slippage, funding for perpetual futures when relevant, causal signal timing, realistic order execution, intrabar stop/target ambiguity, position sizing, concurrent positions, portfolio exposure, liquidation and margin constraints where relevant.

Use conservative execution assumptions where lower-timeframe evidence is unavailable. Never report optimistic close-price fills as realistic trading results.

### 10. STATISTICAL VALIDATION

Calculate: total trades, trades per month, win rate, average winning and losing trade, net expectancy in R, profit factor, net return, maximum drawdown, monthly return distribution, consecutive losses, exposure-adjusted performance, bootstrap confidence intervals, walk-forward performance, out-of-sample performance, cross-coin performance, parameter sensitivity.

Use block or trade-cluster bootstrapping when observations are dependent. Account for multiple strategy and parameter tests to reduce data-mining bias.

Do not treat a strategy with very few trades as statistically established. Compare results against simple, appropriate baselines.

### 11. MINIMAL FILTER POLICY

Start with the raw strategy and measure its baseline performance. Add at most one additional filter at a time. Retain a filter only when it demonstrates a meaningful, repeatable out-of-sample improvement. Measure how much each filter reduces signal frequency.

Do not combine numerous weak filters simply to improve historical win rate. Prefer a robust, understandable strategy over a complicated high-win-rate strategy.

### 12. RISK MANAGEMENT

Evaluate:

- 0.25% and 0.5% equity risk per trade.
- Structural stop-loss versus volatility-adjusted stop-loss.
- Fixed-R target versus structure-based target.
- Partial take-profit versus single exit.
- Trailing stop only if it improves out-of-sample performance.
- Maximum concurrent portfolio risk.
- Correlated coin exposure.
- Daily loss limits and portfolio drawdown controls.

Compare exit methods empirically. Do not assume 1:2 or 1:3 risk/reward is always optimal. Distinguish risk per trade from leveraged notional exposure.

### 13. RESEARCH WORKFLOW

- **Phase 1 — Data and market behavior.** Briefly verify data quality, market regimes, structure methods, and signal feasibility. → CHECKPOINT: stop for approval.
- **Phase 2 — Strategy discovery.** Select 5–8 genuinely different strategy hypotheses. Define exact trading rules. → CHECKPOINT: stop for approval.
- **Phase 3 — Baseline backtests.** Test the strategies without excessive filters.
- **Phase 4 — Statistical comparison.** Evaluate trade frequency, expectancy, drawdown, and robustness.
- **Phase 5 — Minimal improvement.** Improve only promising strategies using a small number of changes.
- **Phase 6 — Out-of-sample validation.** Use walk-forward and untouched test periods.
- **Phase 7 — Final selection.** Recommend no more than 3 strategies for paper-trading validation.

Stop researching when evidence is sufficient to make a decision. If no strategy passes, report that outcome rather than inventing success.

### 14. REQUIRED OUTPUT

Produce a concise comparison table in `research/REPORT.md`:

| Rank | Strategy | Coins | Direction | Timeframes | Trades/month | Net expectancy | Profit factor | Max drawdown | OOS status |

For each shortlisted strategy provide: exact entry and exit logic, required market structure, timeframe configuration, direction and regime, risk-management approach, strengths and failure conditions, statistical evidence, whether it is ready for paper trading.

Separate tested results from hypotheses and assumptions. Do not invent numerical results.

### 15. EFFICIENCY RULES

- No unnecessary web research, generic trading tutorials, or lengthy literature reviews.
- No repeated audits.
- No bot implementation, UI or database changes, or architecture redesign.
- No token-heavy explanations.
- No premature parameter optimization.
- No claims of guaranteed profitability.

Use the existing research and backtesting capabilities. Prioritize actual evidence, compact tables, and clear decisions.

---

## START HERE

1. Copy this file to `docs/research_instructions.md` if it is not already there.
2. Begin Phase 1. Produce `research/REPORT.md` section "Phase 1 — Data audit" covering: available symbols, per-symbol/per-timeframe date coverage, missing or duplicate candles, timeframe alignment check, spot vs perp evidence, volume sanity, a simple regime map of the history (bull/bear/sideways by a volatility-normalized method), and your proposed development / validation / OOS date split.
3. Stop and wait for my approval before Phase 2.
