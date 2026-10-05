#!/usr/bin/env python3
"""Recover 10m combined_load artifacts overwritten by a later short run."""

from __future__ import annotations

import csv
import json
from pathlib import Path

TERM = Path(
    r"C:\Users\masin\.cursor\projects\e-cryptoscreener\terminals\606006.txt"
)
OUT = Path("reports/full_system_performance_followup")
CUR = OUT / "combined_load_stress.json"


def main() -> None:
    text = TERM.read_text(encoding="utf-8", errors="replace")
    samples: list[dict] = []
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        if '"t":' not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "t" in obj and "backfill_queue" in obj:
            samples.append(obj)

    # Extract trailing summary block printed by the 10m run.
    marker = '"duration_s": 600'
    idx = text.rfind(marker)
    if idx < 0:
        raise SystemExit("10m summary not found in terminal log")
    start = text.rfind("{", 0, idx)
    end = text.find("\nwrote combined_load_stress.json", idx)
    summary = json.loads(text[start:end])
    summary["backtest"] = {
        "error": "HTTP Error 404: Not Found",
        "job_id": None,
        "status": None,
        "note": (
            "Primary 10m run used wrong start path "
            "/api/research/long-strategy-backtest/job. "
            "Correct path is /api/research/long-strategy/backtest/start."
        ),
    }
    summary["timeseries_note"] = (
        "Full per-metric timeseries recovered as abbreviated 10s samples "
        "from the original runner stdout (health/screener/queue/lag only)."
    )

    # Preserve the later 180s overwrite as a separate artifact if present.
    if CUR.exists():
        try:
            cur = json.loads(CUR.read_text(encoding="utf-8"))
            if (cur.get("summary") or {}).get("duration_s") == 180:
                (OUT / "combined_load_with_backtest_180s.json").write_text(
                    json.dumps(cur, indent=2), encoding="utf-8"
                )
        except Exception as exc:  # noqa: BLE001
            print("warn: could not preserve 180s artifact:", exc)

    payload = {"summary": summary, "timeseries": samples}
    CUR.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    fields = [
        "t",
        "health_last_ms",
        "screener_last_ms",
        "backfill_queue",
        "backlog_state",
        "event_loop_lag_ms",
    ]
    with (OUT / "combined_load_timeseries.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in samples:
            w.writerow(r)

    print(
        json.dumps(
            {
                "restored_samples": len(samples),
                "health_p95_ms": summary["endpoints"]["health"]["p95_ms"],
                "queue_start": summary["queue_start"],
                "queue_end": summary["queue_end"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
