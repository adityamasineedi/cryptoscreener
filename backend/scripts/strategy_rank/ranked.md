# Strategy ranking

Rows loaded: {'combo': 2800, 'scalp': 170, 'grid': 390, 'multicap': 330}
Cells OK: 1866, not run or error: 1584

## scalp

Cells: 340, net positive: 35, passing the rule: 3

### Strategies (all coins combined)

| # | Strategy | Dir | TF | Coins passing | Coins net+ | Trades | Net USD | Fees USD | Worst coin | Passing coins |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | COMBO_02 | SHORT | 15m | 2/10 | 5/10 | 242 | -272.40 | -344.42 | -179.19 | ADA LINK |
| 2 | LARGE_CAP_SWEEP_CHOCH | LONG | 15m | 1/10 | 3/10 | 1317 | -1975.41 | -1650.17 | -834.73 | TRX |
| 3 | INTRADAY_BOS_SD_V1.C | LONG | 5m | 0/10 | 3/10 | 5 | +2.05 | -1.01 | -0.83 | - |
| 4 | COMBO_04_HTF | LONG | 15m | 0/10 | 0/10 | 0 | +0.00 | 0.00 | +0.00 | - |
| 5 | COMBO_04_HTF | SHORT | 15m | 0/10 | 0/10 | 0 | +0.00 | 0.00 | +0.00 | - |
| 6 | INTRADAY_BOS_SD_V1.D | LONG | 5m | 0/10 | 0/10 | 0 | +0.00 | 0.00 | +0.00 | - |
| 7 | INTRADAY_BOS_SD_V1.D | SHORT | 5m | 0/10 | 0/10 | 0 | +0.00 | 0.00 | +0.00 | - |
| 8 | INTRADAY_BOS_SD_V1.C | SHORT | 5m | 0/10 | 1/10 | 2 | -1.97 | -0.23 | -2.57 | - |
| 9 | INTRADAY_BOS_SD_V1.B | SHORT | 5m | 0/10 | 2/10 | 57 | -38.76 | -17.88 | -13.23 | - |
| 10 | INTRADAY_BOS_SD_V1.B | LONG | 5m | 0/10 | 2/10 | 74 | -49.04 | -21.38 | -20.87 | - |
| 11 | COMBO_02 | LONG | 15m | 0/10 | 5/10 | 336 | -63.81 | -488.39 | -192.61 | - |
| 12 | CANDLE12_V2 | LONG | 15m | 0/10 | 3/10 | 571 | -881.95 | -768.09 | -446.46 | - |
| 13 | SMALL_CAP_VOLUME_BOS | LONG | 15m | 0/10 | 3/10 | 451 | -993.77 | -534.43 | -370.75 | - |
| 14 | INTRADAY_BOS_SD_V1.A | LONG | 5m | 0/10 | 0/10 | 1955 | -1011.50 | -558.91 | -180.86 | - |
| 15 | INTRADAY_BOS_SD_V1.A | SHORT | 5m | 0/10 | 0/10 | 1959 | -1078.93 | -549.65 | -139.85 | - |

### Top cells

| # | Strategy | Dir | Symbol | TF | Trades | Win % | Net USD | Fees USD | PF | Max DD USD | Passes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | LARGE_CAP_SWEEP_CHOCH | LONG | TRXUSDT | 15m | 120 | 43.3 | +273.15 | -187.23 | 1.37 | 111.23 | yes |
| 2 | LARGE_CAP_SWEEP_CHOCH | LONG | ADAUSDT | 15m | 139 | 39.6 | +193.91 | -152.06 | 1.11 | 399.78 | no: PF <= 1.2 |
| 3 | COMBO_02_LOCAL | LONG | AVAXUSDT | 15m | 265 | 37.0 | +176.22 | -363.48 | 1.08 | 257.28 | no: PF <= 1.2 |
| 4 | COMBO_02 | LONG | ADAUSDT | 15m | 13 | 61.5 | +170.76 | -18.23 | 3.01 | 26.81 | no: trades < 15 |
| 5 | COMBO_01 | LONG | AVAXUSDT | 15m | 268 | 36.9 | +149.64 | -364.72 | 1.06 | 276.58 | no: PF <= 1.2 |
| 6 | COMBO_01 | LONG | ADAUSDT | 15m | 240 | 34.2 | +118.24 | -311.59 | 1.05 | 412.52 | no: PF <= 1.2 |
| 7 | CANDLE12_V2 | LONG | SOLUSDT | 15m | 52 | 40.4 | +115.50 | -67.68 | 1.19 | 108.86 | no: PF <= 1.2 |
| 8 | LARGE_CAP_SWEEP_CHOCH | LONG | BNBUSDT | 5m | 377 | 37.7 | +111.42 | -581.55 | 1.04 | 224.59 | no: PF <= 1.2 |
| 9 | COMBO_02_LOCAL | LONG | ADAUSDT | 15m | 232 | 34.1 | +69.55 | -302.13 | 1.03 | 392.59 | no: PF <= 1.2 |
| 10 | COMBO_02 | SHORT | ADAUSDT | 15m | 32 | 40.6 | +66.22 | -42.71 | 1.20 | 139.71 | yes |
| 11 | CANDLE12_V2 | SHORT | AVAXUSDT | 5m | 143 | 37.8 | +65.56 | -218.44 | 1.05 | 214.42 | no: PF <= 1.2 |
| 12 | COMBO_02 | SHORT | LINKUSDT | 15m | 27 | 37.0 | +63.48 | -42.26 | 1.26 | 117.13 | yes |
| 13 | CANDLE12_V2 | LONG | ETHUSDT | 15m | 48 | 37.5 | +59.00 | -60.35 | 1.10 | 125.13 | no: PF <= 1.2 |
| 14 | CANDLE12_V2 | LONG | ADAUSDT | 15m | 64 | 35.9 | +53.92 | -72.23 | 1.06 | 204.04 | no: PF <= 1.2 |
| 15 | SMALL_CAP_VOLUME_BOS | LONG | TRXUSDT | 15m | 65 | 47.7 | +52.32 | -102.63 | 1.14 | 63.74 | no: PF <= 1.2 |

## day

Cells: 406, net positive: 88, passing the rule: 37

### Strategies (all coins combined)

| # | Strategy | Dir | TF | Coins passing | Coins net+ | Trades | Net USD | Fees USD | Worst coin | Passing coins |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | CANDLE12_V2 | LONG | 1h | 6/10 | 7/10 | 194 | +525.68 | -164.10 | -97.27 | ADA AVAX BTC DOGE ETH SOL |
| 2 | LARGE_CAP_SWEEP_CHOCH | LONG | 1h | 5/10 | 7/10 | 367 | +986.37 | -319.56 | -106.37 | AVAX BNB BTC ETH TRX |
| 3 | COMBO_02 | LONG | 1h | 5/10 | 8/10 | 210 | +785.82 | -256.15 | -58.71 | BTC ETH LINK SOL XRP |
| 4 | COMBO_01 | LONG | 1h | 4/10 | 6/10 | 608 | +1035.92 | -733.96 | -150.98 | ADA BNB SOL XRP |
| 5 | COMBO_02_LOCAL | LONG | 1h | 4/10 | 7/10 | 602 | +968.19 | -734.10 | -150.98 | ADA BNB SOL XRP |
| 6 | OSE_A_corrected_baseline | LONG | 1h | 3/10 | 8/10 | 1210 | +824.58 | -1071.49 | -54.87 | BNB LINK XRP |
| 7 | OSE_B_acceptance | LONG | 1h | 2/10 | 5/10 | 1020 | +140.13 | -842.89 | -160.60 | LINK XRP |
| 8 | OSE_C_retest | LONG | 1h | 2/10 | 7/10 | 614 | -101.52 | -567.06 | -338.61 | LINK XRP |
| 9 | OSE_D_faster_confirmation | LONG | 1h | 1/10 | 6/10 | 1426 | +634.78 | -1323.92 | -217.68 | XRP |
| 10 | COMBO_02_V2 | LONG | 1h | 1/3 | 3/3 | 106 | +359.45 | -132.88 | +34.81 | SOL |
| 11 | COMBO_03_TRANSITION_B | SHORT | 1h | 1/10 | 4/10 | 126 | +51.12 | -170.82 | -108.97 | LINK |
| 12 | OSE_C_retest | SHORT | 1h | 1/10 | 2/10 | 523 | -600.27 | -443.68 | -187.97 | LINK |
| 13 | OSE_D_faster_confirmation | SHORT | 1h | 1/10 | 3/10 | 1349 | -999.25 | -1190.22 | -331.79 | XRP |
| 14 | OSE_B_acceptance | SHORT | 1h | 1/10 | 1/10 | 958 | -1159.08 | -717.51 | -291.69 | LINK |
| 15 | SMALL_CAP_VOLUME_BOS | LONG | 1h | 0/10 | 7/10 | 113 | +47.53 | -83.04 | -186.44 | - |

### Top cells

| # | Strategy | Dir | Symbol | TF | Trades | Win % | Net USD | Fees USD | PF | Max DD USD | Passes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | COMBO_02_LOCAL | LONG | BNBUSDT | 1h | 49 | 49.0 | +449.13 | -76.29 | 2.07 | 113.78 | yes |
| 2 | COMBO_01 | LONG | BNBUSDT | 1h | 49 | 49.0 | +442.76 | -74.76 | 2.03 | 162.13 | yes |
| 3 | COMBO_01 | LONG | ADAUSDT | 1h | 60 | 43.3 | +372.75 | -55.89 | 1.54 | 210.37 | yes |
| 4 | OSE_D_faster_confirmation | LONG | XRPUSDT | 1h | 131 | 45.0 | +332.32 | -113.62 | 1.35 | 112.24 | yes |
| 5 | COMBO_02_LOCAL | LONG | ADAUSDT | 1h | 58 | 43.1 | +327.66 | -53.69 | 1.49 | 189.91 | yes |
| 6 | OSE_A_corrected_baseline | LONG | LINKUSDT | 1h | 126 | 47.6 | +311.63 | -89.77 | 1.35 | 164.54 | yes |
| 7 | LARGE_CAP_SWEEP_CHOCH | LONG | BNBUSDT | 1h | 37 | 48.6 | +305.88 | -43.31 | 1.85 | 66.59 | yes |
| 8 | COMBO_02_LOCAL | LONG | SOLUSDT | 1h | 55 | 40.0 | +287.31 | -66.93 | 1.49 | 156.03 | yes |
| 9 | COMBO_02_V2 | LONG | SOLUSDT | 1h | 39 | 43.6 | +273.93 | -46.70 | 1.62 | 120.60 | yes |
| 10 | LARGE_CAP_SWEEP_CHOCH | LONG | ETHUSDT | 1h | 36 | 47.2 | +268.65 | -30.01 | 1.70 | 62.55 | yes |
| 11 | COMBO_01 | LONG | SOLUSDT | 1h | 56 | 39.3 | +267.29 | -67.78 | 1.44 | 175.26 | yes |
| 12 | OSE_D_faster_confirmation | SHORT | XRPUSDT | 1h | 132 | 43.9 | +254.83 | -112.53 | 1.28 | 94.36 | yes |
| 13 | OSE_B_acceptance | LONG | XRPUSDT | 1h | 92 | 46.7 | +247.52 | -71.25 | 1.40 | 122.77 | yes |
| 14 | OSE_D_faster_confirmation | LONG | LINKUSDT | 1h | 153 | 48.4 | +234.79 | -119.59 | 1.18 | 149.37 | no: PF <= 1.2 |
| 15 | LARGE_CAP_SWEEP_CHOCH | LONG | TRXUSDT | 1h | 30 | 50.0 | +229.15 | -41.14 | 2.04 | 57.34 | yes |

## swing

Cells: 780, net positive: 92, passing the rule: 0

### Strategies (all coins combined)

| # | Strategy | Dir | TF | Coins passing | Coins net+ | Trades | Net USD | Fees USD | Worst coin | Passing coins |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | COMBO_03_TRANSITION_B | SHORT | 4h | 0/10 | 6/10 | 29 | +217.74 | -23.26 | -61.57 | - |
| 2 | COMBO_02_V2_1_A | SHORT | 1d | 0/10 | 5/10 | 9 | +127.51 | -3.56 | -41.32 | - |
| 3 | COMBO_03_TRANSITION_B | LONG | 4h | 0/10 | 4/10 | 25 | +119.98 | -24.39 | -42.60 | - |
| 4 | COMBO_01 | SHORT | 1d | 0/10 | 5/10 | 20 | +115.07 | -6.34 | -40.78 | - |
| 5 | COMBO_02_LOCAL | SHORT | 1d | 0/10 | 5/10 | 20 | +115.07 | -6.34 | -40.78 | - |
| 6 | COMBO_02_V2 | SHORT | 1d | 0/10 | 5/10 | 18 | +108.09 | -9.41 | -41.26 | - |
| 7 | COMBO_02_V2_1_A | LONG | 1d | 0/10 | 5/10 | 17 | +94.98 | -7.17 | -61.14 | - |
| 8 | COMBO_02_V2_1B | SHORT | 1d | 0/10 | 4/10 | 19 | +88.89 | -10.26 | -61.55 | - |
| 9 | COMBO_02 | SHORT | 1d | 0/10 | 3/10 | 6 | +59.06 | -0.94 | -20.27 | - |
| 10 | COMBO_01 | LONG | 1d | 0/10 | 5/10 | 32 | +56.68 | -11.05 | -42.95 | - |
| 11 | COMBO_02_LOCAL | LONG | 1d | 0/10 | 5/10 | 32 | +56.68 | -11.05 | -42.95 | - |
| 12 | COMBO_02_V2_1B | LONG | 1d | 0/10 | 4/10 | 20 | +46.47 | -7.96 | -61.14 | - |
| 13 | COMBO_02 | LONG | 4h | 0/10 | 6/10 | 87 | +16.64 | -72.27 | -162.79 | - |
| 14 | COMBO_03 | LONG | 4h | 0/10 | 0/10 | 0 | +0.00 | 0.00 | +0.00 | - |
| 15 | COMBO_03 | LONG | 1d | 0/10 | 0/10 | 0 | +0.00 | 0.00 | +0.00 | - |

### Top cells

| # | Strategy | Dir | Symbol | TF | Trades | Win % | Net USD | Fees USD | PF | Max DD USD | Passes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | COMBO_03_TRANSITION_B | LONG | BNBUSDT | 4h | 4 | 100.0 | +157.77 | -4.95 | - | 0.00 | no: trades < 15 |
| 2 | COMBO_03_TRANSITION_B | SHORT | ADAUSDT | 4h | 3 | 100.0 | +150.10 | -2.48 | - | 0.00 | no: trades < 15 |
| 3 | COMBO_01 | SHORT | SOLUSDT | 4h | 12 | 50.0 | +141.42 | -8.86 | 2.14 | 103.94 | no: trades < 15 |
| 4 | COMBO_02_LOCAL | SHORT | SOLUSDT | 4h | 12 | 50.0 | +141.42 | -8.86 | 2.14 | 103.94 | no: trades < 15 |
| 5 | COMBO_02 | LONG | AVAXUSDT | 4h | 7 | 42.9 | +114.67 | -7.53 | 2.37 | 63.34 | no: trades < 15 |
| 6 | COMBO_02_V2_1B | SHORT | XRPUSDT | 1d | 2 | 100.0 | +87.14 | -0.40 | - | 0.00 | no: trades < 15 |
| 7 | COMBO_02_V2 | SHORT | XRPUSDT | 1d | 2 | 100.0 | +86.18 | -0.40 | - | 0.00 | no: trades < 15 |
| 8 | COMBO_02 | LONG | BTCUSDT | 4h | 9 | 44.4 | +86.07 | -9.11 | 1.94 | 34.25 | no: trades < 15 |
| 9 | COMBO_01 | SHORT | LINKUSDT | 4h | 10 | 40.0 | +85.94 | -5.61 | 1.70 | 82.36 | no: trades < 15 |
| 10 | COMBO_02_LOCAL | SHORT | LINKUSDT | 4h | 10 | 40.0 | +85.94 | -5.61 | 1.70 | 82.36 | no: trades < 15 |
| 11 | COMBO_03_TRANSITION_B | LONG | XRPUSDT | 4h | 2 | 100.0 | +81.26 | -1.33 | - | 0.00 | no: trades < 15 |
| 12 | COMBO_02_V2_1_A | LONG | SOLUSDT | 1d | 1 | 100.0 | +79.82 | -0.18 | - | 0.00 | no: trades < 15 |
| 13 | COMBO_02_V2_1B | LONG | SOLUSDT | 1d | 1 | 100.0 | +79.82 | -0.18 | - | 0.00 | no: trades < 15 |
| 14 | COMBO_01 | LONG | XRPUSDT | 4h | 9 | 44.4 | +78.98 | -3.55 | 1.78 | 41.16 | no: trades < 15 |
| 15 | COMBO_02_LOCAL | LONG | XRPUSDT | 4h | 9 | 44.4 | +78.98 | -3.55 | 1.78 | 41.16 | no: trades < 15 |

## grid

Cells: 340, net positive: 179, passing the rule: 41

### Strategies (all coins combined)

| # | Strategy | Dir | TF | Coins passing | Coins net+ | Trades | Net USD | Fees USD | Worst coin | Passing coins |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | GTB-ARI-8 | LONG | 15m | 5/10 | 5/10 | 370 | +131.30 | -22.28 | -26.35 | ADA BNB DOGE LINK TRX |
| 2 | GTB-GEO-12 | LONG | 1h | 4/10 | 9/10 | 179 | +179.12 | -7.58 | -1.34 | ADA AVAX DOGE ETH |
| 3 | GTB-GEO-12 | LONG | 15m | 4/10 | 5/10 | 868 | +159.10 | -32.75 | -25.62 | ADA DOGE LINK TRX |
| 4 | GTB-GEO-8 | LONG | 15m | 4/10 | 5/10 | 394 | +156.54 | -23.64 | -26.54 | ADA DOGE LINK TRX |
| 5 | GTB-ARI-12 | LONG | 15m | 4/10 | 5/10 | 799 | +113.49 | -30.39 | -24.88 | ADA DOGE LINK TRX |
| 6 | GTB-GEO-4 | LONG | 15m | 3/10 | 5/10 | 116 | +237.51 | -15.33 | -19.70 | ADA DOGE LINK |
| 7 | GTB-ARI-4 | LONG | 15m | 3/10 | 5/10 | 105 | +181.63 | -14.07 | -15.71 | ADA DOGE LINK |
| 8 | GTB-GEO-8 | LONG | 1h | 2/10 | 8/10 | 87 | +175.07 | -5.96 | -16.64 | ADA DOGE |
| 9 | GTB-ARI-12 | LONG | 1h | 2/10 | 9/10 | 144 | +139.36 | -6.22 | -10.74 | ADA DOGE |
| 10 | GTB-GEO-4 | LONG | 5m | 2/10 | 5/10 | 111 | +16.54 | -15.53 | -21.22 | LINK TRX |
| 11 | GTB-ARI-4 | LONG | 5m | 2/10 | 5/10 | 113 | +11.84 | -15.73 | -23.00 | LINK TRX |
| 12 | GTB-ARI-12 | LONG | 4h | 1/10 | 10/10 | 84 | +171.98 | -3.64 | +2.84 | AVAX |
| 13 | GTB-GEO-12 | LONG | 4h | 1/10 | 8/10 | 99 | +133.48 | -4.33 | -4.49 | DOGE |
| 14 | GTB-ARI-12 | LONG | 5m | 1/10 | 4/10 | 824 | -38.61 | -32.04 | -20.89 | TRX |
| 15 | GTB-GEO-12 | LONG | 5m | 1/10 | 4/10 | 801 | -42.71 | -31.27 | -21.78 | TRX |

### Top cells

| # | Strategy | Dir | Symbol | TF | Trades | Win % | Net USD | Fees USD | PF | Max DD USD | Passes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | GTB-GEO-4 | LONG | LINKUSDT | 15m | 41 | 97.6 | +183.33 | -4.63 | 4.68 | 41.01 | yes |
| 2 | GTB-GEO-8 | LONG | LINKUSDT | 15m | 127 | 99.2 | +135.71 | -6.84 | 4.11 | 35.71 | yes |
| 3 | GTB-ARI-4 | LONG | ETHUSDT | 1d | 1 | 100.0 | +128.49 | -0.13 | - | 36.50 | no: trades < 15 |
| 4 | GTB-GEO-12 | LONG | LINKUSDT | 15m | 257 | 99.6 | +117.21 | -9.05 | 3.82 | 34.00 | yes |
| 5 | GTB-ARI-4 | LONG | LINKUSDT | 15m | 26 | 96.2 | +116.62 | -2.97 | 4.95 | 27.83 | yes |
| 6 | GTB-ARI-8 | LONG | ETHUSDT | 1d | 4 | 100.0 | +108.65 | -0.22 | - | 32.76 | no: trades < 15 |
| 7 | GTB-ARI-8 | LONG | LINKUSDT | 15m | 91 | 98.9 | +94.42 | -4.96 | 3.80 | 29.19 | yes |
| 8 | GTB-ARI-4 | LONG | BNBUSDT | 1d | 1 | 100.0 | +90.60 | -0.12 | - | 13.26 | no: trades < 15 |
| 9 | GTB-ARI-12 | LONG | LINKUSDT | 15m | 199 | 99.5 | +87.63 | -7.06 | 3.49 | 28.65 | yes |
| 10 | GTB-GEO-4 | LONG | ETHUSDT | 1d | 1 | 100.0 | +80.47 | -0.12 | - | 36.50 | no: trades < 15 |
| 11 | GTB-ARI-4 | LONG | LINKUSDT | 1h | 5 | 80.0 | +77.28 | -0.70 | 2.45 | 56.20 | no: trades < 15 |
| 12 | GTB-GEO-8 | LONG | ETHUSDT | 1d | 4 | 100.0 | +74.75 | -0.21 | - | 16.51 | no: trades < 15 |
| 13 | GTB-GEO-8 | LONG | DOGEUSDT | 1h | 20 | 95.0 | +71.78 | -1.25 | 2.30 | 49.77 | yes |
| 14 | GTB-ARI-4 | LONG | DOGEUSDT | 1h | 5 | 80.0 | +67.63 | -0.70 | 2.43 | 50.65 | no: trades < 15 |
| 15 | GTB-GEO-4 | LONG | DOGEUSDT | 1h | 5 | 80.0 | +66.69 | -0.70 | 2.53 | 51.38 | no: trades < 15 |
