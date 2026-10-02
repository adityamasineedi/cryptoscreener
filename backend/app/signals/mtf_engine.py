"""Multi-timeframe alignment — never hide conflicts."""

from __future__ import annotations

from typing import Any, Mapping

from app.signals.config import SignalConfig
from app.signals.schemas import MTFAlignment, TrendState


def align_mtf(
    trends_by_tf: Mapping[str, str | None],
    *,
    setup_bos: str | None = None,
    entry_trigger: str | None = None,
    config: SignalConfig | None = None,
) -> dict[str, Any]:
    cfg = config or SignalConfig()
    roles = {
        cfg.mtf_major: "major",
        cfg.mtf_primary: "primary",
        cfg.mtf_setup: "setup",
        cfg.mtf_entry: "entry",
    }
    normalized: dict[str, str] = {}
    waiting = []
    for tf, role in roles.items():
        raw = trends_by_tf.get(tf)
        if raw is None or raw in (TrendState.WAITING.value, "WAITING"):
            waiting.append(tf)
            normalized[tf] = TrendState.WAITING.value
        elif raw in (TrendState.INSUFFICIENT_DATA.value, "INSUFFICIENT_DATA"):
            normalized[tf] = TrendState.INSUFFICIENT_DATA.value
        else:
            normalized[tf] = str(raw).upper()

    major = normalized.get(cfg.mtf_major)
    primary = normalized.get(cfg.mtf_primary)
    setup = normalized.get(cfg.mtf_setup)
    entry = normalized.get(cfg.mtf_entry)

    # Conflict if major vs primary opposite
    if major in ("BULLISH", "BEARISH") and primary in ("BULLISH", "BEARISH") and major != primary:
        alignment = MTFAlignment.CONFLICT.value
        reason = f"{cfg.mtf_major.upper()} {major} vs {cfg.mtf_primary.upper()} {primary}"
    elif all(t == "BULLISH" for t in (major, primary, setup) if t not in (None, "WAITING", "INSUFFICIENT_DATA", "NEUTRAL")):
        if setup == "BULLISH" and (entry in ("BULLISH", "WAITING", "INSUFFICIENT_DATA", None) or entry_trigger):
            alignment = MTFAlignment.STRONG_LONG.value
            reason = "Higher TFs bullish with setup alignment"
        else:
            alignment = MTFAlignment.MIXED.value
            reason = "Partial bullish alignment"
    elif all(t == "BEARISH" for t in (major, primary, setup) if t not in (None, "WAITING", "INSUFFICIENT_DATA", "NEUTRAL")):
        if setup == "BEARISH" and (entry in ("BEARISH", "WAITING", "INSUFFICIENT_DATA", None) or entry_trigger):
            alignment = MTFAlignment.STRONG_SHORT.value
            reason = "Higher TFs bearish with setup alignment"
        else:
            alignment = MTFAlignment.MIXED.value
            reason = "Partial bearish alignment"
    elif waiting and not any(t in ("BULLISH", "BEARISH") for t in (major, primary)):
        alignment = MTFAlignment.WAITING.value
        reason = f"Waiting for OHLCV on: {', '.join(waiting)}"
    elif any(t in ("INSUFFICIENT_DATA",) for t in (major, primary, setup)):
        alignment = MTFAlignment.INSUFFICIENT.value
        reason = "Insufficient confirmed swings on one or more TFs"
    else:
        alignment = MTFAlignment.MIXED.value
        reason = "Mixed / incomplete MTF picture"

    return {
        "MTF_ALIGNMENT": alignment,
        "trends": {
            cfg.mtf_major: major,
            cfg.mtf_primary: primary,
            cfg.mtf_setup: setup,
            cfg.mtf_entry: entry,
        },
        "roles": roles,
        "setup_bos": setup_bos,
        "entry_trigger": entry_trigger,
        "waiting_timeframes": waiting,
        "reason": reason,
    }
