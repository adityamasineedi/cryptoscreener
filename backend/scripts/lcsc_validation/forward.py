"""LCSC v1 ETHUSDT LONG forward-validation runner. Read-only.

Contract: docs/lcsc_eth_forward_contract.md (lock: docs/lcsc_eth_forward_contract.lock.json,
superseded versions: docs/lcsc_contract_history/).

The rules and the cost model are the archived study's own code (study.py: candidate
conversion, levels, walk, price_trade with the FULL model, regime labels), so the
forward window is priced exactly like the historical one. This file adds:
- a startup guard: code, config, contract-document and lock fingerprints must match
  the lock, or nothing runs (CONFIG_DRIFT), diagnostic reads included;
- an explicit, audited refreeze that creates a new contract version and keeps the old
  lock as SUPERSEDED (the lock is never updated to match changed code);
- recording of every candidate's fate, per-settlement funding payments, data and
  record revision checks, uptime checks (stale candles, stale funding, gaps);
- hash-chained append-only records, a crash-detecting run lock;
- the contract's gates, kill / pass / grey-zone retirement rules.

No order is ever sent. No paper or live path exists here.

  python scripts/lcsc_validation/forward.py status
  python scripts/lcsc_validation/forward.py run
  python scripts/lcsc_validation/forward.py evaluate [--diagnostic]
  python scripts/lcsc_validation/forward.py freeze-contract          (first freeze only)
  python scripts/lcsc_validation/forward.py refreeze --new-version ID --reason TEXT --by NAME
  python scripts/lcsc_validation/forward.py resolve-exception --id ID --note TEXT --by NAME
(run from backend/ with PYTHONPATH=.)
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import socket
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND / "scripts" / "strategy_rank" / "multicap"))

import study as S  # noqa: E402  (the archived simulator: rules, costs, regimes)

UTC = timezone.utc
CONTRACT_ID = "LCSC_V1_ETHUSDT_LONG/FWD-3"
SYMBOL = "ETHUSDT"
SIDE = "LONG"
FORWARD_START = datetime(2026, 10, 16, tzinfo=UTC)
SERIES_ANCHOR = datetime(2022, 11, 20, 8, tzinfo=UTC)  # the study's first bar (1,000 bars before 2023-01-01)
MIN_CLOSED_TRADES = 40
EARLIEST_EVALUATION = datetime(2027, 7, 16, tzinfo=UTC)  # 9 months after the start
TWELVE_MONTHS = datetime(2027, 10, 16, tzinfo=UTC)
TWELVE_MONTH_EXCEPTION_PREAPPROVED = False
CONTINUATION_MAX_TRADES = 80
CONTINUATION_MAX_TIME = datetime(2028, 4, 16, tzinfo=UTC)  # 18 months after the start
KILL_MAX_DD_R = 15.0
KILL_MIN_PF = 1.0
PASS_MIN_EXPECTANCY_R = 0.10
PASS_MIN_PF = 1.20
TREND_REGIMES = ("TREND_UP", "TREND_DOWN", "RANGE")
FUNDING_INTERVAL = timedelta(hours=8)
CANDLE_STALE_AFTER = timedelta(minutes=90)  # after the close of the bar that should be the latest
FUNDING_STALE_AFTER = timedelta(hours=1)  # after a settlement time that should be in the data
STALE_RUN_LOCK_AFTER = timedelta(hours=2)
RUN_SCHEDULE = "hourly at minute 20 UTC (scripts/lcsc_validation/schedule/)"
MAX_RUN_GAP = timedelta(hours=3)  # two missed hourly runs are tolerated; a third is an exception
GREY_ZONE_LANGUAGE = (
    "A NO_PASS_CONTINUE result at 40 trades is insufficient evidence, not evidence of failure. "
    "It may continue only under the predeclared continuation limit."
)
DATA_SOURCE = {
    "candles": "PostgreSQL ohlcv, symbol ETHUSDT, timeframe 1h, read-only via scripts/strategy_rank/multicap/_db.py",
    "funding": "Binance USD-M public GET /fapi/v1/fundingRate via scripts/lcsc_validation/load_data.py",
}

OUT = HERE / "forward_out"
LOCK_PATH = REPO / "docs" / "lcsc_eth_forward_contract.lock.json"
CONTRACT_DOC = REPO / "docs" / "lcsc_eth_forward_contract.md"
HISTORY_DIR = REPO / "docs" / "lcsc_contract_history"
NON_ACCEPTANCE_DIAGNOSTIC = "NON_ACCEPTANCE_DIAGNOSTIC"
CONFIG_DRIFT = "CONFIG_DRIFT"
GENESIS = "GENESIS"

CODE_FILES = (
    "backend/scripts/lcsc_validation/study.py",
    "backend/scripts/lcsc_validation/forward.py",
    "backend/scripts/lcsc_validation/load_data.py",
    "backend/scripts/strategy_rank/multicap/_db.py",
    "backend/app/research/multi_cap_strategies/large_cap_sweep_choch.py",
    "backend/app/research/multi_cap_strategies/common.py",
    "backend/app/research/multi_cap_strategies/config.py",
    "backend/app/research/multi_cap_strategies/schemas.py",
    "backend/app/research/config.py",
    "backend/app/research/trade_fees.py",
    "backend/app/research/schemas.py",
    "backend/app/signals/swing_detector.py",
    "backend/app/signals/_candle_utils.py",
    "backend/app/engines/mtf/indicators.py",
)


# ------------------------------------------------------------------ contract and fingerprints


def contract_terms() -> dict[str, Any]:
    return {
        "contract_id": CONTRACT_ID,
        "symbol": SYMBOL,
        "side": SIDE,
        "timeframe": "1h",
        "rules": dict(S.V1),
        "cost_model": dict(S.COST_MODELS["FULL"]),
        "sizing": {"risk_usd": S.RISK_USD, "equity": S.EQUITY, "leverage": S.LEVERAGE},
        "forward_start": FORWARD_START.isoformat(),
        "series_anchor": SERIES_ANCHOR.isoformat(),
        "min_closed_trades": MIN_CLOSED_TRADES,
        "earliest_evaluation": EARLIEST_EVALUATION.isoformat(),
        "twelve_months": TWELVE_MONTHS.isoformat(),
        "twelve_month_exception_preapproved": TWELVE_MONTH_EXCEPTION_PREAPPROVED,
        "kill": {"max_expectancy_R": 0.0, "min_pf": KILL_MIN_PF, "max_dd_R": KILL_MAX_DD_R,
                 "downtrend_only_profit": True, "fewer_than_min_trades_after_12_months": True,
                 "unresolved_exceptions": True},
        "pass": {"min_expectancy_R_exclusive": PASS_MIN_EXPECTANCY_R, "min_pf": PASS_MIN_PF,
                 "max_dd_R": KILL_MAX_DD_R, "min_positive_trend_regimes": 2},
        "grey_zone": {"continuation_max_closed_trades": CONTINUATION_MAX_TRADES,
                      "continuation_max_time": CONTINUATION_MAX_TIME.isoformat(),
                      "retire_as": "KILL_INSUFFICIENT_EDGE", "language": GREY_ZONE_LANGUAGE},
        "uptime": {"schedule": RUN_SCHEDULE, "max_run_gap_hours": MAX_RUN_GAP.total_seconds() / 3600,
                   "candle_stale_after_minutes": CANDLE_STALE_AFTER.total_seconds() / 60,
                   "funding_stale_after_minutes": FUNDING_STALE_AFTER.total_seconds() / 60,
                   "funding_interval_hours": FUNDING_INTERVAL.total_seconds() / 3600},
        "data_source": dict(DATA_SOURCE),
    }


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, default=str).encode()


def config_fingerprint() -> str:
    return _sha(_canon(contract_terms()))


def code_fingerprint(root: Path = REPO) -> dict[str, Any]:
    files = {rel: (_sha((root / rel).read_bytes()) if (root / rel).exists() else "MISSING") for rel in CODE_FILES}
    return {"combined": _sha(_canon(files)), "files": files}


def contract_doc_fingerprint(doc: Path = CONTRACT_DOC) -> str:
    return _sha(doc.read_bytes()) if doc.exists() else "MISSING"


def lock_hash(lock: Mapping[str, Any]) -> str:
    return _sha(_canon({k: v for k, v in lock.items() if k != "lock_hash"}))


def data_fingerprint(candles: Sequence[Mapping[str, Any]], funding: Sequence[Mapping[str, Any]]) -> str:
    h = hashlib.sha256()
    for c in candles:
        h.update(f"{c['time'].isoformat()}|{c['open']!r}|{c['high']!r}|{c['low']!r}|{c['close']!r}|{c.get('volume')!r}\n".encode())
    for r in funding:
        h.update(f"F|{r['t']}|{r['rate']!r}|{r.get('mark')}\n".encode())
    return h.hexdigest()


def build_lock(*, root: Path = REPO, doc: Path = CONTRACT_DOC, now: datetime | None = None) -> dict[str, Any]:
    lock = {
        "contract_id": CONTRACT_ID,
        "frozen_at": (now or datetime.now(UTC)).isoformat(),
        "terms": contract_terms(),
        "config_fingerprint": config_fingerprint(),
        "code_fingerprint": code_fingerprint(root),
        "contract_doc_fingerprint": contract_doc_fingerprint(doc),
    }
    lock["lock_hash"] = lock_hash(lock)
    return lock


def write_lock(path: Path = LOCK_PATH, *, root: Path = REPO, doc: Path = CONTRACT_DOC) -> dict[str, Any]:
    """First freeze only. A frozen lock is never overwritten; use ``refreeze``."""
    if path.exists():
        raise FileExistsError(f"{path} exists: the contract is frozen; a change needs `refreeze --new-version`")
    lock = build_lock(root=root, doc=doc)
    path.write_text(json.dumps(lock, indent=1, sort_keys=True), encoding="utf-8")
    return lock


def drift(lock: Mapping[str, Any], *, root: Path = REPO, doc: Path = CONTRACT_DOC) -> list[str]:
    """Every way the running code differs from the frozen lock. Empty means no drift."""
    out: list[str] = []
    if "lock_hash" in lock and lock["lock_hash"] != lock_hash(lock):
        out.append("lock_tampered")
    if lock.get("contract_id") != CONTRACT_ID:
        out.append("contract_id")
    if lock.get("config_fingerprint") != config_fingerprint():
        out.append("config_fingerprint")
    frozen_doc = lock.get("contract_doc_fingerprint")
    if frozen_doc is not None and frozen_doc != contract_doc_fingerprint(doc):
        out.append("contract_doc")
    frozen_files = lock.get("code_fingerprint", {}).get("files", {})
    now = code_fingerprint(root)["files"]
    out += [f"code:{rel}" for rel, sha in frozen_files.items() if now.get(rel) != sha]
    if set(frozen_files) != set(CODE_FILES):
        out.append("code:file_list")
    return out


class DriftBlocked(RuntimeError):
    def __init__(self, code: str, detail: Any) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def startup_guard(*, lock_path: Path = LOCK_PATH, root: Path = REPO, doc: Path = CONTRACT_DOC) -> dict[str, Any]:
    """Refuse to collect or evaluate anything unless the code is exactly what was frozen."""
    if not lock_path.exists():
        raise DriftBlocked(CONFIG_DRIFT, ["lock_missing"])
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    found = drift(lock, root=root, doc=doc)
    if found:
        raise DriftBlocked(CONFIG_DRIFT, found)
    return {"status": "OK", "contract_id": lock["contract_id"], "lock_hash": lock.get("lock_hash"),
            "config": lock["config_fingerprint"], "code": lock["code_fingerprint"]["combined"]}


def refreeze(*, new_version: str, reason: str, by: str, lock_path: Path = LOCK_PATH,
             history_dir: Path = HISTORY_DIR, root: Path = REPO, doc: Path = CONTRACT_DOC,
             now: datetime | None = None) -> dict[str, Any]:
    """The only way to change a frozen contract: a new version, the old lock kept as SUPERSEDED.

    The code must already declare ``new_version`` as CONTRACT_ID. Refused once the forward
    window has started (a new version then needs its own, later forward start in the code).
    """
    now = now or datetime.now(UTC)
    if not reason.strip() or not by.strip():
        raise ValueError("refreeze needs a reason and a name")
    if new_version != CONTRACT_ID:
        raise ValueError(f"the code declares {CONTRACT_ID}; refreeze to that id, not {new_version}")
    if now >= FORWARD_START:
        raise ValueError("WINDOW_STARTED: declare a new forward start for the new version in the code first")
    old = json.loads(lock_path.read_text(encoding="utf-8"))
    if old.get("contract_id") == new_version:
        raise ValueError(f"{new_version} is already the frozen version")
    history_dir.mkdir(parents=True, exist_ok=True)
    archived = history_dir / f"{old['contract_id'].replace('/', '_')}.lock.json"
    if archived.exists():
        raise FileExistsError(f"{archived} exists")
    old_hash = old.get("lock_hash") or lock_hash(old)
    archived.write_text(json.dumps({
        "status": "SUPERSEDED", "superseded_at": now.isoformat(), "superseded_by": new_version,
        "reason": reason, "by": by, "lock_sha256_of_canonical_json": lock_hash(old), "lock": old,
    }, indent=1, sort_keys=True), encoding="utf-8")
    new = build_lock(root=root, doc=doc, now=now)
    new["supersedes"] = {"contract_id": old["contract_id"], "lock_hash": old_hash,
                         "archived_at": str(archived.relative_to(root)) if archived.is_relative_to(root) else str(archived)}
    new["refreeze"] = {"reason": reason, "by": by, "at": now.isoformat()}
    new["lock_hash"] = lock_hash(new)
    lock_path.write_text(json.dumps(new, indent=1, sort_keys=True), encoding="utf-8")
    with (history_dir / "refreeze_log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": now.isoformat(), "from": old["contract_id"], "to": new_version, "by": by,
                             "reason": reason, "old_lock_hash": old_hash, "new_lock_hash": new["lock_hash"],
                             "config_fingerprint": new["config_fingerprint"],
                             "code_fingerprint": new["code_fingerprint"]["combined"],
                             "contract_doc_fingerprint": new["contract_doc_fingerprint"]}, sort_keys=True) + "\n")
    return new


# ------------------------------------------------------------------ series and records


def build_series(candles: Sequence[Mapping[str, Any]], funding: Sequence[Mapping[str, Any]],
                 *, forward_start: datetime = FORWARD_START) -> S.Series:
    """A study.Series over the given bars, with the study's regime labels and funding snapping."""
    s = S.Series.__new__(S.Series)
    s.sym = SYMBOL
    s.candles = list(candles)
    s.t = [c["time"] for c in s.candles]
    s.ts = np.array([c["time"].timestamp() for c in s.candles])
    s.o = np.array([c["open"] for c in s.candles])
    s.h = np.array([c["high"] for c in s.candles])
    s.l = np.array([c["low"] for c in s.candles])
    s.c = np.array([c["close"] for c in s.candles])
    s.eval_start = int(bisect.bisect_left(s.ts, forward_start.timestamp()))
    s.f_t = [round(r["t"] / 1000 / 3600) * 3600 for r in funding]
    s.f_rate = [r["rate"] for r in funding]
    s.f_mark = [float(r["mark"]) if r.get("mark") else None for r in funding]
    s._regimes()
    return s


def candidates(s: S.Series) -> list[dict[str, Any]]:
    """study.candidates for v1 (lookback 48, swing 3) without its on-disk cache."""
    from app.research.multi_cap_strategies.config import MultiCapResearchConfig
    from app.research.multi_cap_strategies.large_cap_sweep_choch import generate_candidates

    cfg = MultiCapResearchConfig(large_cap_htf_swing_lookback=S.V1["lookback"], swing_left_bars=S.V1["swing"],
                                 swing_right_bars=S.V1["swing"])
    raw = generate_candidates(s.sym, "1h", s.candles, config=cfg, index_start=s.eval_start)
    return [{"i": c.entry_index, "close": c.entry_price, "inv": c.structural_invalidation,
             "atr": c.metadata.get("atr"), "sweep_time": c.metadata.get("sweep_time")} for c in raw]


def walk_recorded(s: S.Series, cands: Sequence[Mapping[str, Any]], data_end: int) -> tuple[list[dict], list[dict]]:
    """study.walk for one v1 piece from eval_start, recording every candidate's fate."""
    fates: list[dict] = []
    taken: list[dict] = []
    next_allowed = 0
    for cand in cands:
        i = cand["i"]
        if i < s.eval_start:
            continue
        row = {"signal_time": s.t[i].isoformat(), "i": i, "close": cand["close"], "invalidation": cand["inv"],
               "atr": cand["atr"], "trend": str(s.trend[i]), "vol": str(s.vol[i])}
        if i >= data_end - 1:
            fates.append({**row, "fate": "PENDING_NEXT_BAR"})
            continue
        if i < next_allowed:
            fates.append({**row, "fate": "BLOCKED_OPEN_TRADE"})
            continue
        lv = S.levels(cand, S.V1)
        if lv is None:
            fates.append({**row, "fate": "REJECTED_NO_VALID_LEVELS"})
            continue
        stop, tp = lv
        j_exit, outcome, gap = None, "OPEN", False
        for j in range(i + 1, data_end):
            if s.l[j] <= stop:
                j_exit, outcome, gap = j, "SL", bool(s.o[j] <= stop)
                break
            if s.h[j] >= tp:
                j_exit, outcome = j, "TP"
                break
        if j_exit is None:
            j_exit = data_end - 1
        t = {"sym": s.sym, "config": S.cfg_key(S.V1), "i": i, "exit_i": j_exit, "signal_time": s.t[i].isoformat(),
             "exit_time": s.t[j_exit].isoformat(), "outcome": outcome, "gap": gap, "close": cand["close"], "stop": stop,
             "tp": tp, "hold_bars": j_exit - i, "trend": str(s.trend[i]), "vol": str(s.vol[i])}
        taken.append(t)
        fates.append({**row, "fate": "TAKEN", "outcome": outcome})
        next_allowed = j_exit + 1 if outcome != "OPEN" else data_end
    for t in taken:
        S.price_trade(s, t)
    return fates, taken


def funding_payments(s: S.Series, t: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The FULL model's settlements for one trade, one row each (same loop as study.price_trade)."""
    rows = []
    i, j, qty = t["i"], t["exit_i"], t["qty"]
    k = bisect.bisect_right(s.f_t, s.ts[i + 1])
    while k < len(s.f_t) and s.f_t[k] <= s.ts[j]:
        mark = s.f_mark[k]
        if mark is None:
            bi = bisect.bisect_left(s.ts, s.f_t[k])
            mark = s.o[bi] if bi < len(s.o) else s.c[-1]
        rows.append({"time": datetime.fromtimestamp(s.f_t[k], UTC).isoformat(), "rate": s.f_rate[k],
                     "mark": float(mark), "usd": float(-s.f_rate[k] * qty * mark)})
        k += 1
    return rows


def trade_record(s: S.Series, t: Mapping[str, Any]) -> dict[str, Any]:
    f = t["FULL"]
    pays = funding_payments(s, t)
    assert abs(sum(p["usd"] for p in pays) - f["funding_usd"]) < 1e-9
    return {
        "signal_time": t["signal_time"], "fill_time": s.t[t["i"] + 1].isoformat(), "exit_time": t["exit_time"],
        "outcome": t["outcome"], "gap_stop": t["gap"], "signal_close": t["close"], "stop": t["stop"], "target": t["tp"],
        "qty": t["qty"], "planned_risk_usd": t["risk_usd"], "entry_fill": float(f["entry"]), "exit_fill": float(f["exit"]),
        "entry_slippage_usd": float(t["qty"] * (f["entry"] - s.o[t["i"] + 1])),
        "fees_usd": float(f["fees_usd"]), "slippage_usd": float(f["slip_usd"]), "funding_usd": float(f["funding_usd"]),
        "funding_payments": pays, "gross_usd": float(f["gross_usd"]), "net_usd": float(f["net_usd"]), "R": float(f["R"]),
        "hold_bars": t["hold_bars"], "trend": t["trend"], "vol": t["vol"],
    }


# ------------------------------------------------------------------ data and uptime checks


def _exc(category: str, key: str, detail: str) -> dict[str, Any]:
    return {"id": f"{category}:{key}", "category": category, "detail": detail}


def data_checks(s: S.Series, funding: Sequence[Mapping[str, Any]], forward_start: datetime) -> list[dict[str, Any]]:
    """Missing 1h bars anywhere in the series; missing settlements inside the forward window."""
    out = []
    steps = np.diff(s.ts)
    for k in np.nonzero(steps != 3600)[0]:
        out.append(_exc("DATA_GAP", s.t[k].isoformat(), f"{s.t[k].isoformat()} -> {s.t[k + 1].isoformat()}"))
    have = {round(r["t"] / 1000 / 3600) * 3600 for r in funding}
    t = forward_start
    last_bar = s.t[-1] if s.t else forward_start
    while t <= last_bar:
        if int(t.timestamp()) not in have:
            out.append(_exc("FUNDING_GAP", t.isoformat(), f"no funding settlement at {t.isoformat()}"))
        t += FUNDING_INTERVAL
    return out


def uptime_checks(candles: Sequence[Mapping[str, Any]], funding: Sequence[Mapping[str, Any]], *, now: datetime,
                  forward_start: datetime = FORWARD_START) -> list[dict[str, Any]]:
    """Ingestion freshness inside the forward window: candles and funding must keep up with time."""
    if now < forward_start:
        return []
    out = []
    last_bar = candles[-1]["time"] if candles else None
    # The latest stored bar must have closed within the last 90 minutes (a bar closes at most
    # 60 minutes before now, so this allows 30 minutes of ingestion lag).
    if last_bar is None or now - (last_bar + timedelta(hours=1)) > CANDLE_STALE_AFTER:
        expected_bar = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        out.append(_exc("STALE_CANDLES", expected_bar.isoformat(),
                        f"latest bar {last_bar.isoformat() if last_bar else None}, expected {expected_bar.isoformat()}"))
    last_settle = max((datetime.fromtimestamp(round(r["t"] / 1000 / 3600) * 3600, UTC) for r in funding), default=None)
    # A settlement is due every 8h; allow 1h for it to appear.
    if last_settle is None or now - last_settle > FUNDING_INTERVAL + FUNDING_STALE_AFTER:
        hours = int(FUNDING_INTERVAL.total_seconds() // 3600)
        expected_settle = now.replace(minute=0, second=0, microsecond=0, hour=(now.hour // hours) * hours)
        out.append(_exc("STALE_FUNDING", expected_settle.isoformat(),
                        f"latest settlement {last_settle.isoformat() if last_settle else None}, "
                        f"expected {expected_settle.isoformat()}"))
    return out


def missed_run_checks(prev: Mapping[str, Any] | None, *, now: datetime,
                      forward_start: datetime = FORWARD_START) -> list[dict[str, Any]]:
    """A gap longer than MAX_RUN_GAP between scheduled runs inside the window is an uptime exception.

    Records are rebuilt from the data on every run, so a missed run loses nothing by itself;
    the exception exists because a gap also means stale-data and revision checks did not run.
    """
    if now < forward_start:
        return []
    last = datetime.fromisoformat(prev["as_of"]) if prev and prev.get("as_of") else None
    since = max(last, forward_start) if last else forward_start
    if now - since > MAX_RUN_GAP:
        return [_exc("RUN_MISSED", since.isoformat(),
                     f"no run between {since.isoformat()} and {now.isoformat()} (limit {MAX_RUN_GAP})")]
    return []


def day_hashes(s: S.Series) -> dict[str, str]:
    days: dict[str, Any] = {}
    for c in s.candles[s.eval_start:]:
        key = c["time"].date().isoformat()
        days.setdefault(key, hashlib.sha256()).update(
            f"{c['time'].isoformat()}|{c['open']!r}|{c['high']!r}|{c['low']!r}|{c['close']!r}\n".encode())
    return {k: v.hexdigest() for k, v in days.items()}


def revision_checks(prev: Mapping[str, Any] | None, now_days: Mapping[str, str],
                    trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not prev:
        return []
    out = []
    for day, sha in prev.get("day_hashes", {}).items():
        if day in now_days and now_days[day] != sha and day != max(prev.get("day_hashes", {}) or [day]):
            out.append(_exc("DATA_REVISION", day, f"bars of {day} changed"))
    now_closed = {t["signal_time"]: t for t in trades if t["outcome"] != "OPEN"}
    for old in prev.get("closed_trades", []):
        new = now_closed.get(old["signal_time"])
        if new is None or abs(new["R"] - old["R"]) > 1e-9 or new["exit_time"] != old["exit_time"]:
            out.append(_exc("RECORD_CHANGED", old["signal_time"], "a closed forward trade changed between runs"))
    return out


# ------------------------------------------------------------------ persistence (hash-chained, append-only)


class ConcurrentRun(RuntimeError):
    pass


class Store:
    FILES = ("runs", "reads", "exceptions", "events")

    def __init__(self, root: Path = OUT) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.jsonl"

    def _lines(self, name: str) -> list[str]:
        p = self._path(name)
        return [x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []

    def _rows(self, name: str) -> list[dict[str, Any]]:
        return [json.loads(x) for x in self._lines(name)]

    def _append(self, name: str, row: Mapping[str, Any]) -> None:
        lines = self._lines(name)
        prev = json.loads(lines[-1]).get("row_hash", GENESIS) if lines else GENESIS
        body = json.loads(json.dumps({**row, "prev_hash": prev}, sort_keys=True, default=str))
        body["row_hash"] = _sha(_canon(body))
        with self._path(name).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(body, sort_keys=True) + "\n")

    def verify(self) -> list[str]:
        """Every problem with the append-only chain: a torn line, an edit, a deletion, a reorder."""
        problems = []
        for name in self.FILES:
            p = self._path(name)
            if not p.exists():
                continue
            raw = p.read_text(encoding="utf-8")
            if raw and not raw.endswith("\n"):
                problems.append(f"{name}: last line is incomplete (torn write)")
            prev = GENESIS
            for n, line in enumerate(x for x in raw.splitlines() if x.strip()):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    problems.append(f"{name}:{n + 1}: not valid JSON")
                    break
                stored = row.pop("row_hash", None)
                if row.get("prev_hash") != prev:
                    problems.append(f"{name}:{n + 1}: chain broken (prev_hash)")
                if stored != _sha(_canon(row)):
                    problems.append(f"{name}:{n + 1}: row edited (row_hash)")
                prev = stored
        return problems

    def last_run(self) -> dict[str, Any] | None:
        runs = self._rows("runs")
        return runs[-1] if runs else None

    def add_run(self, row: Mapping[str, Any]) -> None:
        self._append("runs", row)

    def add_read(self, row: Mapping[str, Any]) -> None:
        self._append("reads", row)

    def reads(self) -> list[dict[str, Any]]:
        return self._rows("reads")

    def event(self, kind: str, **detail: Any) -> None:
        self._append("events", {"event": kind, "at": datetime.now(UTC).isoformat(), **detail})

    def add_exception(self, exc: Mapping[str, Any]) -> None:
        if exc["id"] not in {e["id"] for e in self._rows("exceptions")}:
            self._append("exceptions", {**exc, "status": "OPEN", "created_at": datetime.now(UTC).isoformat()})

    def resolve(self, exc_id: str, note: str, by: str) -> None:
        if not note.strip():
            raise ValueError("a resolution needs an explanation")
        self._append("exceptions", {"id": exc_id, "status": "RESOLVED", "note": note, "by": by,
                                    "resolved_at": datetime.now(UTC).isoformat()})

    def open_exceptions(self) -> list[dict[str, Any]]:
        state: dict[str, dict[str, Any]] = {}
        for row in self._rows("exceptions"):
            state[row["id"]] = {**state.get(row["id"], {}), **row}
        return [e for e in state.values() if e.get("status") != "RESOLVED"]

    # -- run lock: one runner at a time; a leftover lock means the last run crashed

    def acquire(self, now: datetime) -> None:
        lock = self.root / "runner.lock"
        if lock.exists():
            info = json.loads(lock.read_text(encoding="utf-8") or "{}")
            started = datetime.fromisoformat(info.get("started_at", now.isoformat()))
            if now - started < STALE_RUN_LOCK_AFTER:
                raise ConcurrentRun(f"another run holds {lock} since {started.isoformat()}")
            self.event("RUNNER_CRASH_RECOVERED", previous=info)
            self.add_exception(_exc("RUNNER_CRASH", info.get("started_at", "unknown"),
                                    f"previous run {info} never released its lock; check its records"))
        lock.write_text(json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "started_at": now.isoformat()}),
                        encoding="utf-8")

    def release(self) -> None:
        (self.root / "runner.lock").unlink(missing_ok=True)


def record_run(candles: Sequence[Mapping[str, Any]], funding: Sequence[Mapping[str, Any]], *, now: datetime,
               store: Store, forward_start: datetime = FORWARD_START,
               guard: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Record every candidate, trade, fee, slippage, funding payment, exit and regime label."""
    closed_bars = [c for c in candles if c["time"] + timedelta(hours=1) <= now]
    s = build_series(closed_bars, funding, forward_start=forward_start)
    fates, taken = walk_recorded(s, candidates(s), data_end=len(s.c))
    trades = [trade_record(s, t) for t in taken]
    days = day_hashes(s)
    exceptions = (missed_run_checks(store.last_run(), now=now, forward_start=forward_start)
                  + data_checks(s, funding, forward_start) + uptime_checks(closed_bars, funding, now=now,
                                                                          forward_start=forward_start)
                  + revision_checks(store.last_run(), days, trades))
    for e in exceptions:
        store.add_exception(e)
    run = {
        "contract_id": CONTRACT_ID,
        "run_at": datetime.now(UTC).isoformat(),
        "as_of": now.isoformat(),
        "forward_start": forward_start.isoformat(),
        "last_closed_bar": s.t[-1].isoformat() if s.t else None,
        "forward_bars": int(len(s.c) - s.eval_start),
        "fingerprints": {"code": code_fingerprint()["combined"], "config": config_fingerprint(),
                         "contract_doc": contract_doc_fingerprint(), "data": data_fingerprint(closed_bars, funding),
                         "lock_hash": (guard or {}).get("lock_hash")},
        "candidates": fates,
        "trades": trades,
        "closed_trades": [t for t in trades if t["outcome"] != "OPEN"],
        "day_hashes": days,
        "new_exceptions": exceptions,
    }
    store.add_run(run)
    return run


# ------------------------------------------------------------------ evaluation


class Refused(RuntimeError):
    def __init__(self, reason: str, **detail: Any) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def metrics(closed: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tr = sorted(closed, key=lambda t: (t["exit_time"], t["signal_time"]))
    R = [float(t["R"]) for t in tr]
    usd = [float(t["net_usd"]) for t in tr]
    pos, neg = sum(u for u in usd if u > 0), -sum(u for u in usd if u < 0)
    eq = pk = dd = 0.0
    for r in R:
        eq += r
        pk = max(pk, eq)
        dd = max(dd, pk - eq)
    by_trend: dict[str, float] = defaultdict(float)
    for t in tr:
        by_trend[t["trend"]] += float(t["R"])
    return {
        "closed_trades": len(tr),
        "expectancy_R": (sum(R) / len(R)) if R else None,
        "net_R": sum(R),
        "net_usd": sum(usd),
        "pf": (pos / neg) if neg > 0 else (float("inf") if pos > 0 else None),
        "max_dd_R": dd,
        "net_R_by_trend": {k: by_trend.get(k, 0.0) for k in TREND_REGIMES},
        "fees_usd": sum(float(t["fees_usd"]) for t in tr),
        "slippage_usd": sum(float(t["slippage_usd"]) for t in tr),
        "funding_usd": sum(float(t["funding_usd"]) for t in tr),
    }


def evaluate(closed: Sequence[Mapping[str, Any]], *, now: datetime, open_exceptions: Sequence[Mapping[str, Any]],
             drift_list: Sequence[str], data_fp: str, diagnostic: bool = False) -> dict[str, Any]:
    """Decision order: CONFIG_DRIFT > KILL > PASS_TO_PAPER_ELIGIBLE > KILL_INSUFFICIENT_EDGE > NO_PASS_CONTINUE."""
    n = len(closed)
    gates_met = n >= MIN_CLOSED_TRADES and now >= EARLIEST_EVALUATION
    late_and_short = now >= TWELVE_MONTHS and n < MIN_CLOSED_TRADES and not TWELVE_MONTH_EXCEPTION_PREAPPROVED
    continuation_limit = n >= CONTINUATION_MAX_TRADES or now >= CONTINUATION_MAX_TIME
    if not diagnostic and not gates_met and not late_and_short:
        raise Refused("GATES_NOT_MET", closed_trades=n, need=MIN_CLOSED_TRADES, now=now.isoformat(),
                      earliest=EARLIEST_EVALUATION.isoformat())
    m = metrics(closed)
    exp, pf, dd = m["expectancy_R"], m["pf"], m["max_dd_R"]
    trend = m["net_R_by_trend"]
    kills = []
    if late_and_short:
        kills.append("FEWER_THAN_40_TRADES_AFTER_12_MONTHS")
    if exp is not None and exp <= 0:
        kills.append("EXPECTANCY_NOT_POSITIVE")
    if pf is not None and pf < KILL_MIN_PF:
        kills.append("PF_BELOW_1")
    if dd > KILL_MAX_DD_R:
        kills.append("DRAWDOWN_ABOVE_15R")
    if trend["TREND_DOWN"] > 0 and trend["TREND_UP"] <= 0 and trend["RANGE"] <= 0:
        kills.append("PROFIT_ONLY_FROM_DOWNTREND")
    if open_exceptions:
        kills.append("UNRESOLVED_EXCEPTIONS")
    passes = {
        "min_trades_and_time": gates_met,
        "expectancy_above_0.10R": exp is not None and exp > PASS_MIN_EXPECTANCY_R,
        "pf_at_least_1.20": pf is not None and pf >= PASS_MIN_PF,
        "drawdown_at_most_15R": dd <= KILL_MAX_DD_R,
        "positive_in_two_trend_regimes": sum(1 for k in TREND_REGIMES if trend[k] > 0) >= 2,
        "no_unresolved_exceptions": not open_exceptions,
    }
    if drift_list:
        decision = "CONFIG_DRIFT_RESTART_REQUIRED"
    elif kills:
        decision = "KILL"
    elif all(passes.values()):
        decision = "PASS_TO_PAPER_ELIGIBLE"
    elif gates_met and continuation_limit:
        decision = "KILL_INSUFFICIENT_EDGE"
    elif gates_met:
        # Reached only with expectancy > 0, PF >= 1.0, DD <= 15R and no open exceptions (no kill fired).
        decision = "NO_PASS_CONTINUE"
    else:
        decision = "NOT_EVALUABLE_BEFORE_GATES"  # a diagnostic read before 40 trades and 9 months
    return {
        "contract_id": CONTRACT_ID,
        "evaluated_at": datetime.now(UTC).isoformat(),
        "as_of": now.isoformat(),
        "label": NON_ACCEPTANCE_DIAGNOSTIC if diagnostic else "FORWARD_CONTRACT_READ",
        "acceptance": (not diagnostic) and decision == "PASS_TO_PAPER_ELIGIBLE",
        "diagnostic": diagnostic,
        "gates_met": gates_met,
        "continuation_limit_reached": continuation_limit,
        "decision": decision,
        "note": GREY_ZONE_LANGUAGE if decision == "NO_PASS_CONTINUE" else None,
        "kills": kills,
        "passes": passes,
        "metrics": m,
        "drift": list(drift_list),
        "open_exceptions": len(open_exceptions),
        "fingerprints": {"code": code_fingerprint()["combined"], "config": config_fingerprint(),
                         "contract_doc": contract_doc_fingerprint(), "data": data_fp},
    }


# ------------------------------------------------------------------ data loading (read-only) and CLI


def load_forward_data(now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import _db  # read-only asyncpg connection
    from load_data import fetch_funding  # Binance public GET /fapi/v1/fundingRate

    candles = _db.load_candles(SYMBOL, "1h", SERIES_ANCHOR, now)
    funding = fetch_funding(SYMBOL, int(SERIES_ANCHOR.timestamp() * 1000), int(now.timestamp() * 1000))
    return candles, funding


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lcsc_forward")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("run")
    ev = sub.add_parser("evaluate")
    ev.add_argument("--diagnostic", action="store_true",
                    help="NON_ACCEPTANCE_DIAGNOSTIC read before the gates; logged, never a pass; cannot bypass drift")
    vr = sub.add_parser("verify-records")
    vr.add_argument("--path", default=None, help="a record store or a backup copy (default: forward_out)")
    sub.add_parser("freeze-contract")
    rf = sub.add_parser("refreeze")
    rf.add_argument("--new-version", required=True)
    rf.add_argument("--reason", required=True)
    rf.add_argument("--by", required=True)
    rs = sub.add_parser("resolve-exception")
    rs.add_argument("--id", required=True)
    rs.add_argument("--note", required=True)
    rs.add_argument("--by", required=True)
    args = ap.parse_args(argv)
    now = datetime.now(UTC)

    if args.cmd == "verify-records":
        target = Path(args.path) if args.path else OUT
        if not target.is_dir():
            print(f"REFUSED: {target} is not a directory")
            return 2
        problems = Store(target).verify()
        print(json.dumps({"path": str(target), "integrity": "OK" if not problems else "BROKEN", "problems": problems},
                         indent=1))
        return 0 if not problems else 4
    if args.cmd == "freeze-contract":
        try:
            lock = write_lock(LOCK_PATH)
        except FileExistsError as exc:
            print(f"REFUSED: {exc}")
            return 2
        print(json.dumps({"contract_id": lock["contract_id"], "lock_hash": lock["lock_hash"]}, indent=1))
        return 0
    if args.cmd == "refreeze":
        try:
            lock = refreeze(new_version=args.new_version, reason=args.reason, by=args.by, lock_path=LOCK_PATH)
        except (ValueError, FileExistsError) as exc:
            print(f"REFUSED: {exc}")
            return 2
        print(json.dumps({k: lock[k] for k in ("contract_id", "frozen_at", "config_fingerprint", "lock_hash",
                                              "supersedes", "refreeze")} | {"code": lock["code_fingerprint"]["combined"]},
                         indent=1))
        return 0

    # Every command that reads forward data or records goes through the guard first.
    try:
        guard = startup_guard(lock_path=LOCK_PATH)
    except DriftBlocked as exc:
        print(f"REFUSED: {exc.code} {exc.detail}")
        return 3
    store = Store()
    problems = store.verify()
    if problems:
        print(f"REFUSED: RECORD_INTEGRITY {problems}")
        return 4
    if args.cmd == "resolve-exception":
        store.resolve(args.id, args.note, args.by)
        return 0
    try:
        store.acquire(now)
    except ConcurrentRun as exc:
        print(f"REFUSED: CONCURRENT_RUN {exc}")
        return 5
    try:
        candles, funding = load_forward_data(now)
        if args.cmd == "status":
            closed = [c for c in candles if c["time"] + timedelta(hours=1) <= now]
            print(json.dumps({"guard": guard, "integrity": "OK", "last_closed_bar": closed[-1]["time"].isoformat(),
                              "last_settlement": datetime.fromtimestamp(funding[-1]["t"] / 1000, UTC).isoformat(),
                              "uptime": uptime_checks(closed, funding, now=now),
                              "open_exceptions": store.open_exceptions()}, indent=1, default=str))
            return 0
        run = record_run(candles, funding, now=now, store=store, guard=guard)
        if args.cmd == "run":
            print(json.dumps({k: run[k] for k in ("as_of", "last_closed_bar", "forward_bars", "fingerprints")}
                             | {"candidates": len(run["candidates"]), "trades": len(run["trades"]),
                                "new_exceptions": len(run["new_exceptions"])}, indent=1, default=str))
            return 0
        try:
            read = evaluate(run["closed_trades"], now=now, open_exceptions=store.open_exceptions(), drift_list=[],
                            data_fp=run["fingerprints"]["data"], diagnostic=args.diagnostic)
        except Refused as exc:
            print(f"REFUSED: {exc.reason} {exc.detail}")
            return 2
        store.add_read({**read, "lock_hash": guard["lock_hash"]})
        print(json.dumps(read, indent=1, default=str))
        return 0
    finally:
        store.release()


if __name__ == "__main__":
    raise SystemExit(main())
