"""Read-only git diagnostics. Never runs destructive git commands."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from app.config import ROOT

# Paths especially relevant for multi-agent conflict awareness
WATCHED_PREFIXES = (
    "backend/app/signals/",
    "backend/app/research/",
    "frontend/src/",
    "backend/app/db/",
    "backend/app/services/database.py",
)


def _run(args: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001
        return 1, "", f"{exc.__class__.__name__}: {exc}"


def collect_git_info(repo_root: Path | None = None) -> dict[str, Any]:
    root = repo_root or ROOT
    if not (root / ".git").exists():
        return {
            "available": False,
            "reason": "not a git repository",
            "branch": None,
            "commit": None,
            "dirty": None,
            "modified_files": [],
            "untracked_files": [],
            "recent_commits": [],
            "watched_conflicts": [],
        }

    code, branch, err = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root)
    if code != 0:
        return {
            "available": False,
            "reason": err or "git rev-parse failed",
            "branch": None,
            "commit": None,
            "dirty": None,
            "modified_files": [],
            "untracked_files": [],
            "recent_commits": [],
            "watched_conflicts": [],
        }

    _, commit, _ = _run(["git", "rev-parse", "HEAD"], root)
    _, short, _ = _run(["git", "rev-parse", "--short", "HEAD"], root)
    _, status_out, _ = _run(["git", "status", "--porcelain"], root)
    _, log_out, _ = _run(
        ["git", "log", "-8", "--pretty=format:%h %s"], root
    )

    modified: list[str] = []
    untracked: list[str] = []
    for line in status_out.splitlines():
        if not line.strip():
            continue
        if line.startswith("??"):
            untracked.append(line[3:].strip())
        else:
            # XY PATH or XY ORIG -> PATH
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            modified.append(path)

    dirty = bool(modified or untracked)
    watched = [
        p
        for p in (modified + untracked)
        if any(p.replace("\\", "/").startswith(pref) for pref in WATCHED_PREFIXES)
    ]

    recent = []
    for line in log_out.splitlines():
        line = line.strip()
        if line:
            recent.append(line)

    return {
        "available": True,
        "reason": None,
        "branch": branch or None,
        "commit": commit or None,
        "commit_short": short or None,
        "dirty": dirty,
        "worktree_status": "DIRTY" if dirty else "CLEAN",
        "modified_files": modified,
        "untracked_files": untracked,
        "modified_count": len(modified),
        "untracked_count": len(untracked),
        "recent_commits": recent,
        "watched_conflicts": watched,
        "watched_note": (
            "Potential multi-agent conflict zones touched"
            if watched
            else "No watched paths currently modified"
        ),
    }
