# CANDLE12_V3_EXEC_FIX: out-of-sample contract (FROZEN)

**Contract id:** `CANDLE12_V3_EXEC_FIX/OOS-1`
**Frozen:** 2026-10-15 15:00 UTC
**Lock file:** `docs/candle12_v3_oos_contract.lock.json`. It is written once by `freeze-contract` and refuses to be rewritten without `--force`, which is only for a new contract version.
**Runner:** `backend/app/research/candle12_v3_paper/oos.py`, invoked as `python -m app.research.candle12_v3_paper.cli oos-evaluate`.

These rules were fixed before any bar from the window existed. Nothing in this document may be changed after 2026-10-16 00:00 UTC except by starting a new contract version with a new id.

## 1. Window

| Rule | Value |
|---|---|
| OOS start | **2026-10-16 00:00 UTC**. The first trade's Candle-1 must close at or after this time. |
| Warm-up | the 1,000 BTCUSDT 1h bars before the start; detection and BOS re-arm bookkeeping run through them, but no trades |
| Earliest evaluation date | 2027-10-15 00:00 UTC |
| Minimum elapsed time | 12 months since the OOS start, i.e. 2027-10-16 00:00 UTC |
| **Effective earliest read** | **2027-10-16 00:00 UTC**. Both conditions must hold, so the later one governs. |
| Minimum closed trades | 30 |

The runner refuses to evaluate before the effective earliest read, and with fewer than 30 closed trades. The only exception is `--diagnostic`, explained in section 6.

## 2. Accepted book

| Item | Value |
|---|---|
| Strategy | CANDLE12_V3_EXEC_FIX, engine 3.1.0 |
| Market | BTCUSDT 1h, LONG only |
| BOS re-fire | `once_per_level` |
| Targets | `nearest_target_full_exit` |
| Lock | `per_side` |
| Entry | Candle-2 stop at C1 high × 1.0002 (0.02% slippage, `inclusive`). If Candle-2 opens above C1 high, fill at the open × 1.0002 (`GAP_ENTRY_FILL`). |
| Exits | Reduce-only stop (structural) and take-profit (nearest target). Gap-stop and gap-target rules as in engine 3.1.0. Same-bar stop and target counts as the stop. |
| Fees | Taker 0.04% on each side |
| Slippage | 0.02% adverse (primary) and 0.05% adverse (stress) |
| Sizing | $20 intended risk, $1,000 constant equity, 2× leverage, quantity floored to 0.001 |
| Exchange filters | Minimum notional $50 enabled; minimum quantity 0.001 |
| Risk tolerance | `risk_tolerance_pct = 0.10`. This is a **trade-selection filter**: any trade whose actual $ risk differs from $20 by more than 10% is not taken. |
| Funding | Enabled where settlement history exists. Reported separately. Counted in the primary net only for trades whose whole holding period is covered (`funding_coverage = FULL`). |
| Config fingerprint (sha256) | `d439c0b59231a121f8ba049956939a8ea9f62a3ddcf8c483aef98b13e333f266` |
| Code fingerprint (sha256 over 28 files, listed in the lock) | `10f41813ec189920a553bdade5afbf487d350b4f62353d90954cfbd763ef3b6e` |

**How the metrics are computed.** The frozen engine (`run_candle12_v3`) is replayed over the OOS bars, with the contract config, the 1,000-bar warm-up and `index_start` at the OOS start. Trades count when their entry is at or after the OOS start and they closed by the evaluation time. The primary run uses 0.02% slippage and the stress run 0.05%. Paper trading does not produce the metrics; it must reconcile with them (rule 7).

## 3. Primary pass rule

All of these must be true:

1. At least 30 closed trades.
2. At least 12 months elapsed since the OOS start, and the date is on or after 2027-10-15. Together: no read before 2027-10-16 00:00 UTC.
3. Net PnL > 0 after fees and adverse slippage, plus funding on fully covered trades.
4. Net PnL > 0 at both 0.02% and 0.05% adverse slippage.
5. Profit factor ≥ 1.30 (primary run).
6. Maximum peak-to-trough drawdown ≤ 17R on the intended $20 risk, which is $340 under the fixed $1,000 equity convention. It is measured on the cumulative primary net, trade by trade, in exit order.
7. Paper reconciliation has no unresolved execution exceptions.
8. No strategy, config, universe, fee, slippage, target, BOS, risk-tolerance or data-handling change occurred during the window. This is checked as no drift of the code and config fingerprints from the lock.

## 4. Classification

The runner lists every failure. The reported class is the first failure in this order:

| Order | Class | When |
|---:|---|---|
| 1 | `CONFIG_DRIFT` | Any fingerprinted file, or the contract config, differs from the lock (rule 8) |
| 2 | `EXECUTION_MISMATCH` | Any open paper reconciliation exception (rule 7) |
| 3 | `INSUFFICIENT_SAMPLE` | Fewer than 30 closed trades; reachable only in a diagnostic read, since the runner refuses otherwise |
| 4 | `OOS_FAIL_EDGE` | Primary net ≤ 0, or profit factor < 1.30 (rules 3 and 5) |
| 5 | `OOS_FAIL_ROBUSTNESS` | Stress (0.05%) net ≤ 0 (rule 4) |
| 6 | `OOS_FAIL_RISK` | Drawdown > 17R (rule 6) |
| — | `OOS_PASS` | None of the above, in a non-diagnostic read |

Further rules:
- **No conditional pass.** A read either passes or it does not.
- **No refitting after a failure.** A failed contract is closed. A new idea needs a new strategy id and a new contract with its own untouched window.
- **Any change to code, config, symbol, timeframe, fee, slippage, risk, target, BOS or data handling** creates a new version and restarts the OOS window. That includes the paper adapter and the database loader in the fingerprint list.

## 5. Paper reconciliation

Every paper trade is compared field by field with the engine replay on the same closed bars (`reconcile.py`). The fields are:
- signal time, Candle-1 OHLC, Candle-2 OHLC;
- trigger, expected fill, actual fill, entry slippage;
- stop and target prices;
- quantity, notional, intended risk, actual risk;
- fees, funding, gross PnL, net PnL;
- R on actual risk and on the intended $20;
- exit reason, exit time, exit fill;
- rejection tags.

| Field | Tolerance |
|---|---|
| Entry fill | within 0.05% of the engine's expected fill; otherwise an exception whose resolution must explain the gap or market impact |
| Stop exit fill | within 0.05% of the engine's expected stop exit fill; otherwise as above |
| Target exit fill | within 0.05% of the engine's expected target exit fill; otherwise as above |
| Quantity | exact (≤ 1e-12) after exchange step rounding |
| Fees | computed fee within $0.000001 of the exchange-reported fee, where reported |
| Funding | computed funding equal (≤ $1e-9) to exchange-reported funding, where reported |
| Signal time, candle data, exit reason, exit time, rejection tags | exact |
| Order prices (trigger, stop, target) | exact (relative 1e-9) |

Anything outside tolerance becomes an exception, as do these operational events:
- a missing or unexpected trade;
- a rejection mismatch;
- a partial fill;
- an exchange rejection;
- a late placement;
- price beyond the trigger at placement;
- broker unavailable;
- a data gap;
- a failed exit order.

An exception stays open until resolved with a written explanation (`resolve-exception`). Any open exception blocks acceptance (`EXECUTION_MISMATCH`).

## 6. Reads and records

- `oos-evaluate` needs `CANDLE12_V3_ALLOW_OOS=1` and a PAPER store. Smoke and dry-run stores are refused.
- Every read appends a record to `<paper store>/oos_reads.jsonl`. It holds:
  - the code, config and data fingerprints, and the lock's fingerprints;
  - the evaluation timestamp and the as-of time;
  - the classification, every failure, and the primary and stress metrics.
- `--diagnostic` allows a read before the gates are met. It is labelled `NON_ACCEPTANCE_DIAGNOSTIC`, has `acceptance: false`, and is logged like any other read. A diagnostic read is a look at the window: it must be disclosed with the final result, and it must not lead to any change.
- A smoke run (`NON_ACCEPTANCE_SMOKE_ONLY`) runs on seen bars only, never touches the window, and is never strategy validation or OOS evidence.

## 7. What this contract does not claim

It does not claim an edge. Every result to date (2023-01-01 to 2026-10-15) is IN-SAMPLE. A pass would be evidence for this exact book on this exact window, nothing more.
