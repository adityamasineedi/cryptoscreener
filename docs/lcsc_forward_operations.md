# LCSC ETH forward runner: operations, alerts, backup and recovery

**Contract:** [lcsc_eth_forward_contract.md](lcsc_eth_forward_contract.md)
**Runner:** `backend/scripts/lcsc_validation/forward.py` (read-only, never sends an order)
**Records:** `backend/scripts/lcsc_validation/forward_out/` (gitignored). The four files are `runs.jsonl`, `reads.jsonl`, `exceptions.jsonl` and `events.jsonl`; each row is hash-chained to the previous one.

## Schedule

- **When:** hourly, at minute 20 (`RUN_SCHEDULE`), after each 1h bar has closed and ingestion has caught up.
- **What each run does:** rebuilds every forward record from the database candles and Binance funding, checks them, and appends a run row.
- **A missed run loses no data.** But a gap longer than **3 hours** between runs inside the window creates a `RUN_MISSED` exception, because no stale-data or revision check ran during it.

| Platform | Install |
|---|---|
| Windows | `powershell -ExecutionPolicy Bypass -File backend\scripts\lcsc_validation\schedule\register_windows_task.ps1`. This creates the task "LCSC forward run", hourly at local :20, via `run_forward.ps1`. Check with `schtasks /Query /TN "LCSC forward run" /V /FO LIST`. |
| Linux | cron line or systemd service + timer in `backend/scripts/lcsc_validation/schedule/linux_units.txt` |

The scheduler is **not registered** by this repository; installing it is an operator action.

## Alerts

The wrapper turns every non-zero exit into a line in `forward_out/ALERTS.log`, and writes the full output to `forward_out/last_failure.log`.

| Exit | Meaning | Action |
|---|---|---|
| 2 | Refused (a gate or argument) | Read `last_failure.log` |
| 3 | `CONFIG_DRIFT`: code, config, contract document or lock differs from the frozen lock | Stop. Run `scripts/contract_policy/shared_modules.py report`. Never edit the lock. A reviewed `NO_BEHAVIOR_CHANGE` may be refrozen only before the window starts; after that, a change means a new version and a new window. |
| 4 | `RECORD_INTEGRITY`: a record row was edited, removed, reordered or torn | Stop. Restore from the last verified backup (below). |
| 5 | `CONCURRENT_RUN`: another run holds `runner.lock` (less than 2 hours old) | Normally nothing. If no run is active, wait 2 hours or follow crash recovery. |
| other | Crash | Read `last_failure.log`; the next run records `RUNNER_CRASH` if the lock was left behind |

Check `ALERTS.log` and the open exceptions daily: `python scripts/lcsc_validation/forward.py status`.

## Exceptions the runner raises

Every one of these blocks acceptance until resolved with a written note:

| Exception | Cause |
|---|---|
| `DATA_GAP` | Missing 1h bar |
| `FUNDING_GAP` | Missing 8h settlement |
| `STALE_CANDLES` | Latest bar closed more than 90 minutes ago |
| `STALE_FUNDING` | No settlement for more than 9 hours |
| `DATA_REVISION` | Bars of a past forward day changed |
| `RECORD_CHANGED` | A closed forward trade changed |
| `RUN_MISSED` | More than 3 hours between runs |
| `RUNNER_CRASH` | A stale run lock was found |

Resolve with `forward.py resolve-exception --id <ID> --note "<what happened, why the record is still sound>" --by <name>`. A resolution never deletes the exception; it appends a RESOLVED row.

## Backup

- **Daily, plus before any upgrade or restore:**

  `powershell -ExecutionPolicy Bypass -File backend\scripts\lcsc_validation\schedule\backup_records.ps1 -Destination <off-disk folder>`

  The script copies the `.jsonl` files to `forward_out_<UTC timestamp>`, writes `SHA256SUMS`, and runs `forward.py verify-records --path <copy>`. Exit 0 means the copy's hash chain is intact.
- **Keep** at least the last 7 daily copies on a different disk or machine. The records are the only place forward reads are stored; the data can be rebuilt, the read history cannot.
- **Linux:** the cron line in `linux_units.txt` does the same at 00:40 UTC.

## Recovery

1. **Crash or power loss.** Do nothing special. The next run finds the stale `runner.lock`, records `RUNNER_CRASH`, and rebuilds all records from data. Resolve the exception after checking that `forward.py verify-records` passes.
2. **Two runners started.** The second exits 5. Leave the first running.
3. **`RECORD_INTEGRITY` (exit 4).** Do not "fix" lines.
   - Move `forward_out` aside, unchanged, for investigation.
   - Copy the newest backup whose `verify-records` passes into `forward_out`, and run `forward.py run`.
   - Records after the backup are rebuilt from data. Reads made after the backup are lost; re-run any that matter and say so in an exception note.
4. **Database ingestion stopped (`STALE_CANDLES`).** Restart ingestion and backfill the missing bars. The next run rebuilds the records. Resolve the exception with what was missing and when it was filled. Never fabricate a bar.
5. **Binance funding unreachable (`STALE_FUNDING` or `FUNDING_GAP`).** Retry later; the fetch is idempotent. A settlement that never appears stays an open exception until explained.
6. **Machine replaced.**
   - Check out the frozen commit; the line endings are pinned in `.gitattributes`.
   - Restore the latest verified backup into `forward_out`.
   - Run `forward.py status`. The guard must report OK before you re-register the schedule.
