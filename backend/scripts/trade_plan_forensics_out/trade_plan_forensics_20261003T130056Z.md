# Trade Plan Forensics Report

Generated: 2026-10-03T13:00:56.219062+00:00

## 1. Dataset
- Source: `json`
- Raw count: 219
- Filtered count: 219
- Reconstructed OK: 120
- Stopped (missing data): 0

## 2. Trade population
- **n**: 120
- **wins**: 28
- **losses**: 92
- **win_rate**: 0.23333333333333334
- **mean_R**: -0.2365644365889757
- **median_R**: -1.0
- **profit_factor**: 0.6914376914056838
- **max_drawdown_R**: -38.621747104781264
- **mean_MAE_R**: 1.156766816744231
- **mean_MFE_R**: 1.2343375704540598
- **avg_holding_bars**: None
- **sample_status**: MORE_RELIABLE_DESCRIPTIVE_SAMPLE
- **mean_R_bootstrap**: {'mean': -0.2365644365889757, 'ci_low': -0.45840962745260605, 'ci_high': 0.03581042756436702, 'n': 120, 'note': 'Descriptive bootstrap percentile interval; not a significance test.'}
- **win_rate_bootstrap**: {'mean': 0.23333333333333334, 'ci_low': 0.16666666666666666, 'ci_high': 0.31666666666666665, 'n': 120, 'note': 'Descriptive bootstrap percentile interval; not a significance test.'}

## 3. Data quality
- no_lookahead: True
- stopped_reasons: {}
- missing_fields_policy: UNKNOWN/UNAVAILABLE — no guesses

## 4–18. Cross-tabs (summary)
### by_timeframe
- `15m`: n=109 win_rate=0.23853211009174313 mean_R=-0.23482748830052114 sample=MORE_RELIABLE_DESCRIPTIVE_SAMPLE
- `1h`: n=11 win_rate=0.18181818181818182 mean_R=-0.253776015083662 sample=INSUFFICIENT_SAMPLE

### by_htf_state
- `HTF_ALIGNED`: n=27 win_rate=0.2222222222222222 mean_R=-0.3074128139082583 sample=INSUFFICIENT_SAMPLE
- `HTF_CONFLICT`: n=84 win_rate=0.25 mean_R=-0.17253415947112946 sample=LIMITED_SAMPLE
- `HTF_NEUTRAL`: n=9 win_rate=0.1111111111111111 mean_R=-0.6216352243976926 sample=INSUFFICIENT_SAMPLE

### by_entry_timing
- `EARLY`: n=16 win_rate=0.125 mean_R=-0.609406517820732 sample=INSUFFICIENT_SAMPLE
- `UNAVAILABLE`: n=104 win_rate=0.25 mean_R=-0.17920411639947473 sample=MORE_RELIABLE_DESCRIPTIVE_SAMPLE

### by_regime_primary
- `BREAKOUT`: n=1 win_rate=0.0 mean_R=-1.0 sample=INSUFFICIENT_SAMPLE
- `TRENDING_DOWN`: n=1 win_rate=0.0 mean_R=-1.0 sample=INSUFFICIENT_SAMPLE
- `VOLATILITY_CONTRACTION`: n=118 win_rate=0.23728813559322035 mean_R=-0.22362485076844985 sample=MORE_RELIABLE_DESCRIPTIVE_SAMPLE

### by_session_bin
- `00-04`: n=21 win_rate=0.38095238095238093 mean_R=0.18611617085677074 sample=INSUFFICIENT_SAMPLE
- `04-08`: n=24 win_rate=0.125 mean_R=-0.6167135668183471 sample=INSUFFICIENT_SAMPLE
- `08-12`: n=17 win_rate=0.4117647058823529 mean_R=0.46797508201615434 sample=INSUFFICIENT_SAMPLE
- `12-16`: n=26 win_rate=0.23076923076923078 mean_R=-0.21043987156346697 sample=INSUFFICIENT_SAMPLE
- `16-20`: n=16 win_rate=0.1875 mean_R=-0.4361991317908389 sample=INSUFFICIENT_SAMPLE
- `20-24`: n=16 win_rate=0.0625 mean_R=-0.8125 sample=INSUFFICIENT_SAMPLE

### by_direction
- `LONG`: n=67 win_rate=0.29850746268656714 mean_R=-0.0427584238182789 sample=LIMITED_SAMPLE
- `SHORT`: n=53 win_rate=0.1509433962264151 mean_R=-0.4815644904689132 sample=LIMITED_SAMPLE

## Winners vs losers
- winners_n=28 losers_n=92
- Observation: Losses with HTF_CONFLICT: 63/92 (68%).
- Observation: Losses with LATE/VERY_LATE entry timing: 0/92 (0%).
- Observation: Losses labeled CHOP: 1/92 (1%).
- Observation: SHORT losses: 45/92 (49%).
- Observation: Losses ENTRY_TOO_FAR_AFTER_BOS: 7/92 (8%).

## 19. Failure hypotheses

| Potential issue | Evidence | Sample size | Status |
|---|---|---|---|
| Wrong entry timing (late after BOS) | mean_R LATE/VERY_LATE=None vs EARLY/TIMELY=-0.609406517820732; n_late=0 n_timely=16 | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Poor retest / entry before confirmation | mean_R no_retest=-0.2365644365889757 (n=120) vs retest=None (n=0) | MORE_RELIABLE_DESCRIPTIVE_SAMPLE | INSUFFICIENT_DATA |
| HTF conflict | mean_R CONFLICT=-0.17253415947112946 (n=84) vs ALIGNED=-0.3074128139082583 (n=27) | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Sideways / chop regime | mean_R chop/sideways=-1.0 (n=1) vs trending=-1.0 (n=1) | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Wrong timeframe / HTF mismatch on lower TF | 15m mean_R CONFLICT=-0.1716396382385756 (n=74) vs ALIGNED=-0.28077484521242213 (n=26) | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Excessive volatility | mean_R VOL_EXPANSION=None (n=0) vs other=-0.2365644365889757 | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Insufficient volatility | mean_R VOL_CONTRACTION=-0.22362485076844985 (n=118) vs overall=-0.2365644365889757 | MORE_RELIABLE_DESCRIPTIVE_SAMPLE | NOT_SUPPORTED |
| Weak/deep liquidity sweep | mean_R weak/deep=-0.6979484059605714 (n=20) vs valid=-1.0 (n=1) | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| Poor S/D location (FVG unavailable) | FVG=UNAVAILABLE; mean_R FAR_FROM_ZONE=-0.23721949000921372 (n=74) vs NEAR_ZONE=-0.12083725324988173 (n=40) | LIMITED_SAMPLE | SUPPORTED |
| SL too tight | mean_R SL<=0.6ATR=None (n=0) vs wider=None (n=0); losses with MAE_R>=0.9 and never +0.5R: 92 | INSUFFICIENT_SAMPLE | INSUFFICIENT_DATA |
| TP problem | Losing trades that still reached MFE_R>=1 before exit: 29 (possible target/management geometry issue; descriptive only) | MORE_RELIABLE_DESCRIPTIVE_SAMPLE | SUPPORTED |
| Direction asymmetry | mean_R LONG=-0.0427584238182789 (n=67) vs SHORT=-0.4815644904689132 (n=53) | LIMITED_SAMPLE | SUPPORTED |
| Repeated entries in same regime | Top symbol/regime/session cluster share=0.125; top=[('BTCUSDT/VOLATILITY_CONTRACTION/12-16', 15), ('ETHUSDT/VOLATILITY_CONTRACTION/04-08', 11), ('SOLUSDT/VOLATILITY_CONTRACTION/00-04', 10)] | MORE_RELIABLE_DESCRIPTIVE_SAMPLE | NOT_SUPPORTED |
| Session / time-of-day effect | session mean_R={'20-24': -0.8125, '12-16': -0.21043987156346697, '04-08': -0.6167135668183471, '08-12': 0.46797508201615434, '00-04': 0.18611617085677074, '16-20': -0.4361991317908389}; spread check vs 0.25R threshold | MORE_RELIABLE_DESCRIPTIVE_SAMPLE | SUPPORTED |

## 20. Missing data
- FVG engine: UNAVAILABLE in codebase (not fabricated)
- paper_trades.fees / balance: often UNAVAILABLE
- bars_from_pullback_to_entry / bars_from_retest_to_entry: UNAVAILABLE (engine does not expose start indices)

## 21. Sample-size warnings
- Input truncated to max_trades=120 for runtime bounds.

---

NO PRODUCTION TRADING LOGIC WAS CHANGED.

THIS REPORT IDENTIFIES ASSOCIATIONS/FAILURE CHARACTERISTICS. IT DOES NOT SELECT OR RECOMMEND A NEW TRADING STRATEGY.
