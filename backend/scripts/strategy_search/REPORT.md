# Strategy search report (2026-10-14)

Declarations: DECLARATIONS.md (written before any run). Freeze: FREEZE_20261014.json sha256 65aca162d18ae01c…, written before any holdout bar was loaded. Holdout run once at 2026-10-14T16:59:31.581214+00:00.

Every variant (4,656) is in out/variants_all.csv; stage-2 gate records in out/stage2_shard*.jsonl (380 variants × 5 risk levels); 15 rules passed every gate.

Dollars are per $10,000 (universe net × 10 / coins). Discovery = in-sample, 16 coins, 2023-06-01→2026-10-08. Holdout = same-period coin test, 11 coins, 2024-10-09→2026-10-08.

## Books

| book | rules | risk | discovery $/30d (in-sample) | holdout $/30d (coin test) | holdout trades | holdout max DD | holdout worst month | gap to $500 | gap to $1,000 |
|---|---|---|---|---|---|---|---|---|---|
| PB|50|4h|SHORT|ANY|btc0|s1.5|TRAIL2 | 4h SHORT: EMA50<EMA200, bar tags EMA50 and closes back below it with a bearish body; any non-no-trade regime; stop 1.5 ATR; trail 2 ATR from best close, no fixed target | 5% | +111.25 | +76.16 | 463 | $2,676 (24.33%) | -678 | 423.84 | 923.84 |
| BRT|55|4h|BOTH|TREND|btc0|s2.5|R2 | 4h LONG in UPTREND / SHORT in DOWNTREND: close beyond prior 55-bar extreme, then retest of that level within 8 bars closing back beyond it; stop 2.5 ATR; target 2R | 5% | +106.30 | -18.20 | 546 | $3,151 (28.64%) | -691 | 518.20 | 1018.20 |
| SQZ|20|4h|SHORT|ANY|btc0|s1.5|R2 | 4h SHORT: Bollinger(20,2) width in lowest 20% of prior 120 bars, then close below the lower band; any non-no-trade regime; stop 1.5 ATR; target 2R | 5% | +81.84 | +42.50 | 336 | $1,579 (14.36%) | -605 | 457.50 | 957.50 |

## Holdout detail

### PB|50|4h|SHORT|ANY|btc0|s1.5|TRAIL2

total net +2038.68 (11 sleeves) · $/30d per $10k +76.16 · win rate 0.354 · avg win 78.27 · avg loss -36.11 · PF 1.189 · expectancy 0.098R · trades/30d 19.03 · max DD $2,676 (24.33%) · longest losing streak 20 · fees 495.39 · best coin share 0.439 · best month share 0.899

Sensitivity $/30d: base +76.16, slip_0.05pct +61.67, slip_0.10pct +38.28, cost_1.5x +61.78, funding_0.01pct_8h +61.58

By regime at entry: DOWNTREND: 275 trades, net +435.09, win rate 0.345; RANGE: 82 trades, net +1791.74, win rate 0.39; UPTREND: 106 trades, net -188.16, win rate 0.349

Months reaching $500 (per $10k): 3 of 25; reaching $1,000: 2.

Monthly (per $10k): 2024-10 +0, 2024-11 +0, 2024-12 +413, 2025-01 -562, 2025-02 +1115, 2025-03 +490, 2025-04 -347, 2025-05 -100, 2025-06 +855, 2025-07 -103, 2025-08 +2, 2025-09 -165, 2025-10 +1666, 2025-11 -27, 2025-12 +275, 2026-01 -187, 2026-02 -232, 2026-03 -190, 2026-04 -678, 2026-05 -237, 2026-06 +147, 2026-07 -167, 2026-08 -94, 2026-09 -16, 2026-10 -2

### BRT|55|4h|BOTH|TREND|btc0|s2.5|R2

total net -487.15 (11 sleeves) · $/30d per $10k -18.20 · win rate 0.319 · avg win 90.95 · avg loss -43.85 · PF 0.97 · expectancy -0.063R · trades/30d 22.44 · max DD $3,151 (28.64%) · longest losing streak 17 · fees 325.16 · best coin share None · best month share None

Sensitivity $/30d: base -18.20, slip_0.05pct -31.95, slip_0.10pct -43.78, cost_1.5x -25.48, funding_0.01pct_8h -38.61

By regime at entry: UPTREND: 305 trades, net -410.90, win rate 0.315; DOWNTREND: 241 trades, net -76.25, win rate 0.324

Months reaching $500 (per $10k): 2 of 25; reaching $1,000: 0.

Monthly (per $10k): 2024-10 -44, 2024-11 +400, 2024-12 +330, 2025-01 -691, 2025-02 +555, 2025-03 -140, 2025-04 -319, 2025-05 -354, 2025-06 -509, 2025-07 -61, 2025-08 -500, 2025-09 +21, 2025-10 -192, 2025-11 +215, 2025-12 +9, 2026-01 +642, 2026-02 -185, 2026-03 -432, 2026-04 -195, 2026-05 +364, 2026-06 +174, 2026-07 +106, 2026-08 +226, 2026-09 +114, 2026-10 +22

### SQZ|20|4h|SHORT|ANY|btc0|s1.5|R2

total net +1137.66 (11 sleeves) · $/30d per $10k +42.50 · win rate 0.372 · avg win 91.9 · avg loss -49.05 · PF 1.11 · expectancy 0.081R · trades/30d 13.81 · max DD $1,579 (14.36%) · longest losing streak 21 · fees 383.88 · best coin share 0.441 · best month share 0.723

Sensitivity $/30d: base +42.50, slip_0.05pct +30.08, slip_0.10pct +11.60, cost_1.5x +24.89, funding_0.01pct_8h +33.43

By regime at entry: DOWNTREND: 214 trades, net +471.80, win rate 0.364; UPTREND: 98 trades, net +650.03, win rate 0.398; RANGE: 24 trades, net +15.83, win rate 0.333

Months reaching $500 (per $10k): 2 of 25; reaching $1,000: 0.

Monthly (per $10k): 2024-10 +34, 2024-11 +47, 2024-12 +189, 2025-01 +596, 2025-02 -196, 2025-03 +167, 2025-04 +295, 2025-05 +270, 2025-06 +395, 2025-07 +78, 2025-08 -349, 2025-09 -605, 2025-10 -108, 2025-11 +178, 2025-12 -63, 2026-01 +748, 2026-02 +6, 2026-03 -29, 2026-04 -466, 2026-05 -132, 2026-06 +156, 2026-07 -345, 2026-08 +108, 2026-09 -92, 2026-10 +151

## Verdict
No book reaches $500 per 30 days on the holdout at its frozen risk. Best holdout: PB|50|4h|SHORT|ANY|btc0|s1.5|TRAIL2 at +76.16 per 30 days per $10k (gap to $500: 423.84; to $1,000: 923.84). 90% of its holdout profit came from one month (2025-10) and 44% from one coin (RENDER). No paper plan is written (no holdout pass).

