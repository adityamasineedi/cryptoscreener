"""SHA-256 checksum helpers — never claim verified without recalculation."""

from __future__ import annotations

import hashlib
from pathlib import Path


ALGORITHM = "sha256"


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_file_checksum(path: Path, expected: str, *, algorithm: str = ALGORITHM) -> str:
    """Return CHECKSUM_VERIFIED | CHECKSUM_FAILED | NOT_VERIFIED."""
    if not expected or not path.is_file():
        return "NOT_VERIFIED"
    if algorithm.lower() != "sha256":
        return "NOT_VERIFIED"
    actual = sha256_file(path)
    return "CHECKSUM_VERIFIED" if actual.lower() == expected.lower() else "CHECKSUM_FAILED"
