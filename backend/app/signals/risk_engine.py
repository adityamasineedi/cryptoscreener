"""Risk/reward, position size calculator, and futures risk checks.

This is a calculator only — it never places trades.
"""

from __future__ import annotations

import math
from typing import Any

from app.signals.config import SignalConfig


def risk_reward(
    entry: float,
    stop: float,
    targets: list[dict[str, Any]],
    *,
    min_rr: float = 2.0,
) -> dict[str, Any]:
    risk = abs(entry - stop)
    if risk <= 0 or entry <= 0:
        return {
            "risk": risk,
            "TP1_R": None,
            "TP2_R": None,
            "TP3_R": None,
            "best_R": None,
            "RISK_REWARD": "FAIL",
            "min_rr": min_rr,
            "reason": "Invalid risk",
        }
    rs: dict[str, float | None] = {"TP1_R": None, "TP2_R": None, "TP3_R": None}
    for t in targets:
        name = str(t.get("name") or "")
        r = t.get("r_multiple")
        if r is None and t.get("target_price") is not None:
            r = abs(float(t["target_price"]) - entry) / risk
        key = name if name.endswith("_R") else f"{name}_R"
        if key in rs:
            rs[key] = float(r) if r is not None else None
    # If names were missing, fill TP slots in list order
    if rs["TP1_R"] is None and targets:
        for i, t in enumerate(targets[:3]):
            key = f"TP{i+1}_R"
            if rs.get(key) is None and t.get("target_price") is not None:
                rs[key] = abs(float(t["target_price"]) - entry) / risk

    best = max((v for v in rs.values() if v is not None), default=None)
    tp1 = rs.get("TP1_R")
    # Gate on first target: management exits at TP1 first. Passing on TP2/TP3
    # alone produced high TP1 hit-rate with ~0.1R winners vs -1R stops.
    ok = tp1 is not None and tp1 >= min_rr
    return {
        "risk": risk,
        **rs,
        "best_R": best,
        "first_target_R": tp1,
        "RISK_REWARD": "PASS" if ok else "FAIL",
        "min_rr": min_rr,
        "note": (
            "MIN_RR applies to TP1 (first exit). Screening parameter only — "
            "not a profitability guarantee"
        ),
        "reason": None
        if ok
        else (
            f"TP1_R {tp1} < min_rr {min_rr}"
            if tp1 is not None
            else "TP1_R unavailable"
        ),
    }


def position_size(
    *,
    account_equity: float,
    risk_percent: float,
    entry: float,
    stop: float,
    contract_quantity_step: float = 0.001,
    minimum_quantity: float = 0.001,
    leverage: float = 5.0,
    fee_rate: float = 0.0004,
    slippage_rate: float = 0.0002,
) -> dict[str, Any]:
    max_risk = account_equity * risk_percent
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0 or account_equity <= 0 or risk_percent <= 0:
        return {
            "account_equity": account_equity,
            "risk_percent": risk_percent,
            "max_risk_amount": max_risk,
            "entry": entry,
            "stop": stop,
            "risk_per_unit": risk_per_unit,
            "raw_quantity": 0.0,
            "final_quantity": 0.0,
            "notional": 0.0,
            "estimated_fee": 0.0,
            "estimated_slippage": 0.0,
            "leverage": leverage,
            "note": "Risk calculator only — does not place trades",
        }
    raw = max_risk / risk_per_unit
    step = max(contract_quantity_step, 1e-12)
    final = math.floor(raw / step) * step
    if final < minimum_quantity:
        final = 0.0
    notional = final * entry
    return {
        "account_equity": account_equity,
        "risk_percent": risk_percent,
        "max_risk_amount": max_risk,
        "entry": entry,
        "stop": stop,
        "risk_per_unit": risk_per_unit,
        "raw_quantity": raw,
        "final_quantity": final,
        "notional": notional,
        "estimated_fee": notional * fee_rate * 2,
        "estimated_slippage": notional * slippage_rate,
        "leverage": leverage,
        "margin_requirement": (notional / leverage) if leverage > 0 else None,
        "note": "Risk calculator only — does not place trades",
    }


def futures_risk_checks(
    *,
    entry: float,
    stop: float,
    leverage: float,
    position: dict[str, Any],
    config: SignalConfig,
    liquidation_price: float | None = None,
    direction: str = "LONG",
) -> dict[str, Any]:
    warnings: list[str] = []
    stop_pct = abs(entry - stop) / entry if entry else None
    if stop_pct is not None and stop_pct < config.stop_too_close_pct:
        warnings.append("STOP_TOO_CLOSE")
    if leverage > config.max_leverage_warning:
        warnings.append("LEVERAGE_TOO_HIGH")
    if float(position.get("final_quantity") or 0) <= 0:
        warnings.append("INVALID_POSITION_SIZE")
    margin = position.get("margin_requirement")
    equity = position.get("account_equity")
    if margin is not None and equity is not None and margin > equity:
        warnings.append("INSUFFICIENT_MARGIN")

    dist_liq = None
    if liquidation_price is not None and entry:
        dist_liq = abs(entry - liquidation_price) / entry
        if dist_liq < config.liquidation_too_close_pct:
            warnings.append("LIQUIDATION_TOO_CLOSE")
    else:
        liquidation_price = None  # never invent

    return {
        "stop_distance_percent": (stop_pct * 100.0) if stop_pct is not None else None,
        "estimated_liquidation_price": liquidation_price,
        "distance_to_liquidation": (dist_liq * 100.0) if dist_liq is not None else None,
        "margin_requirement": margin,
        "leverage": leverage,
        "notional_exposure": position.get("notional"),
        "warnings": warnings,
        "direction": direction,
        "liquidation_data": "LIVE" if liquidation_price is not None else "N/A",
    }


def estimate_liquidation_price(
    *,
    entry: float,
    leverage: float,
    direction: str,
    maintenance_margin_rate: float = 0.004,
) -> float | None:
    """Simple isolated-ish estimate. Returns None if inputs invalid — never fake."""
    if entry <= 0 or leverage <= 0:
        return None
    # Approximate: long liq ≈ entry * (1 - 1/lev + mmr)
    if direction == "LONG":
        return entry * (1.0 - (1.0 / leverage) + maintenance_margin_rate)
    return entry * (1.0 + (1.0 / leverage) - maintenance_margin_rate)
