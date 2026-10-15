# LCSC v1 ETHUSDT LONG: forward-validation contract (FROZEN)

**Contract id:** `LCSC_V1_ETHUSDT_LONG/FWD-1`
**Frozen:** 2026-10-15, before any forward bar existed. The last stored bar was 2026-10-15 15:00 UTC.
**Lock:** `docs/lcsc_eth_forward_contract.lock.json`
- config fingerprint `e27e5579cb8575c397df07525fb0fe2a45815f2b4b17da18e74dac21431227b7`
- code fingerprint `f4683811ee6c7d33961cf7d8e52594a68023d9911d4fc400c079ada1855ac51a`, over 14 files listed in the lock

**Runner:** `backend/scripts/lcsc_validation/forward.py`. It is read-only and sends no orders.
**Study it follows:** `backend/scripts/lcsc_validation/REPORT.md` and `PROTOCOL.md`.

ETH is **WATCH**: it is not validated, and it is not promoted to paper. This contract decides, once, whether forward evidence supports moving it to paper. The historical validation and holdout windows cannot do that, because ETH and the rules were selected with bars through 2026-10-15.

## 1. What is tested

| Item | Frozen value |
|---|---|
| Symbol / side / timeframe | ETHUSDT, LONG only, 1h |
| Rules | LCSC v1, as frozen in the study |
| Sweep | low below the prior 48-bar low, close back above it |
| CHOCH | first close above the latest confirmed 3/3 swing high after the sweep |
| Stop | min(entry − 1.5 × ATR14, sweep level) |
| Target | 2R, full exit at the first target touched |
| Position and conflicts | one trade at a time; stop and target on one bar counts as the stop |
| Signals | `generate_candidates` on a series anchored at **2022-11-20 08:00 UTC** (the study's first bar), with `index_start` at the forward start. Regime labels come from `study.Series._regimes` on that same series. |
| Sizing | $20 planned risk, $1,000 equity, 2× notional cap: qty = min(20 / (close − stop), 2000 / close) |
| R unit | planned risk = qty × (signal close − stop). R = net USD / planned risk. |

## 2. Window and minimum evaluation conditions

| Rule | Value |
|---|---|
| Forward start | **2026-10-16 00:00 UTC**. Only signals whose signal bar opens at or after this time count. |
| Minimum closed trades | 40 |
| Minimum elapsed time | 9 months: no read before **2027-07-16 00:00 UTC** |
| 12-month rule | From 2027-10-16 00:00 UTC, fewer than 40 closed trades is a KILL. The elapsed-time exception is **not** preapproved. |

The runner refuses a read until both minimums are met. The only exceptions are the 12-month kill and an explicit `--diagnostic` read (section 6).

## 3. Full cost model (the study's FULL model, unchanged)

| Leg | Price |
|---|---|
| Entry | next-bar open + 5 bps (adverse) |
| Stop exit | stop − 10 bps. A bar opening below the stop exits at its open − 10 bps. |
| Target exit | the target level (limit) |
| Fees | taker 0.04% on entry and exit notional |
| Funding | Binance settlements. A long pays `rate × qty × mark` at each settlement with fill time < t ≤ exit-bar open. Mark is the Binance markPrice, else the bar open at t. A missing settlement is a `FUNDING_GAP` exception, not a zero. |

## 4. Kill rules: any one triggers KILL

1. Fully costed expectancy ≤ 0R.
2. Profit factor < 1.0 (net USD).
3. Maximum drawdown > 15R, measured on cumulative R in exit order.
4. Profit comes only from downtrend trades: TREND_DOWN net R > 0 while TREND_UP ≤ 0 and RANGE ≤ 0. Trend label at the signal bar: close vs EMA200 and EMA800 of the 1h close.
5. Fewer than 40 closed trades at 12 months (2027-10-16), unless the exception was preapproved. It was not.
6. Any unresolved execution, data, fee, funding or reconciliation exception. The runner raises:
   - `DATA_GAP` (missing 1h bar);
   - `FUNDING_GAP` (missing settlement);
   - `DATA_REVISION` (stored bars for a past forward day changed between runs);
   - `RECORD_CHANGED` (a closed forward trade changed between runs).

   An exception closes only with a written resolution.

## 5. Pass-to-paper rules: all must be true

1. At least 40 closed trades **and** at least 9 months elapsed.
2. Fully costed expectancy > 0.10R.
3. Profit factor ≥ 1.20.
4. Maximum drawdown ≤ 15R.
5. Net R positive in at least two of the three trend regimes (TREND_UP, TREND_DOWN, RANGE), so not only downtrends.
6. No unresolved exceptions.

| Decision | When |
|---|---|
| `CONFIG_DRIFT_RESTART_REQUIRED` | The code or config fingerprint differs from the lock. Takes precedence over everything else. |
| `KILL` | Any kill rule. ETH stays rejected for this version. |
| `PASS_TO_PAPER_ELIGIBLE` | Every pass rule. This makes ETH eligible for a paper decision; it does not switch paper on. |
| `NO_PASS_CONTINUE` | Neither kill nor pass, for example expectancy between 0 and 0.10R or PF between 1.0 and 1.2. **This is not a pass.** The contract does not say when such a book is retired; that needs a decision before the first read (see blockers). |

- No conditional pass.
- Any change to rules, a parameter, costs, symbol, timeframe, data handling or execution creates a new version, with a new contract id and fingerprints, and restarts the forward window.
- No refit after a KILL.

## 6. Reads and records

- `forward.py run` records, for every forward candidate, its fate:
  - `TAKEN`;
  - `REJECTED_NO_VALID_LEVELS`;
  - `BLOCKED_OPEN_TRADE`;
  - `PENDING_NEXT_BAR`.

  For every trade it records:
  - signal, fill and exit times;
  - entry and exit fills;
  - fees, slippage and each funding payment;
  - gross and net PnL, and R;
  - outcome, and whether the stop gapped;
  - trend and volatility labels.

  Every run stores the code, config and data fingerprints, the as-of time, and drift against the lock (`forward_out/runs.jsonl`).
- `forward.py evaluate` applies sections 2–5 and appends the read to `forward_out/reads.jsonl`, with code, config and data fingerprints and the evaluation timestamp.
- `evaluate --diagnostic` is allowed before the gates. It is labelled `NON_ACCEPTANCE_DIAGNOSTIC`, has `acceptance: false`, does not touch the lock, and is logged like any read. A diagnostic read is still a look at the forward window: disclose it with the final result, and change nothing because of it.

## 7. What to expect (from the archive, not a target)

Suppose the true edge equals ETH's historical full-range FULL expectancy of +0.117R. Block-bootstrapping that history at 40 trades gives:
- expectancy ≤ 0 (a KILL) in about 30% of samples;
- expectancy > 0.10R in about 53%;
- a drawdown above 15R in about 8%.

The forward test is fair but low-powered. A KILL does not prove there is no edge, and a pass does not prove there is one.
