# Grid trading (isolated)

Backtests run on persisted Binance USDT-M futures candles (`ohlcv` via
`load_ohlcv_series` / `load_ohlcv_series_tail`). Results are labeled
`binance_usdt_m_futures`. This is not a spot book.

`GRID_TRADING_ENABLED` defaults to false. While it is off, every route on
`/api/grid-trading` returns 404. The sidebar shows the tab only when
`GET /api/grid-trading/status` returns `enabled: true`.

Live `submit_live` acquires the grid rate limiter and then raises. There is
no order client. Grid paper, when it exists later, stays inside this package
and does not call `paper_trade.py`.

Default mode is backtest. Invalid range, grid count, capital, spacing versus
fees and slippage, max capital, stop, max drawdown, or max open orders is a
422. One bar that touches a resting buy and a resting sell follows
`intrabar_path` (`worst`, `buy_then_sell`, `sell_then_buy`). Fees and
slippage apply on every fill. Slot size spends an equal quote slice including
the fee and the worse buy price, so the first buy of each slot fits in capital.
