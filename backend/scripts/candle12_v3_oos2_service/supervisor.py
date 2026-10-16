"""Supervisor for the CANDLE12_V3 OOS-2 virtual_paper_mainnet runner (Task Scheduler entry point).

It runs exactly one runner at a time, always with --recover:

    python -m app.research.candle12_v3_paper.cli run --mode virtual_paper_mainnet
           --store data/candle12_v3_paper/virtual_paper_mainnet/oos2 --recover

- Single instance. An OS file lock on service_logs/supervisor.lock is released when
  the process dies, so it never goes stale. A runner that is already alive (found by
  its command line) is adopted and watched, never doubled. The store's own run lock
  (runner.lock) is a second guard.
- Restarts. When the runner exits it is started again with --recover. The cli exit codes mean:
    - 2, REFUSED at startup: for example PREFLIGHT_FAIL, or CONCURRENT_RUN while a crashed
      runner's lock is still fresh (it goes stale after 15 min). Retried every 60 s.
      Nothing ran, so it does not count toward a restart storm. The preflight clock-skew
      check takes a single sample, and this network's 0.8-1.2 s round trips can make one
      sample read about -1.8 s while NTP shows about -0.03 s.
    - 3, HALTED by recovery: not restarted. The store needs an operator.
    - Anything else, or any run that lasted 60 s or more: a crash. Restarted with a delay
      that grows from 60 s to 600 s.
- Halted store. If state.json says halted and its HALTED exception is still open, the
  supervisor does not start a runner. It records why and exits. The scheduled task
  tries again every 5 minutes, and the runner starts once an operator has resolved
  the exception.
- Restart storm. Four or more crashes (not startup refusals) within 60 minutes writes one SERVICE_RESTART_STORM
  exception (an open exception, so it blocks OOS acceptance until explained) and a
  PROCESS_RESTART alert into the store. At most one is written per hour.
- Logs, all git-ignored, under backend/data/candle12_v3_paper/service_logs/:
    - runner_<UTC>.out.log and runner_<UTC>.err.log, one pair per start; the 60 newest
      are kept;
    - supervisor.log, rotating at 2 MB x 10 files.
- Priority. The supervisor raises itself, and any runner it adopts, from BelowNormal or
  Idle to the Windows Normal priority class, and never higher. Its children inherit
  Normal. A Task Scheduler task left at its default priority of 7 starts it at
  BelowNormal, and on 2026-10-16 sustained 100% CPU starved such processes.
- It never pulls code, installs packages, or writes outside the store and the
  service_logs directory. Its working directory, interpreter and environment are
  those of backend/ and backend/.venv, exactly as a manual run.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import msvcrt
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil

BACKEND = Path(__file__).resolve().parents[2]
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
STORE_REL = "data/candle12_v3_paper/virtual_paper_mainnet/oos2"
STORE = BACKEND / STORE_REL
LOGS = BACKEND / "data" / "candle12_v3_paper" / "service_logs"
CMD = [str(PYTHON), "-m", "app.research.candle12_v3_paper.cli", "run", "--mode", "virtual_paper_mainnet",
       "--store", STORE_REL, "--recover"]
MARK = "app.research.candle12_v3_paper.cli"
KEEP_RUN_LOGS = 60
STORM_STARTS, STORM_WINDOW = 4, timedelta(minutes=60)
HALTED_EXIT = 3
REFUSED_EXIT = 2
CREATE_NO_WINDOW = 0x08000000
UTC = timezone.utc


def now() -> datetime:
    return datetime.now(UTC)


def setup_log() -> logging.Logger:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("oos2_supervisor")
    log.setLevel(logging.INFO)
    h = logging.handlers.RotatingFileHandler(LOGS / "supervisor.log", maxBytes=2_000_000, backupCount=10,
                                             encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.Formatter.converter = time.gmtime
    log.addHandler(h)
    return log


LOW_CLASSES = (psutil.BELOW_NORMAL_PRIORITY_CLASS, psutil.IDLE_PRIORITY_CLASS)


def ensure_normal_priority(proc: psutil.Process, log: logging.Logger | None = None) -> None:
    """Raise a below-normal or idle process to NORMAL. Never raises above NORMAL."""
    try:
        before = proc.nice()
        if before in LOW_CLASSES:
            proc.nice(psutil.NORMAL_PRIORITY_CLASS)
            if log:
                log.info("priority pid=%s raised %s -> NORMAL", proc.pid, before)
    except (psutil.Error, OSError) as exc:
        if log:
            log.warning("priority pid=%s not changed: %s", proc.pid, exc)


def single_instance() -> object | None:
    """Hold an exclusive OS lock for the life of this process; None if another supervisor holds it."""
    fh = open(LOGS / "supervisor.lock", "a+")
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fh.close()
        return None
    return fh


def live_runners() -> list[psutil.Process]:
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        args = p.info.get("cmdline") or []
        cmd = " ".join(args).replace("\\", "/")
        if MARK in cmd and "run" in args and STORE_REL in cmd:
            out.append(p)
    return out


def halted_and_open() -> str | None:
    """The open halt exception id, if the store is halted and nobody has resolved it."""
    state_path = STORE / "state.json"
    if not state_path.exists():
        return None
    state = json.loads(state_path.read_text(encoding="utf-8"))
    exc_id = state.get("halt_exception_id")
    if not state.get("halted") or not exc_id:
        return None
    rows: dict[str, dict] = {}
    exc_path = STORE / "exceptions.jsonl"
    if exc_path.exists():
        for line in exc_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rows[r["id"]] = {**rows.get(r["id"], {}), **r}
    return exc_id if rows.get(exc_id, {}).get("status") != "RESOLVED" else None


def record_storm(starts: list[datetime], log: logging.Logger) -> None:
    sys.path.insert(0, str(BACKEND))
    from app.research.candle12_v3_paper.store import PaperStore

    store = PaperStore(STORE, mode="VIRTUAL_PAPER_MAINNET")
    exc_id = f"SERVICE_RESTART_STORM:{now().strftime('%Y-%m-%dT%H')}"
    store.exception(exc_id, "SERVICE_RESTART_STORM", starts=[s.isoformat() for s in starts],
                    detail=f"{len(starts)} runner starts within {STORM_WINDOW}; review supervisor.log")
    store.alert("PROCESS_RESTART", cause="SERVICE_RESTART_STORM", starts=len(starts))
    log.error("restart storm recorded as %s", exc_id)


def prune_run_logs() -> None:
    logs = sorted(LOGS.glob("runner_*.log"), key=lambda p: p.stat().st_mtime)
    for p in logs[: max(0, len(logs) - 2 * KEEP_RUN_LOGS)]:
        p.unlink()


def main() -> int:
    log = setup_log()
    lock = single_instance()
    if lock is None:
        log.info("another supervisor holds the lock; exiting")
        return 0
    log.info("supervisor start pid=%s python=%s cwd=%s", os.getpid(), PYTHON, BACKEND)
    ensure_normal_priority(psutil.Process(), log)  # children (the runner) inherit it
    starts: list[datetime] = []
    storm_logged_hour = None
    delay = 60
    while True:
        running = live_runners()
        if running:
            p = running[0]
            log.info("adopting live runner pid=%s; watching", p.pid)
            for proc in running:
                ensure_normal_priority(proc, log)
            psutil.wait_procs(running, timeout=None)
            log.warning("adopted runner pid=%s exited", p.pid)
            continue
        halt = halted_and_open()
        if halt:
            log.error("store halted with open exception %s; not starting a runner (resolve it first)", halt)
            return HALTED_EXIT
        t = now()
        starts = [s for s in starts if t - s < STORM_WINDOW]
        if len(starts) >= STORM_STARTS and storm_logged_hour != t.strftime("%Y%m%d%H"):
            record_storm(starts, log)
            storm_logged_hour = t.strftime("%Y%m%d%H")
        stamp = t.strftime("%Y%m%dT%H%M%SZ")
        prune_run_logs()
        with open(LOGS / f"runner_{stamp}.out.log", "w", encoding="utf-8") as out, \
                open(LOGS / f"runner_{stamp}.err.log", "w", encoding="utf-8") as err:
            proc = subprocess.Popen(CMD, cwd=BACKEND, stdout=out, stderr=err, creationflags=CREATE_NO_WINDOW)
            log.info("runner started pid=%s (launcher) logs=runner_%s.*", proc.pid, stamp)
            code = proc.wait()
            for child in live_runners():  # the venv launcher can leave the real interpreter behind
                psutil.wait_procs([child], timeout=None)
        ran = now() - t
        log.warning("runner exited code=%s after %.0fs", code, ran.total_seconds())
        if code == HALTED_EXIT:
            log.error("runner reported a recovery HALT; not restarting until an operator resolves it")
            return HALTED_EXIT
        if code == REFUSED_EXIT and ran < timedelta(seconds=60):
            log.info("startup refused (see runner_%s.out.log); retrying in 60s", stamp)
            time.sleep(60)
            continue
        starts.append(t)  # a real run that ended: counts toward a restart storm
        delay = 60 if ran > timedelta(minutes=30) else min(delay * 2, 600)
        log.info("restarting in %ss", delay)
        time.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(main())
