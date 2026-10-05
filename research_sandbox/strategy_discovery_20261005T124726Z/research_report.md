# S3 Compression Breakout — Research Report

Generated: 2026-10-05T12:50:48.709253+00:00
Independent of COMBO_02. Sandbox-only. Not a profitability claim.

## Safety

```text
Existing code modified: NO
Existing configs modified: NO
Existing DB data modified: NO
Existing reports overwritten: NO
Existing strategy modified: NO
Live trading affected: NO
Paper trading affected: NO
Dependencies changed: NO
Migrations run: NO
```

## Data

- Quality: **DATA_OK**
- 1h rows: 35039 (2022-10-06T08:00:00+00:00 → 2026-10-05T06:00:00+00:00)
- 4h rows: 8761 (2022-10-06T00:00:00+00:00 → 2026-10-05T00:00:00+00:00)
- Fingerprints: 1h `0839f0c52cec2c5da2110b7305d004ad0649e65a732bb81f3fbbe6e4f10ff993` / 4h `c27cd5fde0a7f932fe237deab9064a855ab289fba2f7225fe6ff138011f7645c`

## Point-in-time

- mapping rows: 35039
- lookahead pass: 35039
- lookahead fail: 0
- missing HTF: 0
- future feature violations: 0
- trade audit rows (A0): 195 all PASS

## Primary config (A0 / E1 / S1 / 2R)

- compression: ATR(14) percentile <= 20.0 over 100
- range lookback: 20 completed bars
- breakout buffer: 0.0005

## Partitions (A0)

- **TRAIN**: 2022-10-06T08:00:00+00:00 → 2025-02-28T06:00:00+00:00 | trades=66+38=104 | avg_R=0.03291757000544558 net_R=3.4234272805663406 PF=1.0558890142471256 DD=15.90154194349687 | STRONGER_RESEARCH_EVIDENCE
- **VALIDATION**: 2025-02-28T07:00:00+00:00 → 2025-12-17T06:00:00+00:00 | trades=28+18=46 | avg_R=0.3154236701896857 net_R=14.509488828725543 PF=1.7450191661399075 DD=5.016739862083077 | EXPLORATORY_ONLY
- **OOS**: 2025-12-17T07:00:00+00:00 → 2026-10-05T06:00:00+00:00 | trades=29+16=45 | avg_R=-0.07014449866163139 net_R=-3.1565024397734125 PF=0.8808408995697579 DD=7.596189306012697 | EXPLORATORY_ONLY

## Ablation

- **A0**: trades=195 avg_R=0.07577648035650497 OOS_n=45 OOS_avg_R=-0.07014449866163139 | STRONGER_RESEARCH_EVIDENCE | compression + range + breakout
- **A1**: trades=178 avg_R=0.01208858717327252 OOS_n=40 OOS_avg_R=-0.16965599521333863 | STRONGER_RESEARCH_EVIDENCE | A0 + closed 4h directional context (close > open for long / close < open for short)
- **A2**: trades=None avg_R=None OOS_n=None OOS_avg_R=None | UNAVAILABLE WITHOUT ENGINE CHANGE | UNAVAILABLE WITHOUT ENGINE CHANGE — production BOS not used (COMBO_02 independence + no production import)
- **A3**: trades=149 avg_R=0.12257170603554839 OOS_n=38 OOS_avg_R=0.055419759864436126 | STRONGER_RESEARCH_EVIDENCE | A0 + retest within max_retest_bars after breakout
- **A0_E2**: trades=195 avg_R=0.07252471093116236 OOS_n=45 OOS_avg_R=-0.062093484562409186 | STRONGER_RESEARCH_EVIDENCE | A0 with entry E2 next open

## Verdict: `EXPLORATORY ONLY`

Reason: OOS net expectancy is negative (avg_R=-0.070, PF=0.881). Full-sample positive expectancy does not promote under chronological ranking.

CROSS_SYMBOL VALIDATION: UNAVAILABLE IN THIS EXPERIMENT

Slippage/funding: UNAVAILABLE WITHOUT ENGINE CHANGE

## Final safety

```text
READ ONLY
NO EXISTING CODE MODIFIED
NO EXISTING REPORTS OVERWRITTEN
NO DATABASE DATA MODIFIED
NO STRATEGY MODIFIED
NO LIVE/PAPER EXECUTION
NO DEPLOYMENT
```

