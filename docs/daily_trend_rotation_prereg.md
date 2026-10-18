# Daily trend rotation — pre-registration

Written 2026-10-18, before any rotation result existed. Research only: not production, not tradable, no orders.
The rule below is fixed. Any change after a result has been seen counts as a new trial and must be logged here as such.

## Universe

The 12 displayed survivors of the pre-registered daily direction screen (run 2): books 1, 2, 3, 4, 5, 6, 9, 11, 12, 14, 15 and 19. Each book runs with its screen rules unchanged (`app/research/daily_trend/books.py`, verified trade-for-trade against the screen). The rotation runs per coin; each coin is independent.

## Shadow paths

For each coin, each book is simulated on its own over all loaded daily bars ("shadow path"). Execution is the same as the Backtest tab: signal on the closed daily bar, fill at the next open, 2.5×ATR(14) stop, slippage of 0.05% on BTC/ETH and 0.15% on other coins, taker fee on both legs, one position per book, no funding.

## Score

- Rebalance dates are the first UTC day of each calendar month (D). The decision is made at the close of the day before D.
- The score of book b on date D is the sum of net R over that book's shadow trades on this coin whose **exit day is before D** and on or after D minus 6 calendar months.
- Net R is the trade's net PnL (fees included) divided by the dollar risk at entry.
- A book with no closed trade in the lookback scores 0.

## Selection

- Books with score > 0 are ranked by score, highest first. Ties go to the lower book number.
- The top 3 are selected. If fewer than 3 score above 0, only those are selected. If none do, the rotation is flat for that month.

## Trading

- In month M, the rotation takes each selected book's shadow trades whose **entry day falls in month M**. Each trade runs to its own exit (stop, signal or window end), even if the book is not selected the next month.
- Shadow trades already open on the 1st of the month are not joined.
- Each selected book's trades carry 1/3 of the per-trade risk (book 19 keeps its own half-risk rule on top). Total open risk per coin is therefore at most about one full risk unit per month's entries.
- Different books may hold positions on the same coin at the same time. This differs from the screen's one-position-per-symbol rule and is accepted here because each selected book is a separate sleeve.

## Warm-up and window

- A book can only be scored once its shadow path has at least 6 months of history after the book becomes computable.
- The first rebalance is the first month start that is at least 6 months after every one of the 12 books is computable.
- Inside a requested date range, only trades entering on or after the range start count. Scoring may use shadow trades from before the range start.
- Open trades are closed at the last close of the range.
- The sealed holdout (2026-07-15 onward) is never loaded.

## Benchmark (diagnostic)

The static benchmark holds all 12 books at the same time on the same coin, each at 1/12 of the per-trade risk, with no selection. The rotation is only interesting if it beats this benchmark.

## How results are used

- **In-sample diagnostic.** One run per coin (BTC, ETH, SOL, direction BOTH) over every daily bar in Postgres up to 2026-07-14. This period produced the 12 survivors, so the run is biased upward and is labeled in-sample. It cannot pass or fail the rule.
- **Confirmation criterion, fixed now for any later clean test** (sealed holdout in one shot, or forward paper): on BTC and on BTC+ETH+SOL pooled, net PnL > 0 and PF > 1.1 after fees and available funding, and net PnL above the static benchmark. Anything else fails.
- **Comparison count.** This is trial 20 on top of the screen's 19 books.
