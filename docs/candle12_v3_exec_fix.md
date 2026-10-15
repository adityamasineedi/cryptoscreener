# CANDLE12_V3_EXEC_FIX

**Status:** Research only. Not paper. Not production.
**Engine:** `backend/app/research/candle12_v3.py` (`run_candle12_v3`, engine version 3.1.0)
**Baseline:** CANDLE12_V2 (`candle12_v2.py`), frozen and unchanged. See `docs/candle12_v2_btc_1h_freeze.md`.
**Scripts:**
- `backend/scripts/run_candle12_v3_exec_fix.py`: V2 vs V3 and the variants.
- `backend/scripts/scan_candle12_v3_gaps.py`: historical gap census.

**Tests:**
- `backend/tests/test_candle12_v3.py`
- `backend/tests/test_candle12_v3_acceptance.py`: BTCUSDT gap fixture.

**Paper readiness:** `docs/candle12_v3_paper_readiness.md`

V3 keeps the V2 Candle-1 setup, structural stop and target builder; it imports them from the frozen module. It changes only execution, sizing and book rules. No parameter was tuned.

## Configuration (`Candle12V3Config`)

| Field | Default | Values / meaning |
|---|---|---|
| `book_side` | `LONG` | `LONG`, `SHORT`, `BOTH` |
| `portfolio_lock_mode` | `per_side` | `per_side`, or `shared` (only with `BOTH`) |
| `bos_refire_policy` | `once_per_level` | `once_per_level`, or `every_close` (= V2) |
| `target_policy` | `nearest_target_full_exit` (= V2) | `nearest_target_full_exit`, `tp1_runner` |
| `entry_slippage_mode` | `inclusive` | `inclusive`, `additional` |
| `entry_slippage` / `exit_slippage` | 0.0002 / 0.0002 | adverse |
| `taker_fee` | 0.0004 | each side |
| `risk_usd` / `account_equity` / `leverage` | 20 / 1,000 / 2 | constant equity |
| `qty_step` / `min_qty` / `min_notional` | from `EXCHANGE_FILTERS` | BTCUSDT 0.001 / 0.001 / 50; ETHUSDT 0.001 / 0.001 / 20 (Binance USD-M exchangeInfo, 2026-10-15 07:20 UTC). Other symbols: no filters, tagged `no_exchange_filter`. |
| `apply_exchange_filters` | `true` | `false` turns the table off |
| `size_rounding_tolerance` | 0.10 | rounding may cut qty by at most 10% |
| `risk_tolerance_pct` | 0.10 | actual $ risk must be within 10% of `risk_usd`, whatever the cause; `None` turns it off |
| `tp1_fraction` | 0.5 | `tp1_runner` only |
| `runner_target` | `TP2` | `TP2`, `TP3`; `tp1_runner` only |
| `min_runner_distance_r` | 1.0 | runner must be ≥ this × R beyond TP1 |
| `breakeven_rule` | `once_at_tp1_close` | only value |
| `max_hold_bars` | 48 | `tp1_runner` only |
| `funding_enabled` | `false` | needs funding rows when on |
| `funding_interval_hours` | 8 | for the coverage label |

## Rules and formulas

`s_in` = entry slippage, `s_out` = exit slippage, `f` = taker fee. LONG is shown; SHORT mirrors every price.

**Setup.** This is the unchanged V2 Candle-1 BOS. Candle-1 closes above the last confirmed 3/3 swing high. The swing trend must be HH+HL. Candle-1 must be an impulse candle that closes up: range ≥ 1.5 ATR14, body ≥ 60%, RVOL ≥ 1.5.

**BOS re-arm (`once_per_level`).** A setup is keyed by the swing it broke: side, bar index and price.

1. The first setup on a key is allowed. It records its Candle-1 index as the key's last use.
2. A later setup on the same key is suppressed, and counted in `refire_suppressed`.
3. The key re-arms once an opposing swing (a swing low for LONG, a swing high for SHORT) is confirmed at a bar after the last use. The next setup on the key is then allowed and becomes the new last use.

Points that follow from this:
- A new swing on the same side (a new swing high for LONG) is a new key, so its break is a new setup.
- A deep pullback with no confirmed opposing swing does not re-arm.
- An opposing swing older than the last use does not re-arm.
- Setups seen during warm-up, while a position is open, or on the side the book does not trade still use up their key.

`every_close` turns this off, as in V2.

**Entry.** Candle-2 stop order only.
- Trigger: `T = C1.high`.
- Stop-order price: `P = T` (inclusive) or `P = T × (1 + s_in)` (additional).
- Normal fill: `T × (1 + s_in)` (inclusive) or `P × (1 + s_in)` (additional). Candle-2 must trade it.
- Gap fill: if `C2.open > P`, fill = `C2.open × (1 + s_in)`, tagged `GAP_ENTRY_FILL`.

**Stop.** `C1.low − 0.2 × ATR14`, as in V2.

**Targets.** From the V2 builder, measured from the actual fill. Slots fill at 2R, 3R and 4R when there is no structural level.
- `nearest_target_full_exit`: 100% exits at the nearest target touched.
- `tp1_runner`: TPn means the n-th nearest target.
  - 50% exits at TP1, rounded down to the step. If either half would be below `min_qty`, the whole position exits at TP1 (`TP1_SPLIT_UNAVAILABLE`).
  - **Runner target.** Start at `runner_target` (TP2) and take the first target at least `min_runner_distance_r` × R beyond TP1, where R = |fill − stop|. If TP2 fails, TP3 is tried. If none qualifies (`NO_RUNNER_TARGET`), the runner ends only at its stop or at max hold. The tag is `RUNNER_TARGET_TP2` or `RUNNER_TARGET_TP3`.
  - **Breakeven (`once_at_tp1_close`).** It is judged once, at the close of the bar that booked TP1. `BE = fill × (1 + f) / ((1 − f) × (1 − s_out))`, so a stop-out there nets zero after both fees. BE is set only if it is above the current stop and below that close. If set, it is the stop from the next bar to the end of the trade (`BREAKEVEN_STOP_SET`). Otherwise the old stop stays, and BE is never judged again (`BREAKEVEN_STOP_SKIPPED`).
  - **Max hold.** At bar `entry + 48`, an open position exits at that bar's close (`MAX_HOLD_EXIT`).

**Exits.** Every exit fill is `raw × (1 − s_out)`. Bars after the entry bar are checked in this order:
1. `open < stop`: exit at the open (`GAP_STOP_FILL`).
2. `open > nearest pending target`: exit at that target level (`GAP_TARGET_FILL`).
3. A bar touching both stop and pending target exits at the stop (`CONSERVATIVE_SAME_BAR_SL`). That includes TP1 and the original stop on one bar, which closes the whole position.
4. Otherwise the stop or target that was touched.

On the entry bar only steps 3 and 4 apply.

**Quantity and rejection.**
- `qty_target = min(risk_usd / |fill − stop|, equity × leverage / fill)`
- `qty = floor(qty_target / step) × step`

Every failing check is listed in `reject_reasons`. The first one is the outcome.

| Check | Rule | Outcome |
|---|---|---|
| `ZERO_QTY` / `BELOW_MIN_QTY` | qty ≤ 0 or qty < min_qty | `SIZE_REJECTED` |
| `ROUNDING_SHORTFALL` | qty < 0.9 × qty_target | `SIZE_REJECTED` |
| `MIN_NOTIONAL_REJECTED` | qty × fill < min_notional | `MIN_NOTIONAL_REJECTED` |
| `RISK_TOLERANCE_REJECTED` | \|qty × \|fill − stop\| − risk_usd\| / risk_usd > 0.10 | `RISK_TOLERANCE_REJECTED` |

Every trade and rejection reports `intended_risk_usd`, `actual_risk_usd`, `risk_diff_usd` and `risk_diff_pct`. A rejected trade takes no position and holds no lock.

**Fees, funding and PnL.**
- Entry fee: `f × qty × fill`. Each exit leg: `f × qty_leg × exit_fill_leg`.
- Funding is charged at each settlement in `(C2.open, exit_bar.open]`, on the quantity open then.
- `net = gross − fees + funding`.

**Funding coverage.** Each trade is labelled `FULL`, `PARTIAL` or `NONE` against the loaded settlement history. A run is labelled `FULL_HISTORY_FUNDING` only if every trade is `FULL`; otherwise `PARTIAL_HISTORY_FUNDING_SENSITIVITY`.

**R.**
- `r_gross = gross / actual_risk`
- `r_net_actual_risk = net / actual_risk`
- `r_net_intended_risk = net / risk_usd`

**Lock.** `per_side` allows one open trade per side. `shared` allows one across both sides, for a `BOTH` book only.

**Context label.** `signal_time` is the entry bar, as in V2. The Backtest adapter reads `market_context` one bar earlier, at Candle-1.

## Results: BTCUSDT 1h, 2023-01-01 to 2026-10-15 13:00 UTC, IN-SAMPLE

Settings: $20 risk, $1,000 constant equity, 2×, 0.04% taker, 0.02% slippage, 1,000 warm-up bars, funding off, `nearest_target_full_exit`. All of this window has been seen before. **Nothing here is out-of-sample.**

| Item | CANDLE12_V2 frozen (LONG row, shared lock) | V3 3.1.0 (LONG, per_side, once_per_level) |
|---|---:|---:|
| LONG trades | 95 | 84 |
| Net PnL | +$594.85 | +$601.34 |
| Win rate | 44.2% | 45.2% |
| Avg R net, actual risk | 0.351 | 0.371 |
| Avg R on intended $20 | 0.313 | 0.358 |
| Profit factor | 1.54 | 1.63 |
| Max drawdown | $137.07 | $169.35 |
| Gap-entry fills | 0 | 0 |
| Gap-stop exits | 0 | 0 |
| Trades blocked by opposite side | 6 | 0 |
| Risk-tolerance rejections | — | 8 (all margin-capped, actual risk $8.93–$16.04) |

V3 with V2's rules reproduces all 186 V2 trades with 0 mismatches. Those rules are `BOTH`, `shared`, `every_close`, no exchange filters and no risk tolerance.

| Run | Trades | Net | Win | Avg R act. | Avg R $20 | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1. LONG, once_per_level, 0.02% | 84 | +$601.34 | 45.2% | 0.371 | 0.358 | 1.63 | $169.35 |
| 2. LONG, every_close, 0.02% | 94 | +$512.20 | 42.6% | 0.282 | 0.272 | 1.46 | $211.80 |
| 3. LONG, once_per_level, 0.05% | 79 | +$506.79 | 44.3% | 0.329 | 0.321 | 1.55 | $172.14 |
| 4. SHORT, once_per_level, 0.02% | 81 | −$299.91 | 25.9% | −0.198 | −0.185 | 0.76 | $346.39 |
| 5. BOTH, shared, once_per_level | 158 | +$245.36 | 35.4% | 0.077 | 0.078 | 1.12 | $253.61 |
| LONG, `tp1_runner` | 88 | +$520.60 | 52.3% | 0.304 | 0.296 | 1.63 | $168.54 |
| LONG, risk tolerance off | 91 | +$670.11 | 46.2% | 0.414 | 0.368 | 1.67 | $143.54 |

Notes on the runs:
- **Run 4:** 7 rejections: 3 `SIZE_REJECTED` (rounding) and 4 risk-tolerance.
- **Run 5:** the LONG side made +$522.76 and the SHORT side −$277.41. 13 fillable setups were blocked by the other side's open trade.

**Funding sensitivity: `PARTIAL_HISTORY_FUNDING_SENSITIVITY`.** Not part of the acceptance metrics.
- Settlement history runs from 2025-09-01 00:00 UTC to 2026-10-11 00:00 UTC (1,216 rows).
- Of the 84 default-book trades, 23 are fully covered and 61 are not covered at all.
- Funding on the covered trades is −$6.45.

**Corrected V2 record.** The freeze doc lists 92 trades and +$598. The engine gives 95 trades and +$594.85; per year 22 / 33 / 21 / 19, ending at $1,001.28 / $1,301.08 / $1,138.98 / $1,153.51. The freeze doc is left as written.
