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
# Owner ruling D145. Used only when the config file lacks the keys (an older config copy).
PAPER_BOOK_SOURCES = ("COMBO_02_V1", "BOOK16_FWD1", "LCSC_FWD3", "PASSED_BOOK")
COST_STOP_DEFAULTS = {"taker_fee_rate": 0.0005, "slippage_rate": 0.0002, "fee_sides": 2, "multiple": 3.0}
COST_STOP_ENTRY_REFS = ("near_edge", "mid")
# Owner rulings D150, D193(a), D194 (DECISIONS D221): a KILLED source listed in the config key
# ``killed_opened_exception_sources`` alerts the signals its paper book OPENED, with this line right after the
# D117 line. The list defaults to EMPTY here, so a config without the key behaves exactly as v3.
KILLED_OPENED_EXCEPTION_LABEL = "⚠️ KILLED STRATEGY (research) · PARITY TEST · observation only"


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
        _ = self.cost_stop_entry_ref  # an unknown value fails at load, not on the first signal

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

    # --- owner rulings D150 / D193(a) / D194 (DECISIONS D221) ---------------------------------------
    @property
    def killed_opened_exception_sources(self) -> frozenset[str]:
        """KILLED sources whose OPENED paper trades still alert (per-source exception). Empty by default."""
        return frozenset(str(s) for s in (self.raw.get("killed_opened_exception_sources") or []))

    @property
    def killed_opened_exception_label(self) -> str:
        return str(self.raw.get("killed_opened_exception_label") or KILLED_OPENED_EXCEPTION_LABEL)

    def killed_opened_exception(self, source: str, status: str, outcome: Any) -> bool:
        """True for a KILLED signal of a listed source whose paper outcome is exactly ``opened``."""
        return status == "KILLED" and str(source) in self.killed_opened_exception_sources and outcome == "opened"

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

    def is_forward_runner(self, source: str) -> bool:
        return str(source) in set((self.raw.get("forward_runner") or {}).get("sources") or [])

    def late_minutes_for(self, source: str) -> float:
        """Forward runners have their own LATE limit (owner ruling D51b); everything else the spec's."""
        if self.is_forward_runner(source):
            return float(self.raw["forward_runner"]["late_minutes"])
        return float(self.raw["late_minutes"])

    def excluded_symbols(self, source: str) -> frozenset[str]:
        """Symbols a source never alerts on (owner rulings D108/D120: no directional BTC alerts)."""
        rules = ((self.raw.get("symbol_exclusions") or {}).get("sources") or {}).get(str(source)) or {}
        return frozenset(str(s).upper() for s in rules.get("exclude_symbols") or [])

    def symbol_excluded(self, source: str, symbol: str) -> bool:
        return str(symbol or "").upper() in self.excluded_symbols(source)

    @property
    def symbol_exclusion_reason(self) -> str:
        return str((self.raw.get("symbol_exclusions") or {}).get("reason") or "symbol excluded")

    def round_trip_cost(self) -> float:
        """Fees both sides + entry and exit slippage, from the app's own ResearchConfig defaults."""
        if self._round_trip is not None:
            return float(self._round_trip)
        from app.research.config import ResearchConfig

        rc = ResearchConfig()
        sides = float(self.raw["cost"]["fee_sides"])
        return sides * float(rc.taker_fee_rate) + float(rc.entry_slippage_rate) + float(rc.exit_slippage_rate)

    # --- owner ruling D145 ----------------------------------------------------------------------
    @property
    def send_not_opened(self) -> bool:
        """Item 2: send paper-book signals the book did not open (marked NOT OPENED). Off unless explicitly true."""
        return self.raw.get("send_not_opened", False) is True

    def has_paper_outcome(self, source: str, outcome: Any) -> bool:
        """A paper-book source, or any source carrying a paper outcome, except the screener (none)."""
        if str(source) in set(self.raw.get("no_paper_outcome_sources", ["SCREENER"]) or []):
            return False
        return str(source) in set(self.raw.get("paper_book_sources") or PAPER_BOOK_SOURCES) or outcome is not None

    def cost_stop_round_trip(self) -> float:
        """Item 3: fee_sides x (taker + slippage), the D144 real costs (defaults when the section is missing)."""
        cs = {**COST_STOP_DEFAULTS, **(self.raw.get("cost_stop") or {})}
        return float(cs["fee_sides"]) * (float(cs["taker_fee_rate"]) + float(cs["slippage_rate"]))

    def cost_stop_floor(self) -> float:
        cs = {**COST_STOP_DEFAULTS, **(self.raw.get("cost_stop") or {})}
        return float(cs["multiple"]) * self.cost_stop_round_trip()

    # --- owner ruling D188 part 3 ------------------------------------------------------------------
    @property
    def screener_cost_ok_only(self) -> bool:
        """SCREENER alerts go out only when cost as R <= 0.25 (the screener's D144 rule). Off unless explicitly true."""
        return self.raw.get("screener_cost_ok_only", False) is True

    @property
    def cost_stop_entry_ref(self) -> str:
        ref = str(self.raw.get("cost_stop_entry_ref", "near_edge"))
        if ref not in COST_STOP_ENTRY_REFS:
            raise ValueError(f"cost_stop_entry_ref must be one of {COST_STOP_ENTRY_REFS}, not {ref!r}")
        return ref

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
