# VectorBT parity check


**Summary:** 38 ids with an engine — 18 passed, 3 failed, 0 skipped, 17 had no trades to compare. 8 strategies listed as NO ENGINE (not run).
Generated 2026-10-16 22:18 UTC by `backend/scripts/vectorbt_parity/build_report.py`.

Each strategy's own bar-loop engine decides every entry, stop, target and exit. VectorBT (`Portfolio.from_orders`, explicit prices, no built-in stops) re-executes those orders with the same fill price, fee rate and size. Compared per trade: entry index, entry price, stop, target, exit index, exit price, outcome, net R. Stop, target and outcome travel with each order as labels, so they can only differ if trades are paired wrongly; the independent checks are the bar indices, the fills, one open position at a time, and net R recomputed from VectorBT's fills and fees. Datasets: `fixture` = seeded synthetic 1h candles (scripts/vectorbt_parity/fixture.py); `real` = BTCUSDT 1h 2024-01-01 → 2024-06-30. Counts only; no performance figures.

| Strategy id | Dataset | Direction | Bar-loop trades | VectorBT trades | Mismatches | First mismatched field | Cell |
|---|---|---|---:|---:|---:|---|---|
| CANDLE12_V2 | fixture | LONG | 8 | 8 | 0 |  | PASS |
| CANDLE12_V2 | fixture | SHORT | 7 | 7 | 0 |  | PASS |
| CANDLE12_V2 | real | LONG | 17 | 17 | 0 |  | PASS |
| CANDLE12_V2 | real | SHORT | 11 | 11 | 0 |  | PASS |
| CANDLE12_V3_EXEC_FIX | fixture | LONG | 7 | 7 | 0 |  | PASS |
| CANDLE12_V3_EXEC_FIX | fixture | SHORT | 8 | 8 | 0 |  | PASS |
| CANDLE12_V3_EXEC_FIX | real | LONG | 13 | 13 | 0 |  | PASS |
| CANDLE12_V3_EXEC_FIX | real | SHORT | 10 | 10 | 0 |  | PASS |
| LARGE_CAP_SWEEP_CHOCH | fixture | LONG | 13 | 13 | 0 |  | PASS |
| LARGE_CAP_SWEEP_CHOCH | fixture | SHORT |  |  |  |  | SKIP: engine_request_error: LARGE_CAP_SWEEP_CHOCH runs LONG only |
| LARGE_CAP_SWEEP_CHOCH | real | LONG | 31 | 31 | 0 |  | PASS |
| LARGE_CAP_SWEEP_CHOCH | real | SHORT |  |  |  |  | SKIP: engine_request_error: LARGE_CAP_SWEEP_CHOCH runs LONG only |
| MID_CAP_FVG_DISCOUNT | fixture | LONG | 62 | 62 | 0 |  | PASS |
| MID_CAP_FVG_DISCOUNT | fixture | SHORT |  |  |  |  | SKIP: engine_request_error: MID_CAP_FVG_DISCOUNT runs LONG only |
| MID_CAP_FVG_DISCOUNT | real | LONG | 103 | 103 | 0 |  | PASS |
| MID_CAP_FVG_DISCOUNT | real | SHORT |  |  |  |  | SKIP: engine_request_error: MID_CAP_FVG_DISCOUNT runs LONG only |
| SMALL_CAP_VOLUME_BOS | fixture | LONG | 4 | 4 | 0 |  | PASS |
| SMALL_CAP_VOLUME_BOS | fixture | SHORT |  |  |  |  | SKIP: engine_request_error: SMALL_CAP_VOLUME_BOS runs LONG only |
| SMALL_CAP_VOLUME_BOS | real | LONG | 14 | 14 | 0 |  | PASS |
| SMALL_CAP_VOLUME_BOS | real | SHORT |  |  |  |  | SKIP: engine_request_error: SMALL_CAP_VOLUME_BOS runs LONG only |
| OSE_A_corrected_baseline | fixture | LONG | 36 | 36 | 0 |  | PASS |
| OSE_A_corrected_baseline | fixture | SHORT | 42 | 42 | 0 |  | PASS |
| OSE_A_corrected_baseline | real | LONG | 89 | 89 | 0 |  | PASS |
| OSE_A_corrected_baseline | real | SHORT | 85 | 85 | 0 |  | PASS |
| OSE_B_acceptance | fixture | LONG | 33 | 33 | 0 |  | PASS |
| OSE_B_acceptance | fixture | SHORT | 32 | 32 | 0 |  | PASS |
| OSE_B_acceptance | real | LONG | 80 | 80 | 0 |  | PASS |
| OSE_B_acceptance | real | SHORT | 60 | 60 | 0 |  | PASS |
| OSE_C_retest | fixture | LONG | 17 | 17 | 0 |  | PASS |
| OSE_C_retest | fixture | SHORT | 18 | 18 | 0 |  | PASS |
| OSE_C_retest | real | LONG | 46 | 46 | 0 |  | PASS |
| OSE_C_retest | real | SHORT | 27 | 27 | 0 |  | PASS |
| OSE_D_faster_confirmation | fixture | LONG | 43 | 43 | 0 |  | PASS |
| OSE_D_faster_confirmation | fixture | SHORT | 54 | 54 | 0 |  | PASS |
| OSE_D_faster_confirmation | real | LONG | 111 | 111 | 0 |  | PASS |
| OSE_D_faster_confirmation | real | SHORT | 97 | 97 | 0 |  | PASS |
| COMBO_01 | fixture | LONG | 19 | 19 | 0 |  | PASS |
| COMBO_01 | fixture | SHORT | 24 | 24 | 0 |  | PASS |
| COMBO_01 | real | LONG | 45 | 45 | 0 |  | PASS |
| COMBO_01 | real | SHORT | 35 | 35 | 0 |  | PASS |
| COMBO_02 | fixture | LONG | 9 | 9 | 0 |  | PASS |
| COMBO_02 | fixture | SHORT | 12 | 12 | 0 |  | PASS |
| COMBO_02 | real | LONG | 11 | 11 | 0 |  | PASS |
| COMBO_02 | real | SHORT | 15 | 15 | 0 |  | PASS |
| COMBO_02_LOCAL | fixture | LONG | 19 | 19 | 0 |  | PASS |
| COMBO_02_LOCAL | fixture | SHORT | 24 | 24 | 0 |  | PASS |
| COMBO_02_LOCAL | real | LONG | 45 | 45 | 0 |  | PASS |
| COMBO_02_LOCAL | real | SHORT | 35 | 35 | 0 |  | PASS |
| COMBO_02_CLOSED_HTF | fixture | LONG | 10 | 10 | 0 |  | PASS |
| COMBO_02_CLOSED_HTF | fixture | SHORT | 12 | 12 | 0 |  | PASS |
| COMBO_02_CLOSED_HTF | real | LONG | 11 | 11 | 0 |  | PASS |
| COMBO_02_CLOSED_HTF | real | SHORT | 15 | 15 | 0 |  | PASS |
| COMBO_02_V1 | fixture | LONG | 9 | 9 | 0 |  | PASS |
| COMBO_02_V1 | fixture | SHORT |  |  |  |  | SKIP: screen rejected request: v1_short_removed: COMBO_02 V1 is LONG only. To backtest COMBO_02 SHORT, send strategy id COMBO_02. COMBO_04_HTF can still be requested by its own id. |
| COMBO_02_V1 | real | LONG | 11 | 11 | 0 |  | PASS |
| COMBO_02_V1 | real | SHORT |  |  |  |  | SKIP: screen rejected request: v1_short_removed: COMBO_02 V1 is LONG only. To backtest COMBO_02 SHORT, send strategy id COMBO_02. COMBO_04_HTF can still be requested by its own id. |
| COMBO_02_V2 | fixture | LONG | 61 | 60 | 34 | entry_price | FAIL |
| COMBO_02_V2 | fixture | SHORT | 61 | 60 | 34 | entry_price | FAIL |
| COMBO_02_V2 | real | LONG | 45 | 45 | 0 |  | PASS |
| COMBO_02_V2 | real | SHORT | 45 | 45 | 0 |  | PASS |
| COMBO_02_V2_1A | fixture | LONG | 56 | 56 | 0 |  | PASS |
| COMBO_02_V2_1A | fixture | SHORT | 56 | 56 | 0 |  | PASS |
| COMBO_02_V2_1A | real | LONG | 45 | 45 | 0 |  | PASS |
| COMBO_02_V2_1A | real | SHORT | 45 | 45 | 0 |  | PASS |
| COMBO_02_V2_1_A | fixture | LONG | 56 | 56 | 0 |  | PASS |
| COMBO_02_V2_1_A | fixture | SHORT | 56 | 56 | 0 |  | PASS |
| COMBO_02_V2_1_A | real | LONG | 45 | 45 | 0 |  | PASS |
| COMBO_02_V2_1_A | real | SHORT | 45 | 45 | 0 |  | PASS |
| COMBO_02_V2_1B | fixture | LONG | 61 | 61 | 0 |  | PASS |
| COMBO_02_V2_1B | fixture | SHORT | 61 | 61 | 0 |  | PASS |
| COMBO_02_V2_1B | real | LONG | 47 | 47 | 0 |  | PASS |
| COMBO_02_V2_1B | real | SHORT | 47 | 47 | 0 |  | PASS |
| COMBO_03 | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_03 | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_03 | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_03 | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_03_TRANSITION | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_03_TRANSITION | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_03_TRANSITION | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_03_TRANSITION | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_03_TRANSITION_B | fixture | LONG | 1 | 1 | 0 |  | PASS |
| COMBO_03_TRANSITION_B | fixture | SHORT | 7 | 7 | 0 |  | PASS |
| COMBO_03_TRANSITION_B | real | LONG | 2 | 2 | 0 |  | PASS |
| COMBO_03_TRANSITION_B | real | SHORT | 6 | 6 | 0 |  | PASS |
| COMBO_03_TRANSITION_C | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_03_TRANSITION_C | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_03_TRANSITION_C | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_03_TRANSITION_C | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_04 | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_04 | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_04 | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_04 | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_04_HTF | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_04_HTF | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_04_HTF | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_04_HTF | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_05 | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_05 | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_05 | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_05 | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_06 | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_06 | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_06 | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_06 | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_07 | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_07 | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_07 | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_07 | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_08 | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_08 | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_08 | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_08 | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_A | fixture | LONG | 1 | 1 | 1 | net_r | FAIL |
| COMBO_SD_A | fixture | SHORT | 2 | 2 | 2 | net_r | FAIL |
| COMBO_SD_A | real | LONG | 1 | 1 | 1 | net_r | FAIL |
| COMBO_SD_A | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_B | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_B | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_B | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_B | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C12 | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C12 | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C12 | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C12 | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C20 | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C20 | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C20 | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C20 | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C_FRESH | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C_FRESH | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C_FRESH | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_C_FRESH | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_D | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_D | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_D | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_D | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_E | fixture | LONG | 1 | 1 | 1 | net_r | FAIL |
| COMBO_SD_E | fixture | SHORT | 1 | 1 | 1 | net_r | FAIL |
| COMBO_SD_E | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_E | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_F | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_F | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_F | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_F | real | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_LIQ | fixture | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_LIQ | fixture | SHORT | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_LIQ | real | LONG | 0 | 0 | 0 |  | EMPTY |
| COMBO_SD_LIQ | real | SHORT | 0 | 0 | 0 |  | EMPTY |

## Per-id verdict

| Strategy id | Verdict |
|---|---|
| CANDLE12_V2 | PASS |
| CANDLE12_V3_EXEC_FIX | PASS |
| LARGE_CAP_SWEEP_CHOCH | PASS |
| MID_CAP_FVG_DISCOUNT | PASS |
| SMALL_CAP_VOLUME_BOS | PASS |
| OSE_A_corrected_baseline | PASS |
| OSE_B_acceptance | PASS |
| OSE_C_retest | PASS |
| OSE_D_faster_confirmation | PASS |
| COMBO_01 | PASS |
| COMBO_02 | PASS |
| COMBO_02_LOCAL | PASS |
| COMBO_02_CLOSED_HTF | PASS |
| COMBO_02_V1 | PASS |
| COMBO_02_V2 | FAIL |
| COMBO_02_V2_1A | PASS |
| COMBO_02_V2_1_A | PASS |
| COMBO_02_V2_1B | PASS |
| COMBO_03 | EMPTY |
| COMBO_03_TRANSITION | EMPTY |
| COMBO_03_TRANSITION_B | PASS |
| COMBO_03_TRANSITION_C | EMPTY |
| COMBO_04 | EMPTY |
| COMBO_04_HTF | EMPTY |
| COMBO_05 | EMPTY |
| COMBO_06 | EMPTY |
| COMBO_07 | EMPTY |
| COMBO_08 | EMPTY |
| COMBO_SD_A | FAIL |
| COMBO_SD_B | EMPTY |
| COMBO_SD_C | EMPTY |
| COMBO_SD_C12 | EMPTY |
| COMBO_SD_C20 | EMPTY |
| COMBO_SD_C_FRESH | EMPTY |
| COMBO_SD_D | EMPTY |
| COMBO_SD_E | FAIL |
| COMBO_SD_F | EMPTY |
| COMBO_SD_LIQ | EMPTY |
| Trend pullback + zone + volume | NO ENGINE |
| Sweep + CHoCH + volume | NO ENGINE |
| BOS zone retest + volume | NO ENGINE |
| Range sweep + volume | NO ENGINE |
| Compression breakout + retest + volume | NO ENGINE |
| CHoCH + supply/demand | NO ENGINE |
| VWAP pullback | NO ENGINE |
| Standalone BOS | NO ENGINE |

## COMBO_02_V2_1A vs COMBO_02_V2_1_A

```
{
  "screen_path": {
    "note": "validate_backtest_request rewrites COMBO_02_V2_1A to combination COMBO_02_V2_1_A, so through the screen path both ids run the same combination object.",
    "comparisons": {
      "real/LONG": {
        "trades_a": 45,
        "trades_b": 45,
        "result": "MATCH",
        "first_differing_field": null,
        "first_differing_position": null,
        "other_keys_differing": []
      },
      "real/SHORT": {
        "trades_a": 45,
        "trades_b": 45,
        "result": "MATCH",
        "first_differing_field": null,
        "first_differing_position": null,
        "other_keys_differing": []
      },
      "fixture/LONG": {
        "trades_a": 56,
        "trades_b": 56,
        "result": "MATCH",
        "first_differing_field": null,
        "first_differing_position": null,
        "other_keys_differing": []
      },
      "fixture/SHORT": {
        "trades_a": 56,
        "trades_b": 56,
        "result": "MATCH",
        "first_differing_field": null,
        "first_differing_position": null,
        "other_keys_differing": []
      }
    }
  },
  "combination_definitions": {
    "a": "COMBO_02_V2_1A",
    "b": "COMBO_02_V2_1_A",
    "differing_keys": {
      "combination_id": {
        "a": "COMBO_02_V2_1A",
        "b": "COMBO_02_V2_1_A"
      },
      "description": {
        "a": "RESEARCH COMBO_02 v2.1-A: same as v2 but CHOPPY \u2192 WAIT (RANGE_MEAN_REVERSION disabled in CHOPPY only).",
        "b": "RESEARCH COMBO_02 V2.1-A (UI id): CHOPPY \u2192 WAIT. Same evaluator as COMBO_02_V2_1A; does not replace COMBO_02_V2."
      }
    }
  },
  "direct_combination": {
    "note": "raw combination COMBO_02_V2_1A run directly (strategy_matrix for real, the fixture layers for fixture; not reachable from the screen) versus the screen's COMBO_02_V2_1_A cell.",
    "comparisons": {
      "real/LONG": {
        "trades_a": 45,
        "trades_b": 45,
        "result": "MATCH",
        "first_differing_field": null,
        "first_differing_position": null,
        "other_keys_differing": [
          "combination_id"
        ]
      },
      "real/SHORT": {
        "trades_a": 45,
        "trades_b": 45,
        "result": "MATCH",
        "first_differing_field": null,
        "first_differing_position": null,
        "other_keys_differing": [
          "combination_id"
        ]
      },
      "fixture/LONG": {
        "trades_a": 56,
        "trades_b": 56,
        "result": "MATCH",
        "first_differing_field": null,
        "first_differing_position": null,
        "other_keys_differing": [
          "combination_id"
        ]
      },
      "fixture/SHORT": {
        "trades_a": 56,
        "trades_b": 56,
        "result": "MATCH",
        "first_differing_field": null,
        "first_differing_position": null,
        "other_keys_differing": [
          "combination_id"
        ]
      }
    }
  },
  "result": "MATCH"
}
```
