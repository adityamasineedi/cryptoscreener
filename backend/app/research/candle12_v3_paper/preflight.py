"""Launch-readiness preflight for an OOS-eligible (virtual_paper_mainnet) run.

PREFLIGHT_PASS only when every check passes; otherwise PREFLIGHT_FAIL with one
structured row per failed check (name, detail, action). ``run`` calls it and
refuses to start on a failure.

Checks:
   1 git_tree_clean          every contract file (fingerprinted code, contract doc, .gitattributes,
                             preserved superseded files) is committed and unmodified
   2 freeze_lock             lock exists, was written by freeze-oos for this contract id,
                             its own hash verifies, its tag exists, its commit is in HEAD's history
   3 fingerprint_match       config, code and contract fingerprints equal the lock
   4 oos_window              fresh start: now is before the OOS start. Resume: the store was
                             created before the start, for this contract, with its first
                             tradable bar exactly at the OOS start
   5 mode_and_symbols        requested mode and symbols are the lock's allowed ones
   6 candle_continuity       stored BTCUSDT 1h bars continuous from warm-up start through the
                             last bar that closed at least ``bar_grace`` ago
   7 funding_continuity      stored 8h settlements continuous through ``funding_grace`` ago
   8 kill_switch_released
   9 no_open_exceptions
  10 broker_state_reconciled no in-flight request, not halted, venue position = local open
                             quantity, every venue order known locally
  11 disk_and_permissions    free space >= min_free_gb and the store directory is writable
  12 clock_skew              |local - exchange server time| <= max_skew_ms

On a resume (``recover=True``) checks 8 and 9 are WARN, not FAIL: refusing a
restart would leave the book unmanaged and add missed bars, and every open
exception still blocks OOS acceptance on its own.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app.research.candle12_v3_paper import backfill as BF
from app.research.candle12_v3_paper import contract as C
from app.research.candle12_v3_paper import guard
from app.research.candle12_v3_paper import modes as M
from app.research.candle12_v3_paper.broker import OPEN_STATUSES
from app.research.candle12_v3_paper.safety import DEFAULT_KILL_SWITCH

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
PREFLIGHT_PASS, PREFLIGHT_FAIL = "PREFLIGHT_PASS", "PREFLIGHT_FAIL"
UTC = timezone.utc
HOUR = timedelta(hours=1)


def _git_status(root: Path) -> str:
    """Contract files not committed exactly as on disk (the same scope freeze-oos enforces).

    Unrelated work elsewhere in the tree does not block an OOS run."""
    from app.research.candle12_v3_paper.freeze import contract_dirty

    return chr(10).join(contract_dirty(root))


def _tag_exists(root: Path, tag: str) -> bool:
    return bool(subprocess.run(["git", "tag", "--list", tag], cwd=root, capture_output=True, text=True).stdout.strip())


def _is_ancestor(root: Path, commit: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=root,
                          capture_output=True).returncode == 0


def _server_time_ms() -> tuple[int, int]:
    """(exchange ms, local ms at the midpoint of the request)."""
    import httpx

    t0 = time.time()
    resp = httpx.get(f"{M.MAINNET_DATA_BASE}/fapi/v1/time", timeout=10.0)
    t1 = time.time()
    resp.raise_for_status()
    return int(resp.json()["serverTime"]), int((t0 + t1) / 2 * 1000)


@dataclass
class PreflightEnv:
    root: Path = C.REPO_ROOT
    lock_path: Path = C.LOCK_PATH
    kill_switch_path: Path = DEFAULT_KILL_SWITCH
    repo: Any = None  # backfill.Repository; PostgresRepository when None
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    git_status: Callable[[Path], str] = _git_status
    tag_exists: Callable[[Path, str], bool] = _tag_exists
    is_ancestor: Callable[[Path, str], bool] = _is_ancestor
    server_time_ms: Callable[[], tuple[int, int]] = _server_time_ms
    disk_free: Callable[[Path], int] = lambda p: shutil.disk_usage(p).free
    min_free_gb: float = 1.0
    max_skew_ms: int = 1000
    bar_grace: timedelta = timedelta(minutes=10)
    funding_grace: timedelta = timedelta(minutes=15)
    funding_interval: timedelta = timedelta(hours=8)
    extra: dict[str, Any] = field(default_factory=dict)


def run_preflight(mode: str, symbols: list[str], store_root: Path, *, recover: bool = False,
                  env: PreflightEnv | None = None) -> dict[str, Any]:
    env = env or PreflightEnv()
    now = env.now()
    store_root = Path(store_root)
    checks: list[dict[str, Any]] = []

    def add(name: str, status: str, detail: Any = None, action: str | None = None) -> None:
        checks.append({"name": name, "status": status, "detail": detail, "action": action})

    def guarded(name: str, fn: Callable[[], None], action: str) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a check that cannot run is a failed check
            add(name, FAIL, f"{type(exc).__name__}: {exc}", action)

    symbols = [s.upper() for s in symbols]
    if mode != C.OOS_MODE:
        add("mode_and_symbols", FAIL, f"preflight is for {C.OOS_MODE}; {mode} never counts toward OOS",
            "use broker-check for testnet_broker_check")
        return _result(mode, symbols, store_root, recover, now, checks)

    lock: dict[str, Any] = json.loads(env.lock_path.read_text(encoding="utf-8")) if env.lock_path.exists() else {}
    freeze = lock.get("freeze") or {}
    manifest_path = store_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    state_path = store_root / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None

    # 1 git
    def c1() -> None:
        dirty = env.git_status(env.root)
        if dirty:
            lines = dirty.splitlines()
            add("git_tree_clean", FAIL, {"entries": len(lines), "first": lines[:10]},
                "commit the contract files exactly as fingerprinted (unrelated work may stay uncommitted)")
        else:
            add("git_tree_clean", PASS)
    guarded("git_tree_clean", c1, "make git available and the tree readable")

    # 2 lock
    def c2() -> None:
        problems = []
        if not lock:
            problems.append("lock file missing")
        elif not freeze:
            problems.append("lock has no freeze block (freeze-oos has not run)")
        else:
            if lock.get("contract_id") != C.CONTRACT_ID or freeze.get("contract_id") != C.CONTRACT_ID:
                problems.append(f"lock is for {lock.get('contract_id')}, code is {C.CONTRACT_ID}")
            if lock.get("lock_hash") != C.lock_hash(lock):
                problems.append("lock_hash does not match the lock content")
            if not env.tag_exists(env.root, str(freeze.get("tag"))):
                problems.append(f"tag {freeze.get('tag')} missing")
            if not env.is_ancestor(env.root, str(freeze.get("git_commit"))):
                problems.append(f"frozen commit {freeze.get('git_commit')} is not in HEAD's history")
            if (freeze.get("supersedes") or {}).get("verified") is not True and C.SUPERSEDES:
                problems.append("supersession of the previous version is not recorded as verified")
        if problems:
            add("freeze_lock", FAIL, problems, "run freeze-oos on the clean tree (before the OOS start)")
        else:
            add("freeze_lock", PASS, {"contract_id": lock["contract_id"], "tag": freeze["tag"],
                                      "frozen_commit": freeze["git_commit"], "lock_hash": lock["lock_hash"]})
    guarded("freeze_lock", c2, "repair or re-create the lock with freeze-oos")

    # 3 fingerprints
    def c3() -> None:
        fp = guard.fingerprint_status(env.lock_path, env.root)
        codes = [c for c in fp["codes"] if c in (guard.CONFIG_DRIFT, guard.CODE_DRIFT, guard.LOCK_TAMPERED)]
        if fp["status"] == guard.OK:
            add("fingerprint_match", PASS, fp["current"])
        elif codes:
            add("fingerprint_match", FAIL, {"codes": codes, "drift": fp["drift"][:20]},
                "no OOS run on drifted code: revert the change, or freeze a new contract version")
        else:
            add("fingerprint_match", FAIL, {"codes": fp["codes"]}, "freeze-oos first")
    guarded("fingerprint_match", c3, "repair the lock")

    # 4 window
    def c4() -> None:
        if state is None:
            if manifest is not None and manifest.get("counts_toward_oos") is False:
                add("oos_window", FAIL, "store exists and is NON_OOS", "use a new, empty --store directory")
            elif now >= C.OOS_START:
                missed = int((now - C.OOS_START) // HOUR)
                add("oos_window", FAIL, {"oos_start": C.OOS_START.isoformat(), "now": now.isoformat(),
                                         "bars_already_in_window": missed},
                    "the window started without the adapter; OOS-eligible collection needs a new contract "
                    "version with a later OOS start")
            else:
                add("oos_window", PASS, {"oos_start": C.OOS_START.isoformat(),
                                         "minutes_to_start": round((C.OOS_START - now).total_seconds() / 60, 1)})
            return
        if not recover:
            add("oos_window", FAIL, "store has a saved state", "restart with --recover")
            return
        problems = []
        if not manifest or manifest.get("contract_id") != C.CONTRACT_ID:
            problems.append(f"store contract {manifest and manifest.get('contract_id')} != {C.CONTRACT_ID}")
        if not manifest or not manifest.get("counts_toward_oos"):
            problems.append("store was not created OOS-eligible")
        created = datetime.fromisoformat(manifest["created_at"]) if manifest and manifest.get("created_at") else None
        if created is None or created >= C.OOS_START:
            problems.append(f"store created at {created and created.isoformat()}, not before the OOS start")
        hs, fti = (manifest or {}).get("history_start"), state.get("first_trade_index")
        if hs is None or fti is None or datetime.fromisoformat(hs) + int(fti) * HOUR != C.OOS_START:
            problems.append("first tradable bar is not the OOS start")
        if problems:
            add("oos_window", FAIL, problems, "this store cannot carry OOS evidence; stop and review")
        else:
            add("oos_window", PASS, {"resume": True, "first_tradable_bar": C.OOS_START.isoformat()})
    guarded("oos_window", c4, "inspect the store manifest")

    # 5 mode / symbols
    def c5() -> None:
        problems = []
        if freeze and mode not in (freeze.get("allowed_modes") or []):
            problems.append(f"mode {mode} not in {freeze.get('allowed_modes')}")
        bad = [s for s in symbols if s not in (freeze.get("allowed_symbols") or [])]
        if not freeze:
            problems.append("no freeze block to compare against")
        if bad:
            problems.append(f"symbols {bad} not in {freeze.get('allowed_symbols')}")
        if not symbols:
            problems.append("no symbols given")
        add("mode_and_symbols", FAIL if problems else PASS, problems or {"mode": mode, "symbols": symbols},
            "run only the frozen mode and symbols" if problems else None)
    guarded("mode_and_symbols", c5, "check the lock")

    repo = env.repo
    if repo is None:
        repo = BF.PostgresRepository()

    # 6 candles
    def c6() -> None:
        start = C.OOS_START - HOUR * C.WARMUP_BARS
        end = datetime.fromtimestamp(int((now - env.bar_grace).timestamp()) // 3600 * 3600, tz=UTC)
        out = {}
        failed = False
        for sym in symbols:
            gaps = BF.candle_gaps(repo.candle_times(sym, start, end), start, end)
            out[sym] = {"from": start.isoformat(), "through_bar_before": end.isoformat(), "gaps": gaps[:20]}
            failed = failed or bool(gaps)
        add("candle_continuity", FAIL if failed else PASS, out,
            "run backfill-data; a gap the exchange cannot fill is recorded, never fabricated" if failed else None)
    guarded("candle_continuity", c6, "make the database reachable")

    # 7 funding
    def c7() -> None:
        start = C.OOS_START - HOUR * C.WARMUP_BARS
        end = now - env.funding_grace
        out, failed = {}, False
        for sym in symbols:
            times = repo.funding_times(sym, start, end)
            gaps = BF.funding_gaps(times, start, end, env.funding_interval)
            out[sym] = {"settlements": len(times), "last": max(times).isoformat() if times else None, "gaps": gaps[:20]}
            failed = failed or bool(gaps) or not times
        add("funding_continuity", FAIL if failed else PASS, out, "run backfill-data" if failed else None)
    guarded("funding_continuity", c7, "make the database reachable")

    # 8 kill switch
    engaged = Path(env.kill_switch_path).exists()
    add("kill_switch_released", (WARN if recover else FAIL) if engaged else PASS, {"path": str(env.kill_switch_path)},
        "kill-switch release (after confirming why it was engaged)" if engaged else None)

    # 9 exceptions
    def c9() -> None:
        rows: dict[str, dict[str, Any]] = {}
        exc_path = store_root / "exceptions.jsonl"
        if exc_path.exists():
            for line in exc_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    rows[r["id"]] = {**rows.get(r["id"], {}), **r}
        open_ = [{"id": r["id"], "category": r.get("category")} for r in rows.values() if r.get("status") != "RESOLVED"]
        status = PASS if not open_ else (WARN if recover else FAIL)
        add("no_open_exceptions", status, open_[:20] or None,
            "resolve-exception with a written explanation" if open_ else None)
    guarded("no_open_exceptions", c9, "read the store")

    # 10 broker state
    def c10() -> None:
        if state is None:
            add("broker_state_reconciled", PASS, "no saved state (fresh store)")
            return
        problems = []
        if state.get("inflight"):
            problems.append({"inflight": sorted(state["inflight"])})
        if state.get("halted"):
            problems.append({"halted": state["halted"]})
        venue = state.get("broker") or {}
        local = 0.0
        for s in (state.get("slots") or {}).values():
            q = sum(f["qty"] for f in s.get("entry_fills", [])) - sum(f["qty"] for f in s.get("exit_fills", []))
            local += q if s.get("direction") == "LONG" else -q
        if abs(float(venue.get("position") or 0.0) - local) > 1e-12:
            problems.append({"venue_position": venue.get("position"), "local_open_qty": local})
        known = set(state.get("orders") or {})
        unknown = [cid for cid, o in (venue.get("orders") or {}).items()
                   if o.get("status") in OPEN_STATUSES and cid not in known]
        if unknown:
            problems.append({"unknown_open_orders": unknown})
        if problems:
            add("broker_state_reconciled", FAIL, problems,
                "in-flight requests are settled by run --recover; a halt or mismatch needs review first")
        else:
            add("broker_state_reconciled", PASS, {"local_open_qty": local})
    guarded("broker_state_reconciled", c10, "read state.json")

    # 11 disk
    def c11() -> None:
        target = store_root if store_root.exists() else store_root.parent
        target.mkdir(parents=True, exist_ok=True)
        free = env.disk_free(target)
        probe = target / f".preflight_{uuid.uuid4().hex}"
        probe.write_text("ok", encoding="utf-8")
        readable = probe.read_text(encoding="utf-8") == "ok"
        probe.unlink()
        ok = free >= env.min_free_gb * 1024 ** 3 and readable
        add("disk_and_permissions", PASS if ok else FAIL, {"free_gb": round(free / 1024 ** 3, 2), "dir": str(target)},
            None if ok else f"free at least {env.min_free_gb} GB")
    guarded("disk_and_permissions", c11, "make the store directory writable")

    # 12 clock
    def c12() -> None:
        server, local = env.server_time_ms()
        skew = local - server
        ok = abs(skew) <= env.max_skew_ms
        add("clock_skew", PASS if ok else FAIL, {"skew_ms": skew, "max_ms": env.max_skew_ms},
            None if ok else "sync the system clock (w32tm /resync)")
    guarded("clock_skew", c12, "make https://fapi.binance.com/fapi/v1/time reachable")

    return _result(mode, symbols, store_root, recover, now, checks)


def _result(mode: str, symbols: list[str], store_root: Path, recover: bool, now: datetime,
            checks: list[dict[str, Any]]) -> dict[str, Any]:
    failures = [c for c in checks if c["status"] == FAIL]
    return {
        "result": PREFLIGHT_FAIL if failures else PREFLIGHT_PASS,
        "contract_id": C.CONTRACT_ID,
        "mode": mode,
        "symbols": symbols,
        "store": str(store_root),
        "recover": recover,
        "as_of": now.isoformat(),
        "checks": checks,
        "failures": [{"name": c["name"], "detail": c["detail"], "action": c["action"]} for c in failures],
        "warnings": [{"name": c["name"], "detail": c["detail"]} for c in checks if c["status"] == WARN],
        "pid": os.getpid(),
    }
