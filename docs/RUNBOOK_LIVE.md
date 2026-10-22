# Live paper runbook (owner ruling D128e)

What to do when something breaks in live paper. Proposal on branch `live/d128e-runbook`; it changes no code.

**Ground rules (they override anything below):**
- **C1 (v2.5, D131).** No real orders, no real money. Exchange API keys exist only for Binance Futures **testnet**. The owner sets them in the root `.env`; they are never logged, printed or committed. They are used only by the D128c adapter, the kill switches and the reconciliation tests, and the code refuses any non-testnet URL. Every live position in this runbook is a paper position. The "exchange" notes apply to the testnet adapter today and to real money only after a separate amendment (C1 v3).
- **D53.** Only the **owner** starts, stops or restarts a live process: the backend, the frontend, the forward runners (FWD-1, FWD-3, CANDLE12 V3 OOS-2) and their scheduled tasks. A Claude session only diagnoses (read-only), writes the exact steps into `research/OWNER_QUEUE.md`, and verifies afterwards.
- **C2.** Nothing here changes live paper behaviour: no edits to `.env`, armed sides, stops, risk or engine code as a "fix". A real fix is a `live/*` branch with a replay test, merged by the owner.
- **Frozen files.** Never edit, move or restore by hand anything listed in `docs/candle12_v3_oos_contract.lock.json` (37 files) or the CANDLE12 V3 OOS-2 store. The OOS-2 runner re-fingerprints them every 5 minutes and halts on drift.
- **Never checkout or switch branches in `D:\cryptoscreener`** (owner D189). Owner branch work happens in a separate worktree (`git -C D:\cryptoscreener worktree add D:\cryptoscreener-worktrees\<name> <branch>`). A checkout in the live folder deletes or overwrites runtime files that a branch tracks or untracks (2026-10-22: it reset the CANDLE12_V2 virtual store, D207). Merges into live stay `--ff-only` from a worktree-built branch.
- **Secrets.** Never paste `.env` values into a chat, a log, a ticket or this repo. Commands below that need the database password prompt for it (`psql` asks).

Each step is tagged with who does it: **[Owner]** or **[Claude]** (any Claude session, read-only unless stated).

---

## 0. Quick reference

| What | Where |
|---|---|
| Live checkout | `D:\cryptoscreener` (branch `feature/grid-trading`). After P12: code runs from the pinned deploy worktree `D:\cryptoscreener-deploy`; `data\` stays in `D:\cryptoscreener`. |
| Backend start today | `cd D:\cryptoscreener\backend` then `.\.venv\Scripts\uvicorn.exe app.main:app --reload --host 0.0.0.0 --port 8000` (owner's standalone Windows Terminal) |
| Backend start after P12 | `powershell -ExecutionPolicy Bypass -File D:\cryptoscreener-deploy\scripts\live\start_live.ps1` (see `docs/LIVE_PINNED_DEPLOY.md` on `live/p12-no-reload-pinned`) |
| Frontend | `cd D:\cryptoscreener\frontend` then `npm run dev` (vite on 127.0.0.1:5173). Display only; paper does not depend on it. |
| Settings | `D:\cryptoscreener\.env` (repo root). `app/main.py` loads `ROOT\.env`, then the working directory's `.env`. A `.env` change needs a restart; `--reload` does not see it. |
| Paper state files | `D:\cryptoscreener\data\paper\`: `decision_journal.jsonl`, `halt_journal.jsonl`, `passed_paper_watermark.json`, `stop_counters_start.json` |
| Virtual stores | `D:\cryptoscreener\backend\data\candle12_v2_paper\btcusdt_long.json`, `backend\data\book16_paper\state.json`; OOS-2 store `backend\data\candle12_v3_paper\virtual_paper_mainnet\oos2\` (frozen contract, never touch) |
| Paper ledger | Postgres table `paper_trades` (+ `paper_trade_dedupe`), service `postgresql-x64-16`, data dir `D:\PostgreSQL\16\data`, tools `C:\Program Files\PostgreSQL\16\bin` |
| Redis | Windows service `Redis` (market cache only; paper does not need it to be correct) |
| Health | `http://127.0.0.1:8000/api/health` (note: `/health` is 404), `http://127.0.0.1:8000/api/paper/status`, `http://127.0.0.1:8000/api/diagnostics/overview` |
| Logs | `%LOCALAPPDATA%\cryptoscreener\logs\backend-<ts>.out.log / .err.log` when started hidden; otherwise the owner's terminal. Research watchdog: `D:\cryptoscreener-worktrees\research-audit\research\out\logs\memory_watchdog.log` |
| Ledger digest | `D:\cryptoscreener\backend\.venv\Scripts\python.exe D:\cryptoscreener-worktrees\research-audit\research\tools\ledger_digest.py` (read-only; row count, status split, SHA-256) |
| Heartbeats | `python research\tools\heartbeat_check.py` (task "RESEARCH heartbeat check (D24)", hourly) writes `research\forward\HEARTBEATS.md` |
| Backups | `research\tools\backup\daily_backup.py` `run` / `run --now` / `restore-test` (tasks "RESEARCH backup daily (D65)" 03:10 IST, "RESEARCH backup restore test weekly (D65)" Sunday 04:30 IST). Results: `research\forward\backups.jsonl`. Copies: `%USERPROFILE%\OneDrive\cryptoscreener_backups\<day>` |
| Writer lock | Postgres session advisory lock key `7302026001` (pg_locks `classid=1`, `objid=3007058705`) |

Scheduled tasks that belong to live or forward tests (all run as the owner, **Interactive**: only while logged on):
`CANDLE12_V3_OOS2_Runner` (at logon + every 5 min, from `D:\cryptoscreener\backend`), `CANDLE12_V3_OOS2_FundingBackfill`, `BOOK16 FWD-1 run` (every 4 h), `BOOK16 FWD-1 backup`, `LCSC forward run` (hourly HH:20 UTC), `LCSC daily backup`, `RESEARCH heartbeat check (D24)`, `RESEARCH backup daily (D65)`, `RESEARCH backup restore test weekly (D65)`.

---

## 1. The standard health check (use it in every section)

**[Owner or Claude]**, PowerShell, read-only:

```powershell
# 1. API up, DB and Redis seen by the app
Invoke-RestMethod http://127.0.0.1:8000/api/health | Select-Object status, database, redis, ingestion, tickers_live

# 2. This process is the paper writer and its ledger loaded
Invoke-RestMethod http://127.0.0.1:8000/api/paper/status | Select-Object enabled, writer_lock_held, ledger_ready, open_count, open_risk_usd, last_skip_reason

# 3. Exactly ONE backend copy (uvicorn binds :8000 with SO_REUSEADDR on Windows, so duplicates are silent)
Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object OwningProcess
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'app\.main:app' -or $_.CommandLine -match 'multiprocessing.spawn' } |
  Select-Object ProcessId, ParentProcessId, CreationDate, CommandLine | Format-List

# 4. Paper loops are alive: the decision journal grows (passed book writes bar_evaluated every closed 1h bar per coin)
Get-Item D:\cryptoscreener\data\paper\decision_journal.jsonl | Select-Object LastWriteTime, Length
Get-Content D:\cryptoscreener\data\paper\decision_journal.jsonl -Tail 3

# 5. Watchdog / research gate sees live healthy
Get-Content D:\cryptoscreener-worktrees\research-audit\research\out\logs\research_gate.json
```

Healthy means: `status` ok, `database` ok, `writer_lock_held` and `ledger_ready` both `True`, one listener and one `app.main:app` tree (a `--reload` tree is a reloader parent plus one worker), journal written in the last ~65 minutes.

**After any restart, verify "the D39 way"** **[Claude]**: one tree; `writer_lock_held` and `ledger_ready` true; a `paper_hydrated_from_db` line in the backend log (owner's console or `%LOCALAPPDATA%\cryptoscreener\logs`); ledger digest row count and status split unchanged except for rows the catch-up legitimately closed; `stop_counters_start.json` unchanged (sha `cc37fa1f…`); journal gap across the restart; all heartbeats OK in `HEARTBEATS.md`. Log the result as a DECISIONS row.

---

## 2. The PC goes down (crash, reboot, power cut, sleep)

What happens: everything stops at once. Live backend, Postgres, Redis, the forward runners and research share one PC and one NVMe disk (D67). Nothing restarts the backend today (D39: 9 h 27 min of downtime after the 10-20 crash). Postgres and Redis are Windows services with Automatic start, so they come back with Windows. The scheduled tasks are Interactive, so they run again only after the owner logs on.

**Steps**

1. **[Owner]** Log on. The scheduled tasks resume (OOS-2 at logon; FWD-1, FWD-3 and the heartbeat check at their next trigger, StartWhenAvailable).
2. **[Owner]** Check the services:
   ```powershell
   Get-Service postgresql-x64-16, Redis | Select-Object Name, Status
   & "C:\Program Files\PostgreSQL\16\bin\pg_isready.exe" -h localhost -p 5432
   ```
   If Postgres is not running, go to section 7 first. Do not start the backend against a down database.
3. **[Owner]** Make sure no backend copy survived (section 1, item 3). Nothing should hold :8000 after a reboot.
4. **[Owner]** Start the backend once.
   - Today: open Windows Terminal (not a Cursor terminal, D47), then
     ```powershell
     cd D:\cryptoscreener\backend
     .\.venv\Scripts\uvicorn.exe app.main:app --reload --host 0.0.0.0 --port 8000
     ```
   - After P12: the "LIVE backend autostart (P12)" task does this at logon (and every 5 minutes if :8000 is down). By hand: `powershell -ExecutionPolicy Bypass -File D:\cryptoscreener-deploy\scripts\live\start_live.ps1`.
5. **[Owner]** Optionally start the frontend: `cd D:\cryptoscreener\frontend; npm run dev`.
6. **[Claude]** Verify the D39 way (section 1). The watchers catch up by themselves: the passed book re-evaluates every closed bar since its watermark (`passed_paper_watermark.json`), oldest first; V1 journals bars it could not evaluate; open causal passed positions take their exits from the stored bars; a missing bar becomes `GAP_EXIT` after the 900 s grace. Missed signals are journaled, not invented.
7. **[Claude]** Record the downtime (last journal row before → first after) in DECISIONS and in `research\BLOCKED.md` if it was over 2 bars. Check that each forward runner's heartbeat is back (`HEARTBEATS.md`). Do not "re-run" a forward runner by hand; their own catch-up rules decide.

**Sleep.** A sleeping PC looks like a crash with no event log entry (D35 cases 3 and 5). Power plan: never sleep on AC (owner's call). Check with `powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE`.

**Disk failure.** If D: is lost, restore from the latest OneDrive backup onto a new disk or a VPS (section 8 "restore", and `docs/VPS_DEPLOYMENT_PLAN.md`). Code and research come back from the git bundles in the same backup.

### 2b. The VPS goes down (after the move in docs/VPS_DEPLOYMENT_PLAN.md)

1. **[Owner]** Provider console: is the VM running? If the provider reports a host failure, start or rebuild it from the console. systemd units (`cryptoscreener-backend.service`, Postgres, Redis) start at boot with `Restart=on-failure`.
2. **[Owner]** `ssh` in and run the health check (the plan's `health_check.sh`): `systemctl status cryptoscreener-backend postgresql redis-server`, `curl -s localhost:8000/api/health`, `curl -s localhost:8000/api/paper/status`.
3. **[Owner]** If the VM is gone: create a new one from the plan, restore the latest object-storage backup (section 8), start, verify. The PC stays the cold standby: **never** run live on the PC and the VPS at the same time (two writers on two databases; the advisory lock does not span them).
4. **[Claude]** Verify the D39 way using the read-only status the owner pastes or the monitoring page; log the outage.

---

## 3. Binance API or WebSocket fails

What the app does by itself:
- **WebSocket.** The futures feed reconnects on its own. Paper exits use the LIVE mark price (WS `!markPrice@arr@1s`), and a mark older than 30 s or not LIVE is not used (`PAPER_MARK_MAX_AGE_SECONDS`). Without a fresh mark the exit loop falls back to ticker, then the stored candle, and otherwise waits. The causal passed book exits only on closed bars, never on marks.
- **REST.** Closed bars for the paper watchers come from a REST catch-up every minute. HTTP 429 is retried with the server's `Retry-After`; 5xx is retried with backoff. While closed bars are missing, the watchers journal the bar as not evaluated (`data_integrity:*`, `history_short`) and open nothing. Nothing is filled from a guess.
- **HTTP 418** means Binance has banned the IP for exceeding limits. Restarting makes it worse: every restart re-downloads history.

**Steps**

1. **[Claude]** Diagnose, read-only:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/api/health/providers
   Invoke-RestMethod http://127.0.0.1:8000/api/diagnostics/websocket
   Invoke-RestMethod http://127.0.0.1:8000/api/diagnostics/rest
   Invoke-RestMethod http://127.0.0.1:8000/api/diagnostics/data-health
   Select-String -Path "$env:LOCALAPPDATA\cryptoscreener\logs\backend-*.out.log" -Pattern 'binance_rate_limited|418|ws_reconnect|ping_timeout' | Select-Object -Last 20
   ```
   Also check Binance's status page and whether the PC has internet (`Test-NetConnection fapi.binance.com -Port 443`).
2. **Short outage (minutes).** Do nothing. **[Claude]** After it ends, confirm the journal shows the missed bars as evaluated by the catch-up, or journaled as missed.
3. **Long outage (over 1 h) or an IP ban (418).**
   - **[Owner]** Do **not** restart the backend in a loop. One restart is reasonable only if the WS shows connected sockets with 0 frames for more than 10 minutes after Binance is back (the P15 failure mode).
   - **[Owner]** For a 418 ban: wait out the ban (the `Retry-After` in the log). If research jobs or other tools on the PC also call Binance, pause them (research downloads go through the gate; ask the Claude session to hold its queue).
   - **[Claude]** After recovery: ledger digest, journal gap per book, `HEARTBEATS.md`; list the bars journaled as missed. Open paper positions that should have exited during the outage exit on the next fresh price or closed bar; the causal passed book books the stored-bar exit. Report the difference in DECISIONS; do not correct rows by hand.
4. **Exchange later (only after a C1 amendment).** Stops and take-profits are exchange-side orders (D128c), so an outage of our feed does not remove protection. If the exchange itself is down: do not send new orders; when it returns, the reconciliation loop compares exchange positions with the ledger before any new entry, and a mismatch trips the kill switch (D128d).

---

## 4. The paper engine loses its writer lock

What it means: one backend process per database may write paper trades. It holds the Postgres advisory lock `7302026001` on a dedicated session. `writer_lock_held: false` means this process writes nothing. That happens when another copy holds the lock (a duplicate backend, an old `--reload` worker that has not exited) or when the lock connection dropped. P11 (in live since 05e95a6) keeps retrying, and the process starts its paper loops when it gets the lock.

**Steps**

1. **[Claude]** Find who holds the lock (read-only; psql prompts for the password of the DATABASE_URL user):
   ```powershell
   & "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -U <db user> -d cryptoscreener -c "SELECT a.pid, a.client_addr, a.client_port, a.backend_start, a.state FROM pg_locks l JOIN pg_stat_activity a USING (pid) WHERE l.locktype='advisory' AND l.classid=1 AND l.objid=3007058705;"
   # Map the client port to the Windows process that owns the lock:
   Get-NetTCPConnection -LocalPort <client_port> | Select-Object OwningProcess
   Get-CimInstance Win32_Process -Filter "ProcessId=<that pid>" | Select-Object ProcessId, ParentProcessId, CreationDate, CommandLine
   ```
   Then list every backend copy (section 1, item 3).
2. **Case A: a duplicate copy holds it.** Typically a Cursor-terminal `--reload` tree or an orphaned `multiprocessing.spawn` child.
   - **[Claude]** Write the exact PIDs into OWNER_QUEUE: which one to keep (the owner's standalone copy) and which to stop.
   - **[Owner]** Stop the duplicate tree: Ctrl+C in its terminal, or `Stop-Process -Id <reloader pid>,<worker pid>`. The survivor gets the lock within its retry interval and starts its loops (P11).
3. **Case B: nobody holds it, but `writer_lock_held` stays false.** The retry loop may have died.
   - **[Owner]** Restart the one backend copy (section 2, step 4).
4. **Case C: an old worker of a `--reload` restart is still shutting down.** That can take about 2.5 minutes. Wait for "Finished server process" before doing anything.
5. **[Claude]** Verify: one tree, lock held by it, `ledger_ready` true, journal rows resume, `paper_loops_running` in the log. Record the gap with no paper writes. Positions opened by nobody during the gap are journaled as missed by the catch-up.

Prevention: P12 (no `--reload`, pinned deploy, single start script that refuses a second copy).

---

## 5. A position is stuck

"Stuck" means one of these: an OPEN position that should have exited (price went through the stop or target) but is still open; a position the UI shows but the ledger does not (or the reverse); or a position that cannot exit because its symbol has no fresh price or bars.

### 5a. Paper (today)

1. **[Claude]** Collect the facts, read-only:
   ```powershell
   (Invoke-RestMethod http://127.0.0.1:8000/api/paper/positions).open | Select-Object id, symbol, side, status, entry_price, stop_price, tp1_price, mark_price, opened_at, timeframe
   ```
   Compare with the ledger: `SELECT id, symbol, status, opened_at FROM paper_trades WHERE status='OPEN' ORDER BY opened_at;` (psql, read-only). Check the price source: `/api/diagnostics/data-health/<dataset>` and whether that symbol's mark is LIVE. Check the decision journal for the symbol.
2. **Cause: no fresh price** (delisted or paused coin, dead flat bars after a delisting). The engine refuses to exit on a stale price, by design.
   - Causal passed positions: once the bar after a missing bar has closed for 900 s, the next catch-up books `GAP_EXIT` by itself.
   - Other books: they wait for a fresh price. **[Owner]** decides whether to close by hand. Today the only manual close path is `POST /api/paper/close-legacy?confirm=true`, and it touches **legacy/research rows only**. It never closes V1 or passed-book positions. After D128d merges and its flag is on, `backend\.venv\Scripts\python.exe backend\scripts\ops\flatten_all.py flatten` (or `POST /api/paper/kill-switch/flatten-all?confirm=FLATTEN%20ALL`; see `docs/KILL_SWITCHES.md` on `live/d128d-kill-switches`) closes every open paper position at the current price, booked in the ledger with `exit_reason=KILL_SWITCH_FLATTEN`. That is an owner decision, logged in DECISIONS, because it changes the forward record.
3. **Cause: the in-memory book and the ledger disagree.**
   - Ledger has OPEN, API does not: the process hydrated before the row was written, or the row is a duplicate refused by `paper_trade_dedupe`. **[Owner]** One restart re-hydrates from the ledger (section 2, step 4) after **[Claude]** has checked that the ledger row is the true one.
   - API has it, ledger does not: a write failed while the database was down. Look for `persist_paper_trade_failed` in the log. **Do not restart** while that is true: the open lives only in memory, and its later close re-writes the full row (upsert on `id`). See section 7.
4. **Never** edit `paper_trades` rows by hand, delete journal lines or move the watermark (C6: no deletion; C2: no behaviour change). If a row is wrong, **[Claude]** writes a proposal with the replay that shows the right row, and the owner decides.

### 5b. Exchange (later, only after a C1 amendment)

1. Every exchange position always has its reduce-only `STOP_MARKET` (and `TAKE_PROFIT_MARKET`) on the exchange, placed by the adapter right after the fill (D128c). If the stop order is missing, the reconciliation loop flags it within 60 s and the kill switch flattens.
2. **[Owner]** If the app cannot close it: close it in the exchange UI with a reduce-only market order. Then cancel that symbol's open stop and TP orders.
3. **[Owner]** Press "flatten all" or set the kill switch, so the app does not re-enter while the ledger and the exchange disagree.
4. **[Claude]** Reconcile: exchange fills (owner exports them) against the ledger. The manual close is booked as `MANUAL_EXCHANGE_CLOSE` through a reviewed proposal, never by editing rows.

---

## 6. The disk is full

Context: C: and D: are one physical NVMe. D: had about 40 GB free on 2026-10-21. The biggest growers are Postgres (`funding_rates` grew about 2.1 GB/day before P16a; P16a now writes one row per coin per minute), the backend logs (a hidden backend wrote 117 MB in 2 hours on 10-18), research caches and outputs, and worktrees.

**Steps**

1. **[Claude]** Measure, read-only:
   ```powershell
   Get-PSDrive C, D | Select-Object Name, @{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}}, @{n='UsedGB';e={[math]::Round($_.Used/1GB,1)}}
   Invoke-RestMethod http://127.0.0.1:8000/api/diagnostics/resources
   Get-ChildItem "$env:LOCALAPPDATA\cryptoscreener\logs" | Sort-Object Length -Descending | Select-Object -First 10 Name, @{n='MB';e={[math]::Round($_.Length/1MB)}}, LastWriteTime
   ```
   Postgres table sizes (psql, read-only): `SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) FROM pg_catalog.pg_statio_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 10;`
2. **If Postgres has stopped because the disk is full** (it does when it cannot write WAL): section 7, after space is freed.
3. **Free space without deleting data (C6):**
   - **[Owner]** Move old backend logs (`backend-*.log` older than the current run) from `%LOCALAPPDATA%\cryptoscreener\logs` to an external drive or compress them. Never touch the log of the running process.
   - **[Owner]** Empty the Windows temp folders and the recycle bin. Check `C:\cryptoscreener-backups\tmp` for a leftover half-written backup (only when no backup is running).
   - **[Owner]** Move finished research outputs or old worktrees to an external drive. C6 means archive, never delete. A Claude session can list candidates with sizes, but the owner moves them.
   - **Funding ticks** (`funding_rates`): the P16b archive/downsample proposal is a data deletion and needs the owner's explicit ruling.
4. **[Owner]** Restart nothing until there are at least 10 GB free. Then follow section 7 if Postgres was hit, otherwise the backend recovers by itself (its writes resume).
5. **[Claude]** Verify the ledger digest, check that the backup ran (section 8), and record the event.

---

## 7. The database is down

What happens: the backend keeps running. `/api/health` still answers `status: ok` but with `database` not ok. **Paper writes fail**: every flush logs `persist_paper_trade_failed` and the row stays only in memory. Ledger loads fail, so a backend that starts while the DB is down has `ledger_ready: false` and opens nothing (it retries the load every 30 s). Closed-bar history reads fail, so the watchers evaluate nothing new.

**Steps**

1. **[Claude]** Confirm:
   ```powershell
   Get-Service postgresql-x64-16
   & "C:\Program Files\PostgreSQL\16\bin\pg_isready.exe" -h localhost -p 5432
   Invoke-RestMethod http://127.0.0.1:8000/api/health | Select-Object database
   Get-Content "D:\PostgreSQL\16\data\log\$(Get-ChildItem D:\PostgreSQL\16\data\log | Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty Name)" -Tail 40
   ```
   Common causes: disk full (section 6), the service stopped after a Windows update, a crash during recovery.
2. **[Owner]** **Do not restart the backend while the database is down.** Rows written while the database was down exist only in this process's memory. A restart loses them, and a later restart hydrates from the stale ledger.
3. **[Owner]** Bring Postgres back:
   ```powershell
   Start-Service postgresql-x64-16      # elevated PowerShell
   & "C:\Program Files\PostgreSQL\16\bin\pg_isready.exe" -h localhost -p 5432
   ```
   If it refuses to start, read the newest file in `D:\PostgreSQL\16\data\log`. Disk full: free space first. A corrupt cluster: stop and follow section 8 "restore" into a **new** data directory; never run `pg_resetwal` on the live cluster without a copy.
4. **[Claude]** Once the DB is up, check:
   - `persist_paper_trade_failed` lines in the log during the outage: which ids. An OPEN that failed is re-written in full when the position closes (upsert on `id`). A CLOSE that failed stays memory-only until that row is written again. **Known gap, proposal:** re-queue failed persists instead of dropping them (filed with this runbook's report).
   - `ledger_ready` true, `writer_lock_held` true (the lock session may have been dropped with the server; P11's retry takes it again; if not, section 4 case B).
   - Ledger digest against the in-memory `/api/paper/positions`. Any id in memory but not in the ledger goes into OWNER_QUEUE with the proposed handling.
5. **[Owner]** Only if step 4 shows the lock was not re-acquired or the loops did not resume: one backend restart (section 2, step 4), **after** the memory-only rows have been recorded.

Redis down: market cache only. The app runs without it (REDIS_ENABLED can be false), with a slower start. **[Owner]** `Start-Service Redis`; no backend restart needed.

---

## 8. A backup fails

The backup (`research\tools\backup\daily_backup.py run`, task "RESEARCH backup daily (D65)", 03:10 IST) waits for the memory gate and retries every 15 minutes until 06:00 IST, then writes FAILED to `research\forward\backups.jsonl`. Two FAILED days in a row add an URGENT item to OWNER_QUEUE by themselves. The restore test (`restore-test`, Sunday 04:30 IST, and after any schema change) restores into the isolated cluster on 127.0.0.1:55433 and refuses the live server.

**Steps**

1. **[Claude]** Read the last results:
   ```powershell
   Get-Content D:\cryptoscreener-worktrees\research-audit\research\forward\backups.jsonl -Tail 5
   Get-ScheduledTask -TaskName "RESEARCH backup daily (D65)" | Get-ScheduledTaskInfo | Select-Object LastRunTime, LastTaskResult, NextRunTime
   ```
   Typical reasons: `gate closed: free RAM ...` (the PC was short of memory all night), `live not healthy for 10 min`, `pg_dump` error (DB down: section 7), OneDrive not syncing or full, disk full (section 6), manifest verify failed.
2. **[Owner]** Fix the cause, then run one attempt by hand (no waiting for the gate deadline):
   ```powershell
   cd D:\cryptoscreener-worktrees\research-audit\research\tools\backup
   D:\cryptoscreener\backend\.venv\Scripts\python.exe -B daily_backup.py run --now
   ```
   It prints one JSON line; `"status": "OK"` with all 7 tables means done. It still refuses when the gate is closed (free RAM under 3.5 GB): close a browser or Cursor window and retry.
3. **[Owner]** Check that OneDrive has synced the new `%USERPROFILE%\OneDrive\cryptoscreener_backups\<day>` folder (green tick). A backup that never leaves the PC does not protect against a disk failure.
4. **[Owner]** Then prove it restores:
   ```powershell
   D:\cryptoscreener\backend\.venv\Scripts\python.exe -B daily_backup.py restore-test --reason manual
   ```
   PASS means every file matched its SHA-256 and every table's row count and ordered-row MD5 matched the manifest. **As of 2026-10-21 the restore test has never run** (D133); run it once by hand.
5. **[Claude]** Confirm the new rows in `backups.jsonl` and in `HEARTBEATS.md` ("Backups"), and close the OWNER_QUEUE item.

**Restoring for real** (new disk, new PC or VPS). **[Owner]** does each step:
1. Copy the newest verified backup folder from OneDrive.
2. Check the manifest: compare every file's SHA-256 with `MANIFEST.json`. `restore-test` does this, or `Get-FileHash -Algorithm SHA256`.
3. Create an empty database and `pg_restore --no-owner --no-privileges -d cryptoscreener <folder>\db\ledger.dump`. The market tables (`ohlcv`, `funding_rates`, ...) are not in the backup; the app refills them from Binance.
4. Copy the state folders back to the same relative paths: `live_state` → `data\paper`, `candle12_v2_paper`, `fwd1_book16`, `fwd3_lcsc`. The OOS-2 store is restored only as an archive copy. Its frozen contract decides whether OOS-2 can continue (a restored store is a drift event for that runner).
5. Restore the code from the git bundles: `git clone <bundle>`, then check out the pinned live commit.
6. Recreate `.env` from the owner's password manager. The manifest lists the variable **names**; values are never in a backup.
7. Start once (section 2, step 4) and verify the D39 way. The restored ledger's digest must equal the manifest's.

---

## 9. Escalation and records

- Every incident: **[Claude]** appends a DECISIONS row (what, when in UTC, gap length, what the owner did, verification) and, for a silent stop over 2 bars, a `research\BLOCKED.md` entry.
- Anything that would change live behaviour goes to `research\PROPOSED_LIVE_CHANGES.md`. Nothing is hot-fixed in `D:\cryptoscreener`.
- A breach of C1–C8 during an incident (for example a wrong process killed that ran a frozen runner): stop, write `research\INCIDENT_<n>.md`, mark affected results INVALID (C9).
