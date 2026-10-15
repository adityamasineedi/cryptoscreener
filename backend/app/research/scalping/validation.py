from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from .catalog import registration


@dataclass(frozen=True)
class ValidationRecord:
    strategy_id: str
    variant: str
    historical_passed: bool
    forward_paper_passed: bool
    selected_without_final_holdout: bool
    evidence: dict[str, Any]
    evaluated_at: datetime

    @property
    def publishable(self) -> bool:
        spec = registration(self.strategy_id, self.variant)
        return bool(
            spec.specification_complete
            and self.historical_passed
            and self.forward_paper_passed
            and self.selected_without_final_holdout
        )

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["evaluated_at"] = self.evaluated_at.isoformat()
        out["publishable"] = self.publishable
        return out


class ScalpingValidationRegistry:
    """Fail-closed runtime registry; tests or research jobs must supply evidence."""

    def __init__(self) -> None:
        self._records: dict[str, ValidationRecord] = {}
        self._lock = RLock()

    @staticmethod
    def key(strategy_id: str, variant: str) -> str:
        return f"{strategy_id}:{variant}"

    def record(
        self,
        *,
        strategy_id: str,
        variant: str = "default",
        historical_passed: bool,
        forward_paper_passed: bool,
        selected_without_final_holdout: bool,
        evidence: dict[str, Any],
    ) -> ValidationRecord:
        registration(strategy_id, variant)
        item = ValidationRecord(
            strategy_id=strategy_id,
            variant=variant,
            historical_passed=bool(historical_passed),
            forward_paper_passed=bool(forward_paper_passed),
            selected_without_final_holdout=bool(selected_without_final_holdout),
            evidence=dict(evidence),
            evaluated_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._records[self.key(strategy_id, variant)] = item
        return item

    def get(self, strategy_id: str, variant: str = "default") -> ValidationRecord | None:
        with self._lock:
            return self._records.get(self.key(strategy_id, variant))

    def is_publishable(self, strategy_id: str, variant: str = "default") -> bool:
        item = self.get(strategy_id, variant)
        return bool(item and item.publishable)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {key: value.to_dict() for key, value in self._records.items()}


validation_registry = ScalpingValidationRegistry()
