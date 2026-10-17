# Backtest screen trade-bug audit — 2026-10-17

**Scope.** This audits the running code on http://localhost:5173/backtest: `feature/grid-trading` at **0ff796f** ("Enable the passed paper book and fill entries at the next open", committed 04:44 UTC during the audit). It covers both the screen and the full code path from OHLCV bar to engine fill to API row to table cell and chart.

**Columns.**
- **Origin:**
  - **398bec1** means the bug is in code that predates today.
  - **0ff796f** means today's commit introduced it.
- **Effect:**
  - **Result** means a trade, its money or the totals change.
  - **Label** means only text or display is wrong.

**Severity:**
- **HIGH:** the result is wrong.
- **MED:** identity, window, clock or which trades are attached is wrong.
- **LOW:** a label only.

The CANDLE12_V2 frozen warnings are known and not refiled. Locked rules (entry, exit, stop, target, same-bar rule, sizing, fees, slippage, fill model, methodology) are reported but not changed.

## Findings

| # | Sev | Strategy | Sym / TF | file:function | Wrong value | Correct value | Effect | Origin | Status |
|---|---|---|---|---|---|---|---|---|---|
| 1 | HIGH | any | any | routes.py:research_long_strategy_backtest_start | Requested 0% fees run at 0.04% taker / 0.02% maker, because the code reads `float(x or 0.04)`. Job af6f05a72e30 shows fees −$3.27 on 8 trades | 0% fees, $0 | Result | 398bec1 | **Fixed** on fix/trade-audit-fixes (`_first_given`). The multi-cap route had the same bug and is fixed too |
| 2 | HIGH | COMBO_01, COMBO_02_V2, COMBO_02_LOCAL (any combination walk) | CFXUSDT, MOVRUSDT 1h | combination_backtest._run_combination_backtest_impl (gap branch), causal_execution._walk_exit / _fill_causal_trade | A trade spanning a missing bar becomes OPEN and leaves the totals. CFX COMBO_02_V2: 5 trades / +$142.39 become 4 / +$34.45. The walk then opens new trades while that one is "OPEN" | The trade keeps a closed result, or the run is flagged. No second position | Result | 0ff796f | Locked exit logic in a frozen file. Report only |
| 3 | HIGH | CANDLE12_V2 / V3 | ETH 1h (synthetic gap) | candle12_v2._simulate_forward + `open_until = n`; candle12_v3 walker `open_until[lock_key] = (n, …)` | A gap inside a trade marks it OPEN and blocks every later entry. V2 drops from 8 trades to 2, V3 from 5 to 1 | The walk continues | Result. Latent: no real cap-symbol trade crossed a gap | 0ff796f | Frozen file. Report only |
| 4 | HIGH | Regime book | ETH 1h (synthetic gap) | passed_regime_backtest.run_symbol (`busy_until = n` for OPEN) + #2 | A gap locks the symbol for the rest of the run: 3 trades / −$62.98 become 0 closed and 61 skipped | The trade closes and the symbol frees up | Result. Latent: the 27 cap symbols have 0 1h gaps | gap 0ff796f, lock 398bec1 | Report only (depends on #2) |
| 5 | HIGH | Every non-SD COMBO, including COMBO_02 v1 | e.g. BTC, ETH 1h | service.strategy_matrix (DUAL for every non-SD combo); backtest_cpu_worker.build_matrix_row_postprocess | The displayed P&L switched from signal-close to the next-open fill (0.05% slippage per side, sizing on a fixed $1,000). COMBO_02 v1 BTC Q1 2025: $49.37, previously $58.23, while still labelled "Production comparable" | Locked fill model. The production label should match the book that is shown | Result | 0ff796f | Fill-model decision for you. Report only |
| 6 | HIGH | Regime book | cap symbols 1h | passed_regime_backtest.run_symbol (`research_config=None`), _size_trade(`candles=[]`) | Priced at the signal close, the optimistic book, with no label. 9 COMBO_02_V2 trades: +0.04R, against −0.71R at the next open | The same fill book the screen uses for COMBO | Result | 398bec1 (0ff796f's message claims next-open fills) | Fill-model decision. Report only |
| 7 | HIGH | COMBO_02_V2 family | BTC 1h | service.strategy_matrix (`direction_filter=None` for the V2 family) | A SHORT row holds 11 SHORT and 8 LONG trades: +$97.60 instead of the SHORT-only +$48.37 | One-direction rows hold one side only | Result | 398bec1 | Screen fix on fix/backtest-screen-bugs moves V2 and V2_1B to the Hedge book. The engine is report only |
| 8 | HIGH | COMBO_SD_* | — | trade_fees.enrich_trade_execution | Gross uses the entry before slippage while fees use the slipped fill. r_net is overstated by qty×entry×slip/risk (SD_A trade: 0.0593043, against 0.0561870 from fills) | Gross from the slipped entry | Result | 398bec1 | Locked fee/slippage rule. Report only |
| 9 | HIGH | COMBO_02_V2 causal book (and all of DUAL since 0ff796f) | SOLUSDT 1h | causal_execution._fill_causal_trade (TP1 target) vs the legacy one-open lock | 2–3 positions open at once. SOL: A is open 4829→4858, B fills at 4836, C at 4852 | One position | Result | 398bec1 | Locked. Report only |
| 10 | MED | OSE_* | BTC 1h | engine_backtest._run_ose → run_variant (both sides, one `busy_until`) | SHORT book: 51 trades, net_r 4.44. 10 SHORT setups were skipped because a hidden LONG was open | SHORT-only: 57 trades, net_r 2.78 | Result vs label | 398bec1 | Locked methodology. Report only |
| 11 | MED | any next-open row | BTC 1h | BacktestPanel TradeRow `fmtTime(t.signal_time)`; backtestTradeOverlay ENTRY at signal_time | The entry is shown at 03:00. The fill was the 04:00 open (95047.5 = 95000 + 0.05%) | Entry at fill_time 04:00; hold 5 = 09:00 − 04:00 | Label (wrong bar) | 398bec1 | **Fixed** on fix/trade-audit-fixes |
| 12 | MED | Regime book | all | regime_backtest_job start/_load_book; BacktestPanel run() | Dates and timeframe are dropped. "30d" runs 120 days of 1h bars. 5,760 and dates mode are clamped to 5,000 bars. No period is shown | The requested window | Window | 0ff796f | Being fixed on fix/regime-book-window |
| 13 | MED | Regime book | all | regime_backtest_job._load_book drops eval_start | Bars 50–79 of warm-up can trade, and period_start is the first warm-up bar | Walk from eval_start | Window | 0ff796f | Being fixed on fix/regime-book-window |
| 14 | MED | Passed (auto) | any | BacktestPanel run() regime branch | The previous job's blotter stays under "Backtest output". Run stays enabled with no progress or Cancel. The backend event loop is blocked for about 8 minutes | Current-run output, progress and Cancel | Stale trades | 0ff796f | Being fixed on fix/regime-book-window |
| 15 | MED | any | any | BacktestPanel run()/result memo | A cancelled job with partial rows looks finished | A "Cancelled — k of N cells" label | Partial job | 398bec1 | **Fixed** on fix/trade-audit-fixes |
| 16 | MED | V2.1-A hedge | BTC 1h | backtest_cpu_worker (direction_u from request) | Row direction "LONG", structure "HL", although 12 of 19 trades are SHORT | "BOTH" | Label | 398bec1 | Open (the screen already treats the hedge book as two-sided) |
| 17 | LOW | multi-coin | — | BacktestPanel stepCoin/viewingSymbol | The stepper follows the form's coins: it says "Coin 3 of 3: SOL" while the output is ETH | The job's coins | Label | 398bec1 | **Fixed** on fix/trade-audit-fixes |
| 18 | LOW | OSE_* | BTC 1h | engine_backtest.build_engine_row; frontend RunLead | tp1_hits 0, "(0 TP, 0 stop-outs)", because the outcomes are `target`/`stop` | 9 TP (17.6%) | Label | 398bec1 | Fixed on fix/backtest-screen-bugs |
| 19 | LOW | OSE_* | — | engine_backtest._run_ose | signal_time is the entry bar, so a 20-bar timeout shows holding 19 | signal_time = the break bar | Label | 398bec1 | Open (the decision_offset coupling needs care) |
| 20 | LOW | DUAL COMBO | — | causal trades skip enrich_trades | trade_id is null, with no trade_no | t1, t2, … | Label | 0ff796f for v1 | Open |
| 21 | LOW | CANDLE12_V2 (frozen) | BTC 1h | candle12_v2._simulate_forward | Trade 3 (2025-01-30 15:00) shows mfe 0.0 | 134.04 | Label | 398bec1 | Frozen. Report only |
| 22 | LOW | job | — | backtest_job._run | bars_processed 2,260 (including warm-up) against total_bars 2,160 | 2,160 | Label | 398bec1 | **Fixed** on fix/trade-audit-fixes |
| 23 | LOW | engine and COMBO rows | — | build_engine_row vs apply_causal_primary_display; frontend headers | PF is net on engine rows but gross-R on COMBO rows. "Max DD R (gross)" and "Equity R (gross)" show net or causal R | Consistent definitions and labels | Label | 398bec1 | Labels **fixed** on fix/trade-audit-fixes; the definitions stay open |
| 24 | LOW | any | — | TradeRow fee cell | A null fee shows "-$0.00" | "—" | Label | 398bec1 | **Fixed** on fix/trade-audit-fixes |
| 25 | LOW | OSE | — | pooled memo | "Pooled n=0" on 85 trades | n=85 | Label | 398bec1 | **Fixed** on fix/trade-audit-fixes |
| 26 | LOW | calendar runs | — | RunLead bars | "20,000 bars" (the API cap) | Bars actually loaded and evaluated | Label | 398bec1 | **Fixed** on fix/trade-audit-fixes |
| 27 | LOW | any | — | RunLead date range | "28 Dec through 3 Jan 2025" | "28 Dec 2024 through 3 Jan 2025" | Label | 398bec1 | **Fixed** on fix/trade-audit-fixes |
| 28 | LOW | any | — | BacktestDataMap props | Shows the form's strategy and coin count next to the job's trades | The job's | Label | 398bec1 | **Fixed** on fix/trade-audit-fixes |
| 29 | LOW | SD | — | backtestTradeOverlay hitSl `includes("STOP")` | TIME_STOP is coloured as a stop loss | A neutral exit colour | Label | 398bec1 | **Fixed** on fix/trade-audit-fixes |
| 30 | LOW (latent) | — | — | SelectedDetail equity, tradesMaxDrawdownUsd | Summed in list order. With overlaps (A −30 1→5, B +20 2→3) it shows 970/990 | Exit order gives 1020/990 | Label | 398bec1 | **Fixed** on fix/trade-audit-fixes |
| 31 | LOW (latent) | causal and combination walks | — | ohlcv_continuity.prepare_series vs the raw candles passed to causal, trade context and the chart | Indices would split if rows were unsorted or duplicated (none are today) | The same series everywhere | Label | 0ff796f | Open |

## Smallest code paths

- **#1:** `routes.py`, `float(body.get("taker_fee_pct") or 0.04)`. A value of 0 is falsy, so the default is used.
- **#2:** `combination_backtest` gap branch → the trade is marked OPEN → `closed = [t for t in trades if t.outcome != "OPEN"]` excludes it from the totals. The walk's one-open lock is released by the gap branch, so new trades open.
- **#5:** `service.strategy_matrix` sets `rcfg.execution_model = DUAL_LEGACY_AND_CAUSAL` for every combo that isn't SD. Then `build_matrix_row_postprocess` → `apply_causal_primary_display`, so the row shows the causal book.
- **#11:** worked example, BTC trade 1:
  - API: signal_time 03:00, fill_time 04:00, entry_price 95047.5, holding_bars 5.
  - TradeRow renders `signal_time` as the entry time, and the overlay places ENTRY on the 03:00 candle.

## Reconciled cleanly

About 160 closed trades were re-walked against Postgres OHLCV, checking identity, window, prices, clock, size, fees, net, R, equity, outcome and MAE/MFE:
- COMBO_02 v1, by calendar and by lookback;
- CANDLE12_V2 LONG;
- OSE_A SHORT;
- LARGE_CAP_SWEEP_CHOCH;
- the V2.1-A hedge;
- COMBO_02 SHORT;
- COMBO_01 on BTC and ETH;
- a regime book over 5 symbols.

135 screen rows matched the API payload in every column.

Also confirmed:
- No entry on a still-forming candle.
- Same-bar stop and target resolves to the stop in every engine.
- market_context is display-only.
- The regime trend uses only BTC daily bars that had already closed.

## Not run

- COMBO_SD on real data (0 trades in the windows used; #8 was shown on a synthetic trade).
- CANDLE12_V3 on BTC (sealed).
- Cancel on a live job (an ambiguous button; #15 rests on a unit test).
- A full 27-symbol regime run.
