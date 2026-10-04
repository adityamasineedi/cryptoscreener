"""AS-OF shadow backtest comparison — never forces equality via strategy edits."""

from __future__ import annotations

from typing import Any, Mapping

from app.research.live_backtest_parity.constants import (
    LIVE_BACKTEST_MATCH,
    LIVE_BACKTEST_MISMATCH,
)


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm_status(v: Any) -> str:
    return str(v or "").upper()


def _norm_dir(v: Any) -> str:
    return str(v or "").upper()


def _near(a: float | None, b: float | None, tol: float = 1e-8) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if a == 0 and b == 0:
        return True
    return abs(float(a) - float(b)) <= max(tol, abs(float(a)) * 1e-9)


def extract_setup_fields(result: Mapping[str, Any] | None) -> dict[str, Any]:
    r = dict(result or {})
    bos = r.get("bos") or (r.get("tf_analysis") or {}).get("bos") or {}
    if not isinstance(bos, Mapping):
        bos = {}
    choch = r.get("choch") or (r.get("tf_analysis") or {}).get("choch") or {}
    if not isinstance(choch, Mapping):
        choch = {}
    trend = r.get("trend") or (r.get("tf_analysis") or {}).get("trend") or {}
    if not isinstance(trend, Mapping):
        trend = {}
    return {
        "status": _norm_status(r.get("status")),
        "direction": _norm_dir(r.get("direction")),
        "setup": str(trend.get("trend") or r.get("setup") or "") or None,
        "bos": str(bos.get("direction") or bos.get("state") or r.get("bos") or "")
        or None,
        "choch": str(choch.get("state") or choch.get("direction") or "") or None,
        "entry": _f(r.get("entry_price")),
        "sl": _f(r.get("stop_price")),
        "tp": _f(r.get("tp1")),
        "rr": _f(r.get("rr")),
        "symbol": str(r.get("symbol") or "") or None,
        "timeframe": str(r.get("timeframe") or "") or None,
        "combination_id": str(r.get("combination_id") or "") or None,
    }


def compare_live_vs_asof(
    *,
    symbol: str,
    timeframe: str,
    timestamp: str | None,
    live_result: Mapping[str, Any] | None,
    asof_result: Mapping[str, Any] | None,
    price_tol: float = 1e-8,
) -> dict[str, Any]:
    """Compare live candidate vs historical AS-OF result on decision fields."""
    live = extract_setup_fields(live_result)
    bt = extract_setup_fields(asof_result)

    reasons: list[str] = []
    # Candidate presence: both entry candidates or both non-candidates.
    live_is_entry = live["status"] in (
        "LONG_ENTRY_CANDIDATE",
        "SHORT_ENTRY_CANDIDATE",
    )
    bt_is_entry = bt["status"] in (
        "LONG_ENTRY_CANDIDATE",
        "SHORT_ENTRY_CANDIDATE",
    )
    if live_is_entry != bt_is_entry:
        reasons.append(
            f"status_presence live={live['status']} backtest={bt['status']}"
        )
    elif live_is_entry and bt_is_entry:
        if live["direction"] != bt["direction"]:
            reasons.append(
                f"direction live={live['direction']} backtest={bt['direction']}"
            )
        if not _near(live["entry"], bt["entry"], price_tol):
            reasons.append(
                f"entry live={live['entry']} backtest={bt['entry']}"
            )
        if not _near(live["sl"], bt["sl"], price_tol):
            reasons.append(f"sl live={live['sl']} backtest={bt['sl']}")
        if not _near(live["tp"], bt["tp"], price_tol):
            reasons.append(f"tp live={live['tp']} backtest={bt['tp']}")
        if live["rr"] is not None and bt["rr"] is not None:
            if not _near(live["rr"], bt["rr"], 1e-6):
                reasons.append(f"rr live={live['rr']} backtest={bt['rr']}")
        if live["bos"] and bt["bos"] and live["bos"] != bt["bos"]:
            reasons.append(f"bos live={live['bos']} backtest={bt['bos']}")
        if live["setup"] and bt["setup"] and live["setup"] != bt["setup"]:
            reasons.append(f"setup live={live['setup']} backtest={bt['setup']}")

    match = not reasons
    return {
        "parity_result": LIVE_BACKTEST_MATCH if match else LIVE_BACKTEST_MISMATCH,
        "match": match,
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": timestamp,
        "live_state": live["status"],
        "backtest_state": bt["status"],
        "live_direction": live["direction"] or None,
        "backtest_direction": bt["direction"] or None,
        "live_entry": live["entry"],
        "backtest_entry": bt["entry"],
        "live_sl": live["sl"],
        "backtest_sl": bt["sl"],
        "live_tp": live["tp"],
        "backtest_tp": bt["tp"],
        "live_rr": live["rr"],
        "backtest_rr": bt["rr"],
        "live_bos": live["bos"],
        "backtest_bos": bt["bos"],
        "live_setup": live["setup"],
        "backtest_setup": bt["setup"],
        "live_choch": live["choch"],
        "backtest_choch": bt["choch"],
        "reason": "; ".join(reasons) if reasons else None,
    }
