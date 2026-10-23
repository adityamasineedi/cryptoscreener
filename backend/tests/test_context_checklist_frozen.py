"""C12 stays off the frozen files and the paper engine. BASE is the C7 stack head."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "docs" / "candle12_v3_oos_contract.lock.json"
BASE = "a766fcdd6f9c87db89d5f92b5630968d3dfc3c0e"

PAPER_ENGINE_FILES = (
    "backend/app/services/paper_trade.py",
    "backend/app/services/paper_sizing.py",
    "backend/app/services/paper_risk.py",
    "backend/app/services/paper_classification.py",
    "backend/app/services/paper_ledger_view.py",
    "backend/app/services/persistence.py",
    "backend/app/services/database.py",
    "backend/app/engines/orchestrator.py",
    "backend/app/services/passed_paper_watcher.py",
    "backend/app/services/v1_paper_watcher.py",
    "backend/app/research/passed_cap_book.py",
    "backend/app/research/passed_symbol_allowlist.py",
    "backend/app/research/passed_symbol_allowlist.json",
    "backend/app/research/v1_production.py",
)

ALLOWED_CHANGES = {
    "backend/app/screener_tool/context_checklist.py",
    "backend/app/screener_tool/api.py",
    "backend/tests/test_context_checklist.py",
    "backend/tests/test_context_checklist_frozen.py",
    "frontend/src/api/client.ts",
    "frontend/src/components/CoinDetailPanel.tsx",
    "frontend/src/components/ContextChecklist.tsx",
    "frontend/src/components/ContextChecklist.test.tsx",
    "frontend/src/components/contextChecklist.ts",
}


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout.strip()


def test_lock_lists_37_files_and_each_matches_its_sha256():
    files = json.loads(LOCK.read_text(encoding="utf-8"))["code_fingerprint"]["files"]
    assert len(files) == 37
    bad = {p: h for p, h in files.items() if hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != h}
    assert bad == {}


@pytest.mark.parametrize("group", ["frozen_37", "paper_engine"])
def test_byte_identical_to_stack_base(group):
    files = json.loads(LOCK.read_text(encoding="utf-8"))["code_fingerprint"]["files"]
    paths = sorted(files) if group == "frozen_37" else list(PAPER_ENGINE_FILES)
    changed = [rel for rel in paths if _git("rev-parse", f"{BASE}:{rel}") != _git("hash-object", "--", rel)]
    assert changed == []


def test_branch_changes_only_the_checklist():
    committed = set(filter(None, _git("diff", "--name-only", BASE, "HEAD").splitlines()))
    working = set(filter(None, _git("diff", "--name-only", "HEAD").splitlines()))
    untracked = set(filter(None, _git("ls-files", "--others", "--exclude-standard").splitlines()))
    assert (committed | working | untracked) <= ALLOWED_CHANGES
