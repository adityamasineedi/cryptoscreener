# Passed book forward test — frozen rule (PASSED_BOOK_FWD-1)

Status: FROZEN TEXT, NOT YET ACTIVE. The test starts only when an activation record is appended to `docs/passed_book_fwd_rule.activation.jsonl` (see Activation). This text is fixed by the SHA-256 in `docs/passed_book_fwd_rule.lock.json`; any edit makes a new rule file with a new id.

Written 2026-10-19 18:50 UTC, before any result of the step-4 regime-book backtest was computed or read.

## What is tested

The passed book exactly as the live paper app runs it:

- the 10 sides in `backend/app/research/passed_cap_book.py` `PASSED_SIDES`, 1h, 27 coins (`cap_symbols()`);
- the BTC daily switch (`passed_regime_router`, `btc_daily_regime`), one position per coin, `SAME_BAR_EARLIER_SIDE`;
- `CAUSAL_NEXT_OPEN` fills and exits, fees and sizing of the live paper engine;
- the portfolio limits file `backend/app/research/passed_book_limits.json` and the per-coin allowlist `backend/app/research/passed_symbol_allowlist.json` as they stand at activation.

The activation record lists the SHA-256 of each file in the Config hash set. A change to any of them after activation ends the test as VOID.

Config hash set: `passed_cap_book.py`, `passed_regime_router.py`, `btc_daily_regime.py`, `passed_book_limits.json`, `passed_symbol_allowlist.json`, `services/passed_paper_watcher.py`, `services/paper_trade.py`, `causal_execution.py`.

## Data

- Trades: the live paper journal, source `PASSED_CAP_BOOK`, signal bar opening at or after the activation bar.
- A trade counts once it is closed. Open trades at the read are not counted.
- Signals recorded as `missed` (evaluated after the late-open bound) or `not_evaluated` are not trades. Their count is reported with the result; it is not a gate.

## Definitions

- **Planned risk** of a trade: `risk_percent x $1,000` (`risk_percent` as stored on the position: 2% large, 1% mid).
- **R** of a trade: `net_pnl_usd / planned risk`, after fees and slippage as booked.
- **Signal-day**: a UTC calendar date on which at least one counted trade has its signal bar. Several trades on one date are one signal-day.
- **Net R per trade**: mean R over counted trades.
- **PF**: sum of positive `net_pnl_usd` / |sum of negative `net_pnl_usd`|.
- **Book net R**: sum of R over counted trades.
- **Random-entry baseline** (same period): for every counted trade, draw one 1h bar uniformly from the same coin's closed bars inside the test period on which the BTC daily switch gave the same trend as at that trade's signal. Place a trade in the same direction with the same stop distance (% of entry) and the same TP1 distance (in R), filled and exited by `CAUSAL_NEXT_OPEN` with the same fees and the same sizing rule. One draw of every trade is one seed; 200 seeds, seeds 1..200, Python `random.Random(seed)`. The baseline value of a seed is its book net R.

## Read

- First and only read: when the count reaches **30 signal-days**. Before that, only counts (trades, signal-days, missed signals) may be looked at; no R, PnL, PF or baseline is computed.
- If 30 signal-days are not reached by **2027-10-20 00:00 UTC**, the result is **INCONCLUSIVE** and is read once on that date.

## Pass rule

PASS only if all four hold at the read:

1. at least 30 signal-days;
2. net R per trade > 0;
3. PF > 1.10;
4. book net R > the 75th percentile of the 200 baseline values.

Otherwise FAIL. VOID if a hashed file changed after activation. The verdict is recorded in the activation log as an append-only row.

## Activation

Activation is one JSON line appended to `docs/passed_book_fwd_rule.activation.jsonl` with: `rule_sha256` (this file), `activated_at` (UTC), `first_bar` (the first 1h bar opening after the deploy), `git_commit` of the live checkout, and the SHA-256 of every file in the Config hash set. It is written only with the user's approval of the live config.
