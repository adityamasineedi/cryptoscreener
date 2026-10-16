# CANDLE12_V3_EXEC_FIX/OOS-2: first-24-hour runbook

**Window:** opens 2026-10-16 00:00:00 UTC. **Earliest acceptance read:** 2027-10-16 00:00:00 UTC.

**No OOS result may be read, interpreted, compared or tuned on before 2027-10-16.** That includes trade counts, PnL and win rate from `status`, `trades.jsonl` or anywhere else. These checks are about plumbing only.

All commands run from `D:\cryptoscreener\backend`. `$P` is `.venv\Scripts\python.exe -m app.research.candle12_v3_paper.cli`. `$S` is `data/candle12_v3_paper/virtual_paper_mainnet/oos2`. Service: `scripts\candle12_v3_oos2_service\control.ps1` (task `CANDLE12_V3_OOS2_Runner`). Logs: `data\candle12_v3_paper\service_logs\`.

**Do not edit any of the 37 fingerprinted files** (the list is in the lock). The runner halts within 5 minutes of a fingerprint drift.

## Alert locations: both must be checked

Alerts are **local files only**. Nothing sends an e-mail, push or other external notification. A person has to read them.

| File | Written by | Contents |
|---|---|---|
| `data\candle12_v3_paper\virtual_paper_mainnet\oos2\alerts.jsonl` | the runner (frozen code) and the supervisor's restart-storm record | missed candle, missing funding, order rejection, exit failure, unexpected position, recovery action, fingerprint drift, process restart, halt, kill switch |
| `data\candle12_v3_paper\service_logs\ops_alerts.jsonl` | `scheduled_backfill.py` | backfill failures after all retries: MISSING_FUNDING, MISSED_CANDLE, BACKFILL_FAILED |

The backfill never writes to the runner's store. The store has no cross-process lock, and a torn line would halt the frozen runner. Check both files at every hourly check and at the daily check. Also read `service_logs\supervisor.log` and `service_logs\backfill_runs.jsonl`.

## 1. At 00:00 UTC (window opens). Check at 00:02, again at 01:02

| Check | Command | Expected | If not |
|---|---|---|---|
| Service up | `control.ps1 status` | task Ready or Running; one supervisor (pythonw) and one runner (python launcher plus child); fresh heartbeat | see section 6 |
| Store eligible | `$P status --store $S` | `mode virtual_paper_mainnet`, `oos_eligibility.eligible_store true`, `fingerprint_status OK`, `open_exceptions []` | HALT (section 7) |
| 23:00 bar processed (at 00:02) | same, `last_processed_candle` | `2026-10-15T23:00:00+00:00` | section 6 |
| First window bar processed (at 01:02) | same | `2026-10-16T00:00:00+00:00` | section 6 |
| 00:00 settlement recorded | same, `last_funding_settlement` | `2026-10-16T00:00:00+00:00` once the feed has it (within minutes) | run section 3 |

## 2. Every hour at about :02

| Check | Command | Expected |
|---|---|---|
| Bar advanced | `$P status --store $S` → `last_processed_candle` | the bar that opened one hour before the last full hour (at 05:02 → 04:00) |
| Heartbeat | `last_heartbeat.at` | < 6 min old; `halted null`, `inflight []` |
| No new alerts | `Get-Content $S\alerts.jsonl -Tail 5` (the file may not exist) | nothing new since the last check |
| No gaps | `Select-String '"DATA_GAP"\|"DATA_STALE"\|"MISSING_FUNDING_DATA"' $S\events.jsonl` | no hits |
| State written | `(Get-Item $S\state.json).LastWriteTimeUtc` | within the last hour |
| Venue = book | `status` → `venue_position` vs `open_position` qty | equal (0 when flat) |
| No duplicate trades | `$P status` plus `trades.jsonl` trade_id uniqueness (`Get-Content $S\trades.jsonl \| ConvertFrom-Json \| Group-Object trade_id \| ? Count -gt 1`) | no output |

Look only at plumbing fields. Do not look at PnL.

## 3. Funding at 00:00, 08:00 and 16:00 UTC (check at about :20)

**Automated since 2026-10-16:** task `CANDLE12_V3_OOS2_FundingBackfill` runs the command below at 00:20Z, 08:20Z and 16:20Z. Check `data\candle12_v3_paper\service_logs\backfill_runs.jsonl` for the newest row with `healthy: true`, and confirm `ops_alerts.jsonl` has no new rows. The task is Interactive: it runs only while the owner is logged on, and a missed start runs at the next logon. Run the command by hand if the newest healthy row is older than the last settlement plus 30 minutes.

```
.venv\Scripts\python.exe -m app.research.candle12_v3_paper.cli backfill-data --symbols BTCUSDT --start 2026-09-04T08:00:00+00:00 --end now
```

Expected: `"ok": true`; `funding status OK`; `gaps []`; `conflicts []`; `last_settlement` equals the settlement just passed. `candles_1h` is `OK` with no gaps or conflicts.

- `backfill-data` exits 0 even when it reports gaps (deferred item D1). **Read the JSON. Never rely on the exit code.**
- **Conflicts:** do not overwrite. Compare against `GET /fapi/v1/klines`. Correct a stored row only when it is a partial candle, and record the correction in `docs/candle12_v3_oos2_launch_log.md` (see the 2026-10-01 04:00 example).
- **A real gap the exchange cannot fill:** it stays a gap. Record it, and expect `MISSING_FUNDING_DATA` or `DATA_GAP` exceptions.

## 4. Restart checks (after any restart, planned or not)

1. `control.ps1 status` shows one supervisor and one runner.
2. `Select-String '"ADAPTER_RESUMED"\|"RECOVERY_COMPLETE"\|"RECOVERY_HALTED"' $S\events.jsonl | Select -Last 3` shows `ADAPTER_RESUMED` and `RECOVERY_COMPLETE` after the restart, and no `RECOVERY_HALTED`.
3. `alerts.jsonl` has a `PROCESS_RESTART` row. After a hard kill, `exceptions.jsonl` has a `RUNNER_CRASH:<started_at>` row. The runner only takes over a lock 15 minutes after its last refresh.
4. The next bar close is processed (section 2).
5. Resolve `RUNNER_CRASH` only after checks 1–4, with a dated note:
   `$P resolve-exception --store $S --id "RUNNER_CRASH:<started_at>" --by <name> --note "<UTC time>: <cause>; resumed at <time>, RECOVERY_COMPLETE, bar <t> processed, no inflight, position = book"`

## 5. Daily health check (about 00:10 UTC)

- **Service:** `control.ps1 status`. Check the task's last result and the supervisor log tail (`data\candle12_v3_paper\service_logs\supervisor.log`). There should be no `SERVICE_RESTART_STORM`.
- **Freeze:** `.venv\Scripts\python.exe -c "from app.research.candle12_v3_paper import guard; print(guard.fingerprint_status())"` shows `OK [] []`. `git status --porcelain -- <37 files>` is empty.
- **Data:** section 3 backfill, `ok: true`.
- **Store:** `open_exceptions` is empty, or each one has a written plan. Free disk is at least 1 GB. `KILL` file is absent.
- **Preflight:** `$P preflight --mode virtual_paper_mainnet --symbols BTCUSDT --store $S --recover` gives `PREFLIGHT_PASS`. Warnings are allowed only for kill switch and open exceptions.

## 6. Failure actions

| Symptom | Action |
|---|---|
| No runner process | `control.ps1 status`. If the supervisor is alive it will restart the runner (60–600 s backoff). If the task is disabled: `control.ps1 start`. Then run section 4. |
| Bar not advanced, process alive | Check `runner_*.err.log`. If `DATA_STALE` or the exchange is down, wait one more bar. If still stuck, `control.ps1 restart` (15-min lock takeover), then section 4. |
| `MISSED_CANDLE`, `DATA_GAP`, `MISSING_FUNDING` | Run section 3 backfill. The exception stays open until explained with `resolve-exception`. Never fill data by hand. |
| `PREFLIGHT_FAIL` in `runner_*.out.log` | Read the failure list. `fingerprint_match` means a file changed: **stop** (section 7). Data checks: run backfill. |
| Clock skew > 1 s | `w32tm /resync`, then restart. |
| Disk < 1 GB | Free space outside the repo. Never delete store files. |

## 7. Escalation: HALT and do not restart without review

These mean stop and review. The supervisor itself stops restarting after a recovery HALT (exit code 3) or while the store's halt exception is open.
- `FINGERPRINT_DRIFT`, `CODE_DRIFT`, `CONFIG_DRIFT`, or `git status` showing a change in a fingerprinted file. Restore the committed bytes, or accept that OOS-2 is void. Never edit to "fix" a result.
- `UNEXPECTED_POSITION`, `UNRECONCILED_EXTERNAL_POSITION` / `_ORDER`, `ORDER_STATE_UNKNOWN`, `EXIT_ORDER_FAILURE`, `BAR_PROCESSING_INCOMPLETE`, `TORN_RECORD`, `CONCURRENT_RUN` with two live runners.
- `SERVICE_RESTART_STORM` (4 or more starts in an hour).
- Any request to read, compare or tune on window results before 2027-10-16: refuse.

To resume after a halt:
1. Find the cause and write it up.
2. Run `resolve-exception` with a note.
3. Run `control.ps1 start`. The supervisor sees the resolved halt, and recovery runs before any bar.

## 8. Pause vs halt

- **Pause** (`$P kill-switch engage --reason "<why>"`): blocks new entries only; exits keep working. Each signal missed while paused is a permanent open exception that must be explained. Release with `kill-switch release`, and record the release time by hand (release is not logged; deferred item D5).
- **Halt:** section 7. Stop the service with `control.ps1 stop`. Restart only after review.
