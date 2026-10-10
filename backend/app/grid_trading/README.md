# Grid trading (isolated)

Results are for a **USDT-M futures, unleveraged, long grid**. Candles come from
persisted Binance USDT-M futures `ohlcv` (`load_ohlcv_series` /
`load_ohlcv_series_tail`). The market label is `binance_usdt_m_futures`.
Leverage above 1 is rejected. This is not a spot book.

`GRID_TRADING_ENABLED` defaults to false. While it is off, every route on
`/api/grid-trading` returns 404 and `create_app` does not run a grid backtest,
scan, or live submit. The sidebar shows the tab only when
`GET /api/grid-trading/status` returns `enabled: true`.

Live `submit_live` acquires the grid rate limiter and then raises. There is
no order client. `mode=paper` is rejected. Grid paper, when it exists later,
stays inside this package and does not call `paper_trade.py`.

## Fees

Grid limit buys and sells pay the maker fee (`fee_bps`) at the level. They do
not pay slippage. Stop, max-drawdown, and the buy-and-hold benchmark pay the
taker fee (`taker_fee_bps`, or `fee_bps` when taker is omitted) and
`slippage_bps` against the exit. Spacing must clear a round trip of the maker
fee. A maker fee that consumes the whole grid step is rejected.

**Funding is excluded.** The result always says so. This module does not read
`funding_rates`.

## Cycle and PnL

A grid cycle is one slot's maker buy paired with that same slot's later maker
sell. Stop, drawdown, and mark sells are not cycles. `avg_profit_per_grid_cycle`
is the mean of (sell credit − buy debit) over those pairs, fees included.

`realized_pnl` is closed-trade PnL, including flatten sells. `unrealized_pnl`
is open inventory marked at the last close with no exit fee: `base * close -
cost basis`. `net_pnl` is the sum. `pct_time_in_range` is the share of
simulated bars whose close is inside `[range_low, range_high]`. `fill_rate` is
fills divided by level touches. Events record `stop`, `drawdown`, `range_exit`,
and `paused` with timestamps. A range exit is recorded when a bar trades
outside the range. It does not by itself flatten the book; the stop and the
drawdown kill do.

`paused` is the pause/kill switch. When it is true, the run takes no fills and
marks inventory to market (there is none, because nothing is bought).

## Scanner window

`window_end` is the last candle time the scanner scored. A backtest that sends
`after_time` drops candles at or before that time. If none remain, the request
is rejected unless `allow_overlap` is true, and the response then warns that
the backtest reuses the window that chose the range.

## Sweep

The grid-count sweep scores robustness: the share of nearby configs with
positive net PnL. Nearby means other grid counts within ±20% and the same grid
with range width at 90% and 110%, centered on the same midpoint. Rows are
ranked by that share. The payload always includes an overfitting warning.

## Run

Set `GRID_TRADING_ENABLED=true` and post to `/api/grid-trading/backtest` with
`range_low`, `range_high`, `grid_count`, `capital`, and `stop_price`. Pass
`candles` or a `symbol` plus `timeframe`. Invalid range, grid count, capital,
maker spacing, leverage, stop, max drawdown, or max open orders is a 422.
