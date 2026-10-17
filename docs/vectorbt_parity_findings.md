# VectorBT parity — findings (2026-10-17)

Companion to `vectorbt_parity_report.md`, which is the generated table. No strategy, signal, stop, target, fill, fee, slippage, sizing or same-bar rule was changed. Each FAIL below is reported as found, and the job gate keeps that strategy on the bar loop.

## FAIL causes

### 1. COMBO_SD_A, COMBO_SD_E — `net_r` (6 cells)
- The SD rows are enriched with `slippage_rate = 0.0002`. In `trade_fees.enrich_trade_execution`, the fee is charged on the slipped entry fill, but gross P&L is computed from the **unslipped** entry and the slipped exit.
- VectorBT executes both slipped fills, so the engine's `r_net` comes out higher by exactly `qty × raw_entry × slippage / risk_usd`.
- Example: SD_A, real slice, LONG, trade 1.
  - Raw entry 43014.0, slipped fill 43022.6028, exit fill 43212.15584, qty 0.0072471746803, risk $20.
  - Engine r_net 0.0593043. VectorBT net R 0.0561870.
  - Gap 0.0031173, equal to the formula within 3e-10.
- All 6 cells match the formula to within 1e-9.

### 2. COMBO_02_V2 (multi-TF fixture) — `entry_price`, then a trade-count shift (2 cells)
- The screen row shows the CAUSAL_NEXT_OPEN book. The legacy walk enforces one open trade using its own (earlier) exits. Causal exits can land later, so a causal fill can open while the previous causal trade is still open.
- On the fixture:
  - Trade 27 is open from bar 2508 to bar 2562. Trade 28 fills at bar 2544, while 27 is still open.
  - VectorBT nets the two positions into one: 61 bar-loop trades vs 60, and every later pairing shifts.
- On the real slice there is no overlap, and it passes 45/45.

## Screen-path findings (not parity failures)

- **COMBO same-bar stop and target are labeled `"SL"`, not `AMBIGUOUS_INTRABAR`.**
  - The handling is `ResearchConfig.ambiguous_handling = AMBIGUOUS_CONSERVATIVE` (`research/config.py:30`), and `strategy_matrix` never overrides it. `combination_backtest.py:202` then returns `("SL", stop, ambiguous=True)`.
  - 8 such trades occurred in this check. All were carried through verbatim.
  - The COMBO_02_V2 causal book applies its own stop-wins rule (`causal_execution.py:126`).
- **COMBO_03_TRANSITION (and any variant except _B) can never pass its 15m stage on the screen.**
  - `strategy_matrix` loads 15m candles only for the COMBO_02_V2 family and COMBO_SD_*.
  - Diagnostics show `WAIT_15M_DATA_MISSING`; the 15m requirement is at `combo03_transition/variants.py:32` and `setup.py:139`.
- **The COMBO_02_V2 family ignores direction.**
  - `strategy_matrix` passes `direction_filter=None` (service.py around line 1100), so LONG and SHORT runs return the same two-sided trade list.
  - The separate screen audit hit the same thing: the run-mismatch guard hides those results.
- **COMBO_02_V2_1A and COMBO_02_V2_1_A match.**
  - `validate_backtest_request` rewrites 1A to 1_A, so the screen runs one combination object.
  - Run directly, the raw COMBO_02_V2_1A combination also matches field by field: 45/45 real, 56/56 fixture, both directions.
- **The 71 EMPTY cells are genuine zeros.** I reconfirmed them through `ResearchService.strategy_matrix` directly.
  - The pullback combos (03–08, 04_HTF) fail the pullback gate at all 211 bars that pass the BOS prefilter.
  - The SD variants report `NO_ZONE_OR_SEQUENCE` and `SEQUENCE_INCOMPLETE`.

## What the PASS proves
- **Checked independently by VectorBT:**
  - entry and exit bar indices;
  - one open position at a time and the trade count;
  - net R, recomputed from VectorBT's own fills, quantity and fee rates.
- **Equal by construction:** stop, target and outcome travel with each order as labels.
- **Rounding:** the engine rounds each fee leg to 1e-8 USD. With $20 risk that leaves gaps of 5e-10 R or less; the tolerance is 1e-8 R.
- **Not exercised:** no engine ever entered on the bar where its previous trade exited. That ordering is covered only by a unit test.

## Not run
- The eight unbuilt hypotheses are listed as NO ENGINE: trend pullback + zone + volume, sweep + CHoCH + volume, BOS zone retest + volume, range sweep + volume, compression breakout + retest + volume, CHoCH + supply/demand, VWAP pullback, standalone BOS.
- The H1–H8 study was not rerun.
