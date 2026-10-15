### Structure methods (dev, core coins)

| coin | tf | method | swings_per_month | median_delay_bars | median_swing_atr | median_travel_at_confirm_atr | pct_up | pct_down | pct_range |
|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | 1h | fractal k=3 | 150.9 | 3.0 | 2.21 | 1.21 | 30 | 28 | 42 |
| BTCUSDT | 1h | DC 2.5×ATR | 74.2 | 3.0 | 4.15 | 2.33 | 35 | 35 | 31 |
| BTCUSDT | 1h | zigzag 1.5% | 117.2 | 1.0 | 2.84 | 1.53 | 37 | 35 | 28 |
| BTCUSDT | 4h | fractal k=3 | 38.3 | 3.0 | 2.22 | 1.18 | 32 | 27 | 41 |
| BTCUSDT | 4h | DC 2.5×ATR | 18.7 | 3.0 | 4.14 | 2.33 | 38 | 29 | 33 |
| BTCUSDT | 4h | zigzag 3.0% | 31.8 | 1.0 | 2.85 | 1.59 | 42 | 27 | 31 |
| ETHUSDT | 1h | fractal k=3 | 149.8 | 3.0 | 2.25 | 1.25 | 30 | 27 | 43 |
| ETHUSDT | 1h | DC 2.5×ATR | 73.3 | 3.0 | 4.15 | 2.31 | 35 | 32 | 33 |
| ETHUSDT | 1h | zigzag 1.5% | 170.5 | 1.0 | 2.45 | 1.27 | 36 | 30 | 33 |
| ETHUSDT | 4h | fractal k=3 | 37.9 | 3.0 | 2.34 | 1.22 | 29 | 29 | 42 |
| ETHUSDT | 4h | DC 2.5×ATR | 18.5 | 3.0 | 4.1 | 2.31 | 38 | 33 | 30 |
| ETHUSDT | 4h | zigzag 3.0% | 45.9 | 1.0 | 2.38 | 1.25 | 37 | 36 | 27 |
| SOLUSDT | 1h | fractal k=3 | 143.9 | 3.0 | 2.28 | 1.27 | 30 | 30 | 40 |
| SOLUSDT | 1h | DC 2.5×ATR | 64.5 | 4.0 | 4.12 | 2.35 | 33 | 33 | 34 |
| SOLUSDT | 1h | zigzag 1.5% | 356.2 | 1.0 | 1.73 | 0.89 | 35 | 33 | 32 |
| SOLUSDT | 4h | fractal k=3 | 36.2 | 3.0 | 2.29 | 1.26 | 30 | 30 | 40 |
| SOLUSDT | 4h | DC 2.5×ATR | 16.1 | 3.0 | 4.19 | 2.34 | 36 | 32 | 32 |
| SOLUSDT | 4h | zigzag 3.0% | 95.8 | 1.0 | 1.76 | 0.88 | 35 | 34 | 31 |

### 4h DC structural trend vs daily volatility-normalised regime (dev)

| coin | bars_4h | pct_dc_trend_nonzero | pct_z_trend_nonzero | pct_same_sign_when_both_trend | pct_opposite_sign_when_both_trend |
|---|---|---|---|---|---|
| BTCUSDT | 5388 | 67 | 32 | 76 | 24 |
| ETHUSDT | 5388 | 70 | 30 | 75 | 25 |
| SOLUSDT | 5388 | 68 | 35 | 75 | 25 |

### Setup frequency per hypothesis (dev, sum of BTC+ETH+SOL; limit setups are orders, not fills)

| hyp | setups_3coins | per_month_3coins | long | short | limit_orders | median_stop_pct | median_stop_atr | median_cost_R |
|---|---|---|---|---|---|---|---|---|
| H1 | 621 | 21.1 | 303 | 318 | 0 | 1.63 | 0.95 | 0.098 |
| H2 | 433 | 14.6 | 243 | 190 | 379 | 2.04 | 1.1 | 0.078 |
| H3 | 501 | 17.0 | 246 | 255 | 418 | 3.11 | 3.61 | 0.051 |
| H4 | 149 | 5.1 | 70 | 79 | 149 | 1.31 | 0.75 | 0.122 |
| H5 | 520 | 17.6 | 253 | 267 | 0 | 2.57 | 1.52 | 0.062 |
| H6 | 1288 | 43.7 | 649 | 639 | 0 | 1.3 | 1.5 | 0.123 |
| H7 | 333 | 11.3 | 179 | 154 | 0 | 2.54 | 1.5 | 0.063 |
| H8 | 898 | 30.5 | 527 | 371 | 0 | 2.08 | 2.29 | 0.077 |

Per coin:

| hyp | coin | setups | per_month | long | short | limit_orders | median_stop_pct | median_stop_atr | median_cost_R |
|---|---|---|---|---|---|---|---|---|---|
| H1 | BTCUSDT | 226 | 7.7 | 101 | 125 | 0 | 1.36 | 1.04 | 0.118 |
| H2 | BTCUSDT | 140 | 4.7 | 77 | 63 | 119 | 1.55 | 1.1 | 0.103 |
| H3 | BTCUSDT | 180 | 6.1 | 84 | 96 | 151 | 2.57 | 3.61 | 0.062 |
| H4 | BTCUSDT | 58 | 2.0 | 24 | 34 | 58 | 1.14 | 0.75 | 0.14 |
| H5 | BTCUSDT | 171 | 5.8 | 85 | 86 | 0 | 1.91 | 1.52 | 0.084 |
| H6 | BTCUSDT | 389 | 13.2 | 205 | 184 | 0 | 1.04 | 1.5 | 0.154 |
| H7 | BTCUSDT | 100 | 3.4 | 56 | 44 | 0 | 2.1 | 1.5 | 0.076 |
| H8 | BTCUSDT | 359 | 12.2 | 212 | 147 | 0 | 1.69 | 2.29 | 0.095 |
| H1 | ETHUSDT | 228 | 7.7 | 121 | 107 | 0 | 1.63 | 0.95 | 0.098 |
| H2 | ETHUSDT | 159 | 5.4 | 90 | 69 | 140 | 2.04 | 1.1 | 0.078 |
| H3 | ETHUSDT | 164 | 5.6 | 79 | 85 | 137 | 3.11 | 3.57 | 0.051 |
| H4 | ETHUSDT | 52 | 1.8 | 28 | 24 | 52 | 1.31 | 0.75 | 0.122 |
| H5 | ETHUSDT | 165 | 5.6 | 83 | 82 | 0 | 2.57 | 1.52 | 0.062 |
| H6 | ETHUSDT | 439 | 14.9 | 213 | 226 | 0 | 1.3 | 1.5 | 0.123 |
| H7 | ETHUSDT | 122 | 4.1 | 66 | 56 | 0 | 2.54 | 1.48 | 0.063 |
| H8 | ETHUSDT | 313 | 10.6 | 182 | 131 | 0 | 2.08 | 2.36 | 0.077 |
| H1 | SOLUSDT | 167 | 5.7 | 81 | 86 | 0 | 2.41 | 0.88 | 0.066 |
| H2 | SOLUSDT | 134 | 4.5 | 76 | 58 | 120 | 3.17 | 1.1 | 0.05 |
| H3 | SOLUSDT | 157 | 5.3 | 83 | 74 | 130 | 5.1 | 3.72 | 0.031 |
| H4 | SOLUSDT | 39 | 1.3 | 18 | 21 | 39 | 2.17 | 0.75 | 0.074 |
| H5 | SOLUSDT | 184 | 6.2 | 85 | 99 | 0 | 3.76 | 1.53 | 0.043 |
| H6 | SOLUSDT | 460 | 15.6 | 231 | 229 | 0 | 1.98 | 1.5 | 0.081 |
| H7 | SOLUSDT | 111 | 3.8 | 57 | 54 | 0 | 4.47 | 1.51 | 0.036 |
| H8 | SOLUSDT | 226 | 7.7 | 133 | 93 | 0 | 3.39 | 2.26 | 0.047 |
