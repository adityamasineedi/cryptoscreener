"""Scheduled BTCUSDT candle and funding backfill for CANDLE12_V3 OOS-2 (Task Scheduler entry point).

It runs the existing, documented command unchanged, with the backend's own interpreter:

    python -m app.research.candle12_v3_paper.cli backfill-data --symbols BTCUSDT
           --start 2026-09-04T08:00:00+00:00 --end now

The backfill implementation inserts only rows the exchange returns
(ON CONFLICT DO NOTHING). It never overwrites a stored row; a fetched row that
differs from the stored one is reported as a conflict. Nothing is invented.

- Success means exit code 0, report `ok: true`, and every data type `OK`. `backfill-data`
  exits 0 even when it reports GAPS (deferred item D1), so this wrapper reads the JSON
  report and treats GAPS as a failure.
- Retries: up to 3 attempts, 5 minutes apart.
- Single instance: an OS file lock on service_logs/backfill.lock (released when the
  process dies), plus the task's MultipleInstances=IgnoreNew.
- Records, all under the git-ignored backend/data/candle12_v3_paper/service_logs/:
    - backfill_<UTC>_a<n>.log: sanitized stdout and stderr of each attempt; the 90
      newest are kept;
    - backfill_runs.jsonl: one summary row per attempt;
    - ops_alerts.jsonl: a row when all attempts fail.
- Credentials: any DSN user:password is masked before anything is written.
- Alerts go to service_logs/ops_alerts.jsonl, not to the OOS store's alerts.jsonl.
  The live runner appends to its store with no cross-process lock, and a torn line
  there would halt the frozen runner (TORN_RECORD). The alert rows use the store's
  alert vocabulary: MISSING_FUNDING, MISSED_CANDLE, BACKFILL_FAILED.
- Exit codes: 0 on success, 1 when every attempt failed, so Task Scheduler's last
  result shows the failure.
"""

from __future__ import annotations

import json
import msvcrt
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

BACKEND = Path(__file__).resolve().parents[2]
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
LOGS = BACKEND / "data" / "candle12_v3_paper" / "service_logs"
CMD = [str(PYTHON), "-m", "app.research.candle12_v3_paper.cli", "backfill-data", "--symbols", "BTCUSDT",
       "--start", "2026-09-04T08:00:00+00:00", "--end", "now"]
ATTEMPTS, RETRY_S, TIMEOUT_S, KEEP_LOGS = 3, 300, 900, 90
CREATE_NO_WINDOW = 0x08000000
_DSN = re.compile(r"([a-z0-9+]+://)[^/\s@:]+(:[^/\s@]*)?@", re.I)
_KV = re.compile(r"(password|passwd|pwd|secret|api[_-]?key)(\s*[=:]\s*)\S+", re.I)


def utc() -> datetime:
    return datetime.now(timezone.utc)


def sanitize(text: str) -> str:
    return _KV.sub(r"\1\2***", _DSN.sub(r"\1***:***@", text))


def append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def parse_report(stdout: str) -> dict | None:
    try:
        return json.loads(stdout[stdout.index("{"): stdout.rindex("}") + 1])
    except ValueError:
        return None


def summarize(report: dict | None) -> dict:
    if not report:
        return {"report": None}
    out = {"ok": report.get("ok"), "end": report.get("end"), "symbols": {}}
    for sym, kinds in (report.get("symbols") or {}).items():
        out["symbols"][sym] = {
            k: {"status": v.get("status"), "fetched": v.get("fetched"), "inserted": v.get("inserted"),
                "gaps": len(v.get("gaps") or []), "conflicts": len(v.get("conflicts") or []),
                "error": sanitize(str(v["error"])) if v.get("error") else None}
            for k, v in kinds.items()}
    marks = (report.get("watermarks") or {}).get("BTCUSDT") or {}
    out["last_bar_open"] = (marks.get("candles_1h") or {}).get("last_bar_open")
    out["last_settlement"] = (marks.get("funding") or {}).get("last_settlement")
    return out


def healthy(code: int, summary: dict) -> bool:
    if code != 0 or summary.get("ok") is not True:
        return False
    kinds = (summary.get("symbols") or {}).get("BTCUSDT") or {}
    return bool(kinds) and all(v["status"] == "OK" and v["gaps"] == 0 and v["conflicts"] == 0 for v in kinds.values())


def prune() -> None:
    logs = sorted(LOGS.glob("backfill_*_a*.log"), key=lambda p: p.stat().st_mtime)
    for p in logs[: max(0, len(logs) - KEEP_LOGS)]:
        p.unlink()


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    me = psutil.Process()  # never run below Normal; the backfill-data child inherits it
    if me.nice() in (psutil.BELOW_NORMAL_PRIORITY_CLASS, psutil.IDLE_PRIORITY_CLASS):
        me.nice(psutil.NORMAL_PRIORITY_CLASS)
    lock = open(LOGS / "backfill.lock", "a+")
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        append(LOGS / "backfill_runs.jsonl", {"at": utc().isoformat(), "skipped": "another backfill is running"})
        return 0
    started = utc()
    summary: dict = {}
    for attempt in range(1, ATTEMPTS + 1):
        t = utc()
        try:
            proc = subprocess.run(CMD, cwd=BACKEND, capture_output=True, text=True, timeout=TIMEOUT_S,
                                  creationflags=CREATE_NO_WINDOW)
            code, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            code, out, err = -1, exc.stdout or "", f"TIMEOUT after {TIMEOUT_S}s"
        stamp = t.strftime("%Y%m%dT%H%M%SZ")
        (LOGS / f"backfill_{stamp}_a{attempt}.log").write_text(
            f"cmd: {' '.join(CMD[1:])}\nexit: {code}\n--- stdout\n{sanitize(out or '')}\n--- stderr\n{sanitize(err or '')}\n",
            encoding="utf-8")
        summary = summarize(parse_report(out or ""))
        ok = healthy(code, summary)
        append(LOGS / "backfill_runs.jsonl", {"at": t.isoformat(), "attempt": attempt, "exit_code": code, "healthy": ok,
                                               "log": f"backfill_{stamp}_a{attempt}.log", **summary})
        if ok:
            prune()
            return 0
        if attempt < ATTEMPTS:
            time.sleep(RETRY_S)
    kinds = (summary.get("symbols") or {}).get("BTCUSDT") or {}
    kind = ("MISSING_FUNDING" if (kinds.get("funding") or {}).get("status") != "OK" else
            "MISSED_CANDLE" if (kinds.get("candles_1h") or {}).get("status") != "OK" else "BACKFILL_FAILED")
    append(LOGS / "ops_alerts.jsonl", {"alert": kind, "at": utc().isoformat(), "source": "scheduled_backfill",
                                       "started": started.isoformat(), "attempts": ATTEMPTS, "last": summary,
                                       "action": "run the backfill by hand and read its JSON; see the runbook, section 3"})
    prune()
    return 1


if __name__ == "__main__":
    sys.exit(main())
