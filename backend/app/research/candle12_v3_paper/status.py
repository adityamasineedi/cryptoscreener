"""Status of a V3 paper store, read from disk only (no network, no broker call)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.research.candle12_v3_paper import contract as C
from app.research.candle12_v3_paper import guard
from app.research.candle12_v3_paper.broker import OPEN_STATUSES
from app.research.candle12_v3_paper.safety import DEFAULT_KILL_SWITCH
from app.research.candle12_v3_paper.store import OOS_STORE_MODE, PaperStore

STORE_TO_MODE = {"VIRTUAL_PAPER_MAINNET": "virtual_paper_mainnet", "TESTNET_BROKER_CHECK": "testnet_broker_check"}


def build_status(root: Path, *, lock_path: Path = C.LOCK_PATH, repo_root: Path = C.REPO_ROOT,
                 now: datetime | None = None, kill_switch_path: Path = DEFAULT_KILL_SWITCH) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    store = PaperStore(root, mode=manifest["mode"])
    state = store.load_state() or {}
    broker = state.get("broker") or {}
    slots = state.get("slots") or {}
    open_position = {d: {"trade_id": s["trade_id"], "state": s["state"],
                         "qty": sum(f["qty"] for f in s.get("entry_fills", [])) - sum(f["qty"] for f in s.get("exit_fills", [])),
                         "stop": (s.get("plan") or {}).get("stop"), "target": (s.get("plan") or {}).get("target")}
                     for d, s in slots.items()}
    open_orders = [{"client_id": cid, "purpose": o.get("purpose"), "type": o.get("type"), "qty": o.get("qty"),
                    "trigger": o.get("stop_price"), "status": o.get("status")}
                   for cid, o in (broker.get("orders") or {}).items() if o.get("status") in OPEN_STATUSES]
    exceptions = store.open_exceptions()
    mode = STORE_TO_MODE.get(manifest["mode"], manifest["mode"].lower())
    fp = guard.fingerprint_status(lock_path, repo_root)
    halted = state.get("halted")
    reasons = []
    if manifest["mode"] != OOS_STORE_MODE:
        reasons.append(f"{manifest['mode']} stores never count toward OOS")
    if not manifest.get("counts_toward_oos"):
        reasons.append("store was not created as OOS-eligible (counts_toward_oos=false)")
    if fp["status"] != guard.OK:
        reasons.append(f"fingerprint {fp['status']}")
    if exceptions:
        reasons.append(f"{len(exceptions)} open exception(s)")
    if halted:
        reasons.append(f"halted: {halted}")
    earliest = C.earliest_evaluation()
    heartbeats = store.read("heartbeats")
    return {
        "store": str(root),
        "mode": mode,
        "label": manifest.get("label"),
        "symbols": [state.get("symbol") or manifest.get("symbol") or C.SYMBOL],
        "current_state": "HALTED" if halted else (
            ",".join(f"{d}:{s['state']}" for d, s in slots.items()) or ("SIGNAL_PENDING" if state.get("pending") else "IDLE")),
        "open_position": open_position,
        "venue_position": broker.get("position"),
        "open_orders": open_orders,
        "inflight_requests": sorted((state.get("inflight") or {})),
        "last_processed_candle": state.get("last_closed_time"),
        "last_funding_settlement": state.get("last_funding_time"),
        "last_heartbeat": heartbeats[-1] if heartbeats else None,
        "open_exceptions": [{"id": e["id"], "category": e.get("category")} for e in exceptions],
        "oos_eligibility": {
            "eligible_store": not reasons,
            "reasons": reasons,
            "oos_start": C.OOS_START.isoformat(),
            "earliest_read": earliest.isoformat(),
            "read_allowed_now": now >= earliest,
        },
        "fingerprint_status": {"status": fp["status"], "codes": fp["codes"], "drift": fp["drift"][:20]},
        "kill_switch": {"engaged": Path(kill_switch_path).exists(), "path": str(kill_switch_path)},
        "as_of": now.isoformat(),
    }
