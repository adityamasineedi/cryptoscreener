"""Append-only persistence for the V3 paper adapter.

One directory per run:
  manifest.json     mode, labels, contract and code fingerprints, window
  events.jsonl      every state transition, in order
  trades.jsonl      closed paper trades with every reconciliation field
  signals.jsonl     signals that did not become trades (rejected, no fill, missed, blocked)
  exceptions.jsonl  reconciliation and operational exceptions, and their resolutions
  state.json        latest adapter snapshot (open slot, pending signal), for restarts
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODES = ("DRY_RUN", "PAPER", "SMOKE")
NON_ACCEPTANCE_SMOKE_ONLY = "NON_ACCEPTANCE_SMOKE_ONLY"


def _dump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str)


class PaperStore:
    def __init__(self, root: Path, *, mode: str, manifest: dict[str, Any] | None = None) -> None:
        if mode not in MODES:
            raise ValueError(f"mode {mode!r}; allowed {MODES}")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        path = self.root / "manifest.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("mode") != mode:
                raise ValueError(f"store {self.root} is a {existing.get('mode')} store, not {mode}")
            self.manifest = existing
        else:
            self.manifest = {
                "mode": mode,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "non_acceptance": mode == "SMOKE",
                "label": NON_ACCEPTANCE_SMOKE_ONLY if mode == "SMOKE" else mode,
                **(manifest or {}),
            }
            self._write_manifest()
        self._seq = len(self.read("events"))

    def _write_manifest(self) -> None:
        (self.root / "manifest.json").write_text(_dump(self.manifest), encoding="utf-8")

    def update_manifest(self, **fields: Any) -> None:
        self.manifest.update(fields)
        self._write_manifest()

    def _append(self, name: str, row: dict[str, Any]) -> None:
        with (self.root / f"{name}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(_dump(row) + "\n")

    def read(self, name: str) -> list[dict[str, Any]]:
        path = self.root / f"{name}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def event(self, kind: str, **payload: Any) -> dict[str, Any]:
        self._seq += 1
        row = {"seq": self._seq, "event": kind, **payload}
        self._append("events", row)
        return row

    def trade(self, row: dict[str, Any]) -> None:
        self._append("trades", row)

    def signal(self, row: dict[str, Any]) -> None:
        self._append("signals", row)

    def exception(self, exc_id: str, category: str, **detail: Any) -> None:
        if any(e["id"] == exc_id for e in self.read("exceptions")):
            return
        self._append("exceptions", {"id": exc_id, "category": category, "status": "OPEN",
                                    "created_at": datetime.now(timezone.utc).isoformat(), **detail})

    def resolve_exception(self, exc_id: str, note: str, by: str) -> None:
        if not note.strip():
            raise ValueError("a resolution needs an explanation")
        self._append("exceptions", {"id": exc_id, "status": "RESOLVED", "note": note, "by": by,
                                    "resolved_at": datetime.now(timezone.utc).isoformat()})

    def exceptions(self) -> list[dict[str, Any]]:
        """Latest state of every exception (a RESOLVED row closes the OPEN one)."""
        by_id: dict[str, dict[str, Any]] = {}
        for row in self.read("exceptions"):
            by_id[row["id"]] = {**by_id.get(row["id"], {}), **row}
        return list(by_id.values())

    def open_exceptions(self) -> list[dict[str, Any]]:
        return [e for e in self.exceptions() if e.get("status") != "RESOLVED"]

    def save_state(self, state: dict[str, Any]) -> None:
        (self.root / "state.json").write_text(_dump(state), encoding="utf-8")

    def load_state(self) -> dict[str, Any] | None:
        path = self.root / "state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
