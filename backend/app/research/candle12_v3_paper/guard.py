"""Pre-run fingerprint guard.

An OOS-eligible run (virtual_paper_mainnet) may start or resume only when the
lock file was written by ``freeze-oos``, the lock is intact, and the current
config and code fingerprints equal the lock's:

  LOCK_MISSING   no lock file
  NOT_FROZEN     the lock has no freeze block (freeze-oos never ran)
  LOCK_TAMPERED  the lock's own hash does not match its content
  CONFIG_DRIFT   contract config (or the contract document) differs from the lock
  CODE_DRIFT     a fingerprinted file, or the fingerprinted file list, differs
  MODE_NOT_ALLOWED / SYMBOL_NOT_ALLOWED  the run is outside the lock's scope

testnet_broker_check and smoke runs are never blocked; they are labelled NON_OOS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.research.candle12_v3_paper import contract as C
from app.research.candle12_v3_paper import modes as M

OK = "OK"
LOCK_MISSING = "LOCK_MISSING"
NOT_FROZEN = "NOT_FROZEN"
LOCK_TAMPERED = "LOCK_TAMPERED"
CONFIG_DRIFT = "CONFIG_DRIFT"
CODE_DRIFT = "CODE_DRIFT"
MODE_NOT_ALLOWED = "MODE_NOT_ALLOWED"
SYMBOL_NOT_ALLOWED = "SYMBOL_NOT_ALLOWED"


class DriftError(RuntimeError):
    def __init__(self, code: str, detail: Any = None) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def fingerprint_status(lock_path: Path = C.LOCK_PATH, root: Path = C.REPO_ROOT) -> dict[str, Any]:
    if not Path(lock_path).exists():
        return {"status": LOCK_MISSING, "codes": [LOCK_MISSING], "drift": []}
    lock = C.load_lock(Path(lock_path))
    codes: list[str] = []
    freeze = lock.get("freeze")
    if not freeze:
        codes.append(NOT_FROZEN)
    elif lock.get("lock_hash") != C.lock_hash(lock):
        codes.append(LOCK_TAMPERED)
    drift = C.drift(lock, root)
    if "config_fingerprint" in drift:
        codes.append(CONFIG_DRIFT)
    if freeze and freeze.get("contract_fingerprint") not in (None, C.contract_fingerprint(root / "docs" / C.CONTRACT_DOC.name)):
        if CONFIG_DRIFT not in codes:
            codes.append(CONFIG_DRIFT)
        drift.append("contract_doc")
    if any(d.startswith("code:") for d in drift):
        codes.append(CODE_DRIFT)
    return {
        "status": codes[0] if codes else OK,
        "codes": codes,
        "drift": drift,
        "lock": {"config": lock.get("config_fingerprint"),
                 "code": (lock.get("code_fingerprint") or {}).get("combined"),
                 "commit": (freeze or {}).get("git_commit"), "tag": (freeze or {}).get("tag")},
        "current": {"config": C.config_fingerprint(), "code": C.code_fingerprint(root)["combined"]},
    }


def preflight(mode: str, *, symbol: str = C.SYMBOL, lock_path: Path = C.LOCK_PATH,
              root: Path = C.REPO_ROOT) -> dict[str, Any]:
    """Whether a run in ``mode`` may count toward OOS. Never raises; see require_oos_eligible."""
    s = M.spec(mode)
    fp = fingerprint_status(lock_path, root)
    codes = list(fp["codes"])
    if s.mode != C.OOS_MODE:
        return {"mode": mode, "label": M.NON_OOS, "eligible": False, "status": fp["status"], "codes": codes,
                "fingerprint": fp, "reason": "mode never counts toward OOS"}
    lock = C.load_lock(Path(lock_path)) if Path(lock_path).exists() else {}
    freeze = lock.get("freeze") or {}
    if freeze and mode not in (freeze.get("allowed_modes") or []):
        codes.append(MODE_NOT_ALLOWED)
    if freeze and symbol.upper() not in (freeze.get("allowed_symbols") or []):
        codes.append(SYMBOL_NOT_ALLOWED)
    eligible = not codes
    return {"mode": mode, "label": M.OOS_ELIGIBLE if eligible else M.NON_OOS, "eligible": eligible,
            "status": codes[0] if codes else OK, "codes": codes, "fingerprint": fp}


def require_oos_eligible(mode: str, **kw: Any) -> dict[str, Any]:
    result = preflight(mode, **kw)
    if not result["eligible"]:
        raise DriftError(result["status"], result["codes"])
    return result
