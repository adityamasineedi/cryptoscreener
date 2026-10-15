"""freeze-oos: record the OOS baseline in the lock file, commit it, and tag the commit.

Refuses a dirty working tree (DIRTY_WORKING_TREE): the fingerprints must
describe committed code. The lock records the git commit and branch it was
frozen from, the config / code / contract fingerprints, the OOS window, the
allowed symbols and the only OOS-eligible mode, and a hash of itself. The
lock is committed alone and that commit gets an annotated tag. Nothing is pushed.

OOS timestamps are the contract's and are not changed here: the window starts
2026-10-16T00:00:00Z and the earliest acceptance read is 2027-10-16T00:00:00Z.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.research.candle12_v3_paper import contract as C

TAG_PREFIX = "candle12-v3-oos-freeze"
DIRTY_WORKING_TREE = "DIRTY_WORKING_TREE"
TAG_EXISTS = "TAG_EXISTS"
NOT_A_GIT_REPO = "NOT_A_GIT_REPO"


class FreezeError(RuntimeError):
    def __init__(self, code: str, detail: Any = None) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FreezeError(NOT_A_GIT_REPO if "not a git repository" in proc.stderr else "GIT_ERROR",
                          f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def build_frozen_lock(root: Path, *, now: datetime, commit: str, branch: str, tag: str) -> dict[str, Any]:
    lock = C.build_lock(root)
    lock["frozen_at"] = now.isoformat()
    lock["freeze"] = {
        "git_commit": commit,
        "git_branch": branch,
        "tag": tag,
        "config_fingerprint": lock["config_fingerprint"],
        "code_fingerprint": lock["code_fingerprint"]["combined"],
        "contract_fingerprint": C.contract_fingerprint(root / "docs" / C.CONTRACT_DOC.name),
        "oos_start": C.OOS_START.isoformat(),
        "earliest_evaluation": C.earliest_evaluation().isoformat(),
        "allowed_symbols": list(C.ALLOWED_SYMBOLS),
        "allowed_modes": [C.OOS_MODE],
        "frozen_after_oos_start": now >= C.OOS_START,
    }
    lock["lock_hash"] = C.lock_hash(lock)
    return lock


def freeze_oos(*, root: Path = C.REPO_ROOT, lock_path: Path | None = None, now: datetime | None = None,
               tag_prefix: str = TAG_PREFIX) -> dict[str, Any]:
    root = Path(root)
    lock_path = Path(lock_path) if lock_path else root / "docs" / C.LOCK_PATH.name
    now = now or datetime.now(timezone.utc)
    dirty = _git(root, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise FreezeError(DIRTY_WORKING_TREE, dirty.splitlines()[:50])
    tag = f"{tag_prefix}-{now:%Y-%m-%d}"
    if _git(root, "tag", "--list", tag):
        raise FreezeError(TAG_EXISTS, tag)
    commit = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    lock = build_frozen_lock(root, now=now, commit=commit, branch=branch, tag=tag)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock, indent=1, sort_keys=True), encoding="utf-8")
    rel = lock_path.resolve().relative_to(root.resolve()).as_posix()
    _git(root, "add", "--", rel)
    _git(root, "commit", "-m", f"Freeze {C.CONTRACT_ID} OOS baseline at {commit[:12]}", "--", rel)
    _git(root, "tag", "-a", tag, "-m", f"{C.CONTRACT_ID} OOS freeze; code {commit}")
    return {"lock": str(lock_path), "tag": tag, "frozen_commit": commit, "lock_commit": _git(root, "rev-parse", "HEAD"),
            "branch": branch, "lock_hash": lock["lock_hash"], "freeze": lock["freeze"]}
