"""Explainable entry/exit — explicit conditions only, never a black-box BUY/SELL."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from app.models.schemas import DataStatus, FreshValue


@dataclass
class ConditionResult:
    id: str
    description: str
    passed: bool | None  # None = insufficient data
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "passed": self.passed,
            "evidence": self.evidence,
        }


class EntryExitEngine:
    """
    Evaluates configured boolean conditions.

    States: WAITING | NO_SETUP | ENTRY_CANDIDATE | EXIT_CANDIDATE | CONFLICT
    Never emits Strong Buy / Sell marketing labels.
    """

    SOURCE = "entry_exit_engine"

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        ee = dict(cfg.get("entry_exit") or cfg)
        self.require_bullish_structure = bool(ee.get("require_bullish_structure", True))
        self.require_rvol_above = float(ee.get("require_rvol_above", 1.2))
        self.rsi_entry_max = float(ee.get("rsi_entry_max", 70))
        self.rsi_exit_min = float(ee.get("rsi_exit_min", 70))
        self.require_demand_zone = bool(ee.get("require_demand_zone", False))
        self.exit_on_bearish_structure = bool(ee.get("exit_on_bearish_structure", True))
        self.exit_on_supply_zone = bool(ee.get("exit_on_supply_zone", True))

    def evaluate(
        self,
        *,
        structure: str | None,
        bos: str | None,
        choch: str | None,
        rvol: float | None,
        rsi: float | None,
        nearest_demand: str | None,
        nearest_supply: str | None,
        oi_classification: str | None = None,
    ) -> dict[str, Any]:
        entry_conds = [
            ConditionResult(
                id="structure_bullish",
                description="Market structure is bullish",
                passed=(
                    None
                    if structure is None
                    else structure.lower() in {"bullish", "bull"}
                ),
                evidence={"structure": structure},
            ),
            ConditionResult(
                id="rvol_expansion",
                description=f"RVOL >= {self.require_rvol_above}",
                passed=None if rvol is None else rvol >= self.require_rvol_above,
                evidence={"rvol": rvol, "threshold": self.require_rvol_above},
            ),
            ConditionResult(
                id="rsi_not_overbought",
                description=f"RSI <= {self.rsi_entry_max}",
                passed=None if rsi is None else rsi <= self.rsi_entry_max,
                evidence={"rsi": rsi, "max": self.rsi_entry_max},
            ),
            ConditionResult(
                id="bos_or_choch_bullish",
                description="Recent BOS/CHOCH bullish event preferred",
                passed=(
                    None
                    if bos is None and choch is None
                    else bool(
                        (bos and "BULLISH" in bos.upper())
                        or (choch and "BULLISH" in choch.upper())
                    )
                ),
                evidence={"bos": bos, "choch": choch},
            ),
        ]
        if self.require_demand_zone:
            entry_conds.append(
                ConditionResult(
                    id="near_demand",
                    description="Nearest demand zone present",
                    passed=None if nearest_demand is None else True,
                    evidence={"nearest_demand": nearest_demand},
                )
            )

        exit_conds = [
            ConditionResult(
                id="structure_bearish",
                description="Market structure turned bearish",
                passed=(
                    None
                    if structure is None
                    else structure.lower() in {"bearish", "bear"}
                ),
                evidence={"structure": structure},
            ),
            ConditionResult(
                id="rsi_overbought",
                description=f"RSI >= {self.rsi_exit_min}",
                passed=None if rsi is None else rsi >= self.rsi_exit_min,
                evidence={"rsi": rsi, "min": self.rsi_exit_min},
            ),
        ]
        if self.exit_on_supply_zone:
            exit_conds.append(
                ConditionResult(
                    id="near_supply",
                    description="Nearest supply zone present",
                    passed=None if nearest_supply is None else bool(nearest_supply),
                    evidence={"nearest_supply": nearest_supply},
                )
            )
        if oi_classification:
            exit_conds.append(
                ConditionResult(
                    id="oi_price_divergence",
                    description="Price up while OI down (descriptive, not a trade signal)",
                    passed=oi_classification == "PRICE_UP_OI_DOWN",
                    evidence={"oi_classification": oi_classification},
                )
            )

        def _score(conds: list[ConditionResult]) -> tuple[int, int, int]:
            passed = sum(1 for c in conds if c.passed is True)
            failed = sum(1 for c in conds if c.passed is False)
            unknown = sum(1 for c in conds if c.passed is None)
            return passed, failed, unknown

        e_ok, e_fail, e_unk = _score(entry_conds)
        x_ok, x_fail, x_unk = _score(exit_conds)

        if e_unk == len(entry_conds) and x_unk == len(exit_conds):
            state = "WAITING"
            method = "Requires Structure + Volume (+ Zones/RSI when configured)"
        elif e_ok >= max(2, len(entry_conds) - 1) and e_fail == 0 and x_ok == 0:
            state = "ENTRY_CANDIDATE"
            method = "Explicit boolean conditions from structure/RVOL/RSI/zones/OI"
        elif x_ok >= 2 and e_ok < 2:
            state = "EXIT_CANDIDATE"
            method = "Explicit boolean conditions from structure/RVOL/RSI/zones/OI"
        elif e_ok >= 2 and x_ok >= 2:
            state = "CONFLICT"
            method = "Explicit boolean conditions from structure/RVOL/RSI/zones/OI"
        else:
            state = "NO_SETUP"
            method = "Explicit boolean conditions from structure/RVOL/RSI/zones/OI"

        ts = datetime.now(timezone.utc)
        return {
            "state": FreshValue(
                value=state,
                timestamp=ts,
                source=self.SOURCE,
                status=DataStatus.WAITING if state == "WAITING" else DataStatus.LIVE,
                methodology=method,
            ),
            "entry_conditions": [c.to_dict() for c in entry_conds],
            "exit_conditions": [c.to_dict() for c in exit_conds],
            "entry_pass_count": e_ok,
            "exit_pass_count": x_ok,
            "insufficient_data_count": e_unk + x_unk,
        }
