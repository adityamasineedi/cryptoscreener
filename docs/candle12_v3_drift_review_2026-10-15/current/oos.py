"""OOS evaluation for CANDLE12_V3_EXEC_FIX under the frozen contract.

The runner refuses to evaluate before the earliest evaluation time, and below
30 closed trades, unless ``diagnostic=True``. A diagnostic read is labelled
NON_ACCEPTANCE_DIAGNOSTIC, can never be OOS_PASS, and is logged like any read.
Every read stores the code, config and data fingerprints and its timestamp.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research import candle12_v3 as v3
from app.research.candle12_v3_paper import contract as C
from app.research.candle12_v3_paper.store import OOS_STORE_MODE
from app.signals._candle_utils import candle_time

NON_ACCEPTANCE_DIAGNOSTIC = "NON_ACCEPTANCE_DIAGNOSTIC"
# First failing class wins.
PRECEDENCE = ("CONFIG_DRIFT", "EXECUTION_MISMATCH", "INSUFFICIENT_SAMPLE",
              "OOS_FAIL_EDGE", "OOS_FAIL_ROBUSTNESS", "OOS_FAIL_RISK")


class OOSRefused(RuntimeError):
    def __init__(self, reason: str, **detail: Any) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _closed_oos_trades(result: Mapping[str, Any], now: datetime) -> list[dict[str, Any]]:
    trades = [t for t in result["trades"] if t["outcome"] != "OPEN"
              and datetime.fromisoformat(t["entry_time"]) >= C.OOS_START
              and datetime.fromisoformat(t["exit_time"]) <= now]
    return sorted(trades, key=lambda t: (t["exit_time"], t["entry_time"]))


def _primary_net(t: Mapping[str, Any]) -> float:
    """Funding counts only when the trade's whole holding period has settlement data."""
    funding = float(t.get("funding_usd") or 0.0)
    keep = funding if t.get("funding_coverage") == "FULL" else 0.0
    return float(t["net_pnl_usd"]) - funding + keep


def metrics(trades: Sequence[Mapping[str, Any]], risk_usd: float) -> dict[str, Any]:
    nets = [_primary_net(t) for t in trades]
    eq = peak = dd = 0.0
    for p in nets:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    wins = sum(p for p in nets if p > 0)
    losses = -sum(p for p in nets if p < 0)
    return {
        "closed_trades": len(nets),
        "net_pnl_usd": sum(nets),
        "profit_factor": (wins / losses) if losses > 0 else (float("inf") if wins > 0 else None),
        "max_drawdown_usd": dd,
        "max_drawdown_r": dd / risk_usd,
        "funding_included_usd": sum(float(t.get("funding_usd") or 0.0) for t in trades if t.get("funding_coverage") == "FULL"),
        "funding_excluded_trades": sum(1 for t in trades if t.get("funding_coverage") != "FULL"),
    }


def _oos_index(candles: Sequence[Mapping[str, Any]]) -> int:
    idx = next((i for i, c in enumerate(candles) if candle_time(c) >= C.OOS_START), None)
    if idx is None:
        raise OOSRefused("NO_OOS_BARS")
    if idx < C.WARMUP_BARS:
        raise OOSRefused("WARMUP_SHORT", have=idx, need=C.WARMUP_BARS)
    return idx


def evaluate_oos(
    *,
    candles: Sequence[Mapping[str, Any]],
    funding_rows: Sequence[Mapping[str, Any]] | None,
    now: datetime,
    lock: Mapping[str, Any],
    open_exceptions: Sequence[Mapping[str, Any]],
    store_mode: str,
    root: Path = C.REPO_ROOT,
    diagnostic: bool = False,
    read_log: Path | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if store_mode != OOS_STORE_MODE:
        raise OOSRefused("NOT_A_PAPER_STORE", store_mode=store_mode,
                         detail="only virtual_paper_mainnet stores count toward OOS; smoke, dry-run, "
                                "testnet_broker_check and legacy PAPER (mainnet data + testnet fills) stores never do")
    if manifest is not None and not manifest.get("counts_toward_oos"):
        raise OOSRefused("NOT_OOS_ELIGIBLE", detail="the store was not created OOS-eligible (drift guard or --allow-non-oos)")
    earliest = C.earliest_evaluation()
    too_early = now < earliest
    if too_early and not diagnostic:
        raise OOSRefused("TOO_EARLY", now=now.isoformat(), earliest=earliest.isoformat())
    start = _oos_index(candles)
    candles = [c for c in candles if candle_time(c) <= now]
    funding = list(funding_rows or [])
    primary_cfg = C.contract_config(C.PRIMARY_SLIPPAGE)
    stress_cfg = C.contract_config(C.STRESS_SLIPPAGE)
    if not funding:
        primary_cfg.funding_enabled = stress_cfg.funding_enabled = False
    primary = v3.run_candle12_v3(C.SYMBOL, C.TIMEFRAME, candles, config=primary_cfg, index_start=start,
                                 funding=funding or None)
    closed = _closed_oos_trades(primary, now)
    if len(closed) < C.MIN_CLOSED_TRADES and not diagnostic:
        raise OOSRefused("BELOW_MIN_TRADES", closed_trades=len(closed), need=C.MIN_CLOSED_TRADES)
    stress = v3.run_candle12_v3(C.SYMBOL, C.TIMEFRAME, candles, config=stress_cfg, index_start=start,
                                funding=funding or None)
    m_primary = metrics(closed, primary_cfg.risk_usd)
    m_stress = metrics(_closed_oos_trades(stress, now), stress_cfg.risk_usd)

    failures: list[str] = []
    drift = C.drift(lock, root)
    if drift:
        failures.append("CONFIG_DRIFT")
    if open_exceptions:
        failures.append("EXECUTION_MISMATCH")
    if len(closed) < C.MIN_CLOSED_TRADES:
        failures.append("INSUFFICIENT_SAMPLE")
    pf = m_primary["profit_factor"]
    if m_primary["net_pnl_usd"] <= 0 or pf is None or pf < C.MIN_PROFIT_FACTOR:
        failures.append("OOS_FAIL_EDGE")
    if m_stress["net_pnl_usd"] <= 0:
        failures.append("OOS_FAIL_ROBUSTNESS")
    if m_primary["max_drawdown_r"] > C.MAX_DRAWDOWN_R:
        failures.append("OOS_FAIL_RISK")
    failures = [f for f in PRECEDENCE if f in failures]
    # A diagnostic read's classification is information only; acceptance stays False.
    classification = failures[0] if failures else "OOS_PASS"

    read = {
        "contract_id": C.CONTRACT_ID,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": now.isoformat(),
        "label": NON_ACCEPTANCE_DIAGNOSTIC if diagnostic else "OOS_ACCEPTANCE_READ",
        "acceptance": (not diagnostic) and classification == "OOS_PASS",
        "diagnostic": diagnostic,
        "too_early": too_early,
        "classification": classification,
        "failures": failures,
        "config_drift": drift,
        "open_exceptions": len(open_exceptions),
        "primary": m_primary,
        "stress": m_stress,
        "fingerprints": {
            "code": C.code_fingerprint(root)["combined"],
            "config": C.config_fingerprint(),
            "data": C.data_fingerprint(candles),
            "lock_code": lock.get("code_fingerprint", {}).get("combined"),
            "lock_config": lock.get("config_fingerprint"),
        },
        "window": {"oos_start": C.OOS_START.isoformat(), "first_bar": candle_time(candles[0]).isoformat(),
                   "last_bar": candle_time(candles[-1]).isoformat()},
    }
    if read_log is not None:
        read_log.parent.mkdir(parents=True, exist_ok=True)
        with read_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(read, sort_keys=True, default=str) + "\n")
    return read
