"""Cross-contract shared-module policy for the frozen research contracts. Read-only on locks.

Every frozen contract lock fingerprints its code files (sha256 per file). Some files are
fingerprinted by more than one contract (trade_fees.py, swing_detector.py, ...). This tool:

1. detects every fingerprinted file whose current bytes differ from any lock;
2. reports which contracts each changed file affects (a shared file affects all of them);
3. classifies each change from the append-only review ledger:
     NO_BEHAVIOR_CHANGE  reviewed, with a diff and passing-test evidence on record
     BEHAVIOR_CHANGE     reviewed, behaviour changes
     UNREVIEWED_CHANGE   no ledger entry for exactly this (file, frozen sha, current sha)
4. decides per contract:
     OK                         nothing changed
     ALLOW_DOCUMENTED_REFREEZE  every change is NO_BEHAVIOR_CHANGE: the contract may be refrozen
                                with its own audited refreeze command, citing the ledger entries
     RESTART_REQUIRED           any BEHAVIOR_CHANGE or UNREVIEWED_CHANGE: a new contract version
                                with a new forward/OOS start, for every affected contract

It never writes, updates or deletes a lock. A refreeze is always the contract's own explicit
command (LCSC: `forward.py refreeze`; CANDLE12: its freeze command for a new contract id).

  python scripts/contract_policy/shared_modules.py report [--json]
  python scripts/contract_policy/shared_modules.py classify --file REL --from SHA --to SHA \
      --class NO_BEHAVIOR_CHANGE|BEHAVIOR_CHANGE --diff PATH --tests "CMD -> RESULT" --by NAME --reason TEXT
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[3]
CONTRACTS: dict[str, Path] = {
    "LCSC_ETH_FORWARD": REPO / "docs" / "lcsc_eth_forward_contract.lock.json",
    "CANDLE12_V3_OOS": REPO / "docs" / "candle12_v3_oos_contract.lock.json",
}
LEDGER = REPO / "docs" / "contract_policy" / "shared_module_reviews.jsonl"
CLASSES = ("NO_BEHAVIOR_CHANGE", "BEHAVIOR_CHANGE")
UNREVIEWED = "UNREVIEWED_CHANGE"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "MISSING"


def load_locks(contracts: Mapping[str, Path] = CONTRACTS) -> dict[str, dict[str, Any]]:
    out = {}
    for name, path in contracts.items():
        if path.exists():
            lock = json.loads(path.read_text(encoding="utf-8"))
            out[name] = {"contract_id": lock.get("contract_id"), "lock_path": str(path),
                         "files": dict(lock.get("code_fingerprint", {}).get("files", {}))}
    return out


def read_ledger(ledger: Path = LEDGER) -> list[dict[str, Any]]:
    if not ledger.exists():
        return []
    return [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]


def classify_change(rel: str, frozen: str, current: str, ledger: list[dict[str, Any]]) -> tuple[str, dict | None]:
    matches = [e for e in ledger if e["file"] == rel and e["from_sha"] == frozen and e["to_sha"] == current]
    if not matches:
        return UNREVIEWED, None
    entry = matches[-1]  # the latest review of exactly this change
    return entry["classification"], entry


def report(*, root: Path = REPO, contracts: Mapping[str, Path] = CONTRACTS, ledger: Path = LEDGER) -> dict[str, Any]:
    locks = load_locks(contracts)
    entries = read_ledger(ledger)
    owners: dict[str, list[str]] = {}
    for name, lk in locks.items():
        for rel in lk["files"]:
            owners.setdefault(rel, []).append(name)
    shared = sorted(rel for rel, names in owners.items() if len(names) > 1)
    changes = []
    for name, lk in locks.items():
        for rel, frozen in lk["files"].items():
            current = _sha(root / rel)
            if current != frozen:
                cls, entry = classify_change(rel, frozen, current, entries)
                changes.append({"contract": name, "file": rel, "frozen_sha": frozen, "current_sha": current,
                                "shared_with": [n for n in owners[rel] if n != name], "classification": cls,
                                "review": entry})
    decisions = {}
    for name in locks:
        mine = [c for c in changes if c["contract"] == name]
        if not mine:
            decisions[name] = "OK"
        elif all(c["classification"] == "NO_BEHAVIOR_CHANGE" for c in mine):
            decisions[name] = "ALLOW_DOCUMENTED_REFREEZE"
        else:
            decisions[name] = "RESTART_REQUIRED"
    return {"generated_at": datetime.now(timezone.utc).isoformat(),
            "contracts": {n: {"contract_id": lk["contract_id"], "files": len(lk["files"])} for n, lk in locks.items()},
            "shared_files": shared, "changes": changes, "decisions": decisions}


def classify(*, file: str, from_sha: str, to_sha: str, classification: str, diff: str, tests: str, by: str,
             reason: str, root: Path = REPO, ledger: Path = LEDGER) -> dict[str, Any]:
    """Append one review to the ledger. NO_BEHAVIOR_CHANGE needs a diff on disk and test evidence."""
    if classification not in CLASSES:
        raise ValueError(f"classification must be one of {CLASSES}")
    if not by.strip() or not reason.strip():
        raise ValueError("a review needs a reviewer and a reason")
    if _sha(root / file) != to_sha:
        raise ValueError("to_sha is not the file's current content")
    if classification == "NO_BEHAVIOR_CHANGE":
        if not diff or not (root / diff).exists():
            raise ValueError("NO_BEHAVIOR_CHANGE needs the reviewed diff committed under the repo")
        if "->" not in tests:
            raise ValueError("NO_BEHAVIOR_CHANGE needs test evidence as 'command -> result'")
    entry = {"file": file, "from_sha": from_sha, "to_sha": to_sha, "classification": classification,
             "diff": diff, "tests": tests, "by": by, "reason": reason,
             "at": datetime.now(timezone.utc).isoformat()}
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shared_modules")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report")
    rp.add_argument("--json", action="store_true")
    cl = sub.add_parser("classify")
    for a in ("--file", "--from", "--to", "--class", "--diff", "--tests", "--by", "--reason"):
        cl.add_argument(a, required=a not in ("--diff", "--tests"), default="")
    args = ap.parse_args(argv)
    if args.cmd == "classify":
        try:
            entry = classify(file=args.file, from_sha=getattr(args, "from"), to_sha=args.to,
                             classification=getattr(args, "class"), diff=args.diff, tests=args.tests,
                             by=args.by, reason=args.reason)
        except ValueError as exc:
            print(f"REFUSED: {exc}")
            return 2
        print(json.dumps(entry, indent=1))
        return 0
    rep = report()
    if args.json:
        print(json.dumps(rep, indent=1))
    else:
        print("contracts:", json.dumps(rep["contracts"]))
        print("shared files:", len(rep["shared_files"]))
        for f in rep["shared_files"]:
            print("  ", f)
        for c in rep["changes"]:
            print(f"CHANGED [{c['contract']}] {c['file']} -> {c['classification']}"
                  + (f" (also affects {', '.join(c['shared_with'])})" if c["shared_with"] else ""))
        print("decisions:", json.dumps(rep["decisions"]))
    return 0 if all(d == "OK" for d in rep["decisions"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
