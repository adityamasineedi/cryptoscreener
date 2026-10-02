"""Explicit rule engine — ENTRY_CANDIDATE states only, never BUY/SELL commands."""

from __future__ import annotations

from typing import Any

from app.signals.config import SignalConfig
from app.signals.schemas import ConditionCheck, ConditionVerdict, EntryType, SignalStatus


def evaluate_entry(
    *,
    mtf: dict[str, Any],
    setup_trend: dict[str, Any],
    bos: dict[str, Any] | None,
    impulse: dict[str, Any] | None,
    pullback: dict[str, Any] | None,
    retest: dict[str, Any] | None,
    stop: dict[str, Any] | None,
    targets: list[dict[str, Any]],
    risk_reward: dict[str, Any] | None,
    config: SignalConfig,
    last_close: float | None,
    volume_ok: bool | None = None,
    oi_available: bool | None = None,
    liquidation_available: bool | None = None,
) -> dict[str, Any]:
    conditions: list[ConditionCheck] = []

    def add(cid: str, label: str, verdict: str, detail: str = "") -> None:
        conditions.append(ConditionCheck(cid, label, verdict, detail))

    align = str(mtf.get("MTF_ALIGNMENT") or "")
    trends = mtf.get("trends") or {}
    ht_bull = trends.get(config.mtf_major) == "BULLISH" and trends.get(config.mtf_primary) == "BULLISH"
    ht_bear = trends.get(config.mtf_major) == "BEARISH" and trends.get(config.mtf_primary) == "BEARISH"

    if align == "CONFLICT":
        add("mtf", "MTF", ConditionVerdict.FAIL.value, mtf.get("reason", "Conflict"))
    elif align == "WAITING":
        add("mtf", "MTF", ConditionVerdict.WAITING.value, mtf.get("reason", ""))
    elif ht_bull or ht_bear:
        add("mtf", "MTF", ConditionVerdict.PASS.value, align)
    else:
        add("mtf", "MTF", ConditionVerdict.FAIL.value, align or "not aligned")

    st = str((setup_trend or {}).get("trend") or "")
    if st == "WAITING":
        add("trend", "Trend", ConditionVerdict.WAITING.value, "Setup TF waiting")
    elif st in ("BULLISH", "BEARISH"):
        add("trend", "Trend", ConditionVerdict.PASS.value, st)
    elif st == "INSUFFICIENT_DATA":
        add("trend", "Trend", ConditionVerdict.WAITING.value, st)
    else:
        add("trend", "Trend", ConditionVerdict.FAIL.value, st)

    bos_dir = (bos or {}).get("direction")
    if bos is None:
        add("bos", "BOS", ConditionVerdict.WAITING.value, "No BOS data")
    elif bos.get("state") == "CONFIRMED" and bos_dir:
        add("bos", "BOS", ConditionVerdict.PASS.value, str(bos_dir))
    else:
        add("bos", "BOS", ConditionVerdict.FAIL.value, (bos or {}).get("reason", ""))

    if impulse and impulse.get("is_impulse"):
        add("impulse", "Impulse", ConditionVerdict.PASS.value, impulse.get("quality", ""))
    elif impulse and impulse.get("quality") == "INVALID":
        add("impulse", "Impulse", ConditionVerdict.FAIL.value, impulse.get("reason", ""))
    else:
        add("impulse", "Impulse", ConditionVerdict.FAIL.value, (impulse or {}).get("reason", ""))

    pb_state = (pullback or {}).get("pullback_state")
    if pb_state in ("ACTIVE", "CONFIRMED"):
        add("pullback", "Pullback", ConditionVerdict.PASS.value, str(pb_state))
    elif pb_state == "WAITING":
        add("pullback", "Pullback", ConditionVerdict.WAITING.value, "")
    else:
        add("pullback", "Pullback", ConditionVerdict.FAIL.value, str(pb_state))

    if retest and retest.get("retest"):
        add("retest", "Retest", ConditionVerdict.PASS.value, retest.get("reason", ""))
    elif retest and retest.get("state") == "WAITING":
        add("retest", "Retest", ConditionVerdict.WAITING.value, "")
    else:
        add("retest", "Retest", ConditionVerdict.FAIL.value, (retest or {}).get("reason", ""))

    if volume_ok is None:
        add("volume", "Volume", ConditionVerdict.NA.value, "RVOL optional / N/A")
    elif volume_ok:
        add("volume", "Volume", ConditionVerdict.PASS.value, "")
    else:
        add("volume", "Volume", ConditionVerdict.FAIL.value, "RVOL below threshold")

    if config.oi_mandatory:
        add(
            "oi",
            "OI",
            ConditionVerdict.PASS.value if oi_available else ConditionVerdict.FAIL.value,
            "mandatory",
        )
    else:
        add(
            "oi",
            "OI",
            ConditionVerdict.NA.value if not oi_available else ConditionVerdict.PASS.value,
            "N/A" if not oi_available else "available",
        )

    if config.liquidation_mandatory:
        add(
            "liquidation",
            "Liquidation",
            ConditionVerdict.PASS.value if liquidation_available else ConditionVerdict.FAIL.value,
            "mandatory",
        )
    else:
        add(
            "liquidation",
            "Liquidation",
            ConditionVerdict.NA.value if not liquidation_available else ConditionVerdict.PASS.value,
            "N/A" if not liquidation_available else "available",
        )

    structure_ok = bool((pullback or {}).get("structure_intact", False))
    add(
        "structure",
        "Structure",
        ConditionVerdict.PASS.value if structure_ok else ConditionVerdict.FAIL.value,
        "intact" if structure_ok else "broken",
    )

    stop_ok = bool(stop and stop.get("final_stop") is not None)
    add("risk", "Risk", ConditionVerdict.PASS.value if stop_ok else ConditionVerdict.FAIL.value, "")

    targets_ok = bool(targets)
    add("targets", "Targets", ConditionVerdict.PASS.value if targets_ok else ConditionVerdict.FAIL.value, "")

    rr_pass = (risk_reward or {}).get("RISK_REWARD") == "PASS"
    if not rr_pass and config.allow_entry_below_min_rr:
        add("rr", "R:R", ConditionVerdict.PASS.value, "below MIN_RR allowed by config")
        rr_ok = True
    else:
        add("rr", "R:R", ConditionVerdict.PASS.value if rr_pass else ConditionVerdict.FAIL.value, "")
        rr_ok = rr_pass

    # Direction
    direction = None
    if bos_dir == "BULLISH_BOS" and (ht_bull or not config.require_mtf_alignment):
        direction = "LONG"
    elif bos_dir == "BEARISH_BOS" and (ht_bear or not config.require_mtf_alignment):
        direction = "SHORT"

    hard = {"mtf", "trend", "bos", "impulse", "pullback", "structure", "risk", "targets", "rr"}
    # Retest preferred but allow CONFIRMED pullback without perfect retest if configured soft
    soft_fail_ids = {"retest"}
    verdicts = {c.id: c.verdict for c in conditions}
    if align == "CONFLICT" or (ht_bull and bos_dir == "BEARISH_BOS") or (ht_bear and bos_dir == "BULLISH_BOS"):
        status = SignalStatus.CONFLICT.value
    elif any(verdicts.get(i) == ConditionVerdict.WAITING.value for i in hard):
        status = SignalStatus.WAITING.value
    elif any(
        verdicts.get(i) == ConditionVerdict.FAIL.value
        for i in hard
        if i not in soft_fail_ids
    ) or not rr_ok:
        if (pullback or {}).get("pullback_state") == "INVALIDATED":
            status = SignalStatus.INVALIDATED.value
        else:
            status = SignalStatus.NO_SETUP.value
    else:
        # Require retest PASS or pullback CONFIRMED
        if verdicts.get("retest") == ConditionVerdict.PASS.value or pb_state == "CONFIRMED":
            if direction == "LONG":
                status = SignalStatus.LONG_ENTRY_CANDIDATE.value
            elif direction == "SHORT":
                status = SignalStatus.SHORT_ENTRY_CANDIDATE.value
            else:
                status = SignalStatus.NO_SETUP.value
        else:
            status = SignalStatus.WAITING.value

    entry_type = EntryType.WAITING.value
    entry_price = None
    entry_zone = None
    if status in (
        SignalStatus.LONG_ENTRY_CANDIDATE.value,
        SignalStatus.SHORT_ENTRY_CANDIDATE.value,
        SignalStatus.ENTRY_CANDIDATE.value,
    ):
        if retest and retest.get("retest") and bos and bos.get("broken_level") is not None:
            entry_type = EntryType.LIMIT_RETEST.value
            entry_price = float(bos["broken_level"])
            entry_zone = [entry_price * 0.999, entry_price * 1.001]
        elif last_close is not None:
            entry_type = EntryType.MARKET.value
            entry_price = float(last_close)
            entry_zone = [entry_price, entry_price]
        else:
            status = SignalStatus.WAITING.value
            entry_type = EntryType.WAITING.value

    return {
        "status": status,
        "direction": direction,
        "entry_type": entry_type,
        "entry_price": entry_price,
        "entry_zone": entry_zone,
        "conditions": [c.to_dict() for c in conditions],
        "note": "ENTRY_CANDIDATE is a structured setup label — not a trade command",
    }
