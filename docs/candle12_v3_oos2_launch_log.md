# CANDLE12_V3_EXEC_FIX/OOS-2: launch log

Launch owner: Claude Code session b63782ae (cryptoscreener-8e). All times UTC.

| Time | Step | Result |
|---|---|---|
| 17:14 | Stop concurrent writes | cryptoscreener-48 and cryptoscreener-55 confirmed read-only |
| 17:27 | Line-ending pins + runtime ignore | `.gitattributes` / `.gitignore`; fingerprinted files normalised to CRLF (content unchanged) |
| 17:32 | Tests + smoke | 197 passed; SMOKE_PASS, 5/5 reconciled, 0 exceptions |
| 17:33–17:37 | Commits | b5b3fd9 (CANDLE12 baseline), 9ac1551 (shared fingerprinted modules, bytes = OOS-1 hashes), then 7 unrelated commits; tree clean |
| 17:38 | Tests on committed tree | 197 passed; fresh-worktree fingerprints equal to the working tree |
| 17:38:59 | `freeze-oos` | tag `candle12-v3-oos-2-freeze-2026-10-15`, frozen commit 03c70c9, lock commit 80d62d9, lock_hash `34eb8235…83af`, supersession of OOS-1 verified |
| 17:39 | Backfill BTCUSDT 2026-09-04T08:00 → now | gaps none; 74 funding settlements inserted; **1 candle conflict** (below) |
| 17:40 | Data correction + re-backfill | conflicts none, `ok: true` |

## Data correction: `ohlcv` BTCUSDT 1h 2026-10-01T04:00:00Z

The stored row (source `db_chart_hydrate`) was a forming candle captured mid-hour and never updated. Its open matched; high, low, close and volume did not. It was replaced with Binance's final kline (`GET /fapi/v1/klines`). This is the only mismatch among the 993 warm-up bars. It lies in the warm-up window, before the OOS start. No value was invented.

| Field | Stored (old) | Exchange final (new) |
|---|---:|---:|
| open | 83724.6 | 83724.6 |
| high | 83788.0 | 84169.3 |
| low | 83715.2 | 83682.6 |
| close | 83748.0 | 84072.9 |
| volume | 458.461 | 5713.136 |
| source | db_chart_hydrate | binance_final_kline_correction |

Corrected at 2026-10-15T17:40:11.851174+00:00. To revert, write the old values back to that row.
