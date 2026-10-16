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

## Restart drill (pre-window), 2026-10-15

| Time (UTC) | Step | Evidence |
|---|---|---|
| 18:01 | Kill-switch drill | engage gives `status.kill_switch.engaged=true`; release gives false; no exception |
| 18:12:14 | Hard kill of the detached runner (launcher 8404, interpreter 12868), while idle | store intact: no torn lines; `completed_bar_index` 993 = last; no inflight; not halted |
| 18:27:16 | `preflight --recover` | PREFLIGHT_PASS 12/12, no warnings |
| 18:27–18:30 | Runner started through task `CANDLE12_V3_OOS2_Runner` (always `--recover`) | first start refused: `clock_skew` −1800 ms single-sample noise. NTP offset −0.03 s. The supervisor now retries refusals every 60 s (deferred item D6). |
| 18:30:22 | Stale-lock takeover (runner PID 6248) | `RUNNER_CRASH_RECOVERED`; exception `RUNNER_CRASH:2026-10-15T17:41:16.966785+00:00`; `PROCESS_RESTART` alerts |
| 18:30:24 | Recovery | `ADAPTER_RESUMED`; `RECOVERY_COMPLETE` `REC-20261015T183024053556Z` `RECONCILED` |
| 18:30 | No loss or duplication | pre-restart events and heartbeats are an unchanged prefix; event seq contiguous; flat; no orders |
| 19:00:07 | Next closed candle | bar 2026-10-15T18:00Z processed |
| 19:00:30 | Exception resolved | written note on `RUNNER_CRASH:…17:41:16…`; open exceptions none; store eligible |

## Service evidence (task `CANDLE12_V3_OOS2_Runner`, supervisor `backend/scripts/candle12_v3_oos2_service/`)

| Test | Time (UTC) | Result |
|---|---|---|
| B: supervisor killed, runner left alive | 19:01:02 | runner PID 6248 kept running; the 5-minute watchdog started a new supervisor at 19:05:40, which logged `adopting live runner pid=6248` and started no second runner |
| Single instance | 19:10:37 tick | still one supervisor (task IgnoreNew plus OS file lock) |
| A: runner killed | 19:12:25 | supervisor restarted at once; refused with `CONCURRENT_RUN` every 60 s while the lock was fresh (no storm exception); took over at 19:26:32 (runner PID 16420) with `RUNNER_CRASH_RECOVERED`, `ADAPTER_RESUMED`, `RECOVERY_COMPLETE` |
| Reboot / logon | not run | manual test plan: `docs/candle12_v3_oos2_first_24h_runbook.md` section 4, and the risk note below |

**Accepted risks:**
- Without admin rights the task starts at **logon**, not at boot. `install_task.ps1 -AtStartup`, run elevated, registers a boot-time S4U task.
- A hard stop always costs the 15-minute lock takeover and a `RUNNER_CRASH` exception, because the runner has no graceful stop in frozen code.
- The laptop was on battery (58%, discharging) at 18:47 UTC. It must stay on mains power.

## Maintenance: funding backfill, 2026-10-16 (owner-approved)

| Time (UTC) | Action / check | Result |
|---|---|---|
| 02:58–03:01 | Read-only post-window check | runner healthy; the 00:00 and 01:00 window bars and the 02:00 bar were processed. `grid_funding_settlements` lacked the 2026-10-16 00:00 settlement: the runbook's 00:20 backfill had not run. Laptop on battery. |
| 03:36:57–03:37:07 | `backfill-data --symbols BTCUSDT --start 2026-09-04T08:00:00+00:00 --end now` (approved) | `ok: true`. Candles: 1003 fetched, 0 inserted, 1003 existing, no gaps or conflicts. Funding: 126 fetched, **1 inserted**, 125 existing, no gaps or conflicts. Nothing overwritten. |
| 03:37 | Verification | 00:00 settlement stored exactly once (`2026-10-16T00:00:00.001Z`); timestamp and rate equal Binance `GET /fapi/v1/fundingRate`. The 02:00 bar is in `ohlcv` (source `binance_rest`) and equals the exchange's final kline, volume included. |
| 03:37:41 | Runner and freeze | one runner (interpreter 16420, launcher 27980); heartbeat 03:32:59; not halted; nothing in flight; book flat. Guard OK; tree clean. No runner restart. |
| 03:37:41 | Power | **still on battery** (PowerLineStatus Offline, 63%, about 1%/min since 03:01). Blocker open. |

**Funding ingestion is manual.** No scheduled task writes `grid_funding_settlements`. The app writes `funding_rates` (quotes) only. The runner reads funding live from Binance, so collection does not depend on the database. The OOS read and reconciliation do, so the runbook's 00:00, 08:00 and 16:00 backfills must be run by hand until a scheduled task is approved.
