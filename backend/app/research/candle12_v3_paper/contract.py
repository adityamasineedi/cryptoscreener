"""Frozen out-of-sample contract for CANDLE12_V3_EXEC_FIX (docs/candle12_v3_oos_contract.md).

The values here are the contract. The fingerprints of the code they govern are
stored in docs/candle12_v3_oos_contract.lock.json (written by ``freeze-contract``)
so this file can be checked like any other.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research.candle12_v3 import ENGINE_VERSION, STRATEGY_ID, Candle12V3Config
from app.signals._candle_utils import candle_time

UTC = timezone.utc
REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND = REPO_ROOT / "backend"
LOCK_PATH = REPO_ROOT / "docs" / "candle12_v3_oos_contract.lock.json"
CONTRACT_DOC = REPO_ROOT / "docs" / "candle12_v3_oos_contract.md"

CONTRACT_ID = "CANDLE12_V3_EXEC_FIX/OOS-2"
# OOS-1 was frozen 2026-10-15 15:00:23 UTC and then drifted (ops layer added after the
# freeze). Its lock and contract text are kept byte-for-byte in the drift review
# (commit fd2de20); OOS-2 supersedes it with the same window and thresholds.
SUPERSEDES = {
    "contract_id": "CANDLE12_V3_EXEC_FIX/OOS-1",
    "status": "SUPERSEDED",
    "frozen_at": "2026-10-15T15:00:23+00:00",
    "lock_path": "docs/candle12_v3_drift_review_2026-10-15/candle12_v3_oos_contract.lock.OOS-1.json",
    "lock_sha256": "82f54fc792027d4aad23c7a7e4db40c51ce5061ec3d0735273c19a1cdf57e599",
    "contract_doc_path": "docs/candle12_v3_drift_review_2026-10-15/candle12_v3_oos_contract.OOS-1.md",
    "contract_doc_sha256": "09ac3c85d39d585736776bea7e52a7b1b95675225dd597d62649a61500c3088a",
    "drift_review_commit": "fd2de20532c2581d628ddd698a396b8048620df5",
    "reason": "CONFIG_DRIFT after freeze: execution modes, restart recovery, Binance algo endpoints, backfill, "
              "freeze/drift guard and safety were added to the package; no strategy, sizing, fee, slippage, "
              "threshold or date change (DRIFT_REVIEW.md)",
}
SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"
BOOK_SIDE = "LONG"
WARMUP_BARS = 1000
ALLOWED_SYMBOLS = (SYMBOL,)
OOS_MODE = "virtual_paper_mainnet"  # the only execution mode whose store can count toward OOS

OOS_START = datetime(2026, 10, 16, tzinfo=UTC)
EARLIEST_EVALUATION = datetime(2027, 10, 15, tzinfo=UTC)
MIN_ELAPSED_MONTHS = 12
MIN_CLOSED_TRADES = 30
MIN_PROFIT_FACTOR = 1.30
MAX_DRAWDOWN_R = 17.0  # on intended $20 risk = $340
PRIMARY_SLIPPAGE = 0.0002
STRESS_SLIPPAGE = 0.0005

CLASSIFICATIONS = (
    "OOS_PASS",
    "OOS_FAIL_EDGE",
    "OOS_FAIL_ROBUSTNESS",
    "OOS_FAIL_RISK",
    "INSUFFICIENT_SAMPLE",
    "EXECUTION_MISMATCH",
    "CONFIG_DRIFT",
)

# Strategy, execution, data-handling and contract code. A change to any of these
# is a new version and restarts the OOS window.
CODE_FILES = (
    "backend/app/research/candle12_v3.py",
    "backend/app/research/candle12_v2.py",
    "backend/app/research/rolling_structure.py",
    "backend/app/research/combination_backtest.py",
    "backend/app/research/config.py",
    "backend/app/signals/_candle_utils.py",
    "backend/app/signals/bos_engine.py",
    "backend/app/signals/impulse_engine.py",
    "backend/app/signals/pullback_engine.py",
    "backend/app/signals/trend_engine.py",
    "backend/app/signals/swing_detector.py",
    "backend/app/signals/stop_engine.py",
    "backend/app/signals/target_engine.py",
    "backend/app/signals/config.py",
    "backend/app/signals/schemas.py",
    "backend/app/engines/mtf/indicators.py",
    "backend/app/services/paper_sizing.py",
    "backend/app/research/candle12_v3_paper/contract.py",
    "backend/app/research/candle12_v3_paper/tracker.py",
    "backend/app/research/candle12_v3_paper/broker.py",
    "backend/app/research/candle12_v3_paper/binance.py",
    "backend/app/research/candle12_v3_paper/adapter.py",
    "backend/app/research/candle12_v3_paper/reconcile.py",
    "backend/app/research/candle12_v3_paper/feeds.py",
    "backend/app/research/candle12_v3_paper/oos.py",
    "backend/app/research/candle12_v3_paper/store.py",
    "backend/app/research/candle12_v3_paper/cli.py",
    "backend/app/research/candle12_v3_paper/capabilities.py",
    "backend/app/research/candle12_v3_paper/broker_capabilities.json",
    "backend/app/research/candle12_v3_paper/modes.py",
    "backend/app/research/candle12_v3_paper/recovery.py",
    "backend/app/research/candle12_v3_paper/safety.py",
    "backend/app/research/candle12_v3_paper/guard.py",
    "backend/app/research/candle12_v3_paper/backfill.py",
    "backend/app/research/candle12_v3_paper/freeze.py",
    "backend/app/research/candle12_v3_paper/preflight.py",
    "backend/scripts/strategy_rank/multicap/_db.py",
)


def add_months(when: datetime, months: int) -> datetime:
    month = when.month - 1 + months
    return when.replace(year=when.year + month // 12, month=month % 12 + 1)


def earliest_evaluation() -> datetime:
    """Both rules must hold: the stated date and 12 months after the start."""
    return max(EARLIEST_EVALUATION, add_months(OOS_START, MIN_ELAPSED_MONTHS))


def contract_config(slippage: float = PRIMARY_SLIPPAGE) -> Candle12V3Config:
    """The accepted book. Only the slippage differs between primary and stress runs."""
    return Candle12V3Config(
        book_side=BOOK_SIDE,
        portfolio_lock_mode="per_side",
        bos_refire_policy="once_per_level",
        target_policy="nearest_target_full_exit",
        entry_slippage_mode="inclusive",
        entry_slippage=slippage,
        exit_slippage=slippage,
        taker_fee=0.0004,
        risk_usd=20.0,
        account_equity=1000.0,
        leverage=2.0,
        apply_exchange_filters=True,
        risk_tolerance_pct=0.10,
        funding_enabled=True,
    )


def contract_terms() -> dict[str, Any]:
    return {
        "contract_id": CONTRACT_ID,
        "supersedes": SUPERSEDES.get("contract_id"),
        "strategy_id": STRATEGY_ID,
        "engine_version": ENGINE_VERSION,
        "symbol": SYMBOL,
        "allowed_symbols": list(ALLOWED_SYMBOLS),
        "oos_mode": OOS_MODE,
        "timeframe": TIMEFRAME,
        "warmup_bars": WARMUP_BARS,
        "oos_start": OOS_START.isoformat(),
        "earliest_evaluation": earliest_evaluation().isoformat(),
        "min_closed_trades": MIN_CLOSED_TRADES,
        "min_profit_factor": MIN_PROFIT_FACTOR,
        "max_drawdown_r": MAX_DRAWDOWN_R,
        "primary_slippage": PRIMARY_SLIPPAGE,
        "stress_slippage": STRESS_SLIPPAGE,
        "config": contract_config().to_dict(),
    }


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def config_fingerprint() -> str:
    return _sha(json.dumps(contract_terms(), sort_keys=True, default=str).encode())


def code_fingerprint(root: Path = REPO_ROOT, files: Sequence[str] = CODE_FILES) -> dict[str, Any]:
    per_file = {}
    for rel in files:
        path = root / rel
        per_file[rel] = _sha(path.read_bytes()) if path.exists() else "MISSING"
    combined = _sha(json.dumps(per_file, sort_keys=True).encode())
    return {"combined": combined, "files": per_file}


def contract_fingerprint(path: Path = CONTRACT_DOC) -> str:
    """sha256 of the contract document."""
    return _sha(path.read_bytes()) if path.exists() else "MISSING"


def lock_hash(lock: Mapping[str, Any]) -> str:
    """sha256 of the lock's canonical JSON without its own hash field."""
    body = {k: v for k, v in lock.items() if k != "lock_hash"}
    return _sha(json.dumps(body, sort_keys=True, default=str).encode())


def data_fingerprint(candles: Sequence[Mapping[str, Any]]) -> str:
    h = hashlib.sha256()
    for c in candles:
        t = candle_time(c)
        h.update(
            f"{t.isoformat() if t else ''}|{c['open']!r}|{c['high']!r}|{c['low']!r}|{c['close']!r}|{c.get('volume')!r}\n".encode()
        )
    return h.hexdigest()


def build_lock(root: Path = REPO_ROOT) -> dict[str, Any]:
    return {
        "contract_id": CONTRACT_ID,
        "frozen_at": datetime.now(UTC).isoformat(),
        "terms": contract_terms(),
        "config_fingerprint": config_fingerprint(),
        "code_fingerprint": code_fingerprint(root),
    }


def write_lock(path: Path = LOCK_PATH, root: Path = REPO_ROOT) -> dict[str, Any]:
    lock = build_lock(root)
    path.write_text(json.dumps(lock, indent=1, sort_keys=True), encoding="utf-8")
    return lock


def load_lock(path: Path = LOCK_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def drift(lock: Mapping[str, Any], root: Path = REPO_ROOT) -> list[str]:
    """What changed since the contract was frozen. Empty means no drift."""
    out = []
    if lock.get("config_fingerprint") != config_fingerprint():
        out.append("config_fingerprint")
    now = code_fingerprint(root, tuple(lock.get("code_fingerprint", {}).get("files", {}).keys()) or CODE_FILES)
    frozen = lock.get("code_fingerprint", {}).get("files", {})
    for rel, sha in frozen.items():
        if now["files"].get(rel) != sha:
            out.append(f"code:{rel}")
    if set(CODE_FILES) != set(frozen):
        out.append("code:file_list")
    return out
