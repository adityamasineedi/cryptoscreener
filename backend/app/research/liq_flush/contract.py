"""Freeze for the E34 forward paper run.

The lock is the hash of this package and the contract text. A mismatch
halts the run. The lock file's own hash covers every field except itself.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CONTRACT_ID = "LIQ_FLUSH_E34"
REPO_ROOT = Path(__file__).resolve().parents[4]
LOCK_PATH = REPO_ROOT / "docs" / "liq_flush_e34_contract.lock.json"
CONTRACT_DOC = "docs/liq_flush_e34_contract.md"

CODE_FILES: tuple[str, ...] = (
    "backend/app/research/liq_flush/__init__.py",
    "backend/app/research/liq_flush/__main__.py",
    "backend/app/research/liq_flush/atr.py",
    "backend/app/research/liq_flush/book.py",
    "backend/app/research/liq_flush/cli.py",
    "backend/app/research/liq_flush/contract.py",
    "backend/app/research/liq_flush/costs.py",
    "backend/app/research/liq_flush/execute.py",
    "backend/app/research/liq_flush/feeds.py",
    "backend/app/research/liq_flush/ledger.py",
    "backend/app/research/liq_flush/recorder.py",
    "backend/app/research/liq_flush/signal.py",
    "backend/app/research/liq_flush/stream.py",
    "backend/app/research/liq_flush/universe.py",
    CONTRACT_DOC,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def file_hashes(root: Path | None = None) -> dict[str, str]:
    root = root or REPO_ROOT
    return {rel: sha256_file(root / rel) for rel in CODE_FILES}


def fingerprint(root: Path | None = None) -> str:
    lines = [f"{rel}\n{digest}\n" for rel, digest in file_hashes(root).items()]
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def lock_body(root: Path | None = None) -> dict[str, Any]:
    return {
        "code_combined": fingerprint(root),
        "contract_id": CONTRACT_ID,
        "files": file_hashes(root),
    }


def lock_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def write_lock(root: Path | None = None) -> dict[str, Any]:
    root = root or REPO_ROOT
    body = lock_body(root)
    payload = dict(body)
    payload["lock_hash"] = lock_hash(body)
    path = root / "docs" / "liq_flush_e34_contract.lock.json"
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def read_lock(root: Path | None = None) -> dict[str, Any]:
    root = root or REPO_ROOT
    return json.loads((root / "docs" / "liq_flush_e34_contract.lock.json").read_text(encoding="utf-8"))


def drift(root: Path | None = None) -> list[str]:
    """Empty when the tree matches the lock. Any entry halts the run."""
    root = root or REPO_ROOT
    path = root / "docs" / "liq_flush_e34_contract.lock.json"
    if not path.exists():
        return ["LOCK_MISSING"]
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["LOCK_TAMPERED"]
    body = {key: lock[key] for key in ("code_combined", "contract_id", "files") if key in lock}
    found: list[str] = []
    if lock.get("lock_hash") != lock_hash(body) or set(body) != {"code_combined", "contract_id", "files"}:
        found.append("lock_hash")
    files = lock.get("files")
    if not isinstance(files, dict):
        return found or ["lock_hash"]
    for rel in CODE_FILES:
        if rel not in files:
            found.append(rel)
            continue
        file_path = root / rel
        if not file_path.exists() or sha256_file(file_path) != files[rel]:
            found.append(rel)
    extra = [rel for rel in files if rel not in CODE_FILES]
    found.extend(f"extra:{rel}" for rel in extra)
    if fingerprint(root) != lock.get("code_combined") and "code_combined" not in found:
        found.append("code_combined")
    return found


def main() -> None:
    payload = write_lock()
    print(payload["code_combined"])


if __name__ == "__main__":
    main()
