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

## Funding

`funding_mode` is `charge` or `exclude`. The default is `charge`.

`charge` loads `funding_rates` for the symbol between the first and last
candle, and one neighboring row on each side. Those neighbors are only used
to measure the cadence. Settlements are the stored timestamps. Nothing in
this book assumes an 8 hour interval.

At each settlement, payment is `inventory_qty * price * rate` for the inventory
held after that candle's fills and stop check. `price` is the row's
`mark_price` when it is finite and positive, otherwise the close of the last
candle at or before the settlement. A settlement that falls between two
candles uses the earlier candle. Positive rate: the long pays. Negative rate:
the long receives. `funding_paid` is the signed sum (positive means paid) and
is not included in `fees_paid`. It is subtracted inside `net_pnl` and inside
the equity curve. The same settlements are charged on the buy-and-hold
benchmark.

Pause does not flatten inventory and does not stop funding. A stop or a
drawdown flatten does: later settlements are not charged. A settlement that
shares the stop candle is not charged, because that candle's flatten is known
only at the candle timestamp.

Coverage is the share of settlements in the window that have a stored rate.
A gap wider than 1.5 times the median stored interval counts as missing
settlements, and the result names that uncovered span. Missing rows are not
treated as a zero rate. If coverage is 0, the result keeps the warning
"Funding is excluded. USDT-M funding payments are not charged." `exclude`
does not charge and keeps that same warning, so the PnL matches a run with
no funding cashflows.

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
