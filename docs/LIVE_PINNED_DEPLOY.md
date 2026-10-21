# Live from a pinned commit, without --reload (P12; owner rulings D46, D128a)

Proposal on branch `live/p12-no-reload-pinned`. It adds scripts and docs only. No app code changes. Nothing is installed, scheduled or started until the owner runs the steps below (D53).

## Why

- Today live runs `uvicorn app.main:app --reload` in `D:\cryptoscreener`. That working tree has uncommitted changes, so the running code is not any commit. Every save there (Cursor edits, merges) restarts the worker. A reload can take about 2.5 min to shut down, and reloads and duplicate copies caused writer-lock hand-overs (D35).
- After the 10-20 crash nothing restarted the backend for 9 h 27 min (D39).

## What it does

| Script (`scripts\live\`) | Does |
|---|---|
| `start_live.ps1 [-Commit <sha or tag>]` | Starts one backend, **no `--reload`**, from the detached deploy worktree `D:\cryptoscreener-deploy`. The first start creates that worktree at `-Commit`. It refuses when anything listens on :8000 or any `app.main:app` process exists, when the deploy worktree has tracked changes or is on a branch, or when `data\` there is a real folder. |
| `stop_live.ps1` | Sends CTRL+BREAK to each backend tree (uvicorn runs its normal shutdown: paper rows flushed, writer lock released). After 180 s it force-stops what is left. Writes `data\live_stopped_by_owner`, so auto-start leaves live down. |
| `deploy_live.ps1 -Commit <sha or tag>` | Checks the commit is a fast-forward of the running one, then stop, `git checkout --detach`, start, and waits for the health check. On failure it prints the rollback command (`-AutoRollback` runs it). |
| `rollback_live.ps1 [-To <sha>]` | Goes back to the commit before the last deploy (from `data\live_runs.jsonl`), or to `-To`. |
| `health_check.ps1 [-Json] [-WaitSeconds N]` | Read-only checks: `/api/health` ok, database ok, `writer_lock_held`, `ledger_ready`, exactly one listener and one backend tree, no `--reload`, running from the deploy worktree at the recorded commit, decision journal fresh (WARN only). Exit 0/1. |
| `register_autostart.ps1 [-AtStartup] [-Unregister] [-DryRun]` | Registers the task "LIVE backend autostart (P12)": at logon and every 5 min it runs `start_live.ps1 -Auto`. `live_autostart_task.xml` is the same task as XML. |
| `send_ctrl_break.ps1` | Helper for `stop_live.ps1`. |

How the pinned copy keeps live state where it is:
- **State.** `D:\cryptoscreener-deploy\data` and `D:\cryptoscreener-deploy\backend\data` are NTFS junctions to `D:\cryptoscreener\data` and `D:\cryptoscreener\backend\data`. The journal, watermark, stop counters, halt journal, CANDLE12_V2 and book-16 stores keep their paths. The code finds them through `ROOT / "data"` and `parents[2] / "data"`. Both folders are untracked in git, so a checkout never touches them.
- **Settings.** uvicorn gets `--env-file D:\cryptoscreener\.env`. That is the same file and the same "do not override existing variables" rule as today's `load_dotenv(ROOT / ".env")`. The scripts check that the file exists and never read it.
- **Python.** `D:\cryptoscreener\backend\.venv\Scripts\python.exe -m uvicorn ... --app-dir D:\cryptoscreener-deploy\backend`. `app` is imported from the deploy worktree (the venv has no editable install of it).
- **Process.** Started through `Win32_Process.Create`, with a hidden console and logs in `%LOCALAPPDATA%\cryptoscreener\logs\backend-<ts>.out.log / .err.log`. It is not tied to the terminal or the task that started it.
- **Record.** Every start, stop, deploy and rollback is appended to `D:\cryptoscreener\data\live_runs.jsonl`.

Auto-start safety:
- `-Auto` does nothing if live is up or `data\live_stopped_by_owner` exists.
- After 3 auto-starts in 60 min it writes `data\live_autostart_paused` and stops trying (crash-loop guard). Delete the file after fixing the cause.

Not changed:
- The CANDLE12 V3 OOS-2 runner still runs from `D:\cryptoscreener\backend` (its own task, frozen files untouched).
- FWD-1, FWD-3, the backups and the frontend (`npm run dev` in `D:\cryptoscreener\frontend`; display only) are unchanged.
- The paper engine code is unchanged. The only runtime differences are no reloader process and the code location.

## Switch-over (owner, once; D53)

1. **Save the uncommitted work in the live checkout.** `git -C D:\cryptoscreener status` lists the grid-trading files. Commit them on their own branch or stash them. A pinned commit does **not** include uncommitted edits. Today's live runs them (grid routes are off unless `GRID_TRADING_ENABLED`). Decide whether the pinned commit needs them.
2. **Pick the commit and tag it:** `git -C D:\cryptoscreener tag live-2026-10-22 <sha>` (the merged live HEAD that contains this branch).
3. **Dry run:** `powershell -ExecutionPolicy Bypass -File D:\cryptoscreener\scripts\live\start_live.ps1 -Commit live-2026-10-22 -DryRun`. It should print the plan. It refuses while the old copy runs, which is expected.
4. **Stop the old copy:** Ctrl+C in its terminal. Wait for "Finished server process". `Get-NetTCPConnection -LocalPort 8000` must show nothing.
5. **Start pinned:** `powershell -ExecutionPolicy Bypass -File D:\cryptoscreener\scripts\live\start_live.ps1 -Commit live-2026-10-22`. It waits up to 180 s for `health_check.ps1` to pass.
6. **Auto-start:** `powershell -ExecutionPolicy Bypass -File D:\cryptoscreener-deploy\scripts\live\register_autostart.ps1`. Use `-AtStartup` to also start at boot without a logon; Windows asks for the account password.
7. **Verify (Claude, the D39 way):** one tree, lock held, `paper_hydrated_from_db` in the new `.out.log`, ledger digest unchanged, `stop_counters_start.json` unchanged, journal gap, all heartbeats OK.

## Day to day (owner)

```powershell
$L = 'D:\cryptoscreener-deploy\scripts\live'
powershell -ExecutionPolicy Bypass -File $L\health_check.ps1                         # is live OK?
powershell -ExecutionPolicy Bypass -File $L\deploy_live.ps1 -Commit <sha or tag>      # after merging a live/* branch
powershell -ExecutionPolicy Bypass -File $L\rollback_live.ps1                         # undo the last deploy
powershell -ExecutionPolicy Bypass -File $L\stop_live.ps1                             # stop (auto-start stays off)
powershell -ExecutionPolicy Bypass -File $L\start_live.ps1                            # start again (clears the stop marker)
```

- A merge into `D:\cryptoscreener` no longer changes live. Live changes only when `deploy_live.ps1` runs.
- A `.env` change needs `stop_live.ps1` then `start_live.ps1` (as today).
- Editing files in `D:\cryptoscreener-deploy` is refused at the next start or deploy (tracked changes). Do not work there.

## Removing it (back to today's way)

1. `register_autostart.ps1 -Unregister`, then `stop_live.ps1`.
2. Start the old way: `cd D:\cryptoscreener\backend; .\.venv\Scripts\uvicorn.exe app.main:app --reload --host 0.0.0.0 --port 8000`.
3. The deploy worktree can stay. `git worktree remove D:\cryptoscreener-deploy` removes it; first delete its two junctions with `cmd /c rmdir D:\cryptoscreener-deploy\data` and `cmd /c rmdir D:\cryptoscreener-deploy\backend\data`. `rmdir` on a junction removes only the link, never the live data.

## Tests

`backend/tests/test_live_scripts.py` (20 tests, Windows only):
- argument checks: bad or unknown commit, deploy dir inside the live root, port range, missing `.env` or Python, first start without `-Commit`, `-Auto` with `-Commit`;
- refusals: deploy without a worktree, a non-fast-forward, rollback without history;
- dry runs that change nothing; no `--reload` anywhere in the start path;
- a health check against a closed port;
- the auto-start task dry run;
- an end-to-end run on a **dummy** FastAPI app (no database, no Redis, `DATABASE_ENABLED=false` / `REDIS_ENABLED=false` in its env file) from a temp git repo on a free localhost port. It covers start, state written through the junction, an edit in the live checkout not reaching the running code, a second copy refused, `-Auto` no-op, health OK and pinned, deploy (fast-forward), non-fast-forward refused, rollback, a clean CTRL+BREAK stop (no forced kill), and auto-start respecting the owner's stop.

Every script call in the tests has a `-ProcessMatch` that matches only the dummy server's port, so the tests can never see or stop the live backend.
