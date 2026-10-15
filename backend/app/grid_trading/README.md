# Grid trading (isolated)

Results are for a **USDT-M futures long grid at 1x to 5x leverage**. Candles come from
persisted Binance USDT-M futures `ohlcv` (`load_ohlcv_series` /
`load_ohlcv_series_tail`). The market label is `binance_usdt_m_futures`.
This is not a spot book.

`GRID_TRADING_ENABLED` defaults to false. While it is off, every route on
`/api/grid-trading` returns 404 and `create_app` does not run a grid backtest,
scan, or live submit. The sidebar shows the tab only when
`GET /api/grid-trading/status` returns `enabled: true`.

Live `submit_live` acquires the grid rate limiter and then raises. There is
no order client. `mode=paper` is rejected. Grid paper, when it exists later,
stays inside this package and does not call `paper_trade.py`.

## Risk

`capital` is the margin. Each slot is sized to `capital * leverage / grid_count`
of notional, so 2x on 1000 is 2000 of grid notional. Leverage outside `[1, 5]`
is rejected. A buy is filled only while equity after the buy covers its initial
margin, `(base + qty) * price / leverage`. At 1x that is the old `quote >= debit`.

Equity is margin equity: `quote + base * price`, where `quote` goes negative
once a leveraged buy borrows. The equity curve, `max_drawdown`, the
max-drawdown kill, and `return` are all measured on that margin, not on the
leveraged notional.

Liquidation is at the price where margin equity falls to the maintenance
margin, `maintenance_margin_rate * base * price` (default 0.5%). Realized
grid profit stays in the margin. On every bar after fills, price is taken to
fall from the open to the low, so whichever of the stop and the liquidation
price is higher fires first. A liquidation closes at the liquidation price
with the taker fee and forfeits the margin left after the close
(`liquidation_fee`), which leaves equity at zero.

The run is rejected (`liquidation`) when the stop is at or below the
worst-case liquidation price. The worst case holds the `max_open_grids`
highest slots from their buys with no realized profit. Lower leverage, lower
`max_open_grids`, or a higher stop clears it. Funding can still move the live
liquidation price above the stop, which is why the per-bar check stays.

`max_open_grids` caps how many slots can hold inventory at once. Default is
`grid_count`. Buys fill from the highest touched slot down. A buy blocked by
the cap stays armed, counts as a touch without a fill, and is counted in
`blocked_buys`.

`exit_above_range` (default false; the UI sends true) flattens and ends the
grid when a bar closes above `range_high` while equity is above `capital`. A
wick above the range does not trigger it, and neither does a close above the
range while the book is flat or under water, so a grid that has not made money
keeps running. The event is `take_profit`. Funding stops after it, as after a stop.

The buy-and-hold benchmark uses the same leverage and maintenance margin. If
its liquidation price is reached its equity is zero.

The result counts `stop_hits`, `liquidations`, `take_profit_exits`, and
`drawdown_kills`. `round_trips` counts grid take-profits (slot sells). There is
one global stop; it closes the whole book, so `stop_hits` is 0 or 1.

## Fees

Grid limit buys and sells pay the maker fee (`fee_bps`) at the level. They do
not pay slippage. Stop, max-drawdown, and the buy-and-hold benchmark pay the
taker fee (`taker_fee_bps`, or `fee_bps` when taker is omitted) and
`slippage_bps` against the exit. Spacing must clear a round trip of the maker
fee. A maker fee that consumes the whole grid step is rejected.

## Funding

`funding_mode` is `charge` or `exclude`. The default is `charge`.

`charge` does not read `funding_rates`. That table is a live quote of the
current rate. With no settlement history for the symbol and window, charge
returns `funding_status` `unavailable`, leaves `net_pnl` empty, and reports
`pnl_before_funding`. It does not fall back to exclude. `exclude` is an
explicit choice and warns "Funding is not charged." A sweep in charge mode
with unavailable funding is not ranked. Coverage under 95% is
`funding_status` `incomplete`: funded net PnL is hidden and the sweep is not
ranked. A median settlement gap under one hour is treated as quotes, not
settlements. Annualized funding above 50% of average notional is a warning.
A sweep in exclude mode is labeled
"funding excluded."

At each settlement, payment is `inventory_qty * price * rate` for the inventory
held after that candle's fills and stop check. `price` is the row's
`mark_price` when it is finite and positive, otherwise the close of the last
candle at or before the settlement. The result counts those as
`funding_mark_prices` and `funding_closes`. If the settlement tables are
missing, charge mode stays `unavailable` and names
`python scripts/backfill_grid_funding.py`. A settlement timestamp equal to a candle's
open uses the inventory at the end of the previous candle, so a buy or sell
on that candle is not included. A timestamp strictly inside the candle uses
the worse of the opening and closing inventory: the larger quantity when the
rate is positive, the smaller when it is negative. Positive rate: the long pays. Negative rate:
the long receives. `funding_paid` is the signed sum (positive means paid) and
is not included in `fees_paid`. It is subtracted inside `net_pnl` and inside
the equity curve. The same settlements are charged on the buy-and-hold
benchmark.

Pause does not flatten inventory and does not stop funding. A stop or a
drawdown flatten ends funding for later settlements. A settlement at or
inside the stop candle is charged on the inventory held at that candle's
open, which is the position before the flatten.

The backtester does not reject a candle timeframe. The OHLCV loader knows
`1m`, `5m`, `15m`, `30m`, `1h`, `4h`, and `1d` (and their aliases). Any other
string is passed through. When the candle gaps match that timeframe and the
timeframe is longer than the median settlement spacing, `charge` still runs
and adds a warning that inventory at each settlement is approximated.

Coverage is the share of settlements in the window that have a stored rate.
A gap wider than 1.5 times the median stored interval counts as missing
settlements, and the result names that uncovered span. If the stored interval
itself changes inside the window (the later half of the gaps has a median more
than 1.5 times the earlier half, or the other way around), the result warns
and still returns. That change is not a silent pass and not a failed run. A
single longer hole stays a missing-row warning. Missing rows are not
treated as a zero rate. Coverage of 0 with no settlement source is
`unavailable`, not a zero rate and not exclude.

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
maker spacing, leverage, maintenance margin, max open grids, stop, stop below
liquidation, max drawdown, or max open orders is a 422.
