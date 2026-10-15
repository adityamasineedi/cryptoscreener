# sim2.py vs scripts/sleeve_study/sim.py (original left unedited)

sim2.py is a copy of sim.py extended for the study-2 brief. Each change is covered by a case in test_sim2.py
(19 synthetic cases, all passing) and its effect on a reference variant is measured in out/sim_effects.json
(sim_effects.py: same signals through the original sim.py and through sim2 with each change toggled).

| # | Area | sim.py (sleeve study) | sim2.py (study 2) | Test |
|---|---|---|---|---|
| 1 | Risk budget | risk_frac × sleeve cash / \|fill − stop\| | entry fee and stop-exit fee + execution allowance inside the budget: qty = budget / (\|fill−stop\| + fee·fill + (fee+slip)·stop) | risk sizing |
| 2 | Lot size | none | round down to 10^−qty_precision (symbols snapshot); min notional 5 USDT | lot step |
| 3 | Account risk cap | sum of initial risk ≤ fixed USD cap | open stop risk (from current stop, incl. exit costs; 0 once the stop locks profit) ≤ min($200, 2% of marked account equity); size reduced to fit, else skip | account cap |
| 4 | Loss pauses | none | daily 2% / weekly 5% entry pauses (00:00 UTC / Monday 00:00 UTC resets); exits continue | daily pause |
| 5 | Drawdown stop | none | 20% cash-flow-adjusted peak drawdown → all positions exit at the next exec open; experiment ends | dd stop |
| 6 | Funding | optional flat column | hypothetical 0.01% of notional per 00/08/16 UTC boundary in the base score, both sides; scaled ×1.5 in the stress column | funding |
| 7 | Partial exits | none | partial_r / partial_frac, taker fill at the level (or open on a gap), stop to breakeven from the next bar | partial |
| 8 | Additions | none | ≤ 2 adds on a fresh same-rule signal while ≥ +1R; stop tightened to the new signal's stop; aggregate risk ≤ the position's original allowance | addition |
| 9 | Time exit | exit at the bar close | flagged at the bar close, filled at the next exec-bar open | time exit |
| 10 | Withdrawals | excess from sleeves above base, pro rata to their excess; could include unrealised profit | excess pro rata to positive sleeve equity; paid only from free collateral (cash − notional/2 − unrealised loss); shortfall → proportional reduction at the next open, then pay; remainder recorded as deferred | withdrawal (2 cases) |
| 11 | Marking | hourly marks, sleeve uses entry fill incl. slippage | hourly marks against the raw entry price (entry execution cost already in cash; no double count) | next-open |
| 12 | Conflicts | higher tf, then regime match, then priority | higher tf; at equal tf only a unique regime-compatible side (else skip); then priority | conflicts |
| 13 | Cost split | fees, slippage folded into net | gross, fees, execution allowance and funding reported separately per trade | next-open |
| 14 | Leverage | 2× (sleeve study) / 3× (strategy_search) | 2× gross notional / sleeve equity; nothing above 2× studied | leverage cap |
