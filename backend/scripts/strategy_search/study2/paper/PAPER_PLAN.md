# Study 2 — prospective paper plan (OFF by default)

The paper runner (`paper/runner.py`) evaluates the frozen PRIMARY book forward from its freeze timestamp. It never
places orders, never touches `backend/app`, the v1 paper watcher, REGIME_BOOK_OPEN or any `paper_eligible` flag, and
reads the database through the read-only loader. `paper_config.json` ships with `"enabled": false`.

## Commands (from `backend/scripts/strategy_search/study2`, with the backend venv)

```
..\..\..\.venv\Scripts\python.exe -B paper\runner.py check-data          # data-quality gate only
..\..\..\.venv\Scripts\python.exe -B paper\runner.py dry-run             # replay from the freeze, print state + entries due
..\..\..\.venv\Scripts\python.exe -B paper\runner.py dry-run --now 2026-11-01T00:30:00+00:00   # reproducible replay
..\..\..\.venv\Scripts\python.exe -B paper\runner.py run                 # refused unless enabled=true AND book eligible
```

To arm the paper period the owner sets `"enabled": true` in `paper_config.json` (nothing else may change) and schedules
`run` hourly at hh:05 UTC (after the 1h bar closes and ingestion lands). `run` writes `paper/state/paper_<utc>.json`.
`run` is refused for a book whose freeze status is not `NOT_YET_TESTABLE` (for example NOT_ELIGIBLE / NOT_A_CANDIDATE)
and for any book whose evaluation status has become TARGET_NOT_MET or FAILED_DD_STOP.

## What one run does
1. Verifies the freeze file's sha256 against `paper_config.json`.
2. Data gate: every book coin's last closed 1h bar ≤ 3 h old; no missing 1h bar in the last 14 days. A failure blocks
   `run` (dry-run reports it). 4h/1d bars missing from storage are rebuilt only from complete groups of stored 1h bars.
3. Rebuilds features, causal regime labels (same classifier calls) and BTC daily trend on bars through the last closed
   bar; builds the frozen components' signals; replays `sim2.simulate` from the freeze timestamp with the frozen risk,
   coins, costs (incl. hypothetical funding), caps, pauses, drawdown stop and withdrawals.
4. Prints closed trades, open positions, month table, withdrawals, cash-flow-adjusted drawdown, and the entries that
   would be placed at the next open.

## Monitoring and invalidation (fixed at the freeze)
| Trigger | Action |
|---|---|
| 20% cash-flow-adjusted drawdown (sim stop) | book FAILED_DD_STOP; paper ends; no restart under this id |
| Data gate fails 6 consecutive hours | pause evaluation; resume only when the gap is repaired from exchange history; record the gap |
| Any change to rules, coins, risk, costs or code paths used by the replay | the forward record ends; a new id, new freeze, new forward period |
| Monthly review (1st of each month) | report net_30day_usd, gaps to $500/$1,000, month table, trades, expectancy in R with its standard error, max DD, cost columns (re-run the replay with 1.5× costs) |
| ≥ 6 full months AND ≥ 100 closed trades | evaluate once: TARGET MET iff net_30day_usd ≥ 500 at the frozen risk; robust evidence additionally needs positive expectancy (interval reported), risk-limit compliance and positive net at 1.5× costs |
| 12 months without reaching 100 closed trades | report as preliminary at the trade count reached; no target claim |

Live trading is out of scope. A paper TARGET MET would still not authorise live execution.
