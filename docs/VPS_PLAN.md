# VPS plan for live paper (owner ruling D128b)

Plan only. This file does not create a VM, account, DNS record, firewall rule, secret, or process. Nothing has been bought, logged into, or moved. The owner makes every account, VM, DNS, and secret decision and runs every step marked **[Owner]** (D53). No session starts, stops, or restarts the live backend, the frontend, the alert sidecar, or a forward runner as part of this plan.

`docs/VPS_DEPLOYMENT_PLAN.md` (rebased with the runbook) is the same proposal. This file is the D128b name on the top of the rebased stack, and it records the merge constraint below. The steps are unchanged.

**Where this sits.** Top of the cursor stack, `cursor/d128-exec-adapter`, which fast-forwards from `cursor/d128-kill-switches`, then `cursor/d128-p12`, then `cursor/d128-runbook`. That stack was rebased onto live `8b730cd`. Live HEAD does not yet contain tasks C4–C7. Rebase this stack again after those merges, then fast-forward. A fast-forward of these branches onto today's live HEAD would land D128 before C4–C7.

**Code already on this stack, still off.** `KILL_SWITCHES_ENABLED` defaults to false, so the paper engine does not attach a kill switch. `EXECUTION_TESTNET_ENABLED` defaults to false. The testnet client refuses to construct unless that flag is on, the constitution is v2.5 or later, and the base URL host is `testnet.binancefuture.com` or `demo-fapi.binance.com`. Any other host is refused. Keys stay in the root `.env`, set by the owner, and are never logged, printed, or committed. There are no real-money orders.

**Why.** Live paper, its database, the forward runners, and all research share one home PC and one NVMe disk (D67). A crash or a sleep stops everything: 9 h 27 min of downtime after the 10-20 crash (D39), and two PC-sleep gaps (D35). An always-on VPS removes the PC as the single point of failure for live paper. Research stays on the PC.

**Constitution.** C1 v2.5: no real orders, no real money. Testnet keys only, set by the owner in the live root `.env`, used only by the D128c adapter, the kill switches, and the reconciliation tests. C2: the move must not change live paper behaviour. The parity check in section 6 proves it before the cut-over.

---

## 1. What has to run, and how big

| Component | Measured on the PC | Plan on the VPS |
|---|---|---|
| Backend (uvicorn, one process) | RSS about 0.7–0.9 GB. CPU about 90% of one core (D73: 90.4% after P16a; D99 profile: the scalping watcher was about 12%, now off with P22b). Event-loop lag p95 about 0.8 s. | 1.5 GB budget (systemd `MemoryMax=2G`), 1 dedicated core |
| Postgres 16 + TimescaleDB | 53 GB on the PC, about half of it raw funding ticks (P16; now 1 row per coin per minute after P16a). The ledger and state tables are small (backup 87.5 MB). | Start from the ledger and state tables plus a fresh market-data backfill. `shared_buffers` 1 GB, ~1.5–2 GB RAM in all. 40–80 GB of disk for a year of OHLCV and setup tables with retention on, plus WAL |
| Redis | market cache only | 256 MB `maxmemory`, `allkeys-lru` |
| Frontend | display only | built once (`npm run build`), served by nginx on the private interface only |
| OS, logs, backups staging | | 1 GB RAM, 10 GB disk |

**Recommended size:** 4 vCPU (2+ of them dedicated or CPU-optimised), 8 GB RAM, 160 GB NVMe, in a region where Binance Futures serves the API (not the US: Binance returns HTTP 451 to US IPs). Tokyo, Singapore, and Frankfurt are good choices. Binance's matching engine is in AWS Tokyo, and latency only matters later for the testnet adapter.

**Minimum that works:** 2 vCPU / 4 GB / 80 GB. That is tight: one core is already ~90% busy, and Postgres writes and the catch-up bursts compete with the event loop. Use it only to save money during the parity week.

## 2. Providers and monthly cost (list prices from memory, all "verify" before buying)

| Provider | Plan (≈ 4 vCPU / 8 GB unless noted) | Approx. monthly | Notes |
|---|---|---|---|
| Hetzner Cloud | CPX31 (4 shared AMD vCPU, 8 GB, 160 GB); CCX13 (2 dedicated vCPU, 8 GB, 80 GB) | ~€15–20; ~€15–25 (verify) | Cheapest. Locations: DE, FI, US, SG (use DE/FI/SG). Storage Box for backups. |
| Vultr | Regular/High-frequency 4 vCPU / 8 GB | ~$40–48 (verify) | Tokyo and Singapore regions |
| DigitalOcean | Basic 4 vCPU / 8 GB; CPU-optimised 2 vCPU / 4 GB | ~$48; ~$42 (verify) | Singapore, Frankfurt |
| AWS Lightsail | 8 GB bundle (2 vCPU, 160 GB) | ~$44 (verify) | Tokyo (ap-northeast-1) is close to Binance |
| Akamai / Linode | Shared 8 GB (4 vCPU) | ~$48 (verify) | Tokyo, Singapore |
| OVHcloud | VPS 4 vCPU / 8 GB | ~€10–15 (verify) | Singapore, Frankfurt |

| Extra | Approx. monthly |
|---|---|
| Object storage for backups (Backblaze B2 ~$6/TB, Cloudflare R2 10 GB free, Hetzner Storage Box 1 TB ~€4, or S3) | $0–5 (verify) |
| Provider snapshots / backups add-on (often 20% of the VM price) | $3–10 (verify) |
| Dead-man's-switch monitor (Healthchecks.io free tier) and private network (Tailscale free tier) | $0 |

**Total: about $20–60 a month**, depending on provider. Recommended: Hetzner CPX31 or CCX13 in Falkenstein, Helsinki, or Singapore, with B2 or a Storage Box. That is roughly €20–30 a month, verify.

## 3. Setup steps

All steps **[Owner]**. Commands assume Ubuntu 24.04 LTS. A later session can prepare files on a branch. It does not log in to the VPS and it does not see the secrets.

### 3.1 OS and access
1. Create the VM (Ubuntu 24.04, SSH key only, no password login), then enable the provider firewall and backups.
2. `adduser screener`; disable root SSH login (`PermitRootLogin no`, `PasswordAuthentication no` in `/etc/ssh/sshd_config`); `apt update && apt full-upgrade`; `apt install unattended-upgrades fail2ban`.
3. Private access with Tailscale (`curl -fsSL https://tailscale.com/install.sh | sh; tailscale up`), so the API and UI are never on the public internet.
4. Firewall (`ufw`): `ufw default deny incoming; ufw allow in on tailscale0; ufw allow 22/tcp` (or SSH only over Tailscale). Never open 8000, 5432, 6379, or 80 publicly.
5. Time: `timedatectl set-ntp true` (UTC; bar boundaries depend on it).

### 3.2 Postgres 16 + TimescaleDB, Redis
1. Install Postgres 16 and TimescaleDB from their apt repos. In `postgresql.conf` set `shared_preload_libraries = 'timescaledb'`, `listen_addresses = 'localhost'`, `shared_buffers = 1GB`. Then `systemctl enable --now postgresql`.
2. Create the role and database (the password goes into the env file only): `CREATE ROLE screener LOGIN PASSWORD '…'; CREATE DATABASE cryptoscreener OWNER screener;`. The app's `init.sql` creates the extension and the hypertables at first start.
3. Create a separate read-only role for backups and reconciliation: `CREATE ROLE backup_ro LOGIN PASSWORD '…'; GRANT pg_read_all_data TO backup_ro;`.
4. Redis: `apt install redis-server`. In `redis.conf` set `bind 127.0.0.1`, `maxmemory 256mb`, `maxmemory-policy allkeys-lru`. Then `systemctl enable --now redis-server`.

### 3.3 Code, pinned (the P12 model on Linux)
```bash
sudo mkdir -p /opt/cryptoscreener /var/lib/cryptoscreener && sudo chown screener: /opt/cryptoscreener /var/lib/cryptoscreener
# as screener: the repo comes from a git bundle in the latest backup (C7: no push); later updates by bundle too
git clone /path/to/cryptoscreener.bundle /opt/cryptoscreener/repo
git -C /opt/cryptoscreener/repo worktree add --detach /opt/cryptoscreener/deploy <pinned sha or tag>
mkdir -p /var/lib/cryptoscreener/data /var/lib/cryptoscreener/backend-data
ln -s /var/lib/cryptoscreener/data         /opt/cryptoscreener/deploy/data
ln -s /var/lib/cryptoscreener/backend-data /opt/cryptoscreener/deploy/backend/data
python3.12 -m venv /opt/cryptoscreener/venv
/opt/cryptoscreener/venv/bin/pip install -r /opt/cryptoscreener/deploy/backend/requirements.txt
```
- The venv must match the PC's package versions. Freeze them on the PC first (`D:\cryptoscreener\backend\.venv\Scripts\python.exe -m pip freeze > requirements.lock.txt`) and install from that file. A version change is a behaviour risk under C2.
- Deploys: `git -C /opt/cryptoscreener/deploy checkout --detach <sha>`, then `systemctl restart cryptoscreener-backend`. Fast-forward only, as with `deploy_live.ps1`. Rollback: check out the previous sha and restart. A Linux `deploy.sh` mirroring P12 is a follow-up branch. On the PC, never `git checkout` inside `D:\cryptoscreener` (owner D189).

### 3.4 Secrets (owner only)
- The live root env file on the VPS, `/opt/cryptoscreener/repo/.env` (C1 v2.5: stored only in the root `.env`), is created by the owner over SSH from the password manager: `chmod 600`, owned by `screener`. It is never in git, never in a backup (backups list variable names only, as on the PC), never in a log, never pasted into a chat.
- Testnet keys (only when the owner turns on the D128c testnet client) go into the same file. Any non-testnet URL is refused by the code.
- Rotate the Postgres and Telegram secrets at the move (new VPS values; the PC keeps its own).

### 3.5 systemd units
`/etc/systemd/system/cryptoscreener-backend.service`:
```ini
[Unit]
Description=cryptoscreener live paper backend (pinned, no reload)
After=network-online.target postgresql.service redis-server.service
Wants=network-online.target
Requires=postgresql.service

[Service]
User=screener
WorkingDirectory=/opt/cryptoscreener/deploy/backend
ExecStart=/opt/cryptoscreener/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 \
          --env-file /opt/cryptoscreener/repo/.env --app-dir /opt/cryptoscreener/deploy/backend
Restart=on-failure
RestartSec=15
StartLimitIntervalSec=3600
StartLimitBurst=3
# SIGINT = uvicorn's clean shutdown (flush paper rows, release the writer lock)
KillSignal=SIGINT
TimeoutStopSec=180
MemoryMax=2G
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```
- `StartLimitBurst=3` per hour is the same crash-loop guard as P12.
- Also add `cryptoscreener-health.service` + `.timer` (every 5 min, runs `health_check.sh`, section 5) and `cryptoscreener-backup.service` + `.timer` (daily 00:40 UTC, section 4).
- `systemctl enable --now cryptoscreener-backend cryptoscreener-health.timer cryptoscreener-backup.timer`.
- Logs go to journald: `journalctl -u cryptoscreener-backend --since -1h`. Cap them with `SystemMaxUse=2G` in `journald.conf`; uvicorn access logs are large.

### 3.6 Frontend (optional)
Build on the PC (`npm run build`), copy `frontend/dist` to the VPS, and serve it with nginx bound to the Tailscale IP, with `/api` and `/ws` proxied to 127.0.0.1:8000.

## 4. Backups to object storage, and the restore test

- **Daily backup** (00:40 UTC, a port of `research/tools/backup/daily_backup.py`; that tool has Windows paths and must be ported on a branch):
  - `pg_dump -Fc` of the ledger and state tables only (`paper_trades`, `paper_trade_dedupe`, `alerts`, `grid_funding_*`, `symbols`, `asset_metadata`), as the read-only role;
  - a tar of `/var/lib/cryptoscreener/data` and `backend-data`;
  - `MANIFEST.json` with the SHA-256 of every file, row counts and ordered-row MD5 per table, the schema hash, and the env variable names.
- **Upload** with `restic` (encrypted, deduplicated) or `rclone` to B2, R2, or a Storage Box. The repository password is in the owner's password manager. Retention: 7 daily, 4 weekly, 3 monthly (D65); deletion of old snapshots only by that rule.
- **Restore test** (weekly, and after any schema change):
  - restore the latest snapshot into a scratch database on a second, local-only Postgres instance (port 55433), never the live one;
  - compare row counts and ordered-row MD5 with the manifest; verify every SHA-256;
  - write PASS/FAIL into `backups.jsonl`.
  A failed test alerts like a failed backup.
- **Off-provider copy.** The object store is a different company from the VPS provider, so one account problem cannot take both.
- **Provider snapshots** (daily) are a second layer for a fast whole-VM restore. They do not replace the tested backup.

## 5. Monitoring and alerting

| Check | How | Alert |
|---|---|---|
| Backend up and healthy | `health_check.sh` every 5 min (systemd timer): `/api/health` ok and database ok; `/api/paper/status` `writer_lock_held` and `ledger_ready`; one listener; decision journal written in the last 70 min | Telegram message to the owner's private chat (the existing bot token, from the env file; never printed), sent after 2 failures in a row |
| VPS itself down / network down | the timer also pings a Healthchecks.io check URL on success. If no ping arrives for 15 min, Healthchecks.io emails or Telegrams the owner (a dead-man's switch: it catches the case where the VPS cannot report) | external |
| Disk | `df` above 80% warns, above 90% is urgent | Telegram |
| Memory | backend RSS above 1.5 GB, or swap use | Telegram |
| Backups | no OK backup in 26 h, or a FAILED restore test | Telegram + OWNER_QUEUE |
| Binance feed | `/api/diagnostics/websocket`: connected sockets with 0 frames for 10 min; any HTTP 418 in the log | Telegram |
| Paper kill switches (D128d, once merged and the owner turns the flag on) | any trip or auto-flatten is journaled and alerted. The flag stays off until that decision | Telegram |

The PC's research heartbeat check (`heartbeat_check.py`) reads the live state files. After the move it reads them from the VPS over Tailscale (an rsync of `data/paper` every 15 min to a read-only folder on the PC), so `HEARTBEATS.md` keeps working.

## 6. Migration and cut-over, with a parity check against the PC

**Phase 0: build (1–2 days, no effect on live).** Do sections 3–5. Start the VPS backend against its own empty database with `PAPER_TRADE_ENABLED=false`, to check ingestion, backfill, and health only.

**Phase 1: shadow parity (at least 72 h; same code, same config).**
1. **[Owner]** Deploy the same pinned commit as the PC, with the same env file except the secrets (copy the variable values for every non-secret setting).
2. **[Owner]** At T0, take a backup of the PC (`daily_backup.py run --now`) and restore its ledger and state files into the VPS. Both books now start from the same state.
3. **[Owner]** Start the VPS with paper on. It is a separate database, so the VPS writes only its own shadow ledger, never the PC's. The PC stays live and authoritative.
4. **[Claude]** Compare every closed bar after T0 with a read-only parity tool (to be built on a branch; same output limits as C3e reconciliation: match/mismatch counts and reason codes):
   - decision journal rows keyed by (book, symbol, bar, event, reason): every `bar_evaluated`, `signal`, `skip`, `opened` must match;
   - ledger rows opened after T0, matched on (symbol, stream, timeframe, source_candle_ts): same entry, stop, TP1, quantity, and risk;
   - closed rows: same exit reason. Causal passed-book exits must match exactly. Mark-based exits must match the stop/TP level; the close time may differ by seconds;
   - expected and allowed differences: journal `ts` and `lag_seconds`, ids (UUIDs), and bars one side missed because of its own feed. A missed bar is a reason code, not a silent difference.
5. **Pass rule:** 0 unexplained mismatches over at least 72 h, at least one bar per live book per hour, and no VPS restart. Any unexplained mismatch stops the move. The fix is a branch, then the parity run starts again.

**Phase 2: cut-over (owner, about 30 minutes, between two 1h bars, for example HH:15–HH:45 UTC).**
1. **[Owner]** PC: `register_autostart.ps1 -Unregister`, then `stop_live.ps1` (writes the stop marker). Confirm with `health_check.ps1` that nothing runs.
2. **[Owner]** PC: final backup `daily_backup.py run --now`. Record the ledger digest (`ledger_digest.py`).
3. **[Owner]** VPS: `systemctl stop cryptoscreener-backend`. Archive the shadow database and state (rename; never delete). Restore the PC's final backup. Check that the digest equals step 2.
4. **[Owner]** VPS: `systemctl start cryptoscreener-backend`. **[Claude]** verifies the D39 way (one copy, lock held, `ledger_ready`, hydrate line, digest unchanged, `stop_counters_start.json` unchanged, journal gap).
5. **[Claude]** Log the cut-over in DECISIONS with the gap (should be under one bar). The catch-up journals any bar in the gap.

**Rollback (any time in the first week).** Stop the VPS, run a final VPS backup, restore it on the PC, `start_live.ps1`, verify. The rule is one live writer at a time. The PC and the VPS have separate databases, so the advisory lock cannot stop two writers. The stop marker on the PC and the disabled auto-start are what prevent it, so check both before any start.

**What does not move (until the owner decides otherwise).**
- FWD-1, FWD-3, and CANDLE12 V3 OOS-2 stay on the PC. They are frozen forward contracts with paths and fingerprints tied to their worktrees. Moving one is a contract change, and for OOS-2 a drift event.
- The book-16 and CANDLE12_V2 virtual stores inside the live app move with `backend/data`.

## 7. How research stays on the PC

- The research worktrees, caches, gate, watchdog, and tournament stay on the PC. Nothing in research needs the live backend except:
  - **the memory gate's "live healthy 10 min" check.** `memory_watchdog.ps1` probes `http://127.0.0.1:8000/api/health`. After the move it must probe the VPS over Tailscale, or the rule becomes "PC health" (owner decision; a research-owned edit);
  - **the C3e reconciliation tool and the heartbeat check.** They read the live ledger and state files. They read them over Tailscale with the read-only `backup_ro` role and the rsynced state folder. Same output limits.
- Research never writes to the VPS, never holds its writer lock, and never reads bars after 2026-07-14 except under C3e/C3f.
- The PC no longer needs to stay awake for live. Its sleep then only pauses research and the PC-bound forward runners.

## 8. Owner decisions needed

1. Provider and region (recommendation: Hetzner CPX31 or CCX13, FI/DE/SG; verify prices).
2. Object store (B2, R2, or a Storage Box) and who holds the restic password (owner's password manager).
3. Whether the gate's live-health rule follows live to the VPS (section 7).
4. When to start phase 1. Estimate: build 1–2 days, shadow parity at least 72 h, cut-over 30 minutes.
5. Approve the follow-up branches: the Linux deploy and health scripts, the backup port, and the journal parity tool.
6. Leave `KILL_SWITCHES_ENABLED` and `EXECUTION_TESTNET_ENABLED` off until a separate decision. This plan does not turn them on.
