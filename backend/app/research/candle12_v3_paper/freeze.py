"""freeze-oos: freeze the current contract version, commit the lock, and tag the commit.

The command refuses, with nothing written, when:
- the working tree is not clean, or any file the contract depends on is missing
  (DIRTY_WORKING_TREE): the lock records one commit, so the whole tree must be that
  commit, and the fingerprinted code, the contract document, .gitattributes and the
  superseded version's preserved files must all exist;
- it is at or after the contract's OOS start (FREEZE_AFTER_OOS_START): a frozen
  baseline must exist before the first window bar. A later freeze needs a new
  version with a later start;
- the lock at the path is already frozen under this contract id
  (LOCK_ALREADY_FROZEN): a frozen lock is never rewritten in place;
- the lock at the path belongs to a contract this version does not supersede
  (UNKNOWN_EXISTING_LOCK);
- the superseded version's preserved lock or contract text is missing, or does
  not hash to the recorded value (SUPERSEDED_LOCK_UNVERIFIED);
- the drift-review commit is not in the repository.

The lock records the git commit and branch, the config, code and contract
fingerprints, the OOS window, the allowed symbols and mode, the verified
supersession record, and a hash of itself. Only the lock file is committed, and
that commit gets an annotated tag. Nothing is pushed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.research.candle12_v3_paper import contract as C

DIRTY_WORKING_TREE = "DIRTY_WORKING_TREE"
TAG_EXISTS = "TAG_EXISTS"
NOT_A_GIT_REPO = "NOT_A_GIT_REPO"
FREEZE_AFTER_OOS_START = "FREEZE_AFTER_OOS_START"
LOCK_ALREADY_FROZEN = "LOCK_ALREADY_FROZEN"
UNKNOWN_EXISTING_LOCK = "UNKNOWN_EXISTING_LOCK"
SUPERSEDED_LOCK_UNVERIFIED = "SUPERSEDED_LOCK_UNVERIFIED"


class FreezeError(RuntimeError):
    def __init__(self, code: str, detail: Any = None) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def tag_name(now: datetime) -> str:
    """candle12-v3-oos-2-freeze-YYYY-MM-DD for CANDLE12_V3_EXEC_FIX/OOS-2."""
    version = C.CONTRACT_ID.split("/")[-1].lower()
    return f"candle12-v3-{version}-freeze-{now:%Y-%m-%d}"


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FreezeError(NOT_A_GIT_REPO if "not a git repository" in proc.stderr else "GIT_ERROR",
                          f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def contract_paths() -> list[str]:
    """Every repo file the frozen contract depends on, except the lock the freeze writes."""
    paths = list(C.CODE_FILES) + [f"docs/{C.CONTRACT_DOC.name}", ".gitattributes"]
    sup = C.SUPERSEDES or {}
    paths += [sup[k] for k in ("lock_path", "contract_doc_path") if sup.get(k)]
    return paths


def contract_dirty(root: Path) -> list[str]:
    """Contract files that are not committed exactly as on disk (empty: all clean)."""
    paths = contract_paths()
    status = _git(root, "status", "--porcelain", "--untracked-files=all", "--", *paths)
    lines = status.splitlines() if status else []
    lines += [f"!! missing: {rel}" for rel in paths if not (root / rel).exists()]
    return lines


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def verify_supersession(root: Path, supersedes: dict[str, Any] | None = None) -> dict[str, Any]:
    """The superseded version's lock and contract text must be preserved byte-for-byte."""
    sup = dict(supersedes if supersedes is not None else C.SUPERSEDES)
    if not sup:
        return {}
    problems = []
    for key in ("lock", "contract_doc"):
        got = _sha(root / sup[f"{key}_path"])
        if got != sup[f"{key}_sha256"]:
            problems.append({"file": sup[f"{key}_path"], "expected": sup[f"{key}_sha256"], "found": got})
    commit = sup.get("drift_review_commit")
    if commit:
        ok = subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=root, capture_output=True).returncode == 0
        if not ok:
            problems.append({"commit": commit, "found": None})
    if problems:
        raise FreezeError(SUPERSEDED_LOCK_UNVERIFIED, problems)
    return {**sup, "verified": True}


def check_existing_lock(lock_path: Path) -> dict[str, Any] | None:
    """Refuse an in-place re-freeze; allow replacing an unfrozen draft or the superseded version's lock."""
    if not lock_path.exists():
        return None
    old = json.loads(lock_path.read_text(encoding="utf-8"))
    cid = old.get("contract_id")
    if cid == C.CONTRACT_ID and old.get("freeze"):
        raise FreezeError(LOCK_ALREADY_FROZEN, {"contract_id": cid, "tag": old["freeze"].get("tag")})
    if cid not in (C.CONTRACT_ID, C.SUPERSEDES.get("contract_id")):
        raise FreezeError(UNKNOWN_EXISTING_LOCK, {"contract_id": cid})
    return {"contract_id": cid, "sha256": _sha(lock_path), "had_freeze_block": bool(old.get("freeze"))}


def build_frozen_lock(root: Path, *, now: datetime, commit: str, branch: str, tag: str,
                      supersedes: dict[str, Any], replaced: dict[str, Any] | None,
                      unrelated_dirty: int = 0) -> dict[str, Any]:
    lock = C.build_lock(root)
    lock["frozen_at"] = now.isoformat()
    lock["freeze"] = {
        "contract_id": C.CONTRACT_ID,
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
        "frozen_after_oos_start": False,
        "contract_files_clean": True,
        "unrelated_dirty_entries": unrelated_dirty,
        "supersedes": supersedes,
        "replaced_file_at_lock_path": replaced,
    }
    lock["lock_hash"] = C.lock_hash(lock)
    return lock


def freeze_oos(*, root: Path = C.REPO_ROOT, lock_path: Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    root = Path(root)
    lock_path = Path(lock_path) if lock_path else root / "docs" / C.LOCK_PATH.name
    now = now or datetime.now(timezone.utc)
    if now >= C.OOS_START:
        raise FreezeError(FREEZE_AFTER_OOS_START, {"now": now.isoformat(), "oos_start": C.OOS_START.isoformat(),
                                                   "action": "new contract version with a later OOS start"})
    dirty = contract_dirty(root)
    if dirty:
        raise FreezeError(DIRTY_WORKING_TREE, dirty[:50])
    whole = _git(root, "status", "--porcelain", "--untracked-files=all")
    if whole:  # the freeze records a commit, so the whole tree must be that commit
        raise FreezeError(DIRTY_WORKING_TREE, whole.splitlines()[:50])
    unrelated_dirty = 0
    supersedes = verify_supersession(root)
    replaced = check_existing_lock(lock_path)
    tag = tag_name(now)
    if _git(root, "tag", "--list", tag):
        raise FreezeError(TAG_EXISTS, tag)
    commit = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    lock = build_frozen_lock(root, now=now, commit=commit, branch=branch, tag=tag, supersedes=supersedes,
                             replaced=replaced, unrelated_dirty=unrelated_dirty)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock, indent=1, sort_keys=True), encoding="utf-8")
    rel = lock_path.resolve().relative_to(root.resolve()).as_posix()
    _git(root, "add", "--", rel)
    _git(root, "commit", "-m", f"Freeze {C.CONTRACT_ID} OOS baseline at {commit[:12]}", "--", rel)
    _git(root, "tag", "-a", tag, "-m", f"{C.CONTRACT_ID} OOS freeze; code {commit}")
    return {"contract_id": C.CONTRACT_ID, "lock": str(lock_path), "tag": tag, "frozen_commit": commit,
            "lock_commit": _git(root, "rev-parse", "HEAD"), "branch": branch, "lock_hash": lock["lock_hash"],
            "freeze": lock["freeze"]}
