# Market Structure Analysis — BTCUSDT 1h (fixed attribution)

**Historical research only. Not a profitability claim.**
**Analytics only — does not affect strategy decisions.**

## 1. Signal time vs execution time

- `signal_time`: strategy setup / signal timestamp from the trade ledger.
- `decision_time`: closed-bar decision timestamp used for point-in-time features (setup open + TF duration).
- `entry_time`: simulated fill time from the ledger (for COMBO_02 this is the signal bar).

For COMBO_02 Path A, signal and execution occur on the **same** setup bar:
- `signal_bar=true`, `execution_bar=true`, `entry=YES`, `entry_attribution_type=EXECUTION_BAR`.

## 2. Raw decision rows vs unique trades

| Metric | Value |
|--------|------:|
| raw_decision_rows | 1100 |
| unique_executed_trades | 3 |
| signal_row_count | 3 |
| execution_row_count | 3 |
| position_active_row_count | 18 |
| no_trade_row_count | 1079 |
| ledger_trade_count | 3 |

Authoritative trade count = **unique executed trades** (= ledger count when fully matched).
Raw `market_structure_by_bar.csv` may contain many rows per trade (`POSITION_ACTIVE`).

## 3. Row roles

- `SIGNAL_BAR` / `EXECUTION_BAR`: strategy signal/fill bar (COMBO_02: same bar).
- `POSITION_ACTIVE`: open trade, **not** a new entry (`entry=NO`).
- `TRADE_EXIT_BAR`: exit bar while position closes.
- `NO_TRADE_BAR`: no ledger trade on this decision bar.

## 4. 15m source vs feature availability

| Metric | Value |
|--------|------:|
| 15m source available rows | 1100 / 1100 |
| 15m feature available rows | 1100 / 1100 |
| 15m unknown/missing feature rows | 0 |
| 15m status distribution | {'OK': 1100} |

`15m_source_available` means candles exist. `15m_feature_available` means structure/indicators were mapped to the decision timestamp. These are no longer collapsed into a single OK flag.

## 5. Research labels

`research_label` is a **hypothesis only**. It never creates entries and never replaces `primary_rejection_reason`.

## 6. Strategy rejection reasons

- On execution bars: no rejection (`final_strategy_decision=ACCEPTED`).
- On no-entry bars: `primary_rejection_reason=NO_STRATEGY_ENTRY_AT_BAR` with `primary_rejection_stage=UNKNOWN_NOT_EXPORTED` and `rejection_detail_status=NOT_EXPORTED`.
- Stage-level gates (trend/BOS/HL/HTF/cooldown/risk) are **not invented** when the hot-path funnel was not exported.

## 7. Offline funnel

Detailed stage rejection causes require the offline funnel audit. This analytics export does not reconstruct them.

## 8. Strategy isolation confirmation

- baseline trades == observed trades: **YES**
- equity curve unchanged: **YES**
- configuration hash unchanged: **YES**
- duplicate execution entries per trade: **0**
- future feature violations: **0**

No paper/live trades. No Telegram. No regime filter. No COMBO_02 rule changes.
