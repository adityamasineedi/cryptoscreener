"""Owner D188 hard rules: the 37 CANDLE12 V3 OOS-2 frozen files and the paper engine files are unchanged.

1. Every file in docs/candle12_v3_oos_contract.lock.json hashes to its locked sha256 (working-copy bytes,
   as the OOS-2 drift guard reads them).
2. Those files and the paper engine files are byte-identical to the live base commit 1251e57 (git blob ids,
   so the check does not depend on the checkout's line-ending setting). Skipped outside a git checkout.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "docs" / "candle12_v3_oos_contract.lock.json"
BASE = "1251e579a084b20c4fc35030238592741487f303"

PAPER_ENGINE_FILES = (
    "backend/app/services/paper_trade.py",
    "backend/app/services/paper_sizing.py",
    "backend/app/services/paper_risk.py",
    "backend/app/services/paper_classification.py",
    "backend/app/services/paper_ledger_view.py",
    "backend/app/services/path_a_paper_validation.py",
    "backend/app/services/v21a_paper_validation.py",
    "backend/app/services/v1_paper_watcher.py",
    "backend/app/services/passed_paper_watcher.py",
    "backend/app/services/v21a_paper_watcher.py",
    "backend/app/services/v2_candidate_paper_watcher.py",
    "backend/app/services/scalping_watcher.py",
    "backend/app/services/book16_paper.py",
    "backend/app/services/candle12_v2_paper.py",
    "backend/app/services/persistence.py",
    "backend/app/services/database.py",
    "backend/app/engines/orchestrator.py",
    "backend/app/research/passed_cap_book.py",
    "backend/app/research/passed_symbol_allowlist.py",
    "backend/app/research/passed_symbol_allowlist.json",
    "backend/app/research/v1_production.py",
)


def _locked() -> dict[str, str]:
    return dict(json.loads(LOCK.read_text(encoding="utf-8"))["code_fingerprint"]["files"])


def test_lock_lists_37_files_and_each_matches_its_sha256():
    files = _locked()
    assert len(files) == 37
    bad = {p: h for p, h in files.items() if hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != h}
    assert bad == {}


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.mark.parametrize("group", ["frozen_37", "paper_engine"])
def test_byte_identical_to_live_base_commit(group):
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    try:
        _git("cat-file", "-e", f"{BASE}^{{commit}}")
    except subprocess.CalledProcessError:
        pytest.skip(f"base commit {BASE[:7]} not in this clone")
    paths = sorted(_locked()) if group == "frozen_37" else list(PAPER_ENGINE_FILES)
    if group == "frozen_37":
        assert len(paths) == 37
    changed = []
    for rel in paths:
        base_blob = _git("rev-parse", f"{BASE}:{rel}")
        now_blob = _git("hash-object", "--", rel)
        if base_blob != now_blob:
            changed.append(rel)
    assert changed == []
