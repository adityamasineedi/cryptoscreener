# Live ↔ Backtest Entry Parity — Validation Report

LIVE↔BACKTEST PARITY RESEARCH ONLY. No live orders. No dynamic production Telegram. No COMBO_02 parameter changes. No V1 modifications. Descriptive entry deviation only.

## Verdict

- LIVE ↔ BACKTEST PARITY: **PASS**
- NO-LOOKAHEAD: **PASS**
- ENTRY PRICE TRACEABILITY: **PASS**
- LATENCY TRACEABILITY: **PASS**
- TELEGRAM TRACEABILITY: **PASS**
- NO LIVE ORDERS: **CONFIRMED**
- NO PRODUCTION APPROVAL: **CONFIRMED**
- NO STRATEGY CHANGES: **CONFIRMED**

## AS-OF Parity

- total live candidates: 38
- AS-OF matches: 38
- AS-OF mismatches: 0
- match rate: 1.0
- future_data_fails: 0

## Entry Deviation

- P50: 0.0
- P75: 0.0
- P90: 0.0
- P95: 0.0
- P99: 0.0
- MAX: 0.0

## ENTRY_PRICE_CHECK

- VALID: 38
- STALE / STALE_ENTRY: 0
- UNAVAILABLE: 0

## Latency (ms)

- candle_close_to_signal_ms: P50=9.0 P95=11.299999999999997 P99=13.0 MAX=13.0
- signal_to_trade_plan_ms: P50=11.5 P95=14.149999999999999 P99=16.260000000000005 MAX=17.0
- trade_plan_to_alert_ms: P50=0.0 P95=0.0 P99=0.0 MAX=0.0
- alert_to_paper_entry_ms: P50=0.0 P95=0.0 P99=0.0 MAX=0.0
- candle_close_to_paper_entry_ms: P50=20.0 P95=26.299999999999997 P99=29.260000000000005 MAX=30.0
- total_signal_to_entry_ms: P50=11.5 P95=14.149999999999999 P99=16.260000000000005 MAX=17.0

## Telegram (mock / research)

- generated: 38
- sent: 0
- blocked: 38
- failed: 0

## Notes

- Entry deviation buckets are descriptive only; no validity declared.
- Dynamic production Telegram remains disabled.
- V1 and COMBO_02 parameters were not modified.
