# COMBO_02_V1_CLOSED_HTF

**Strategy ID:** `COMBO_02_V1_CLOSED_HTF`  
**Combo ID:** `COMBO_02_CLOSED_HTF`  
**Parent:** `COMBO_02_V1` / `COMBO_02` (frozen forming-HTF Path A)  
**Status:** RESEARCH variant — **not** approved for live trading

## What changed vs COMBO_02_V1

| Item | COMBO_02_V1 | COMBO_02_V1_CLOSED_HTF |
|------|-------------|------------------------|
| BOS / trend / HL / stop / TP / min RR / fees / leverage / risk | unchanged | unchanged |
| HTF hard gate (4h ∧ 1h BULLISH) | yes | yes |
| HTF as-of rule | largest HTF with **open ≤ setup open** (forming allowed) | last HTF whose **close ≤ setup close** (fully closed) |
| 4h coverage on limit tails | `limit//4+100` | same |

Frozen COMBO_02 / COMBO_02_V1 forming as-of behavior is intentionally unchanged (`htf_require_fully_closed=False`).

## Boundary rule

```text
htf_close_time <= setup_close_time
```

Example at setup close 12:00 UTC: 4h closing 12:00 is eligible; 4h closing 16:00 is not.

## Three-label comparison (required)

1. **original** — incomplete 4h HTF (`limit//16`) **plus** forming-candle HTF  
2. **corrected** — full 4h HTF coverage (`limit//4`) **plus** forming-candle HTF (`COMBO_02`)  
3. **final** — full 4h HTF coverage **plus** closed-candle HTF (`COMBO_02_CLOSED_HTF`)

Do **not** compare original (6 closed) directly to final without those labels.  
No profitability claim.

## Code / artifacts

- Flag: `CombinationDefinition.htf_require_fully_closed`
- Map: `build_htf_as_of_index_map_fully_closed`
- Walk: `combination_backtest.run_combination_backtest`
- Script: `backend/scripts/compare_combo02_v1_closed_htf.py`
- Reports: `backend/reports/combo02_v1_closed_htf_compare/`
  - `comparison_summary.json` / `.csv`
  - `trade_comparison.csv`
  - `htf_mapping_audit.csv`
  - `rejected_candidates_{original,corrected,final}.csv`
  - `test_results.txt`
