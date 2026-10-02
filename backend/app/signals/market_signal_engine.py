"""Transparent market-direction classification from existing setup components.

Values: STRONG_BUY | BUY | NEUTRAL | SELL | STRONG_SELL | WAITING

This is NOT an order instruction and NOT a profitability estimate.
confirmation_strength = confirmed_named_conditions / scored_slots (not win probability).
"""

from __future__ import annotations

from typing import Any, Mapping

from app.signals.config import SignalConfig


CLASSIFICATION_VERSION = "market_signal_v1"

MARKET_SIGNALS = (
    "STRONG_BUY",
    "BUY",
    "NEUTRAL",
    "SELL",
    "STRONG_SELL",
    "WAITING",
)


def classify_market_signal(
    *,
    analysis: Mapping[str, Any],
    config: SignalConfig | None = None,
) -> dict[str, Any]:
    """Classify market_signal from an existing SetupAnalysis dict (no second TA)."""
    cfg = config or SignalConfig()
    strong_min = int(getattr(cfg, "market_signal_strong_min_confirmations", 7))
    buy_min = int(getattr(cfg, "market_signal_buy_min_confirmations", 4))

    deps = dict(analysis.get("data_dependencies") or {})
    setup_tf = str(analysis.get("timeframe") or cfg.mtf_setup)
    status = str(analysis.get("status") or "")
    signal_status = str(analysis.get("signal_status") or "")

    # Mandatory OHLCV missing → WAITING (never invent direction)
    if deps.get(setup_tf) == "WAITING FOR OHLCV":
        return _waiting_payload(deps, setup_tf)
    if signal_status == "WAITING" and not analysis.get("trend"):
        return _waiting_payload(deps, setup_tf)

    trends = analysis.get("trend") or {}
    mtf = analysis.get("mtf") or {}
    mtf_align = str(mtf.get("MTF_ALIGNMENT") or "")
    bos = analysis.get("bos") or {}
    choch = analysis.get("choch") or {}
    impulse = analysis.get("impulse") or {}
    pullback = analysis.get("pullback") or {}
    retest = analysis.get("retest") or {}
    rr = analysis.get("risk_reward") or {}
    entry = analysis.get("entry") or {}
    setup_status = status

    htf = str(trends.get(cfg.mtf_major) or "WAITING").upper()
    primary = str(trends.get(cfg.mtf_primary) or "WAITING").upper()
    setup_trend = str(trends.get(cfg.mtf_setup) or "WAITING").upper()

    # Build named condition matrix (PASS / FAIL / N/A / WAITING)
    conditions: dict[str, str] = {}
    bullish: list[str] = []
    bearish: list[str] = []
    reasons: list[str] = []

    def set_cond(key: str, verdict: str, bull_reason: str | None = None, bear_reason: str | None = None) -> None:
        conditions[key] = verdict
        if verdict == "PASS" and bull_reason:
            bullish.append(bull_reason)
            reasons.append(f"✓ {bull_reason}")
        elif verdict == "PASS" and bear_reason:
            bearish.append(bear_reason)
            reasons.append(f"✓ {bear_reason}")
        elif verdict == "N/A":
            reasons.append(f"• {key}: N/A")
        elif verdict == "WAITING":
            reasons.append(f"• {key}: WAITING")

    # HTF / primary
    if htf == "WAITING":
        set_cond("htf_trend", "WAITING")
    elif htf == "BULLISH":
        set_cond("htf_trend", "PASS", bull_reason=f"{cfg.mtf_major.upper()} bullish trend")
    elif htf == "BEARISH":
        set_cond("htf_trend", "PASS", bear_reason=f"{cfg.mtf_major.upper()} bearish trend")
    else:
        set_cond("htf_trend", "FAIL")

    if primary == "WAITING":
        set_cond("primary_trend", "WAITING")
    elif primary == "BULLISH":
        set_cond("primary_trend", "PASS", bull_reason=f"{cfg.mtf_primary.upper()} bullish trend")
    elif primary == "BEARISH":
        set_cond("primary_trend", "PASS", bear_reason=f"{cfg.mtf_primary.upper()} bearish trend")
    else:
        set_cond("primary_trend", "FAIL")

    # MTF — CONFLICT must never become BUY/SELL
    if mtf_align == "CONFLICT":
        conditions["mtf_alignment"] = "FAIL"
        reasons.append("✗ MTF CONFLICT — opposite higher-timeframe trends")
    elif mtf_align == "WAITING":
        set_cond("mtf_alignment", "WAITING")
    elif mtf_align == "STRONG_LONG":
        set_cond("mtf_alignment", "PASS", bull_reason="MTF alignment bullish")
    elif mtf_align == "STRONG_SHORT":
        set_cond("mtf_alignment", "PASS", bear_reason="MTF alignment bearish")
    elif mtf_align in ("MIXED", "INSUFFICIENT"):
        set_cond("mtf_alignment", "FAIL")
    else:
        set_cond("mtf_alignment", "FAIL")

    # BOS
    bos_dir = str(bos.get("direction") or "")
    if bos.get("state") == "CONFIRMED" and bos_dir == "BULLISH_BOS":
        set_cond("bos", "PASS", bull_reason=f"{setup_tf.upper()} bullish BOS")
    elif bos.get("state") == "CONFIRMED" and bos_dir == "BEARISH_BOS":
        set_cond("bos", "PASS", bear_reason=f"{setup_tf.upper()} bearish BOS")
    elif bos.get("state") in (None, "NONE", "WAITING"):
        set_cond("bos", "WAITING" if deps.get(setup_tf) == "WAITING FOR OHLCV" else "FAIL")
    else:
        set_cond("bos", "FAIL")

    # CHOCH (informational; opposite CHOCH counts against)
    choch_dir = str(choch.get("direction") or "")
    if choch.get("state") == "CONFIRMED" and choch_dir == "CHOCH_BULLISH":
        set_cond("choch", "PASS", bull_reason="bullish CHOCH")
    elif choch.get("state") == "CONFIRMED" and choch_dir == "CHOCH_BEARISH":
        set_cond("choch", "PASS", bear_reason="bearish CHOCH")
    else:
        set_cond("choch", "N/A")

    # Impulse
    if impulse.get("is_impulse") and impulse.get("direction") == "BULLISH":
        set_cond("impulse", "PASS", bull_reason="bullish impulse confirmed")
    elif impulse.get("is_impulse") and impulse.get("direction") == "BEARISH":
        set_cond("impulse", "PASS", bear_reason="bearish impulse confirmed")
    else:
        set_cond("impulse", "FAIL")

    # Pullback
    pb = str(pullback.get("pullback_state") or "")
    if pb in ("ACTIVE", "CONFIRMED") and pullback.get("structure_intact", True):
        # direction from impulse/bos
        if bos_dir == "BULLISH_BOS" or impulse.get("direction") == "BULLISH":
            set_cond("pullback", "PASS", bull_reason="bullish pullback valid")
        elif bos_dir == "BEARISH_BOS" or impulse.get("direction") == "BEARISH":
            set_cond("pullback", "PASS", bear_reason="bearish pullback valid")
        else:
            set_cond("pullback", "N/A")
    elif pb in ("WAITING", "NONE", ""):
        set_cond("pullback", "N/A")
    else:
        set_cond("pullback", "FAIL")

    # Retest
    if retest.get("retest"):
        if bos_dir == "BULLISH_BOS":
            set_cond("retest", "PASS", bull_reason="pullback retest confirmed")
        elif bos_dir == "BEARISH_BOS":
            set_cond("retest", "PASS", bear_reason="pullback retest confirmed")
        else:
            set_cond("retest", "N/A")
    else:
        set_cond("retest", "N/A")

    # Volume / RVOL — from impulse rvol vs config
    rvol = impulse.get("rvol")
    if rvol is None:
        set_cond("volume", "N/A")
    elif float(rvol) >= cfg.min_rvol:
        if impulse.get("direction") == "BULLISH" or bos_dir == "BULLISH_BOS":
            set_cond("volume", "PASS", bull_reason=f"RVOL {float(rvol):.2f} ≥ {cfg.min_rvol}")
        elif impulse.get("direction") == "BEARISH" or bos_dir == "BEARISH_BOS":
            set_cond("volume", "PASS", bear_reason=f"RVOL {float(rvol):.2f} ≥ {cfg.min_rvol}")
        else:
            set_cond("volume", "PASS", bull_reason=f"RVOL {float(rvol):.2f}")
    else:
        set_cond("volume", "FAIL")

    # Supply/Demand — optional context from tags in analysis if present
    # We only mark PASS if pullback zone_hit is demand/supply aligning
    zone_hit = pullback.get("zone_hit")
    if zone_hit in ("demand", "structure") and bos_dir == "BULLISH_BOS":
        set_cond("supply_demand", "PASS", bull_reason=f"zone interaction ({zone_hit})")
    elif zone_hit in ("supply", "structure") and bos_dir == "BEARISH_BOS":
        set_cond("supply_demand", "PASS", bear_reason=f"zone interaction ({zone_hit})")
    else:
        set_cond("supply_demand", "N/A")

    # OI / Liquidation — optional; NEVER directional from absence
    oi = str(deps.get("OI") or "N/A")
    if oi == "LIVE":
        set_cond("oi", "N/A")  # classification does not invent OI bias without engine label
    elif oi in ("WAITING",):
        set_cond("oi", "N/A")
    else:
        set_cond("oi", "N/A")

    liq = str(deps.get("Liquidations") or "N/A")
    set_cond("liquidation", "N/A" if liq != "LIVE" else "N/A")

    # Entry setup / R:R (supporting, not sole score)
    if setup_status in ("LONG_ENTRY_CANDIDATE", "ENTRY_CANDIDATE"):
        set_cond("entry_setup", "PASS", bull_reason="LONG_ENTRY_CANDIDATE")
    elif setup_status == "SHORT_ENTRY_CANDIDATE":
        set_cond("entry_setup", "PASS", bear_reason="SHORT_ENTRY_CANDIDATE")
    elif setup_status == "CONFLICT":
        set_cond("entry_setup", "FAIL")
        reasons.append("✗ setup CONFLICT")
    elif setup_status == "WAITING":
        set_cond("entry_setup", "WAITING")
    else:
        set_cond("entry_setup", "N/A")

    if rr.get("RISK_REWARD") == "PASS":
        if setup_status in ("LONG_ENTRY_CANDIDATE", "ENTRY_CANDIDATE") or bos_dir == "BULLISH_BOS":
            set_cond("risk_reward", "PASS", bull_reason=f"R:R PASS (best={rr.get('best_R')})")
        elif setup_status == "SHORT_ENTRY_CANDIDATE" or bos_dir == "BEARISH_BOS":
            set_cond("risk_reward", "PASS", bear_reason=f"R:R PASS (best={rr.get('best_R')})")
        else:
            set_cond("risk_reward", "N/A")
    elif rr.get("RISK_REWARD") == "FAIL":
        set_cond("risk_reward", "FAIL")
    else:
        set_cond("risk_reward", "N/A")

    # Invalidation blocks strong signals
    invalidated = setup_status == "INVALIDATED" or bool(analysis.get("invalidation_reason"))
    if invalidated:
        reasons.append(f"✗ invalidated: {analysis.get('invalidation_reason') or setup_status}")

    # Count confirmations by side (PASS conditions attributed to bullish/bearish lists)
    bull_n = len(bullish)
    bear_n = len(bearish)
    na_n = sum(1 for v in conditions.values() if v == "N/A")
    waiting_n = sum(1 for v in conditions.values() if v == "WAITING")
    scored_slots = max(bull_n + bear_n + sum(1 for v in conditions.values() if v == "FAIL"), 1)
    # confirmation_strength = confirmed directional conditions out of 10 named slots
    named_slots = [
        "htf_trend",
        "primary_trend",
        "mtf_alignment",
        "bos",
        "impulse",
        "pullback",
        "retest",
        "volume",
        "entry_setup",
        "risk_reward",
    ]
    confirmed = sum(1 for k in named_slots if conditions.get(k) == "PASS")
    confirmation_strength = confirmed  # X/10 style
    confirmation_denominator = len(named_slots)

    data_status = {
        "signal_status": signal_status or "LIVE",
        "setup_status": setup_status,
        "dependencies": deps,
        "optional_na": {"oi": conditions.get("oi"), "liquidation": conditions.get("liquidation")},
    }

    # WAITING if mandatory HTF/primary/setup still waiting for OHLCV
    if waiting_n >= 3 and bull_n + bear_n == 0:
        return {
            "market_signal": "WAITING",
            "confirmation_strength": 0,
            "confirmation_denominator": confirmation_denominator,
            "direction": None,
            "reasons": reasons or ["WAITING FOR OHLCV"],
            "conditions": conditions,
            "bullish_conditions": bullish,
            "bearish_conditions": bearish,
            "data_status": data_status,
            "market_signal_reason": "Mandatory OHLCV dependencies unavailable",
            "classification_version": CLASSIFICATION_VERSION,
            "note": "Market signal classification only — not a trade instruction or profitability claim",
        }

    # CONFLICT → NEUTRAL (never BUY/SELL)
    if mtf_align == "CONFLICT" or setup_status == "CONFLICT":
        return {
            "market_signal": "NEUTRAL",
            "confirmation_strength": confirmation_strength,
            "confirmation_denominator": confirmation_denominator,
            "direction": None,
            "reasons": reasons,
            "conditions": conditions,
            "bullish_conditions": bullish,
            "bearish_conditions": bearish,
            "data_status": data_status,
            "market_signal_reason": "MTF or setup CONFLICT — direction not classified as BUY/SELL",
            "classification_version": CLASSIFICATION_VERSION,
            "note": "Market signal classification only — not a trade instruction or profitability claim",
        }

    if invalidated:
        return {
            "market_signal": "NEUTRAL",
            "confirmation_strength": confirmation_strength,
            "confirmation_denominator": confirmation_denominator,
            "direction": None,
            "reasons": reasons,
            "conditions": conditions,
            "bullish_conditions": bullish,
            "bearish_conditions": bearish,
            "data_status": data_status,
            "market_signal_reason": "Structure invalidated — no directional market_signal",
            "classification_version": CLASSIFICATION_VERSION,
            "note": "Market signal classification only — not a trade instruction or profitability claim",
        }

    # Strong / regular classification from named mandatory gates
    strong_bull_ok = (
        conditions.get("htf_trend") == "PASS"
        and htf == "BULLISH"
        and conditions.get("primary_trend") == "PASS"
        and primary == "BULLISH"
        and conditions.get("bos") == "PASS"
        and bos_dir == "BULLISH_BOS"
        and conditions.get("impulse") == "PASS"
        and impulse.get("direction") == "BULLISH"
        and (
            conditions.get("pullback") == "PASS"
            or conditions.get("retest") == "PASS"
        )
        and conditions.get("mtf_alignment") == "PASS"
        and mtf_align in ("STRONG_LONG",)
        and bull_n >= strong_min
        and bear_n == 0
    )
    strong_bear_ok = (
        conditions.get("htf_trend") == "PASS"
        and htf == "BEARISH"
        and conditions.get("primary_trend") == "PASS"
        and primary == "BEARISH"
        and conditions.get("bos") == "PASS"
        and bos_dir == "BEARISH_BOS"
        and conditions.get("impulse") == "PASS"
        and impulse.get("direction") == "BEARISH"
        and (
            conditions.get("pullback") == "PASS"
            or conditions.get("retest") == "PASS"
        )
        and conditions.get("mtf_alignment") == "PASS"
        and mtf_align in ("STRONG_SHORT",)
        and bear_n >= strong_min
        and bull_n == 0
    )

    buy_ok = (
        htf == "BULLISH"
        and primary in ("BULLISH", "NEUTRAL")
        and bos_dir == "BULLISH_BOS"
        and mtf_align != "CONFLICT"
        and bull_n >= buy_min
        and bull_n > bear_n
    )
    sell_ok = (
        htf == "BEARISH"
        and primary in ("BEARISH", "NEUTRAL")
        and bos_dir == "BEARISH_BOS"
        and mtf_align != "CONFLICT"
        and bear_n >= buy_min
        and bear_n > bull_n
    )

    if strong_bull_ok:
        market_signal = "STRONG_BUY"
        direction = "BULLISH"
        reason = f"STRONG_BUY: {bull_n} bullish named confirmations; mandatory HTF/primary/BOS/impulse/MTF PASS"
    elif strong_bear_ok:
        market_signal = "STRONG_SELL"
        direction = "BEARISH"
        reason = f"STRONG_SELL: {bear_n} bearish named confirmations; mandatory HTF/primary/BOS/impulse/MTF PASS"
    elif buy_ok:
        market_signal = "BUY"
        direction = "BULLISH"
        reason = f"BUY: bullish structure with {bull_n} confirmations (below STRONG_BUY gates)"
    elif sell_ok:
        market_signal = "SELL"
        direction = "BEARISH"
        reason = f"SELL: bearish structure with {bear_n} confirmations (below STRONG_SELL gates)"
    else:
        market_signal = "NEUTRAL"
        direction = None
        reason = (
            f"NEUTRAL: insufficient or mixed evidence "
            f"(bullish={bull_n}, bearish={bear_n}, N/A={na_n})"
        )

    return {
        "market_signal": market_signal,
        "confirmation_strength": confirmation_strength,
        "confirmation_denominator": confirmation_denominator,
        "direction": direction,
        "reasons": reasons,
        "conditions": conditions,
        "bullish_conditions": bullish,
        "bearish_conditions": bearish,
        "data_status": data_status,
        "market_signal_reason": reason,
        "classification_version": CLASSIFICATION_VERSION,
        "setup_status": setup_status,
        "thresholds_note": (
            f"strong_min={strong_min}, buy_min={buy_min} — screening defaults, not claimed optima"
        ),
        "note": "Market signal classification only — not a trade instruction or profitability claim",
    }


def _waiting_payload(deps: dict[str, str], setup_tf: str) -> dict[str, Any]:
    return {
        "market_signal": "WAITING",
        "confirmation_strength": 0,
        "confirmation_denominator": 10,
        "direction": None,
        "reasons": [f"{setup_tf.upper()}: WAITING FOR OHLCV"],
        "conditions": {
            "htf_trend": "WAITING",
            "primary_trend": "WAITING",
            "bos": "WAITING",
            "impulse": "WAITING",
            "pullback": "WAITING",
            "retest": "WAITING",
            "mtf_alignment": "WAITING",
            "volume": "N/A",
            "supply_demand": "N/A",
            "oi": "N/A",
            "liquidation": "N/A",
            "entry_setup": "WAITING",
            "risk_reward": "N/A",
        },
        "bullish_conditions": [],
        "bearish_conditions": [],
        "data_status": {"dependencies": deps, "signal_status": "WAITING"},
        "market_signal_reason": "Required OHLCV unavailable",
        "classification_version": CLASSIFICATION_VERSION,
        "note": "Market signal classification only — not a trade instruction or profitability claim",
    }
