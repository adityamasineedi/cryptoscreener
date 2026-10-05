#!/usr/bin/env python3
"""Full-restart validation driver for startup backfill burst control."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
OUT = BACKEND / "reports" / "startup_backfill_burst_control"
PORT = int(os.environ.get("STARTUP_BACKFILL_PORT", "8001"))
BASE = f"http://127.0.0.1:{PORT}"
DURATION = os.environ.get("FOLLOWUP_STRESS_SECONDS", "600")
REPS = int(os.environ.get("STARTUP_BACKFILL_REPS", "3"))
VENV_PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
UVICORN = BACKEND / ".venv" / "Scripts" / "uvicorn.exe"


def _health_ok(timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=timeout) as r:
            body = r.read().decode()
        return '"status"' in body and "ok" in body
    except Exception:  # noqa: BLE001
        return False


def _stop_proc(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND)
    env["FOLLOWUP_API_BASE"] = BASE
    env["STARTUP_BACKFILL_OUT"] = "reports/startup_backfill_burst_control"
    env["FOLLOWUP_STRESS_SECONDS"] = str(DURATION)
    env["STARTUP_WINDOW_SECONDS"] = "60"
    env["ENABLE_MARKET_STRUCTURE_ANALYTICS"] = "false"
    env["STARTUP_BACKFILL_REPS"] = "1"

    for rep in range(1, REPS + 1):
        print(f"==== REP {rep} / {REPS} full restart ====", flush=True)
        stdout_path = OUT / f"uvicorn_rep{rep}_stdout.txt"
        stderr_path = OUT / f"uvicorn_rep{rep}_stderr.txt"
        stdout_f = stdout_path.open("w", encoding="utf-8")
        stderr_f = stderr_path.open("w", encoding="utf-8")
        proc = subprocess.Popen(
            [
                str(UVICORN),
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(PORT),
            ],
            cwd=str(BACKEND),
            env=env,
            stdout=stdout_f,
            stderr=stderr_f,
        )
        ready = False
        for _ in range(180):
            if proc.poll() is not None:
                print(f"uvicorn exited early code={proc.returncode}", flush=True)
                break
            if _health_ok():
                ready = True
                break
            time.sleep(1)
        if not ready:
            print(f"Health not ready for rep {rep}", flush=True)
            _stop_proc(proc)
            stdout_f.close()
            stderr_f.close()
            continue

        env["STARTUP_BACKFILL_REP_TAG"] = f"rep{rep}"
        print(f"[rep{rep}] running validation for {DURATION}s...", flush=True)
        rc = subprocess.call(
            [str(VENV_PYTHON), str(BACKEND / "scripts" / "_startup_backfill_validation.py")],
            cwd=str(BACKEND),
            env=env,
        )
        print(f"[rep{rep}] validation exit={rc}", flush=True)
        _stop_proc(proc)
        stdout_f.close()
        stderr_f.close()
        time.sleep(3)

    # Aggregate last-rep canonical names via validation script helper path.
    # Re-run aggregator lightly by importing last written files.
    print(f"All reps finished. Artifacts in {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
