# Trade Plan Forensics Report

Generated: 2026-10-03T12:54:38.468726+00:00

## 1. Dataset
- Source: `json`
- Raw count: 219
- Filtered count: 219
- Reconstructed OK: 25
- Stopped (missing data): 0

## 2. Trade population
- **n**: 25
- **wins**: 7
- **losses**: 18
- **win_rate**: 0.28
- **mean_R**: -0.13338077579340202
- **median_R**: -1.0
- **profit_factor**: 0.8147489225091639
- **max_drawdown_R**: -10.594717019579234
- **mean_MAE_R**: 1.1266989158530096
- **mean_MFE_R**: 1.1214229601873091
- **avg_holding_bars**: None
- **sample_status**: INSUFFICIENT_SAMPLE
- **mean_R_bootstrap**: {'mean': -0.13338077579340202, 'ci_low': -0.64, 'ci_high': 0.45614674772969205, 'n': 25, 'note': 'Descriptive bootstrap percentile interval; not a significance test.'}
- **win_rate_bootstrap**: {'mean': 0.28, 'ci_low': 0.12, 'ci_high': 0.48, 'n': 25, 'note': 'Descriptive bootstrap percentile interval; not a significance test.'}

## 3. Data quality
- no_lookahead: True
- stopped_reasons: {}
- missing_fields_policy: UNKNOWN/UNAVAILABLE — no guesses

## 4–18. Cross-tabs (summary)
### by_timeframe
- `15m`: n=25 win_rate=0.28 mean_R=-0.13338077579340202 sample=INSUFFICIENT_SAMPLE

### by_htf_state
- `HTF_ALIGNED`: n=2 win_rate=0.0 mean_R=-1.0 sample=INSUFFICIENT_SAMPLE
- `HTF_CONFLICT`: n=20 win_rate=0.3 mean_R=-0.08699011876279084 sample=INSUFFICIENT_SAMPLE
- `HTF_NEUTRAL`: n=3 win_rate=0.3333333333333333 mean_R=0.13509432680692215 sample=INSUFFICIENT_SAMPLE

### by_entry_timing
- `EARLY`: n=4 win_rate=0.25 mean_R=-0.2397420298028572 sample=INSUFFICIENT_SAMPLE
- `UNAVAILABLE`: n=21 win_rate=0.2857142857142857 mean_R=-0.11312148931541055 sample=INSUFFICIENT_SAMPLE

### by_regime_primary
- `VOLATILITY_CONTRACTION`: n=25 win_rate=0.28 mean_R=-0.13338077579340202 sample=INSUFFICIENT_SAMPLE

### by_session_bin
- `00-04`: n=2 win_rate=0.5 mean_R=0.7026414902103832 sample=INSUFFICIENT_SAMPLE
- `04-08`: n=5 win_rate=0.4 mean_R=0.21163410758228265 sample=INSUFFICIENT_SAMPLE
- `08-12`: n=7 win_rate=0.2857142857142857 mean_R=-0.14107075794022642 sample=INSUFFICIENT_SAMPLE
- `12-16`: n=7 win_rate=0.14285714285714285 mean_R=-0.5443539439408064 sample=INSUFFICIENT_SAMPLE
- `16-20`: n=1 win_rate=0.0 mean_R=-1.0 sample=INSUFFICIENT_SAMPLE
- `20-24`: n=3 win_rate=0.3333333333333333 mean_R=0.0 sample=INSUFFICIENT_SAMPLE

### by_direction
- `LONG`: n=19 win_rate=0.3157894736842105 mean_R=-0.01976585661176955 sample=INSUFFICIENT_SAMPLE
- `SHORT`: n=6 win_rate=0.16666666666666666 mean_R=-0.4931613532019048 sample=INSUFFICIENT_SAMPLE

## Winners vs losers
- winners_n=7 losers_n=18
- Observation: Losses with HTF_CONFLICT: 14/18 (78%).
- Observation: Losses with LATE/VERY_LATE entry timing: 0/18 (0%).
- Observation: Losses labeled CHOP: 0/18 (0%).
- Observation: SHORT losses: 5/18 (28%).
- Observation: Losses ENTRY_TOO_FAR_AFTER_BOS: 3/18 (17%).

## 19. Failure hypotheses

| Potential issue | Evidence | Sample size | Status |
|---|---|---|---|
| Wrong entry timing (late after BOS) | mean_R LATE/VERY_LATE=None vs EARLY/TIMELY=-0.2397420298028572; n_late=0 n_timely=4 | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Poor retest / entry before confirmation | mean_R no_retest=-0.13338077579340202 (n=25) vs retest=None (n=0) | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| HTF conflict | mean_R CONFLICT=-0.08699011876279084 (n=20) vs ALIGNED=-1.0 (n=2) | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Sideways / chop regime | mean_R chop/sideways=None (n=0) vs trending=None (n=0) | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Wrong timeframe / HTF mismatch on lower TF | 15m mean_R CONFLICT=-0.08699011876279084 (n=20) vs ALIGNED=-1.0 (n=2) | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Excessive volatility | mean_R VOL_EXPANSION=None (n=0) vs other=-0.13338077579340202 | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Insufficient volatility | mean_R VOL_CONTRACTION=-0.13338077579340202 (n=25) vs overall=-0.13338077579340202 | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Weak/deep liquidity sweep | mean_R weak/deep=0.20820637615771426 (n=5) vs valid=None (n=0) | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Poor S/D location (FVG unavailable) | FVG=UNAVAILABLE; mean_R FAR_FROM_ZONE=-0.2004328855970732 (n=12) vs NEAR_ZONE=0.09733411202998439 (n=11) | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| SL too tight | mean_R SL<=0.6ATR=None (n=0) vs wider=None (n=0); losses with MAE_R>=0.9 and never +0.5R: 18 | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| TP problem | Losing trades that still reached MFE_R>=1 before exit: 5 (possible target/management geometry issue; descriptive only) | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Direction asymmetry | mean_R LONG=-0.01976585661176955 (n=19) vs SHORT=-0.4931613532019048 (n=6) | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Repeated entries in same regime | Top symbol/regime/session cluster share=0.28; top=[('BTCUSDT/VOLATILITY_CONTRACTION/12-16', 7), ('BTCUSDT/VOLATILITY_CONTRACTION/08-12', 7), ('BTCUSDT/VOLATILITY_CONTRACTION/04-08', 5)] | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Session / time-of-day effect | session mean_R={'12-16': -0.5443539439408064, '04-08': 0.21163410758228265, '08-12': -0.14107075794022642}; spread check vs 0.25R threshold | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |

## 20. Missing data
- FVG engine: UNAVAILABLE in codebase (not fabricated)
- paper_trades.fees / balance: often UNAVAILABLE
- bars_from_pullback_to_entry / bars_from_retest_to_entry: UNAVAILABLE (engine does not expose start indices)

## 21. Sample-size warnings
- Overall sample_status=INSUFFICIENT_SAMPLE (n=25)
- Input truncated to max_trades=25 for runtime bounds.

---

NO PRODUCTION TRADING LOGIC WAS CHANGED.

THIS REPORT IDENTIFIES ASSOCIATIONS/FAILURE CHARACTERISTICS. IT DOES NOT SELECT OR RECOMMEND A NEW TRADING STRATEGY.
