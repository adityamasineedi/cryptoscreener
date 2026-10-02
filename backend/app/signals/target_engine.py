"""Take-profit levels from structure / R multiples. Do not force TP3.

TP1 must not be a micro structural level (e.g. 0.1R) while management exits at
the first target — that creates high hit-rate / negative expectancy. Structural
targets below min_rr are skipped so R-multiple fallbacks (default 2R/3R/4R) fill
the first slot instead.
"""

from __future__ import annotations

from typing import Any

from app.signals.config import SignalConfig
from app.signals.schemas import TargetLevel


def _swing_price(s: Any) -> float:
    return float(s.price if hasattr(s, "price") else s.get("price"))


def _swing_type(s: Any) -> str | None:
    return getattr(s, "swing_type", None) or (s.get("swing_type") if isinstance(s, dict) else None)


def compute_targets(
    *,
    direction: str,
    entry_price: float,
    stop: dict[str, Any],
    swings: list[Any],
    supply_zone: tuple[float, float] | None = None,
    demand_zone: tuple[float, float] | None = None,
    config: SignalConfig | None = None,
) -> list[dict[str, Any]]:
    cfg = config or SignalConfig()
    risk = float(stop.get("risk_per_unit") or 0)
    if entry_price <= 0 or risk <= 0:
        return []

    min_r = float(cfg.min_rr)
    structural_candidates: list[TargetLevel] = []

    if direction == "LONG":
        highs = sorted(
            {
                _swing_price(s)
                for s in swings
                if _swing_type(s) == "HIGH" and _swing_price(s) > entry_price
            }
        )
        for px in highs:
            r_mult = (px - entry_price) / risk
            if r_mult + 1e-12 < min_r:
                # Too close vs stop — would dominate exits with tiny wins
                continue
            structural_candidates.append(
                TargetLevel(
                    name="TP_STRUCT",
                    target_price=px,
                    target_type="previous_swing_high",
                    distance=px - entry_price,
                    r_multiple=r_mult,
                    structural_reason=f"Prior swing high at {px} (≥ {min_r:.2f}R)",
                )
            )
        if supply_zone:
            mid = (float(supply_zone[0]) + float(supply_zone[1])) / 2.0
            if mid > entry_price:
                r_mult = (mid - entry_price) / risk
                if r_mult + 1e-12 >= min_r:
                    structural_candidates.append(
                        TargetLevel(
                            name="TP_SD",
                            target_price=mid,
                            target_type="supply_zone",
                            distance=mid - entry_price,
                            r_multiple=r_mult,
                            structural_reason="Nearest supply zone midpoint",
                        )
                    )
    else:
        lows = sorted(
            {
                _swing_price(s)
                for s in swings
                if _swing_type(s) == "LOW" and _swing_price(s) < entry_price
            },
            reverse=True,
        )
        for px in lows:
            r_mult = (entry_price - px) / risk
            if r_mult + 1e-12 < min_r:
                continue
            structural_candidates.append(
                TargetLevel(
                    name="TP_STRUCT",
                    target_price=px,
                    target_type="previous_swing_low",
                    distance=entry_price - px,
                    r_multiple=r_mult,
                    structural_reason=f"Prior swing low at {px} (≥ {min_r:.2f}R)",
                )
            )
        if demand_zone:
            mid = (float(demand_zone[0]) + float(demand_zone[1])) / 2.0
            if mid < entry_price:
                r_mult = (entry_price - mid) / risk
                if r_mult + 1e-12 >= min_r:
                    structural_candidates.append(
                        TargetLevel(
                            name="TP_SD",
                            target_price=mid,
                            target_type="demand_zone",
                            distance=entry_price - mid,
                            r_multiple=r_mult,
                            structural_reason="Nearest demand zone midpoint",
                        )
                    )

    # Nearest valid structural levels first (by distance / R)
    structural_candidates.sort(key=lambda t: float(t.r_multiple or 0))
    structural: list[TargetLevel] = []
    for i, t in enumerate(structural_candidates[:2]):
        t.name = f"TP{i+1}"
        structural.append(t)

    # Fill remaining slots with R multiples if structural targets missing
    used = {t.name for t in structural}
    r_targets: list[TargetLevel] = []
    for i, mult in enumerate(cfg.tp_r_multiples):
        name = f"TP{i+1}"
        if name in used:
            continue
        if direction == "LONG":
            px = entry_price + risk * mult
        else:
            px = entry_price - risk * mult
        r_targets.append(
            TargetLevel(
                name=name,
                target_price=px,
                target_type="r_multiple",
                distance=abs(px - entry_price),
                r_multiple=float(mult),
                structural_reason=f"Configurable R multiple ×{mult} (screening param)",
            )
        )

    # Merge: prefer structural for TP1/TP2, allow R for gaps; max 3
    by_name: dict[str, TargetLevel] = {}
    for t in structural + r_targets:
        if t.name not in by_name:
            by_name[t.name] = t
    # leftover SD zone if not already used as TP1/TP2
    for t in structural_candidates:
        if t.target_type in ("supply_zone", "demand_zone") and t.name == "TP_SD":
            by_name.setdefault("TP_SD", t)

    ordered: list[TargetLevel] = []
    for name in ("TP1", "TP2", "TP3"):
        if name in by_name:
            ordered.append(by_name[name])
    # include TP_SD as next slot if missing
    if len(ordered) < 3 and "TP_SD" in by_name:
        sd = by_name["TP_SD"]
        sd.name = f"TP{len(ordered)+1}"
        ordered.append(sd)
    return [t.to_dict() for t in ordered[:3]]
