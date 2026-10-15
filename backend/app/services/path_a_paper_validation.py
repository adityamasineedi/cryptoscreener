"""Fail-closed Path A paper entry check against the closed-candle evaluator.

Paper must not open from a cached setup payload. The closed-candle
SignalEngine result is the only source of entry, stop, and target. A
disagreement rejects the entry. It does not replace the payload prices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.signals.schemas import SignalStatus

logger = get_logger("path_a_paper_validation")

_ENTRY_OK = frozenset(
    {
        SignalStatus.LONG_ENTRY_CANDIDATE.value,
        SignalStatus.ENTRY_CANDIDATE.value,
    }
)


@dataclass
class PathAGateResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


def analyze_closed_path_a(symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Re-run the live setup evaluator on closed candles only.

    ``payload`` is unused by the production evaluator. Tests replace this
    function with a fixture. A failure is a rejection, not a payload fallback.
    """
    del payload
    sym = str(symbol or "").upper()
    try:
        from app.services.ohlcv_store import ohlcv_store
        from app.services.setup_signals import get_setup_signal_service

        result = get_setup_signal_service().analyze_symbol(sym)
    except Exception as exc:  # noqa: BLE001
        logger.warning("path_a_closed_eval_failed", symbol=sym, error=str(exc))
        return {
            "status": SignalStatus.WAITING.value,
            "data_status": "CLOSED_EVAL_FAILED",
            "reason": str(exc),
        }
    if not isinstance(result, dict):
        return {
            "status": SignalStatus.WAITING.value,
            "data_status": "CLOSED_EVAL_FAILED",
        }
    out = dict(result)
    try:
        from app.services.ohlcv_store import ohlcv_store

        candles = ohlcv_store.get_candles_for_engine(sym, "15m", include_open=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("path_a_closed_window_failed", symbol=sym, error=str(exc))
        candles = []
    highs: list[float] = []
    lows: list[float] = []
    for candle in candles or []:
        high = _candle_field(candle, "high")
        low = _candle_field(candle, "low")
        if high is not None:
            highs.append(high)
        if low is not None:
            lows.append(low)
    if highs:
        out["window_high"] = max(highs)
    if lows:
        out["window_low"] = min(lows)
    if not candles:
        deps = out.get("data_dependencies") or {}
        if not isinstance(deps, dict):
            deps = {}
        deps.setdefault("15m", "WAITING FOR OHLCV")
        out["data_dependencies"] = deps
    return out


def validate_path_a_paper_entry(
    payload: dict[str, Any] | None,
    closed: dict[str, Any] | None,
) -> PathAGateResult:
    """Return every failed gate. ``ok`` is true only when none fired."""
    payload = payload or {}
    reasons: list[str] = []
    closed = closed or {}

    data_status = str(closed.get("data_status") or "").upper()
    deps = closed.get("data_dependencies") or {}
    setup_dep = ""
    if isinstance(deps, dict):
        setup_dep = str(deps.get("15m") or deps.get(payload.get("timeframe") or "") or "")
    if not closed:
        reasons.append("closed_candles_unavailable")
    elif data_status in {"CLOSED_EVAL_FAILED", "NO_CLOSED_CANDLES"}:
        reasons.append("closed_candles_unavailable")
    elif "WAITING FOR OHLCV" in setup_dep.upper():
        reasons.append("closed_candles_unavailable")

    closed_status = str(closed.get("status") or "")
    payload_status = str(payload.get("status") or "")
    if closed and closed_status not in _ENTRY_OK:
        reasons.append(f"closed_eval_{closed_status or 'missing'}")
    if payload_status and closed_status and payload_status != closed_status:
        reasons.append("status_mismatch")

    direction = str(closed.get("direction") or "").upper()
    if closed and direction != "LONG":
        reasons.append("direction_not_long")

    bos = closed.get("bos") or {}
    if not isinstance(bos, dict):
        bos = {}
    bos_ok = (
        str(bos.get("state") or "").upper() == "CONFIRMED"
        and str(bos.get("direction") or "").upper() == "BULLISH_BOS"
    )
    if closed and not bos_ok:
        reasons.append("bos_not_confirmed")

    setup_tf = str(payload.get("timeframe") or closed.get("timeframe") or "15m")
    setup_trend = _trend_label(closed, setup_tf)
    if closed and setup_trend != "BULLISH":
        reasons.append("setup_trend_not_bullish")

    trend_4h = _trend_label(closed, "4h")
    trend_1h = _trend_label(closed, "1h")
    if closed and not (trend_4h == "BULLISH" and trend_1h == "BULLISH"):
        reasons.append("htf_not_bullish")
    alignment = str(((closed.get("mtf") or {}) if isinstance(closed.get("mtf"), dict) else {}).get("MTF_ALIGNMENT") or "").upper()
    if closed and alignment != "STRONG_LONG":
        reasons.append("htf_not_strong_long")
    payload_alignment = str(
        ((payload.get("mtf") or {}) if isinstance(payload.get("mtf"), dict) else {}).get("MTF_ALIGNMENT") or ""
    ).upper()
    if payload_alignment and alignment and payload_alignment != alignment:
        reasons.append("htf_label_stale")

    impulse = closed.get("impulse") or {}
    if not isinstance(impulse, dict):
        impulse = {}
    if closed and not impulse.get("is_impulse"):
        reasons.append("impulse_fail")

    pullback = closed.get("pullback") or {}
    if not isinstance(pullback, dict):
        pullback = {}
    pullback_state = str(pullback.get("pullback_state") or "").upper()
    if closed and pullback_state not in {"ACTIVE", "CONFIRMED"}:
        reasons.append("pullback_not_ready")

    stop = closed.get("stop") or {}
    if not isinstance(stop, dict):
        stop = {}
    final_stop = _num(stop.get("final_stop"))
    stop_reason = str(stop.get("reason") or "")
    structural_stop = (
        final_stop is not None
        and not bool(stop.get("provisional"))
        and "no structural" not in stop_reason.lower()
    )
    if closed and not structural_stop:
        reasons.append("no_structural_stop")
    if closed and bool(stop.get("provisional")):
        reasons.append("provisional_stop")

    targets = closed.get("targets") or []
    target = _target_price(targets)
    target_type = ""
    if targets and isinstance(targets[0], dict):
        target_type = str(targets[0].get("target_type") or "")
    if closed and target is None:
        reasons.append("no_in_window_target")

    entry = _num(((closed.get("entry") or {}) if isinstance(closed.get("entry"), dict) else {}).get("entry_price"))
    if closed and entry is None:
        reasons.append("no_entry_price")
    if entry is not None and final_stop is not None and final_stop >= entry:
        reasons.append("stop_not_below_entry")

    window_high = _num(closed.get("window_high"))
    if (
        target is not None
        and window_high is not None
        and target > window_high + 1e-9
        and target_type != "r_multiple"
    ):
        reasons.append("target_outside_window")

    payload_stop = _num(((payload.get("stop") or {}) if isinstance(payload.get("stop"), dict) else {}).get("final_stop"))
    if payload_stop is None:
        payload_stop = _num(((payload.get("risk_management") or {}) if isinstance(payload.get("risk_management"), dict) else {}).get("stop"))
    payload_target = _target_price(payload.get("targets") or [])
    payload_entry = _num(((payload.get("entry") or {}) if isinstance(payload.get("entry"), dict) else {}).get("entry_price"))
    if payload_entry is None:
        payload_entry = _num(((payload.get("risk_management") or {}) if isinstance(payload.get("risk_management"), dict) else {}).get("entry"))

    if payload_stop is not None and final_stop is None:
        reasons.append("payload_stop_not_structural")
    elif payload_stop is not None and final_stop is not None and not _same(payload_stop, final_stop):
        reasons.append("stop_mismatch")
    if payload_target is not None and target is None:
        reasons.append("payload_target_not_in_closed_eval")
    elif payload_target is not None and target is not None and not _same(payload_target, target):
        reasons.append("target_mismatch")
    if payload_entry is not None and entry is not None and not _same(payload_entry, entry):
        reasons.append("entry_mismatch")

    diagnostics = {
        "reasons": list(reasons),
        "payload_status": payload_status,
        "closed_status": closed_status,
        "closed_direction": direction,
        "closed_4h": trend_4h,
        "closed_1h": trend_1h,
        "closed_alignment": alignment,
        "payload_alignment": payload_alignment,
        "impulse_reason": impulse.get("reason"),
        "pullback_state": pullback_state,
        "pullback_reason": pullback.get("reason"),
        "stop_reason": stop_reason,
        "provisional_stop": bool(stop.get("provisional")),
        "closed_entry": entry,
        "closed_stop": final_stop,
        "closed_target": target,
        "closed_target_type": target_type,
        "payload_entry": payload_entry,
        "payload_stop": payload_stop,
        "payload_target": payload_target,
        "window_high": window_high,
    }
    ok = not reasons
    if not ok:
        logger.info("path_a_paper_gate_rejected", reasons=reasons, diagnostics=diagnostics)
    return PathAGateResult(
        ok=ok,
        reasons=reasons,
        entry=entry if ok else None,
        stop=final_stop if ok else None,
        target=target if ok else None,
        diagnostics=diagnostics,
    )


def _trend_label(payload: dict[str, Any], timeframe: str) -> str:
    trend = payload.get("trend") or {}
    if not isinstance(trend, dict):
        return str(trend or "").upper()
    raw = trend.get(timeframe) or trend.get(timeframe.lower())
    if isinstance(raw, dict):
        return str(raw.get("trend") or "").upper()
    if raw:
        return str(raw).upper()
    if timeframe in {"15m", "setup"} and trend.get("trend") and not isinstance(trend.get("trend"), dict):
        return str(trend.get("trend") or "").upper()
    return ""


def _target_price(targets: Any) -> float | None:
    if not targets or not isinstance(targets, list):
        return None
    first = targets[0]
    if not isinstance(first, dict):
        return None
    return _num(first.get("target_price"))


def _candle_field(candle: Any, name: str) -> float | None:
    if isinstance(candle, dict):
        return _num(candle.get(name))
    return _num(getattr(candle, name, None))


def _num(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:
        return None
    return value


def _same(left: float, right: float) -> bool:
    return abs(left - right) <= 1e-6 * max(1.0, abs(left), abs(right))
