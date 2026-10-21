"""Load the alert config, the status map and the tick sizes. Every number comes from these files."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "trade_alerts_config.json"
STATUS_MAP_PATH = HERE / "status_map.json"
TICK_SIZES_PATH = HERE / "tick_sizes.json"

STATUSES = ("KILLED", "FORWARD-PASSED", "PAPER", "UNPROVEN")


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def timeframe_minutes(tf: str) -> int:
    s = str(tf or "").strip().lower()
    units = {"m": 1, "h": 60, "d": 1440, "w": 10080}
    if len(s) < 2 or s[-1] not in units or not s[:-1].isdigit():
        raise ValueError(f"unknown timeframe {tf!r}")
    return int(s[:-1]) * units[s[-1]]


class AlertConfig:
    """Read-only view over ``trade_alerts_config.json`` (plus status map and ticks)."""

    def __init__(
        self,
        raw: Mapping[str, Any] | None = None,
        *,
        status_map: Mapping[str, Any] | None = None,
        ticks: Mapping[str, float] | None = None,
        round_trip_cost: float | None = None,
    ) -> None:
        self.raw: dict[str, Any] = copy.deepcopy(dict(raw if raw is not None else _read(CONFIG_PATH)))
        self.status_map: dict[str, Any] = copy.deepcopy(
            dict(status_map if status_map is not None else _read(STATUS_MAP_PATH))
        )
        self.ticks: dict[str, float] = {
            str(k).upper(): float(v)
            for k, v in (ticks if ticks is not None else _read(TICK_SIZES_PATH)["ticks"]).items()
        }
        self._round_trip = round_trip_cost

    # --- flags ---------------------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", False))

    @property
    def dry_run(self) -> bool:
        # Anything but an explicit false is a dry run.
        return self.raw.get("dry_run", True) is not False

    @property
    def sends_telegram(self) -> bool:
        return self.enabled and not self.dry_run

    @property
    def allow_killed(self) -> bool:
        return bool(self.raw.get("allow_killed", False))

    def section(self, name: str) -> dict[str, Any]:
        return dict(self.raw[name])

    def num(self, section: str, key: str) -> float:
        return float(self.raw[section][key])

    # --- derived -------------------------------------------------------------------------------
    def label_for(self, timeframe: str) -> str:
        minutes = timeframe_minutes(timeframe)
        for row in sorted(self.raw["labels"], key=lambda r: float(r["max_minutes"])):
            if minutes <= float(row["max_minutes"]):
                return str(row["label"])
        return str(sorted(self.raw["labels"], key=lambda r: float(r["max_minutes"]))[-1]["label"])

    def round_trip_cost(self) -> float:
        """Fees both sides + entry and exit slippage, from the app's own ResearchConfig defaults."""
        if self._round_trip is not None:
            return float(self._round_trip)
        from app.research.config import ResearchConfig

        rc = ResearchConfig()
        sides = float(self.raw["cost"]["fee_sides"])
        return sides * float(rc.taker_fee_rate) + float(rc.entry_slippage_rate) + float(rc.exit_slippage_rate)

    def status_for(self, source: str, strategy_id: str | None = None) -> str:
        entry = (self.status_map.get("sources") or {}).get(str(source))
        if isinstance(entry, Mapping):
            status = str(entry.get("status") or "")
            if status in STATUSES:
                return status
        default = str(self.status_map.get("default") or "UNPROVEN")
        return default if default in STATUSES else "UNPROVEN"

    def tick_for(self, symbol: str) -> float | None:
        tick = self.ticks.get(str(symbol or "").upper())
        return float(tick) if tick and tick > 0 else None


def load_config(path: str | Path | None = None) -> AlertConfig:
    raw = _read(Path(path)) if path else None
    return AlertConfig(raw)
