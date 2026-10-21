# Dashboard review

Read-only investigation of the live paper book. No trading change in this branch applies any of the proposals below.

Snapshot time for live market-cap and screener rows: 2026-10-21, about 06:15 UTC, from `GET /api/screener/fundamentals?limit=100` and `GET /api/paper/opportunities?limit=300&warm=0`. Group refusals come from `data/paper/decision_journal.jsonl`. Duplicate-fill rows come from a read-only `SELECT` (`default_transaction_read_only = on`). The journal’s first rows are 2026-10-20, so it does not reach 2026-10-09.

## A1. ATOM is mid on the book and small-cap in the engine

The passed book’s group is a static list. `cap_group` returns `large` only when the symbol is in `LARGE`, otherwise `mid` (`backend/app/research/passed_cap_book.py:64-65`). ATOM is in `MID` (`passed_cap_book.py:27`). Stake is 2% for large and 1% for mid (`passed_cap_book.py:81-90`).

The engine group is live market cap. `paper_risk._mcap_and_group` calls `classify_asset_group` (`backend/app/services/paper_risk.py:135-152`). Thresholds are large ≥ $10B, mid [$1B, $10B), small [$50M, $1B) (`backend/app/research/config.py:42-46`, `config.py:126-155`). Allowed groups default to BTC, ETH, large-cap, mid-cap (`paper_risk.py:20`). A known cap outside that set is `group_not_allowed` (`paper_risk.py:327-336`). The watcher prefixes that reason as `engine_refused:` (`backend/app/services/passed_paper_watcher.py:327-336`).

Live CoinGecko caps, status LIVE, and whether the engine would allow an open:

| Symbol | Book group | Book risk | Live cap (USD) | Engine group | Engine allowed |
| --- | --- | --- | --- | --- | --- |
| BTCUSDT | large | 2% | 1.660e12 | BTC | yes |
| ETHUSDT | large | 2% | 2.976e11 | ETH | yes |
| BNBUSDT | large | 2% | 9.768e10 | large-cap | yes |
| SOLUSDT | large | 2% | 6.229e10 | large-cap | yes |
| XRPUSDT | large | 2% | 8.416e10 | large-cap | yes |
| DOGEUSDT | large | 2% | 1.273e10 | large-cap | yes |
| ADAUSDT | large | 2% | 8.344e9 | mid-cap | yes |
| TRXUSDT | large | 2% | 3.143e10 | large-cap | yes |
| AVAXUSDT | large | 2% | 4.268e9 | mid-cap | yes |
| LINKUSDT | large | 2% | 9.411e9 | mid-cap | yes |
| DOTUSDT | large | 2% | 1.960e9 | mid-cap | yes |
| BCHUSDT | large | 2% | 5.728e9 | mid-cap | yes |
| LTCUSDT | large | 2% | 4.746e9 | mid-cap | yes |
| TONUSDT | large | 2% | absent from fundamentals | UNKNOWN | no (`mcap_unavailable` until a LIVE/CACHED/STALE cap exists) |
| NEARUSDT | mid | 1% | 6.523e9 | mid-cap | yes |
| ATOMUSDT | mid | 1% | 9.309e8 | small-cap | no |
| APTUSDT | mid | 1% | 7.274e8 | small-cap | no |
| ARBUSDT | mid | 1% | 1.297e9 | mid-cap | yes |
| OPUSDT | mid | 1% | 2.991e8 | small-cap | no |
| SUIUSDT | mid | 1% | 4.197e9 | mid-cap | yes |
| INJUSDT | mid | 1% | 8.723e8 | small-cap | no |
| AAVEUSDT | mid | 1% | 2.374e9 | mid-cap | yes |
| UNIUSDT | mid | 1% | 4.630e9 | mid-cap | yes |
| FILUSDT | mid | 1% | 8.557e8 | small-cap | no |
| ETCUSDT | mid | 1% | 1.259e9 | mid-cap | yes |
| ICPUSDT | mid | 1% | 1.618e9 | mid-cap | yes |
| RENDERUSDT | mid | 1% | 9.529e8 | small-cap | no |

Not allowed on the engine: TONUSDT, ATOMUSDT, APTUSDT, OPUSDT, INJUSDT, FILUSDT, RENDERUSDT. ADA, AVAX, LINK, DOT, BCH and LTC are large on the book and mid-cap on the engine; mid-cap is still allowed, so they are not refused for group.

Passed-book signals refused for a group reason since 2026-10-09, as far as the journal exists: two rows, both ATOMUSDT, outcome `not_opened`, reason `engine_refused:group_not_allowed=small-cap`, recorded 2026-10-21T06:01:21Z, setup bar 2026-10-21T05:00:00Z, trend BEARISH, strategies COMBO_01 and COMBO_02_LOCAL, direction SHORT. No earlier group refusal is in the journal.

Proposal, not applied: for PASSED_CAP_BOOK opens only, take the group and the 2%/1% stake from `cap_group` / the static lists. Leave `classify_asset_group` in place for every other stream.

## A2. Screener stop and TP1 can be inside the cost band

Stop comes from `signals/stop_engine.compute_stop` (structural low or high plus an ATR buffer). TP1 comes from `signals/target_engine.compute_targets`. A structural target closer than `SignalConfig.min_rr` (default 2.0) is skipped (`target_engine.py:60-75`); the fallback targets are 2R, 3R and 4R. `risk_engine.risk_reward` then requires TP1 R ≥ `min_rr`. The Trade chances row copies `final_stop` and `targets[0].target_price` (`paper_trade.py` opportunity builder, about 2992-3134). Nothing there compares `|TP1 - stop|` with fees.

2R is a multiple of the stop distance, not a minimum distance in price. A stop a few tenths of a percent away still produces a “2R” target. Round trip at 0.04% fee plus 0.05% slippage, both sides, is `2 × (0.0004 + 0.0005) = 0.0018`. Three times that is 0.0054 of the reference price. When reward is 2R, `|TP1 - stop|` is about 3× the stop distance, so a stop under about 0.18% of price puts the span under that line.

Live `GET /api/paper/opportunities?limit=300&warm=0`: 218 rows, 19 with both a stop and a TP1, 5 of those 19 under `0.0054 ×` the stop (entry was null on those rows). That is 5/19 = 26.3% of rows that show both levels, and 5/218 = 2.3% of all rows. The five: AAVEUSDT stop 154.9484 / TP1 154.4 (span/price 0.00354, `rr_pass` true; this is the cited ~154.9535 / 154.4000 pair), LTCUSDT 61.080 / 60.84, RSRUSDT, MUBARAKUSDT, AVAXUSDT 9.632 / 9.587. The ADA row in that response was 0.22408 / 0.2214, about 1.21% of price, above the line. The cited 0.2229 / 0.2226 pair is the same display path; a span of 0.0003 is about 0.13% of price and would have been under the line. It was not the cached row at the snapshot.

Proposal, not applied: hide or reject a screener row whose `|TP1 - stop|` is under 3× the round trip, or require a minimum stop distance in percent of price. Doing that inside `compute_targets` or `risk_reward` would change who is screened and can change who is opened. That belongs to the owner.

## A3. V1 paper exits fill the level; 0.05% is the passed-book model

`PaperTradeEngine.tick` closes a non-causal position when the mark crosses the stop or TP1, at that exact price (`paper_trade.py:2388-2436`). Causal passed positions are skipped there (`paper_trade.py:2422-2425`) and exit through `resolve_passed_exits`. `_close_locked` tick-rounds the non-causal exit, then `net_paper_pnl` subtracts `slippage_rate` (`paper_trade.py:2546-2585`). When the fill did not set a rate, that haircut is 0.0002 (`paper_sizing.py:19`). The recorded exit price is not moved by 0.05%.

The mark is `orchestrator.paper_tick_price`: a LIVE mark no older than 30 seconds, else the ticker, else the last candle close (`orchestrator.py:181-205`).

`combination_backtest.resolve_intrabar_outcome` exits at the raw stop or the nearest target on the bar high/low (`combination_backtest.py:183-207`). The same bar touching stop and target is a stop (`AMBIGUOUS_CONSERVATIVE`). That simulator does not apply 0.05% to the exit price.

`ResearchConfig.exit_slippage_rate` is 0.0005 (`config.py:56`). `causal_execution.causal_exit` applies it by filling worse than the raw level (`causal_execution.py:323-376`). The passed book uses that model in backtest and in `resolve_passed_exits`, and then stores `slippage_rate` 0 on the fill so the haircut is not charged twice.

So COMBO_02 v1 paper does fill the stop or TP1 on the mark, with no 0.05% exit slippage. The v1 combination simulator also exits at the raw level. The 0.05% adverse exit is the passed-book causal model, not the v1 simulator. V1 net PnL still subtracts 0.02% of both notionals.

Differences against the passed-book exit:

- V1 uses a single mark. The passed book walks closed bars.
- V1 has no gap-through `GAP_EXIT`.
- V1 does not move the fill by 0.05%.
- V1 checks stop, then TP1, on one price. The passed book uses the bar range and lets the stop win when both are touched.
- V1 takes the 0.02% haircut off PnL. The passed book puts slippage in the price and does not haircut again.

Proposal, not applied: walk closed bars for v1 the way the passed book does, or apply `exit_slippage_rate` 0.0005 to the v1 exit fill and set `slippage_rate` to 0 so it is not counted twice. Align the same-bar stop and the gap rule. Do not change the frozen v1 signal rules.

## A4. Missed signals 160

`missed_signals` increments only in `_record_missed`, and only for `late_tip` (`passed_paper_watcher.py:346-367`). `_record_not_evaluated` increments `unevaluated_bars` for `history_short`, `beyond_catchup_window` and `eval_failed`, and also prepends that row to the same recent deque (`passed_paper_watcher.py:369-397`). `ohlcv_catchup` is a `last_skip` that returns before any miss is recorded (`passed_paper_watcher.py:610-616`). The watermark file holds the counters. `recent_missed` keeps a capped tail. The decision journal does not contain the 160 `late_tip` events.

Retained tail (20 rows) at the snapshot:

| Bucket | Rows | What they are |
| --- | --- | --- |
| sleep | 0 | — |
| crash | 0 | — |
| late bar | 7 | `late_tip` on DOGEUSDT bars 2026-10-18T19:00Z and 20:00Z, strategies COMBO_02, COMBO_02_CLOSED_HTF, COMBO_01, COMBO_02_LOCAL, recorded 2026-10-19T17:44Z |
| ohlcv_catchup | 13 | `history_short`, all TONUSDT. 3 recorded 2026-10-19; 10 recorded 2026-10-21T03:50:00Z for bars 2026-10-20T17:00Z through 2026-10-21T02:00Z |
| other | 0 | — |

By book, all 20 are the passed book. By day, the retained rows fall on 2026-10-19 and 2026-10-21. The other ~140 of the 160 counter have no per-reason record: the deque dropped them, and the journal was not writing them.

After 2026-10-21 03:48 UTC the retained rows are those 10 TON `history_short` bars. The cause is known: store history shorter than `MIN_CLOSED_*`, so the bar is not evaluated. They increment `unevaluated_bars` (counter 34), not `missed_signals`. No `late_tip`, and no retained row with an unknown cause, after that restart.

## A5. Duplicate fills

No `paper_trades` row with `exit_reason = 'duplicate_fill'` has `closed_at` on or after 2026-10-14. The latest are 2026-10-13 19:03 UTC, TRXUSDT, bar 2026-10-13T12:00:00Z (four rows), then BNB and ARB on 2026-10-12. Total `duplicate_fill` rows: 43, of which 39 closed before 2026-10-14.

The existing guard is `ledger_dedupe_key` (`paper_trade.py:108-122`), `remember_dedupe_keys` (`paper_trade.py:575-578`), and `_writes_blocked` when `writer_lock_held` is false (`paper_trade.py:304-312`). The database migration cancels extras as `duplicate_fill` (`database.py:211-303`). The new test models two processes in memory: the process without the writer lock cannot open, and after the lock moves, `remember_dedupe_keys` of the first process’s key blocks the same bar. It does not touch a database.

## Not applied

A1 through A3 stay proposals. A5’s test is the only code change from this part, and it does not change how a fill is decided.
