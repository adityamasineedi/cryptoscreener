# LIQ-FLUSH E34 forward paper contract

Contract id: `LIQ_FLUSH_E34`. Pre-registered as GATES E34 (owner D203B). Paper only. No backtest. No orders, no API keys, no alerts.

The lock file `docs/liq_flush_e34_contract.lock.json` is the SHA-256 of the code listed there. If any hashed file differs, the runner halts before it records a new hour or a new virtual trade. A store whose start line carries a different code fingerprint also halts.

## Data

- Long and short liquidation notionals are summed from Binance `!forceOrder@arr`, parsed by `parse_force_order` in the existing liquidation engine. This runner opens its own socket. It does not call the live ingestion object.
- A SELL is a long liquidation. A BUY is a short liquidation.
- Notional is order price × quantity.
- An event belongs to the 1h bar `[t, t + 1h)`.
- The stream's one-liquidation-per-symbol-per-second undercount is kept as received.
- A hour is written only when the process observed it in full. The partial hour at startup, hours before the start, hours lost on restart, and hours with a dropped socket are absent, not zero. A sealed quiet hour for a universe coin is zero and counts as present.
- The store path the owner process writes is `D:\cryptoscreener\data\liq_flush`. Tests refuse that path.

## Signal

- Universe: the 26 cap-book USDT perpetuals other than BTC.
- 1h closed bars only.
- p99 is the linear percentile (Hyndman-Fan type 7) of the coin's own long or short notionals in the prior 720 clock hours. At least 648 of those hours must be present.
- No signal before 30 days after that coin's first complete hour (`bar open >= first complete hour + 720h`), even if 648 hours exist sooner.
- Long: `L(t) > p99`, lower wick `(min(open, close) - low) >= 50%` of `(high - low)` with range `> 0`, and the close of bar `t+1` above the midpoint of bar `t`.
- Short: the mirror, with the upper wick and the next close below the midpoint.
- The long and short reclaim tests cannot both be true, so a bar is at most one side. If they ever were, that bar would not be a trade.

## Execution

- Fill at the open of bar `t+2`, slipped by D144 (taker 0.05% per side; slippage 2 bp, or 5 bp on AAVE, APT, ARB, DOT, ETC, OP and FIL).
- Stop: long `low(t) - 0.2 × ATR(14)` as of bar `t`; short `high(t) + 0.2 × ATR`. ATR is Wilder, recursive from the fixed price origin stored on the start line (40 days before the start hour).
- Target is 2R from the fill. A stop on the wrong side of the fill is not a trade.
- Time stop: the entry bar is bar 1, and the position is closed at the close of bar `e+47` if neither stop nor target has hit.
- A bar that touches both exits at the stop. A gap through the stop or the target fills at that bar's open, then slippage.
- One position per coin. The liquidation bar of a new signal must be strictly after the previous exit bar.
- Funding is the settlement rate × the 1h close at or before the settlement, for settlements in `(entry open, exit time]`. A stop or target uses the exit bar's open as the exit time. A time stop uses the exit bar's close. Longs pay positive funding.
- Size: `$20` planned risk on a virtual `$1,000`, notional capped at `$2,000`. R is net / `$20`. Capped trades are counted.

## Read

At 30 closed trades, on the first 30 by exit time: PASS if net > 0 and profit factor > 1.10. Otherwise FAIL. If 30 trades are not reached 12 months after the start line, the read is INCONCLUSIVE. Status withholds net and profit factor until that read.
