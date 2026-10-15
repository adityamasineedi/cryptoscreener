"""Cross-contract shared-module policy: detection, classification, per-contract decisions, no lock writes."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "contract_policy"))

import shared_modules as SM  # noqa: E402


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def world(tmp_path):
    root = tmp_path
    files = {"shared/fees.py": b"fee = 4\n", "a/only_a.py": b"a = 1\n", "b/only_b.py": b"b = 1\n"}
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(body)
    locks = {}
    for name, rels in (("A", ["shared/fees.py", "a/only_a.py"]), ("B", ["shared/fees.py", "b/only_b.py"])):
        p = root / f"{name}.lock.json"
        p.write_text(json.dumps({"contract_id": f"{name}/V1",
                                 "code_fingerprint": {"files": {r: sha(files[r]) for r in rels}}}))
        locks[name] = p
    ledger = root / "ledger.jsonl"
    return root, locks, ledger, files


def run(world):
    root, locks, ledger, _ = world
    return SM.report(root=root, contracts=locks, ledger=ledger)


def test_no_change_is_ok_and_shared_files_are_found(world):
    rep = run(world)
    assert rep["shared_files"] == ["shared/fees.py"]
    assert rep["decisions"] == {"A": "OK", "B": "OK"} and rep["changes"] == []


def test_unreviewed_shared_change_restarts_every_affected_contract(world):
    root, locks, ledger, _ = world
    before = {n: p.read_bytes() for n, p in locks.items()}
    (root / "shared/fees.py").write_bytes(b"fee = 5\n")
    rep = run(world)
    assert {c["contract"] for c in rep["changes"]} == {"A", "B"}
    assert all(c["classification"] == "UNREVIEWED_CHANGE" for c in rep["changes"])
    assert rep["decisions"] == {"A": "RESTART_REQUIRED", "B": "RESTART_REQUIRED"}
    assert {n: p.read_bytes() for n, p in locks.items()} == before  # never touches a lock


def test_no_behavior_change_with_evidence_allows_a_documented_refreeze(world):
    root, locks, ledger, files = world
    new = b"fee = 4  # comment only\n"
    (root / "shared/fees.py").write_bytes(new)
    (root / "review.diff").write_text("- fee = 4\n+ fee = 4  # comment only\n")
    with pytest.raises(ValueError):
        SM.classify(file="shared/fees.py", from_sha=sha(files["shared/fees.py"]), to_sha=sha(new),
                    classification="NO_BEHAVIOR_CHANGE", diff="review.diff", tests="no arrow", by="r", reason="x",
                    root=root, ledger=ledger)
    SM.classify(file="shared/fees.py", from_sha=sha(files["shared/fees.py"]), to_sha=sha(new),
                classification="NO_BEHAVIOR_CHANGE", diff="review.diff", tests="pytest tests/ -> 200 passed",
                by="reviewer", reason="comment only", root=root, ledger=ledger)
    rep = run(world)
    assert rep["decisions"] == {"A": "ALLOW_DOCUMENTED_REFREEZE", "B": "ALLOW_DOCUMENTED_REFREEZE"}


def test_behavior_change_restarts_and_one_bad_file_outweighs_a_good_one(world):
    root, locks, ledger, files = world
    (root / "a/only_a.py").write_bytes(b"a = 2\n")
    SM.classify(file="a/only_a.py", from_sha=sha(files["a/only_a.py"]), to_sha=sha(b"a = 2\n"),
                classification="BEHAVIOR_CHANGE", diff="", tests="", by="r", reason="value changed",
                root=root, ledger=ledger)
    rep = run(world)
    assert rep["decisions"] == {"A": "RESTART_REQUIRED", "B": "OK"}


def test_a_review_applies_only_to_the_exact_bytes_it_reviewed(world):
    root, locks, ledger, files = world
    v1 = b"fee = 4  # one\n"
    (root / "shared/fees.py").write_bytes(v1)
    (root / "d.diff").write_text("x")
    SM.classify(file="shared/fees.py", from_sha=sha(files["shared/fees.py"]), to_sha=sha(v1),
                classification="NO_BEHAVIOR_CHANGE", diff="d.diff", tests="t -> ok", by="r", reason="c",
                root=root, ledger=ledger)
    (root / "shared/fees.py").write_bytes(b"fee = 4  # two\n")  # edited again after the review
    rep = run(world)
    assert rep["decisions"]["A"] == "RESTART_REQUIRED"
    with pytest.raises(ValueError):  # cannot record a review for bytes that are not on disk
        SM.classify(file="shared/fees.py", from_sha=sha(files["shared/fees.py"]), to_sha=sha(v1),
                    classification="BEHAVIOR_CHANGE", diff="", tests="", by="r", reason="c", root=root, ledger=ledger)
